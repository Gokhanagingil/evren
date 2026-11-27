"""Olay omurgası sözleşmesi (U2, ADR-0017): anahtar, kodlama, DLQ başlıkları, olay biçimi, maskeleme, Kafka ayarları."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from evren_core.envelope import IngestEnvelope, IngestRecord
from evren_core.messaging import (
    MAX_MESSAGE_BYTES,
    TOPIC_ACTIVITY,
    TOPIC_AUDIT,
    TOPIC_CI_CHANGED,
    KafkaConfigError,
    KafkaSettings,
    MessageError,
    decode_record,
    dlq_headers,
    encode_record,
    outbox_message,
    record_key,
    redact_payload,
)


def envelope(**attrs) -> IngestEnvelope:
    rec = IngestRecord(data_source="Zabbix", run_id=uuid.uuid4(), source_key="10084", class_hint="cmdb_ci_linux_server",
                       observed_at=datetime(2026, 11, 25, tzinfo=UTC), attributes=attrs or {"name": "srv-01"})
    return IngestEnvelope(id="e-1", source="urn:evren:connector:zabbix", time=datetime(2026, 11, 25, tzinfo=UTC),
                          data=rec)


def test_record_roundtrip_and_key_consistency():
    env = envelope()
    value = encode_record(env)
    key = record_key("Zabbix", "10084")
    assert decode_record(value, key) == env
    with pytest.raises(MessageError, match="anahtarı"):
        decode_record(value, record_key("Zabbix", "başka"))


@pytest.mark.parametrize("value", [b"", None, b"{not json", b'{"specversion": "1.0"}'])
def test_invalid_messages_are_rejected_without_echoing_content(value):
    with pytest.raises(MessageError) as exc:
        decode_record(value)
    assert "not json" not in str(exc.value)


def test_unknown_field_is_rejected():
    doc = json.loads(encode_record(envelope()))
    doc["data"]["beklenmeyen"] = 1
    with pytest.raises(MessageError, match=r"data\.beklenmeyen"):
        decode_record(json.dumps(doc).encode())


def test_oversized_record_rejected_on_both_sides():
    with pytest.raises(MessageError, match="çok büyük"):
        encode_record(envelope(comments="x" * MAX_MESSAGE_BYTES))
    with pytest.raises(MessageError, match="çok büyük"):
        decode_record(b"{" + b" " * MAX_MESSAGE_BYTES + b"}")


def test_dlq_headers_are_bounded():
    h = dict(dlq_headers("x" * 2000, origin_topic="t", partition=3, offset=42, error_id="abc"))
    assert len(h["evren-dlq-reason"]) == 500 and h["evren-origin-offset"] == b"42" and h["evren-error-id"] == b"abc"


def test_outbox_message_routes_and_redacts():
    row = {"id": 7, "aggregate_id": "a" * 32, "event_type": "cmdb.ci.changed.v1",
           "created_at": datetime(2026, 11, 25, tzinfo=UTC),
           "payload": {"sys_id": "a" * 32, "changes": [{"attribute": "u_gizli", "old": "eski", "new": "yeni"},
                                                       {"attribute": "name", "old": "a", "new": "b"}]}}
    topic, key, value = outbox_message(row, hidden={"u_gizli"})
    event = json.loads(value)
    assert topic == TOPIC_CI_CHANGED and key == b"a" * 32
    assert event["id"] == "outbox-7" and event["type"] == "evren.cmdb.ci.changed.v1" and event["subject"] == "a" * 32
    assert event["data"]["changes"][0] == {"attribute": "u_gizli", "old": None, "new": None, "redacted": True}
    assert event["data"]["changes"][1]["new"] == "b"
    assert b"eski" not in value and b"yeni" not in value
    topic2, _k, value2 = outbox_message({**row, "event_type": "evren.ci.edit.requested.v1", "payload": {"id": "x"}})
    assert topic2 == TOPIC_ACTIVITY and json.loads(value2)["type"] == "evren.ci.edit.requested.v1"
    assert outbox_message({**row, "event_type": "cmdb.ci.created.v1"})[0] == TOPIC_CI_CHANGED
    # Denetim izi (ADR-0039): güvenlik olayı, mühür ve doğrulama SIEM konusuna
    for kind in ("audit.security_event", "audit.seal", "audit.seal_check"):
        topic3, key3, value3 = outbox_message({**row, "aggregate_id": "seal:5", "event_type": kind, "payload": {"id": 5}})
        assert topic3 == TOPIC_AUDIT == "evren.audit.v1" and key3 == b"seal:5"
        assert json.loads(value3)["type"] == f"evren.{kind}"


def test_redact_payload_without_changes_is_identity():
    assert redact_payload({"a": 1}, {"x"}) == {"a": 1}


def test_kafka_settings_secure_by_default(tmp_path):
    with pytest.raises(KafkaConfigError, match="BOOTSTRAP"):
        KafkaSettings.from_env({})
    with pytest.raises(KafkaConfigError, match="SASL_SSL için"):
        KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "k:9093"})
    with pytest.raises(KafkaConfigError, match="ALLOW_PLAINTEXT"):
        KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "k:9092", "EVREN_KAFKA_SECURITY_PROTOCOL": "PLAINTEXT"})
    with pytest.raises(KafkaConfigError, match="Desteklenmeyen"):
        KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "k:9092", "EVREN_KAFKA_SECURITY_PROTOCOL": "SASL_PLAINTEXT"})
    dev = KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "kafka:9092", "EVREN_KAFKA_SECURITY_PROTOCOL": "plaintext",
                                  "EVREN_KAFKA_ALLOW_PLAINTEXT": "1"})
    assert dev.consumer_config("c", "g")["enable.auto.offset.store"] is False
    assert dev.producer_config("p")["enable.idempotence"] is True and dev.producer_config("p")["acks"] == "all"


def test_kafka_password_only_from_file_and_never_in_repr(tmp_path):
    canary = "-".join(("kafka", "parola", "123"))  # çalışma anında kurulur (sır tarayıcısı için değil)
    pfile = tmp_path / "kafka_pw"
    pfile.write_text(canary + "\n", encoding="utf-8")
    s = KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "k:9093", "EVREN_KAFKA_USERNAME": "evren-gw",
                                "EVREN_KAFKA_PASSWORD_FILE": str(pfile), "EVREN_KAFKA_CA_FILE": "/ca.pem"})
    cfg = s.client_config("gw")
    assert cfg["sasl.password"] == canary and cfg["ssl.ca.location"] == "/ca.pem"
    assert cfg["ssl.endpoint.identification.algorithm"] == "https"
    assert canary not in repr(s)
    with pytest.raises(KafkaConfigError, match="okunamadı"):
        KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "k", "EVREN_KAFKA_PASSWORD_FILE": str(tmp_path / "yok")})
    with pytest.raises(KafkaConfigError, match="birlikte"):
        KafkaSettings.from_env({"EVREN_KAFKA_BOOTSTRAP": "k", "EVREN_KAFKA_SECURITY_PROTOCOL": "SSL",
                                "EVREN_KAFKA_CERT_FILE": "/c.pem"})

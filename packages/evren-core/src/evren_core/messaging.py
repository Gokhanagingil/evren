"""Olay omurgası sözleşmesi (U2, ADR-0017) — konu adları, mesaj anahtarları, olay biçimi, Kafka yapılandırması.

G/Ç yapmaz (Kafka istemcisi servislerde). Alım geçidi, uzlaştırıcı tüketicisi ve outbox yayıncısı aynı sözleşmeyi
buradan alır; böylece konu adı, anahtar ve olay biçimi tek yerde değişir ve test edilir.

* Kaynak kayıtları ``evren.ingest.record.v1`` konusuna ``<veri_kaynağı>:<source_key>`` anahtarıyla yazılır: aynı
  nesnenin kayıtları aynı bölüme düşer, sırası korunur.
* CMDB olayları outbox'tan yayınlanır (``cmdb.ci.*`` → ``evren.cmdb.ci.changed.v1``, insan istekleri →
  ``evren.cmdb.activity.v1``); CI başına sıra için anahtar ``sys_id``'dir. Hassasiyeti
  ``confidential`` veya ``restricted`` olan alanların değerleri olaya konmaz (yalnızca alan adı ve "gizli" işareti).
* Kafka bağlantısı güvenli varsayılanlıdır: düz metin (PLAINTEXT) yalnızca açık geliştirme izniyle; SASL parolası
  yalnızca dosyadan okunur ve ``repr``'da görünmez.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .envelope import IngestEnvelope

__all__ = [
    "MAX_MESSAGE_BYTES", "TOPIC_ACTIVITY", "TOPIC_CI_CHANGED", "TOPIC_DLQ", "TOPIC_RECORD", "TOPIC_RUN",
    "KafkaConfigError", "KafkaSettings", "MessageError", "decode_record", "dlq_headers", "encode_record",
    "outbox_message", "record_key", "redact_payload", "topic_for_event",
]

TOPIC_RECORD = "evren.ingest.record.v1"
TOPIC_RUN = "evren.ingest.run.v1"
TOPIC_CI_CHANGED = "evren.cmdb.ci.changed.v1"
TOPIC_ACTIVITY = "evren.cmdb.activity.v1"
TOPIC_DLQ = "evren.dlq.v1"
#: Tek mesaj üst sınırı (Kafka varsayılanı ~1 MB). Geçit daha büyük kaydı reddeder; tüketici yine denetler.
MAX_MESSAGE_BYTES = 900_000


class MessageError(ValueError):
    """Mesaj çözülemedi veya sözleşmeye uymuyor (DLQ'ya gider)."""


class KafkaConfigError(ValueError):
    pass


# --------------------------------------------------------------------------- kaynak kayıtları
def record_key(data_source: str, source_key: str) -> bytes:
    return f"{data_source}:{source_key}".encode()


def encode_record(envelope: IngestEnvelope) -> bytes:
    value = envelope.model_dump_json().encode()
    if len(value) > MAX_MESSAGE_BYTES:
        raise MessageError(f"Kayıt çok büyük ({len(value)} bayt; üst sınır {MAX_MESSAGE_BYTES})")
    return value


def decode_record(value: bytes | None, key: bytes | None = None) -> IngestEnvelope:
    """Konudan okunan değeri doğrular. Anahtar verilirse kayıtla tutarlı olmalıdır (yanlış bölüm = sıra bozulur)."""
    if not value:
        raise MessageError("Boş mesaj")
    if len(value) > MAX_MESSAGE_BYTES:
        raise MessageError(f"Mesaj çok büyük ({len(value)} bayt)")
    try:
        env = IngestEnvelope.model_validate_json(value)
    except ValidationError as exc:
        locs = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})[:10]
        raise MessageError("Zarf doğrulanamadı: " + ", ".join(locs)) from None
    if key is not None and key != record_key(env.data.data_source, env.data.source_key):
        raise MessageError("Mesaj anahtarı kayıtla uyuşmuyor")
    return env


def dlq_headers(reason: str, *, origin_topic: str, partition: int | None, offset: int | None,
                error_id: str | None = None) -> list[tuple[str, bytes]]:
    """DLQ başlıkları: neden (kısa, Türkçe), kaynak konu/bölüm/ofset. Mesaj içeriği değiştirilmeden kopyalanır."""
    headers = [("evren-dlq-reason", reason[:500].encode()), ("evren-origin-topic", origin_topic.encode()),
               ("evren-origin-partition", str(partition).encode()), ("evren-origin-offset", str(offset).encode())]
    if error_id:
        headers.append(("evren-error-id", error_id.encode()))
    return headers


# --------------------------------------------------------------------------- outbox olayları
def topic_for_event(event_type: str) -> str:
    """CI yaşam döngüsü olayları (``cmdb.ci.*``: oluşturma, değişiklik) CI konusuna; insan istekleri etkinlik konusuna."""
    return TOPIC_CI_CHANGED if event_type.startswith("cmdb.ci.") else TOPIC_ACTIVITY


def redact_payload(payload: Mapping[str, Any], hidden: Iterable[str]) -> dict[str, Any]:
    """Değişiklik listesindeki gizli alanların değerlerini çıkarır (alan adı ve kaynak kalır)."""
    hidden = set(hidden)
    out = dict(payload)
    changes = payload.get("changes")
    if isinstance(changes, list) and hidden:
        red = []
        for ch in changes:
            if isinstance(ch, Mapping) and ch.get("attribute") in hidden:
                ch = {**ch, "old": None, "new": None, "redacted": True}
            red.append(ch)
        out["changes"] = red
    return out


def outbox_message(row: Mapping[str, Any], hidden: Iterable[str] = ()) -> tuple[str, bytes, bytes]:
    """Outbox satırı → (konu, anahtar, CloudEvents JSON). ``id`` outbox kimliğidir: tüketiciler tekrarı onunla ayıklar."""
    created: datetime = row["created_at"]
    event = {
        "specversion": "1.0", "id": f"outbox-{row['id']}", "source": "urn:evren:cmdb",
        "type": f"evren.{row['event_type']}" if not str(row["event_type"]).startswith("evren.") else row["event_type"],
        "time": created.isoformat(), "subject": str(row["aggregate_id"]), "datacontenttype": "application/json",
        "data": redact_payload(row["payload"] or {}, hidden),
    }
    value = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return topic_for_event(str(row["event_type"])), str(row["aggregate_id"]).encode(), value


# --------------------------------------------------------------------------- Kafka yapılandırması
_PROTOCOLS = {"PLAINTEXT", "SSL", "SASL_SSL"}
_MECHANISMS = {"SCRAM-SHA-512", "SCRAM-SHA-256", "PLAIN"}


def _read(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        raise KafkaConfigError(f"{what} dosyası okunamadı") from None


@dataclass(frozen=True)
class KafkaSettings:
    """``EVREN_KAFKA_*`` ortam değişkenlerinden Kafka istemci ayarları.

    | Değişken | Anlam |
    | --- | --- |
    | ``EVREN_KAFKA_BOOTSTRAP`` | ``host:port[,host:port]`` (zorunlu) |
    | ``EVREN_KAFKA_SECURITY_PROTOCOL`` | ``SASL_SSL`` (varsayılan), ``SSL`` veya ``PLAINTEXT`` |
    | ``EVREN_KAFKA_ALLOW_PLAINTEXT=1`` | PLAINTEXT için zorunlu açık izin (yalnızca yerel geliştirme) |
    | ``EVREN_KAFKA_SASL_MECHANISM`` | ``SCRAM-SHA-512`` (varsayılan) |
    | ``EVREN_KAFKA_USERNAME`` / ``EVREN_KAFKA_PASSWORD_FILE`` | SASL kimliği; parola yalnızca dosyadan |
    | ``EVREN_KAFKA_CA_FILE``, ``EVREN_KAFKA_CERT_FILE``, ``EVREN_KAFKA_KEY_FILE`` | TLS güven ve istemci sertifikası |
    """

    bootstrap: str
    protocol: str = "SASL_SSL"
    mechanism: str = "SCRAM-SHA-512"
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> KafkaSettings:
        bootstrap = (env.get("EVREN_KAFKA_BOOTSTRAP") or "").strip()
        if not bootstrap:
            raise KafkaConfigError("EVREN_KAFKA_BOOTSTRAP tanımlı değil")
        protocol = env.get("EVREN_KAFKA_SECURITY_PROTOCOL", "SASL_SSL").upper()
        if protocol not in _PROTOCOLS:
            raise KafkaConfigError(f"Desteklenmeyen güvenlik protokolü: {protocol} (SASL_SSL, SSL, PLAINTEXT)")
        if protocol == "PLAINTEXT" and env.get("EVREN_KAFKA_ALLOW_PLAINTEXT") != "1":
            raise KafkaConfigError("PLAINTEXT yalnızca EVREN_KAFKA_ALLOW_PLAINTEXT=1 ile (yerel geliştirme) kullanılır")
        mechanism = env.get("EVREN_KAFKA_SASL_MECHANISM", "SCRAM-SHA-512").upper()
        if mechanism not in _MECHANISMS:
            raise KafkaConfigError(f"Desteklenmeyen SASL yöntemi: {mechanism}")
        username = env.get("EVREN_KAFKA_USERNAME") or None
        pfile = env.get("EVREN_KAFKA_PASSWORD_FILE")
        password = _read(pfile, "Kafka parola") if pfile else None
        if protocol == "SASL_SSL" and not (username and password):
            raise KafkaConfigError("SASL_SSL için EVREN_KAFKA_USERNAME ve EVREN_KAFKA_PASSWORD_FILE gerekir")
        if bool(env.get("EVREN_KAFKA_CERT_FILE")) != bool(env.get("EVREN_KAFKA_KEY_FILE")):
            raise KafkaConfigError("İstemci sertifikası ve anahtarı birlikte verilmelidir")
        return cls(bootstrap, protocol, mechanism, username, password, env.get("EVREN_KAFKA_CA_FILE") or None,
                   env.get("EVREN_KAFKA_CERT_FILE") or None, env.get("EVREN_KAFKA_KEY_FILE") or None)

    def client_config(self, client_id: str) -> dict[str, Any]:
        cfg: dict[str, Any] = {"bootstrap.servers": self.bootstrap, "security.protocol": self.protocol,
                               "client.id": client_id}
        if self.protocol == "SASL_SSL":
            cfg |= {"sasl.mechanism": self.mechanism, "sasl.username": self.username, "sasl.password": self.password}
        if self.protocol in ("SSL", "SASL_SSL"):
            cfg["ssl.endpoint.identification.algorithm"] = "https"
            if self.ca_file:
                cfg["ssl.ca.location"] = self.ca_file
            if self.cert_file:
                cfg |= {"ssl.certificate.location": self.cert_file, "ssl.key.location": self.key_file}
        return cfg

    def producer_config(self, client_id: str) -> dict[str, Any]:
        """Kayıp ve tekrar olmadan üretim: idempotent üretici, tüm eşlerin onayı, sıkıştırma."""
        return self.client_config(client_id) | {
            "enable.idempotence": True, "acks": "all", "compression.type": "zstd", "linger.ms": 20,
            "message.max.bytes": MAX_MESSAGE_BYTES + 100_000, "delivery.timeout.ms": 60_000}

    def consumer_config(self, client_id: str, group_id: str) -> dict[str, Any]:
        """En az bir kez işleme: ofset yalnızca veritabanı işlemi tamamlanınca saklanır (otomatik saklama kapalı)."""
        return self.client_config(client_id) | {
            "group.id": group_id, "enable.auto.commit": True, "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest", "isolation.level": "read_committed",
            "max.poll.interval.ms": 300_000, "session.timeout.ms": 45_000}

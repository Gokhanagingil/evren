"""Alım geçidi testleri: göç + tohum uygulanmış PostgreSQL ve ``EVREN_TEST_PG=1``. Kafka yerine taklit üretici.

Geçit gerçek bağlantı havuzuyla ve ``evren_app_ingest`` rolüyle çalışır (üretimle aynı en az yetki). Her test kendi
kimlik bilgisini ve çalışmasını açar; test sonunda ``gwtest-`` önekli kayıtlar sahip rolüyle silinir (denetim izi
salt eklemedir, kalır).
"""
from __future__ import annotations

import dataclasses
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/ingest-gateway"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from evren_core.credentials import new_token, token_hint, token_sha256  # noqa: E402
from evren_core.kafka_client import PublishError  # noqa: E402
from evren_core.messaging import KafkaSettings  # noqa: E402
from evren_core.metadata import load_metadata  # noqa: E402
from evren_gateway import GatewaySettings, create_app  # noqa: E402


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes, Any]] = []
        self.fail = False
        self.is_ready = True

    def publish(self, messages: Any, timeout: float) -> None:
        if self.fail:
            raise PublishError("test")
        self.sent.extend(messages)

    def ready(self, timeout: float = 2.0) -> bool:
        return self.is_ready

    def close(self) -> None:
        pass


@pytest.fixture(scope="session")
def metadata():
    return load_metadata(REPO / "metadata")


def settings(**kw: Any) -> GatewaySettings:
    return GatewaySettings(db_conninfo="", kafka=KafkaSettings("kafka:9092", "PLAINTEXT"), db_pool_size=2,
                           max_batch=5, max_body_bytes=64 * 1024, **kw)


@pytest.fixture
def gateway(metadata) -> Iterator[tuple[TestClient, FakePublisher]]:
    pub = FakePublisher()
    with TestClient(create_app(settings(), publisher=pub, metadata=metadata)) as client:
        yield client, pub
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM ingest.source_run WHERE opened_by LIKE 'gwtest-%'")
        c.execute("DELETE FROM ingest.connector_credential WHERE name LIKE 'gwtest-%'")


@pytest.fixture
def retired_gateway(metadata) -> Iterator[TestClient]:
    md = dataclasses.replace(metadata, data_sources=[{**d, "lifecycle": "retired"} if d["name"] == "Defender" else d
                                                     for d in metadata.data_sources])
    with TestClient(create_app(settings(), publisher=FakePublisher(), metadata=md)) as client:
        yield client
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM ingest.connector_credential WHERE name LIKE 'gwtest-%'")


def issue(data_source: str = "Zabbix", *, per_minute: int = 60000, expires: timedelta = timedelta(days=30),
          revoked: bool = False) -> tuple[str, dict[str, str]]:
    """Doğrudan sahip rolüyle kimlik verir; (kimlik adı, Authorization başlığı)."""
    token = new_token()
    name = f"gwtest-{uuid.uuid4().hex[:10]}"
    created = datetime.now(UTC) - timedelta(days=60)
    with psycopg.connect(autocommit=True) as c:
        c.execute("""INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint,
                         max_records_per_minute, created_by, created_at, expires_at, revoked_at, revoked_by)
                     VALUES (%s, %s, %s, %s, %s, 'test', %s, %s, %s, %s)""",
                  (name, data_source, token_sha256(token), token_hint(token), per_minute, created,
                   datetime.now(UTC) + expires, datetime.now(UTC) if revoked else None, "test" if revoked else None))
    return name, {"Authorization": f"Bearer {token}"}


def run_env(data_source: str, run_id: uuid.UUID, *, started: bool = True, count: int = 0, completed: bool = True,
            errors: int = 0) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {"specversion": "1.0", "id": str(uuid.uuid4()), "source": "urn:evren:connector:test", "time": now,
            "type": "evren.ingest.run.started.v1" if started else "evren.ingest.run.completed.v1",
            "data": {"data_source": data_source, "run_id": str(run_id), "mode": "snapshot", "completed": completed,
                     "record_count": count, "error_count": errors, "started_at": now}}


def record_env(data_source: str, run_id: uuid.UUID, key: str, **attrs: Any) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {"specversion": "1.0", "id": str(uuid.uuid4()), "source": "urn:evren:connector:test", "time": now,
            "type": "evren.ingest.record.v1",
            "data": {"data_source": data_source, "run_id": str(run_id), "source_key": key,
                     "class_hint": "cmdb_ci_linux_server", "attributes": attrs or {"name": key}, "observed_at": now}}


def db(query: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(autocommit=True) as c:
        return c.execute(query, params).fetchall()

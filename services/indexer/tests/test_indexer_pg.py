"""Arama dizinleyicisi (U3, ADR-0018): gerçek PostgreSQL (``evren_app_indexer`` rolü), taklit OpenSearch ve Kafka.

CI'lar test işleminin içinde uzlaştırıcıyla oluşturulur; işlem sonunda geri alınır. Gerçek OpenSearch ile akış
``tools/dev_stack.py smoke`` ve ``packages/evren-core/tests/test_search_os.py`` ile sınanır.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/reconciler", REPO / "services/indexer"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)

import psycopg  # noqa: E402

from evren_core.envelope import IngestRecord  # noqa: E402
from evren_core.metadata import load_metadata  # noqa: E402
from evren_core.opensearch_client import OpenSearchError  # noqa: E402
from evren_core.search import ALIAS, MAPPING_VERSION  # noqa: E402
from evren_indexer import CiIndexer, IndexerConfig, IndexerWorker  # noqa: E402
from evren_reconciler import ReconcilerStore  # noqa: E402

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


class FakeOpenSearch:
    def __init__(self) -> None:
        self.indices: dict[str, dict[str, dict[str, Any]]] = {}
        self.aliases: dict[str, str] = {}
        self.fail = False

    def _target(self, name: str) -> str:
        return self.aliases.get(name, name)

    def create_index(self, index: str, body: Any) -> None:
        self.indices[index] = {}

    def delete_index(self, index: str) -> None:
        self.indices.pop(index, None)

    def alias_targets(self, alias: str) -> list[str]:
        return [self.aliases[alias]] if alias in self.aliases else []

    def point_alias(self, alias: str, index: str) -> list[str]:
        old = self.alias_targets(alias)
        self.aliases[alias] = index
        return [o for o in old if o != index]

    def bulk_index(self, index: str, docs: Any, *, refresh: bool = False) -> int:
        if self.fail:
            raise OpenSearchError("test")
        docs = list(docs)
        for d in docs:
            self.indices[self._target(index)][d["sys_id"]] = d
        return len(docs)

    def request(self, method: str, path: str, *a: Any, **k: Any) -> None:
        return None

    def docs(self) -> dict[str, dict[str, Any]]:
        return self.indices[self.aliases[ALIAS]]


@pytest.fixture(scope="session")
def metadata():
    return load_metadata(REPO / "metadata")


@pytest.fixture
def conn():
    with psycopg.connect(autocommit=True) as c, c.transaction(force_rollback=True):
        yield c


def make_cis(conn, metadata, n: int = 3) -> list[str]:
    conn.execute("SET ROLE evren_app_reconciler")
    store = ReconcilerStore(conn, metadata, clock=lambda: NOW)
    run = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    out = []
    for i in range(n):
        out.append(store.apply(IngestRecord(
            data_source="BMC", run_id=run, source_key=f"idx-{i}", class_hint="cmdb_ci_linux_server", observed_at=NOW,
            attributes={"name": f"idx-sunucu-{i}", "serial_number": f"CZIDX000{i}", "ip_address": f"10.77.0.{i + 1}",
                        "os": "Red Hat Enterprise Linux 9.4"})).ci_sys_id)
    conn.execute("SET ROLE evren_app_indexer")
    return out


def test_reindex_builds_versioned_index_and_moves_alias(conn, metadata):
    cis = make_cis(conn, metadata)
    fake = FakeOpenSearch()
    ix = CiIndexer(conn, metadata, fake, clock=lambda: NOW)
    first = ix.ensure()
    assert first and first.startswith(f"{ALIAS}-v{MAPPING_VERSION}-") and fake.aliases[ALIAS] == first
    doc = fake.docs()[cis[0]]
    assert doc["name"] == "idx-sunucu-0" and doc["ip_address"] == "10.77.0.1" and doc["sources"] == ["BMC"]
    assert doc["class_label"] != "cmdb_ci_linux_server" and doc["os"].startswith("Red Hat")
    assert ix.ensure() is None                      # güncel sürüm: yeniden dizinleme yok
    ix.clock = lambda: NOW + timedelta(seconds=1)
    second, _n = ix.reindex()
    assert fake.aliases[ALIAS] == second and first not in fake.indices   # eski dizin silindi


def test_event_batch_indexes_current_state_and_ignores_bad_keys(conn, metadata):
    cis = make_cis(conn, metadata, 2)
    fake = FakeOpenSearch()
    ix = CiIndexer(conn, metadata, fake, clock=lambda: NOW)
    ix.ensure()
    fake.indices[fake.aliases[ALIAS]].clear()
    assert ix.index_ids([cis[0], cis[0], "../x", "f" * 32]) == 1   # tekrar ve geçersiz anahtar elenir, yok olan atlanır
    assert list(fake.docs()) == [cis[0]]


class Msg:
    def __init__(self, key: bytes, offset: int, partition: int = 0) -> None:
        self._k, self._o, self._p = key, offset, partition

    def key(self) -> bytes:
        return self._k

    def offset(self) -> int:
        return self._o

    def partition(self) -> int:
        return self._p

    def topic(self) -> str:
        return "evren.cmdb.ci.changed.v1"

    def error(self) -> None:
        return None


class FakeConsumer:
    def __init__(self, msgs: list[Msg]) -> None:
        self.msgs, self.stored, self.seeks = msgs, [], []

    def consume(self, num_messages: int, timeout: float) -> list[Msg]:
        out, self.msgs = self.msgs, []
        return out

    def store_offsets(self, message: Msg) -> None:
        self.stored.append(message.offset())

    def seek(self, tp: Any) -> None:
        self.seeks.append(tp)


def worker(fake: FakeOpenSearch, metadata) -> IndexerWorker:
    cfg = IndexerConfig(conninfo="", opensearch=None, worker_id="test")  # type: ignore[arg-type]
    return IndexerWorker(cfg, metadata, consumer_factory=lambda: None, client=fake,  # type: ignore[arg-type]
                         topic_partition=lambda t, p, o: (t, p, o))


def test_offsets_stored_only_after_successful_write(conn, metadata):
    cis = make_cis(conn, metadata, 2)
    fake = FakeOpenSearch()
    ix = CiIndexer(conn, metadata, fake, clock=lambda: NOW)
    ix.ensure()
    w = worker(fake, metadata)
    ok = FakeConsumer([Msg(cis[0].encode(), 5), Msg(cis[1].encode(), 6, 1)])
    assert w.poll_once(ok, ix) == 2 and ok.stored == [5, 6]
    fake.fail = True
    bad = FakeConsumer([Msg(cis[0].encode(), 9, 2), Msg(cis[1].encode(), 7, 2), Msg(cis[1].encode(), 3, 1)])
    with pytest.raises(OpenSearchError):
        w.poll_once(bad, ix)
    assert bad.stored == [] and sorted(bad.seeks) == [("evren.cmdb.ci.changed.v1", 1, 3), ("evren.cmdb.ci.changed.v1", 2, 7)]


def test_heartbeat_is_written_with_indexer_role(conn, metadata):
    conn.execute("SET ROLE evren_app_indexer")
    w = worker(FakeOpenSearch(), metadata)
    w._heartbeat(conn)
    assert conn.execute("SELECT kind FROM ingest.worker_heartbeat WHERE worker_id = 'test'").fetchone()[0] == "indexer"


@pytest.mark.parametrize("statement", [
    "UPDATE cmdb.cmdb_ci SET name = 'x'",
    "SELECT source_key FROM ingest.identity_link",
    "SELECT count(*) FROM ingest.source_record",
    "SELECT count(*) FROM ingest.connector_credential",
    "SELECT count(*) FROM cmdb.ci_security_posture",
])
def test_indexer_role_is_read_only(conn, statement):
    conn.execute("SET ROLE evren_app_indexer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute(statement)

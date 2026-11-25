"""Testler için kendi kendine yeterli senaryolar (API ve tarayıcı testleri ortak kullanır)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from evren_core.envelope import IngestRecord
from evren_core.metadata import load_metadata
from evren_reconciler import ReconcilerStore

REPO = Path(__file__).resolve().parents[3]


def make_review_item() -> tuple[str, str, str]:
    """Kendi mükerrer senaryosu: iki SN kaydı aynı adla, izleme kaydı ad ile ikisine de uyar → inceleme."""
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    now = datetime.now(UTC)
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        sn = store.start_run(uuid.uuid4(), "ServiceNow")
        for sid, serial in ((a, f"CZJ{tag}A"), (b, f"CZJ{tag}B")):
            store.import_record(IngestRecord(data_source="ServiceNow", run_id=sn, source_key=sid,
                                             class_hint="cmdb_ci_linux_server",
                                             attributes={"name": f"dup-{tag}", "serial_number": serial}, observed_at=now), sid)
        zb = store.start_run(uuid.uuid4(), "Zabbix")
        res = store.apply(IngestRecord(data_source="Zabbix", run_id=zb, source_key=f"z-{tag}",
                                       class_hint="cmdb_ci_linux_server", attributes={"name": f"dup-{tag}"}, observed_at=now))
        assert res.outcome == "review"
        item = conn.execute("SELECT sys_id FROM ingest.match_candidate WHERE source_key = %s AND status = 'open'",
                            (f"z-{tag}",)).fetchone()[0]
    return item, a, f"z-{tag}"



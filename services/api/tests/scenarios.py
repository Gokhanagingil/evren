"""Testler için kendi kendine yeterli senaryolar (API ve tarayıcı testleri ortak kullanır)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

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




def make_wrong_merge() -> tuple[str, dict[str, str], str]:
    """Kendi yanlış birleştirme senaryosu: keşif ve sanallaştırma kaydı aynı seriyle tek CI'a bağlanır.

    Döner: (CI sys_id, {kaynak: bağ sys_id}, etiket). Sanallaştırma kaydı bellek ve CPU bildirir (yalnız o).
    """
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        bmc = store.start_run(uuid.uuid4(), "BMC")
        ci = store.apply(IngestRecord(data_source="BMC", run_id=bmc, source_key=f"wm-b-{tag}",
                                      class_hint="cmdb_ci_linux_server", observed_at=now,
                                      attributes={"name": f"wm-{tag}", "serial_number": f"CZW{tag}",
                                                  "os": "RHEL 9.4"})).ci_sys_id
        vc = store.start_run(uuid.uuid4(), "vCenter")
        res = store.apply(IngestRecord(data_source="vCenter", run_id=vc, source_key=f"wm-v-{tag}",
                                       class_hint="cmdb_ci_linux_server", observed_at=now,
                                       attributes={"name": f"wm-{tag}", "serial_number": f"CZW{tag}", "ram": 16384}))
        assert res.ci_sys_id == ci, res
        links = dict(conn.execute("SELECT data_source, sys_id FROM ingest.identity_link WHERE ci_sys_id = %s AND active",
                                  (ci,)).fetchall())
    return ci, links, tag


def apply_pending_splits() -> list[str]:
    """Uzlaştırıcıyı bir tur çalıştırır (üretimde sürekli çalışan servis); sonuç CI'larını döner."""
    md = load_metadata(REPO / "metadata")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        return [r.ci_sys_id for r in ReconcilerStore(conn, md).apply_splits() if r.outcome == "split"]


def apply_pending_creates() -> list[str]:
    """Uzlaştırıcıyı bir tur çalıştırır (elle oluşturma istekleri); sonuç türlerini döner (U6.1)."""
    md = load_metadata(REPO / "metadata")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        return [r.outcome for r in ReconcilerStore(conn, md).apply_manual_creates()]


def apply_pending_relations() -> list[str]:
    """Uzlaştırıcıyı bir tur çalıştırır (elle ilişki istekleri); sonuç türlerini döner (U6.2)."""
    md = load_metadata(REPO / "metadata")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        return [r.outcome for r in ReconcilerStore(conn, md).apply_manual_relations()]


def make_ci() -> tuple[str, str]:
    """Tek keşif kaydından oluşan, testin kendine ait CI'ı. Döner: (sys_id, ad)."""
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        run = store.start_run(uuid.uuid4(), "BMC")
        ci = store.apply(IngestRecord(data_source="BMC", run_id=run, source_key=f"ed-{tag}",
                                      class_hint="cmdb_ci_linux_server", observed_at=datetime.now(UTC),
                                      attributes={"name": f"ed-{tag}", "serial_number": f"CZE{tag}", "os": "RHEL 9.4"}))
    return ci.ci_sys_id, f"ed-{tag}"


def apply_pending_edits() -> list[str]:
    """Uzlaştırıcıyı bir tur çalıştırır (elle düzenleme istekleri); sonuç notlarını döner."""
    md = load_metadata(REPO / "metadata")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        return [r.summary for r in ReconcilerStore(conn, md).apply_manual_edits()]


def make_sn_pair(*, same_serial: bool) -> tuple[str, str, str]:
    """İki ServiceNow ilk yükleme kaydı, aynı ad. ``same_serial``: aynı seri (kesin mükerrer) veya farklı seri
    (ad çakışması). Döner: (sys_id_a, sys_id_b, ad)."""
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        sn = store.start_run(uuid.uuid4(), "ServiceNow")
        for sid, serial in ((a, f"CZP{tag}A"), (b, f"CZP{tag}{'A' if same_serial else 'B'}")):
            store.import_record(IngestRecord(data_source="ServiceNow", run_id=sn, source_key=sid,
                                             class_hint="cmdb_ci_linux_server", observed_at=datetime.now(UTC),
                                             attributes={"name": f"pair-{tag}", "serial_number": serial}), sid)
    return a, b, f"pair-{tag}"


def make_owned_ci(user_name: str | None, *, used_for: str = "Production", cls: str = "cmdb_ci_linux_server",
                  **extra: str) -> tuple[str, str, str | None]:
    """Sorumlusu ``user_name`` olan (None: sahipsiz) testin kendine ait CI'ı; ITSM rolü sahibi yazar (U6.4).
    Kullanıcı ``ref.sys_user``'da yoksa açılır. Döner: (CI sys_id, ad, sys_user sys_id)."""
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    user = None
    with psycopg.connect(autocommit=True) as conn:
        if user_name:
            conn.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'evren') "
                         "ON CONFLICT DO NOTHING", (uuid.uuid4().hex, user_name, user_name.replace("-", " ").title()))
            (user,) = conn.execute("SELECT sys_id FROM ref.sys_user WHERE lower(user_name) = lower(%s)",
                                   (user_name,)).fetchone()
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        run = store.start_run(uuid.uuid4(), "ServiceNow")
        attrs = {"name": f"onay-{tag}", "serial_number": f"CZO{tag}", "used_for": used_for, **extra}
        if user:
            attrs["owned_by"] = user
        ci = store.apply(IngestRecord(data_source="ServiceNow", run_id=run, source_key=f"onay-{tag}", class_hint=cls,
                                      observed_at=datetime.now(UTC), attributes=attrs))
    return ci.ci_sys_id, f"onay-{tag}", user


def apply_pending_attestations() -> list[str]:
    """Uzlaştırıcıyı bir tur çalıştırır (sahiplik onayı istekleri); sonuç türlerini döner (U6.4)."""
    md = load_metadata(REPO / "metadata")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        return [r.outcome for r in ReconcilerStore(conn, md).apply_attestations()]


def make_stale_case(stage: str = "candidate", days: int = 50) -> tuple[str, str, str]:
    """Testin kendine ait CI'ı ve onun için açık eskime önerisi (U7.1). Öneri sahip rolüyle eklenir (uzlaştırıcının
    yazacağı satırın aynısı); CI'ın keşif kaydı ``active`` olduğu için uzlaştırıcı emekliye ayırmayı reddeder.
    Döner: (CI sys_id, ad, öneri sys_id)."""
    ci, name = make_ci()
    seen = datetime.now(UTC).replace(microsecond=0) - timedelta(days=days)
    with psycopg.connect(autocommit=True) as conn:
        (case,) = conn.execute(
            """INSERT INTO insight.staleness_case (ci_sys_id, stage, rule_class, last_seen, observations)
               VALUES (%s, %s, 'cmdb_ci_hardware', %s, %s) RETURNING sys_id""",
            (ci, stage, seen, Jsonb([{"data_source": "BMC", "last_seen": seen.isoformat(), "status": "missing",
                                      "source_ok": True}]))).fetchone()
    return ci, name, case


def apply_pending_retirements() -> list[str]:
    """Uzlaştırıcıyı bir tur çalıştırır (eskime kararları); sonuç türlerini döner (U7.1)."""
    md = load_metadata(REPO / "metadata")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        return [r.outcome for r in ReconcilerStore(conn, md).apply_retirements()]


def make_dependent_pair() -> tuple[str, str, str]:
    """Testin kendine ait sunucusu ve ona bağlı ağ adaptörü (U7.2, ADR-0029; keşif kaynağı, tek çalışma).
    Döner: (sunucu sys_id, adaptör sys_id, sunucu adı)."""
    from evren_core.envelope import ParentRef
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        run = store.start_run(uuid.uuid4(), "BMC")
        now = datetime.now(UTC)
        host = store.apply(IngestRecord(data_source="BMC", run_id=run, source_key=f"dp-{tag}", observed_at=now,
                                        class_hint="cmdb_ci_linux_server",
                                        attributes={"name": f"dp-{tag}", "serial_number": f"CZD{tag}"}))
        nic = store.apply(IngestRecord(data_source="BMC", run_id=run, source_key=f"dp-{tag}/eth0", observed_at=now,
                                       class_hint="cmdb_ci_network_adapter", parent=ParentRef(source_key=f"dp-{tag}"),
                                       attributes={"name": "eth0", "mac_address": f"02:00:{tag[:2]}:{tag[2:4]}:"
                                                                                   f"{tag[4:6]}:{tag[6:8]}"}))
    return host.ci_sys_id, nic.ci_sys_id, f"dp-{tag}"


def make_service_chain() -> dict[str, str]:
    """Testin kendine ait CSDM zinciri (U7.3, ADR-0030), ServiceNow kayıtları gibi (sys_id korunur):
    iş uygulaması —Consumes→ uygulama hizmeti —Depends on→ sunucu; iş hizmeti —Depends on→ uygulama hizmeti; döngü için
    sunucu —Depends on→ iş uygulaması. Döner: ad → sys_id."""
    from evren_core.envelope import RelationRef
    md = load_metadata(REPO / "metadata")
    ids = {k: uuid.uuid4().hex for k in ("server", "app_service", "business_app", "business_service")}
    tag = ids["server"][:6]
    rel = lambda t, target: RelationRef(type=t, direction="parent", target_source_key=ids[target])  # noqa: E731
    spec = [
        ("server", "cmdb_ci_linux_server", {"name": f"etki-srv-{tag}", "serial_number": f"ETK{tag}",
                                            "used_for": "Production"}, [rel("Depends on::Used by", "business_app")]),
        ("app_service", "cmdb_ci_service_auto", {"name": f"etki-hizmet-{tag}", "used_for": "Production"},
         [rel("Depends on::Used by", "server")]),
        ("business_app", "cmdb_ci_business_app", {"name": f"etki-uygulama-{tag}"},
         [rel("Consumes::Consumed by", "app_service")]),
        ("business_service", "cmdb_ci_service_business", {"name": f"etki-is-{tag}"},
         [rel("Depends on::Used by", "app_service")]),
    ]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        run = store.start_run(uuid.uuid4(), "ServiceNow")
        now = datetime.now(UTC)
        for key, cls, attrs, rels in spec:
            store.apply(IngestRecord(data_source="ServiceNow", run_id=run, source_key=ids[key], class_hint=cls,
                                     observed_at=now, attributes=attrs, relations=rels))
        store.finish_run(run)
    return ids

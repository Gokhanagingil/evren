"""Ürün değişimi, gerçek PostgreSQL uzlaştırıcısında (F1-13, ADR-0009): gölge → etkin + devir → emekli.

Kurallar dosyası hiç değişmez; yalnız veri kaynağının yaşam döngüsü ve rol sırası değişir. Her aşamadan sonra
``rereconcile_source`` bağlı CI'ları güncel metadata ile yeniden uzlaştırır.
"""
from __future__ import annotations

import copy
import hashlib
import uuid
from pathlib import Path

from test_store_pg import NOW, fetch, fetch_audit, rec

from evren_reconciler import ReconcilerStore

REPO = Path(__file__).resolve().parents[3]


def _md(metadata, states: dict[str, tuple[str, int]]):
    md = copy.deepcopy(metadata)
    if not any(s["name"] == "YeniKesif" for s in md.data_sources):
        md.data_sources.append({"name": "YeniKesif", "label_tr": "Yeni keşif ürünü (test)", "role": "Discovery",
                                "role_rank": 2, "lifecycle": "shadow", "connector": "yeni", "mappings": []})
    for s in md.data_sources:
        if s["name"] in states:
            s["lifecycle"], s["role_rank"] = states[s["name"]]
    return md


def _set(conn, name: str, lifecycle: str, rank: int) -> None:
    """Değişiklik setinin yayınlanmasını taklit eder (sahip rolüyle)."""
    conn.execute("RESET ROLE")
    conn.execute("UPDATE ingest.data_source SET lifecycle = %s, role_rank = %s WHERE name = %s", (lifecycle, rank, name))
    conn.execute("SET ROLE evren_app_reconciler")


def _winner(conn, ci: str, attr: str) -> tuple[str, str]:
    row = fetch(conn, "SELECT winners->%s->>'source', COALESCE(to_jsonb(c)->>%s, c.class_attributes->>%s) "
                      "FROM cmdb.ci_reconciliation_state s JOIN cmdb.cmdb_ci c ON c.sys_id = s.ci_sys_id "
                      "WHERE s.ci_sys_id = %s", attr, attr, attr, ci)[0]
    return row[0], row[1]


def test_product_swap_without_touching_rules(conn, metadata):
    rules = {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest()
             for f in ("metadata/reconciliation_rules.yaml", "metadata/identification_rules.yaml")}
    conn.execute("""INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank, lifecycle, connector)
                    VALUES ('YeniKesif', 'Yeni keşif ürünü (test)', 'discovery', 'Discovery', 2, 'shadow', 'yeni')""")
    conn.execute("SET ROLE evren_app_reconciler")

    # 1) Gölge: yeni ürün kimliği çözülür, değer yazmaz
    store = ReconcilerStore(conn, _md(metadata, {}), clock=lambda: NOW)
    bmc = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    ci = store.apply(rec("BMC", "ps-b1", bmc, name="srv-ps", serial_number="CZJPS0001", os="Red Hat Enterprise Linux 9.4",
                         cpu_type="Xeon Gold")).ci_sys_id
    new = store.start_run(uuid.uuid4(), "YeniKesif", started_at=NOW)
    assert store.apply(rec("YeniKesif", "ps-y1", new, name="srv-ps", serial_number="CZJPS0001",
                           os="RHEL 9.4")).ci_sys_id == ci
    assert _winner(conn, ci, "os") == ("BMC", "Red Hat Enterprise Linux 9.4")

    # 2) Etkin + devir: yeni ürün rol sırası 1, eski ürün devrediliyor (2) → değer yeni üründen
    _set(conn, "BMC", "retiring", 3)
    _set(conn, "YeniKesif", "active", 1)
    store = ReconcilerStore(conn, _md(metadata, {"BMC": ("retiring", 3), "YeniKesif": ("active", 1)}), clock=lambda: NOW)
    totals = store.rereconcile_source("YeniKesif")
    assert totals["cis"] == 1 and totals["changes"] >= 1
    assert _winner(conn, ci, "os") == ("YeniKesif", "RHEL 9.4")
    assert _winner(conn, ci, "cpu_type") == ("BMC", "Xeon Gold")  # yeni ürün vermiyor: devredilen ürün yedek
    audit = fetch_audit(conn, "SELECT old_value, new_value, data_source, reason FROM audit.ci_change "
                              "WHERE ci_sys_id = %s AND attribute = 'os' ORDER BY id DESC LIMIT 1", ci)[0]
    assert audit == ("Red Hat Enterprise Linux 9.4", "RHEL 9.4", "YeniKesif", "reconciliation")
    assert store.registry.may_create_ci("BMC") is False  # devredilen ürün yeni CI açamaz

    # 3) Emeklilik: eski ürün yazmaz; yalnız onun verdiği alan sahipsiz kalır (silinmez, sayısı raporlanır)
    _set(conn, "BMC", "retired", 3)
    store = ReconcilerStore(conn, _md(metadata, {"BMC": ("retired", 3), "YeniKesif": ("active", 1)}), clock=lambda: NOW)
    totals = store.rereconcile_source("BMC")
    assert totals["orphaned"] == 1  # cpu_type
    assert _winner(conn, ci, "os") == ("YeniKesif", "RHEL 9.4")
    source, value = _winner(conn, ci, "cpu_type")
    assert source is None and value == "Xeon Gold"
    assert {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest() for f in rules} == rules
    conn.execute("RESET ROLE")

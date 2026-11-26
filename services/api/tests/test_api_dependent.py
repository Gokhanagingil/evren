"""Bağımlı CI kimliği (U7.2, ADR-0029): CI ayrıntısında bağımlı CI'lar ve ana CI. Veriler sentetiktir."""
from __future__ import annotations

from .scenarios import make_dependent_pair


def test_detail_shows_dependents_and_parent(reader):
    host, nic, name = make_dependent_pair()
    h = reader.get(f"/api/v1/ci/{host}").json()
    assert [(d["sys_id"], d["class"], d["key"].startswith("mac_address: 02:00:")) for d in h["dependents"]] == [
        (nic, "cmdb_ci_network_adapter", True)]
    assert h["depends_on"] == []
    n = reader.get(f"/api/v1/ci/{nic}").json()
    assert [(p["sys_id"], p["name"]) for p in n["depends_on"]] == [(host, name)] and n["dependents"] == []
    ref = next(a for a in n["attributes"] if a["attribute"] == "cmdb_ci")
    assert ref["value"] == host and ref["display"] == name  # ana CI başvurusu uzlaştırmadan geçer, adla gösterilir

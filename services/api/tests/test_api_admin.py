"""Yönetim paneli ve teknik belgeler (U8.2, ADR-0033): kişiye düşen işler yalnızca yetkisi olduğu işlerdir; teknik
belgeler oturumla ve yalnızca docs altından, izinli adla verilir. Veriler sentetiktir."""
from __future__ import annotations

import pytest

from .conftest import make_client
from .scenarios import make_stale_case


def ids(c) -> dict[str, dict]:
    r = c.get("/api/v1/admin/attention")
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


def test_attention_lists_only_work_the_person_may_do():
    with make_client(("evren_reader",), "salt-okuyucu") as reader:
        assert ids(reader) == {}  # okuyucuya düşen karar yok
    with make_client(("evren_reader", "evren_match_reviewer"), "eslestirici") as reviewer:
        got = ids(reviewer)
        assert set(got) <= {"queue"}
        if "queue" in got:  # demo verisinde açık kuyruk kaydı var
            assert got["queue"]["href"] == "#/kuyruk" and got["queue"]["count"] > 0
    with make_client(("evren_operator",), "operator") as operator:
        got = ids(operator)
        assert set(got) <= {"system"}
        if "system" in got:
            assert got["system"]["href"] == "#/sistem-durumu" and got["system"]["hint"]


def test_staleness_candidates_reach_the_data_steward():
    make_stale_case("candidate")
    with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu") as steward:
        got = ids(steward)
        assert got["staleness"]["count"] >= 1 and got["staleness"]["href"] == "#/eskime"
        tones = [i["tone"] for i in steward.get("/api/v1/admin/attention").json()["items"]]
        assert tones == sorted(tones, key={"bad": 0, "warn": 1, "info": 2}.get)  # önce ciddi olanlar


def test_me_reports_admin_capabilities_for_the_panel():
    with make_client(("evren_reader", "evren_schema_designer", "evren_data_steward"), "tasarimci") as c:
        can = c.get("/api/v1/me").json()["can"]
        assert can["schema_draft"] is True and can["schema_approve"] is False
        assert can["connector_admin"] is True and can["iam_admin"] is False and can["system"] is True


def test_technical_docs_are_listed_and_served_by_name(client):
    lst = client.get("/api/v1/docs").json()
    assert {"name": "06-guvenlik", "title": lst["docs"][5]["title"]} in lst["docs"]
    assert any(d["name"] == "adr/0032-sistem-durumu" for d in lst["adrs"])
    doc = client.get("/api/v1/docs/06-guvenlik").json()
    assert doc["base"] == "" and doc["markdown"].startswith("# ") and "STRIDE" in doc["markdown"]
    adr = client.get("/api/v1/docs/adr/0032-sistem-durumu").json()
    assert adr["base"] == "adr" and adr["title"].startswith("ADR-0032")
    assert client.get("/api/v1/docs/adr/README").status_code == 200


@pytest.mark.parametrize(("name", "code"), [
    ("..%2FCLAUDE", 400), ("adr/..%2F..%2FCLAUDE", 400), ("yok-boyle-bir-belge", 404), ("kilavuz/kilavuz", 400),
    ("06-guvenlik.md", 404), (".gizli", 400)])
def test_doc_names_cannot_escape_docs(client, name, code):
    r = client.get(f"/api/v1/docs/{name}")
    assert r.status_code == code, (name, r.status_code, r.text)

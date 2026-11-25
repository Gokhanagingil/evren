"""Küresel arama (U3, ADR-0018): OpenSearch yolu (taklit istemci), yetki süzgeçleri, PostgreSQL yedeği."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from evren_api import create_app
from evren_api.config import Settings
from evren_core.opensearch_client import OpenSearchError
from evren_core.search import ALIAS


class FakeSearch:
    def __init__(self, hits: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.hits, self.fail, self.calls = hits or [], fail, []

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((index, body))
        if self.fail:
            raise OpenSearchError("test", 503)
        return {"hits": {"hits": [{"_source": h} for h in self.hits]}}


def client(search: Any = None, roles: str = "evren_reader") -> TestClient:
    env = {"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": roles, "EVREN_DB_POOL_SIZE": "2"}
    return TestClient(create_app(Settings.from_env(env), search_client=search))


HIT = {"sys_id": "a" * 32, "name": "ist-db-01", "sys_class_name": "cmdb_ci_linux_server", "ip_address": "10.0.0.1",
       "fqdn": "ist-db-01.kurum.local", "os": "RHEL", "sources": ["BMC"]}


def test_opensearch_path_passes_readable_classes_and_visible_fields():
    fake = FakeSearch([HIT])
    with client(fake) as c:
        r = c.get("/api/v1/search", params={"q": "ist-db", "limit": 5})
        assert r.status_code == 200
        body = r.json()
        assert body["engine"] == "opensearch" and body["degraded"] is False
        assert body["items"][0] == {"sys_id": "a" * 32, "class": "cmdb_ci_linux_server", "class_label": "Linux sunucu",
                                    "sources": ["BMC"], "name": "ist-db-01", "ip_address": "10.0.0.1",
                                    "fqdn": "ist-db-01.kurum.local", "os": "RHEL"}
        index, query = fake.calls[0]
        allowed = query["query"]["bool"]["filter"][0]["terms"]["sys_class_name"]
        assert index == ALIAS and query["size"] == 5 and "cmdb_ci_linux_server" in allowed and "cmdb_ci" in allowed


def test_engine_bypass_cannot_leak_unreadable_classes():
    """Motor süzgeci atlansa bile (ör. yanlış eşleme) okunamayan sınıf yanıta girmez."""
    fake = FakeSearch([{**HIT, "sys_class_name": "olmayan_sinif"}, HIT])
    with client(fake) as c:
        assert [i["sys_id"] for i in c.get("/api/v1/search", params={"q": "x"}).json()["items"]] == ["a" * 32]


def test_restricted_field_is_neither_searched_nor_returned(monkeypatch):
    fake = FakeSearch([HIT])
    with client(fake) as c:
        md = c.app.state.metadata
        original = md.fields_of

        def fields_of(cls: str, **kw: Any) -> dict[str, Any]:
            out = dict(original(cls, **kw))
            if "ip_address" in out:
                out["ip_address"] = {**out["ip_address"], "sensitivity": "restricted"}
            return out

        monkeypatch.setattr(md, "fields_of", fields_of)
        item = c.get("/api/v1/search", params={"q": "10.0.0.1"}).json()["items"][0]
        assert item["ip_address"] is None and item["name"] == "ist-db-01"
        should = fake.calls[0][1]["query"]["bool"]["should"]
        searched = {k for clause in should for spec in clause.values() for k in (spec if isinstance(spec, dict) else {})}
        searched |= {f for clause in should for f in clause.get("multi_match", {}).get("fields", [])}
        assert searched and not any(f.startswith("ip_address") for f in searched)


def test_opensearch_outage_degrades_to_postgresql():
    with client(FakeSearch(fail=True)) as c:
        body = c.get("/api/v1/search", params={"q": "İST-DB"}).json()
        assert body["engine"] == "postgresql" and body["degraded"] is True
        assert any(i["name"].startswith("ist-db") for i in body["items"])


@pytest.mark.parametrize(("q", "expect_any"), [("İST-DB", True), ("ıst-db", True), ("%", False), ("_", False)])
def test_postgresql_fallback_folds_turkish_and_escapes_wildcards(q, expect_any):
    with client(None) as c:
        body = c.get("/api/v1/search", params={"q": q}).json()
        assert body["engine"] == "postgresql" and body["degraded"] is False
        assert bool(body["items"]) is expect_any


def test_search_requires_read_permission_and_valid_input():
    with client(FakeSearch([HIT]), roles="") as c:
        assert c.get("/api/v1/search", params={"q": "ist"}).status_code == 403
    with client(FakeSearch([HIT])) as c:
        assert c.get("/api/v1/search").status_code == 422
        assert c.get("/api/v1/search", params={"q": "x" * 201}).status_code == 422
        assert c.get("/api/v1/search", params={"q": "x", "limit": 51}).status_code == 422

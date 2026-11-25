"""CI arama (U3, ADR-0018): dizin belgesi, sorgu kurulumu, OpenSearch istemcisinin güvenlik davranışı.

Gerçek OpenSearch ile alaka testleri ``test_search_os.py``'dedir (``EVREN_TEST_OPENSEARCH_URL``).
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from evren_core.metadata import load_metadata
from evren_core.opensearch_client import OpenSearch, OpenSearchConfigError, OpenSearchError, OpenSearchSettings
from evren_core.search import (
    ALIAS,
    INDEX_BODY,
    MAPPING_VERSION,
    build_query,
    ci_document,
    current_version,
    hidden_fields,
    index_name,
)

ROW = {"sys_id": "a" * 32, "name": "ist-db-01", "sys_class_name": "cmdb_ci_linux_server", "ip_address": "10.0.0.1",
       "fqdn": "", "serial_number": None, "os": "RHEL 9", "sources": ["vCenter", "BMC"],
       "updated_at": datetime(2026, 11, 25, tzinfo=UTC)}


def test_document_skips_empty_and_hidden_fields():
    doc = ci_document(ROW, class_label="Linux sunucu", hidden={"os"}, indexed_at=datetime(2026, 11, 25, 1, tzinfo=UTC))
    assert doc["name"] == "ist-db-01" and doc["sources"] == ["BMC", "vCenter"] and doc["class_label"] == "Linux sunucu"
    assert "os" not in doc and "fqdn" not in doc and "serial_number" not in doc
    assert doc["updated_at"].startswith("2026-11-25") and doc["indexed_at"].startswith("2026-11-25T01")
    assert set(doc) <= set(INDEX_BODY["mappings"]["properties"])  # katı eşleme: bilinmeyen alan yok


def test_query_filters_by_readable_classes_and_visible_fields():
    q = build_query("ist-db", classes=["cmdb_ci_linux_server", "cmdb_ci_linux_server"], fields=["name"], limit=500)
    assert q["size"] == 50
    assert q["query"]["bool"]["filter"] == [{"terms": {"sys_class_name": ["cmdb_ci_linux_server"]}}]
    text = json.dumps(q["query"])
    assert "ip_address" not in text and "fqdn" not in text and "serial_number" not in text  # görünmeyen alan sorguda yok
    assert "name.prefix^4" in text


def test_ip_like_queries_use_ip_prefix_not_fuzzy():
    q = json.dumps(build_query("10.20.3", classes=["cmdb_ci"]))
    assert '"prefix": {"ip_address.raw"' in q and "fuzziness" not in q
    assert "fuzziness" in json.dumps(build_query("ist db", classes=["cmdb_ci"]))


def test_empty_class_list_matches_nothing():
    assert build_query("x", classes=[])["query"]["bool"]["filter"] == [{"terms": {"sys_class_name": []}}]


def test_index_versioning():
    name = index_name(datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC))
    assert name.startswith(f"{ALIAS}-v{MAPPING_VERSION}-20261125120000")
    assert current_version([name]) and not current_version([]) and not current_version([name, name + "x"])
    assert not current_version([f"{ALIAS}-v0-20200101000000"])


def test_hidden_fields_come_from_metadata_sensitivity():
    md = load_metadata(__import__("pathlib").Path(__file__).resolve().parents[3] / "metadata")
    assert hidden_fields(md) == frozenset()  # bugünkü sözlükte gizli alan yok
    cls = md.classes[0]
    cls.setdefault("fields", []).append({"name": "u_gizli", "type": "string", "sensitivity": "restricted"})
    try:
        assert "u_gizli" in hidden_fields(md)
    finally:
        cls["fields"].pop()


# ------------------------------------------------------------------ istemci
def test_settings_are_secure_by_default(tmp_path):
    with pytest.raises(OpenSearchConfigError, match="tanımlı değil"):
        OpenSearchSettings.from_env({})
    with pytest.raises(OpenSearchConfigError, match="ALLOW_HTTP"):
        OpenSearchSettings.from_env({"EVREN_OPENSEARCH_URL": "http://os:9200"})
    with pytest.raises(OpenSearchConfigError, match="adrese yazılmaz"):
        OpenSearchSettings.from_env({"EVREN_OPENSEARCH_URL": "https://u:p@os:9200"})
    with pytest.raises(OpenSearchConfigError, match="birlikte"):
        OpenSearchSettings.from_env({"EVREN_OPENSEARCH_URL": "https://os:9200", "EVREN_OPENSEARCH_USERNAME": "evren"})
    canary = "-".join(("os", "parola", "77"))
    (tmp_path / "pw").write_text(canary, encoding="utf-8")
    s = OpenSearchSettings.from_env({"EVREN_OPENSEARCH_URL": "https://os:9200/", "EVREN_OPENSEARCH_USERNAME": "evren",
                                     "EVREN_OPENSEARCH_PASSWORD_FILE": str(tmp_path / "pw")})
    assert s.url == "https://os:9200" and canary not in repr(s)


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def _reply(self, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        _Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body,
                              "ctype": self.headers.get("Content-Type")})
        if self.path.startswith("/_bulk"):
            self._reply(200, {"errors": "bozuk" in body, "items": [{"index": {"status": 400 if "bozuk" in body else 201}}]})
        else:
            self._reply(500, {"error": "iç ayrıntı SIZMAMALI"})

    def do_GET(self) -> None:
        self._reply(200, {"status": "yellow"})


@pytest.fixture
def fake_os(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / "pw").write_text("gizli-deger", encoding="utf-8")
    settings = OpenSearchSettings.from_env({"EVREN_OPENSEARCH_URL": f"http://127.0.0.1:{server.server_port}",
                                            "EVREN_OPENSEARCH_ALLOW_HTTP": "1", "EVREN_OPENSEARCH_USERNAME": "evren",
                                            "EVREN_OPENSEARCH_PASSWORD_FILE": str(tmp_path / "pw")})
    _Handler.seen.clear()
    yield OpenSearch(settings)
    server.shutdown()


def test_client_bulk_auth_and_errors_do_not_echo_server_body(fake_os):
    assert fake_os.ping()
    assert fake_os.bulk_index(ALIAS, [ci_document(ROW, class_label="L")]) == 1
    sent = _Handler.seen[-1]
    assert sent["auth"].startswith("Basic ") and sent["ctype"] == "application/x-ndjson"
    first, second = sent["body"].strip().split("\n")
    assert json.loads(first) == {"index": {"_index": ALIAS, "_id": "a" * 32}} and json.loads(second)["name"] == "ist-db-01"
    with pytest.raises(OpenSearchError, match="1 belge başarısız"):
        fake_os.bulk_index(ALIAS, [ci_document({**ROW, "name": "bozuk"}, class_label="L")])
    with pytest.raises(OpenSearchError) as exc:
        fake_os.search(ALIAS, {"query": {"match_all": {}}})
    assert exc.value.status == 500 and "SIZMAMALI" not in str(exc.value)
    with pytest.raises(ValueError, match="Geçersiz dizin adı"):
        fake_os.search("../_all", {})


def test_unreachable_server_is_a_clean_error():
    client = OpenSearch(OpenSearchSettings("http://127.0.0.1:9", timeout=1))
    assert client.ping() is False
    with pytest.raises(OpenSearchError, match="ulaşılamadı"):
        client.search(ALIAS, {})

"""Gerçek OpenSearch'e karşı arama alakası (U3, ADR-0018). ``EVREN_TEST_OPENSEARCH_URL`` yoksa atlanır.

CI'da "Yığın" işi bu testi compose yığınındaki OpenSearch'e karşı çalıştırır. Test kendi geçici dizinini açar ve
siler; üretim takma adına (``evren-cmdb-ci``) dokunmaz.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from evren_core.opensearch_client import OpenSearch, OpenSearchSettings
from evren_core.search import INDEX_BODY, build_query, ci_document

if not os.environ.get("EVREN_TEST_OPENSEARCH_URL"):
    pytest.skip("EVREN_TEST_OPENSEARCH_URL tanımlı değil", allow_module_level=True)

CLASSES = ["cmdb_ci_linux_server", "cmdb_ci_win_server", "cmdb_ci_ip_switch"]
ROWS = [
    {"sys_id": "a" * 32, "name": "ist-db-01", "sys_class_name": "cmdb_ci_linux_server", "ip_address": "10.20.30.41",
     "fqdn": "ist-db-01.kurum.local", "serial_number": "CZJ1234567", "os": "Red Hat Enterprise Linux 9.4"},
    {"sys_id": "b" * 32, "name": "ANK-WEB-02", "sys_class_name": "cmdb_ci_win_server", "ip_address": "10.20.31.5",
     "os": "Windows Server 2022"},
    {"sys_id": "c" * 32, "name": "şırnak-sw-çekirdek", "sys_class_name": "cmdb_ci_ip_switch", "ip_address": "10.9.0.1"},
    {"sys_id": "d" * 32, "name": "ist-db-02", "sys_class_name": "cmdb_ci_linux_server", "ip_address": "10.20.30.42"},
]


@pytest.fixture(scope="module")
def index():
    client = OpenSearch(OpenSearchSettings.from_env({"EVREN_OPENSEARCH_URL": os.environ["EVREN_TEST_OPENSEARCH_URL"],
                                                     "EVREN_OPENSEARCH_ALLOW_HTTP": "1"}))
    name = f"evren-test-{uuid.uuid4().hex[:8]}"
    client.create_index(name, INDEX_BODY)
    client.bulk_index(name, [ci_document(r, class_label=r["sys_class_name"], indexed_at=datetime.now(UTC)) for r in ROWS],
                      refresh=True)
    yield client, name
    client.delete_index(name)


def names(index, q, classes=CLASSES, fields=None):
    client, name = index
    res = client.search(name, build_query(q, classes=classes, fields=fields))
    return [h["_source"]["name"] for h in res["hits"]["hits"]]


@pytest.mark.parametrize(("q", "expected"), [
    ("İST-DB-01", ["ist-db-01"]),                  # Türkçe büyük harf + ayırıcı
    ("ISTDB", ["ist-db-01", "ist-db-02"]),         # ayırıcısız önek
    ("istdb01", ["ist-db-01"]),                    # ayırıcısız tam
    ("ist db", ["ist-db-01", "ist-db-02"]),        # parça önekleri, sırası önemsiz
    ("db 02", ["ist-db-02"]),
    ("10.20.30.41", ["ist-db-01"]),                # tam IP yalnızca kendisi
    ("10.20.30", ["ist-db-01", "ist-db-02"]),      # IP öneki
    ("czj12345", ["ist-db-01"]),                   # seri öneki
    ("redhat", ["ist-db-01"]),                     # işletim sistemi, ayırıcısız
    ("ŞIRNAK", ["şırnak-sw-çekirdek"]), ("sirnak", ["şırnak-sw-çekirdek"]), ("cekirdek", ["şırnak-sw-çekirdek"]),
    ("windws", ["ANK-WEB-02"]),                    # yazım hatası
    ("a" * 32, ["ist-db-01"]),                     # sys_id
])
def test_relevance(index, q, expected):
    assert names(index, q) == expected


def test_class_and_field_restrictions_are_enforced_by_the_engine(index):
    assert names(index, "ist", classes=["cmdb_ci_win_server"]) == []
    assert names(index, "10.20.30.41", fields=["name"]) == []   # IP'yi göremeyen IP ile bulamaz
    assert names(index, "ist", classes=[]) == []

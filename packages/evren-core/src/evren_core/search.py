"""CI arama dizini (U3, ADR-0018) — saf mantık: dizin eşlemesi, belge, sorgu. G/Ç yok (``opensearch_client``).

* Ad, FQDN, IP ve seri numarası parçalara ayrılır (``ist-db-01`` → ``ist``, ``db``, ``01``); Türkçe küçük harf ve ASCII
  katlama birlikte uygulanır: "İST-DB-01", "ist-db-01" ve "ıst db" aynı belgeyi bulur.
* Önekle arama (``ist-d`` → ``ist-db-01``) önek alt alanlarıyla, yazım hatası toleransı bulanık eşleşmeyle yapılır;
  tam kimlikler (sys_id, ad, IP, seri, MAC) en yüksek ağırlıkla eşleşir.
* Dizin kayıt sistemi değildir: belge PostgreSQL'den üretilir, gizli hassasiyetteki alanlar dizine hiç girmez.
  Sorguda kullanıcının okuyabildiği sınıflar süzgeç olarak, görebildiği alanlar arama alanı olarak verilir.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

__all__ = ["ALIAS", "DOC_FIELDS", "HIDDEN_SENSITIVITY", "INDEX_BODY", "MAPPING_VERSION", "SEARCH_FIELDS", "build_query",
           "ci_document", "current_version", "hidden_fields", "index_name"]

ALIAS = "evren-cmdb-ci"
#: Eşleme (INDEX_BODY) değişince artırılır: dizinleyici açılışta takma adın eski sürümü gösterdiğini görür ve
#: yeniden dizinler (kesintisiz: yeni dizin dolunca takma ad atomik olarak taşınır).
MAPPING_VERSION = 1
HIDDEN_SENSITIVITY = frozenset({"confidential", "restricted"})
#: Dizine giren CI alanları (``cmdb_ci`` sütunları ve sınıf öznitelikleri). Hassasiyeti gizli olan dizine girmez.
DOC_FIELDS = ("name", "fqdn", "ip_address", "mac_address", "serial_number", "asset_tag", "os", "used_for",
              "install_status", "operational_status")
#: Aranabilir alanlar ve ağırlıkları (kullanıcının göremediği alan sorgudan çıkarılır).
SEARCH_FIELDS = {"name": 4, "fqdn": 3, "ip_address": 2, "serial_number": 2, "asset_tag": 2, "mac_address": 2, "os": 1}
_EXACT = ("name", "fqdn", "ip_address", "serial_number", "asset_tag", "mac_address")

_TEXT = {"type": "text", "analyzer": "evren_text",
         "fields": {"prefix": {"type": "text", "analyzer": "evren_prefix", "search_analyzer": "evren_text"},
                    "raw": {"type": "keyword", "normalizer": "evren_kw"},
                    "compact": {"type": "keyword", "normalizer": "evren_compact"}}}
_IP_LIKE = re.compile(r"^[0-9a-fA-F:.]{2,45}$")

INDEX_BODY: dict[str, Any] = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0, "max_ngram_diff": 19},
        "analysis": {
            "tokenizer": {"evren_parts": {"type": "pattern", "pattern": "[^\\p{L}\\p{N}]+"}},
            "char_filter": {"evren_strip": {"type": "pattern_replace", "pattern": "[^\\p{L}\\p{N}]+", "replacement": ""}},
            "filter": {"evren_tr_lower": {"type": "lowercase", "language": "turkish"},
                       "evren_edge": {"type": "edge_ngram", "min_gram": 1, "max_gram": 20}},
            "analyzer": {
                "evren_text": {"type": "custom", "tokenizer": "evren_parts", "filter": ["evren_tr_lower", "asciifolding"]},
                "evren_prefix": {"type": "custom", "tokenizer": "evren_parts",
                                 "filter": ["evren_tr_lower", "asciifolding", "evren_edge"]},
            },
            "normalizer": {
                "evren_kw": {"type": "custom", "filter": ["evren_tr_lower", "asciifolding"]},
                # "ist-db-01", "IST DB 01" ve "istdb01" aynı sıkışık değere iner
                "evren_compact": {"type": "custom", "char_filter": ["evren_strip"],
                                  "filter": ["evren_tr_lower", "asciifolding"]},
            },
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "sys_id": {"type": "keyword"},
            "sys_class_name": {"type": "keyword"},
            "class_label": {"type": "text", "analyzer": "evren_text"},
            **{f: _TEXT for f in ("name", "fqdn", "ip_address", "serial_number", "asset_tag", "mac_address", "os")},
            "used_for": {"type": "keyword"},
            "install_status": {"type": "keyword"},
            "operational_status": {"type": "keyword"},
            "sources": {"type": "keyword"},
            "updated_at": {"type": "date"},
            "indexed_at": {"type": "date"},
        },
    },
}


def index_name(stamp: datetime, *, alias: str = ALIAS) -> str:
    """Sürümlü dizin adı; takma ad yeniden dizinlemeden sonra atomik olarak yenisine taşınır."""
    return f"{alias}-v{MAPPING_VERSION}-{stamp:%Y%m%d%H%M%S%f}"


def current_version(targets: Iterable[str], *, alias: str = ALIAS) -> bool:
    """Takma ad tam olarak bir dizini ve o dizin güncel eşleme sürümünü mü gösteriyor?"""
    targets = list(targets)
    return len(targets) == 1 and targets[0].startswith(f"{alias}-v{MAPPING_VERSION}-")


def hidden_fields(md: Any) -> frozenset[str]:
    """Herhangi bir sınıfta gizli hassasiyetteki (``confidential``/``restricted``) alan adları. Güvenli taraf: ad
    her sınıfta gizlenir. Dizin belgesine ve olay akışına girmez."""
    return frozenset(name for cls in md.hierarchy.parents for name, spec in md.fields_of(cls).items()
                     if spec.get("sensitivity", "internal") in HIDDEN_SENSITIVITY)


def ci_document(row: Mapping[str, Any], *, class_label: str, hidden: Iterable[str] = (),
                indexed_at: datetime | None = None) -> dict[str, Any]:
    """PostgreSQL satırından dizin belgesi. ``hidden``: gizli hassasiyetteki alan adları (dizine girmez)."""
    hidden = set(hidden)
    doc: dict[str, Any] = {"sys_id": row["sys_id"], "sys_class_name": row["sys_class_name"], "class_label": class_label,
                           "sources": sorted(row.get("sources") or [])}
    for f in DOC_FIELDS:
        value = row.get(f)
        if f not in hidden and value not in (None, ""):
            doc[f] = str(value)
    if row.get("updated_at"):
        doc["updated_at"] = row["updated_at"].isoformat() if isinstance(row["updated_at"], datetime) else row["updated_at"]
    if indexed_at:
        doc["indexed_at"] = indexed_at.isoformat()
    return doc


def build_query(q: str, *, classes: Iterable[str], fields: Iterable[str] | None = None, limit: int = 20) -> dict[str, Any]:
    """Arama sorgusu. ``classes``: kullanıcının okuyabildiği sınıflar (boşsa hiçbir şey dönmez);
    ``fields``: kullanıcının görebildiği aranabilir alanlar (varsayılan: tümü)."""
    q = q.strip()[:200]
    allowed = sorted(set(classes))
    usable = [f for f in (fields if fields is not None else SEARCH_FIELDS) if f in SEARCH_FIELDS]
    should: list[dict[str, Any]] = [{"term": {"sys_id": {"value": q.lower(), "boost": 20}}}]
    # Tam kimlik (normalleştirici sorguya da uygulanır), ayırıcısız yazım ve ayırıcısız önek
    should += [{"term": {f"{f}.raw": {"value": q, "boost": 10}}} for f in _EXACT if f in usable]
    should += [{"term": {f"{f}.compact": {"value": q, "boost": 6}}} for f in _EXACT if f in usable]
    should += [{"prefix": {f"{f}.compact": {"value": q, "boost": 1 + SEARCH_FIELDS[f] / 2}}}
               for f in usable if f != "ip_address"]
    if "ip_address" in usable and _IP_LIKE.fullmatch(q):
        should.append({"prefix": {"ip_address.raw": {"value": q, "boost": 8}}})
    elif usable:
        should.append({"multi_match": {"query": q, "type": "best_fields", "operator": "and",
                                       "fields": [f"{f}.prefix^{SEARCH_FIELDS[f]}" for f in usable]}})
        should.append({"multi_match": {"query": q, "type": "best_fields", "operator": "and", "fuzziness": "AUTO",
                                       "prefix_length": 1, "max_expansions": 20,
                                       "fields": [f"{f}^{SEARCH_FIELDS[f]}" for f in usable]}})
    return {
        "size": max(1, min(limit, 50)),
        "track_total_hits": False,
        "_source": ["sys_id", "name", "sys_class_name", "class_label", "ip_address", "fqdn", "os", "sources"],
        "query": {"bool": {"filter": [{"terms": {"sys_class_name": allowed}}], "should": should,
                           "minimum_should_match": 1}},
    }

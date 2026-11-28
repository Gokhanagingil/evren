"""ServiceNow replikası (U9.1, F2-01, ADR-0042): kapsam, IRE yükü, yanıt yorumu, istemci ve simülatör.

Evren ana kaynaktır; ServiceNow CMDB replikadır. Kapsamdaki CI'lar ServiceNow'a **Identification and
Reconciliation API** ile yazılır (``POST /api/now/identifyreconcile``); ServiceNow'da CI tablosuna doğrudan yazılmaz,
iş kuralı, ACL veya görev alanı değişikliği gerekmez (CLAUDE.md kural 11).

Sözleşme ServiceNow'un herkese açık API başvurusundan alınmıştır (``c_IdentifyReconcileAPI``; ServiceNow/ServiceNowDocs
deposu, australia sürümü): istek ``items[]`` (``className``, ``internal_id``, ``values``, ``sys_object_source_info``)
ve ``relations[]`` (``parent``/``child`` öğe sırası, ``type`` = ``cmdb_rel_type.name``); yanıt ``result.items[]``
(``className``, ``operation``, ``sysId``, ``inputIndices``, ``errors``) ve ``result.relations[]``. İşlemeden deneme
uç noktası ``POST /api/now/identifyreconcile/query`` aynı yükü alır, veritabanına yazmaz.

Güvenlik kararları:

* **Önce doğrula, sonra yaz.** Bir CI'ın ilk itmesi her zaman önce ``/query`` ile denenir. ServiceNow kaydı beklenen
  kayıtla (aynı sys_id; Evren'de doğan CI için yeni kayıt) eşleştirmezse yazılmaz, **çelişki** olarak insan kararına
  düşer. Böylece Evren'deki bir CI, ServiceNow'daki başka bir CI'ın üzerine yazılmaz (kural 7'nin replika karşılığı).
* İlk başarılı itmeden sonra ServiceNow, CI'ı ``sys_object_source`` (kaynak adı + Evren sys_id) ile tanır; sonraki
  itmeler doğrudan yazılır, dönen sys_id yine eşlemeyle karşılaştırılır.
* Evren'de boş olan alan ServiceNow'da silinmez (yükte yer almaz).
* Kişi ve grup referansı yalnızca kayıt ServiceNow kaynaklıysa gönderilir; Evren'de veya Entra'da doğan kişinin
  ServiceNow'da karşılığı yoktur (``skipped``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.parse
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from .connectors.http import HttpClient, HttpResponse

__all__ = [
    "ALWAYS_EXCLUDED", "MODES", "STATES", "BuiltItem", "CiSnapshot", "IreClient", "IreSimulator", "ItemResult",
    "RelationResult", "ReplicaField", "Verdict", "build_item", "judge", "parse_response", "payload_hash", "sn_value",
    "validate_sn_sync",
]

#: Replika modu: kapalı → gölge (yalnızca deneme, ServiceNow'a yazılmaz) → etkin (yazılır)
MODES = {"off": "Kapalı", "shadow": "Gölge", "active": "Etkin"}
#: ``sync.sn_ci_map.state`` → Türkçe
STATES = {"in_sync": "Eşit", "pending_push": "Bekliyor", "pending_pull": "Çekme bekliyor", "conflict": "Çelişki",
          "error": "Hata", "excluded": "Dışarıda"}
#: Hiçbir sınıfta gönderilmeyen alanlar: IRE kaynak alanlarını kendisi tutar; ``duplicate_of`` Evren'in iç izidir
ALWAYS_EXCLUDED = frozenset({"discovery_source", "first_discovered", "last_discovered", "duplicate_of"})
#: ServiceNow'un kimlik çözümlemesinde "bulunamadı" anlamındaki sys_id değeri
UNKNOWN = "Unknown"

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_SYSID = re.compile(r"^[0-9a-f]{32}$")
_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9 _.-]{1,39}$")


def validate_sn_sync(doc: Mapping[str, Any] | None, *, classes: set[str], fields: set[str]) -> list[str]:
    """``metadata/sn_sync.yaml`` doğrulaması: sınıflar sözlükte, dışlanan alanlar bilinen alanlar, kaynak adı geçerli."""
    if not doc:
        return []
    errors: list[str] = []
    name = doc.get("source_name", "Evren")
    if not isinstance(name, str) or not _SOURCE.match(name):
        errors.append("sn_sync.source_name: 2–40 karakter, harfle başlayan ad olmalı")
    seen: set[str] = set()
    for i, c in enumerate(doc.get("classes") or []):
        cname = (c or {}).get("name")
        if cname not in classes:
            errors.append(f"sn_sync.classes[{i}]: sözlükte olmayan sınıf {cname!r}")
        elif cname in seen:
            errors.append(f"sn_sync.classes[{i}]: {cname} iki kez")
        seen.add(str(cname))
        if not isinstance((c or {}).get("enabled", True), bool):
            errors.append(f"sn_sync.classes[{i}].enabled: true/false olmalı")
    for f in doc.get("exclude_fields") or []:
        if f not in fields:
            errors.append(f"sn_sync.exclude_fields: sözlükte olmayan alan {f!r}")
    return errors


# ----------------------------------------------------------------------------------------------------- yük
@dataclass(frozen=True)
class ReplicaField:
    name: str
    type: str
    reference: str | None = None


@dataclass(frozen=True)
class CiSnapshot:
    """Golden record'un replikaya giden hâli: sınıf, alan değerleri (ham), son güncelleme."""
    sys_id: str
    sys_class_name: str
    values: Mapping[str, Any]
    updated_on: datetime


@dataclass(frozen=True)
class BuiltItem:
    evren_sys_id: str
    class_name: str
    item: dict[str, Any]
    hash: str
    skipped: tuple[str, ...] = ()


def sn_value(value: Any, internal_type: str) -> str | None:
    """Evren değeri → ServiceNow'un beklediği metin. Zaman UTC ``YYYY-MM-DD hh:mm:ss``, tarih ``YYYY-MM-DD``,
    mantıksal ``true``/``false``. Boş değer ``None`` (yüke girmez)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        v = value if value.tzinfo else value.replace(tzinfo=UTC)
        return v.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if internal_type == "boolean" and isinstance(value, str):
        return "true" if value.lower() in ("true", "1", "yes") else "false"
    return str(value)


def payload_hash(class_name: str, values: Mapping[str, str]) -> str:
    """Yükün değişip değişmediği: sınıf + değerler (zaman damgası hariç). Aynı içerik tekrar gönderilmez."""
    canon = json.dumps({"c": class_name, "v": dict(sorted(values.items()))}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build_item(ci: CiSnapshot, fields: Sequence[ReplicaField], *, source_name: str,
               ref_value: Callable[[str, str], str | None] | None = None) -> BuiltItem:
    """IRE öğesi. ``internal_id`` ve ``source_native_key`` Evren sys_id'sidir; ilk itmeden sonra ServiceNow CI'ı bu
    anahtarla (``sys_object_source``) tanır.

    ``ref_value(tablo, sys_id)`` referansın ServiceNow'daki sys_id'sini döner (referans tablosunda aynı sys_id; CI
    referansında eşlemedeki ServiceNow sys_id'si); ``None`` dönerse ServiceNow'da karşılığı yoktur, gönderilmez
    (``skipped``)."""
    values: dict[str, str] = {"sys_class_name": ci.sys_class_name}
    skipped: list[str] = []
    for f in fields:
        if f.name in ALWAYS_EXCLUDED or f.name == "sys_class_name":
            continue
        raw = ci.values.get(f.name)
        if f.type == "reference" and raw not in (None, ""):
            target = str(raw) if _SYSID.match(str(raw)) else None
            if target and ref_value is not None and f.reference:
                target = ref_value(f.reference, target)
            if not target:
                skipped.append(f.name)
                continue
            raw = target
        text = sn_value(raw, f.type)
        if text is not None:
            values[f.name] = text
    item = {
        "className": ci.sys_class_name,
        "internal_id": ci.sys_id,
        "values": values,
        "sys_object_source_info": {
            "source_name": source_name,
            "source_native_key": ci.sys_id,
            "source_recency_timestamp": sn_value(ci.updated_on, "glide_date_time"),
        },
    }
    return BuiltItem(ci.sys_id, ci.sys_class_name, item, payload_hash(ci.sys_class_name, values), tuple(skipped))


# --------------------------------------------------------------------------------------------- yanıt
@dataclass(frozen=True)
class ItemResult:
    index: int
    class_name: str
    operation: str
    sys_id: str | None
    errors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RelationResult:
    index: int
    operation: str
    errors: tuple[tuple[str, str], ...] = ()


def _errors(raw: Any) -> tuple[tuple[str, str], ...]:
    out = []
    for e in raw or []:
        if isinstance(e, Mapping):
            out.append((str(e.get("error") or "ERROR")[:60], str(e.get("message") or "")[:300]))
    return tuple(out)


def _indices(raw: Any) -> list[int]:
    out = []
    for i in raw or []:
        if isinstance(i, int) and not isinstance(i, bool):
            out.append(i)
        elif isinstance(i, Mapping) and isinstance(i.get("mainIndex"), int) and i.get("subIndex") in (None, -1):
            out.append(int(i["mainIndex"]))
    return out


def parse_response(doc: Any) -> tuple[dict[int, ItemResult], dict[int, RelationResult]]:
    """``result.items`` / ``result.relations`` → istek sırasına göre sonuçlar (``inputIndices``). Birleştirilmiş
    öğede birden çok sıra aynı sonucu paylaşır."""
    result = (doc or {}).get("result") or {}
    items: dict[int, ItemResult] = {}
    for it in result.get("items") or []:
        if not isinstance(it, Mapping):
            continue
        sys_id = str(it.get("sysId") or "")
        found = sys_id if _SYSID.match(sys_id) else None
        for idx in _indices(it.get("inputIndices")):
            items[idx] = ItemResult(idx, str(it.get("className") or ""), str(it.get("operation") or ""), found,
                                    _errors(it.get("errors")))
    rels: dict[int, RelationResult] = {}
    for r in result.get("relations") or []:
        if not isinstance(r, Mapping):
            continue
        for idx in _indices(r.get("inputIndices")):
            rels[idx] = RelationResult(idx, str(r.get("operation") or ""), _errors(r.get("errors")))
    return items, rels


@dataclass(frozen=True)
class Verdict:
    state: str                 # ok | conflict | error
    operation: str
    sn_sys_id: str | None      # ServiceNow'un bulduğu/verdiği sys_id
    message: str = ""


_ERROR_TR = {
    "MISSING_MATCHING_ATTRIBUTES": "Kimlik kuralının istediği alanlar yükte yok",
    "MULTIPLE_DUPLICATE_RECORDS": "ServiceNow'da aynı kimlikte birden çok kayıt var",
    "MULTI_MATCH": "ServiceNow'da aynı kimlikte birden çok kayıt var",
    "INVALID_INPUT_DATA": "ServiceNow yükü geçersiz buldu",
    "TYPE_CONFLICT_IN_QUALIFICATION": "Sınıf ServiceNow'daki kayıtla çelişiyor",
}


def judge(result: ItemResult | None, expected: str | None, *, taken_by_other: bool = False,
          committed: bool = True) -> Verdict:
    """IRE sonucunu Evren'in beklentisiyle karşılaştırır.

    * ``expected``: eşlemedeki ServiceNow sys_id'si (ServiceNow kökenli CI'da Evren sys_id'siyle aynı). Yoksa CI
      Evren'de doğmuştur ve beklenen sonuç **yeni kayıt** (``INSERT``) olur.
    * ServiceNow başka bir kaydı bulursa, Evren'de doğan CI var olan bir kayıtla eşleşirse ya da bulunan kayıt başka bir
      Evren CI'ına eşliyse sonuç **çelişki**dir: yazılmaz (deneme) veya insan kararına düşer.
    * Denemede (``committed=False``) yeni kayıt henüz yoktur: sys_id'siz ``INSERT`` geçerli bir sonuçtur.
    """
    if result is None:
        return Verdict("error", "", None, "ServiceNow yanıtında bu öğe yok")
    if not committed and not result.errors and result.sys_id is None and result.operation == "INSERT":
        if expected:
            return Verdict("conflict", "INSERT", None, "ServiceNow'da beklenen kayıt bulunamadı; yeni kayıt açılırdı")
        return Verdict("ok", "INSERT", None)
    if result.errors or result.sys_id is None:
        code, msg = result.errors[0] if result.errors else ("UNKNOWN", "")
        if code in _ERROR_TR:  # bilinen kod: Türkçe açıklama yeter
            return Verdict("error", result.operation, None, f"{_ERROR_TR[code]} ({code})")
        return Verdict("error", result.operation, None,
                       f"ServiceNow kaydı tanımlayamadı ({code})" + (f": {msg[:160]}" if msg else ""))
    if taken_by_other:
        return Verdict("conflict", result.operation, result.sys_id,
                       "ServiceNow'un bulduğu kayıt başka bir Evren CI'ına eşli")
    if expected and result.sys_id != expected:
        return Verdict("conflict", result.operation, result.sys_id,
                       "ServiceNow bu CI'ı beklenenden başka bir kayıtla eşleştirdi")
    if not expected and result.operation != "INSERT":
        return Verdict("conflict", result.operation, result.sys_id,
                       "Evren'de doğan CI ServiceNow'da var olan bir kayıtla eşleşti; onay gerekir")
    return Verdict("ok", result.operation, result.sys_id)


# --------------------------------------------------------------------------------------------- istemci
class IreClient:
    """Identification and Reconciliation API istemcisi. Hata iletileri sır ve sunucu gövdesi içermez (HttpClient)."""

    COMMIT = "/api/now/identifyreconcile"
    QUERY = "/api/now/identifyreconcile/query"

    def __init__(self, http: HttpClient, source_name: str) -> None:
        if not _SOURCE.match(source_name):
            raise ValueError("Geçersiz veri kaynağı adı")
        self.http = http
        self.source_name = source_name

    def send(self, items: Sequence[Mapping[str, Any]], relations: Sequence[Mapping[str, Any]] = (), *,
             commit: bool) -> tuple[dict[int, ItemResult], dict[int, RelationResult]]:
        path = (self.COMMIT if commit else self.QUERY) + "?" + urllib.parse.urlencode(
            {"sysparm_data_source": self.source_name})
        body: dict[str, Any] = {"items": list(items)}
        if relations:
            body["relations"] = list(relations)
        return parse_response(self.http.post_json(path, body, ok=(200,)))


# --------------------------------------------------------------------------------------------- simülatör
@dataclass
class _SnCi:
    sys_id: str
    class_name: str
    values: dict[str, str]
    sources: set[tuple[str, str]] = field(default_factory=set)


class IreSimulator:
    """ServiceNow CMDB'nin IRE davranışının alt kümesi (``Transport``), geliştirme ve test için.

    Kimlik sırası: ``sys_object_source`` (kaynak adı + yerel anahtar) → seri numarası → aynı sınıfta ad (büyük/küçük
    harf duyarsız). Birden çok eşleşme ``MULTIPLE_DUPLICATE_RECORDS``; kimlik alanı yoksa ``MISSING_MATCHING_ATTRIBUTES``.
    ``/query`` yazmaz. ``seed`` ServiceNow'da önceden var olan kaydı ekler. ``fail_next`` sonraki istekleri 503 yapar.
    Basic veya Bearer kimlik doğrulaması zorunludur; ``sysparm_data_source`` ``allowed_sources``'ta olmalıdır."""

    def __init__(self, *, allowed_sources: Iterable[str] = ("Evren",), fail_next: int = 0) -> None:
        self.cis: dict[str, _SnCi] = {}
        self.relations: set[tuple[str, str, str]] = set()
        self.allowed_sources = set(allowed_sources)
        self.fail_next = fail_next
        self.calls: list[tuple[str, int]] = []     # (uç nokta, öğe sayısı)
        self.commits = 0

    def seed(self, sys_id: str, class_name: str, *, source: tuple[str, str] | None = None, **values: str) -> None:
        """ServiceNow'da önceden var olan kayıt; ``source`` (kaynak adı, yerel anahtar) önceki itmenin izidir."""
        self.cis[sys_id] = _SnCi(sys_id, class_name, {"sys_class_name": class_name, **values},
                                 {source} if source else set())

    @staticmethod
    def _json(status: int, payload: Any) -> HttpResponse:
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

    def _identify(self, item: Mapping[str, Any]) -> tuple[str | None, list[dict[str, Any]], tuple[str, str] | None]:
        values = item.get("values") or {}
        src = item.get("sys_object_source_info") or {}
        attempts: list[dict[str, Any]] = []
        key = (str(src.get("source_name") or ""), str(src.get("source_native_key") or ""))
        if all(key):
            hit = [c.sys_id for c in self.cis.values() if key in c.sources]
            attempts.append({"identifierName": "Source Rule", "attemptResult": "MATCHED" if hit else "NO_MATCH",
                             "attributes": ["source_name", "source_native_key"], "searchOnTable": "sys_object_source"})
            if hit:
                return hit[0], attempts, None
        serial = str(values.get("serial_number") or "").strip().lower()
        if serial:
            hit = [c.sys_id for c in self.cis.values() if c.values.get("serial_number", "").lower() == serial]
            attempts.append({"identifierName": "Hardware Rule", "attemptResult":
                             "MATCHED" if len(hit) == 1 else "MULTI_MATCH" if hit else "NO_MATCH",
                             "attributes": ["serial_number"], "searchOnTable": "cmdb_ci_hardware"})
            if len(hit) > 1:
                return None, attempts, ("MULTIPLE_DUPLICATE_RECORDS", "Duplicate records found")
            if hit:
                return hit[0], attempts, None
        name = str(values.get("name") or "").strip().lower()
        if not name and not serial and not all(key):
            return None, attempts, ("MISSING_MATCHING_ATTRIBUTES", "Identification rules require name or serial")
        if name:
            cls = str(item.get("className") or "")
            hit = [c.sys_id for c in self.cis.values() if c.class_name == cls and c.values.get("name", "").lower() == name]
            attempts.append({"identifierName": "Name Rule", "attemptResult":
                             "MATCHED" if len(hit) == 1 else "MULTI_MATCH" if hit else "NO_MATCH",
                             "attributes": ["name"], "searchOnTable": cls})
            if len(hit) > 1:
                return None, attempts, ("MULTIPLE_DUPLICATE_RECORDS", "Duplicate records found")
            if hit:
                return hit[0], attempts, None
        return None, attempts, None

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        if self.fail_next:
            self.fail_next -= 1
            return HttpResponse(503, {}, b"")
        auth = str(headers.get("Authorization", ""))
        if not ((auth.startswith("Basic ") and base64.b64decode(auth[6:])) or auth.startswith("Bearer ")):
            return self._json(401, {"error": {"message": "User Not Authenticated"}, "status": "failure"})
        if method != "POST" or parts.path not in (IreClient.COMMIT, IreClient.QUERY):
            return self._json(400, {"error": {"message": "Invalid request"}, "status": "failure"})
        q = dict(urllib.parse.parse_qsl(parts.query))
        if q.get("sysparm_data_source") not in self.allowed_sources:
            return self._json(400, {"error": {"message": "Invalid data source"}, "status": "failure"})
        commit = parts.path == IreClient.COMMIT
        data = json.loads(body or b"{}")
        items = data.get("items") or []
        self.calls.append(("commit" if commit else "query", len(items)))
        out_items: list[dict[str, Any]] = []
        resolved: list[str | None] = []
        for idx, item in enumerate(items):
            found, attempts, err = self._identify(item)
            values = {str(k): str(v) for k, v in (item.get("values") or {}).items()}
            src = item.get("sys_object_source_info") or {}
            key = (str(src.get("source_name") or ""), str(src.get("source_native_key") or ""))
            if err:
                out_items.append({"className": item.get("className"), "operation": "NO_CHANGE", "sysId": UNKNOWN,
                                  "identifierEntrySysId": UNKNOWN, "identificationAttempts": attempts,
                                  "inputIndices": [idx], "errorCount": 1,
                                  "errors": [{"error": err[0], "message": err[1]}]})
                resolved.append(None)
                continue
            if found:
                ci = self.cis[found]
                changed = any(ci.values.get(k) != v for k, v in values.items())
                op = "UPDATE" if changed else "NO_CHANGE"
                if commit:
                    ci.values.update(values)
                    if all(key):
                        ci.sources.add(key)
                sys_id = found
            else:
                op = "INSERT"
                sys_id = uuid.uuid4().hex
                if commit:
                    self.cis[sys_id] = _SnCi(sys_id, str(item.get("className")), values, {key} if all(key) else set())
            resolved.append(sys_id)
            out_items.append({"className": item.get("className"), "operation": op, "sysId": sys_id,
                              "identifierEntrySysId": "0" * 32, "identificationAttempts": attempts,
                              "inputIndices": [idx], "errorCount": 0, "markers": [], "mergedPayloadIds": []})
        out_rels: list[dict[str, Any]] = []
        for idx, rel in enumerate(data.get("relations") or []):
            p, c = rel.get("parent"), rel.get("child")
            ok = isinstance(p, int) and isinstance(c, int) and 0 <= p < len(resolved) and 0 <= c < len(resolved)
            if not ok or resolved[p] is None or resolved[c] is None:
                out_rels.append({"className": "cmdb_rel_ci", "operation": "NO_CHANGE", "inputIndices": [idx],
                                 "errorCount": 1, "errors": [{"error": "INVALID_INPUT_DATA",
                                                              "message": "Relation endpoint not identified"}]})
                continue
            triple = (str(resolved[p]), str(resolved[c]), str(rel.get("type")))
            op = "NO_CHANGE" if triple in self.relations else "INSERT"
            if commit:
                self.relations.add(triple)
            out_rels.append({"className": "cmdb_rel_ci", "operation": op, "inputIndices": [idx], "errorCount": 0})
        if commit:
            self.commits += 1
        return self._json(200, {"result": {"items": out_items, "additionalCommittedItems": [],
                                           "relations": out_rels, "additionalCommittedRelations": []}})

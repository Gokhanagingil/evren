"""ServiceNow CMDB istemcisi (F0-08, F1-12, U5.2): Table API ile salt okunur çekme.

* **Uç:** ``GET /api/now/table/{tablo}``; ``sysparm_exclude_reference_link=true`` ve ``sysparm_display_value=false``
  (referans alanları ``sys_id`` olarak gelir, görünen değer hesaplanmaz), ``sysparm_fields`` ile yalnızca eşlemenin
  kullandığı alanlar, ``sysparm_no_count=true`` (toplam sayım sorgusu yapılmaz).
* **Sayfalama:** offset yerine anahtar sırası (keyset): ``sys_id>SON^ORDERBYsys_id``. ServiceNow'un "Break up a large
  export" belgesindeki desen budur; offset her sayfada önceki satırları yeniden okur ve değişen tabloda kayıt
  atlatabilir. Erişim kuralları (ACL) bazı satırları gizlerse sayfa istenenden kısa gelebilir; bu yüzden istemci kısa
  sayfada durmaz, **boş sayfa** gelene kadar devam eder.
* **Delta:** ``sys_updated_on>=<since>`` (UTC). ServiceNow sorgudaki tarihi entegrasyon kullanıcısının saat dilimine
  göre yorumlar; entegrasyon kullanıcısının saat dilimi **UTC** olmalıdır (docs/04). Delta silinen kaydı göstermez;
  delta çalışması "görülmeyeni eksik say" işlemine girmez (ADR-0017).
* **Tablolar:** ``cmdb_ci_server`` (alt sınıflarıyla: Windows, Linux, ESX…) ve ``cmdb_ci_netgear`` (switch, router,
  güvenlik duvarı). Üst tablo sorgusu alt sınıf kayıtlarını da döndürür; sınıf ``sys_class_name``'den gelir. Ardından
  aynı çalışmada referans tabloları (``sys_user``, ``sys_user_group``, ``cmn_location``, ``core_company``,
  ``cmn_department``, ``cmn_cost_center``, ``cmdb_model``; ADR-0022): her tablodan yalnızca izinli sütunlar istenir
  (``evren_core.reference``); kişisel veri en azdır.
* **Değişiklik kayıtları** (``servicenow_change`` bağlayıcısı, ADR-0025): ``change_request`` ve ``task_ci``. Tam liste
  açık (``active=true``) değişiklikleri ve son ``lookback_days`` günde güncellenen veya bitişi bu tarihten sonra olanları
  çeker; ``task_ci``'dan yalnızca değişiklik kayıtlarına ait satırlar (``task.sys_class_name=change_request``): bağlı
  değişikliği açık olanlar ile kendisi veya bağlı değişikliği güncellenenler (``task.sys_updated_on``). Delta yalnızca
  güncellenenleri alır. Alanlar ``evren_core.reference.CHANGE_TABLES``'tan.
* **Hata:** ServiceNow hata gövdesi (``error.message``, ``error.detail``) kurum bilgisi içerebilir; iletiye yazılmaz.

``ServiceNowSimulator`` kullanılan alt kümeyi örnek veriyle yanıtlar (testler, duman testi, demo).
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from ...reference import CHANGE_TABLES, REFERENCE_TABLES, ROLE_TABLES
from ..http import HttpClient, HttpError, HttpResponse

__all__ = ["CHANGE_QUERIES", "CI_TABLES", "FIELDS", "TABLES", "ServiceNowClient", "ServiceNowSimulator", "fields_for"]

CI_TABLES = ("cmdb_ci_server", "cmdb_ci_netgear")
FIELDS = (
    "sys_id", "sys_class_name", "name", "fqdn", "ip_address", "serial_number", "mac_address", "os", "os_version",
    "support_group", "owned_by", "managed_by", "used_for", "location", "company", "cost_center", "model_id",
    "operational_status", "install_status", "short_description", "sys_updated_on",
)
# Referans tabloları aynı çalışmada CI tablolarından sonra çekilir (ADR-0022); alanlar tek listeden.
TABLES = (*CI_TABLES, *REFERENCE_TABLES)
# Değişiklik tabloları (ADR-0025): tablo başına (tam liste, delta) koşulu. {since}: delta başlangıcı, {cutoff}: geriye
# bakış sınırı. ``^OR`` önceki koşula bağlanır: ``A^B^ORC`` = A VE (B VEYA C).
CHANGE_QUERIES: dict[str, tuple[str, str]] = {
    "change_request": ("active=true^ORsys_updated_on>={cutoff}^ORend_date>={cutoff}", "sys_updated_on>={since}"),
    "task_ci": ("task.sys_class_name=change_request^task.active=true^ORsys_updated_on>={cutoff}"
                "^ORtask.sys_updated_on>={cutoff}",
                "task.sys_class_name=change_request^sys_updated_on>={since}^ORtask.sys_updated_on>={since}"),
}
assert set(CHANGE_QUERIES) == set(CHANGE_TABLES)


def fields_for(table: str) -> tuple[str, ...]:
    """Tablo için istenen alanlar: referans/değişiklik tablosunda yalnızca izinli sütunlar, CI tablosunda ``FIELDS``."""
    ref = ROLE_TABLES.get(table)
    return ref.fields if ref else FIELDS
_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_SYSID = re.compile(r"^[0-9a-f]{32}$")


def sn_datetime(value: datetime) -> str:
    """ServiceNow sorgu tarihi (UTC, ``YYYY-MM-DD HH:MM:SS``)."""
    if value.tzinfo is None:
        raise HttpError("Delta başlangıcı saat dilimi içermeli")
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


class ServiceNowClient:
    data_source = "ServiceNow"

    def __init__(self, http: HttpClient, *, tables: Sequence[str] = TABLES, page_size: int = 1000,
                 queries: Mapping[str, tuple[str, str]] | None = None, lookback_days: int = 30,
                 data_source: str | None = None, clock: Callable[[], datetime] | None = None) -> None:
        for t in tables:
            if not _TABLE.match(t):
                raise HttpError(f"Geçersiz tablo adı: {t[:40]!r}")
        if not 1 <= page_size <= 10000:
            raise HttpError("Sayfa boyutu 1–10000 olmalı")
        if not 1 <= lookback_days <= 3650:
            raise HttpError("Geriye bakış 1–3650 gün olmalı")
        self.http, self.tables, self.page_size = http, tuple(tables), page_size
        self.queries = dict(queries or {})
        self.lookback = timedelta(days=lookback_days)
        self.clock = clock or (lambda: datetime.now(UTC))
        if data_source:
            self.data_source = data_source

    def _page(self, table: str, query: str) -> list[dict[str, Any]]:
        params = {"sysparm_query": query, "sysparm_fields": ",".join(fields_for(table)), "sysparm_limit": self.page_size,
                  "sysparm_exclude_reference_link": "true", "sysparm_display_value": "false",
                  "sysparm_no_count": "true"}
        doc = self.http.get_json(f"/api/now/table/{table}", **params)
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise HttpError(f"ServiceNow yanıtı beklenen biçimde değil ({table})")
        return rows

    def _base(self, table: str, mode: str, since: datetime | None) -> list[str]:
        delta = mode == "delta" and since is not None
        if table not in self.queries:
            return [f"sys_updated_on>={sn_datetime(since)}"] if delta and since else []
        snapshot_q, delta_q = self.queries[table]
        if delta and since:
            return [delta_q.format(since=sn_datetime(since))]
        return [snapshot_q.format(cutoff=sn_datetime(self.clock() - self.lookback))]

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        for table in self.tables:
            base = self._base(table, mode, since)
            last = ""
            while True:
                rows = self._page(table, "^".join([*base, *([f"sys_id>{last}"] if last else []), "ORDERBYsys_id"]))
                if not rows:
                    break
                for row in rows:
                    sid = row.get("sys_id")
                    if not isinstance(sid, str) or not _SYSID.match(sid) or sid <= last:
                        raise HttpError(f"ServiceNow sıralaması veya sys_id beklenmedik ({table})")
                    last = sid
                    yield {**row, "_table": table}


# ---------------------------------------------------------------------------------------------------- simülatör
_COND = re.compile(r"^(?P<field>[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)(?P<op>>=|>|=)(?P<value>.*)$")


class ServiceNowSimulator:
    """Table API alt kümesi (``Transport``). Örnek veri: ``connectors/servicenow_cmdb/samples/tables.json``.

    Desteklenen sorgu: ``alan>=…``, ``alan>…``, ``alan=…``, ``^OR`` (önceki koşula bağlanır), bir düzey nokta yürüyüşü
    (``task.sys_updated_on``; başvurulan satır tüm tablolarda sys_id ile aranır, ``sys_class_name`` yoksa tablo adı)
    ve ``ORDERBYsys_id`` (``^`` ile). Basic kimlik doğrulama
    zorunludur (yoksa 401). ``hidden``: ACL'nin gizlediği ``sys_id``'ler (sayfa kısa gelir). ``fail_on_call``:
    sıradaki çağrıda 503. Tablo üst sınıf sorgusu gibi davranır: ``cmdb_ci_server`` alt sınıf kayıtlarını da döndürür.
    """

    def __init__(self, tables: Mapping[str, list[dict[str, Any]]], *, user: str | None = None,
                 password: str | None = None, hidden: set[str] | None = None,
                 fail_on_call: int | None = None) -> None:
        self.tables = {k: sorted(v, key=lambda r: r["sys_id"]) for k, v in tables.items()}
        self.user, self.password = user, password
        self.hidden = hidden or set()
        self.fail_on_call = fail_on_call
        self.calls: list[str] = []

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> ServiceNowSimulator:
        doc = json.loads((Path(directory) / "tables.json").read_text(encoding="utf-8"))
        return cls(doc["tables"], **kw)

    def _value(self, table: str, row: Mapping[str, Any], field: str) -> str:
        if "." not in field:
            return str(row.get(field, table if field == "sys_class_name" else "") or "")
        ref, sub = field.split(".", 1)
        target = str(row.get(ref) or "")
        for name, rows in self.tables.items():
            for r in rows:
                if r["sys_id"] == target:
                    return self._value(name, r, sub)
        return ""

    def _test(self, table: str, row: Mapping[str, Any], field: str, op: str, value: str) -> bool:
        actual = self._value(table, row, field)
        if op == "=":
            return actual == value
        if not actual:
            return False  # boş tarih karşılaştırmada eşleşmez (ServiceNow davranışı)
        return actual >= value if op == ">=" else actual > value

    @staticmethod
    def _json(status: int, payload: Any) -> HttpResponse:
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        auth = str(headers.get("Authorization", ""))
        if not auth.startswith("Basic "):
            return False
        if self.user is None:
            return True
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
        except ValueError:
            return False
        return user == self.user and (self.password is None or pw == self.password)

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        self.calls.append(parts.path + "?" + parts.query)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            return HttpResponse(503, {}, b"")
        if not self._authorized(headers):
            return self._json(401, {"error": {"message": "User Not Authenticated",
                                              "detail": "Required to provide Auth information"}, "status": "failure"})
        m = re.fullmatch(r"/api/now/table/([a-z0-9_]+)", parts.path)
        if method != "GET" or not m:
            return self._json(400, {"error": {"message": "Invalid request"}, "status": "failure"})
        table = m.group(1)
        if table not in self.tables:
            return self._json(400, {"error": {"message": "Invalid table", "detail": table}, "status": "failure"})
        q = dict(urllib.parse.parse_qsl(parts.query))
        groups: list[list[tuple[str, str, str]]] = []
        for c in (c for c in q.get("sysparm_query", "").split("^") if c and c != "ORDERBYsys_id"):
            alternative = c.startswith("OR") and bool(groups)
            cm = _COND.match(c[2:] if alternative else c)
            if not cm:
                return self._json(400, {"error": {"message": "Invalid query"}, "status": "failure"})
            cond = (cm["field"], cm["op"], cm["value"])
            if alternative:
                groups[-1].append(cond)
            else:
                groups.append([cond])
        matched = [r for r in self.tables[table] if all(any(self._test(table, r, *c) for c in g) for g in groups)]
        page = matched[:int(q.get("sysparm_limit", "10000"))]
        fields = [f for f in q.get("sysparm_fields", "").split(",") if f]
        # ACL sınır uygulandıktan sonra süzer: gizli satır sayfayı kısaltır (ServiceNow davranışı)
        out = [{f: r.get(f, "") for f in fields} if fields else dict(r) for r in page if r["sys_id"] not in self.hidden]
        return self._json(200, {"result": out})

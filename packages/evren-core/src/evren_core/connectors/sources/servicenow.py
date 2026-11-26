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
* **Hata:** ServiceNow hata gövdesi (``error.message``, ``error.detail``) kurum bilgisi içerebilir; iletiye yazılmaz.

``ServiceNowSimulator`` kullanılan alt kümeyi örnek veriyle yanıtlar (testler, duman testi, demo).
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ...reference import REFERENCE_TABLES
from ..http import HttpClient, HttpError, HttpResponse

__all__ = ["CI_TABLES", "FIELDS", "TABLES", "ServiceNowClient", "ServiceNowSimulator", "fields_for"]

CI_TABLES = ("cmdb_ci_server", "cmdb_ci_netgear")
FIELDS = (
    "sys_id", "sys_class_name", "name", "fqdn", "ip_address", "serial_number", "mac_address", "os", "os_version",
    "support_group", "owned_by", "managed_by", "used_for", "location", "company", "cost_center", "model_id",
    "operational_status", "install_status", "short_description", "sys_updated_on",
)
# Referans tabloları aynı çalışmada CI tablolarından sonra çekilir (ADR-0022); alanlar tek listeden.
TABLES = (*CI_TABLES, *REFERENCE_TABLES)


def fields_for(table: str) -> tuple[str, ...]:
    """Tablo için istenen alanlar: referans tablosunda yalnızca izinli sütunlar, CI tablosunda ``FIELDS``."""
    ref = REFERENCE_TABLES.get(table)
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

    def __init__(self, http: HttpClient, *, tables: Sequence[str] = TABLES, page_size: int = 1000) -> None:
        for t in tables:
            if not _TABLE.match(t):
                raise HttpError(f"Geçersiz tablo adı: {t[:40]!r}")
        if not 1 <= page_size <= 10000:
            raise HttpError("Sayfa boyutu 1–10000 olmalı")
        self.http, self.tables, self.page_size = http, tuple(tables), page_size

    def _page(self, table: str, query: str) -> list[dict[str, Any]]:
        params = {"sysparm_query": query, "sysparm_fields": ",".join(fields_for(table)), "sysparm_limit": self.page_size,
                  "sysparm_exclude_reference_link": "true", "sysparm_display_value": "false",
                  "sysparm_no_count": "true"}
        doc = self.http.get_json(f"/api/now/table/{table}", **params)
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise HttpError(f"ServiceNow yanıtı beklenen biçimde değil ({table})")
        return rows

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        base = [f"sys_updated_on>={sn_datetime(since)}"] if mode == "delta" and since else []
        for table in self.tables:
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
_COND = re.compile(r"^(sys_id|sys_updated_on)(>=|>)(.+)$")


class ServiceNowSimulator:
    """Table API alt kümesi (``Transport``). Örnek veri: ``connectors/servicenow_cmdb/samples/tables.json``.

    Desteklenen sorgu: ``sys_updated_on>=…``, ``sys_id>…`` ve ``ORDERBYsys_id`` (``^`` ile). Basic kimlik doğrulama
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
        matched = list(self.tables[table])
        for c in (c for c in q.get("sysparm_query", "").split("^") if c and c != "ORDERBYsys_id"):
            cm = _COND.match(c)
            if not cm:
                return self._json(400, {"error": {"message": "Invalid query"}, "status": "failure"})
            field, op, value = cm.groups()
            matched = [r for r in matched if (r[field] >= value if op == ">=" else r[field] > value)]
        page = matched[:int(q.get("sysparm_limit", "10000"))]
        fields = [f for f in q.get("sysparm_fields", "").split(",") if f]
        # ACL sınır uygulandıktan sonra süzer: gizli satır sayfayı kısaltır (ServiceNow davranışı)
        out = [{f: r.get(f, "") for f in fields} if fields else dict(r) for r in page if r["sys_id"] not in self.hidden]
        return self._json(200, {"result": out})

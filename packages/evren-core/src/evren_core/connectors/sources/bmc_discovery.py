"""BMC Helix Discovery istemcisi (F1-01, U5.2): REST ``data/search`` ile Host düğümleri (Keşif rolü).

On-prem appliance ve Helix SaaS aynı REST API'yi sunar; fark adres, ağ yolu (SaaS'ta outpost) ve belirteçtir.

* **Kimlik doğrulama:** ``Authorization: Bearer <belirteç>``; belirteç Discovery kullanıcısına aittir ve dosyadan
  okunur. Kullanıcıya yalnızca okuma (sorgu) yetkisi yeter.
* **Sorgu:** ``GET /api/v{sürüm}/data/search?query=…&format=object&limit=…`` (``HOST_QUERY``). Sürüm kuruluma göre
  değişir (on-prem 25.2 örneklerinde 1.12, SaaS'ta 1.13); ``--api-release`` ile verilir.
* **Sayfalama:** ilk yanıt ``count``, ``offset``, ``next_offset`` ve ``results_id`` döndürür. Sonraki sayfalar
  ``results_id`` + ``offset`` ile istenir (``limit`` en fazla 10000). Sunucunun verdiği ``next`` adresi izlenmez:
  istemci adresi kendisi kurar, belirteç başka bir adrese gitmez. Sonuç kümesi ``results_id`` ile sabittir; toplanan
  sayı ``count``'a eşit değilse çalışma tamamlanmamış sayılır.
* **Hata:** sunucu iletisi yazılmaz; yalnızca yöntem, yol ve durum kodu.

Alan adları eşleme dosyasındaki ``verify: true`` ile işaretlidir; kurum örneğindeki çıktıyla doğrulanınca kalkar (F1-01).
``BMCDiscoverySimulator`` kullanılan alt kümeyi örnek veriyle yanıtlar.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..http import HttpClient, HttpError, HttpResponse

__all__ = ["HOST_QUERY", "BMCDiscoveryClient", "BMCDiscoverySimulator"]

HOST_QUERY = (
    "search Host show key, name, hostname, local_fqdn, dns_domain, os, os_type, os_class, os_version, os_edition, "
    "serial, uuid, vendor, model, num_processors, cores_per_processor, num_logical_processors, processor_type, "
    "processor_speed, ram, virtual, host_type, last_update_success, __all_ip_addrs as 'ip_addrs', "
    "#DeviceWithInterface:DeviceInterface:InterfaceOfDevice:NetworkInterface.mac_addr as 'mac_addrs'"
)
_RELEASE = re.compile(r"^\d{1,2}\.\d{1,3}$")


class BMCDiscoveryClient:
    data_source = "BMC"

    def __init__(self, http: HttpClient, *, release: str = "1.12", page_size: int = 500,
                 query: str = HOST_QUERY) -> None:
        if not _RELEASE.match(release):
            raise HttpError("BMC Discovery API sürümü geçersiz (ör. 1.12)")
        if not 1 <= page_size <= 10000:
            raise HttpError("Sayfa boyutu 1–10000 olmalı")
        self.http, self.path, self.page_size, self.query = http, f"/api/v{release}/data/search", page_size, query

    def _page(self, **params: Any) -> dict[str, Any]:
        doc = self.http.get_json(self.path, query=self.query, format="object", limit=self.page_size, **params)
        if not isinstance(doc, list) or len(doc) != 1 or not isinstance(doc[0], dict):
            raise HttpError("BMC Discovery yanıtı beklenen biçimde değil")
        page = doc[0]
        if not isinstance(page.get("results"), list):
            raise HttpError("BMC Discovery yanıtında sonuç listesi yok")
        return page

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """Her çalışma tam listedir (Discovery sorgusunda güvenilir "değişti" süzgeci kullanılmaz)."""
        page = self._page()
        total = int(page.get("count", 0))
        results_id = page.get("results_id")
        seen = 0
        while True:
            for row in page["results"]:
                if not isinstance(row, dict) or not row.get("key"):
                    raise HttpError("BMC Discovery sonucunda düğüm anahtarı yok")
                seen += 1
                yield row
            nxt = page.get("next_offset")
            if nxt is None or not page["results"]:
                break
            if not results_id or int(nxt) <= int(page.get("offset", 0)):
                raise HttpError("BMC Discovery sayfalama bilgisi tutarsız")
            page = self._page(results_id=results_id, offset=int(nxt))
        if seen != total:
            raise HttpError(f"BMC Discovery sonuç sayısı uyuşmuyor ({seen}/{total})")


class BMCDiscoverySimulator:
    """``data/search`` alt kümesi (``Transport``). Örnek veri: ``connectors/bmc_discovery/samples/hosts.json``.

    İlk istek yeni bir sonuç kümesi açar; sonraki sayfalar ``results_id`` + ``offset`` ile. Bearer belirteci yoksa 401.
    ``fail_on_call``: sıradaki çağrıda 503; ``lose_rows``: son sayfadan satır düşürür (sayı uyuşmazlığı sınaması).
    """

    def __init__(self, hosts: list[dict[str, Any]], *, fail_on_call: int | None = None, lose_rows: int = 0) -> None:
        self.hosts = hosts
        self.fail_on_call, self.lose_rows = fail_on_call, lose_rows
        self.calls: list[dict[str, str]] = []
        self.sets: dict[str, int] = {}

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> BMCDiscoverySimulator:
        return cls(json.loads((Path(directory) / "hosts.json").read_text(encoding="utf-8"))["hosts"], **kw)

    @staticmethod
    def _json(status: int, payload: Any) -> HttpResponse:
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append(q)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            return HttpResponse(503, {}, b"")
        if not str(headers.get("Authorization", "")).startswith("Bearer "):
            return self._json(401, {"message": "Authentication required"})
        if method != "GET" or not re.fullmatch(r"/api/v\d+\.\d+/data/search", parts.path):
            return self._json(404, {"message": "Not found"})
        if not q.get("query", "").startswith("search Host"):
            return self._json(400, {"message": "Unsupported query"})
        limit = int(q.get("limit", "100"))
        if "offset" in q and q.get("results_id") not in self.sets:
            return self._json(400, {"message": "offset requires results_id"})
        rid = q.get("results_id") or f"rs-{len(self.sets) + 1}"
        self.sets.setdefault(rid, len(self.hosts))
        offset = int(q.get("offset", "0"))
        rows = self.hosts[offset:offset + limit]
        more = offset + limit < len(self.hosts)
        if not more and self.lose_rows:
            rows = rows[:-self.lose_rows]
        page: dict[str, Any] = {"count": len(self.hosts), "kind": "Host", "offset": offset, "results_id": rid,
                                "results": rows}
        if more:
            page["next_offset"] = offset + limit
        return self._json(200, [page])

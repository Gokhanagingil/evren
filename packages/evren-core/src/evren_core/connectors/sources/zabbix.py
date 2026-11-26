"""Zabbix istemcisi (F0-11, U5): JSON-RPC ``host.get`` ile izlenen host'lar.

* Kimlik doğrulama: Zabbix 6.4+ API belirteci, ``Authorization: Bearer`` başlığıyla (belirteç dosyadan okunur).
* Sayfalama: Zabbix API'de ofset yoktur. Önce yalnızca ``hostid`` listesi çekilir, sonra ayrıntı ``page_size``'lık
  kimlik parçalarıyla istenir; bellek ve yanıt boyutu sınırlı kalır.
* Delta: Zabbix host'larında güvenilir "değiştiği zaman" alanı yoktur; her çalışma tam listedir (snapshot). Delta
  istenirse de tam liste çekilir ve çalışma snapshot gibi değerlendirilir.
* Hata: API ``error`` döndürürse yalnızca hata kodu iletilir; sunucu iletisi (kurum bilgisi içerebilir) günlüğe yazılmaz.

``ZabbixSimulator`` aynı API'nin ``host.get`` alt kümesini örnek veriyle yanıtlayan bir taşımadır (testler, geliştirme
yığınının duman testi, kurum dışı demo). Gerçek Zabbix kabulü yerine geçmez.
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..http import HttpClient, HttpError, HttpResponse

__all__ = ["ZabbixClient", "ZabbixSimulator"]

HOST_OUTPUT = ["hostid", "host", "name", "status"]
INTERFACE_OUTPUT = ["interfaceid", "type", "ip", "dns", "main"]
INVENTORY_OUTPUT = ["os", "os_full", "serialno_a", "macaddress_a", "asset_tag"]


class ZabbixClient:
    data_source = "Zabbix"
    path = "/api_jsonrpc.php"

    def __init__(self, http: HttpClient, *, page_size: int = 500) -> None:
        self.http = http
        self.page_size = page_size
        self._id = 0

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        self._id += 1
        resp = self.http.post_json(self.path, {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id})
        if not isinstance(resp, dict) or "result" not in resp:
            code = (resp or {}).get("error", {}).get("code") if isinstance(resp, dict) else None
            raise HttpError(f"Zabbix API hatası ({method}, kod {code})")
        return resp["result"]

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        ids = [h["hostid"] for h in self._call("host.get", {"output": ["hostid"], "sortfield": "hostid"})]
        for i in range(0, len(ids), self.page_size):
            chunk = ids[i:i + self.page_size]
            hosts = self._call("host.get", {
                "hostids": chunk, "output": HOST_OUTPUT, "selectInterfaces": INTERFACE_OUTPUT,
                "selectInventory": INVENTORY_OUTPUT, "selectHostGroups": ["groupid", "name"], "sortfield": "hostid"})
            if len(hosts) > len(chunk):  # arada silinen host olabilir (eksik sorun değil); fazlası beklenmez
                raise HttpError("Zabbix beklenenden fazla host döndürdü")
            yield from hosts


class ZabbixSimulator:
    """Zabbix JSON-RPC ``host.get`` simülatörü (``Transport``). Örnek veri: ``connectors/zabbix/samples/hosts.json``.

    Desteklenen: ``hostids`` süzgeci, ``output`` alan listesi, ``selectInterfaces``/``selectInventory``/
    ``selectHostGroups`` alan listeleri, ``sortfield: hostid``. ``Authorization: Bearer`` başlığı yoksa Zabbix gibi
    ``-32602`` hatası döner. ``fail_on_call``: verilen sıradaki çağrıda 503 döner (yarıda kalan çekme sınaması).
    """

    def __init__(self, hosts: list[dict[str, Any]], *, fail_on_call: int | None = None) -> None:
        self.hosts = sorted(hosts, key=lambda h: int(h["hostid"]))
        self.fail_on_call = fail_on_call
        self.calls = 0

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> ZabbixSimulator:
        doc = json.loads((Path(directory) / "hosts.json").read_text(encoding="utf-8"))
        return cls(doc["hosts"], **kw)

    @staticmethod
    def _pick(obj: Any, fields: Any) -> Any:
        if not isinstance(obj, Mapping) or fields in (None, "extend"):
            return obj
        return {k: obj[k] for k in fields if k in obj}

    def _host_get(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        wanted = params.get("hostids")
        rows = [h for h in self.hosts if wanted is None or h["hostid"] in set(map(str, wanted))]
        out = []
        for h in rows:
            row = self._pick(h, params.get("output", "extend"))
            for param, key in (("selectInterfaces", "interfaces"), ("selectInventory", "inventory"),
                               ("selectHostGroups", "hostgroups")):
                if param in params:
                    value = h.get(key, [])
                    row[key] = ([self._pick(v, params[param]) for v in value] if isinstance(value, list) and key != "inventory"
                                else self._pick(value, params[param]))
            out.append(row)
        return out

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            return HttpResponse(503, {}, b"")
        req = json.loads(body or b"{}")
        if not str(headers.get("Authorization", "")).startswith("Bearer "):
            result: dict[str, Any] = {"error": {"code": -32602, "message": "Not authorised.", "data": ""}}
        elif req.get("method") == "host.get":
            result = {"result": self._host_get(req.get("params") or {})}
        else:
            result = {"error": {"code": -32601, "message": "Method not found.", "data": ""}}
        payload = {"jsonrpc": "2.0", **result, "id": req.get("id")}
        return HttpResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

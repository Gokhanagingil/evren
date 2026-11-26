"""OpenText (Micro Focus) NNMi istemcisi (F1-03, U5.2): WS-I SOAP ``NodeBeanService`` ile ağ düğümleri (Ağ rolü).

* **Uç:** ``POST /NodeBeanService/NodeBean`` (SOAP 1.1, document/literal), işlem ``getNodes``. Kimlik doğrulama Basic;
  NNMi kullanıcısının "Web Services Client" rolü yeter (salt okunur). Parola dosyadan okunur.
* **Sayfalama:** süzgeç ``id > SON`` koşulu + ``maxObjects`` kısıtı (anahtar sırası). Gelen kimlikler artan sırada
  değilse çalışma hata verir: sıralama güvencesi yoksa kayıt atlanabilir, sessizce eksik veri üretilmez. Boş sayfada biter.
* **XML:** yanıt ``safe_xml`` ile ayrıştırılır (DTD, varlık ve dış başvuru reddedilir; ek bağımlılık yok).
* **Hata:** SOAP ``Fault`` metni kurum bilgisi içerebilir; iletiye yazılmaz.

* **Arayüzler ve L2 (F1-03):** ``POST /InterfaceBeanService/InterfaceBean`` işlem ``getInterfaces`` (aynı süzgeç ve
  sıralama güvencesi). Her arayüzün ``hostedOnId`` (düğüm), ``physicalAddress`` (MAC) ve ``connectionId`` (L2 bağlantısı)
  alanları okunur. Çalışma tam listedir: önce tüm düğümler ve arayüzler çekilir, sonra her düğüm şu ek alanlarla verilir:
  ``_mac_addresses`` (kimlik eşleşmesi için), ``_interface_count`` (MAC'i olan arayüz sayısı), ``_l2_down`` / ``_l2_up``
  (aynı L2 bağlantısını paylaşan komşu düğümlerin uuid'leri; kimliği küçük olan uç üsttür, böylece iki uçtan gelen kayıt
  aynı ilişkiyi üretir). Ağ adaptörü ayrı CI olarak alınmaz (bağımlı CI kimliği, U7).

Süzgeç biçimi ve alan adları NNMi WS-I belgesinden (Developer Toolkit, "WS-I Filter Definition") alınmıştır; kurumdaki
NNMi sürümüyle doğrulanmalıdır (eşleme ``verify: true``, F1-03). ``NNMiSimulator`` kullanılan alt kümeyi yanıtlar.
"""
from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..http import HttpClient, HttpError, HttpResponse
from ..safe_xml import Node, UnsafeXml, parse

__all__ = ["NNMiClient", "NNMiSimulator", "get_interfaces_envelope", "get_nodes_envelope"]

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
NODE_NS = "http://node.sdk.nms.ov.hp.com/"
IFACE_NS = "http://iface.sdk.nms.ov.hp.com/"
FILTER_NS = "http://filter.sdk.nms.ov.hp.com/"
PATH = "/NodeBeanService/NodeBean"
IFACE_PATH = "/InterfaceBeanService/InterfaceBean"


def _envelope(op: str, ns: str, after_id: int, max_objects: int) -> bytes:
    """``op`` isteği: ``id > after_id`` VE ``maxObjects``. Değerler yalnızca tam sayıdır (enjeksiyon yok)."""
    after_id, max_objects = int(after_id), int(max_objects)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_NS}" xmlns:sdk="{ns}" xmlns:fil="{FILTER_NS}" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<soapenv:Header/><soapenv:Body><sdk:{op}>'
        f'<arg0 xsi:type="fil:expression"><operator>AND</operator>'
        f'<subFilters xsi:type="fil:condition"><name>id</name><operator>GT</operator><value>{after_id}</value>'
        f'</subFilters>'
        f'<subFilters xsi:type="fil:constraint"><name>maxObjects</name><value>{max_objects}</value></subFilters>'
        f'</arg0></sdk:{op}></soapenv:Body></soapenv:Envelope>'
    ).encode()


def get_nodes_envelope(after_id: int, max_objects: int) -> bytes:
    return _envelope("getNodes", NODE_NS, after_id, max_objects)


def get_interfaces_envelope(after_id: int, max_objects: int) -> bytes:
    return _envelope("getInterfaces", IFACE_NS, after_id, max_objects)


def _node_dict(item: Node) -> dict[str, Any]:
    """``<return>`` öğesinin yalın çocukları → alan sözlüğü (iç içe yapılar alınmaz)."""
    return {c.tag: c.text for c in item.children if not c.children}


class NNMiClient:
    data_source = "NNMi"

    def __init__(self, http: HttpClient, *, page_size: int = 500) -> None:
        if not 1 <= page_size <= 5000:
            raise HttpError("Sayfa boyutu 1–5000 olmalı")
        self.http, self.page_size = http, page_size

    def _page(self, path: str, op: str, envelope: bytes) -> list[dict[str, Any]]:
        resp = self.http.request("POST", path, raw=envelope, content_type="text/xml; charset=utf-8",
                                 extra={"SOAPAction": '""'})
        try:
            root = parse(resp.body)
        except UnsafeXml as exc:
            raise HttpError(f"NNMi yanıtı reddedildi: {exc}") from None
        body = root.find("Body") if root.tag == "Envelope" else None
        if body is None:
            raise HttpError("NNMi yanıtı SOAP zarfı değil")
        if body.find("Fault") is not None:
            raise HttpError("NNMi SOAP hatası (ayrıntı yazılmaz)")
        response = body.find(f"{op}Response")
        if response is None:
            raise HttpError(f"NNMi yanıtında {op}Response yok")
        return [_node_dict(r) for r in response.findall("return")]

    def _all(self, path: str, op: str, build: Any, what: str) -> list[dict[str, Any]]:
        """Kimlik sırasıyla tüm sayfalar; sıra bozulursa çalışma durur (sessiz eksik veri yok)."""
        out: list[dict[str, Any]] = []
        last = 0
        while True:
            rows = self._page(path, op, build(last, self.page_size))
            if not rows:
                return out
            for row in rows:
                try:
                    rid = int(row.get("id", ""))
                except ValueError:
                    raise HttpError(f"NNMi {what} kimliği sayısal değil") from None
                if rid <= last:
                    raise HttpError(f"NNMi {what} kimlik sırasında gelmedi; kayıt atlanmasın diye çalışma durdu")
                last = rid
                out.append(row)

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """Her çalışma tam listedir: düğümler, sonra arayüzler; düğümler MAC, arayüz sayısı ve L2 komşularıyla verilir."""
        nodes = self._all(PATH, "getNodes", get_nodes_envelope, "düğüm")
        for n in nodes:
            if not n.get("uuid"):
                raise HttpError("NNMi düğümünde uuid yok")
        ifaces = self._all(IFACE_PATH, "getInterfaces", get_interfaces_envelope, "arayüz")
        by_id = {int(n["id"]): n for n in nodes}
        macs: dict[int, list[str]] = {}
        counts: dict[int, int] = {}
        links: dict[str, set[int]] = {}
        for i in ifaces:
            host = i.get("hostedOnId", "")
            if not str(host).isdigit() or int(host) not in by_id:
                continue  # kapsam dışı veya silinmiş düğümün arayüzü
            host_id = int(host)
            mac = (i.get("physicalAddress") or "").strip()
            if mac:
                macs.setdefault(host_id, []).append(mac)
                counts[host_id] = counts.get(host_id, 0) + 1
            conn = (i.get("connectionId") or "").strip()
            if conn:
                links.setdefault(conn, set()).add(host_id)
        down: dict[int, set[int]] = {}
        up: dict[int, set[int]] = {}
        for members in links.values():
            for a in members:
                for b in members:
                    if a < b:  # kimliği küçük uç üst: iki uçtan gelen kayıt aynı ilişkiyi üretir
                        down.setdefault(a, set()).add(b)
                        up.setdefault(b, set()).add(a)
        for nid, n in by_id.items():
            yield {**n, "_mac_addresses": sorted(set(macs.get(nid, []))), "_interface_count": counts.get(nid, 0),
                   "_l2_down": sorted(by_id[x]["uuid"] for x in down.get(nid, ())),
                   "_l2_up": sorted(by_id[x]["uuid"] for x in up.get(nid, ()))}


# ---------------------------------------------------------------------------------------------------- simülatör
class NNMiSimulator:
    """``NodeBeanService.getNodes`` ve ``InterfaceBeanService.getInterfaces`` alt kümesi (``Transport``). Örnek:
    ``connectors/nnmi/samples/nodes.json`` (``nodes`` ve ``interfaces``).

    İstek gövdesi ``safe_xml`` ile okunur (``id GT`` + ``maxObjects``). Basic kimlik doğrulama yoksa 401. ``unordered``:
    sayfayı ters sırada döndürür (sıralama güvencesi sınaması); ``fault``: SOAP Fault döndürür.
    """

    def __init__(self, nodes: list[dict[str, Any]], *, interfaces: list[dict[str, Any]] | None = None,
                 unordered: bool = False, fault: bool = False) -> None:
        self.nodes = sorted(nodes, key=lambda n: int(n["id"]))
        self.interfaces = sorted(interfaces or [], key=lambda n: int(n["id"]))
        self.unordered, self.fault = unordered, fault
        self.calls: list[tuple[int, int]] = []
        self.paths: list[str] = []

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> NNMiSimulator:
        doc = json.loads((Path(directory) / "nodes.json").read_text(encoding="utf-8"))
        return cls(doc["nodes"], interfaces=doc.get("interfaces", []), **kw)

    @staticmethod
    def _xml(status: int, body: str) -> HttpResponse:
        doc = f'<?xml version="1.0" encoding="UTF-8"?><S:Envelope xmlns:S="{SOAP_NS}"><S:Body>{body}</S:Body></S:Envelope>'
        return HttpResponse(status, {"content-type": "text/xml; charset=utf-8"}, doc.encode("utf-8"))

    @staticmethod
    def _esc(v: Any) -> str:
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        if not str(headers.get("Authorization", "")).startswith("Basic "):
            return HttpResponse(401, {"content-type": "text/html"}, b"<html>Unauthorized</html>")
        path = urllib.parse.urlsplit(url).path
        if method != "POST" or path not in (PATH, IFACE_PATH):
            return HttpResponse(404, {}, b"")
        self.paths.append(path)
        req = parse(body or b"")
        conds = req.iter("subFilters")
        after = next(int(c.find("value").text) for c in conds if c.find("name").text == "id")  # type: ignore[union-attr]
        limit = next(int(c.find("value").text) for c in conds if c.find("name").text == "maxObjects")  # type: ignore[union-attr]
        self.calls.append((after, limit))
        if self.fault:
            return self._xml(500, "<S:Fault><faultcode>S:Server</faultcode><faultstring>gizli ayrıntı</faultstring>"
                                  "</S:Fault>")
        rows, op, ns = (self.nodes, "getNodes", NODE_NS) if path == PATH else (self.interfaces, "getInterfaces", IFACE_NS)
        page = [n for n in rows if int(n["id"]) > after][:limit]
        if self.unordered:
            page = list(reversed(page))
        items = "".join("<return>" + "".join(f"<{k}>{self._esc(v)}</{k}>" for k, v in n.items()) + "</return>"
                        for n in page)
        return self._xml(200, f'<ns2:{op}Response xmlns:ns2="{ns}">{items}</ns2:{op}Response>')

"""OpenText (Micro Focus) NNMi istemcisi (F1-03, U5.2): WS-I SOAP ``NodeBeanService`` ile ağ düğümleri (Ağ rolü).

* **Uç:** ``POST /NodeBeanService/NodeBean`` (SOAP 1.1, document/literal), işlem ``getNodes``. Kimlik doğrulama Basic;
  NNMi kullanıcısının "Web Services Client" rolü yeter (salt okunur). Parola dosyadan okunur.
* **Sayfalama:** süzgeç ``id > SON`` koşulu + ``maxObjects`` kısıtı (anahtar sırası). Gelen kimlikler artan sırada
  değilse çalışma hata verir: sıralama güvencesi yoksa kayıt atlanabilir, sessizce eksik veri üretilmez. Boş sayfada biter.
* **XML:** yanıt ``safe_xml`` ile ayrıştırılır (DTD, varlık ve dış başvuru reddedilir; ek bağımlılık yok).
* **Hata:** SOAP ``Fault`` metni kurum bilgisi içerebilir; iletiye yazılmaz.

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

__all__ = ["NNMiClient", "NNMiSimulator", "get_nodes_envelope"]

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
NODE_NS = "http://node.sdk.nms.ov.hp.com/"
FILTER_NS = "http://filter.sdk.nms.ov.hp.com/"
PATH = "/NodeBeanService/NodeBean"


def get_nodes_envelope(after_id: int, max_objects: int) -> bytes:
    """``getNodes`` isteği: ``id > after_id`` VE ``maxObjects``. Değerler yalnızca tam sayıdır (enjeksiyon yok)."""
    after_id, max_objects = int(after_id), int(max_objects)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_NS}" xmlns:node="{NODE_NS}" xmlns:fil="{FILTER_NS}" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<soapenv:Header/><soapenv:Body><node:getNodes>'
        f'<arg0 xsi:type="fil:expression"><operator>AND</operator>'
        f'<subFilters xsi:type="fil:condition"><name>id</name><operator>GT</operator><value>{after_id}</value>'
        f'</subFilters>'
        f'<subFilters xsi:type="fil:constraint"><name>maxObjects</name><value>{max_objects}</value></subFilters>'
        f'</arg0></node:getNodes></soapenv:Body></soapenv:Envelope>'
    ).encode()


def _node_dict(item: Node) -> dict[str, Any]:
    """``<return>`` öğesinin yalın çocukları → alan sözlüğü (iç içe yapılar alınmaz)."""
    return {c.tag: c.text for c in item.children if not c.children}


class NNMiClient:
    data_source = "NNMi"

    def __init__(self, http: HttpClient, *, page_size: int = 500) -> None:
        if not 1 <= page_size <= 5000:
            raise HttpError("Sayfa boyutu 1–5000 olmalı")
        self.http, self.page_size = http, page_size

    def _page(self, after_id: int) -> list[dict[str, Any]]:
        resp = self.http.request("POST", PATH, raw=get_nodes_envelope(after_id, self.page_size),
                                 content_type="text/xml; charset=utf-8", extra={"SOAPAction": '""'})
        try:
            root = parse(resp.body)
        except UnsafeXml as exc:
            raise HttpError(f"NNMi yanıtı reddedildi: {exc}") from None
        body = root.find("Body") if root.tag == "Envelope" else None
        if body is None:
            raise HttpError("NNMi yanıtı SOAP zarfı değil")
        if body.find("Fault") is not None:
            raise HttpError("NNMi SOAP hatası (ayrıntı yazılmaz)")
        response = body.find("getNodesResponse")
        if response is None:
            raise HttpError("NNMi yanıtında getNodesResponse yok")
        return [_node_dict(r) for r in response.findall("return")]

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """Her çalışma tam listedir."""
        last = 0
        while True:
            rows = self._page(last)
            if not rows:
                break
            for row in rows:
                try:
                    nid = int(row.get("id", ""))
                except ValueError:
                    raise HttpError("NNMi düğüm kimliği sayısal değil") from None
                if nid <= last:
                    raise HttpError("NNMi düğümleri kimlik sırasında gelmedi; kayıt atlanmasın diye çalışma durdu")
                last = nid
                if not row.get("uuid"):
                    raise HttpError("NNMi düğümünde uuid yok")
                yield row


# ---------------------------------------------------------------------------------------------------- simülatör
class NNMiSimulator:
    """``NodeBeanService.getNodes`` alt kümesi (``Transport``). Örnek: ``connectors/nnmi/samples/nodes.json``.

    İstek gövdesi ``safe_xml`` ile okunur (``id GT`` + ``maxObjects``). Basic kimlik doğrulama yoksa 401. ``unordered``:
    sayfayı ters sırada döndürür (sıralama güvencesi sınaması); ``fault``: SOAP Fault döndürür.
    """

    def __init__(self, nodes: list[dict[str, Any]], *, unordered: bool = False, fault: bool = False) -> None:
        self.nodes = sorted(nodes, key=lambda n: int(n["id"]))
        self.unordered, self.fault = unordered, fault
        self.calls: list[tuple[int, int]] = []

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> NNMiSimulator:
        return cls(json.loads((Path(directory) / "nodes.json").read_text(encoding="utf-8"))["nodes"], **kw)

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
        if method != "POST" or urllib.parse.urlsplit(url).path != PATH:
            return HttpResponse(404, {}, b"")
        req = parse(body or b"")
        conds = req.iter("subFilters")
        after = next(int(c.find("value").text) for c in conds if c.find("name").text == "id")  # type: ignore[union-attr]
        limit = next(int(c.find("value").text) for c in conds if c.find("name").text == "maxObjects")  # type: ignore[union-attr]
        self.calls.append((after, limit))
        if self.fault:
            return self._xml(500, "<S:Fault><faultcode>S:Server</faultcode><faultstring>gizli ayrıntı</faultstring>"
                                  "</S:Fault>")
        page = [n for n in self.nodes if int(n["id"]) > after][:limit]
        if self.unordered:
            page = list(reversed(page))
        items = "".join("<return>" + "".join(f"<{k}>{self._esc(v)}</{k}>" for k, v in n.items()) + "</return>"
                        for n in page)
        return self._xml(200, f'<ns2:getNodesResponse xmlns:ns2="{NODE_NS}">{items}</ns2:getNodesResponse>')

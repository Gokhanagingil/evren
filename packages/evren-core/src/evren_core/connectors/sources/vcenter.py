"""vCenter istemcisi (F0-11, U5.2): VI/JSON API ile ESXi host'ları ve sanal makineler.

**Neden VI/JSON:** Eşleme dosyaları vSphere yönetilen nesne modelinin özellik adlarını kullanır (``config.instanceUuid``,
``runtime.host``, ``guest.net``). vSphere 8.0 U1 ile gelen VI/JSON API aynı modeli JSON/HTTP üzerinden sunar; SOAP
(XML) veya ek bağımlılık (pyVmomi) gerekmez. Daha eski vCenter (7.x, genel desteği bitti) desteklenmez.

Akış (ADR-0020):

1. ``GET  /sdk/vim25/{sürüm}/ServiceInstance/ServiceInstance/content`` → kök klasör, görünüm yöneticisi, özellik
   toplayıcı, oturum yöneticisi, ``about.instanceUuid`` (vCenter örnek kimliği).
2. ``POST …/SessionManager/{moId}/Login`` (``userName``, ``password``); oturum kimliği ``vmware-api-session-id`` yanıt
   başlığında döner ve sonraki isteklerde aynı başlıkla gönderilir. Kullanıcı adı ve parola yalnızca bu istekte
   gövdededir; parola dosyadan okunur.
3. Her tür için (``HostSystem``, ``VirtualMachine``) kök klasörde özyinelemeli kapsayıcı görünüm açılır; özellikler
   ``PropertyCollector.RetrievePropertiesEx`` ile ``page_size``'lık sayfalarla, ``token`` bitene kadar
   ``ContinueRetrievePropertiesEx`` ile çekilir; görünüm kapatılır (``DestroyView``).
4. Oturum her durumda kapatılır (``Logout``).

**Değer biçimi:** VI/JSON ``propSet[].val`` içindeki ilkel değerleri ``{"_typeName": "string", "_value": …}``, dizileri
``{"_typeName": "ArrayOf…", "_value": [...]}``, nesne başvurularını ``{"_typeName": "ManagedObjectReference",
"type": …, "value": "host-221"}`` biçiminde gönderir. İstemci bunları açar (başvuru → ``moId``) ve noktalı özellik
adlarını iç içe belgeye çevirir; böylece eşleme dosyası API'nin kendi alan adlarıyla yazılır.

**Bağlayıcının eklediği alanlar** (``_`` önekli; API'de yoktur):

* ``_moId``, ``_type``: nesne başvurusu.
* ``_vcenter.instanceUuid``: vCenter örnek kimliği.
* ``_key``: ``<vCenter örnek kimliği>/<moId>`` (küçük harf). ``moId`` yalnızca tek vCenter içinde tekildir
  (``host-10`` her vCenter'da vardır); kaynak anahtarı ve ilişki hedefi bu nitelikli anahtardır.
* ``_ref.host``: VM'nin üzerinde çalıştığı host'un nitelikli anahtarı (``runtime.host``'tan).
* ``_derived.vmwareSerial``: BIOS UUID'den türetilen ve misafir işletim sisteminin bildirdiği seri numarası
  (``VMware-42 1a … 70-81 … f8``).

``VCenterSimulator`` aynı API'nin kullanılan alt kümesini örnek veriyle yanıtlar (testler, duman testi, demo).
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..http import HttpClient, HttpError, HttpResponse

__all__ = ["HOST_PROPERTIES", "VM_PROPERTIES", "VCenterClient", "VCenterSimulator", "unbox", "vmware_serial"]

SESSION_HEADER = "vmware-api-session-id"

HOST_PROPERTIES = (
    "name", "summary.hardware.vendor", "summary.hardware.model", "summary.hardware.uuid",
    "summary.hardware.numCpuPkgs", "summary.hardware.numCpuCores", "summary.hardware.memorySize",
    "summary.config.product.fullName", "hardware.systemInfo.serialNumber", "config.network.vnic",
    "config.network.pnic",
)
VM_PROPERTIES = (
    "name", "config.instanceUuid", "config.uuid", "config.guestId", "config.guestFullName", "config.template",
    "config.files.vmPathName", "config.hardware.numCPU", "config.hardware.memoryMB", "runtime.powerState",
    "runtime.host", "guest.hostName", "guest.ipAddress", "guest.net",
)


def unbox(value: Any) -> Any:
    """VI/JSON değerini sade JSON'a çevirir: kutulu ilkel/dizi → değer, nesne başvurusu → moId, ``_typeName`` atılır."""
    if isinstance(value, list):
        return [unbox(v) for v in value]
    if isinstance(value, dict):
        kind = value.get("_typeName")
        if kind == "ManagedObjectReference":
            return value.get("value")
        if "_value" in value and set(value) <= {"_typeName", "_value"}:
            return unbox(value["_value"])
        return {k: unbox(v) for k, v in value.items() if k != "_typeName"}
    return value


def vmware_serial(bios_uuid: str | None) -> str | None:
    """``421A2B3C-4D5E-…-A3B4C5D6E7F8`` → ``VMware-42 1a 2b 3c 4d 5e 6f 70-81 92 a3 b4 c5 d6 e7 f8``."""
    if not bios_uuid:
        return None
    hexs = bios_uuid.replace("-", "").lower()
    if len(hexs) != 32 or any(c not in "0123456789abcdef" for c in hexs):
        return None
    pairs = [hexs[i:i + 2] for i in range(0, 32, 2)]
    return f"VMware-{' '.join(pairs[:8])}-{' '.join(pairs[8:])}"


def _nest(props: list[dict[str, Any]]) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    for p in props:
        *parents, leaf = p["name"].split(".")
        node = doc
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = unbox(p.get("val"))
    return doc


def _moref(ref: Mapping[str, Any]) -> dict[str, str]:
    return {"_typeName": "ManagedObjectReference", "type": str(ref["type"]), "value": str(ref["value"])}


class VCenterClient:
    data_source = "vCenter"

    def __init__(self, http: HttpClient, *, user: str, password: str, release: str = "8.0.1.0",
                 page_size: int = 500) -> None:
        if not all(part.isdigit() for part in release.split(".")):
            raise HttpError("vCenter API sürümü geçersiz (ör. 8.0.1.0)")
        self.http, self._user, self._password = http, user, password
        self.base = f"/sdk/vim25/{release}"
        self.page_size = page_size
        self.vcenter_uuid: str | None = None

    def __repr__(self) -> str:
        return f"VCenterClient({self.http!r})"

    def _post(self, mo_type: str, mo_id: str, method: str, body: Any) -> Any:
        """Yöntem çağrısı; dönüşü olmayan yöntemler 204 verir (``None``)."""
        return self.http.request("POST", f"{self.base}/{mo_type}/{mo_id}/{method}", body, ok=(200, 204)).json()

    def _login(self, content: Mapping[str, Any]) -> str:
        sm = content["sessionManager"]["value"]
        resp = self.http.request("POST", f"{self.base}/SessionManager/{sm}/Login",
                                 {"userName": self._user, "password": self._password})
        session = resp.headers.get(SESSION_HEADER)
        if not session:
            raise HttpError("vCenter oturum kimliği dönmedi")
        self.http.extra_headers[SESSION_HEADER] = session
        return sm

    def _logout(self, sm: str) -> None:
        try:
            self.http.request("POST", f"{self.base}/SessionManager/{sm}/Logout", ok=(200, 204))
        except HttpError:
            pass  # oturum zaten düşmüş olabilir; sunucu süre sonunda kapatır
        finally:
            self.http.extra_headers.pop(SESSION_HEADER, None)

    def _objects(self, content: Mapping[str, Any], mo_type: str, paths: tuple[str, ...]) -> Iterator[dict[str, Any]]:
        view = self._post("ViewManager", content["viewManager"]["value"], "CreateContainerView",
                          {"container": _moref(content["rootFolder"]), "type": [mo_type], "recursive": True})
        pc = content["propertyCollector"]["value"]
        spec = {"_typeName": "PropertyFilterSpec",
                "propSet": [{"_typeName": "PropertySpec", "type": mo_type, "pathSet": list(paths)}],
                "objectSet": [{"_typeName": "ObjectSpec", "obj": _moref(view), "skip": True,
                               "selectSet": [{"_typeName": "TraversalSpec", "name": "gorunum", "type": "ContainerView",
                                              "path": "view", "skip": False}]}]}
        try:
            result = self._post("PropertyCollector", pc, "RetrievePropertiesEx",
                                {"specSet": [spec], "options": {"_typeName": "RetrieveOptions",
                                                                "maxObjects": self.page_size}})
            while result:
                for obj in result.get("objects") or []:
                    yield self._document(obj, mo_type)
                token = result.get("token")
                if not token:
                    break
                result = self._post("PropertyCollector", pc, "ContinueRetrievePropertiesEx", {"token": token})
        finally:
            try:
                self._post("ManagedObjectView", view["value"], "DestroyView", None)
            except HttpError:
                pass  # görünüm oturumla birlikte kapanır

    def _document(self, obj: Mapping[str, Any], mo_type: str) -> dict[str, Any]:
        mo_id = str(obj["obj"]["value"])
        doc = _nest(obj.get("propSet") or [])
        vc = self.vcenter_uuid or ""
        doc |= {"_moId": mo_id, "_type": mo_type, "_vcenter": {"instanceUuid": vc}, "_key": f"{vc.lower()}/{mo_id}"}
        if mo_type == "VirtualMachine":
            host = (doc.get("runtime") or {}).get("host")
            if host:
                doc["_ref"] = {"host": f"{vc.lower()}/{host}"}
            serial = vmware_serial((doc.get("config") or {}).get("uuid"))
            if serial:
                doc["_derived"] = {"vmwareSerial": serial}
        if obj.get("missingSet"):  # yetki veya erişim sorunu: eksik özellik sessizce yutulmaz
            doc["_missing"] = sorted(m.get("path", "?") for m in obj["missingSet"])
        return doc

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """Önce host'lar, sonra VM'ler (ilişki hedefleri önce gelir). Delta yok: her çalışma tam listedir."""
        content = unbox_content(self.http.request("GET", f"{self.base}/ServiceInstance/ServiceInstance/content").json())
        self.vcenter_uuid = str(content["about"].get("instanceUuid") or "")
        if not self.vcenter_uuid:
            raise HttpError("vCenter örnek kimliği (about.instanceUuid) yok")
        sm = self._login(content)
        try:
            yield from self._objects(content, "HostSystem", HOST_PROPERTIES)
            yield from self._objects(content, "VirtualMachine", VM_PROPERTIES)
        finally:
            self._logout(sm)


def unbox_content(content: Mapping[str, Any]) -> dict[str, Any]:
    """ServiceContent: başvurular ``{type, value}`` olarak kalır (çağrılarda gerekir), ``about`` sade JSON olur."""
    out: dict[str, Any] = {}
    for key in ("rootFolder", "propertyCollector", "viewManager", "sessionManager"):
        ref = content.get(key)
        if not isinstance(ref, Mapping) or "value" not in ref:
            raise HttpError(f"vCenter ServiceContent eksik: {key}")
        out[key] = {"type": ref.get("type"), "value": ref["value"]}
    out["about"] = unbox(content.get("about") or {})
    return out


# ---------------------------------------------------------------------------------------------------- simülatör
_ARRAY_TYPES = {"guest.net": "GuestNicInfo", "config.network.vnic": "HostVirtualNic",
                "config.network.pnic": "PhysicalNic"}
_MOREF_TYPES = {"runtime.host": "HostSystem"}
_LONG = {"summary.hardware.memorySize"}


def _box(path: str, value: Any) -> Any:
    if path in _MOREF_TYPES:
        return {"_typeName": "ManagedObjectReference", "type": _MOREF_TYPES[path], "value": value}
    if path in _ARRAY_TYPES:
        item = _ARRAY_TYPES[path]
        return {"_typeName": f"ArrayOf{item}", "_value": [{"_typeName": item, **v} for v in value]}
    if isinstance(value, bool):
        return {"_typeName": "boolean", "_value": value}
    if isinstance(value, int):
        return {"_typeName": "long" if path in _LONG else "int", "_value": value}
    if isinstance(value, str):
        return {"_typeName": "string", "_value": value}
    return value


def _get(doc: Mapping[str, Any], path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


class VCenterSimulator:
    """VI/JSON alt kümesi (``Transport``). Örnek veri: ``connectors/vcenter/samples/inventory.json`` (sentetik).

    Oturum olmadan (``Login`` dışında) yapılan çağrılara 401 ``NotAuthenticated`` döner; yanlış parola 500
    ``InvalidLogin``. Sayfalama ``maxObjects`` + ``token`` ile; ``fail_on_call`` sıradaki çağrıda 503 döner.
    """

    def __init__(self, inventory: Mapping[str, Any], *, password: str | None = None,
                 fail_on_call: int | None = None) -> None:
        self.inv = inventory
        self.password = password
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.sessions: set[str] = set()
        self.views: dict[str, str] = {}
        self.tokens: dict[str, tuple[str, list[str], int, int]] = {}
        self.destroyed: list[str] = []
        self.logged_out = 0

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> VCenterSimulator:
        return cls(json.loads((Path(directory) / "inventory.json").read_text(encoding="utf-8")), **kw)

    @staticmethod
    def _json(status: int, payload: Any, headers: Mapping[str, str] | None = None) -> HttpResponse:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return HttpResponse(status, {"content-type": "application/json", **(headers or {})}, body)

    def _fault(self, status: int, kind: str) -> HttpResponse:
        return self._json(status, {"_typeName": kind, "faultCause": None, "faultMessage": []})

    def _page(self, mo_type: str, paths: list[str], start: int, size: int) -> dict[str, Any]:
        objs = self.inv["hosts" if mo_type == "HostSystem" else "vms"]
        out = []
        for raw in objs[start:start + size]:
            props = [{"_typeName": "DynamicProperty", "name": p, "val": _box(p, v)}
                     for p in paths if (v := _get(raw, p)) is not None]
            item: dict[str, Any] = {"_typeName": "ObjectContent",
                                    "obj": {"_typeName": "ManagedObjectReference", "type": mo_type,
                                            "value": raw["_moId"]}, "propSet": props}
            if raw.get("_missing"):
                item["missingSet"] = [{"_typeName": "MissingProperty", "path": p,
                                       "fault": {"_typeName": "NoPermission"}} for p in raw["_missing"]]
            out.append(item)
        result: dict[str, Any] = {"_typeName": "RetrieveResult", "objects": out}
        if start + size < len(objs):
            token = f"t{len(self.tokens) + 1}"
            self.tokens[token] = (mo_type, paths, start + size, size)
            result["token"] = token
        return result

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            return HttpResponse(503, {}, b"")
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        parts = path.split("?")[0].strip("/").split("/")  # sdk, vim25, sürüm, tür, moId, yöntem
        if parts[:2] != ["sdk", "vim25"] or len(parts) < 5:
            return self._fault(404, "NotFound")
        mo_type, mo_id, action = parts[3], parts[4], parts[5] if len(parts) > 5 else ""
        req = json.loads(body) if body else {}
        if (mo_type, action) == ("ServiceInstance", "content") and method == "GET":
            return self._json(200, {
                "_typeName": "ServiceContent",
                "rootFolder": {"_typeName": "ManagedObjectReference", "type": "Folder", "value": "group-d1"},
                "propertyCollector": {"_typeName": "ManagedObjectReference", "type": "PropertyCollector",
                                      "value": "propertyCollector"},
                "viewManager": {"_typeName": "ManagedObjectReference", "type": "ViewManager", "value": "ViewManager"},
                "sessionManager": {"_typeName": "ManagedObjectReference", "type": "SessionManager",
                                   "value": "SessionManager"},
                "about": {"_typeName": "AboutInfo", "fullName": self.inv.get("fullName", "VMware vCenter Server 8.0"),
                          "apiVersion": "8.0.1.0", "instanceUuid": self.inv["instanceUuid"]}})
        if (mo_type, action) == ("SessionManager", "Login"):
            if not req.get("userName") or (self.password is not None and req.get("password") != self.password):
                return self._fault(500, "InvalidLogin")
            sid = f"sim-{len(self.sessions) + 1}"
            self.sessions.add(sid)
            return self._json(200, {"_typeName": "UserSession", "userName": req["userName"]}, {SESSION_HEADER: sid})
        if headers.get(SESSION_HEADER) not in self.sessions:
            return self._fault(401, "NotAuthenticated")
        if (mo_type, action) == ("SessionManager", "Logout"):
            self.sessions.discard(headers[SESSION_HEADER])
            self.logged_out += 1
            return self._json(204, None)
        if action == "CreateContainerView":
            view = f"session[sim]{len(self.views) + 1}"
            self.views[view] = req["type"][0]
            return self._json(200, {"_typeName": "ManagedObjectReference", "type": "ContainerView", "value": view})
        if action == "RetrievePropertiesEx":
            spec = req["specSet"][0]
            mo = spec["propSet"][0]["type"]
            if self.views.get(spec["objectSet"][0]["obj"]["value"]) != mo:
                return self._fault(500, "InvalidArgument")
            return self._json(200, self._page(mo, spec["propSet"][0]["pathSet"], 0,
                                              int(req.get("options", {}).get("maxObjects") or 100)))
        if action == "ContinueRetrievePropertiesEx":
            if req.get("token") not in self.tokens:
                return self._fault(500, "InvalidArgument")
            mo, paths, start, size = self.tokens.pop(req["token"])
            return self._json(200, self._page(mo, paths, start, size))
        if action == "DestroyView":
            self.destroyed.append(mo_id)
            return self._json(204, None)
        return self._fault(500, "MethodNotFound")

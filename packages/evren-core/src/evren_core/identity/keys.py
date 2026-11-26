"""Kaynak kaydından kimlik anahtarlarının çıkarılması."""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Any

from ..normalize import (
    normalize_hostname,
    normalize_ip,
    normalize_mac,
    normalize_serial,
    normalize_uuid,
    os_family,
    uuid_from_vmware_serial,
    uuid_variants,
)
from .model import IdentityKeys, SourceRecord
from .rules import CLASS_KEY_ATTRIBUTES

__all__ = ["extract_keys"]

NAME_FIELDS = ("name", "host_name")
FQDN_FIELDS = ("fqdn", "dns_name")
SERIAL_FIELDS = ("serial_number",)
UUID_FIELDS = ("bios_uuid", "smbios_uuid", "uuid")
MAC_FIELDS = ("mac_address",)
IP_FIELDS = ("ip_address",)
OS_FIELDS = ("os", "guest_os_fullname", "os_name")


def _values(record: SourceRecord, fields: Iterable[str], lookup_name: str | None = None) -> list[Any]:
    out: list[Any] = []
    for f in fields:
        v = record.attributes.get(f)
        if v not in (None, ""):
            out.append(v)
    if lookup_name:
        out.extend(v for v in record.lookup.get(lookup_name, ()) if v not in (None, ""))
    return out


def _class_key_value(attr: str, raw: Any) -> str | None:
    """Sınıf anahtarı normalizasyonu: alt ağ CIDR'ye (``10.1.2.5/24`` → ``10.1.2.0/24``), diğerleri küçük harf."""
    text = str(raw).strip()
    if not text or len(text) > 255:
        return None
    if attr == "subnet":
        try:
            return ipaddress.ip_network(text, strict=False).with_prefixlen
        except ValueError:
            return None
    return text.lower()


def extract_keys(record: SourceRecord) -> IdentityKeys:
    names: set[str] = set()
    compacts: set[str] = set()
    fqdns: set[str] = set()
    for raw in _values(record, NAME_FIELDS + FQDN_FIELDS):
        host = normalize_hostname(raw)
        if host and host.usable:
            names.add(host.short)  # type: ignore[arg-type]
            compacts.add(host.compact)  # type: ignore[arg-type]
            if host.fqdn:
                fqdns.add(host.fqdn)

    serials: set[str] = set()
    vmware_serials: set[str] = set()
    uuids: set[str] = set()
    for raw in _values(record, SERIAL_FIELDS, "serial_numbers"):
        serial = normalize_serial(raw)
        if serial is None:
            continue
        if serial.kind == "vmware":
            vmware_serials.add(serial.value)
            uuids |= uuid_variants(uuid_from_vmware_serial(serial))
        else:
            serials.add(serial.value)
    for raw in _values(record, UUID_FIELDS):
        uuids |= uuid_variants(normalize_uuid(raw))

    macs: set[str] = set()
    weak_macs: set[str] = set()
    for raw in _values(record, MAC_FIELDS, "mac_addresses"):
        mac = normalize_mac(raw)
        if mac is None:
            continue
        (macs if mac.strong else weak_macs).add(mac.value)

    ips = {ip.value for ip in (normalize_ip(v) for v in _values(record, IP_FIELDS, "ip_addresses")) if ip}
    families = {f for f in (os_family(v) for v in _values(record, OS_FIELDS)) if f}

    external = {f"{k.lower()}:{str(v).strip().lower()}" for k, v in record.external_ids.items() if v}
    correlation = {str(v).strip().lower() for v in _values(record, ("correlation_id",))}
    vm_objects: set[str] = set()
    vc, obj = record.attributes.get("vcenter_uuid"), record.attributes.get("object_id")
    if vc and obj:
        vm_objects.add(f"{str(vc).strip().lower()}:{str(obj).strip().lower()}")

    class_keys = {f"{a}={v}" for a in CLASS_KEY_ATTRIBUTES
                  if record.attributes.get(a) not in (None, "")
                  and (v := _class_key_value(a, record.attributes[a])) is not None}

    return IdentityKeys(
        names=frozenset(names),
        compacts=frozenset(compacts),
        fqdns=frozenset(fqdns),
        serials=frozenset(serials),
        vmware_serials=frozenset(vmware_serials),
        uuids=frozenset(uuids),
        macs=frozenset(macs),
        weak_macs=frozenset(weak_macs),
        ips=frozenset(ips),
        external_ids=frozenset(external),
        correlation_ids=frozenset(correlation),
        vm_objects=frozenset(vm_objects),
        os_families=frozenset(families),
        class_keys=frozenset(class_keys),
    )

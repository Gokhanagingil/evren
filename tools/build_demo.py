#!/usr/bin/env python3
"""Arayüz için sentetik demo veri seti üretir — çekirdeğin GERÇEK hattından geçirerek.

Akış (uzlaştırıcı servisinin referans akışıyla aynı):
  kaynak yerel JSON (vCenter, BMC Helix Discovery, NNMi, Zabbix, Defender, ServiceNow)
    → connectors/*/…mapping.yaml (FieldMapping)          → IngestRecord
    → ServiceNow: IdentityEngine.import_existing (sys_id korunur)
    → diğerleri: IdentityEngine.process (IRE kuralları, skor, çelişki denetimi)
    → Reconciler (rol tabanlı uzlaştırma, veri tazeleme)  → CI öznitelik kararları + gerekçe
    → evaluate_run (devre kesici)                          → kaynak sağlığı
    → GRC Platformu kontrol önizlemesi (provenance + inconclusive ilkesi)

Çıktı: apps/web/content/demo.json. Kurallar, roller, sınıflar ve kaynak kaydı gerçek metadata'dan gelir;
CI'lar, kişiler, adresler ve çalışma geçmişi tamamen uydurmadır (sabit tohum, sabit tarih).

Kullanım: python tools/build_demo.py [--check]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import yaml

from _bootstrap import REPO_ROOT
from evren_core.change_control import (
    ANOMALY_STATUS_TR,
    APPROVAL_TR,
    STATE_TR,
    TYPE_TR,
    VERDICT_TR,
    ChangeRecord,
    evaluate,
)
from evren_core.circuit_breaker import evaluate_run
from evren_core.connectors import FieldMapping
from evren_core.identity import IdentityEngine, IdentityResolver, InMemoryCandidateIndex, InMemoryLinkStore, Outcome, RuleSet
from evren_core.identity.adapters import source_record_from_ingest
from evren_core.metadata import load_metadata, sys_id_for
from evren_core.reconcile import Reconciler, SourceValues

OUT = REPO_ROOT / "apps" / "web" / "content" / "demo.json"
NOW = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
SEED = 20261125
RUN = uuid.UUID("00000000-0000-4000-8000-00000000d3e0")
DOMAIN = "corp.ornek.local"

# --------------------------------------------------------------------------- uydurma referans verisi
GROUPS = {
    "Sunucu Operasyon": "Sunucu Operasyon",
    "Veritabanı Yönetimi": "Veritabanı Yönetimi",
    "Ağ Operasyon": "Ağ Operasyon",
    "Uygulama Destek": "Uygulama Destek",
}
USERS = ["Ayşe Demir (demo)", "Mehmet Kaya (demo)", "Zeynep Arslan (demo)", "Burak Çelik (demo)", "Elif Şahin (demo)"]
LOCATIONS = {"ist": "İstanbul VM-1", "ank": "Ankara Felaket Kurtarma"}
#: Destek grubunun bağlı olduğu maliyet merkezi (U4.3). Kimlikler ayrı ve deterministik üretilir;
#: Gen dizisini kaydırmaz, var olan demo sys_id'leri değişmez.
COST_CENTERS = {
    "Sunucu Operasyon": "MM-110 Sunucu altyapısı (demo)",
    "Veritabanı Yönetimi": "MM-120 Veri platformu (demo)",
    "Ağ Operasyon": "MM-130 Ağ altyapısı (demo)",
    "Uygulama Destek": "MM-210 Uygulama hizmetleri (demo)",
}
APPS = [
    ("erp", "ERP uygulama", "linux", "Uygulama Destek"),
    ("db", "Veritabanı", "linux", "Veritabanı Yönetimi"),
    ("web", "Web ön yüz", "linux", "Uygulama Destek"),
    ("dc", "Etki alanı denetleyicisi", "win", "Sunucu Operasyon"),
    ("muh", "Muhasebe", "win", "Uygulama Destek"),
    ("crm", "Müşteri ilişkileri", "win", "Uygulama Destek"),
    ("bi", "Raporlama", "linux", "Veritabanı Yönetimi"),
    ("file", "Dosya sunucusu", "win", "Sunucu Operasyon"),
    ("log", "Log toplama", "linux", "Sunucu Operasyon"),
]
OS = {
    "linux": ("Red Hat Enterprise Linux 9 (64-bit)", "Red Hat Enterprise Linux 9.4", "9.4", "RedHatEnterpriseLinux", "Linux"),
    "win": ("Microsoft Windows Server 2022 (64-bit)", "Microsoft Windows Server 2022 Standard", "10.0.20348",
            "WindowsServer2022", "Windows"),
}


@dataclass
class Host:
    name: str
    role_tr: str
    family: str
    site: str
    virtual: bool
    cpu: int
    ram: int
    serial: str
    bios_uuid: str
    mac: str
    ip: str
    used_for: str
    group: str
    owner: str | None
    in_sn: bool = True
    in_bmc: bool = True
    in_zabbix: bool = True
    in_defender: bool = True
    vm_tools: bool = True
    bmc_age_days: float = 0.4
    quirks: dict[str, Any] = field(default_factory=dict)

    @property
    def fqdn(self) -> str:
        return f"{self.name}.{DOMAIN}"


class Gen:
    def __init__(self) -> None:
        # Sentetik demo verisi; kriptografik kullanım yok (bandit B311 bilinçli olarak kapatıldı).
        self.rng = random.Random(SEED)  # noqa: S311  # nosec B311

    def hex(self, n: int = 32) -> str:
        return f"{self.rng.getrandbits(n * 4):0{n}x}"

    def uuid(self) -> str:
        h = self.hex(32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}".upper()

    def serial(self) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
        return "CZJ" + "".join(self.rng.choice(alphabet) for _ in range(7))

    def mac(self, oui: str) -> str:
        return oui + ":" + ":".join(f"{self.rng.randrange(256):02x}" for _ in range(3))


def vmware_serial(bios_uuid: str) -> str:
    h = bios_uuid.replace("-", "").lower()
    pairs = [h[i:i + 2] for i in range(0, 32, 2)]
    return "VMware-" + " ".join(pairs[:8]) + "-" + " ".join(pairs[8:])


# --------------------------------------------------------------------------- envanter
def build_hosts(g: Gen) -> list[Host]:
    hosts: list[Host] = []
    for i in range(36):
        app, role_tr, family, group = APPS[i % len(APPS)]
        site = "ist" if i % 4 else "ank"
        idx = i // len(APPS) + 1
        virtual = g.rng.random() < 0.75
        bios = g.uuid()
        hosts.append(Host(
            name=f"{site}-{app}-{idx:02d}", role_tr=role_tr, family=family, site=site, virtual=virtual,
            cpu=g.rng.choice([2, 4, 8, 16]), ram=g.rng.choice([8192, 16384, 32768, 65536]),
            serial=vmware_serial(bios) if virtual else g.serial(), bios_uuid=bios,
            mac=g.mac("00:50:56") if virtual else g.mac("3c:a8:2a"),
            ip=f"10.{20 if site == 'ist' else 40}.{10 + i // 12}.{20 + i}",
            used_for="Disaster recovery" if site == "ank" else g.rng.choice(["Production"] * 3 + ["Test", "Development"]),
            group=group, owner=g.rng.choice(USERS),
        ))
    # Kapsam boşlukları ve tuzaklar (gerçek hayatta görülen durumlar)
    by = {h.name: h for h in hosts}
    for name in ("ist-web-01", "ist-bi-02", "ist-log-02"):
        by[name].in_zabbix = False            # izlenmeyen sunucu
    for name in ("ist-file-01", "ist-crm-02"):
        by[name].in_defender = False          # korumasız sunucu
    for name in ("ist-log-03", "ist-muh-02"):
        by[name].in_sn = False                # SN CMDB'de olmayan, keşifte bulunan sunucu
    by["ist-dc-03"].in_bmc = False
    by["ist-erp-02"].owner = None              # sahipsiz üretim CI'ı
    by["ist-db-02"].owner = None
    for name in ("ist-crm-01", "ist-bi-01"):
        by[name].bmc_age_days = 9.0            # BMC taraması eski → veri tazeleme kuralı devreye girer
    by["ist-file-03"].quirks["zabbix_ip_as_name"] = True    # Zabbix'te host adı IP → kimlik verisi yetersiz
    by["ist-db-01"].quirks["sn_duplicate"] = True          # SN'de mükerrer kayıt (eski donanım serisiyle)
    by["ist-web-02"].quirks["vm_recreated"] = True          # VM aynı adla yeniden kuruldu (yeni BIOS UUID)
    by["ist-muh-03"].quirks["zabbix_asset_tag"] = "DMB-00417"  # izleme aracı yönetişim alanı göndermeye çalışıyor
    by["ist-dc-01"].quirks["manual_owner"] = USERS[4]       # sahiplik Evren arayüzünde elle düzeltildi
    for h in hosts:
        if h.site == "ank":
            h.in_defender = h.in_defender and h.family == "win"
    return hosts


NET = [
    ("IST-DC1-CORE-SW01", "switchrouter", "Cisco NX-OS nxos.9.3.10", True, True),
    ("IST-DC1-CORE-SW02", "switchrouter", "Cisco NX-OS nxos.9.3.10", True, True),
    ("IST-DC1-ACC-SW11", "switch", "Cisco IOS-XE 17.9.4", True, False),
    ("IST-DC1-ACC-SW12", "switch", "Cisco IOS-XE 17.9.4", True, True),
    ("IST-DC1-EDGE-RT01", "router", "Cisco IOS-XR 7.8.2", True, True),
    ("IST-DC1-FW01", "firewall", "FortiOS 7.2.8", True, True),
    ("ANK-DR-CORE-SW01", "switchrouter", "Cisco NX-OS nxos.9.3.10", True, False),
    ("ANK-DR-FW01", "firewall", "FortiOS 7.2.8", False, True),
]


# --------------------------------------------------------------------------- kaynak yerel yükler
def sn_server(g: Gen, h: Host, refs: dict[str, str], sys_id: str | None = None, serial: str | None = None) -> dict:
    cls = "cmdb_ci_linux_server" if h.family == "linux" else "cmdb_ci_win_server"
    return {
        "_table": "cmdb_ci_server",  # ServiceNow istemcisinin eklediği tablo adı (eşleme tabloya göre seçer, ADR-0022)
        "sys_id": sys_id or g.hex(), "sys_class_name": cls, "name": h.name, "fqdn": h.fqdn if not h.virtual else "",
        "ip_address": h.ip, "serial_number": serial if serial is not None else h.serial, "os": OS[h.family][1],
        "support_group": ref_id(refs, "group", h.group), "owned_by": ref_id(refs, "user", h.owner) if h.owner else "",
        "used_for": h.used_for, "location": ref_id(refs, "location", LOCATIONS[h.site]),
        "cost_center": ref_id(refs, "cost_center", COST_CENTERS[h.group]),
        "operational_status": "1", "install_status": "1", "short_description": h.role_tr,
        "mac_address": h.mac,
    }


def vcenter_vm(h: Host, instance_uuid: str, bios: str) -> dict:
    os_full = OS[h.family][0]
    return {
        "_moId": f"vm-{1000 + int(instance_uuid[:4], 16) % 9000}",
        "_type": "VirtualMachine",
        "_vcenter": {"instanceUuid": "8D5F1A2B-0000-4C3D-9E8F-ABCDEF012345"},
        "_derived": {"vmwareSerial": vmware_serial(bios)},
        "name": f"{h.name} ({h.role_tr})",
        "config": {"instanceUuid": instance_uuid, "uuid": bios, "guestId": f"{h.family}_64Guest",
                   "guestFullName": os_full, "template": False,
                   "files": {"vmPathName": f"[DS-{h.site.upper()}-01] {h.name}/{h.name}.vmx"},
                   "hardware": {"numCPU": h.cpu, "memoryMB": h.ram}},
        "runtime": {"powerState": "poweredOn", "host": f"host-{200 + len(h.name)}"},
        "_ref": {"host": f"8d5f1a2b-0000-4c3d-9e8f-abcdef012345/host-{200 + len(h.name)}"},  # bağlayıcının eklediği
        "guest": ({"hostName": h.fqdn, "ipAddress": h.ip, "net": [{"macAddress": h.mac, "ipAddress": [h.ip]}]}
                  if h.vm_tools else {}),
    }


def bmc_host(g: Gen, h: Host, serial: str, bios: str) -> dict:
    name = h.name.upper() if h.family == "win" else h.name
    return {
        "key": g.hex() + "/Host", "name": name, "hostname": name, "local_fqdn": h.fqdn, "dns_domain": DOMAIN,
        "os": OS[h.family][1], "os_type": OS[h.family][4], "os_version": OS[h.family][2],
        "serial": serial, "uuid": bios, "vendor": "VMware, Inc." if h.virtual else "HPE",
        "num_processors": h.cpu, "cores_per_processor": 1 if h.virtual else 8,
        "processor_type": "Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz", "processor_speed": 2500,
        # BMC fiziksel bellekten işletim sisteminin gördüğünü bildirir: sanal sunucuda küçük sapma
        "ram": h.ram - (256 if h.virtual else 0), "virtual": h.virtual,
        "host_type": f"{OS[h.family][4]} Server", "ip_addrs": [h.ip], "mac_addrs": [h.mac.upper()],
    }


def zabbix_host(h: Host, hostid: int) -> dict:
    tech = h.ip if h.quirks.get("zabbix_ip_as_name") else (
        h.name.upper().replace("-", "_") if h.family == "win" else h.name)
    return {
        "hostid": str(hostid), "host": tech, "name": f"{h.name} - {h.role_tr}", "status": "0",
        "interfaces": [{"interfaceid": str(hostid * 10), "type": "1", "ip": h.ip, "dns": "", "main": "1"}],
        "inventory": {"os_full": OS[h.family][1], "serialno_a": "", "macaddress_a": "" if h.quirks.get(
            "zabbix_ip_as_name") else h.mac.replace(":", "-").upper(), "asset_tag": h.quirks.get("zabbix_asset_tag", "")},
        "hostgroups": [{"groupid": "4", "name": f"Sunucular/{'Windows' if h.family == 'win' else 'Linux'}"}],
    }


def defender_machine(g: Gen, h: Host) -> dict:
    return {
        "id": g.hex(40), "computerDnsName": h.fqdn, "osPlatform": OS[h.family][3],
        "version": OS[h.family][2], "lastIpAddress": h.ip, "aadDeviceId": g.uuid().lower(),
        "healthStatus": "Active", "riskScore": g.rng.choice(["Low", "Low", "Medium", "High"]),
        "exposureLevel": g.rng.choice(["Low", "Medium", "Medium", "High"]),
        "ipAddresses": [{"ipAddress": h.ip, "macAddress": h.mac.replace(":", "").upper(), "operationalStatus": "Up"}],
    }


def nnmi_node(g: Gen, name: str, category: str, firmware: str, serial: str, ip: str) -> dict:
    return {"uuid": str(uuid.UUID(int=g.rng.getrandbits(128), version=4)), "name": name.lower(), "systemName": name,
            "longName": f"{name.lower()}.net.ornek.local", "systemObjectId": ".1.3.6.1.4.1.9.1.2494",
            "systemDescription": firmware, "deviceCategory": f"com.hp.ov.nms.devices.{category}",
            "managementAddress": ip, "serialNumber": serial}


def ref_id(refs: dict[str, str], kind: str, label: str) -> str:
    return refs[f"{kind}:{label}"]  # build() başında deterministik olarak doldurulur


# --------------------------------------------------------------------------- hat
class Pipeline:
    def __init__(self) -> None:
        self.md = load_metadata(REPO_ROOT / "metadata")
        self.registry = self.md.sources
        self.reconciler = Reconciler.from_metadata(self.md.hierarchy, self.md.reconciliation, self.registry)
        self.mappings: dict[str, FieldMapping] = {}
        for src in self.md.data_sources:
            for rel in src.get("mappings") or []:
                doc = yaml.safe_load((REPO_ROOT / rel).read_text(encoding="utf-8"))
                self.mappings[rel.split("/", 1)[1]] = FieldMapping.from_dict(doc)
        counter = iter(range(1, 100_000))
        rules = RuleSet.from_metadata(self.md.identification, self.md.hierarchy)
        self.engine = IdentityEngine(IdentityResolver(rules, InMemoryCandidateIndex(), InMemoryLinkStore()),
                                     id_factory=lambda: f"e7e{next(counter):029x}")
        self.records: list[dict[str, Any]] = []   # her kaynak kaydı + kimlik sonucu
        self.manual_values: dict[str, dict[str, Any]] = {}

    def ingest(self, mapping: str, raw: dict, observed_at: datetime, *, import_sys_id: str | None = None) -> None:
        rec = self.mappings[mapping].apply(raw, run_id=RUN, observed_at=observed_at)
        if rec is None:
            return
        sr = source_record_from_ingest(rec)
        res = self.engine.import_existing(sr, import_sys_id) if import_sys_id else self.engine.process(sr)
        self.records.append({"ingest": rec, "resolution": res})

    def manual(self, ci_sys_id: str, values: dict[str, Any], observed_at: datetime) -> None:
        self.manual_values[ci_sys_id] = {**values, "_observed": observed_at}


def fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Sink(Protocol):
    """Sentetik envanteri tüketen hat: bellek içi (Pipeline) veya PostgreSQL uzlaştırıcısı (tools/load_demo_pg.py)."""

    def ingest(self, mapping: str, raw: dict, observed_at: datetime, *, import_sys_id: str | None = None) -> None: ...

    def manual(self, ci_sys_id: str, values: dict[str, Any], observed_at: datetime) -> None: ...


def feed(sink: Sink) -> tuple[dict[str, str], list[Host]]:
    """Sentetik envanteri kaynak yerel yükler olarak, gerçek alım sırasıyla hatta besler (deterministik)."""
    g = Gen()
    refs: dict[str, str] = {}
    sn_by_name: dict[str, str] = {}
    hosts = build_hosts(g)

    # Referans sys_id'leri deterministik olsun
    for label in GROUPS:
        refs[f"group:{label}"] = g.hex()
    for label in USERS:
        refs[f"user:{label}"] = g.hex()
    for label in LOCATIONS.values():
        refs[f"location:{label}"] = g.hex()
    for label in COST_CENTERS.values():
        refs[f"cost_center:{label}"] = sys_id_for("demo", "cmn_cost_center", label)

    sn_seen = NOW - timedelta(minutes=20)
    vc_seen = NOW - timedelta(hours=1, minutes=10)
    zb_seen = NOW - timedelta(hours=1, minutes=45)
    df_seen = NOW - timedelta(hours=3, minutes=15)
    nn_seen = NOW - timedelta(days=1, hours=3)   # son NNMi çalışması karantinada; önceki başarılı çalışma geçerli

    # 1) ServiceNow ilk yükleme (sys_id korunur)
    for h in hosts:
        if not h.in_sn:
            continue
        raw = sn_server(g, h, refs)
        sink.ingest("servicenow_cmdb/server.mapping.yaml", raw, sn_seen, import_sys_id=raw["sys_id"])
        sn_by_name.setdefault(h.name, raw["sys_id"])
        if h.quirks.get("sn_duplicate"):
            dup = sn_server(g, h, refs, serial=g.serial())
            dup["short_description"] = f"{h.role_tr} (2019 kaydı)"
            sink.ingest("servicenow_cmdb/server.mapping.yaml", dup, sn_seen, import_sys_id=dup["sys_id"])
    net_serials = {}
    for i, (name, cat, _fw, in_sn, _zb) in enumerate(NET):
        net_serials[name] = "FDO" + g.hex(8).upper()
        if not in_sn:
            continue
        sid = g.hex()
        cls = {"switchrouter": "cmdb_ci_ip_switch", "switch": "cmdb_ci_ip_switch", "router": "cmdb_ci_ip_router",
               "firewall": "cmdb_ci_ip_firewall"}[cat]
        raw = {"_table": "cmdb_ci_netgear", "sys_id": sid, "sys_class_name": cls, "name": name.lower(),
               "ip_address": f"10.0.0.{i + 1}",
               "serial_number": net_serials[name], "support_group": refs["group:Ağ Operasyon"],
               "owned_by": refs[f"user:{USERS[1]}"], "used_for": "Production",
               "location": refs[f"location:{LOCATIONS['ist' if name.startswith('IST') else 'ank']}"],
               "cost_center": refs[f"cost_center:{COST_CENTERS['Ağ Operasyon']}"],
               "operational_status": "1", "install_status": "1"}
        sink.ingest("servicenow_cmdb/server.mapping.yaml", raw, sn_seen, import_sys_id=sid)

    # 2) vCenter: VM örneği + misafir sunucu
    for h in hosts:
        if not h.virtual:
            continue
        bios = g.uuid() if h.quirks.get("vm_recreated") else h.bios_uuid
        raw = vcenter_vm(h, g.uuid(), bios)
        sink.ingest("vcenter/vm_instance.mapping.yaml", raw, vc_seen)
        sink.ingest("vcenter/guest_server.mapping.yaml", raw, vc_seen)

    # 3) BMC Helix Discovery
    for h in hosts:
        if h.in_bmc:
            sink.ingest("bmc_discovery/host.mapping.yaml", bmc_host(g, h, h.serial, h.bios_uuid),
                     NOW - timedelta(days=h.bmc_age_days))

    # 4) NNMi
    for i, (name, cat, fw, _sn, _zb) in enumerate(NET):
        sink.ingest("nnmi/mapping.yaml", nnmi_node(g, name, cat, fw, net_serials[name], f"10.0.0.{i + 1}"), nn_seen)

    # 5) Defender
    for h in hosts:
        if h.in_defender:
            sink.ingest("defender/mapping.yaml", defender_machine(g, h), df_seen)

    # 6) Zabbix (sunucular + izlenen ağ cihazları)
    hostid = 10500
    for h in hosts:
        if h.in_zabbix:
            hostid += 1
            sink.ingest("zabbix/mapping.yaml", zabbix_host(h, hostid), zb_seen)
    for i, (name, _cat, _fw, _sn, in_zb) in enumerate(NET):
        if in_zb:
            hostid += 1
            sink.ingest("zabbix/mapping.yaml", {
                "hostid": str(hostid), "host": name, "name": name, "status": "0",
                "interfaces": [{"interfaceid": str(hostid), "type": "2", "ip": f"10.0.0.{i + 1}", "main": "1"}],
                "inventory": {}, "hostgroups": [{"groupid": "9", "name": "Ağ cihazları"}]}, zb_seen)

    # 7) Elle düzeltme (Manual kaynak kaydı uzlaştırmadan geçer)
    for h in hosts:
        if h.quirks.get("manual_owner") and h.name in sn_by_name:
            sink.manual(sn_by_name[h.name], {"owned_by": refs[f"user:{h.quirks['manual_owner']}"]},
                        NOW - timedelta(days=12))
    return refs, hosts


def build() -> dict[str, Any]:
    p = Pipeline()
    refs, hosts = feed(p)
    return assemble(p, p.md, refs, hosts)


# --------------------------------------------------------------------------- çıktı
def assemble(p: Pipeline, md, refs: dict[str, str], hosts: list[Host]) -> dict[str, Any]:
    reg = p.registry
    labels = {c["name"]: c["label_tr"] for c in md.classes}
    choice_labels = {(c["element"]): {str(v["value"]): v["label_tr"] for v in c["values"]} for c in md.choices
                     if c["table"] == "cmdb_ci"}
    ref_names = {v: k.split(":", 1)[1] for k, v in refs.items()}
    index = p.engine.resolver.index

    # CI → bağlı kaynak kayıtları
    by_ci: dict[str, list[dict[str, Any]]] = {}
    queue: list[dict[str, Any]] = []
    source_stats: dict[str, dict[str, int]] = {s: {"records": 0, "linked": 0, "review": 0, "insufficient": 0, "new": 0}
                                               for s in reg.sources}
    for r in p.records:
        rec, res = r["ingest"], r["resolution"]
        st = source_stats[rec.data_source]
        st["records"] += 1
        if res.outcome in (Outcome.LINKED, Outcome.RULE_MATCH, Outcome.SCORE_MATCH, Outcome.NEW):
            st["new" if res.outcome is Outcome.NEW else "linked"] += 1
            by_ci.setdefault(res.ci_sys_id, []).append(r)
        else:
            st["review" if res.outcome is Outcome.REVIEW else "insufficient"] += 1
            queue.append(r)

    def display(attr: str, value: Any) -> str:
        if value is None:
            return "—"
        if attr in choice_labels:
            return choice_labels[attr].get(str(value), str(value))
        if isinstance(value, bool):
            return "Evet" if value else "Hayır"
        if isinstance(value, str) and value in ref_names:
            return ref_names[value]
        if attr in ("ram", "memory") and isinstance(value, int):
            return f"{value // 1024} GB" if value >= 1024 else f"{value} MB"
        return str(value)

    cis: list[dict[str, Any]] = []
    vm_ci_by_key: dict[str, str] = {}
    for ci, recs in by_ci.items():
        for r in recs:
            if r["ingest"].class_hint == "cmdb_ci_vmware_instance":
                vm_ci_by_key[r["ingest"].source_key] = ci
    for ci, recs in by_ci.items():
        profile = index.profile(ci)
        cls = profile.class_name
        inputs = [SourceValues(r["ingest"].data_source, dict(r["ingest"].attributes), r["ingest"].observed_at)
                  for r in recs]
        if ci in p.manual_values:
            vals = dict(p.manual_values[ci])
            observed = vals.pop("_observed")
            inputs.append(SourceValues("Manual", vals, observed))
        report = p.reconciler.reconcile_report(cls, inputs, NOW)
        decisions = report.decisions
        fields = md.fields_of(cls)
        attrs = []
        for a, d in sorted(decisions.items(), key=lambda kv: (kv[0] != "name", kv[0])):
            attrs.append({
                "attribute": a, "label": fields.get(a, {}).get("label_tr", a), "value": d.value,
                "display": display(a, d.value), "source": d.data_source, "role": d.role, "reason": d.reason,
                "observed_at": fmt(d.observed_at),
                "overridden": [{"source": s, "value": v, "display": display(a, v)} for s, v in d.overridden],
                "unauthorized": list(d.unauthorized),
            })
        rejected = [{"attribute": a, "label": fields.get(a, {}).get("label_tr", a),
                     "attempts": [{"source": s_, "display": display(a, v)} for s_, v in vals_]}
                    for a, vals_ in sorted(report.rejected.items())]
        val = {a["attribute"]: a["value"] for a in attrs}
        sources = sorted({r["ingest"].data_source for r in recs} | ({"Manual"} if ci in p.manual_values else set()))
        relations = []
        for r in recs:
            for rel in r["ingest"].relations:
                target = vm_ci_by_key.get(rel.target_source_key)
                if target:
                    relations.append({"type": rel.type, "direction": rel.direction, "target": target})
        is_server = "cmdb_ci_server" in md.hierarchy.ancestors(cls)
        cis.append({
            "sys_id": ci, "class": cls, "class_label": labels.get(cls, cls),
            "name": val.get("name") or "(adsız)", "fqdn": val.get("fqdn"), "ip_address": val.get("ip_address"),
            "os": val.get("os"), "used_for": val.get("used_for"), "used_for_label": display("used_for", val.get("used_for")),
            "support_group": display("support_group", val.get("support_group")) if val.get("support_group") else None,
            "owned_by": display("owned_by", val.get("owned_by")) if val.get("owned_by") else None,
            "operational_status": display("operational_status", val.get("operational_status"))
            if val.get("operational_status") is not None else None,
            "sources": sources, "attributes": attrs, "rejected": rejected, "relations": relations,
            "links": [{"data_source": r["ingest"].data_source, "role": reg.role_of(r["ingest"].data_source),
                       "source_key": r["ingest"].source_key, "outcome": r["resolution"].outcome.value,
                       "summary": r["resolution"].summary_tr, "observed_at": fmt(r["ingest"].observed_at)}
                      for r in recs],
            "server": is_server,
            "coverage": {"discovery": any(reg.role_of(s) in ("Discovery", "Virtualization", "Network") for s in sources),
                         "monitoring": "Zabbix" in sources, "security": "Defender" in sources,
                         "itsm": "ServiceNow" in sources},
            "unauthorized_count": sum(1 for a in attrs if a["unauthorized"]) + len(rejected),
        })
    # Ters yönlü ilişkiler (VM örneği tarafında da görünsün)
    name_of = {c["sys_id"]: c["name"] for c in cis}
    for c in cis:
        for rel in c["relations"]:
            rel["target_name"] = name_of.get(rel["target"], rel["target"])
    # Yalnızca kaynaktan gelen ilişkiler çevrilir; döngü içinde eklenen ters kayıt yeniden çevrilmez
    by_id = {c["sys_id"]: c for c in cis}
    originals = [(c, rel) for c in cis for rel in c["relations"]]
    for c, rel in originals:
        by_id[rel["target"]]["relations"].append({
            "type": rel["type"], "direction": "child" if rel["direction"] == "parent" else "parent",
            "target": c["sys_id"], "target_name": c["name"]})
    cis.sort(key=lambda c: (c["class_label"], c["name"]))

    # Eşleştirme kuyruğu
    q_items = []
    for n, r in enumerate(queue, 1):
        rec, res = r["ingest"], r["resolution"]
        cands = []
        for cid, prob in res.candidates:
            ci = next((c for c in cis if c["sys_id"] == cid), None)
            if ci:
                cands.append({"sys_id": cid, "name": ci["name"], "class_label": ci["class_label"],
                              "probability": round(prob, 4), "sources": ci["sources"],
                              "attributes": {a["attribute"]: a["display"] for a in ci["attributes"]
                                             if a["attribute"] in ("name", "fqdn", "serial_number", "bios_uuid",
                                                                   "ip_address", "os", "used_for")}})
        q_items.append({
            "id": f"EQ-{n:04d}", "kind": "review" if res.outcome is Outcome.REVIEW else "insufficient",
            "data_source": rec.data_source, "role": reg.role_of(rec.data_source), "source_key": rec.source_key,
            "class_label": labels.get(rec.class_hint, rec.class_hint), "observed_at": fmt(rec.observed_at),
            "summary": res.summary_tr, "rule": res.rule, "reasons": list(res.reasons),
            "record": {k: display(k, v) for k, v in rec.attributes.items()},
            "lookup": {"ip": list(rec.lookup.ip_addresses), "mac": list(rec.lookup.mac_addresses)},
            "features": [{"code": f.code, "weight": f.weight, "detail": f.detail} for f in res.features],
            "candidates": cands,
        })

    sources_out, runs_state = build_sources(p, source_stats)
    build_change_control(cis, md)
    controls = build_controls(cis, runs_state)
    servers = [c for c in cis if c["server"]]
    return {
        "generated_at": fmt(NOW), "synthetic": True,
        "note": "Sentetik demo verisi: CI'lar, kişiler, adresler ve çalışma geçmişi uydurmadır. Kurallar, roller, "
                "sınıflar ve eşleme dosyaları gerçek metadata'dır; sonuçlar evren-core hattından geçirilerek üretildi.",
        "summary": {
            "ci_count": len(cis), "server_count": len(servers),
            "network_count": sum(1 for c in cis if "cmdb_ci_netgear" in md.hierarchy.ancestors(c["class"])),
            "vm_count": sum(1 for c in cis if c["class"] == "cmdb_ci_vmware_instance"),
            "queue_review": sum(1 for q in q_items if q["kind"] == "review"),
            "queue_insufficient": sum(1 for q in q_items if q["kind"] == "insufficient"),
            "unmonitored": sum(1 for c in servers if not c["coverage"]["monitoring"]),
            "unprotected": sum(1 for c in servers if not c["coverage"]["security"]),
            "not_in_itsm": sum(1 for c in servers if not c["coverage"]["itsm"]),
            "unauthorized_attempts": sum(c["unauthorized_count"] for c in cis),
            "unauthorized_changes": sum(c["unauthorized_changes"] for c in cis),
            "records": sum(s["stats"]["records"] for s in sources_out),
        },
        "roles": [{"name": r["name"], "label_tr": r["label_tr"], "kind": r["kind"],
                   "description_tr": r.get("description_tr", ""),
                   "ci_attribute_source": bool(r.get("ci_attribute_source", True))} for r in md.source_roles],
        "sources": sources_out,
        "rules": build_rules(md, p),
        "cis": cis,
        "queue": q_items,
        "controls": controls,
    }


RUN_PROFILE = {
    # kaynak: (taban kayıt sayısı, son çalışma senaryosu)
    "ServiceNow": "ok", "vCenter": "ok", "BMC": "ok", "Defender": "ok",
    "Zabbix": "drop", "NNMi": "partial", "ServiceNowChange": "ok",
}


def build_sources(p: Pipeline, stats: dict[str, dict[str, int]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    # Sentetik çalışma geçmişi; kriptografik kullanım yok.
    rng = random.Random(SEED + 7)  # noqa: S311  # nosec B311
    out, states = [], {}
    for src in p.md.data_sources:
        name = src["name"]
        role = next(r for r in p.md.source_roles if r["name"] == src["role"])
        st = stats[name]
        base = st["records"] or (42 if name == "ServiceNowChange" else 0)
        runs = []
        profile = RUN_PROFILE.get(name)
        if profile and base:
            history: list[int] = []
            for k in range(10, -1, -1):
                started = NOW - timedelta(hours=4 * k + 1) if name != "BMC" else NOW - timedelta(days=k, hours=3)
                count = base + rng.randint(-1, 1) if k else base
                completed, changed, errors = True, rng.randint(0, max(1, base // 12)), 0
                if k == 0 and profile == "drop":
                    count = round(base * 0.9)
                if k == 0 and profile == "partial":
                    completed, count, changed = False, round(base * 0.4), 0
                v = evaluate_run(count, history[-8:], changed_count=changed, error_count=errors, completed=completed)
                runs.append({"started_at": fmt(started), "finished_at": fmt(started + timedelta(minutes=6 + rng.randint(0, 9))),
                             "records": count, "changed": changed, "errors": errors, "completed": completed,
                             "state": v.state.value, "reasons": list(v.reasons),
                             "baseline": round(v.baseline, 1) if v.baseline is not None else None})
                # Taban çizgisi: tamamlanmış ve karantinaya alınmamış çalışmalar (ilk çalışmalar yetersiz
                # geçmiş nedeniyle WARN olur; bunlar da sayılmazsa taban çizgisi hiç oluşmaz)
                if completed and v.state.value != "open":
                    history.append(count)
            runs.reverse()
        state = runs[0]["state"] if runs else None
        states[name] = state or "ok"
        reg_src = p.registry.source(name)
        out.append({
            "name": name, "label_tr": src["label_tr"], "role": src["role"], "role_label": role["label_tr"],
            "kind": role["kind"], "lifecycle": reg_src.lifecycle, "role_rank": reg_src.role_rank,
            "writes_attributes": p.registry.writes_attributes(name), "may_create_ci": p.registry.may_create_ci(name),
            "product": dict(src.get("product") or {}), "connector": src.get("connector"),
            "mappings": list(src.get("mappings") or []), "schedule": src.get("schedule"),
            "authoritative_for_tr": src.get("authoritative_for_tr", ""), "open_question": src.get("open_question"),
            "stats": st, "runs": runs, "breaker": state,
        })
    return out, states


def build_rules(md, p: Pipeline) -> dict[str, Any]:
    labels = {c["name"]: c["label_tr"] for c in md.classes}
    grid = []
    for rule in md.reconciliation.get("rules", []):
        attrs = rule.get("attributes", "*")
        fields = md.fields_of(rule["applies_to"])
        grid.append({
            "applies_to": rule["applies_to"], "class_label": labels.get(rule["applies_to"], rule["applies_to"]),
            "description": rule.get("description", ""),
            "attributes": [{"name": a, "label": fields.get(a, {}).get("label_tr", a)} for a in
                           (attrs if attrs != "*" else [])],
            "priorities": dict(sorted(rule["priorities"].items(), key=lambda kv: kv[1])),
            "sources": {role: [s.name for s in p.registry.sources_in_role(role)] for role in rule["priorities"]},
        })
    ident = []
    for rule in md.identification:
        ident.append({"applies_to": rule["applies_to"], "class_label": labels.get(rule["applies_to"], rule["applies_to"]),
                      "description": rule.get("description", ""), "independent": rule.get("independent", True),
                      "entries": [{"priority": e["priority"], "attributes": e["attributes"],
                                   "evren_extension": bool(e.get("evren_extension"))} for e in rule["entries"]]})
    return {
        "reconciliation": grid,
        "refresh": [{"applies_to": d["applies_to"], "class_label": labels.get(d["applies_to"], d["applies_to"]),
                     "role": d["role"], "effective_days": d["effective_days"]} for d in md.reconciliation.get("data_refresh", [])],
        "staleness": [{**s, "class_label": labels.get(s["applies_to"], s["applies_to"])}
                      for s in md.reconciliation.get("staleness", [])],
        "identification": ident,
    }


def build_change_control(cis: list[dict[str, Any]], md) -> None:
    """Değişiklik denetimi önizlemesi (ADR-0025). İki üretim sunucusunda keşfin (BMC) gördüğü RAM değişikliği gerçek
    değerlendirme mantığıyla (``evren_core.change_control``) onaylı pencereye göre sınıflanır. Değişiklik kayıtları
    (CHG004xxxx) sentetiktir; API modunda bu bölüm uzlaştırıcının ``insight.config_change`` kayıtlarından gelir."""
    policy = md.change_policy
    for c in cis:
        c["change_control"] = {"observations": [], "changes": [], "anomalies": [], "open_unauthorized": 0,
                               "can_review": False}
        c["unauthorized_changes"] = 0

    def ram(c: dict[str, Any]) -> dict[str, Any] | None:
        return next((a for a in c["attributes"] if a["attribute"] == "ram" and isinstance(a["value"], int)), None)

    picks = sorted((c for c in cis if c["server"] and c["used_for"] == "Production" and ram(c)),
                   key=lambda c: c["name"])[:2]
    if len(picks) < 2:
        return
    observed_from, observed_at = NOW - timedelta(hours=18), NOW - timedelta(hours=2)
    scenario = [
        (picks[0], [ChangeRecord("c4" * 16, "CHG0040001", "normal", "implement", "approved",
                                 NOW - timedelta(hours=20), NOW + timedelta(hours=4), NOW - timedelta(hours=19))],
         f"{picks[0]['name']} bellek artırımı"),
        (picks[1], [ChangeRecord("c5" * 16, "CHG0040002", "normal", "authorize", "requested",
                                 NOW - timedelta(hours=20), NOW + timedelta(hours=4)),
                    ChangeRecord("c6" * 16, "CHG0040003", "standard", "closed", "not_requested",
                                 NOW - timedelta(days=5), NOW - timedelta(days=5) + timedelta(hours=1),
                                 NOW - timedelta(days=5), NOW - timedelta(days=5) + timedelta(minutes=40))],
         f"{picks[1]['name']} bellek artırımı (onay bekliyor)"),
    ]
    for c, records, desc in scenario:
        a = ram(c)
        verdict = evaluate(observed_from, observed_at, records, policy, NOW)
        state = "authorized" if verdict.authorized else "unauthorized"
        text = verdict.explanation(observed_from, observed_at)
        anomalies = [] if verdict.authorized else [{
            "sys_id": "a5" * 16, "status": "open", "status_label": ANOMALY_STATUS_TR["open"],
            "severity": policy.severity_for({"used_for": c["used_for"]}), "detected_at": fmt(observed_at + policy.grace),
            "explanation": f"{c['name']}: {a['label']} {a['value'] // 2} → {a['value']}. Kaynak: BMC. {text}",
            "related_change": None, "reviewed_by": None, "reviewed_at": None, "review_note": None}]
        c["change_control"] = {
            "observations": [{
                "attribute": "ram", "label": a["label"], "old": a["value"] // 2, "new": a["value"],
                "old_display": f"{a['value'] // 2048} GB", "new_display": a["display"], "source": "BMC",
                "observed_from": fmt(observed_from), "observed_at": fmt(observed_at), "verdict": state,
                "verdict_label": VERDICT_TR[state], "change_number": verdict.change.number if verdict.change else None,
                "explanation": text, "late": False, "evaluated_at": fmt(observed_at + policy.grace),
                "anomaly_status": "open" if anomalies else None,
                "anomaly_status_label": ANOMALY_STATUS_TR["open"] if anomalies else None}],
            "changes": [{"number": r.number, "short_description": desc if i == 0 else "Yapılandırma yedeği (standart)",
                         "type": r.type, "type_label": TYPE_TR[r.type or "other"], "state": r.state,
                         "state_label": STATE_TR[r.state or "other"], "approval": r.approval,
                         "approval_label": APPROVAL_TR[r.approval or "other"],
                         "start_date": fmt(r.start_date) if r.start_date else None,
                         "end_date": fmt(r.end_date) if r.end_date else None,
                         "work_start": fmt(r.work_start) if r.work_start else None,
                         "work_end": fmt(r.work_end) if r.work_end else None} for i, r in enumerate(records)],
            "anomalies": anomalies, "open_unauthorized": len(anomalies), "can_review": False}
        c["unauthorized_changes"] = c["change_control"]["open_unauthorized"]


def build_controls(cis: list[dict[str, Any]], states: dict[str, str]) -> list[dict[str, Any]]:
    """GRC Platformu'na gidecek kontrol sonuçlarının önizlemesi (docs/08). Kısmi veri → inconclusive."""
    servers = [c for c in cis if c["server"]]
    prod = [c for c in servers if c["used_for"] == "Production"]

    def status_for(source: str | None, failed: list) -> tuple[str, str]:
        collection = "complete" if source is None or states.get(source) == "ok" else "partial"
        if collection != "complete":
            return "inconclusive", collection
        return ("fail" if failed else "pass"), collection

    def item(cid, title, source, failed, query, why):
        status, collection = status_for(source, failed)
        return {"id": cid, "title": title, "status": status, "why": why,
                "evidence": [{"sys_id": c["sys_id"], "name": c["name"]} for c in failed],
                "provenance": {"source_system": "Evren", "query_reference": query, "collection_status": collection,
                               "depends_on": source}}

    return [
        item("CMDB-01", "İzlenmeyen üretim sunucusu", "Zabbix", [c for c in prod if not c["coverage"]["monitoring"]],
             "sys_class_nameINSTANCEOFcmdb_ci_server^used_for=Production^evren.coverage.monitoring=false",
             "Zabbix'in son çalışması devre kesicide uyarıda; kapsam verisi eksik olabilir, sonuç kesin değil."),
        item("CMDB-02", "Uç nokta koruması olmayan sunucu", "Defender",
             [c for c in servers if not c["coverage"]["security"]],
             "sys_class_nameINSTANCEOFcmdb_ci_server^evren.coverage.security=false",
             "Defender ile eşleşmeyen sunucular."),
        item("CMDB-03", "Sahibi olmayan üretim CI'ı", None, [c for c in prod if not c["owned_by"]],
             "sys_class_nameINSTANCEOFcmdb_ci_server^used_for=Production^owned_byISEMPTY",
             "Yönetişim alanı boş; sahip ataması gerekli."),
        item("CMDB-04", "ServiceNow CMDB'de kaydı olmayan sunucu (kapsam boşluğu)", "ServiceNow",
             [c for c in servers if not c["coverage"]["itsm"]],
             "sys_class_nameINSTANCEOFcmdb_ci_server^evren.coverage.itsm=false",
             "Keşifte bulunan ama mevcut CMDB'de olmayan sunucular; gölge mod raporu."),
        item("CMDB-05", "Onaylı değişiklik kaydı olmadan yapılandırması değişen CI", "ServiceNowChange",
             [c for c in cis if c["unauthorized_changes"]], "evren.anomaly.kind=unauthorized_change^status=open",
             "Keşfin gözlediği izlenen öznitelik değişikliği (RAM, CPU, işletim sistemi, seri, firmware) onaylı bir "
             "değişiklik penceresine düşmüyor (ADR-0025)."),
    ]


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="demo.json güncel mi? (CI)")
    args = parser.parse_args(argv)
    text = render(build())
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("apps/web/content/demo.json güncel değil: python tools/build_demo.py", file=sys.stderr)
            return 1
        print("demo.json güncel.")
        return 0
    OUT.write_text(text, encoding="utf-8")
    data = json.loads(text)
    s = data["summary"]
    print(f"demo.json yazıldı: {s['ci_count']} CI, {s['records']} kaynak kaydı, kuyrukta {s['queue_review']} inceleme "
          f"+ {s['queue_insufficient']} yetersiz kimlik.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

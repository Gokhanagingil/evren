#!/usr/bin/env python3
"""Yerel geliştirme yığınını tek komutla yönetir (U1, ADR-0016). Yalnızca standart kütüphane + docker compose.

    python tools/dev_stack.py up [--demo]   # sırlar → imaj → göç → (demo) → Kafka konuları → servisler
    EVREN_BUILD_CA_FILE=/yol/kurum-ca.pem python tools/dev_stack.py up   # TLS denetimli vekil sunucu arkasında
    python tools/dev_stack.py smoke         # uçtan uca duman testi (CI'daki "Yığın" işiyle aynı)
    python tools/dev_stack.py status        # servis durumları
    python tools/dev_stack.py logs [SERVİS]
    python tools/dev_stack.py digests [--pull]   # dış görüntülerin özetleri (sabitleme için)
    python tools/dev_stack.py down [--volumes]

Neden ``up -d --wait`` tek başına değil: tek seferlik işler (göç, Kafka konuları) sırayla ve açıkça çalıştırılır;
biri başarısız olursa yığın yarım açılmaz, hata o adımda görünür.

Duman testi yalnızca 127.0.0.1'e bağlanır ve şunları doğrular: API hazır, CMDB veri içeriyor, uzlaştırıcı çalışanı
canlı; API'den gönderilen bir elle düzenleme konteynerdeki uzlaştırıcı tarafından uygulanıyor (golden record
değişiyor); Kafka konuları açılmış; OpenSearch kümesi en az sarı; olay omurgası uçtan uca çalışıyor (geçici bağlayıcı
kimliği → alım geçidi → Kafka → tüketici → CI → outbox → Kafka CI olayı → kimlik iptali); bağlayıcı çalıştırıcı
(ServiceNow ilk yüklemesi sys_id korunarak, Zabbix, vCenter, BMC, Defender ve NNMi simülatörleri → eşleme → geçit →
uzlaştırıcı → ilişkiler, beş kaynaklı sunucu ve üç kaynaklı ağ cihazı); arama (dizinleyici →
OpenSearch → yetki süzgeçli API; Türkçe harf, yazım hatası, ayırıcısız yazım). Hiçbir sır yazdırılmaz.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.dev.yaml")]
LONG_RUNNING = ["postgres", "kafka", "opensearch", "api", "worker", "ingest", "gateway", "indexer"]
API = "http://127.0.0.1:8088"
GATEWAY = "http://127.0.0.1:8089"
OPENSEARCH = "http://127.0.0.1:59200"
TOPICS = {"evren.ingest.record.v1", "evren.ingest.run.v1", "evren.cmdb.ci.changed.v1", "evren.cmdb.activity.v1",
          "evren.dlq.v1"}


class SmokeError(RuntimeError):
    pass


def compose(*args: str, check: bool = True, capture: bool = False,
            stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*COMPOSE, *args], check=check, text=True, cwd=ROOT,  # nosec B603
                          capture_output=capture, input=stdin)


def step(title: str) -> None:
    print(f"\n==> {title}", flush=True)


def build() -> None:
    """Evren imajı.

    ``EVREN_BUILD_CA_FILE``: TLS denetimli kurumsal vekil sunucu varsa CA paketi (derleme sırrı, imaja yazılmaz).
    ``EVREN_BUILD_OS_UPDATES=0``: Debian deposuna erişimi olmayan geliştirme makinesi (güvenlik güncellemesi atlanır).
    """
    ca = os.environ.get("EVREN_BUILD_CA_FILE")
    os_updates = os.environ.get("EVREN_BUILD_OS_UPDATES", "1")
    if os_updates not in {"0", "1"}:
        raise SystemExit("EVREN_BUILD_OS_UPDATES 0 veya 1 olmalı")
    if not ca and os_updates == "1":
        compose("build", "migrate")
        return
    cmd = ["docker", "build", "--build-arg", f"OS_UPDATES={os_updates}", "-f", "deploy/Dockerfile", "-t", "evren:dev"]
    if ca:
        if not Path(ca).is_file():
            raise SystemExit(f"EVREN_BUILD_CA_FILE bulunamadı: {ca}")
        cmd += ["--secret", f"id=build_ca,src={ca}"]
    subprocess.run([*cmd, "."], check=True, cwd=ROOT)  # nosec B603


def up(demo: bool) -> int:
    step("Geliştirme sırları (deploy/secrets/, git'e girmez)")
    subprocess.run([sys.executable, str(ROOT / "tools" / "dev_secrets.py")], check=True)
    step("İmaj")
    build()
    step("Göç, metadata tohumu ve servis giriş rolleri")
    compose("run", "--rm", "migrate")
    if demo:
        step("Sentetik demo verisi (yalnızca boş veritabanına)")
        res = compose("--profile", "demo", "run", "--rm", "--no-deps", "demo", check=False)
        if res.returncode == 2:
            print("Veritabanı boş değil; demo verisi yeniden yüklenmedi.")
        elif res.returncode:
            return res.returncode
    step("Kafka konuları")
    compose("run", "--rm", "kafka-init")
    step("Servisler (sağlık denetimleri geçene kadar beklenir)")
    compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", "300", *LONG_RUNNING)
    print(f"\nHazır: {API}  (OpenSearch {OPENSEARCH}, Kafka 127.0.0.1:59092, PostgreSQL 127.0.0.1:55432)")
    return 0


# --------------------------------------------------------------------------- duman testi
def http(method: str, url: str, body: dict[str, Any] | None = None, *,
         auth: str | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers |= {"Content-Type": "application/json", "X-Evren-Request": "1", "Origin": API}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except ValueError:
            return exc.code, None


def wait_for(what: str, probe: Any, timeout: float = 120, interval: float = 2) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = probe()
        except (OSError, ValueError) as exc:
            last = type(exc).__name__
        else:
            if last:
                return last
        time.sleep(interval)
    raise SmokeError(f"{what}: {timeout:.0f} sn içinde gerçekleşmedi (son durum: {last!r})")


def check(ok: bool, message: str) -> None:
    if not ok:
        raise SmokeError(message)
    print(f"  ✓ {message}")


def smoke() -> int:
    try:
        step("API ve veritabanı")
        wait_for("API hazır (/readyz)", lambda: http("GET", f"{API}/readyz")[0] == 200)
        print("  ✓ API hazır (/readyz)")
        status, me = http("GET", f"{API}/api/v1/me")
        check(status == 200 and me.get("mode") == "dev" and me["can"]["read_ci"],
              "dev oturumu çözülüyor, CI okuma yetkisi var (/api/v1/me)")
        status, summary = http("GET", f"{API}/api/v1/summary")
        check(status == 200 and summary["ci_count"] > 0, f"CMDB veri içeriyor ({summary.get('ci_count')} CI)")
        worker = wait_for("uzlaştırıcı çalışanı canlı",
                          lambda: (w := http("GET", f"{API}/api/v1/summary")[1]["worker"])["state"] == "ok" and w)
        check(worker["failed"] == 0, f"uzlaştırıcı çalışanı canlı (tarama {worker['poll_seconds']} sn)")

        step("Uçtan uca: API isteği → uzlaştırıcı → golden record")
        status, page = http("GET", f"{API}/api/v1/ci?limit=50")
        check(status == 200 and page["items"], "CI listesi okunuyor")
        target = None
        for item in page["items"]:
            _s, detail = http("GET", f"{API}/api/v1/ci/{item['sys_id']}")
            if any(f["attribute"] == "comments" for f in detail.get("editable") or []):
                target = detail
                break
        check(target is not None, "düzenlenebilir CI bulundu")
        marker = f"duman-testi {uuid.uuid4().hex[:12]}"
        status, req = http("PATCH", f"{API}/api/v1/ci/{target['sys_id']}",
                           {"attributes": {"comments": marker}, "note": "Yığın duman testi",
                            "idempotency_key": f"smoke-{uuid.uuid4().hex}"})
        check(status == 202 and req["status"] == "pending", "düzenleme isteği kabul edildi (202, bekliyor)")

        def applied() -> Any:
            _s, d = http("GET", f"{API}/api/v1/ci/{target['sys_id']}")
            edit = next((e for e in d["edits"] if e["id"] == req["id"]), None)
            return edit and edit["status"] != "pending" and (edit, d)

        edit, detail = wait_for("uzlaştırıcı düzenlemeyi uyguladı", applied, timeout=90)
        check(edit["status"] == "applied", f"uzlaştırıcı isteği uyguladı ({edit['status_label']})")
        value = next(f["value"] for f in detail["editable"] if f["attribute"] == "comments")
        check(value == marker, "golden record güncellendi (Manual kaynağı uzlaştırmadan geçti)")

        step("Kafka")
        topics = set(compose("exec", "-T", "kafka", "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server",
                             "localhost:9092", "--list", capture=True).stdout.split())
        check(TOPICS <= topics, f"konular açılmış ({', '.join(sorted(TOPICS))})")

        step("OpenSearch")
        health = wait_for("OpenSearch kümesi", lambda: (r := http("GET", f"{OPENSEARCH}/_cluster/health"))[0] == 200
                          and r[1])
        check(health["status"] in {"green", "yellow"}, f"küme durumu {health['status']}")

        name, sys_id = smoke_event_backbone()
        smoke_search(name, sys_id)
    except SmokeError as exc:
        print(f"\nDuman testi BAŞARISIZ: {exc}", file=sys.stderr)
        return 1
    print("\nDuman testi geçti.")
    return 0


def _envelope(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {"specversion": "1.0", "id": str(uuid.uuid4()), "source": "urn:evren:connector:duman-testi", "time": now,
            "type": kind, "data": data}


def smoke_event_backbone() -> tuple[str, str]:
    """Bağlayıcı → alım geçidi → Kafka → tüketici → golden record → outbox → Kafka (ADR-0017)."""
    step("Olay omurgası: geçit → Kafka → uzlaştırıcı → CI olayı")
    wait_for("alım geçidi hazır (/readyz)", lambda: http("GET", f"{GATEWAY}/readyz")[0] == 200)
    print("  ✓ alım geçidi hazır (veritabanı ve Kafka erişimi)")
    tag = uuid.uuid4().hex[:10]
    issued = compose("run", "--rm", "--no-deps", "-T", "migrate", "connector", "issue", "--name", f"duman-{tag}",
                     "--data-source", "Zabbix", "--by", "duman-testi", "--expires-days", "1", capture=True)
    token = issued.stdout.strip().splitlines()[-1]  # ekrana yazılmaz
    check(token.startswith("evr_"), "geçici bağlayıcı kimliği verildi (Zabbix, 1 gün)")
    rid = str(uuid.uuid4())
    run = {"data_source": "Zabbix", "run_id": rid, "mode": "delta", "completed": False, "record_count": 0,
           "started_at": datetime.now(UTC).isoformat()}
    check(http("POST", f"{GATEWAY}/ingest/v1/runs", _envelope("evren.ingest.run.started.v1", run))[0] == 401,
          "belirteçsiz istek reddedildi (401)")
    other = {**run, "data_source": "vCenter"}
    check(http("POST", f"{GATEWAY}/ingest/v1/runs", _envelope("evren.ingest.run.started.v1", other),
               auth=token)[0] == 403, "kimlik başka kaynak adına yazamıyor (403)")
    status, _ = http("POST", f"{GATEWAY}/ingest/v1/runs", _envelope("evren.ingest.run.started.v1", run), auth=token)
    check(status == 201, "çalışma açıldı (201)")
    name = f"duman-{tag}"
    rec = {"data_source": "Zabbix", "run_id": rid, "source_key": f"smoke-{tag}", "class_hint": "cmdb_ci_linux_server",
           "attributes": {"name": name, "serial_number": f"SMK{tag.upper()}", "os": "Linux"},
           "lookup": {"mac_addresses": [f"02:00:{tag[0:2]}:{tag[2:4]}:{tag[4:6]}:{tag[6:8]}"]},
           "observed_at": datetime.now(UTC).isoformat()}
    status, body = http("POST", f"{GATEWAY}/ingest/v1/runs/{rid}/records",
                        {"records": [_envelope("evren.ingest.record.v1", rec)]}, auth=token)
    check(status == 202 and body["accepted"] == 1, "kayıt Kafka'ya teslim edildi (202)")
    done = {**run, "completed": True, "record_count": 1, "finished_at": datetime.now(UTC).isoformat()}
    status, body = http("POST", f"{GATEWAY}/ingest/v1/runs/{rid}/complete",
                        _envelope("evren.ingest.run.completed.v1", done), auth=token)
    check(status == 202 and body["state"] == "closing", "kapanış istendi (bildirilen 1 kayıt)")

    def finished() -> Any:
        _s, b = http("GET", f"{GATEWAY}/ingest/v1/runs/{rid}", auth=token)
        return b and b.get("state") == "finished" and b

    final = wait_for("çalışma kapanışı (tüketici işledi, çalışan kapattı)", finished, timeout=120)
    check(final["completed"] is True and final["processed"] == 1,
          f"çalışma tamamlandı: işlenen {final['processed']}, devre kesici {final['breaker_state']}")
    found = wait_for("CI arayüzde", lambda: (r := http("GET", f"{API}/api/v1/ci?q={name}"))[0] == 200
                     and r[1]["items"] and r[1]["items"][0])
    check(found["name"] == name, f"CI oluştu ve API'de görünüyor ({found['sys_id']})")

    def published() -> bool:
        out = compose("exec", "-T", "kafka", "/opt/kafka/bin/kafka-console-consumer.sh", "--bootstrap-server",
                      "localhost:9092", "--topic", "evren.cmdb.ci.changed.v1", "--from-beginning", "--timeout-ms",
                      "8000", check=False, capture=True).stdout
        return found["sys_id"] in out

    wait_for("CI olayı Kafka'da", published, timeout=90, interval=3)
    print("  ✓ CI olayı outbox'tan Kafka'ya yayınlandı (evren.cmdb.ci.changed.v1)")
    ingest = http("GET", f"{API}/api/v1/summary")[1]["worker"]["ingest"]
    check(ingest["state"] == "ok" and ingest["processed"] >= 1,
          f"alım tüketicisi canlı ({ingest['instances']} örnek, {ingest['processed']} kayıt)")
    smoke_connector_run(token)
    smoke_connector_runner()
    compose("run", "--rm", "--no-deps", "-T", "migrate", "connector", "revoke", "--name", f"duman-{tag}", "--by",
            "duman-testi", capture=True)
    check(http("GET", f"{GATEWAY}/ingest/v1/runs/{rid}", auth=token)[0] == 401, "iptal edilen kimlik reddediliyor")
    return name, found["sys_id"]


SAMPLES = {"zabbix": "connectors/zabbix/samples", "vcenter": "connectors/vcenter/samples",
           "servicenow": "connectors/servicenow_cmdb/samples", "bmc_discovery": "connectors/bmc_discovery/samples",
           "defender": "connectors/defender/samples", "nnmi": "connectors/nnmi/samples"}
SN_APP_SYS_ID = "5b1c2d3e4f5061728394a5b6c7d8e9f0"  # örnek ServiceNow kaydı (ist-app-02)
SN_SWITCH_SYS_ID = "9f5061728394a5b6c7d8e9f0a1b2c3d4"  # örnek ServiceNow kaydı (IST-DC2-CORE-SW01)


def _connector_run(connector: str, token: str, expected: int) -> dict[str, Any]:
    """Konteynerde ``evren connector-run --simulate``; belirteç standart girdiden, komut satırına ve ekrana yazılmaz."""
    res = compose("run", "--rm", "--no-deps", "-T", "-e", "EVREN_CONNECTOR_TOKEN_FILE=/dev/stdin", "connector",
                  "connector-run", "--connector", connector, "--name", f"duman-{connector}", "--simulate",
                  SAMPLES[connector], "--page-size", "2", "--batch-size", "4",
                  check=False, capture=True, stdin=token + "\n")
    lines = res.stdout.strip().splitlines()
    report = json.loads(lines[-1]) if lines and lines[-1].startswith("{") else {}
    check(res.returncode == 0 and report.get("completed") is True and report.get("sent") == expected,
          f"{connector} bağlayıcısı çalıştı: çekilen {report.get('fetched')}, gönderilen {report.get('sent')} "
          f"(sınıflar: {', '.join(report.get('by_class') or {})})")
    rid = report["run_id"]

    def finished() -> Any:
        _s, b = http("GET", f"{GATEWAY}/ingest/v1/runs/{rid}", auth=token)
        return b and b.get("state") == "finished" and b

    final = wait_for(f"{connector} çalışmasının kapanışı", finished, timeout=120)
    check(final["completed"] is True and final["processed"] == expected,
          f"çalışma tamamlandı: işlenen {final['processed']}, devre kesici {final['breaker_state']}")
    return report


def _find_ci(name: str) -> dict[str, Any]:
    found = wait_for(f"'{name}' API'de", lambda: (r := http("GET", f"{API}/api/v1/ci?q={name}"))[0] == 200
                     and next((i for i in r[1]["items"] if i["name"].lower() == name), None))
    return http("GET", f"{API}/api/v1/ci/{found['sys_id']}")[1]


def _issue(data_source: str) -> tuple[str, str]:
    name = f"duman-{data_source.lower()}-{uuid.uuid4().hex[:8]}"
    issued = compose("run", "--rm", "--no-deps", "-T", "migrate", "connector", "issue", "--name", name,
                     "--data-source", data_source, "--by", "duman-testi", "--expires-days", "1", capture=True)
    return name, issued.stdout.strip().splitlines()[-1]  # belirteç ekrana yazılmaz


def _revoke(name: str) -> None:
    compose("run", "--rm", "--no-deps", "-T", "migrate", "connector", "revoke", "--name", name, "--by", "duman-testi",
            capture=True)


def smoke_connector_run(token: str) -> None:
    """Bağlayıcı çalıştırıcı (U5, ADR-0020): kaynak simülatörü → eşleme → geçit → Kafka → uzlaştırıcı → kapanış.

    Sıra gerçek geçişteki gibidir: önce ServiceNow ilk yüklemesi (CI'lar SN sys_id'siyle açılır), sonra keşif ve izleme
    kaynakları bu CI'lara bağlanır. Zabbix olay omurgası adımının geçici kimliğiyle; ServiceNow ve vCenter kendi geçici
    kimlikleriyle çalışır. Kaynak verisi sentetiktir; gerçek kaynak kabulü yerine geçmez."""
    sn_name, sn_token = _issue("ServiceNow")
    vc_name, vc_token = _issue("vCenter")
    bmc_name, bmc_token = _issue("BMC")
    df_name, df_token = _issue("Defender")
    nn_name, nn_token = _issue("NNMi")
    try:
        step("Bağlayıcı çalıştırıcı: ServiceNow simülatörü (Table API) → ilk yükleme, sys_id korunur")
        _connector_run("servicenow", sn_token, 5 + 12)  # 5 CI + 12 referans kaydı (ADR-0022)
        status, ci = http("GET", f"{API}/api/v1/ci/{SN_APP_SYS_ID}")
        check(status == 200 and ci["name"] == "ist-app-02", f"CI ServiceNow sys_id'siyle açıldı ({SN_APP_SYS_ID})")
        shown = {a["attribute"]: a["display"] for a in ci.get("attributes") or []}
        check(shown.get("support_group") == "Linux Sistem Ekibi (SENTETİK)"
              and shown.get("cost_center") == "BT Altyapı Masraf Merkezi (SENTETİK)",
              f"referans verileri yüklendi: destek grubu ve maliyet merkezi adıyla görünüyor ({shown.get('support_group')})")

        step("Bağlayıcı çalıştırıcı: Zabbix simülatörü (JSON-RPC) → geçit → uzlaştırıcı")
        _connector_run("zabbix", token, 5)
        detail = _find_ci("ank-db-14")
        check(any(lk["data_source"] == "Zabbix" and lk["source_key"] == "20504" for lk in detail.get("links") or []),
              f"Zabbix host'u CMDB'de, kaynak bağı 20504 ({detail['sys_id']})")

        step("Bağlayıcı çalıştırıcı: vCenter simülatörü (VI/JSON) → ESXi, VM, misafir sunucu ve ilişkiler")
        _connector_run("vcenter", vc_token, 10)
        esx = _find_ci("esx01")
        check(esx["class"] == "cmdb_ci_esx_server", "ESXi host'u CMDB'de (cmdb_ci_esx_server)")
        rels = {(r["type"], r["direction"], r["target_name"]) for r in esx.get("relations") or []}
        check(("Registered on::Has registered", "child", "ist-app-02 (Uygulama)") in rels
              and ("Virtualized by::Virtualizes", "child", "ist-app-02") in rels,
              "ilişkiler yazıldı: VM örneği ve misafir sunucu → ESXi (nitelikli anahtar)")

        step("Bağlayıcı çalıştırıcı: BMC Helix Discovery simülatörü (data/search) → Keşif rolü")
        _connector_run("bmc_discovery", bmc_token, 3)

        step("Bağlayıcı çalıştırıcı: Defender simülatörü (Entra istemci kimliği + /api/machines) → Uç nokta güvenliği")
        _connector_run("defender", df_token, 2)
        _s, app = http("GET", f"{API}/api/v1/ci/{SN_APP_SYS_ID}")
        check({lk["data_source"] for lk in app.get("links") or []}
              >= {"ServiceNow", "Zabbix", "vCenter", "BMC", "Defender"},
              "akıl katmanı: beş kaynak ServiceNow'dan gelen CI'a bağlandı (seri, UUID, MAC; sys_id aynı)")
        posture = app.get("security_posture") or {}
        names = {a["attribute"] for a in app.get("attributes") or []}
        check(posture.get("exposure_level") == "Medium" and posture.get("data_source") == "Defender"
              and not names & {"exposure_level", "risk_score", "health_status", "protection_status"},
              "kısıtlı güvenlik duruşu yalnızca cmdb.ci_security_posture'da; Güvenlik okuyucu rolüyle görünüyor (ADR-0023)")

        step("Bağlayıcı çalıştırıcı: NNMi simülatörü (SOAP getNodes, güvenli XML) → Ağ rolü")
        _connector_run("nnmi", nn_token, 3)
        _s, sw = http("GET", f"{API}/api/v1/ci/{SN_SWITCH_SYS_ID}")
        check({lk["data_source"] for lk in sw.get("links") or []} >= {"ServiceNow", "Zabbix", "NNMi"},
              f"ağ cihazı: ServiceNow, Zabbix ve NNMi tek CI'da (seri numarası; {SN_SWITCH_SYS_ID})")
    finally:
        for name in (sn_name, vc_name, bmc_name, df_name, nn_name):
            _revoke(name)


def _zabbix_source() -> dict[str, Any]:
    status, body = http("GET", f"{API}/api/v1/sources")
    if status != 200:
        return {}
    return next((x for x in body["sources"] if x["name"] == "Zabbix"), {})


def smoke_connector_runner() -> None:
    """Bağlayıcı çalıştırıcı servisi (U5.3b, ADR-0021): API'den kimlik (belirteç bir kez) → çalıştırıcı servisi →
    zamanlanmış çalışma ve "şimdi çalıştır" → alım geçidi → uzlaştırıcı kapanışı. Kaynak simülatördür (sentetik)."""
    step("Bağlayıcı çalıştırıcı servisi: API'den kimlik verme, örnek bildirimi, zamanlama ve şimdi çalıştır")
    name = f"duman-runner-{uuid.uuid4().hex[:8]}"
    status, cred = http("POST", f"{API}/api/v1/sources/Zabbix/credentials",
                        {"name": name, "expires_days": 1, "per_minute": 10000, "idempotency_key": f"duman-{uuid.uuid4().hex}"})
    check(status == 201 and bool(cred and cred.get("token")), "kimlik API'den verildi; belirteç yanıtta bir kez")
    token = cred["token"]
    token_file = ROOT / "deploy" / "secrets" / "connector" / "zabbix-sim.token"
    token_file.parent.mkdir(mode=0o755, exist_ok=True)
    token_file.write_text(token + "\n", encoding="utf-8")
    os.chmod(token_file, 0o644)  # konteyner kullanıcısı okur (yalnızca yerel geliştirme)
    try:
        compose("--profile", "runner", "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "connector-runner")
        wait_for("çalıştırıcının örneği bildirmesi", lambda: any(
            i["name"] == "zabbix-sim" and i["available"] for i in _zabbix_source().get("connectors") or []), timeout=60)
        print("  ✓ çalıştırıcı örneği sırsız bildirdi (zabbix-sim; adres ve dosya yolu yok)")

        def finished(pred: Any) -> Any:
            return next((r for r in _zabbix_source().get("requests") or []
                         if pred(r) and r["status"] not in ("pending", "running")), None)

        sched = wait_for("zamanlanmış çalışma", lambda: finished(lambda r: r["origin"] == "schedule"), timeout=150,
                         interval=3)
        check(sched["status"] == "succeeded" and sched["summary"].get("sent") == 5,
              f"zamanlanmış çalışma tamamlandı: gönderilen {sched['summary'].get('sent')} ({sched['result_note'] or 'not yok'})")

        def accepted() -> Any:  # zamanlanmış istek yürürken 409 döner; sonraki denemede kabul edilir
            st, body = http("POST", f"{API}/api/v1/sources/Zabbix/run-requests",
                            {"instance": "zabbix-sim", "mode": "snapshot", "note": "duman testi",
                             "idempotency_key": f"duman-{uuid.uuid4().hex}"})
            return st == 202 and body

        req = wait_for("şimdi çalıştır isteği", accepted, timeout=90, interval=3)
        done = wait_for("elle istenen çalışma", lambda: finished(lambda r: r["id"] == req["id"]), timeout=120,
                        interval=2)
        check(done["status"] == "succeeded" and done["origin"] == "manual" and done["summary"].get("sent") == 5,
              f"şimdi çalıştır tamamlandı: gönderilen {done['summary'].get('sent')}, süre {done['summary'].get('duration_s')} sn")

        def closed() -> Any:
            _s, b = http("GET", f"{GATEWAY}/ingest/v1/runs/{done['run_id']}", auth=token)
            return b and b.get("state") == "finished" and b

        final = wait_for("çalışmanın uzlaştırıcıda kapanışı", closed, timeout=120)
        check(final["completed"] is True, "istek ile geçitteki çalışma aynı run_id; uzlaştırıcı tamamladı")
    finally:
        compose("--profile", "runner", "stop", "connector-runner", check=False, capture=True)
        http("POST", f"{API}/api/v1/credentials/{name}/revoke", {})
        token_file.unlink(missing_ok=True)


def smoke_search(name: str, sys_id: str) -> None:
    """Olay → dizinleyici → OpenSearch → arama API'si (yetki süzgeçli); Türkçe harf, yazım hatası, ayırıcısız yazım."""
    step("Arama: CI olayı → dizinleyici → OpenSearch → /api/v1/search")

    def found(q: str) -> Any:
        status, body = http("GET", f"{API}/api/v1/search?{urllib.parse.urlencode({'q': q})}")
        return status == 200 and body["engine"] == "opensearch" and any(i["sys_id"] == sys_id for i in body["items"])

    wait_for("yeni CI aramada (dizinleyici olayı işledi)", lambda: found(name), timeout=90)
    check(True, f"yeni CI OpenSearch'ten bulundu ({name})")
    check(found(name.upper().replace("I", "İ")), "Türkçe büyük harfle bulundu")
    check(found(name.replace("duman", "dumn")), "yazım hatasıyla bulundu (dumn-…)")
    check(found(name.replace("-", "")), "ayırıcısız yazımla bulundu")
    status, body = http("GET", f"{API}/api/v1/search?q=%C4%B0ST-DB-01")  # "İST-DB-01"
    check(status == 200 and any(i["name"] == "ist-db-01" for i in body["items"]), "demo CI 'İST-DB-01' ile bulundu")


def pinned_images() -> list[str]:
    """Dockerfile temel görüntüsü + compose'taki dış görüntüler (``ad:etiket@sha256:…``)."""
    arg = re.search(r"^ARG PYTHON_IMAGE=(\S+)$", (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8"), re.M)
    refs = compose("config", "--images", capture=True).stdout.split()
    return [*([arg.group(1)] if arg else []), *sorted({r for r in refs if not r.startswith("evren:")})]


def digests(pull: bool) -> int:
    """Sabit özetler; ``--pull`` ile etiketin bugünkü özetiyle karşılaştırır (güncelleme ayrı değişiklik olarak)."""
    differ = 0
    for ref in pinned_images():
        tag, _, pinned = ref.partition("@")
        if not pull:
            print(f"{tag}: {pinned or 'SABİT DEĞİL'}")
            differ += not pinned
            continue
        subprocess.run(["docker", "pull", "-q", tag], check=False, capture_output=True)  # nosec B603 B607
        res = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", tag],  # nosec B603 B607
                             text=True, capture_output=True, check=False)
        current = next((d.split("@", 1)[1] for d in json.loads(res.stdout or "[]") if "@" in d), "?")
        same = current == pinned
        differ += not same
        print(f"{tag}: sabit {pinned or '-'} | güncel {current} {'(aynı)' if same else '(FARKLI)'}")
    return 0 if not pull else min(differ, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up", help="yığını kur ve başlat")
    p_up.add_argument("--demo", action="store_true", help="boş veritabanına sentetik demo verisi yükle")
    sub.add_parser("smoke", help="uçtan uca duman testi")
    sub.add_parser("status", help="servis durumları")
    p_logs = sub.add_parser("logs", help="servis günlükleri")
    p_logs.add_argument("service", nargs="?")
    p_dig = sub.add_parser("digests", help="dış görüntü özetleri")
    p_dig.add_argument("--pull", action="store_true", help="etiketleri yeniden çek (yeni özet)")
    p_down = sub.add_parser("down", help="yığını durdur")
    p_down.add_argument("--volumes", action="store_true", help="veri birimlerini de sil (geri alınamaz)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "up":
            return up(args.demo)
        if args.cmd == "smoke":
            return smoke()
        if args.cmd == "status":
            return compose("ps", "-a").returncode
        if args.cmd == "logs":
            return compose("logs", "--no-color", "--tail", "300", *([args.service] if args.service else [])).returncode
        if args.cmd == "digests":
            return digests(args.pull)
        return compose("down", "--remove-orphans", *(["--volumes"] if args.volumes else [])).returncode
    except subprocess.CalledProcessError as exc:
        print(f"Adım başarısız (çıkış kodu {exc.returncode}): {' '.join(exc.cmd[len(COMPOSE):])}", file=sys.stderr)
        return exc.returncode or 1
    except FileNotFoundError:
        print("docker bulunamadı: Docker Engine ve compose eklentisi gerekir.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

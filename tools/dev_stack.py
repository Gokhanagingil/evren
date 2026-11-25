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
kimliği → alım geçidi → Kafka → tüketici → CI → outbox → Kafka CI olayı → kimlik iptali); arama (dizinleyici →
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


def compose(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*COMPOSE, *args], check=check, text=True, cwd=ROOT,  # nosec B603
                          capture_output=capture)


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
    compose("run", "--rm", "--no-deps", "-T", "migrate", "connector", "revoke", "--name", f"duman-{tag}", "--by",
            "duman-testi", capture=True)
    check(http("GET", f"{GATEWAY}/ingest/v1/runs/{rid}", auth=token)[0] == 401, "iptal edilen kimlik reddediliyor")
    return name, found["sys_id"]


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

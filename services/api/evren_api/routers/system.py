"""Sistem durumu (U8.1, ADR-0032): veritabanı, göçler, Kafka, OpenSearch, servis nabızları, olay yayını, kuyruklar ve
kaynak çalışmaları tek yanıtta.

Her bileşen ``ok`` (çalışıyor), ``warn`` (dikkat), ``bad`` (sorun var) veya ``off`` (bu kurulumda yok) durumundadır ve
durumun gerekçesi Türkçe yazılır. Kafka'ya API bağlanmaz: Kafka'nın durumu onu kullanan servislerin nabız
ayrıntısından (alım geçidi, alım tüketicisi, dizinleyici) okunur. Yalnızca ``platform`` okuma izni olan görür: yanıt iç
ayrıntı taşır (sürümler, bağlantı sayıları, kuyruklar); sır veya bağlantı bilgisi taşımaz.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from evren_core.opensearch_client import OpenSearchError
from evren_core.search import ALIAS

from ..config import REPO_ROOT
from ..deps import Ctx, context

router = APIRouter(prefix="/api/v1", tags=["system"])

STATE_TR = {"ok": "Çalışıyor", "warn": "Dikkat", "bad": "Sorun var", "off": "Bu kurulumda yok"}
SEVERITY = {"off": 0, "ok": 1, "warn": 2, "bad": 3}
#: Servis türü → (etiket, işlenen sayının adı, başarısızın adı)
SERVICES = {
    "requests": ("Uzlaştırıcı çalışanı", "Uygulanan istek", "Başarısız istek"),
    "ingest": ("Alım tüketicisi (Kafka → uzlaştırıcı)", "İşlenen kayıt", "DLQ'ya giden kayıt"),
    "indexer": ("Arama dizinleyicisi", "Dizinlenen CI", "Başarısız toplu yazım"),
    "connector": ("Bağlayıcı çalıştırıcı", "Tamamlanan çalışma", "Başarısız çalışma"),
    "gateway": ("Alım geçidi", "Kabul edilen kayıt", "Kafka teslim hatası"),
    "governance": ("Görev çalışanı (görevler, uyarılar, bildirimler)", "Teslim edilen görev", "Teslim hatası"),
}
#: Kafka tüketici gecikmesi bu sayıyı aşarsa dikkat (kayıt)
LAG_WARN = 10_000
#: Yayımlanmamış en eski olayın yaşı (dakika): dikkat / sorun
OUTBOX_WARN_MIN, OUTBOX_BAD_MIN = 5, 60


def _component(cid: str, label: str, state: str, summary: str, items: list[tuple[str, Any]] | None = None,
               note: str | None = None) -> dict[str, Any]:
    return {"id": cid, "label": label, "state": state, "state_label": STATE_TR[state], "summary": summary,
            "items": [{"label": k, "value": v} for k, v in (items or []) if v is not None], "note": note}


def _ago(ts: datetime | None, now: datetime) -> str | None:
    if ts is None:
        return None
    s = max(int((now - ts).total_seconds()), 0)
    if s < 90:
        return f"{s} sn önce"
    if s < 5400:
        return f"{s // 60} dk önce"
    if s < 172800:
        return f"{s // 3600} sa önce"
    return f"{s // 86400} gün önce"


def _num(n: int | float) -> str:
    return f"{int(n):,}".replace(",", ".")


def _database(cur: Any, now: datetime) -> dict[str, Any]:
    cur.execute("""SELECT split_part(current_setting('server_version'), ' ', 1), pg_database_size(current_database()),
                          (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()),
                          current_setting('max_connections')::int, pg_postmaster_start_time()""")
    version, size, conns, max_conns, started = cur.fetchone()
    ratio = conns / max_conns if max_conns else 0
    state = "warn" if ratio >= 0.8 else "ok"
    return _component("database", "Veritabanı (PostgreSQL)", state,
                      f"PostgreSQL {version} · {size / 1048576:.0f} MB · {conns}/{max_conns} bağlantı",
                      [("Sürüm", version), ("Boyut", f"{size / 1048576:.1f} MB"), ("Bağlantı", f"{conns} / {max_conns}"),
                       ("Çalışma süresi", _ago(started, now))],
                      "Bağlantıların %80'i dolu: havuz boyutlarını veya max_connections'ı gözden geçirin."
                      if state == "warn" else None)


def _migrations(cur: Any, migrations_dir: Any) -> dict[str, Any]:
    cur.execute("SELECT version, sha256, applied_at FROM meta.schema_migration ORDER BY version")
    ledger = {v: (sha, at) for v, sha, at in cur.fetchall()}
    files = {p.stem: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))} if migrations_dir.is_dir() else {}
    last = max(ledger) if ledger else None
    items = [("Uygulanan", len(ledger)), ("Depodaki göç", len(files) or None), ("Son göç", last),
             ("Son uygulama", ledger[last][1].astimezone(UTC).strftime("%d.%m.%Y %H:%M") if last else None)]
    if not ledger:
        return _component("migrations", "Göçler", "warn", "Göç defteri boş", items,
                          "Göçler göç aracı dışında uygulanmış (ör. psql ile); hangi göçün uygulandığı izlenmiyor. "
                          "Kurulumlarda python tools/migrate.py kullanın.")
    changed = sorted(v for v, (sha, _) in ledger.items() if v in files and files[v] != sha)
    pending = sorted(set(files) - set(ledger))
    if changed:
        return _component("migrations", "Göçler", "bad", f"Uygulandıktan sonra değişmiş göç: {', '.join(changed)}",
                          items, "Uygulanmış bir göç dosyası değiştirilmiş; göç aracı ilerlemeyi durdurur.")
    if pending:
        return _component("migrations", "Göçler", "warn", f"Bekleyen göç: {', '.join(pending)}", items,
                          "Yeni sürümün göçleri henüz uygulanmamış: python tools/migrate.py")
    return _component("migrations", "Göçler", "ok", f"{len(ledger)} göç uygulanmış, bekleyen yok", items)


def _heartbeats(cur: Any) -> dict[str, list[dict[str, Any]]]:
    """Son bir günde görülen örnekler (yeniden başlayan örnek yeni kimlikle gelir; eskiler gürültü olmasın)."""
    cur.execute("""SELECT kind, worker_id, started_at, seen_at, stopped_at, processed, failed, last_error_at,
                          poll_seconds, details,
                          stopped_at IS NULL AND seen_at > now() - greatest(2 * poll_seconds, 60) * interval '1 second'
                     FROM ingest.worker_heartbeat
                    WHERE seen_at > now() - interval '1 day'
                       OR worker_id IN (SELECT DISTINCT ON (kind) worker_id FROM ingest.worker_heartbeat
                                         ORDER BY kind, seen_at DESC)
                    ORDER BY kind, seen_at DESC""")
    out: dict[str, list[dict[str, Any]]] = {}
    for kind, wid, started, seen, stopped, processed, failed, err_at, poll, details, live in cur.fetchall():
        out.setdefault(kind, []).append({"worker_id": wid, "started": started, "seen": seen, "stopped": stopped,
                                         "processed": processed, "failed": failed, "error_at": err_at, "poll": poll,
                                         "details": details or {}, "live": live})
    return out


def _service(kind: str, rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    label, done_label, fail_label = SERVICES[kind]
    live = [r for r in rows if r["live"]]
    if not rows:
        return _component(f"svc_{kind}", label, "off", "Hiç çalışmadı",
                          note="Bu kurulumda bu servis yok veya henüz başlatılmadı.")
    latest = rows[0]
    items = [("Canlı örnek", len(live)), ("Son görülme", _ago(latest["seen"], now)),
             (done_label, _num(sum(r["processed"] for r in live or [latest]))),
             (fail_label, _num(sum(r["failed"] for r in live or [latest])))]
    kafka = [r["details"].get("kafka") for r in live if isinstance(r["details"].get("kafka"), dict)]
    lag = sum(int(k.get("lag") or 0) for k in kafka if k.get("ok"))
    if kafka and any(k.get("ok") for k in kafka) and kind in ("ingest", "indexer"):
        items.append(("Kafka gecikmesi", f"{_num(lag)} kayıt"))
    if kind == "governance" and live:
        d = live[0]["details"]
        items += [("Kanal", {"kapalı": "ServiceNow ayarı yok (görevler Evren'de bekler)",
                             "simülatör": "simülatör (geliştirme)"}.get(d.get("channel"), "ServiceNow")),
                  ("Teslim bekleyen", _num(int(d.get("pending") or 0))),
                  ("Teslim edilemeyen", _num(int(d.get("failed") or 0)))]
        n = d.get("notify") or {}
        if n:
            items += [("Bildirim: e-posta / Teams", f"{n.get('email', '—')} / {n.get('teams', '—')}"),
                      ("Etkin uyarı", _num(int(n.get("alerts_firing") or 0)))]
    if kind == "requests":
        items.append(("Olay yayıncısı", "açık" if any(r["details"].get("outbox_publisher") for r in live) else
                      "kapalı (Kafka yapılandırılmamış)"))
    if live:
        state, summary, note = "ok", f"{len(live)} örnek çalışıyor", None
        if kind == "ingest" and sum(r["failed"] for r in live):
            state, note = "warn", "DLQ'ya kayıt düştü: alım tüketicisinin işleyemediği kayıtlar DLQ konusunda bekliyor."
        if kafka and not all(k.get("ok") for k in kafka):
            state, note = "bad", "Kafka'ya ulaşılamıyor."
        elif kind == "governance" and int(live[0]["details"].get("failed") or 0):
            state, note = "warn", "ServiceNow'a teslim edilemeyen görev var: kanal ayarını ve bağlantıyı denetleyin."
        elif kind in ("ingest", "indexer") and lag > LAG_WARN:
            state, note = "warn", f"Tüketici gecikmesi yüksek ({_num(lag)} kayıt): örnek sayısını artırın."
        return _component(f"svc_{kind}", label, state, summary, items, note)
    if latest["stopped"] is not None:
        return _component(f"svc_{kind}", label, "warn", f"Durduruldu ({_ago(latest['stopped'], now)})", items,
                          "Servis düzgün kapatıldı ve yeniden başlatılmadı.")
    return _component(f"svc_{kind}", label, "bad", f"Yanıt vermiyor (son görülme {_ago(latest['seen'], now)})",
                      items, "Nabız beklenen sürede gelmedi: servis çökmüş veya veritabanına ulaşamıyor olabilir.")


def _kafka(beats: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Kafka'nın durumu, onu kullanan canlı servislerin bildirdiğinden (API Kafka'ya bağlanmaz)."""
    reports = [(SERVICES[k][0], r["details"]["kafka"]) for k in ("gateway", "ingest", "indexer")
               for r in beats.get(k, []) if r["live"] and isinstance(r["details"].get("kafka"), dict)]
    if not reports:
        return _component("kafka", "Kafka (olay omurgası)", "off", "Kafka kullanan çalışan servis yok",
                          note="Kafka yapılandırılmamış (geliştirme) veya alım geçidi, alım tüketicisi ve dizinleyici "
                               "çalışmıyor.")
    down = sorted({name for name, k in reports if not k.get("ok")})
    items = [("Bildiren servis", ", ".join(sorted({n for n, _ in reports})))]
    if down:
        return _component("kafka", "Kafka (olay omurgası)", "bad", f"Ulaşılamıyor: {', '.join(down)}", items,
                          "Bu servisler Kafka'ya bağlanamıyor; kayıtlar alınamaz veya işlenemez.")
    return _component("kafka", "Kafka (olay omurgası)", "ok", f"{len(reports)} servis bağlı", items)


def _opensearch(ctx: Ctx, cur: Any) -> dict[str, Any]:
    client = ctx.state.search
    label = "OpenSearch (arama)"
    if client is None:
        return _component("opensearch", label, "off", "Yapılandırılmamış",
                          note="Küresel arama PostgreSQL yedeğiyle çalışıyor (ADR-0018).")
    try:
        health = client.request("GET", "/_cluster/health")
        docs = int(client.request("GET", f"/{ALIAS}/_count").get("count", 0))
    except OpenSearchError as exc:
        return _component("opensearch", label, "bad", "Ulaşılamıyor",
                          note=f"Arama PostgreSQL yedeğine düşer. Ayrıntı: HTTP {exc.status or '—'}.")
    cur.execute("SELECT count(*) FROM cmdb.cmdb_ci")
    cis = cur.fetchone()[0]
    status = str(health.get("status", "red"))
    drift = abs(docs - cis)
    items = [("Küme", f"{status} · {health.get('number_of_nodes', '—')} düğüm"), ("Dizindeki CI", _num(docs)),
             ("Veritabanındaki CI", _num(cis))]
    if status == "red":
        return _component("opensearch", label, "bad", "Küme kırmızı: bazı parçalar kullanılamıyor", items)
    if drift > max(10, cis // 100):
        return _component("opensearch", label, "warn", f"Dizin {_num(drift)} CI geride", items,
                          "Dizinleyici yetişemiyor veya çalışmıyor; gerekirse tam yeniden dizinleme: "
                          "python -m evren_indexer --reindex")
    if status == "yellow":
        return _component("opensearch", label, "warn", "Küme sarı: kopya parçalar atanmamış", items,
                          "Tek düğümlü kurulumda olağan; üretimde düğüm eksikliğine işaret eder.")
    return _component("opensearch", label, "ok", f"Küme yeşil, dizin güncel ({_num(docs)} CI)", items)


def _outbox(cur: Any, beats: dict[str, list[dict[str, Any]]], now: datetime) -> dict[str, Any]:
    cur.execute("SELECT count(*), min(created_at) FROM events.outbox WHERE published_at IS NULL")
    waiting, oldest = cur.fetchone()
    publisher = any(r["live"] and r["details"].get("outbox_publisher") for r in beats.get("requests", []))
    label = "Olay yayını (outbox)"
    items = [("Bekleyen olay", _num(waiting)), ("En eski", _ago(oldest, now))]
    if not publisher:
        return _component(label=label, cid="outbox", state="off",
                          summary=f"Yayıncı çalışmıyor; {_num(waiting)} olay bekliyor", items=items,
                          note="Olaylar veritabanında kaybolmadan bekler; Kafka yapılandırılıp uzlaştırıcı çalışınca "
                               "yayımlanır. Arama dizini ve abonelikler o zamana kadar geride kalır.")
    age_min = (now - oldest).total_seconds() / 60 if oldest else 0
    state = "bad" if age_min >= OUTBOX_BAD_MIN else "warn" if age_min >= OUTBOX_WARN_MIN else "ok"
    summary = "Bekleyen olay yok" if not waiting else f"{_num(waiting)} olay bekliyor (en eskisi {_ago(oldest, now)})"
    return _component("outbox", label, state, summary, items,
                      "Olaylar yayımlanamıyor: Kafka'yı ve uzlaştırıcıyı denetleyin." if state != "ok" else None)


def _queues(cur: Any, beats: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    cur.execute("""SELECT (SELECT count(*) FROM ingest.match_candidate
                            WHERE applied_at IS NULL AND status IN ('accepted', 'created_new', 'rejected')),
                          (SELECT count(*) FROM ingest.manual_edit WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.manual_create WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.manual_relation WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.split_request WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.attestation_request WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.retirement_request WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.connector_run_request WHERE status IN ('pending', 'running')),
                          (SELECT count(*) FROM ingest.match_candidate WHERE status = 'open'),
                          (SELECT count(*) FROM ingest.match_candidate WHERE apply_error IS NOT NULL)""")
    decisions, edits, creates, rels, splits, attests, retires, runs, review, failed = cur.fetchone()
    pending = decisions + edits + creates + rels + splits + attests + retires
    items = [("Uygulanmayı bekleyen insan kararı", _num(pending)), ("Eşleştirme kuyruğunda inceleme bekleyen", _num(review)),
             ("Sırada/çalışan bağlayıcı isteği", _num(runs)), ("Uygulanamayan kuyruk kararı", _num(failed))]
    if failed:
        return _component("queues", "İstek kuyrukları", "warn", f"{_num(failed)} karar uygulanamadı", items,
                          "Eşleştirme kuyruğunda hata kimliğiyle görünür; karar yeniden verilebilir.")
    if pending and not any(r["live"] for r in beats.get("requests", [])):
        return _component("queues", "İstek kuyrukları", "warn",
                          f"{_num(pending)} istek bekliyor, uzlaştırıcı çalışmıyor", items,
                          "İstekler kaybolmaz; uzlaştırıcı çalışanı başlayınca sırayla uygulanır.")
    return _component("queues", "İstek kuyrukları", "ok",
                      f"{_num(pending)} istek uygulanmayı bekliyor" if pending else "Bekleyen istek yok", items)


def _sources(cur: Any, now: datetime) -> dict[str, Any]:
    cur.execute("""SELECT d.name, d.label_tr, r.finished_at, r.breaker_state, r.completed, r.mode,
                          (SELECT count(*) FROM ingest.source_run o WHERE o.data_source = d.name AND o.finished_at IS NULL
                             AND o.started_at < now() - interval '1 day')
                     FROM ingest.data_source d
                     LEFT JOIN LATERAL (SELECT finished_at, breaker_state, completed, mode FROM ingest.source_run s
                                         WHERE s.data_source = d.name AND s.finished_at IS NOT NULL
                                         ORDER BY s.finished_at DESC LIMIT 1) r ON true
                    WHERE d.lifecycle IN ('active', 'shadow', 'retiring') AND d.name NOT IN ('Manual', 'Evren')
                    ORDER BY d.name""")
    rows = cur.fetchall()
    items, opened, stuck = [], [], []
    for name, label, finished, breaker, completed, mode, abandoned in rows:
        if finished is None:
            items.append((label or name, "henüz çalışmadı"))
            continue
        state_tr = {"ok": "sağlıklı", "warn": "dikkat", "open": "devre kesici açık"}.get(breaker or "", breaker or "—")
        items.append((label or name, f"{_ago(finished, now)} · {state_tr}{'' if completed else ' · eksik'}"
                                      f"{' · delta' if mode == 'delta' else ''}"))
        if breaker == "open":
            opened.append(label or name)
        if abandoned:
            stuck.append(label or name)
    if stuck:
        return _component("sources", "Kaynak çalışmaları", "warn", f"Bir günden uzun süredir açık çalışma: {', '.join(stuck)}",
                          items, "Uzlaştırıcı terk edilmiş çalışmayı eksik olarak kapatır; bağlayıcıyı denetleyin.")
    if opened:
        return _component("sources", "Kaynak çalışmaları", "warn", f"Devre kesici açık: {', '.join(opened)}", items,
                          "Bu kaynakların son çalışmasında eskime, emekliye ayırma ve ilişki kaldırma durduruldu "
                          "(kural 4). Ayrıntı Veri Kaynakları sayfasında.")
    return _component("sources", "Kaynak çalışmaları", "ok",
                      f"{len(rows)} kaynak; son çalışmalar sağlıklı" if rows else "Etkin kaynak yok", items)


@router.get("/system/status")
def system_status(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Platformun bütün parçalarının durumu (yalnızca ``platform`` okuma izniyle)."""
    ctx.require("platform", "read")
    settings = ctx.state.settings
    migrations_dir = REPO_ROOT / "db" / "migrations"
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT now()")
        now = cur.fetchone()[0]
        beats = _heartbeats(cur)
        components = [
            _component("api", "Evren API", "ok", "Bu yanıtı veren örnek",
                       [("Kimlik doğrulama", "Entra ID" if settings.auth_mode == "entra" else "geliştirme (dev)")]),
            _database(cur, now),
            _migrations(cur, migrations_dir),
            _kafka(beats),
            _opensearch(ctx, cur),
            *[_service(kind, beats.get(kind, []), now) for kind in SERVICES],
            _outbox(cur, beats, now),
            _queues(cur, beats),
            _sources(cur, now),
        ]
    worst = max((c["state"] for c in components), key=lambda s: SEVERITY[s])
    overall = worst if worst != "off" else "ok"
    counts = {s: sum(1 for c in components if c["state"] == s) for s in STATE_TR}
    return {"checked_at": now.isoformat(), "overall": overall, "overall_label": STATE_TR[overall], "counts": counts,
            "components": components, "stale_after": str(timedelta(seconds=60))}

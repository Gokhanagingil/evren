"""Platform uyarıları (U8.9, ADR-0040; F1-15): kurallar, sağlık anlık görüntüsü, değerlendirme, yaşam döngüsü.

Görev çalışanı her turda platformun anlık görüntüsünü (servis nabızları, Kafka, olay yayını, çalıştırma istekleri,
devre kesiciler, denetim mühürleri) alır; kurallar (``metadata/alerts.yaml``) koşulları üretir. Koşul kuralın
``for_minutes`` süresi boyunca sürerse uyarı **ateşlenir**, koşul kalkınca **çözülür**. Süre dolmadan kalkan koşul
kimseyi rahatsız etmez (``pending`` → ``resolved``, ateşlenmeden).

Kurallar ürüne değil sinyale yazılır (kural 8): konu (``subject``) servis türü veya veri kaynağı adıdır, kuralın
metninde ürün adı yoktur. Saf modüldür: SQL çalıştırmaz.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

__all__ = ["SERVICE_TR", "SEVERITIES", "SIGNALS", "AlertRule", "Beat", "Condition", "OpsSnapshot", "evaluate",
           "load_rules", "next_state", "validate_alerts"]

SEVERITIES = ("info", "warning", "critical")
SEVERITY_TR = {"info": "Bilgi", "warning": "Uyarı", "critical": "Kritik"}
SERVICE_TR = {
    "requests": "Uzlaştırıcı çalışanı", "ingest": "Alım tüketicisi", "indexer": "Arama dizinleyicisi",
    "connector": "Bağlayıcı çalıştırıcı", "gateway": "Alım geçidi", "governance": "Görev çalışanı",
    "sn_sync": "ServiceNow replikası",
}
#: Sinyal → (Türkçe açıklama, eşiğin birimi). Eşiksiz sinyalde birim None.
SIGNALS: dict[str, tuple[str, str | None]] = {
    "service_down": ("Servis nabzı beklenen sürede gelmiyor", None),
    "kafka_unreachable": ("Servis Kafka'ya ulaşamıyor", None),
    "kafka_lag": ("Kafka tüketici gecikmesi", "kayıt"),
    "outbox_backlog": ("Yayımlanmamış en eski olayın yaşı", "dakika"),
    "breaker_open": ("Veri kaynağının son çalışmasında devre kesici açık", None),
    "run_request_stuck": ("Bağlayıcı çalıştırma isteği alınmıyor", "dakika"),
    "task_delivery_failed": ("Görev kanala teslim edilemiyor", "görev"),
    "audit_seal_lag": ("Denetim izi mühürlenmiyor", "dakika"),
    "audit_seal_failed": ("Denetim izi mührü doğrulanamadı (son 24 saat)", None),
}
_ROLE = re.compile(r"^evren_[a-z_]{2,40}$")
_ID = re.compile(r"^[a-z][a-z0-9_]{2,60}$")


@dataclass(frozen=True)
class AlertRule:
    id: str
    signal: str
    title: str                 # Türkçe; {subject} ve {value} yer tutucuları
    severity: str
    for_minutes: int
    audience: tuple[str, ...]  # bildirim alacak roller
    threshold: float = 0
    kinds: tuple[str, ...] = ()  # service_down/kafka_*: yalnızca bu servis türleri (boş: hepsi)
    advice: str = ""             # ne yapılmalı (Türkçe, kısa)

    def render(self, subject: str, value: float | None) -> str:
        label = SERVICE_TR.get(subject, subject)
        shown = "" if value is None else (f"{value:,.0f}".replace(",", ".") if value >= 100 else f"{value:g}")
        return self.title.replace("{subject}", label).replace("{value}", shown).strip()


@dataclass(frozen=True)
class Beat:
    kind: str
    worker_id: str
    seen: datetime
    poll_seconds: int
    stopped: datetime | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def live(self, now: datetime, grace: float = 3.0) -> bool:
        return self.stopped is None and now - self.seen <= timedelta(seconds=max(grace * self.poll_seconds, 120))


@dataclass(frozen=True)
class OpsSnapshot:
    now: datetime
    beats: Sequence[Beat] = ()
    outbox_backlog: int = 0
    outbox_oldest: datetime | None = None
    requests_waiting: Sequence[datetime] = ()  # bekleyen çalıştırma isteklerinin istek zamanı
    breakers_open: Sequence[str] = ()          # son tamamlanmış çalışmasında devre kesici açık kaynaklar
    seal_last_end: datetime | None = None
    seal_failed_24h: int = 0


@dataclass(frozen=True)
class Condition:
    rule: AlertRule
    subject: str
    value: float | None
    detail: str

    @property
    def key(self) -> tuple[str, str]:
        return self.rule.id, self.subject

    @property
    def title(self) -> str:
        return self.rule.render(self.subject, self.value)


# ------------------------------------------------------------------------------------------------- kurallar
def load_rules(doc: Mapping[str, Any] | None) -> list[AlertRule]:
    """``alerts.yaml`` belgesinden kurallar (doğrulanmış olduğu varsayılır; geçersiz kural atlanmaz, hata verir)."""
    errors = validate_alerts(doc)
    if errors:
        raise ValueError("; ".join(errors))
    out = []
    for r in (doc or {}).get("rules") or []:
        out.append(AlertRule(
            id=r["id"], signal=r["signal"], title=r["title"], severity=r["severity"],
            for_minutes=int(r.get("for_minutes", 0)), audience=tuple(r.get("audience") or ()),
            threshold=float(r.get("threshold", 0)), kinds=tuple(r.get("kinds") or ()), advice=r.get("advice", "")))
    return out


def validate_alerts(doc: Mapping[str, Any] | None, *, roles: Iterable[str] = (), products: Sequence[str] = ()) -> list[str]:
    """Kural dosyasını doğrular: bilinen sinyal, önem, süre, eşik, roller, servis türleri, ürün adı yok (kural 8)."""
    if not doc:
        return []
    errors: list[str] = []
    known_roles = set(roles)
    seen: set[str] = set()
    for i, r in enumerate(doc.get("rules") or []):
        where = f"alerts.rules[{i}]"
        if not isinstance(r, Mapping):
            errors.append(f"{where}: kural sözlük olmalı")
            continue
        rid = str(r.get("id", ""))
        where = f"alerts.{rid or i}"
        if not _ID.match(rid):
            errors.append(f"{where}: kimlik küçük harf, rakam ve _ (3–61 karakter)")
        if rid in seen:
            errors.append(f"{where}: kimlik tekrarlanıyor")
        seen.add(rid)
        if r.get("signal") not in SIGNALS:
            errors.append(f"{where}: bilinmeyen sinyal {r.get('signal')!r} (bilinenler: {', '.join(SIGNALS)})")
        elif SIGNALS[r["signal"]][1] is not None and not isinstance(r.get("threshold"), int | float):
            errors.append(f"{where}: eşik (threshold) sayı olmalı ({SIGNALS[r['signal']][1]})")
        if r.get("severity") not in SEVERITIES:
            errors.append(f"{where}: önem info, warning veya critical olmalı")
        fm = r.get("for_minutes", 0)
        if not isinstance(fm, int) or not 0 <= fm <= 1440:
            errors.append(f"{where}: for_minutes 0–1440 tam sayı olmalı")
        title = r.get("title")
        if not isinstance(title, str) or not 3 <= len(title) <= 160:
            errors.append(f"{where}: başlık 3–160 karakter olmalı")
        aud = r.get("audience") or []
        if not aud or not all(isinstance(a, str) and _ROLE.match(a) for a in aud):
            errors.append(f"{where}: audience en az bir rol (evren_…) olmalı")
        elif known_roles:
            errors += [f"{where}: tanımsız rol {a}" for a in aud if a not in known_roles]
        errors += [f"{where}: bilinmeyen servis türü {k}" for k in r.get("kinds") or [] if k not in SERVICE_TR]
        text = " ".join(str(v) for v in (r.get("title"), r.get("advice")) if v).lower()
        errors += [f"{where}: ürün adı içeremez ({p})" for p in products
                   if p and re.search(rf"(?<![\w]){re.escape(p.lower())}(?![\w])", text)]
    return errors


# ------------------------------------------------------------------------------------------------- değerlendirme
def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60, 1)


def evaluate(snap: OpsSnapshot, rules: Sequence[AlertRule]) -> list[Condition]:
    """Anlık görüntüde şu an doğru olan koşullar (süre koşulu ``next_state``'te uygulanır)."""
    now = snap.now
    by_kind: dict[str, list[Beat]] = {}
    for b in sorted(snap.beats, key=lambda b: b.seen, reverse=True):
        by_kind.setdefault(b.kind, []).append(b)
    out: list[Condition] = []
    for rule in rules:
        kinds = [k for k in by_kind if not rule.kinds or k in rule.kinds]
        if rule.signal == "service_down":
            for k in kinds:
                rows = by_kind[k]
                if any(b.live(now) for b in rows) or rows[0].stopped is not None:
                    continue  # canlı örnek var veya düzgün kapatıldı (bakım)
                out.append(Condition(rule, k, _minutes(now - rows[0].seen),
                                     f"Son nabız {_minutes(now - rows[0].seen):g} dakika önce."))
        elif rule.signal == "kafka_unreachable":
            for k in kinds:
                bad = [b for b in by_kind[k] if b.live(now) and isinstance(b.details.get("kafka"), Mapping)
                       and not b.details["kafka"].get("ok")]
                if bad:
                    out.append(Condition(rule, k, len(bad), f"{len(bad)} örnek Kafka'ya bağlanamıyor."))
        elif rule.signal == "kafka_lag":
            for k in kinds:
                lag = sum(int(b.details["kafka"].get("lag") or 0) for b in by_kind[k]
                          if b.live(now) and isinstance(b.details.get("kafka"), Mapping) and b.details["kafka"].get("ok"))
                if lag > rule.threshold:
                    out.append(Condition(rule, k, lag, f"Gecikme {lag} kayıt (eşik {rule.threshold:g})."))
        elif rule.signal == "outbox_backlog":
            publisher = any(b.live(now) and b.details.get("outbox_publisher") for b in by_kind.get("requests", []))
            if publisher and snap.outbox_oldest is not None:
                age = _minutes(now - snap.outbox_oldest)
                if age > rule.threshold:
                    out.append(Condition(rule, "", age, f"{snap.outbox_backlog} olay bekliyor; en eskisi {age:g} dakika."))
        elif rule.signal == "breaker_open":
            out += [Condition(rule, s, None, "Son çalışma eksik veya şüpheli; yıkıcı işlemler durduruldu.")
                    for s in sorted(set(snap.breakers_open))]
        elif rule.signal == "run_request_stuck":
            old = [t for t in snap.requests_waiting if _minutes(now - t) > rule.threshold]
            if old:
                age = _minutes(now - min(old))
                out.append(Condition(rule, "", age, f"{len(old)} istek bekliyor; en eskisi {age:g} dakika."))
        elif rule.signal == "task_delivery_failed":
            live = [b for b in by_kind.get("governance", []) if b.live(now)]
            failed = int(live[0].details.get("failed") or 0) if live else 0
            if failed > rule.threshold:
                out.append(Condition(rule, "", failed, f"{failed} görev kanala teslim edilemedi."))
        elif rule.signal == "audit_seal_lag":
            if snap.seal_last_end is not None:
                age = _minutes(now - snap.seal_last_end)
                if age > rule.threshold:
                    out.append(Condition(rule, "", age, f"Son mühürlenen dönem {age:g} dakika önce bitti."))
        elif rule.signal == "audit_seal_failed" and snap.seal_failed_24h > 0:
            out.append(Condition(rule, "", snap.seal_failed_24h,
                                 f"{snap.seal_failed_24h} doğrulama kayıtların mühürden sonra değiştiğini gösteriyor."))
    return out


def next_state(state: str | None, present: bool, pending_since: datetime | None, for_minutes: int,
               now: datetime) -> str | None:
    """Uyarının yeni durumu. ``state`` None: henüz uyarı yok. Dönüş None: değişiklik yok / uyarı açılmaz.

    yok + koşul → pending (süre 0 ise doğrudan firing) · pending + koşul + süre doldu → firing ·
    pending/firing + koşul yok → resolved."""
    if state is None:
        if not present:
            return None
        return "firing" if for_minutes <= 0 else "pending"
    if state == "pending":
        if not present:
            return "resolved"
        if pending_since is not None and now - pending_since >= timedelta(minutes=for_minutes):
            return "firing"
        return None
    if state == "firing":
        return None if present else "resolved"
    return None

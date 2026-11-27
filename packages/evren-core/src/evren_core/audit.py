"""Denetim izi (U8.8, ADR-0039; F1-16): olay kataloğu ve değiştirilemezlik mühürleri.

Denetim izi üç akıştan oluşur ve ileride süreç uygulamasının (olay, değişiklik, talep) olaylarıyla genişler:

* ``security`` — güvenlik ve yönetim olayları (``audit.security_event``): kim, ne, hangi nesne, izin/ret, kaynak IP,
  istek kimliği.
* ``ci``       — CI öznitelik değişiklikleri (``audit.ci_change``): eski/yeni değer, kaynak, neden.
* ``task``     — görev olayları (``gov.task_event``).

**Mühür:** her akış saatlik dönemlere bölünür; dönemin kayıtları sabit sırayla (kimlik) ve sabit biçimle (anahtarları
sıralı JSON) satır satır yazılır, SHA-256 özeti alınır. Mühür bir önceki mührün zincir değerini de içerir
(``chain = sha256(önceki_zincir ":" özet)``); böylece bir kaydın değiştirilmesi, silinmesi, sonradan eklenmesi veya
bütün bir dönemin kaldırılması doğrulamada görünür. Boş dönem de mühürlenir. Mühürler olay omurgasıyla kurum SIEM'ine
gider; veritabanı dışında bir tanık olur.

Saf modüldür: SQL metinlerini ve hesabı tanımlar, çalıştırmaz.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

__all__ = ["ACTIONS", "CATEGORIES", "FIRST_SQL", "GENESIS", "GRACE", "SEAL_SQL", "STREAMS", "WINDOW", "SealRecord",
           "action_info", "canonical_line", "category_actions", "chain_of", "check_seal", "digest_of", "floor_hour",
           "safe_cell", "seal_windows", "verify_chain"]

STREAMS = {"security": "Güvenlik ve yönetim", "ci": "CI değişiklikleri", "task": "Görevler"}
CATEGORIES = {
    "access": "Erişim ve oturum", "iam": "Kimlik ve yetki", "cmdb": "CMDB kayıtları", "schema": "Veri modeli",
    "integration": "Entegrasyon", "governance": "Yönetişim", "audit": "Denetim", "other": "Diğer",
}
#: Olay türü → (Türkçe ad, kategori). Yetki reddi ``<işlem>:<kaynak>`` biçimindedir (bkz. ``action_info``).
ACTIONS: dict[str, tuple[str, str]] = {
    "login": ("Oturum açma", "access"), "logout": ("Oturum kapatma", "access"),
    "restricted_read": ("Kısıtlı veri okundu", "access"),
    "role_insert": ("Rol verildi", "iam"), "role_update": ("Rol yenilendi", "iam"), "role_delete": ("Rol geri alındı", "iam"),
    "user_create": ("Kişi açıldı", "iam"), "user_update": ("Kişi güncellendi", "iam"),
    "group_bind": ("Gruba Entra grubu bağlandı", "iam"),
    "ci_create": ("CI oluşturma isteği", "cmdb"), "ci_edit": ("CI elle düzenleme", "cmdb"),
    "ci_relation": ("İlişki ekleme/kaldırma", "cmdb"), "ci_import": ("Toplu içe aktarma", "cmdb"),
    "ci_split": ("Birleştirme ayırma", "cmdb"), "ci_retire_decision": ("Emekliye ayırma / koruma kararı", "cmdb"),
    "ci_attest": ("Sahiplik onayı", "cmdb"), "match_decision": ("Eşleştirme kararı", "cmdb"),
    "anomaly_review": ("Yetkisiz değişiklik kararı", "cmdb"), "evidence_snapshot": ("Kanıt anlık görüntüsü", "cmdb"),
    "schema_draft": ("Değişiklik seti taslağı", "schema"), "schema_submit": ("Değişiklik seti onaya gönderildi", "schema"),
    "schema_approve": ("Değişiklik seti onaylandı", "schema"), "schema_reject": ("Değişiklik seti reddedildi", "schema"),
    "schema_publish": ("Değişiklik seti yayımlandı", "schema"),
    "connector_credential_issue": ("Bağlayıcı kimliği verildi", "integration"),
    "connector_credential_revoke": ("Bağlayıcı kimliği iptal edildi", "integration"),
    "connector_run_request": ("Bağlayıcı çalıştırma isteği", "integration"),
    "connector_run_cancel": ("Bağlayıcı çalışması iptal", "integration"),
    "connector_run_start": ("Bağlayıcı çalışması başladı", "integration"),
    "ingest_run_open": ("Alım çalışması açıldı", "integration"), "ingest_run_close": ("Alım çalışması kapandı", "integration"),
    "campaign_create": ("Kampanya oluşturuldu", "governance"), "campaign_update": ("Kampanya değişti", "governance"),
    "campaign_run": ("Kampanya çalıştırıldı", "governance"), "task_channel_update": ("Görev kanalı ayarı", "governance"),
    "quality_rule_create": ("Kalite kuralı oluşturuldu", "governance"),
    "quality_rule_update": ("Kalite kuralı değişti", "governance"),
    "quality_rule_run": ("Kalite kuralı çalıştırıldı", "governance"),
    "finding_exception": ("Bulguya istisna", "governance"), "finding_reopen": ("Bulgu yeniden açıldı", "governance"),
    "audit_export": ("Denetim izi dışa aktarıldı", "audit"), "audit_verify": ("Mühürler doğrulandı", "audit"),
    "audit_review": ("Denetim izi incelemesi", "audit"),
}
_TASK_TR = {"created": "Görev açıldı", "delivered": "Görev kanala teslim edildi", "delivery_failed": "Görev teslim edilemedi",
            "kept_in_evren": "Görev Evren'de tutuldu", "synced": "Görev eşitlendi", "external_closed": "Görev kanalda kapatıldı",
            "completed": "Görev kararı", "cancelled": "Görev iptal", "closed_sent": "Kanal kaydı kapatıldı",
            "reassigned": "Görev yeniden atandı", "reminded": "Görev hatırlatıldı", "overdue": "Görevin süresi geçti"}
_CI_TR = {"reconciliation": "uzlaştırma", "manual": "elle düzenleme", "merge": "birleştirme", "split": "ayırma",
          "reclassification": "sınıf değişikliği", "staleness": "eskime", "import": "içe aktarma", "schema": "şema",
          "sn_pull": "ServiceNow'dan çekme"}
_OPS_TR = {"read": "okuma", "create": "oluşturma", "write": "yazma", "delete": "silme", "merge": "birleştirme",
           "split": "ayırma", "review_match": "eşleştirme kararı", "schema_draft": "şema taslağı",
           "schema_approve": "şema onayı", "iam_admin": "yetki yönetimi", "audit_read": "denetim izi okuma",
           "connector_admin": "bağlayıcı yönetimi", "anomaly_review": "anomali kararı", "attest": "sahiplik onayı",
           "retire": "emekliye ayırma", "task_admin": "görev yönetimi", "audit_review": "denetim incelemesi"}


def action_info(source: str, action: str) -> tuple[str, str]:
    """(Türkçe ad, kategori). Bilinmeyen tür "Diğer" kategorisine düşer, adı olduğu gibi kalır."""
    if source == "ci":
        reason = action.removeprefix("ci_attribute_")
        return f"CI özniteliği değişti ({_CI_TR.get(reason, reason)})", "cmdb"
    if source == "task":
        return _TASK_TR.get(action.removeprefix("task_"), action), "governance"
    if action in ACTIONS:
        return ACTIONS[action]
    if ":" in action:  # yetki reddi: işlem:kaynak
        op, resource = action.split(":", 1)
        return f"Yetki reddi: {_OPS_TR.get(op, op)} ({resource})", "access"
    return action, "other"


_FORMULA = ("=", "+", "-", "@", "\t", "\r", "|")


def safe_cell(value: Any) -> str:
    """CSV hücresi: elektronik tabloda formül olarak çalışabilecek değerin önüne ``'`` konur (CSV enjeksiyonu)."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA) else text


def category_actions(category: str) -> list[str]:
    """Kategorideki bilinen güvenlik olayı türleri (süzgeç için)."""
    return sorted(a for a, (_label, cat) in ACTIONS.items() if cat == category)


# ------------------------------------------------------------------------------------------------------- mühür
GENESIS = "0" * 64
#: Akış başına mühürlenen satırlar: sabit sütunlar ve sıra (kimlik). Mühürleyen ve doğrulayan aynı metni kullanır.
SEAL_SQL = {
    "security": """SELECT id, at, principal, action, resource, decision, detail, host(client_ip), request_id
                     FROM audit.security_event WHERE at >= %s AND at < %s ORDER BY id""",
    "ci": """SELECT id, changed_at, ci_sys_id, sys_class_name, attribute, old_value, new_value, data_source, actor, reason,
                    correlation
               FROM audit.ci_change WHERE changed_at >= %s AND changed_at < %s ORDER BY id""",
    "task": """SELECT id, at, task, actor, action, detail FROM gov.task_event WHERE at >= %s AND at < %s ORDER BY id""",
}
#: Akışın ilk kaydının zamanı (ilk mühür, mühürleme başlamadan önceki bütün geçmişi tek dönemde kapsar)
FIRST_SQL = {
    "security": "SELECT min(at) FROM audit.security_event",
    "ci": "SELECT min(changed_at) FROM audit.ci_change",
    "task": "SELECT min(at) FROM gov.task_event",
}
WINDOW = timedelta(hours=1)
#: Dönem bittikten sonra bu kadar beklenir (sonradan işlenen işlemlerin kaydı da girsin diye)
GRACE = timedelta(minutes=15)


def _norm(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_norm(v) for v in value]
    return value


def canonical_line(row: Sequence[Any]) -> bytes:
    """Bir denetim satırının sabit biçimi: sütunlar sırayla, zaman UTC mikro saniye, sözlük anahtarları sıralı."""
    return json.dumps(_norm(list(row)), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str).encode()


def digest_of(rows: Iterable[Sequence[Any]]) -> tuple[str, int]:
    """Dönemin özeti ve satır sayısı."""
    h = hashlib.sha256()
    n = 0
    for row in rows:
        h.update(canonical_line(row))
        h.update(b"\n")
        n += 1
    return h.hexdigest(), n


def chain_of(previous: str | None, digest: str) -> str:
    return hashlib.sha256(f"{previous or GENESIS}:{digest}".encode()).hexdigest()


def floor_hour(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def seal_windows(last_end: datetime | None, first_event: datetime | None, now: datetime,
                 limit: int = 200) -> list[tuple[datetime, datetime]]:
    """Mühürlenecek dönemler (bitişi ``now - GRACE`` öncesine düşenler, en çok ``limit`` tane).

    * Akışın hiç mührü yoksa ilk dönem **başlangıç mührüdür**: ilk kaydın saatinden bugünün son tamamlanmış saatine
      kadar bütün geçmiş tek dönemdir (kurulumdan önceki yıllar binlerce boş dönem üretmez).
    * Sonra saatlik dönemler; çalışan bir süre kapalı kaldıysa aradaki saatler sırayla mühürlenir (boşluk kalmaz).
    """
    cutoff = floor_hour(now - GRACE)
    out: list[tuple[datetime, datetime]] = []
    if last_end is None:
        start = floor_hour(first_event) if first_event else cutoff - WINDOW
        end = max(cutoff, start + WINDOW) if first_event else cutoff
        if end > now - GRACE:
            return out
        out.append((start, end))
        start = end
    else:
        start = last_end
    while start + WINDOW <= now - GRACE and len(out) < limit:
        out.append((start, start + WINDOW))
        start += WINDOW
    return out


@dataclass(frozen=True)
class SealRecord:
    stream: str
    period_start: datetime
    period_end: datetime
    row_count: int
    digest: str
    prev_chain: str
    chain: str
    id: int | None = None
    sealed_at: datetime | None = None

    SQL_COLUMNS = "id, stream, period_start, period_end, row_count, digest, prev_chain, chain, sealed_at"

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> SealRecord:
        """``SQL_COLUMNS`` sırasındaki bir satırdan."""
        sid, stream, start, end, count, digest, prev, chain, sealed_at = row
        return cls(stream, start, end, count, digest, prev, chain, sid, sealed_at)


def check_seal(seal: SealRecord, previous: SealRecord | None, rows: Iterable[Sequence[Any]]) -> str | None:
    """Mührü dönemin bugünkü kayıtlarıyla doğrular; sorun yoksa ``None``, varsa Türkçe kısa açıklama.

    Yakalananlar: kayıt değişmesi, silinmesi veya döneme sonradan eklenmesi (özet ve sayı), mührün kendisinin
    değişmesi (zincir), bir mührün veya dönemin kaldırılması (önceki mühürle bağ ve dönem bitişikliği)."""
    if chain_of(seal.prev_chain, seal.digest) != seal.chain:
        return "Mühür kaydı değiştirilmiş: zincir değeri özetle tutmuyor."
    if previous is None:
        if seal.prev_chain != GENESIS:
            return "Zincirin ilk mührü eksik: önceki mühür bulunamadı."
    else:
        if previous.chain != seal.prev_chain:
            return "Önceki mühürle bağ kopuk: aradaki bir mühür kaldırılmış veya değiştirilmiş."
        if previous.period_end != seal.period_start:
            return "Dönemler bitişik değil: arada mühürlenmemiş bir dönem var."
    digest, count = digest_of(rows)
    if count != seal.row_count:
        verb = "eklenmiş" if count > seal.row_count else "silinmiş"
        return f"Dönemin kayıt sayısı değişti: mühürde {seal.row_count}, bugün {count} (kayıt {verb})."
    if digest != seal.digest:
        return "Dönemin kayıtlarından en az biri mühürden sonra değiştirilmiş."
    return None


def verify_chain(seals: Sequence[SealRecord], previous: SealRecord | None,
                 rows_for: Callable[[SealRecord], Iterable[Sequence[Any]]]) -> list[tuple[SealRecord, str | None]]:
    """Bir akışın ardışık mühürlerini sırayla doğrular. ``previous`` aralıktan önceki mühürdür (yoksa zincirin başı);
    ``rows_for`` mührün döneminin bugünkü kayıtlarını ``SEAL_SQL`` ile getirir (G/Ç çağırana aittir)."""
    out: list[tuple[SealRecord, str | None]] = []
    prev = previous
    for seal in seals:
        out.append((seal, check_seal(seal, prev, rows_for(seal))))
        prev = seal
    return out

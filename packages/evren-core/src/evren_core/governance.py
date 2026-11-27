"""Yönetişim görevleri (U8.4, ADR-0035, F1-24): sorumlu seçimi, durum kuralları ve görev kanalları.

Görevin doğruluk kaynağı Evren'dir (``gov.task``). Kanal yalnızca teslim ve iş takibidir:

* ``evren``       — uygulama içi; dış kayıt yoktur.
* ``servicenow``  — ServiceNow'da standart bir görev tablosuna (varsayılan ``sc_task``) **yalnızca standart görev
  alanlarıyla** kayıt açılır. ``correlation_id`` Evren görev kimliğidir: yeniden denemede ikinci kayıt açılmaz.
  ServiceNow'da iş kuralı, ACL veya alan değişikliği gerekmez (CLAUDE.md kural 11).

Karar Evren'de verilir; ServiceNow kaydı Evren'de kapanınca kapatılır. ServiceNow'da kararsız kapatılan kayıt Evren'de
"kanalda kapatıldı" diye işaretlenir, görev açık kalır.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .connectors.http import HttpClient, HttpError, HttpResponse

__all__ = [
    "ALLOWED_SN_FIELDS", "OUTCOMES", "Assignee", "ChannelSettings", "ExternalRecord", "Party", "ServiceNowTaskChannel",
    "ServiceNowTaskSimulator", "TaskForChannel", "pick_assignee",
]

#: Kararlar (Türkçe etiketleriyle). "cancelled" yalnızca görevi iptal eden Konfigürasyon yöneticisinindir.
OUTCOMES = {
    "confirmed": "Doğrulandı / yapıldı",
    "corrected": "Düzeltildi",
    "not_applicable": "Geçerli değil",
    "rejected": "Reddedildi",
    "cancelled": "İptal edildi",
    "resolved": "Bulgu giderildi",  # yalnızca kural görevinde, bulgu kapanınca kendiliğinden (ADR-0037)
}
#: Sorumlunun seçebildiği kararlar (iptal ve "bulgu giderildi" kendiliğinden veya yöneticiden gelir)
DECISIONS = tuple(k for k in OUTCOMES if k not in ("cancelled", "resolved"))

#: ServiceNow'a yazılan alanlar: yalnızca ``task`` tablosunun standart alanları (özel alan, iş kuralı, ACL gerekmez)
ALLOWED_SN_FIELDS = frozenset({
    "short_description", "description", "assignment_group", "assigned_to", "cmdb_ci", "due_date", "correlation_id",
    "correlation_display", "state", "close_notes",
})

_TABLE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_SYSID = re.compile(r"^[0-9a-f]{32}$")
_STATE = re.compile(r"^-?[0-9]{1,3}$")


@dataclass(frozen=True)
class Party:
    """Kişi veya grup: sys_id, etkin mi, kaynağı (servicenow / entra / evren)."""

    sys_id: str
    active: bool = True
    source: str = "servicenow"


@dataclass(frozen=True)
class Assignee:
    user: str | None
    group: str | None
    basis: str  # owned_by | managed_by | support_group | assigned | unassigned


def pick_assignee(*, owned_by: Party | None = None, managed_by: Party | None = None,
                  support_group: Party | None = None) -> Assignee:
    """CI'ın sorumlusu: sahip → yöneten → destek grubu. Pasif kişi atlanır; kişi seçilince destek grubu da eklenir
    (görev kişiden ayrılırsa grup üyeleri görür). Kimse yoksa "atanmamış" (Konfigürasyon yöneticisinin listesine düşer)."""
    group = support_group.sys_id if support_group and support_group.active else None
    for basis, person in (("owned_by", owned_by), ("managed_by", managed_by)):
        if person and person.active:
            return Assignee(person.sys_id, group, basis)
    if group:
        return Assignee(None, group, "support_group")
    return Assignee(None, None, "unassigned")


@dataclass(frozen=True)
class ChannelSettings:
    """ServiceNow kanal ayarı (``gov.task_channel.settings``; sır içermez)."""

    table: str = "sc_task"
    close_state: str = "3"
    cancel_state: str = "4"
    closed_states: Mapping[str, str] = field(default_factory=lambda: {"3": "done", "4": "cancelled", "7": "cancelled"})
    default_group: str | None = None

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> ChannelSettings:
        table = str(doc.get("table") or "sc_task")
        if not _TABLE.fullmatch(table) or table.startswith(("sys_", "cmdb")):
            raise ValueError("Hedef tablo bir görev tablosu adı olmalı (ör. sc_task); sys_ ve cmdb tabloları olmaz")
        close, cancel = str(doc.get("close_state", "3")), str(doc.get("cancel_state", "4"))
        closed = {str(k): str(v) for k, v in (doc.get("closed_states") or {"3": "done", "4": "cancelled"}).items()}
        if not all(_STATE.fullmatch(s) for s in (close, cancel, *closed)) or not set(closed.values()) <= {"done", "cancelled"}:
            raise ValueError("Durum değerleri sayı olmalı; kapanış eşlemesi done veya cancelled")
        group = doc.get("default_group")
        if group is not None and not _SYSID.fullmatch(str(group)):
            raise ValueError("Varsayılan atama grubu ServiceNow sys_id'si olmalı")
        return cls(table, close, cancel, closed, str(group) if group else None)

    def to_json(self) -> dict[str, Any]:
        return {"table": self.table, "close_state": self.close_state, "cancel_state": self.cancel_state,
                "closed_states": dict(self.closed_states), "default_group": self.default_group}


@dataclass(frozen=True)
class TaskForChannel:
    """Kanala gönderilen görev görünümü (yalnızca gerekenler; kişisel veri: atanacak kişi ve grup sys_id'si)."""

    sys_id: str
    number: str
    title: str
    description: str
    due_at: datetime
    assigned_to: str | None = None        # ServiceNow sys_user sys_id'si
    assignment_group: str | None = None   # ServiceNow sys_user_group sys_id'si
    cmdb_ci: str | None = None            # ServiceNow'da da bulunan CI'ın sys_id'si
    link: str | None = None               # Evren'deki görev sayfası


@dataclass(frozen=True)
class ExternalRecord:
    sys_id: str
    number: str
    state: str
    active: bool


def _standard_only(body: Mapping[str, Any]) -> dict[str, Any]:
    """ServiceNow'a yalnızca standart görev alanları gider (kural 11). Aksi bir programlama hatasıdır."""
    extra = set(body) - ALLOWED_SN_FIELDS
    if extra:
        raise ValueError(f"Standart dışı ServiceNow alanı: {', '.join(sorted(extra))}")
    return dict(body)


def _sn_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


class ServiceNowTaskChannel:
    """ServiceNow Table API ile görev açar, okur ve kapatır. Hata iletileri sır ve sunucu gövdesi içermez."""

    def __init__(self, http: HttpClient, settings: ChannelSettings) -> None:
        self.http = http
        self.settings = settings
        self.path = f"/api/now/table/{settings.table}"

    def payload(self, task: TaskForChannel) -> dict[str, Any]:
        group = task.assignment_group or self.settings.default_group
        text = task.description.strip()
        if task.link:
            text = f"{text}\n\nKarar Evren'de verilir: {task.link}".strip()
        body = {
            "short_description": f"[Evren {task.number}] {task.title}"[:160],
            "description": text[:4000],
            "correlation_id": task.sys_id,
            "correlation_display": "Evren",
            "due_date": _sn_time(task.due_at),
        }
        if task.assigned_to:
            body["assigned_to"] = task.assigned_to
        if group:
            body["assignment_group"] = group
        if task.cmdb_ci:
            body["cmdb_ci"] = task.cmdb_ci
        return _standard_only(body)

    def deliverable(self, task: TaskForChannel) -> bool:
        """ServiceNow'da atanacak bir karşılık (kişi, grup veya varsayılan grup) var mı?"""
        return bool(task.assigned_to or task.assignment_group or self.settings.default_group)

    @staticmethod
    def _record(row: Mapping[str, Any]) -> ExternalRecord:
        sys_id = str(row.get("sys_id") or "")
        if not _SYSID.fullmatch(sys_id):
            raise HttpError("ServiceNow yanıtında geçerli sys_id yok")
        active = str(row.get("active", "true")).lower() in ("true", "1")
        return ExternalRecord(sys_id, str(row.get("number") or "")[:40], str(row.get("state") or "")[:40], active)

    def find(self, task_sys_id: str) -> ExternalRecord | None:
        rows = self.http.get_json(self.path, sysparm_query=f"correlation_id={task_sys_id}",
                                  sysparm_fields="sys_id,number,state,active", sysparm_limit=1).get("result") or []
        return self._record(rows[0]) if rows else None

    def create(self, task: TaskForChannel) -> ExternalRecord:
        """Önce ``correlation_id`` ile arar (önceki deneme açmış olabilir), yoksa açar."""
        existing = self.find(task.sys_id)
        if existing:
            return existing
        res = self.http.post_json(self.path + "?sysparm_fields=sys_id,number,state,active", self.payload(task),
                                  ok=(200, 201))
        return self._record(res.get("result") or {})

    def get(self, ref: str) -> ExternalRecord:
        res = self.http.get_json(f"{self.path}/{ref}", sysparm_fields="sys_id,number,state,active")
        return self._record(res.get("result") or {})

    def close(self, ref: str, *, outcome: str, note: str) -> ExternalRecord:
        state = self.settings.cancel_state if outcome == "cancelled" else self.settings.close_state
        body = _standard_only({"state": state, "close_notes": note[:2000]})
        res = self.http.request("PATCH", f"{self.path}/{ref}?sysparm_fields=sys_id,number,state,active", body).json()
        return self._record(res.get("result") or {})

    def interpret(self, rec: ExternalRecord) -> str:
        """ServiceNow kaydının durumu Evren dilinde: open | done | cancelled."""
        if rec.state in self.settings.closed_states:
            return self.settings.closed_states[rec.state]
        return "open" if rec.active else "done"


class ServiceNowTaskSimulator:
    """Görev tablosu için Table API alt kümesi (``Transport``): ``correlation_id=`` sorgusu, kimlikle okuma, açma,
    PATCH. Basic veya Bearer kimlik doğrulaması zorunludur. ``unknown_fields`` gönderilen standart dışı alanları
    toplar (test: kural 11). ``close_externally`` bir ServiceNow kullanıcısının kaydı kapatmasını taklit eder."""

    def __init__(self, table: str = "sc_task", *, fail_next: int = 0) -> None:
        self.table = table
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.unknown_fields: set[str] = set()
        self.fail_next = fail_next
        self._seq = 1000

    @staticmethod
    def _json(status: int, payload: Any) -> HttpResponse:
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

    def close_externally(self, sys_id: str, state: str = "3") -> None:
        self.rows[sys_id].update(state=state, active="false")

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        self.calls.append((method, parts.path))
        if self.fail_next:
            self.fail_next -= 1
            return HttpResponse(503, {}, b"")
        auth = str(headers.get("Authorization", ""))
        if not ((auth.startswith("Basic ") and base64.b64decode(auth[6:])) or auth.startswith("Bearer ")):
            return self._json(401, {"error": {"message": "User Not Authenticated"}, "status": "failure"})
        m = re.fullmatch(r"/api/now/table/([a-z0-9_]+)(?:/([0-9a-f]{32}))?", parts.path)
        if not m or m.group(1) != self.table:
            return self._json(400, {"error": {"message": "Invalid table"}, "status": "failure"})
        ref = m.group(2)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if method == "GET" and ref:
            row = self.rows.get(ref)
            return self._json(200, {"result": row}) if row else self._json(404, {"error": {"message": "No Record"}})
        if method == "GET":
            cond = q.get("sysparm_query", "")
            if not cond.startswith("correlation_id="):
                return self._json(400, {"error": {"message": "Invalid query"}})
            want = cond.split("=", 1)[1]
            return self._json(200, {"result": [r for r in self.rows.values() if r.get("correlation_id") == want][:1]})
        data = json.loads(body or b"{}")
        self.unknown_fields |= set(data) - ALLOWED_SN_FIELDS
        if method == "POST" and not ref:
            self._seq += 1
            sys_id = uuid.uuid4().hex
            row = {**{k: str(v) for k, v in data.items()}, "sys_id": sys_id, "number": f"SCTASK{self._seq:07d}",
                   "state": "1", "active": "true"}
            self.rows[sys_id] = row
            return self._json(201, {"result": row})
        if method == "PATCH" and ref and ref in self.rows:
            row = self.rows[ref]
            row.update({k: str(v) for k, v in data.items()})
            if row.get("state") in ("3", "4", "7"):
                row["active"] = "false"
            return self._json(200, {"result": row})
        return self._json(400, {"error": {"message": "Invalid request"}})


# ---------------------------------------------------------------------------------------------- kampanyalar (U8.5)
#: Kampanya türleri (görev türüyle aynı ad)
CAMPAIGN_KINDS = {"attestation": "Veri güncelliği teyidi", "access_review": "Erişim gözden geçirme",
                  "checklist": "Kontrol listesi"}
#: Panelde seçilen sıklıklar (5 alanlı cron; ayın ilk günü 09:00, kampanya saat dilimi). Özel cron da verilebilir.
FREQUENCIES = {
    "monthly": ("Her ay", "0 9 1 * *"),
    "quarterly": ("Her çeyrek", "0 9 1 1,4,7,10 *"),
    "semiannual": ("Altı ayda bir", "0 9 1 1,7 *"),
    "annual": ("Her yıl", "0 9 1 1 *"),
}
#: Emekli CI kampanya kapsamına girmez (sahiplik onayıyla aynı kural)
NOT_RETIRED_SQL = "(coalesce(c.life_cycle_stage, '') <> 'End of Life' AND coalesce(c.install_status, 0) <> 7)"


def next_run(schedule: str, tz: str, after: datetime) -> datetime:
    """Zamanlamanın ``after``'dan sonraki ilk anı (UTC). Geçersiz ifade veya hiç gelmeyen an ``ValueError``."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from .schedule import CronError, CronSchedule

    try:
        cron = CronSchedule.parse(schedule)
        zone = ZoneInfo(tz)
    except (CronError, ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Zamanlama geçersiz: {exc}") from None
    nxt = cron.next_after(after.astimezone(zone))
    if nxt is None:
        raise ValueError("Zamanlama hiçbir zaman çalışmaz")
    return nxt.astimezone(UTC)


def campaign_scope(md: Any, scope_class: str, scope_query: str) -> tuple[str, list[Any]]:
    """Kampanya kapsamı → güvenli SQL koşulu (``c`` = cmdb.cmdb_ci). Sınıf ve alt sınıfları, encoded query koşulu
    (alan adları yalnızca metadata sözlüğünden; değerler parametre), emekli CI hariç. Hatalı koşul ``ValueError``."""
    from .encoded_query import QueryError, compile_sql, parse
    from .metadata import metadata_field_resolver

    if scope_class not in md.hierarchy:
        raise ValueError(f"Bilinmeyen sınıf: {scope_class}")
    classes = sorted(md.hierarchy.descendants(scope_class) | {scope_class})
    where, params = ["c.sys_class_name = ANY(%s)", NOT_RETIRED_SQL], [classes]
    if scope_query.strip():
        try:
            sql, qparams, _order = compile_sql(parse(scope_query), metadata_field_resolver(md, scope_class, "c"),
                                               lambda name: md.hierarchy.descendants(name))
        except QueryError as exc:
            raise ValueError(f"Koşul geçersiz: {exc}") from None
        where.append(f"({sql})")
        params += qparams
    return " AND ".join(where), params

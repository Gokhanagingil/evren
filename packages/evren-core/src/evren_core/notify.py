"""Bildirim merkezi (U8.9, ADR-0040): tür kataloğu, olaydan bildirim taslağı, teslim hedefi seçimi, ileti metni.

Bildirim kişiye (``user``: sys_user), gruba (``group``: sys_user_group) veya role (``role``: evren_…) adreslenir.
Uygulama içinde her zaman görünür (okundu bilgisi kişi başına). Dış kanallar:

* **e-posta** — kişiye kendi adresine (yalnızca görev Evren kanalındaysa: ServiceNow'a teslim edilen görevi ServiceNow
  zaten bildirir), gruba grubun adresine; rol ve grup için yönlendirme (``notify.route``) ile.
* **Teams** — yönlendirmedeki hedef adı; gelen web kancası adresi çalışanın sır dosyasındadır, veritabanında yoktur.

Bu modül saftır: hangi bildirimin kime gideceğini ve iletinin metnini hesaplar; göndermez. İleride süreç uygulaması
(olay, değişiklik, talep) aynı tür kataloğuna yeni türler ekler.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["KINDS", "SEVERITY_RANK", "Draft", "Recipient", "Route", "TaskInfo", "alert_drafts", "delivery_targets",
           "email_message", "safe_link", "task_drafts", "teams_card", "valid_email"]

#: Tür → (Türkçe ad, varsayılan önem)
KINDS: dict[str, tuple[str, str]] = {
    "task_assigned": ("Görev atandı", "info"),
    "task_reminder": ("Görevin son tarihi yaklaşıyor", "info"),
    "task_overdue": ("Görevin süresi geçti", "warning"),
    "task_escalated": ("Süresi geçen görev (tırmanma)", "warning"),
    "task_unassigned": ("Sorumlusu olmayan görev", "warning"),
    "alert_firing": ("Platform uyarısı", "warning"),
    "alert_resolved": ("Uyarı çözüldü", "info"),
    "test": ("Sınama bildirimi", "info"),
}
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
ESCALATION_ROLE = "evren_config_manager"
_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_LINK = re.compile(r"^#/[A-Za-z0-9/_=.%-]{0,200}$")


def valid_email(addr: str | None) -> bool:
    return bool(addr) and len(addr) <= 254 and bool(_EMAIL.match(addr or ""))


def safe_link(link: str | None) -> str | None:
    """Yalnızca uygulama içi bağlantı (``#/…``); diğerleri düşer."""
    return link if link and _LINK.match(link) else None


@dataclass(frozen=True)
class Draft:
    kind: str
    audience_kind: str        # user | group | role
    audience: str
    title: str
    body: str
    link: str | None
    source: str
    dedup_key: str
    severity: str = "info"


@dataclass(frozen=True)
class TaskInfo:
    sys_id: str
    number: str
    title: str
    due_at: datetime | None
    assignee_user: str | None
    assignee_group: str | None
    group_manager: str | None = None   # grubun yöneticisi (sys_user) — tırmanma
    ci_name: str | None = None


def _trim(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _assignee(task: TaskInfo) -> tuple[str, str] | None:
    if task.assignee_user:
        return "user", task.assignee_user
    if task.assignee_group:
        return "group", task.assignee_group
    return None


def task_drafts(event_id: int, action: str, task: TaskInfo, *, due_text: str = "") -> list[Draft]:
    """Görev olayından bildirimler. ``created``/``reassigned`` → atanana; ``reminded`` → atanana; ``overdue`` →
    atanana ve tırmanma: grubun yöneticisine, yoksa Konfigürasyon yöneticisi rolüne. Sorumlusu olmayan görev
    Konfigürasyon yöneticisine gider. Diğer olaylar bildirim üretmez."""
    link = f"#/gorevler/{task.sys_id}"
    head = f"{task.number} · {_trim(task.title, 120)}"
    ci = f" CI: {task.ci_name}." if task.ci_name else ""
    due = f" Son tarih: {due_text}." if due_text else ""
    who = _assignee(task)
    base = f"task_event:{event_id}"
    out: list[Draft] = []

    def add(kind: str, aud: tuple[str, str], title: str, body: str, sev: str | None = None) -> None:
        out.append(Draft(kind, aud[0], aud[1], _trim(title, 200), _trim(body, 2000), link, f"task:{task.sys_id}",
                         f"{base}:{kind}:{aud[0]}:{aud[1]}", sev or KINDS[kind][1]))

    if action in ("created", "reassigned"):
        if who:
            add("task_assigned", who, f"Size görev atandı: {head}", f"Kararınızı Evren'de verin.{ci}{due}")
        else:
            add("task_unassigned", ("role", ESCALATION_ROLE), f"Sorumlusu olmayan görev: {head}",
                f"CI'ın sahibi, yöneteni ve destek grubu yok; görevi bir kişiye veya gruba atayın.{ci}")
    elif action == "reminded" and who:
        add("task_reminder", who, f"Son tarih yaklaşıyor: {head}", f"Görevi süresi dolmadan sonuçlandırın.{ci}{due}")
    elif action == "overdue":
        if who:
            add("task_overdue", who, f"Görevin süresi geçti: {head}", f"Görevi en kısa sürede sonuçlandırın.{ci}{due}")
        boss = ("user", task.group_manager) if task.group_manager and task.group_manager != task.assignee_user \
            else ("role", ESCALATION_ROLE)
        add("task_escalated", boss, f"Süresi geçen görev: {head}",
            ("Sorumlusu görevi zamanında sonuçlandırmadı; gerekirse yeniden atayın veya sorumluyla görüşün."
             f"{ci}{due}"))
    return out


def alert_drafts(alert_id: str, transition: str, *, title: str, detail: str, advice: str, severity: str,
                 audience: Iterable[str]) -> list[Draft]:
    """Uyarı ateşlenince ve çözülünce kuralın rollerine bildirim."""
    if transition not in ("fired", "resolved"):
        return []
    kind = "alert_firing" if transition == "fired" else "alert_resolved"
    sev = severity if transition == "fired" else "info"
    text = f"{detail} {advice}".strip() if transition == "fired" else f"Koşul kalktı. {detail}".strip()
    head = title if transition == "fired" else f"Çözüldü: {title}"
    return [Draft(kind, "role", role, _trim(head, 200), _trim(text, 2000), "#/sistem-durumu/uyarilar",
                  f"alert:{alert_id}", f"alert:{alert_id}:{transition}:{role}", sev) for role in audience]


@dataclass(frozen=True)
class Route:
    channel: str            # email | teams
    target: str
    min_severity: str = "info"


@dataclass(frozen=True)
class Recipient:
    """Bir bildirimin dış kanal hedeflerini hesaplamak için alıcı bilgisi."""
    user_email: str | None = None
    group_email: str | None = None
    routes: Sequence[Route] = field(default_factory=tuple)
    external_task: bool = False   # görev ServiceNow'a teslim edildi (ServiceNow kendisi bildirir)


def delivery_targets(audience_kind: str, severity: str, rcpt: Recipient, *, email_on: bool,
                     teams_targets: Iterable[str]) -> list[tuple[str, str]]:
    """(kanal, hedef) listesi. Kapalı kanal ve tanımsız Teams hedefi atlanır; aynı hedef bir kez."""
    out: list[tuple[str, str]] = []
    rank = SEVERITY_RANK.get(severity, 0)
    teams = set(teams_targets)
    if email_on and audience_kind == "user" and valid_email(rcpt.user_email) and not rcpt.external_task:
        out.append(("email", str(rcpt.user_email)))
    if email_on and audience_kind == "group" and valid_email(rcpt.group_email) and not rcpt.external_task:
        out.append(("email", str(rcpt.group_email)))
    for r in rcpt.routes:
        if rank < SEVERITY_RANK.get(r.min_severity, 0):
            continue
        if r.channel == "email" and email_on and valid_email(r.target):
            out.append(("email", r.target))
        elif r.channel == "teams" and r.target in teams:
            out.append(("teams", r.target))
    seen: set[tuple[str, str]] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


# ------------------------------------------------------------------------------------------------- ileti
def _url(base_url: str | None, link: str | None) -> str | None:
    link = safe_link(link)
    if not base_url or not link:
        return None
    return base_url.rstrip("/") + "/" + link


def email_message(n: Mapping[str, Any], base_url: str | None) -> tuple[str, str]:
    """(konu, düz metin gövde). HTML yok: içerik metin olarak kalır."""
    sev = {"critical": "[Kritik] ", "warning": "[Uyarı] "}.get(str(n.get("severity")), "")
    subject = _trim(f"[Evren] {sev}{n['title']}", 200).replace("\r", " ").replace("\n", " ")
    lines = [str(n["title"]), "", str(n.get("body") or "")]
    url = _url(base_url, n.get("link"))
    if url:
        lines += ["", f"Evren'de açın: {url}"]
    lines += ["", "—", "Bu ileti Evren tarafından gönderildi. Bildirimlerin hangi adrese gittiği Sistem Durumu → "
                     "Bildirimler bölümünden yönetilir."]
    return subject, "\n".join(lines)


_MD = re.compile(r"([\\`*_\[\]()#>!|~])")


def _plain(text: str) -> str:
    """Kart metninde Markdown yorumlanmasın (bağlantı veya biçim enjeksiyonu)."""
    return _MD.sub(r"\\\1", text)


def teams_card(n: Mapping[str, Any], base_url: str | None) -> dict[str, Any]:
    """Teams iş akışı (gelen web kancası) için Uyarlanabilir Kart iletisi."""
    color = {"critical": "Attention", "warning": "Warning"}.get(str(n.get("severity")), "Default")
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": _plain(str(n["title"])), "weight": "Bolder", "size": "Medium", "wrap": True,
         "color": color},
        {"type": "TextBlock", "text": _plain(str(n.get("body") or "")), "wrap": True},
    ]
    card: dict[str, Any] = {"type": "AdaptiveCard", "version": "1.4", "body": body,
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json"}
    url = _url(base_url, n.get("link"))
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Evren'de aç", "url": url}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                                "content": card}]}

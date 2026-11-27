"""CI başına görev açma (kampanya ve kural görevleri ortak; ADR-0036, ADR-0037).

Sorumlu CI'dan bulunur (sahip → yöneten → destek grubu; ADR-0035). Tekillik anahtarı aynı olan açık görev varsa yenisi
açılmaz (``gov.task`` kısmi tekil dizini). Kanal verilmezse varsayılan kanal kullanılır.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from evren_core.governance import Assignee, Party, pick_assignee

__all__ = ["NoChannel", "OpenResult", "open_ci_tasks"]


class NoChannel(RuntimeError):
    """Etkin görev kanalı yok."""


@dataclass
class OpenResult:
    created: list[tuple[dict[str, Any], str, Assignee]] = field(default_factory=list)
    already_open: int = 0
    skipped: int = 0

    @property
    def unassigned(self) -> int:
        return sum(1 for _i, _t, who in self.created if who.basis == "unassigned")


def open_ci_tasks(conn: psycopg.Connection[Any], items: Sequence[dict[str, Any]], *, kind: str, source: str,
                  due_days: int, channel: str | None, actor: str,
                  event: Callable[[psycopg.Connection[Any], str, str, dict[str, Any]], None],
                  event_detail: dict[str, Any]) -> OpenResult:
    """``items``: ``ci``, ``owned_by``, ``managed_by``, ``support_group``, ``title``, ``description``, ``source_ref``,
    ``dedup_key`` ve isteğe bağlı ``skip_if(assignee) -> bool`` (ör. sorumlu görevi reddetmiş ve değişmemişse)."""
    result = OpenResult()
    if not items:
        return result
    users = {r[0]: Party(*r) for r in conn.execute(
        "SELECT sys_id, active, source FROM ref.sys_user WHERE sys_id = ANY(%s)",
        ([x for i in items for x in (i["owned_by"], i["managed_by"]) if x],)).fetchall()}
    groups = {r[0]: Party(*r) for r in conn.execute(
        "SELECT sys_id, active, source FROM ref.sys_user_group WHERE sys_id = ANY(%s)",
        ([i["support_group"] for i in items if i["support_group"]],)).fetchall()}
    ch = conn.execute("SELECT name, kind FROM gov.task_channel WHERE lifecycle = 'active' AND "
                      "(name = %s OR (%s::text IS NULL AND is_default))", (channel, channel)).fetchone()
    if ch is None:
        raise NoChannel("Etkin görev kanalı yok.")
    due = datetime.now(UTC) + timedelta(days=due_days)
    for item in items:
        who = pick_assignee(owned_by=users.get(item["owned_by"]), managed_by=users.get(item["managed_by"]),
                            support_group=groups.get(item["support_group"]))
        if (skip := item.get("skip_if")) and skip(who):
            result.skipped += 1
            continue
        row = conn.execute(
            """INSERT INTO gov.task (kind, title, description, ci_sys_id, assignee_user, assignee_group, assignee_basis,
                                     due_at, source, source_ref, dedup_key, channel, delivery_status, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (dedup_key) WHERE state = 'open' AND dedup_key IS NOT NULL DO NOTHING
               RETURNING sys_id""",
            (kind, item["title"][:160], item["description"][:4000], item["ci"], who.user, who.group, who.basis, due,
             source, item["source_ref"], item["dedup_key"], ch[0], "not_needed" if ch[1] == "evren" else "pending",
             actor)).fetchone()
        if row is None:
            result.already_open += 1
            continue
        event(conn, row[0], "created", {**event_detail, "basis": who.basis})
        result.created.append((item, row[0], who))
    return result

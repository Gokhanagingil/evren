"""Sunum yardımcıları: etiketler, seçim değerleri, referans adları, kapsam (rol tabanlı)."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg import sql

from evren_core.metadata import Metadata

__all__ = ["COVERAGE_ROLES", "Presenter"]

#: Kapsam bayrağı → rol(ler). Ürün adı değil rol: ürün değişse de kapsam hesabı aynı kalır (ADR-0009).
COVERAGE_ROLES: dict[str, tuple[str, ...]] = {
    "discovery": ("Discovery", "Virtualization", "Network"),
    "monitoring": ("Monitoring",),
    "security": ("EndpointSecurity",),
    "itsm": ("ITSM",),
}
REF_TABLES = ("sys_user", "sys_user_group", "cmn_location", "core_company", "cmn_department", "cmn_cost_center",
              "cmdb_model")


class Presenter:
    def __init__(self, md: Metadata) -> None:
        self.md = md
        self.labels = {c["name"]: c["label_tr"] for c in md.classes}
        self.choices = {(c["table"], c["element"]): {str(v["value"]): v["label_tr"] for v in c["values"]}
                        for c in md.choices}
        self.roles = {r["name"]: r for r in md.source_roles}

    def class_label(self, cls: str) -> str:
        return self.labels.get(cls, cls)

    def choice(self, attribute: str, value: Any) -> str | None:
        table = self.choices.get(("cmdb_ci", attribute))
        return table.get(str(value)) if table and value is not None else None

    def display(self, attribute: str, value: Any, refs: dict[str, str]) -> str:
        if value is None:
            return "—"
        label = self.choice(attribute, value)
        if label:
            return label
        if isinstance(value, bool):
            return "Evet" if value else "Hayır"
        if isinstance(value, str) and value in refs:
            return refs[value]
        if attribute in ("ram", "memory") and isinstance(value, int):
            return f"{value // 1024} GB" if value >= 1024 else f"{value} MB"
        return str(value)

    @staticmethod
    def resolve_refs(conn: psycopg.Connection[Any], ids: Iterable[str]) -> dict[str, str]:
        wanted = sorted({i for i in ids if isinstance(i, str) and len(i) == 32})
        if not wanted:
            return {}
        out: dict[str, str] = {}
        with conn.cursor() as cur:
            for table in REF_TABLES:
                cur.execute(sql.SQL("SELECT sys_id, name FROM {} WHERE sys_id = ANY(%s)").format(
                    sql.Identifier("ref", table)), (wanted,))
                out.update(dict(cur.fetchall()))
        return out

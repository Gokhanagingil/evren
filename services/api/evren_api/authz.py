"""Yetkilendirme: RBAC + rol kalıtımı + sınıf kalıtımı + alan hassasiyeti (docs/07).

* İzinler ``iam.permission`` tablosundan, ``iam.role_contains`` ile genişletilmiş rol kümesi için okunur.
* Kaynak eşlemesi: ``*`` her şeyi; sınıf adı kendisini ve alt sınıflarını kapsar; diğer kaynaklar birebir.
* Çakışmada ``deny`` kazanır. Okumada izin verilen en yüksek hassasiyetin üstündeki alanlar yanıta HİÇ konmaz.
* ABAC koşulları (``condition``) bu sürümde yalnızca okunur; koşullu izin, koşul değerlendirmesi gelene kadar
  verilmez (güvenli varsayılan; CI sahibinin "yalnızca kendi destek grubu" yazma izni F1'de).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import psycopg

from evren_core.classes import ClassHierarchy

__all__ = ["SENSITIVITY_RANK", "Authorizer", "Grant"]

SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


@dataclass(frozen=True)
class Grant:
    allowed: bool
    max_sensitivity: str = "public"

    def can_see(self, sensitivity: str) -> bool:
        return self.allowed and SENSITIVITY_RANK.get(sensitivity, 3) <= SENSITIVITY_RANK[self.max_sensitivity]


class Authorizer:
    def __init__(self, hierarchy: ClassHierarchy, ttl_seconds: float = 60.0) -> None:
        self.hierarchy = hierarchy
        self.ttl = ttl_seconds
        self._cache: dict[frozenset[str], tuple[float, list[dict[str, Any]]]] = {}

    def permissions(self, conn: psycopg.Connection[Any], roles: frozenset[str]) -> list[dict[str, Any]]:
        hit = self._cache.get(roles)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        with conn.cursor() as cur:
            cur.execute(
                """WITH RECURSIVE r(name) AS (
                       SELECT unnest(%s::text[])
                       UNION SELECT rc.contains FROM iam.role_contains rc JOIN r ON rc.role = r.name)
                   SELECT p.role, p.resource, p.operation, p.max_sensitivity, p.effect, p.condition IS NOT NULL
                     FROM iam.permission p JOIN r ON r.name = p.role""", (sorted(roles),))
            perms = [{"role": a, "resource": b, "operation": c, "max_sensitivity": d, "effect": e, "conditional": f}
                     for a, b, c, d, e, f in cur.fetchall()]
        self._cache[roles] = (time.monotonic() + self.ttl, perms)
        return perms

    def _covers(self, perm_resource: str, resource: str) -> bool:
        if perm_resource in ("*", resource):
            return True
        h = self.hierarchy
        return resource in h and perm_resource in h and perm_resource in h.ancestors(resource)

    def grant(self, perms: list[dict[str, Any]], resource: str, operation: str) -> Grant:
        matching = [p for p in perms if p["operation"] == operation and self._covers(p["resource"], resource)]
        if any(p["effect"] == "deny" for p in matching):
            return Grant(False)
        allows = [p for p in matching if p["effect"] == "allow" and not p["conditional"]]
        if not allows:
            return Grant(False)
        top = max(allows, key=lambda p: SENSITIVITY_RANK[p["max_sensitivity"]])["max_sensitivity"]
        return Grant(True, top)

    def summary(self, perms: list[dict[str, Any]]) -> list[dict[str, str]]:
        seen = sorted({(p["resource"], p["operation"], p["max_sensitivity"], p["effect"]) for p in perms})
        return [{"resource": a, "operation": b, "max_sensitivity": c, "effect": d} for a, b, c, d in seen]

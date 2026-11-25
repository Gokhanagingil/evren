"""PostgreSQL üzerinde kimlik aday dizini ve kimlik bağı okuyucusu.

``evren_core.identity.CandidateIndex`` sözleşmesinin üretim uygulaması (kesin anahtarlar). Bellek içi
dizinle aynı davranır: kesin anahtar eşleşmesi 10, ad öneki 1 puan; en yüksek ``limit`` aday döner.
Tüm sorgular verilen imleçle, çağıranın işlemi içinde çalışır.
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from evren_core.identity.index import CandidateProfile
from evren_core.identity.model import IdentityKeys

__all__ = ["PostgresCandidateIndex", "PostgresLinkStore", "keys_from_json", "keys_to_json"]

_FIELDS = tuple(IdentityKeys.__dataclass_fields__)


def keys_to_json(keys: IdentityKeys) -> dict[str, list[str]]:
    return {name: sorted(getattr(keys, name)) for name in _FIELDS if getattr(keys, name)}


def keys_from_json(doc: dict[str, Any] | None) -> IdentityKeys:
    doc = doc or {}
    return IdentityKeys(**{name: frozenset(doc.get(name, ())) for name in _FIELDS})


def _postings(keys: IdentityKeys) -> list[tuple[str, str]]:
    rows = [(key_type, value) for key_type, values in keys.by_type().items() for value in values if value]
    rows += [("prefix", compact[:4]) for compact in keys.compacts if len(compact) >= 4]
    return sorted(set(rows))


class PostgresCandidateIndex:
    def __init__(self, cur: psycopg.Cursor[Any]) -> None:
        self.cur = cur

    def lookup(self, key_type: str, value: str, family: str | None) -> set[str]:
        self.cur.execute(
            "SELECT ci_sys_id FROM ingest.identity_key WHERE key_type = %s AND family = %s AND value = %s",
            (key_type, family or "", value),
        )
        return {row[0] for row in self.cur.fetchall()}

    def profile(self, ci_sys_id: str) -> CandidateProfile:
        self.cur.execute("SELECT class_name, family, keys FROM ingest.identity_profile WHERE ci_sys_id = %s",
                         (ci_sys_id,))
        row = self.cur.fetchone()
        if row is None:
            raise KeyError(ci_sys_id)
        return CandidateProfile(ci_sys_id, row[0], row[1], keys_from_json(row[2]))

    def blocking_candidates(self, keys: IdentityKeys, family: str | None, limit: int = 50) -> set[str]:
        postings = _postings(keys)
        if not postings:
            return set()
        types, values = zip(*postings, strict=True)
        self.cur.execute(
            """SELECT k.ci_sys_id, sum(CASE WHEN k.key_type = 'prefix' THEN 1 ELSE 10 END) AS score
                 FROM ingest.identity_key k
                 JOIN unnest(%s::text[], %s::text[]) AS q(key_type, value)
                   ON q.key_type = k.key_type AND q.value = k.value
                WHERE k.family = %s
                GROUP BY k.ci_sys_id
                ORDER BY score DESC, k.ci_sys_id
                LIMIT %s""",
            (list(types), list(values), family or "", limit),
        )
        return {row[0] for row in self.cur.fetchall()}

    def upsert(self, profile: CandidateProfile) -> None:
        """Anahtarları mevcut profille birleştirir (yalnızca ekler; ayırma bu yolla yapılmaz)."""
        self.cur.execute("SELECT keys FROM ingest.identity_profile WHERE ci_sys_id = %s FOR UPDATE",
                         (profile.ci_sys_id,))
        row = self.cur.fetchone()
        keys = keys_from_json(row[0]).merged(profile.keys) if row else profile.keys
        self.cur.execute(
            """INSERT INTO ingest.identity_profile (ci_sys_id, class_name, family, keys, updated_at)
               VALUES (%s, %s, %s, %s, now())
               ON CONFLICT (ci_sys_id) DO UPDATE SET class_name = EXCLUDED.class_name, family = EXCLUDED.family,
                 keys = EXCLUDED.keys, updated_at = now()""",
            (profile.ci_sys_id, profile.class_name, profile.family, Jsonb(keys_to_json(keys))),
        )
        postings = _postings(keys)
        if postings:
            types, values = zip(*postings, strict=True)
            self.cur.execute(
                """INSERT INTO ingest.identity_key (key_type, family, value, ci_sys_id)
                   SELECT q.key_type, %s, q.value, %s FROM unnest(%s::text[], %s::text[]) AS q(key_type, value)
                   ON CONFLICT DO NOTHING""",
                (profile.family or "", profile.ci_sys_id, list(types), list(values)),
            )


class PostgresLinkStore:
    """Etkin kimlik bağlarını okur. Bağ yazımı meta veriyle (yöntem, olasılık, kanıt, karar veren)
    ``ReconcilerStore`` tarafından yapılır; çözümleyici bu sınıf üzerinden yazmaz."""

    def __init__(self, cur: psycopg.Cursor[Any]) -> None:
        self.cur = cur

    def get(self, data_source: str, source_key: str) -> str | None:
        self.cur.execute(
            "SELECT ci_sys_id FROM ingest.identity_link WHERE data_source = %s AND source_key = %s AND active",
            (data_source, source_key),
        )
        row = self.cur.fetchone()
        return row[0] if row else None

    def set(self, data_source: str, source_key: str, ci_sys_id: str) -> None:  # pragma: no cover - sözleşme
        raise NotImplementedError("Bağlar ReconcilerStore._link ile meta veriyle yazılır")

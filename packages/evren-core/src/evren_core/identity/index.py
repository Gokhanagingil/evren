"""Aday dizini: bir kaynak kaydının karşılaştırılacağı CI'ları hızla bulur (blocking).

``CandidateIndex`` bir protokoldür. Testler ve küçük partiler için bellek içi
uygulama burada; üretim uygulaması aynı arayüzü PostgreSQL (kesin anahtarlar)
ve OpenSearch (bulanık ad araması) üzerinde gerçekleştirir.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Protocol

from .model import IdentityKeys

__all__ = ["CandidateIndex", "CandidateProfile", "InMemoryCandidateIndex"]


@dataclass(frozen=True)
class CandidateProfile:
    ci_sys_id: str
    class_name: str
    family: str | None
    keys: IdentityKeys


class CandidateIndex(Protocol):
    def lookup(self, key_type: str, value: str, family: str | None) -> set[str]: ...

    def profile(self, ci_sys_id: str) -> CandidateProfile: ...

    def blocking_candidates(self, keys: IdentityKeys, family: str | None, limit: int = 50) -> set[str]: ...

    def upsert(self, profile: CandidateProfile) -> None: ...


class InMemoryCandidateIndex:
    def __init__(self) -> None:
        self._profiles: dict[str, CandidateProfile] = {}
        self._postings: dict[tuple[str, str | None, str], set[str]] = defaultdict(set)
        self._prefix: dict[tuple[str | None, str], set[str]] = defaultdict(set)

    def __len__(self) -> int:
        return len(self._profiles)

    def upsert(self, profile: CandidateProfile) -> None:
        existing = self._profiles.get(profile.ci_sys_id)
        keys = existing.keys.merged(profile.keys) if existing else profile.keys
        merged = CandidateProfile(profile.ci_sys_id, profile.class_name, profile.family, keys)
        self._profiles[profile.ci_sys_id] = merged
        for key_type, values in keys.by_type().items():
            for value in values:
                self._postings[(key_type, merged.family, value)].add(merged.ci_sys_id)
        for compact in keys.compacts:
            self._prefix[(merged.family, compact[:4])].add(merged.ci_sys_id)

    def lookup(self, key_type: str, value: str, family: str | None) -> set[str]:
        return set(self._postings.get((key_type, family, value), ()))

    def profile(self, ci_sys_id: str) -> CandidateProfile:
        return self._profiles[ci_sys_id]

    def blocking_candidates(self, keys: IdentityKeys, family: str | None, limit: int = 50) -> set[str]:
        """Ortak anahtar sayısına göre en olası ``limit`` adayı döndürür.

        Ad öneki (ilk 4 karakter) yalnızca bulanık karşılaştırma için aday getirir;
        kesin anahtarlar her zaman önceliklidir.
        """
        hits: Counter[str] = Counter()
        for key_type, values in keys.by_type().items():
            for value in values:
                for ci in self.lookup(key_type, value, family):
                    hits[ci] += 10
        for compact in keys.compacts:
            for ci in self._prefix.get((family, compact[:4]), ()):
                hits[ci] += 1
        return {ci for ci, _ in hits.most_common(limit)}

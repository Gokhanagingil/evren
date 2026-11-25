"""Kimlik çözümleyici: bir kaynak kaydının hangi CI'ya ait olduğuna karar verir.

Karar sırası
------------
1. **Yapışkan bağlantı** — Kaynak kaydı daha önce bir CI'ya bağlandıysa bağlantı
   korunur; ancak güçlü çelişki (UUID/seri/OS değişimi) varsa incelemeye alınır
   (ör. aynı adla yeniden kurulan VM).
2. **Tanımlama kuralları** — IRE semantiği (öncelik sırası, alt sınıfa kalıtım).
   Tek eşleşme + çelişki yok → otomatik. Çelişki veya çoklu eşleşme → inceleme.
3. **Olasılıksal skor** — Kurallar sonuç vermezse aday dizininden gelen CI'lar
   skorlanır. Otomatik eşik + fark koşulu → otomatik; inceleme eşiği → kuyruk.
4. Aksi halde **yeni CI**. Kimlik verisi yoksa **yetersiz** (park edilir).

Çözümleyici hiçbir zaman veri silmez veya birleştirmeyi geri alınamaz yapmaz;
kararlar ``identity_link`` tablosunda kaynak kaydı → CI bağlantısı olarak tutulur
ve ayırma (split) ile geri alınabilir.
"""
from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from .index import CandidateIndex, CandidateProfile
from .keys import extract_keys
from .model import Feature, IdentityKeys, Outcome, Resolution, SourceRecord
from .rules import IdentifierEntry, RuleSet
from .scoring import MatchThresholds, ScoreResult, ScoringWeights, contradictions, score

__all__ = ["IdentityEngine", "IdentityResolver", "InMemoryLinkStore", "LinkStore"]


class LinkStore(Protocol):
    def get(self, data_source: str, source_key: str) -> str | None: ...

    def set(self, data_source: str, source_key: str, ci_sys_id: str) -> None: ...


class InMemoryLinkStore:
    def __init__(self) -> None:
        self._links: dict[tuple[str, str], str] = {}

    def get(self, data_source: str, source_key: str) -> str | None:
        return self._links.get((data_source, source_key))

    def set(self, data_source: str, source_key: str, ci_sys_id: str) -> None:
        self._links[(data_source, source_key)] = ci_sys_id

    def items(self) -> dict[tuple[str, str], str]:
        return dict(self._links)


def _qualified_fqdns(keys: IdentityKeys) -> set[str]:
    """En az iki etiketli alan adına sahip FQDN'ler (``host.local`` gibi sahte alanlar hariç)."""
    return {f for f in keys.fqdns if f.count(".") >= 2}


@dataclass
class IdentityResolver:
    rules: RuleSet
    index: CandidateIndex
    links: LinkStore
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    thresholds: MatchThresholds = field(default_factory=MatchThresholds)

    # -- yardımcılar ---------------------------------------------------------

    def _family(self, class_name: str) -> str | None:
        h = self.rules.hierarchy
        return h.family(class_name) if class_name in h else None

    def _class_conflict(self, record_class: str, candidate: CandidateProfile) -> list[Feature]:
        h = self.rules.hierarchy
        if record_class in h and candidate.class_name in h and not h.compatible(record_class, candidate.class_name):
            return [Feature("class_conflict", -3.0,
                            f"Sınıflar uyumsuz: {record_class} ≠ {candidate.class_name}")]
        return []

    def _conflicts(self, record: SourceRecord, keys: IdentityKeys, candidate: CandidateProfile) -> list[Feature]:
        found = contradictions(keys, candidate.keys, self.weights)
        a, b = _qualified_fqdns(keys), _qualified_fqdns(candidate.keys)
        if a and b and not (a & b):
            found.append(Feature("fqdn_conflict", -2.5, f"FQDN'ler farklı alanlarda: {sorted(a)} ≠ {sorted(b)}"))
        found.extend(self._class_conflict(record.class_hint, candidate))
        return found

    def _score(self, record: SourceRecord, keys: IdentityKeys, candidate: CandidateProfile) -> ScoreResult:
        result = score(keys, candidate.keys, self.weights)
        extra = self._class_conflict(record.class_hint, candidate)
        a, b = _qualified_fqdns(keys), _qualified_fqdns(candidate.keys)
        if a and b and not (a & b):
            extra.append(Feature("fqdn_conflict", -2.5, "FQDN'ler farklı alanlarda"))
        if not extra:
            return result
        log_odds = result.log_odds + sum(f.weight for f in extra)
        return ScoreResult(1.0 / (1.0 + math.exp(-log_odds)), log_odds, result.features + tuple(extra))

    def _lookup_entry(self, entry: IdentifierEntry, keys: IdentityKeys, family: str | None) -> set[str] | None:
        by_type = keys.by_type()
        result: set[str] | None = None
        for key_type in entry.key_types():
            values = by_type.get(key_type, frozenset())
            if not values:
                return None  # girdi uygulanamaz: öznitelik kayıtta yok
            found: set[str] = set()
            for value in values:
                found |= self.index.lookup(key_type, value, family)
            result = found if result is None else result & found
        return result

    # -- karar ----------------------------------------------------------------

    def resolve(self, record: SourceRecord, keys: IdentityKeys | None = None) -> Resolution:
        keys = keys if keys is not None else extract_keys(record)
        family = self._family(record.class_hint)

        linked = self.links.get(record.data_source, record.source_key)
        if linked:
            profile = self.index.profile(linked)
            conflicts = [f for f in self._conflicts(record, keys, profile) if f.code != "class_conflict"]
            if conflicts:
                return Resolution(
                    Outcome.REVIEW, linked, features=tuple(conflicts), candidates=((linked, 0.0),),
                    reasons=("Bağlı CI ile kimlik çelişkisi; nesne yeniden oluşturulmuş olabilir",),
                )
            return Resolution(Outcome.LINKED, linked)

        if keys.is_empty:
            return Resolution(Outcome.INSUFFICIENT, reasons=("Ad, seri, UUID, MAC veya harici kimlik yok",))

        rule = self.rules.for_class(record.class_hint)
        if rule is not None and not rule.independent:
            # Bağımlı CI'lar (uygulama, ağ adaptörü) yalnızca üst CI bağlamında tanımlanabilir.
            # Üst bağlam çözümlemesi uygulanana kadar küresel ad eşleştirmesi YAPILMAZ (güvenli varsayılan).
            return Resolution(Outcome.INSUFFICIENT,
                              reasons=("Bağımlı CI tanımlaması üst CI bağlamı gerektirir (DURUM.md: bağımlı kimlik)",))
        if rule is not None:
            for entry in rule.ordered():
                found = self._lookup_entry(entry, keys, family)
                if found is None or not found:
                    continue
                if len(found) == 1:
                    ci = next(iter(found))
                    conflicts = self._conflicts(record, keys, self.index.profile(ci))
                    if conflicts:
                        return Resolution(
                            Outcome.REVIEW, ci, rule=entry.label, features=tuple(conflicts),
                            candidates=((ci, 0.0),),
                            reasons=("Kural eşleşti ancak güçlü çelişki var; otomatik birleştirilmedi",),
                        )
                    return Resolution(Outcome.RULE_MATCH, ci, rule=entry.label)
                ranked = sorted(
                    ((c, self._score(record, keys, self.index.profile(c)).probability) for c in found),
                    key=lambda item: (-item[1], item[0]),
                )
                return Resolution(
                    Outcome.REVIEW, rule=entry.label, candidates=tuple(ranked),
                    reasons=(f"Kural {len(found)} CI ile eşleşti: CMDB'de olası mükerrer kayıt",),
                )

        scored = sorted(
            ((c, self._score(record, keys, self.index.profile(c))) for c in self.index.blocking_candidates(keys, family)),
            key=lambda item: (-item[1].probability, item[0]),
        )
        no_identity = ("Yalnızca kaynak içi kimlik ve/veya IP var; yeni CI için ad, FQDN, seri, UUID veya MAC gerekir",)
        if not scored:
            return Resolution(Outcome.NEW) if keys.can_create_ci else Resolution(Outcome.INSUFFICIENT, reasons=no_identity)
        best_ci, best = scored[0]
        second = scored[1][1].probability if len(scored) > 1 else 0.0
        candidates = tuple((c, r.probability) for c, r in scored[:5])
        t = self.thresholds
        if best.probability >= t.auto and best.probability - second >= t.margin:
            return Resolution(Outcome.SCORE_MATCH, best_ci, best.probability, features=best.features, candidates=candidates)
        if best.probability >= t.review:
            return Resolution(Outcome.REVIEW, best_ci, best.probability, features=best.features, candidates=candidates,
                              reasons=("Skor otomatik eşiğin altında veya adaylar birbirine çok yakın",))
        if not keys.can_create_ci:
            return Resolution(Outcome.INSUFFICIENT, probability=best.probability, features=best.features,
                              candidates=candidates, reasons=no_identity)
        return Resolution(Outcome.NEW, probability=best.probability, features=best.features, candidates=candidates)


@dataclass
class IdentityEngine:
    """Çözümleyici + dizin güncellemesi. Uzlaştırıcı servisinin referans akışıdır."""

    resolver: IdentityResolver
    id_factory: Callable[[], str] = field(default=lambda: uuid.uuid4().hex)

    def process(self, record: SourceRecord) -> Resolution:
        keys = extract_keys(record)
        resolution = self.resolver.resolve(record, keys)
        if resolution.outcome in (Outcome.LINKED, Outcome.RULE_MATCH, Outcome.SCORE_MATCH):
            self._attach(record, keys, resolution.ci_sys_id)  # type: ignore[arg-type]
        elif resolution.outcome is Outcome.NEW:
            ci = self.id_factory()
            self._attach(record, keys, ci)
            resolution = replace(resolution, ci_sys_id=ci)
        return resolution

    def import_existing(self, record: SourceRecord, ci_sys_id: str) -> Resolution:
        """İlk yükleme: kaydı kimlik çözümlemeden, verilen sys_id ile bağlar (``method='import'``).

        ServiceNow CMDB'nin ilk yüklemesinde CI aynı sys_id ile açılır; task tarafındaki referanslar
        dönüşümsüz çözülür. Mükerrer SN kayıtları birleştirilmez: her biri kendi CI'ı olur ve
        sonraki keşif kayıtları onları "olası mükerrer" olarak eşleştirme kuyruğuna düşürür.
        """
        if self.resolver.links.get(record.data_source, record.source_key):
            raise ValueError(f"{record.data_source}/{record.source_key} zaten bağlı; ilk yükleme tekrarlanamaz")
        self._attach(record, extract_keys(record), ci_sys_id)
        return Resolution(Outcome.LINKED, ci_sys_id, rule="import", reasons=("İlk yükleme: sys_id korundu",))

    def _attach(self, record: SourceRecord, keys: IdentityKeys, ci: str) -> None:
        index = self.resolver.index
        hierarchy = self.resolver.rules.hierarchy
        class_name = record.class_hint
        try:
            current = index.profile(ci)
        except KeyError:
            current = None
        if current is not None and class_name in hierarchy and current.class_name in hierarchy:
            if hierarchy.compatible(class_name, current.class_name):
                class_name = hierarchy.more_specific(class_name, current.class_name)
            else:
                class_name = current.class_name
        index.upsert(CandidateProfile(ci, class_name, self.resolver._family(class_name), keys))
        self.resolver.links.set(record.data_source, record.source_key, ci)

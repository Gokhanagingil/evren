"""Olasılıksal eşleştirme skoru (Fellegi–Sunter yaklaşımına dayalı log-odds toplamı).

Her kanıt bir ağırlık ekler; toplam lojistik fonksiyonla olasılığa çevrilir.
Ağırlıklar ``ScoringWeights`` ile ayarlanabilir ve inceleme kuyruğundaki insan
kararlarıyla zaman içinde kalibre edilmelidir (bkz. docs/03).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .model import Feature, IdentityKeys

__all__ = ["MatchThresholds", "ScoreResult", "ScoringWeights", "contradictions", "jaro_winkler", "score"]


@dataclass(frozen=True)
class ScoringWeights:
    prior: float = -3.0
    uuid_match: float = 9.0
    external_id_match: float = 9.0
    correlation_match: float = 9.0
    vmware_serial_match: float = 8.0
    serial_match: float = 7.0
    fqdn_match: float = 5.0
    name_match: float = 4.5
    compact_name_match: float = 2.6
    similar_name: float = 1.0
    mac_first: float = 3.0
    mac_additional: float = 1.0
    mac_cap: float = 5.0
    ip_overlap: float = 1.0
    serial_conflict: float = -4.0
    uuid_conflict: float = -5.0
    os_conflict: float = -3.0
    name_digit_conflict: float = -3.5
    name_mismatch: float = -1.5


DEFAULT_WEIGHTS = ScoringWeights()


@dataclass(frozen=True)
class MatchThresholds:
    auto: float = 0.97    # bu olasılık ve üstü otomatik birleşir
    review: float = 0.35  # bu aralık inceleme kuyruğuna gider; altı yeni CI
    margin: float = 0.10  # en iyi aday ile ikinci arasındaki asgari fark


@dataclass(frozen=True)
class ScoreResult:
    probability: float
    log_odds: float
    features: tuple[Feature, ...]


# ---------------------------------------------------------------------------
# Jaro–Winkler (bağımlılıksız; üretimde rapidfuzz ile değiştirilebilir)
# ---------------------------------------------------------------------------


def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    l1, l2 = len(s1), len(s2)
    if not l1 or not l2:
        return 0.0
    window = max(max(l1, l2) // 2 - 1, 0)
    m1 = [False] * l1
    m2 = [False] * l2
    matches = 0
    for i, ch in enumerate(s1):
        lo, hi = max(0, i - window), min(i + window + 1, l2)
        for j in range(lo, hi):
            if not m2[j] and s2[j] == ch:
                m1[i] = m2[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t = 0
    k = 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t //= 2
    return (matches / l1 + matches / l2 + (matches - t) / matches) / 3.0


def jaro_winkler(s1: str, s2: str, prefix_scale: float = 0.1) -> float:
    j = _jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1[:4], s2[:4], strict=False):
        if a != b:
            break
        prefix += 1
    return j + prefix * prefix_scale * (1 - j)


_DIGITS = re.compile(r"\d+")


def _digit_signature(name: str) -> tuple[str, tuple[str, ...]]:
    """``srv-db-01`` → (``srv-db-#``, (``01``,)). Numaralı adların ayrımı için."""
    return _DIGITS.sub("#", name), tuple(_DIGITS.findall(name))


# ---------------------------------------------------------------------------
# Çelişkiler ve skor
# ---------------------------------------------------------------------------


def contradictions(a: IdentityKeys, b: IdentityKeys, w: ScoringWeights = DEFAULT_WEIGHTS) -> list[Feature]:
    """İki kimliğin aynı nesne olamayacağını düşündüren güçlü kanıtlar.

    Kural tabanlı eşleşme (ör. yalnızca ad) bu kanıtlardan biri varken otomatik
    birleştirme yapmaz; kayıt incelemeye gider. ServiceNow IRE'den farkımız budur:
    ad eşleşmesi seri/UUID/işletim sistemi çelişkisini ezip geçmez.
    """
    out: list[Feature] = []
    if a.serials and b.serials and not (a.serials & b.serials):
        out.append(Feature("serial_conflict", w.serial_conflict,
                           f"Seri numaraları farklı: {sorted(a.serials)} ≠ {sorted(b.serials)}"))
    if a.uuids and b.uuids and not (a.uuids & b.uuids):
        out.append(Feature("uuid_conflict", w.uuid_conflict, "BIOS/SMBIOS UUID'leri farklı (bayt sırası da denendi)"))
    if a.os_families and b.os_families and not (a.os_families & b.os_families):
        out.append(Feature("os_conflict", w.os_conflict,
                           f"İşletim sistemi ailesi farklı: {sorted(a.os_families)} ≠ {sorted(b.os_families)}"))
    return out


def score(a: IdentityKeys, b: IdentityKeys, w: ScoringWeights = DEFAULT_WEIGHTS) -> ScoreResult:
    features: list[Feature] = []

    if a.uuids & b.uuids:
        features.append(Feature("uuid_match", w.uuid_match, "BIOS/SMBIOS UUID eşleşti"))
    if a.external_ids & b.external_ids:
        features.append(Feature("external_id_match", w.external_id_match,
                                f"Harici kimlik eşleşti: {sorted(a.external_ids & b.external_ids)[0]}"))
    if (a.correlation_ids & b.correlation_ids) or (a.vm_objects & b.vm_objects):
        features.append(Feature("correlation_match", w.correlation_match, "Korelasyon/nesne kimliği eşleşti"))
    if a.vmware_serials & b.vmware_serials:
        features.append(Feature("vmware_serial_match", w.vmware_serial_match, "VMware seri numarası eşleşti"))
    if a.serials & b.serials:
        features.append(Feature("serial_match", w.serial_match,
                                f"Seri numarası eşleşti: {sorted(a.serials & b.serials)[0]}"))

    if a.fqdns & b.fqdns:
        features.append(Feature("fqdn_match", w.fqdn_match, f"FQDN eşleşti: {sorted(a.fqdns & b.fqdns)[0]}"))
    common_names = a.names & b.names
    if common_names:
        features.append(Feature("name_match", w.name_match, f"Kanonik ad eşleşti: {sorted(common_names)[0]}"))
    elif a.compacts & b.compacts:
        features.append(Feature("compact_name_match", w.compact_name_match,
                                f"Ayraçsız ad eşleşti: {sorted(a.compacts & b.compacts)[0]}"))
    elif a.names and b.names:
        best = 0.0
        digit_conflict = False
        for x in a.names:
            for y in b.names:
                sx, sy = _digit_signature(x), _digit_signature(y)
                if sx[0] == sy[0] and sx[1] != sy[1]:
                    digit_conflict = True
                    continue
                best = max(best, jaro_winkler(x, y))
        if digit_conflict and best < 0.93:
            features.append(Feature("name_digit_conflict", w.name_digit_conflict,
                                    "Adlar yalnızca numarada farklı (ör. srv-01 / srv-02): farklı cihaz göstergesi"))
        elif best >= 0.93:
            features.append(Feature("similar_name", w.similar_name, f"Adlar benzer (Jaro-Winkler {best:.2f})"))
        elif best < 0.6:
            features.append(Feature("name_mismatch", w.name_mismatch, "Adlar belirgin biçimde farklı"))

    shared_macs = a.macs & b.macs
    if shared_macs:
        mac_weight = min(w.mac_first + w.mac_additional * (len(shared_macs) - 1), w.mac_cap)
        features.append(Feature("mac_match", mac_weight, f"{len(shared_macs)} güçlü MAC adresi ortak"))
    if a.ips & b.ips:
        features.append(Feature("ip_overlap", w.ip_overlap, "IP adresi ortak (zayıf kanıt)"))

    features.extend(contradictions(a, b, w))

    log_odds = w.prior + sum(f.weight for f in features)
    probability = 1.0 / (1.0 + math.exp(-log_odds))
    return ScoreResult(probability, log_odds, tuple(features))

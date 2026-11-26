"""Bağımlı CI kimliği (F1-07, ADR-0029): ana CI bağlamında yerel anahtar. Saf mantık, G/Ç yok.

ServiceNow IRE'deki bağımlı tanımlama gibi: ağ adaptörü bağlı olduğu CI'ın içinde MAC ile, uygulama üzerinde çalıştığı
sunucunun içinde adla tekildir. Aynı MAC iki farklı sunucuda iki ayrı adaptördür; anahtar hiçbir zaman küresel dizine
girmez. Anahtar kural girdisinin öznitelikleri birleştirilerek kurulur; MAC biçimden bağımsız, metin ASCII'ye katlanmış
küçük harfle karşılaştırılır.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..normalize import ascii_fold, normalize_mac
from .rules import IdentificationRule

__all__ = ["dependent_keys", "entry_signature"]

_MAC_ATTRS = frozenset({"mac_address"})


def entry_signature(attributes: tuple[str, ...]) -> str:
    """Girdinin kalıcı adı (ör. ``mac_address`` veya ``namespace+name``); dizinde anahtar türü olarak saklanır."""
    return "+".join(attributes)


def _norm(attr: str, value: Any) -> str | None:
    if value in (None, "", []):
        return None
    if attr in _MAC_ATTRS:
        mac = normalize_mac(value)  # yerel yönetimli (zayıf) MAC da ana CI bağlamında ayırt edicidir
        return mac.value if mac else None
    text = ascii_fold(str(value)).strip().lower()
    return text[:512] or None


def dependent_keys(rule: IdentificationRule, attributes: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Kural girdilerinden öncelik sırasıyla (imza, değer) çiftleri; bir özniteliği eksik girdi atlanır."""
    out: list[tuple[str, str]] = []
    for entry in rule.ordered():
        parts = [_norm(a, attributes.get(a)) for a in entry.attributes]
        if all(parts):
            out.append((entry_signature(entry.attributes), "\x1f".join(p for p in parts if p)))
    return out

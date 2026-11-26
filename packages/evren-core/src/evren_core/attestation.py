"""Sahiplik onayı (F1-17, ADR-0027): politika dosyası, durum türetme, kapsam SQL'i. Saf mantık, G/Ç yok.

* Politika ``metadata/attestation.yaml``: sıralı ``policies`` (``applies_to`` alt sınıflara kalıtılır, isteğe bağlı
  ``where`` = kök sınıftaki seçim alanı → izinli değerler, ``period_days``); CI'a ilk uyan politika geçerlidir.
* Onay verebilen kişiler ``owner_fields`` alanlarındaki kullanıcılardır (varsayılan ``owned_by``, ``managed_by``).
* Durum (``state``): ``out_of_scope``, ``not_reviewed``, ``valid``, ``due_soon``, ``expired``, ``rejected``.
  ``expired`` okuma anında türetilir; uzlaştırıcının dönemsel taraması alanı ``Not Yet Reviewed`` yapar.
* Emekli CI (yaşam döngüsü ``End of Life`` veya ``install_status`` 7) hiçbir politikada kapsamda değildir.
* Python (``state``) ve SQL (``state_sql``) aynı kuralı uygular; testler ikisini karşılaştırır.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .classes import ClassHierarchy

__all__ = ["ATTESTED", "DISABLED", "NOT_REVIEWED", "REASONS_TR", "REJECTED", "STATE_TR", "AttestationError",
           "AttestationPolicy", "Policy", "validate_attestation"]

ATTESTED, REJECTED, NOT_REVIEWED = "Attested", "Rejected", "Not Yet Reviewed"
STATE_TR = {"out_of_scope": "Kapsam dışı", "not_reviewed": "Henüz incelenmedi", "valid": "Onaylı",
            "due_soon": "Süresi yaklaşıyor", "expired": "Süresi doldu", "rejected": "Reddedildi"}
#: Ret nedenleri (ADR-0027 §3)
REASONS_TR = {"not_owner": "Bu CI'ın sorumlusu ben değilim", "not_exists": "Bu CI artık yok",
              "data_wrong": "Kayıttaki bilgiler yanlış"}
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,79}$")
_KEYS = {"version", "owner_fields", "reminder_days", "policies"}
_POLICY_KEYS = {"name", "label_tr", "applies_to", "where", "period_days"}


class AttestationError(ValueError):
    """Politika dosyası geçersiz."""


@dataclass(frozen=True)
class Policy:
    name: str
    label_tr: str
    applies_to: tuple[str, ...]
    where: Mapping[str, frozenset[str]]
    period: timedelta

    def matches(self, class_name: str, attrs: Mapping[str, Any], hierarchy: ClassHierarchy) -> bool:
        if class_name not in hierarchy or _retired(attrs):
            return False
        ancestors = set(hierarchy.ancestors(class_name))
        if not any(c in ancestors for c in self.applies_to):
            return False
        return all(str(attrs.get(f) or "") in allowed for f, allowed in self.where.items())

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label_tr, "period_days": self.period.days,
                "applies_to": list(self.applies_to), "where": {k: sorted(v) for k, v in self.where.items()}}


@dataclass(frozen=True)
class AttestationPolicy:
    policies: tuple[Policy, ...]
    owner_fields: tuple[str, ...] = ("owned_by", "managed_by")
    reminder: timedelta = timedelta(days=30)

    @property
    def enabled(self) -> bool:
        return bool(self.policies)

    @classmethod
    def from_document(cls, doc: Mapping[str, Any] | None) -> AttestationPolicy:
        """``metadata/attestation.yaml`` → politika nesnesi. Belge yoksa onay kapalıdır (``DISABLED``)."""
        if not doc:
            return DISABLED
        errors = _structural_errors(doc)
        if errors:
            raise AttestationError("; ".join(errors))
        return cls(
            policies=tuple(Policy(str(p["name"]), str(p.get("label_tr") or p["name"]), tuple(p["applies_to"]),
                                  {str(k): frozenset(str(x) for x in v) for k, v in (p.get("where") or {}).items()},
                                  timedelta(days=int(p["period_days"])))
                           for p in doc.get("policies") or ()),
            owner_fields=tuple(doc.get("owner_fields") or ("owned_by", "managed_by")),
            reminder=timedelta(days=int(doc.get("reminder_days", 30))))

    def policy_for(self, class_name: str, attrs: Mapping[str, Any], hierarchy: ClassHierarchy) -> Policy | None:
        return next((p for p in self.policies if p.matches(class_name, attrs, hierarchy)), None)

    def owners(self, attrs: Mapping[str, Any]) -> list[str]:
        """CI'ın onay verebilecek sorumluları (sys_user sys_id), alan sırasıyla, tekrarsız."""
        out: list[str] = []
        for f in self.owner_fields:
            v = attrs.get(f)
            if v and str(v) not in out:
                out.append(str(v))
        return out

    def state(self, class_name: str, attrs: Mapping[str, Any], hierarchy: ClassHierarchy, now: datetime) -> dict[str, Any]:
        """CI'ın onay durumu: politika, durum kodu, son geçerlilik, sorumlular. ``attrs`` golden record değerleridir
        (``attestation_status``, ``attested_by``, ``attested_date`` datetime veya None)."""
        policy = self.policy_for(class_name, attrs, hierarchy)
        owners = self.owners(attrs)
        status = attrs.get("attestation_status")
        when = attrs.get("attested_date")
        by = attrs.get("attested_by")
        valid_until = when + policy.period if policy and isinstance(when, datetime) and status == ATTESTED else None
        if policy is None:
            code = "out_of_scope"
        elif status == REJECTED:
            code = "rejected"
        elif status != ATTESTED or valid_until is None:
            code = "not_reviewed"
        elif valid_until <= now or not by or str(by) not in owners:
            code = "expired"  # süre doldu veya onaylayan artık sorumlu değil
        elif valid_until - now <= self.reminder:
            code = "due_soon"
        else:
            code = "valid"
        return {"state": code, "state_label": STATE_TR[code], "policy": policy.as_json() if policy else None,
                "valid_until": valid_until, "owners": owners, "orphan": policy is not None and not owners}

    # ------------------------------------------------------------------ SQL (parametreli; adlar doğrulanmış)
    def period_sql(self, hierarchy: ClassHierarchy, alias: str = "ci") -> tuple[str, list[Any]]:
        """CI'a uyan politikanın süresini (gün) veren ``CASE`` ifadesi; kapsam dışında NULL. Sınıf listesi alt
        sınıflarla genişletilir; alan adları yalnızca doğrulanmış tanımlayıcılardır, değerler parametredir."""
        if not _IDENT.match(alias):
            raise AttestationError("geçersiz takma ad")
        if not self.policies:
            return "NULL::integer", []
        parts: list[str] = []
        params: list[Any] = []
        for p in self.policies:
            classes = sorted({d for c in p.applies_to if c in hierarchy for d in hierarchy.descendants(c)})
            cond = [f"{alias}.sys_class_name = ANY(%s)",
                    f"COALESCE({alias}.life_cycle_stage, '') <> 'End of Life'",
                    f"COALESCE({alias}.install_status, 0) <> 7"]
            params.append(classes)
            for f, allowed in sorted(p.where.items()):
                if not _IDENT.match(f):
                    raise AttestationError(f"geçersiz alan adı {f}")
                cond.append(f"{alias}.{f} = ANY(%s)")
                params.append(sorted(allowed))
            parts.append(f"WHEN {' AND '.join(cond)} THEN %s")
            params.append(p.period.days)
        return f"(CASE {' '.join(parts)} END)", params

    def owners_sql(self, alias: str = "ci") -> str:
        """Sorumlu alanlarının dizisi (boşlar dahil); ``%s = ANY(...)`` ile kişi denetimi için."""
        if not _IDENT.match(alias) or not all(_IDENT.match(f) for f in self.owner_fields):
            raise AttestationError("geçersiz alan adı")
        return "ARRAY[" + ", ".join(f"{alias}.{f}::text" for f in self.owner_fields) + "]::text[]"


    def state_sql(self, alias: str = "ci", period: str = "period_days") -> str:
        """``state`` ile aynı kuralı uygulayan SQL ifadesi (durum kodu). ``period`` sütunu ``period_sql``'in alt
        sorgudaki takma adıdır; iki ``%s`` parametresi "şimdi"dir (sırayla)::

            SELECT ..., {state} FROM (SELECT ci.*, {period_sql} AS period_days FROM cmdb.cmdb_ci ci) ci
        """
        if not _IDENT.match(alias) or not _IDENT.match(period):
            raise AttestationError("geçersiz takma ad")
        owners = self.owners_sql(alias)
        a = alias
        until = f"{a}.attested_date + make_interval(days => {a}.{period})"
        return (f"(CASE WHEN {a}.{period} IS NULL THEN 'out_of_scope' "
                f"WHEN {a}.attestation_status = 'Rejected' THEN 'rejected' "
                f"WHEN {a}.attestation_status IS DISTINCT FROM 'Attested' OR {a}.attested_date IS NULL "
                f"THEN 'not_reviewed' "
                f"WHEN {until} <= %s OR NOT COALESCE({a}.attested_by::text = ANY({owners}), false) THEN 'expired' "
                f"WHEN {until} - make_interval(days => {int(self.reminder.days)}) <= %s THEN 'due_soon' "
                f"ELSE 'valid' END)")

    def orphan_sql(self, alias: str = "ci") -> str:
        """Sorumlu alanlarının hepsi boş mu (kapsam denetimi ayrıca yapılır)."""
        return "(" + " AND ".join(f"{alias}.{f} IS NULL" for f in self.owner_fields) + ")"


def _retired(attrs: Mapping[str, Any]) -> bool:
    return attrs.get("life_cycle_stage") == "End of Life" or str(attrs.get("install_status") or "") == "7"


DISABLED = AttestationPolicy(policies=())


def _structural_errors(doc: Mapping[str, Any]) -> list[str]:
    errors = [f"bilinmeyen anahtar {k}" for k in doc if k not in _KEYS]
    if doc.get("version") != 1:
        errors.append("version 1 olmalı")
    owner_fields = doc.get("owner_fields", ["owned_by", "managed_by"])
    if not isinstance(owner_fields, list) or not owner_fields or not all(isinstance(f, str) and _IDENT.match(f)
                                                                           for f in owner_fields):
        errors.append("owner_fields boş olmayan alan adı listesi olmalı")
    reminder = doc.get("reminder_days", 30)
    if not isinstance(reminder, int) or not 1 <= reminder <= 180:
        errors.append("reminder_days 1–180 arasında olmalı")
    policies = doc.get("policies")
    if not isinstance(policies, list) or not policies:
        return [*errors, "policies boş olmayan liste olmalı"]
    names: set[str] = set()
    for i, p in enumerate(policies):
        if not isinstance(p, Mapping):
            errors.append(f"policies[{i}] eşleme olmalı")
            continue
        errors += [f"policies[{i}]: bilinmeyen anahtar {k}" for k in p if k not in _POLICY_KEYS]
        name = p.get("name")
        if not isinstance(name, str) or not _IDENT.match(name) or name in names:
            errors.append(f"policies[{i}]: ad tanımlayıcı ve benzersiz olmalı")
        names.add(str(name))
        if not isinstance(p.get("applies_to"), list) or not p["applies_to"]:
            errors.append(f"policies[{i}]: applies_to boş olmayan sınıf listesi olmalı")
        period = p.get("period_days")
        if not isinstance(period, int) or not 30 <= period <= 730:
            errors.append(f"policies[{i}]: period_days 30–730 arasında olmalı")
        where = p.get("where") or {}
        if not isinstance(where, Mapping) or not all(isinstance(v, list) and v for v in where.values()):
            errors.append(f"policies[{i}]: where alan → boş olmayan değer listesi olmalı")
        elif not all(isinstance(k, str) and _IDENT.match(k) for k in where):
            errors.append(f"policies[{i}]: where alan adı geçersiz")
    return errors


def validate_attestation(doc: Mapping[str, Any] | None, *, hierarchy: ClassHierarchy,
                         fields_of: Callable[[str], Mapping[str, Any]],
                         choice_values: Callable[[str], Iterable[str] | None],
                         products: Sequence[str] = ()) -> list[str]:
    """Politika dosyasını sözlüğe karşı doğrular: sınıflar, sorumlu alanları (sys_user referansı), koşul alanları
    (kök sınıfta sütun, seçim listesi değerleri), ürün adı yok (kural 8)."""
    if not doc:
        return []
    errors = [f"attestation: {e}" for e in _structural_errors(doc)]
    base = fields_of("cmdb_ci") if "cmdb_ci" in hierarchy else {}
    for f in doc.get("owner_fields") or []:
        spec = base.get(f)
        if not spec or spec.get("type") != "reference" or spec.get("reference") != "sys_user":
            errors.append(f"attestation.owner_fields: {f} cmdb_ci'da sys_user başvurusu olmalı")
    for p in doc.get("policies") or []:
        if not isinstance(p, Mapping):
            continue
        name = p.get("name")
        errors += [f"attestation.{name}: tanımsız sınıf {c}" for c in p.get("applies_to") or [] if c not in hierarchy]
        for f, values in (p.get("where") or {}).items():
            spec = base.get(f)
            if not spec or spec.get("storage") != "column":
                errors.append(f"attestation.{name}: koşul alanı {f} cmdb_ci'da sütun olarak saklanan alan olmalı")
                continue
            known = choice_values(f)
            if known is not None:
                known = set(known)
                errors += [f"attestation.{name}: {f} için tanımsız değer {v}" for v in values or [] if str(v) not in known]
    text = " ".join(_strings(doc)).lower()
    errors += [f"attestation: ürün adı içeremez ({p})" for p in products
               if p and re.search(rf"(?<![\w]){re.escape(p.lower())}(?![\w])", text)]
    return errors


def _strings(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [s for k, v in value.items() for s in (str(k), *_strings(v))]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return [str(value)]

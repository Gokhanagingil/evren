"""ServiceNow *encoded query* (``sysparm_query``) ayrıştırıcı ve güvenli SQL derleyici.

Amaç: ServiceNow Table API'sini kullanan mevcut betik ve entegrasyonların
(ör. GRC uygulaması, raporlar, CI seçici) Evren'e minimum değişiklikle
yönlendirilebilmesi.

Desteklenen sözdizimi
---------------------
``a=1^b=2``            VE
``a=1^ORb=2``          VEYA (bir önceki koşulla gruplanır: ``x^a^ORb`` = x VE (a VEYA b))
``a=1^NQb=2``          yeni sorgu grubu (gruplar VEYA ile birleşir)
``^ORDERBYname`` / ``^ORDERBYDESCname``
``^^``                 değer içinde ``^`` karakteri

Operatörler: ``= != LIKE NOT LIKE STARTSWITH ENDSWITH IN NOT IN ISEMPTY ISNOTEMPTY
> >= < <= BETWEEN ANYTHING INSTANCEOF``

Güvenlik: değerler her zaman parametre olarak bağlanır; alan adları yalnızca
çözümleyicinin (metadata sözlüğü) tanıdığı ifadelere dönüşür. ``javascript:``
değerleri reddedilir — sunucu tarafında betik çalıştırılmaz.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "OPERATORS",
    "Condition",
    "EncodedQuery",
    "FieldRef",
    "QueryError",
    "compile_sql",
    "parse",
]

OPERATORS = (
    "ISNOTEMPTY",
    "ISEMPTY",
    "INSTANCEOF",
    "NOT LIKE",
    "NOT IN",
    "STARTSWITH",
    "ENDSWITH",
    "BETWEEN",
    "ANYTHING",
    "LIKE",
    "IN",
    "!=",
    ">=",
    "<=",
    "=",
    ">",
    "<",
)
_COND = re.compile(r"^([a-z0-9_.]+?)(" + "|".join(re.escape(o) for o in OPERATORS) + r")(.*)$", re.S)
_ESC = "\x00"
MAX_CONDITIONS = 200
MAX_IN_VALUES = 1000


class QueryError(ValueError):
    """Geçersiz veya desteklenmeyen sorgu (HTTP 400'e çevrilir)."""


@dataclass(frozen=True)
class Condition:
    field: str
    operator: str
    value: str


@dataclass(frozen=True)
class EncodedQuery:
    #: VEYA ile bağlı gruplar → VE ile bağlı terimler → VEYA ile bağlı koşullar
    groups: tuple[tuple[tuple[Condition, ...], ...], ...]
    order_by: tuple[tuple[str, bool], ...] = ()

    @property
    def conditions(self) -> list[Condition]:
        return [c for g in self.groups for t in g for c in t]


def parse(text: str | None) -> EncodedQuery:
    if not text:
        return EncodedQuery(())
    tokens = [t.replace(_ESC, "^") for t in text.replace("^^", _ESC).split("^")]
    groups: list[list[list[Condition]]] = [[]]
    order_by: list[tuple[str, bool]] = []
    count = 0
    for token in tokens:
        if token in ("", "EQ"):
            continue
        if token.startswith("ORDERBYDESC"):
            order_by.append((_field(token[len("ORDERBYDESC"):]), True))
            continue
        if token.startswith("ORDERBY"):
            order_by.append((_field(token[len("ORDERBY"):]), False))
            continue
        is_or = token.startswith("OR")
        is_nq = token.startswith("NQ")
        body = token[2:] if (is_or or is_nq) else token
        condition = _condition(body)
        count += 1
        if count > MAX_CONDITIONS:
            raise QueryError(f"Sorgu en fazla {MAX_CONDITIONS} koşul içerebilir")
        if is_nq:
            groups.append([[condition]])
        elif is_or:
            if not groups[-1]:
                raise QueryError("^OR ile başlayan koşul öncesinde koşul yok")
            groups[-1][-1].append(condition)
        else:
            groups[-1].append([condition])
    frozen = tuple(tuple(tuple(term) for term in group) for group in groups if group)
    return EncodedQuery(frozen, tuple(order_by))


def _field(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9_.]+", name):
        raise QueryError(f"Geçersiz alan adı: {name!r}")
    return name


def _condition(body: str) -> Condition:
    match = _COND.match(body)
    if not match:
        raise QueryError(f"Koşul ayrıştırılamadı: {body!r}")
    field, op, value = match.groups()
    if value.strip().lower().startswith("javascript:"):
        raise QueryError("javascript: değerleri desteklenmez (sunucu tarafında betik çalıştırılmaz)")
    if op in ("ISEMPTY", "ISNOTEMPTY", "ANYTHING") and value:
        raise QueryError(f"{op} operatörü değer almaz")
    return Condition(field, op, value)


# ---------------------------------------------------------------------------
# SQL derleme
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldRef:
    """Çözümleyicinin döndürdüğü güvenli SQL ifadesi ve alan tipi.

    ``type``: ``string``, ``integer``, ``decimal``, ``boolean``, ``glide_date_time``,
    ``reference``, ``choice`` (metin), ``sys_class_name``.
    """

    sql: str
    type: str = "string"


FieldResolver = Callable[[str], FieldRef]
ClassResolver = Callable[[str], Iterable[str]]

_TEXT_TYPES = {"string", "reference", "choice", "sys_class_name", "ip_address", "mac_address", "email", "url"}


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _convert(value: str, ftype: str) -> Any:
    try:
        if ftype == "integer":
            return int(value)
        if ftype == "decimal":
            return Decimal(value)
        if ftype == "boolean":
            low = value.lower()
            if low not in ("true", "false", "1", "0"):
                raise ValueError(value)
            return low in ("true", "1")
        if ftype == "glide_date_time":
            fmt = "%Y-%m-%d %H:%M:%S" if " " in value else "%Y-%m-%d"
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
    except (ValueError, InvalidOperation) as exc:
        raise QueryError(f"{value!r} değeri {ftype} tipine çevrilemedi") from exc
    return value


def compile_sql(
    query: EncodedQuery,
    resolve: FieldResolver,
    class_descendants: ClassResolver | None = None,
) -> tuple[str, list[Any], str]:
    """``(where_sql, params, order_sql)`` döndürür; parametre yer tutucusu ``%s``."""
    params: list[Any] = []

    def cond_sql(c: Condition) -> str:
        ref = resolve(c.field)
        col, ftype = ref.sql, ref.type
        text = ftype in _TEXT_TYPES
        op, v = c.operator, c.value
        if op == "ANYTHING":
            return "TRUE"
        if op == "ISEMPTY":
            return f"({col} IS NULL OR {col}::text = '')"
        if op == "ISNOTEMPTY":
            return f"({col} IS NOT NULL AND {col}::text <> '')"
        if op == "INSTANCEOF":
            if ftype != "sys_class_name" or class_descendants is None:
                raise QueryError("INSTANCEOF yalnızca sys_class_name için kullanılabilir")
            params.append(sorted(set(class_descendants(v)) | {v}))
            return f"{col} = ANY(%s)"
        if op in ("LIKE", "NOT LIKE", "STARTSWITH", "ENDSWITH"):
            pattern = {"LIKE": "%{}%", "NOT LIKE": "%{}%", "STARTSWITH": "{}%", "ENDSWITH": "%{}"}[op]
            params.append(pattern.format(_like_escape(v)))
            if op == "NOT LIKE":
                return f"({col} IS NULL OR {col}::text NOT ILIKE %s)"
            return f"{col}::text ILIKE %s"
        if op in ("IN", "NOT IN"):
            values = [x for x in v.split(",") if x != ""]
            if len(values) > MAX_IN_VALUES:
                raise QueryError(f"IN listesi en fazla {MAX_IN_VALUES} değer içerebilir")
            if text:
                params.append([x.lower() for x in values])
                expr = f"lower({col}) = ANY(%s)"
            else:
                params.append([_convert(x, ftype) for x in values])
                expr = f"{col} = ANY(%s)"
            return expr if op == "IN" else f"({col} IS NULL OR NOT ({expr}))"
        if op == "BETWEEN":
            if "@" not in v:
                raise QueryError("BETWEEN değeri 'alt@üst' biçiminde olmalı")
            lo, hi = v.split("@", 1)
            params.extend([_convert(lo, ftype), _convert(hi, ftype)])
            return f"{col} BETWEEN %s AND %s"
        if op in ("=", "!="):
            if text:
                params.append(v.lower())
                expr = f"lower({col}) = %s"
            else:
                params.append(_convert(v, ftype))
                expr = f"{col} = %s"
            return expr if op == "=" else f"({col} IS NULL OR NOT ({expr}))"
        if op in (">", ">=", "<", "<="):
            params.append(_convert(v, ftype))
            return f"{col} {op} %s"
        raise QueryError(f"Desteklenmeyen operatör: {op}")  # pragma: no cover

    group_sql: list[str] = []
    for group in query.groups:
        terms = []
        for term in group:
            parts = [cond_sql(c) for c in term]
            terms.append(parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")")
        group_sql.append(" AND ".join(terms))
    if not group_sql:
        where = "TRUE"
    elif len(group_sql) == 1:
        where = group_sql[0]
    else:
        where = " OR ".join(f"({g})" for g in group_sql)

    order = ", ".join(f"{resolve(f).sql} {'DESC' if desc else 'ASC'}" for f, desc in query.order_by)
    return where, params, order

"""ServiceNow uyumlu Table API — okuma (U9.3a, F2-05, ADR-0044).

ServiceNow'dan CMDB okuyan entegrasyonlar (raporlama, otomasyon, CI seçiciler, GRC Platformu) yalnızca adres ve kimlik
değiştirerek Evren'i okuyabilir:

* ``GET /api/now/table/{tablo}``: ``sysparm_query`` (encoded query; ``evren_core.encoded_query`` ile güvenli SQL),
  ``sysparm_fields``, ``sysparm_limit`` (varsayılan ve en çok 10000), ``sysparm_offset``,
  ``sysparm_exclude_reference_link``. Sonuçsuz sorgu 200 ve boş dizi döner (ServiceNow v2 davranışı; ``/api/now/v2/``
  yolu da aynıdır).
  Üst sınıf tablosu alt sınıfların kayıtlarını da döner (ServiceNow tablo kalıtımı); alanlar istenen tablonunkilerdir.
* ``GET /api/now/table/{tablo}/{sys_id}``.
* Tablolar: CMDB sınıfları (``cmdb_ci`` soyağacı), ``cmdb_rel_ci``, ``cmdb_rel_type`` ve referans tabloları
  (``sys_user``, ``sys_user_group``, ``cmn_location``…); referans bağlantıları böylece boşa düşmez.

Yanıt ServiceNow biçimindedir: ``{"result": …}``; değerler metin (boş değer ``""``), zaman UTC
``YYYY-MM-DD hh:mm:ss``, referans ``{"link", "value"}``; ``X-Total-Count`` ve ``Link`` (first/prev/next/last)
başlıkları; hata ``{"error": {"message", "detail"}, "status": "failure"}``.

Güvenlik:

* Okuma izni tabloya göre; reddedilen alt sınıfın kayıtları üst tablo sorgusunda da yoktur.
* Hassasiyet sınırını aşan alan yanıtta yoktur ve **sorguda da kullanılamaz** (görünmeyen değer süzgeçle sezdirilemez).
* İlişki, iki ucu da okunabilen CI'lar arasındaysa görünür. Kişi e-postası yalnızca gizli düzeyi görebilene.
* ``javascript:`` değerleri reddedilir; dot-walk desteklenmez; alan adları yalnızca sözlükten çözülür.
* Kimlik: oturum veya (Entra'da) taşıyıcı belirteç. Yazma yoktur (IRE uyumlu yazma ayrı adım).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from evren_core.encoded_query import FieldRef, QueryError, compile_sql, parse
from evren_core.metadata import metadata_field_resolver
from evren_core.reference import REFERENCE_TABLES

from ..deps import Ctx, context_for, principal_from_request
from ..errors import ApiError
from .mcp import bearer_principal

router = APIRouter(prefix="/api/now", tags=["servicenow-uyumlu"])

MAX_LIMIT = 10_000  # ServiceNow'un varsayılanı; sayfalamayan eski betik veriyi sessizce eksik almasın
_HEX = frozenset("0123456789abcdef")
# alan → (SQL ifadesi, sözlük tipi, referans tablosu)
Field = tuple[str, str, str | None]
_CI_SYSTEM = ("sys_id", "sys_class_name", "sys_created_on", "sys_updated_on", "sys_created_by", "sys_updated_by",
              "sys_mod_count")
_REL_FIELDS: dict[str, Field] = {
    "sys_id": ("r.sys_id", "string", None), "parent": ("r.parent", "reference", "cmdb_ci"),
    "child": ("r.child", "reference", "cmdb_ci"), "type": ("r.type", "reference", "cmdb_rel_type"),
    "sys_created_on": ("r.sys_created_on", "glide_date_time", None),
    "sys_updated_on": ("r.sys_updated_on", "glide_date_time", None),
    "sys_mod_count": ("r.sys_mod_count", "integer", None)}
_REL_TYPE_FIELDS: dict[str, Field] = {
    "sys_id": ("t.sys_id", "string", None), "name": ("t.name", "string", None),
    "parent_descriptor": ("t.parent_descriptor", "string", None),
    "child_descriptor": ("t.child_descriptor", "string", None)}
# referans tablolarında sys_id sütunlarının gösterdiği tablo (ServiceNow sözlüğü)
_REF_TARGETS = {("sys_user", "department"): "cmn_department", ("sys_user_group", "manager"): "sys_user",
                ("cmn_location", "parent"): "cmn_location", ("cmn_department", "company"): "core_company",
                ("cmn_cost_center", "manager"): "sys_user", ("cmn_cost_center", "parent"): "cmn_cost_center",
                ("cmdb_model", "manufacturer"): "core_company"}
_REF_TYPES = {"text": "string", "bool": "boolean", "datetime": "glide_date_time", "choice": "choice",
              "sysid": "reference"}
_CONFIDENTIAL = {("sys_user", "email")}  # kişisel veri: yalnızca gizli düzeyi görebilene (KVKK, en az veri)


def _err(status: int, message: str, detail: str) -> ApiError:
    """``/api/now/`` altındaki hata ServiceNow biçimine çevrilir (``errors.py``): ileti + ayrıntı."""
    return ApiError(status, "servicenow_uyumlu", message, [detail])


def sn_context(request: Request) -> Iterator[Ctx]:
    """Oturum çerezi veya taşıyıcı belirteç: makine entegrasyonları ServiceNow'daki gibi ``Authorization`` başlığıyla
    gelir. Çerezle gelen istek yalnızca okur (GET); başka kökenin okuması tarayıcının aynı köken kuralıyla engellenir."""
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        principal = bearer_principal(request, purpose="ServiceNow uyumlu API")
    else:
        principal = principal_from_request(request)
    yield from context_for(request, principal)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        v = value if value.tzinfo else value.replace(tzinfo=UTC)
        return v.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


class _Plan:
    """İstenen tablonun planı: görülebilir alanlar, kaynak, temel süzgeç (yetki), sıralama anahtarı."""

    def __init__(self, ctx: Ctx, table: str) -> None:
        self.md = md = ctx.state.metadata
        self.base_sql, self.base_params = "TRUE", []
        self.fields: dict[str, Field]
        if table in ("cmdb_rel_ci", "cmdb_rel_type"):
            self._need(ctx, "cmdb_rel_ci")
            if table == "cmdb_rel_type":
                self.fields, self.source, self.key = dict(_REL_TYPE_FIELDS), "cmdb.cmdb_rel_type t", "t.sys_id"
                return
            readable = self._readable(ctx, "cmdb_ci")
            self.fields, self.source, self.key = dict(_REL_FIELDS), "cmdb.cmdb_rel_ci r", "r.sys_id"
            self.base_sql = ("EXISTS (SELECT 1 FROM cmdb.cmdb_ci p WHERE p.sys_id = r.parent AND p.sys_class_name = "
                             "ANY(%s)) AND EXISTS (SELECT 1 FROM cmdb.cmdb_ci q WHERE q.sys_id = r.child AND "
                             "q.sys_class_name = ANY(%s))")
            self.base_params = [readable, readable]
            return
        if table in REFERENCE_TABLES:
            ref = REFERENCE_TABLES[table]
            grant = self._need(ctx, "cmdb_ci")  # referans araması ile aynı izin (/api/v1/ref)
            self.source, self.key = f'ref."{ref.db_table}" x', "x.sys_id"
            self.fields = {"sys_id": ("x.sys_id", "string", None)}
            for col, ctype in ref.columns.items():
                if (table, col) in _CONFIDENTIAL and not grant.can_see("confidential"):
                    continue
                target = _REF_TARGETS.get((table, col))
                self.fields[col] = (f'x."{col}"', _REF_TYPES[ctype] if target or ctype != "sysid" else "string", target)
            self.fields["sys_updated_on"] = ("x.sys_updated_on", "glide_date_time", None)
            return
        if table not in md.hierarchy or "cmdb_ci" not in md.hierarchy.ancestors(table):
            raise _err(400, f"Invalid table {table}",
                          "Okunabilen tablolar: CMDB sınıfları, cmdb_rel_ci, cmdb_rel_type ve referans tabloları.")
        grant = self._need(ctx, table)
        resolve = metadata_field_resolver(md, table, "c")
        self.fields = {}
        for name in _CI_SYSTEM:
            ref_ = resolve(name)
            self.fields[name] = (ref_.sql, ref_.type, None)
        for name, fdef in md.fields_of(table).items():
            if grant.can_see(fdef.get("sensitivity", "internal")):
                ref_ = resolve(name)
                target = fdef.get("reference") if fdef["type"] == "reference" else None
                self.fields[name] = (ref_.sql, ref_.type, target)
        self.source, self.key = "cmdb.cmdb_ci c", "c.sys_id"
        self.base_sql, self.base_params = "c.sys_class_name = ANY(%s)", [self._readable(ctx, table)]

    @staticmethod
    def _need(ctx: Ctx, resource: str) -> Any:
        grant = ctx.grant(resource, "read")
        if not grant.allowed:
            ctx.security_event(f"read:{resource}", "deny", resource, {"api": "sn_table"})
            raise _err(403, "User Not Authorized", "Bu tabloyu okuma yetkiniz yok.")
        return grant

    def _readable(self, ctx: Ctx, table: str) -> list[str]:
        """Tablo ve alt sınıfları; okuması reddedilen alt sınıf (``deny``) çıkarılır."""
        return sorted(c for c in self.md.hierarchy.descendants(table) | {table} if ctx.grant(c, "read").allowed)

    def resolver(self, name: str) -> FieldRef:
        if "." in name:
            raise QueryError(f"Dot-walk desteklenmez: {name}")
        f = self.fields.get(name)
        if f is None:
            raise QueryError(f"'{name}' alanı yok veya görme yetkiniz yok")
        return FieldRef(f[0], f[1])

    def descendants(self, cls: str) -> set[str]:
        if cls not in self.md.hierarchy:
            raise QueryError(f"Bilinmeyen sınıf: {cls}")
        return self.md.hierarchy.descendants(cls)

    def pick(self, raw: str | None) -> list[str]:
        if not raw:
            return list(self.fields)
        wanted = [f.strip() for f in raw.split(",") if f.strip()]
        if any("." in f for f in wanted):
            raise _err(400, "Dot-walk desteklenmez", "sysparm_fields yalnızca tablonun kendi alanlarını alır.")
        chosen = list(dict.fromkeys(f for f in wanted if f in self.fields))  # ServiceNow gibi: bilinmeyen alan atlanır
        return chosen or ["sys_id"]

    def select(self, ctx: Ctx, request: Request, *, where: str, params: list[Any], order: str, fields: list[str],
               limit: int, offset: int, plain: bool) -> tuple[list[dict[str, Any]], int]:
        cols = ", ".join(f'{self.fields[f][0]} AS "{f}"' for f in fields)
        order_sql = f"{order}, {self.key}" if order else self.key
        # alan ifadeleri ve kaynak sözlükten/sabitten; kullanıcı değerleri yalnızca parametre
        query = (f"SELECT {cols}, count(*) OVER () FROM {self.source} WHERE ({self.base_sql}) "  # noqa: S608  # nosec B608
                 f"AND ({where}) ORDER BY {order_sql} LIMIT %s OFFSET %s")
        rows = ctx.conn.execute(query, (*self.base_params, *params, limit, offset)).fetchall()
        base = str(request.base_url).rstrip("/")
        out = []
        for row in rows:
            rec: dict[str, Any] = {}
            for i, f in enumerate(fields):
                _sql, ftype, target = self.fields[f]
                value = row[i]
                if ftype == "reference" and target and value not in (None, "") and not plain:
                    rec[f] = {"link": f"{base}/api/now/table/{target}/{value}", "value": str(value)}
                else:
                    rec[f] = _text(value)
            out.append(rec)
        return out, int(rows[0][-1]) if rows else 0


def _links(request: Request, *, offset: int, limit: int, total: int) -> str:
    base = str(request.base_url).rstrip("/") + request.url.path
    params = dict(request.query_params)

    def link(off: int, rel: str) -> str:
        return f'<{base}?{urlencode({**params, "sysparm_offset": str(off), "sysparm_limit": str(limit)})}>;rel="{rel}"'

    last = max(0, (total - 1) // limit * limit)
    parts = [link(0, "first")]
    if offset > 0:
        parts.append(link(max(0, offset - limit), "prev"))
    if offset + limit < total:
        parts.append(link(offset + limit, "next"))
    parts.append(link(last, "last"))
    return ",".join(parts)


def _display_value(value: str) -> None:
    if value.lower() != "false":
        raise _err(400, "sysparm_display_value desteklenmiyor",
                   "Yalnızca gerçek değerler döner (sysparm_display_value=false).")


@router.get("/table/{table}")
@router.get("/v2/table/{table}", include_in_schema=False)
def table_list(table: str, request: Request, ctx: Ctx = Depends(sn_context),
               sysparm_query: str | None = Query(None, max_length=8000),
               sysparm_fields: str | None = Query(None, max_length=4000),
               sysparm_limit: int = Query(MAX_LIMIT, ge=1, le=MAX_LIMIT),
               sysparm_offset: int = Query(0, ge=0, le=10_000_000),
               sysparm_display_value: str = Query("false", max_length=10),
               sysparm_exclude_reference_link: bool = Query(False)) -> JSONResponse:
    _display_value(sysparm_display_value)
    plan = _Plan(ctx, table)
    fields = plan.pick(sysparm_fields)
    try:
        where, params, order = compile_sql(parse(sysparm_query), plan.resolver, plan.descendants)
    except QueryError as exc:
        raise _err(400, "Geçersiz sorgu", str(exc)) from None
    rows, total = plan.select(ctx, request, where=where, params=params, order=order, fields=fields,
                              limit=sysparm_limit, offset=sysparm_offset, plain=sysparm_exclude_reference_link)
    headers = {"X-Total-Count": str(total),
               "Link": _links(request, offset=sysparm_offset, limit=sysparm_limit, total=total)}
    return JSONResponse({"result": rows}, headers=headers)


@router.get("/table/{table}/{sys_id}")
@router.get("/v2/table/{table}/{sys_id}", include_in_schema=False)
def table_get(table: str, sys_id: str, request: Request, ctx: Ctx = Depends(sn_context),
              sysparm_fields: str | None = Query(None, max_length=4000),
              sysparm_display_value: str = Query("false", max_length=10),
              sysparm_exclude_reference_link: bool = Query(False)) -> JSONResponse:
    _display_value(sysparm_display_value)
    plan = _Plan(ctx, table)
    rows: list[dict[str, Any]] = []
    if len(sys_id) == 32 and set(sys_id) <= _HEX:
        rows, _ = plan.select(ctx, request, where=f"{plan.key} = %s", params=[sys_id], order="",
                              fields=plan.pick(sysparm_fields), limit=1, offset=0,
                              plain=sysparm_exclude_reference_link)
    if not rows:
        raise _err(404, "No Record found", "Kayıt yok veya erişim kuralı okumayı kısıtlıyor.")
    return JSONResponse({"result": rows[0]})

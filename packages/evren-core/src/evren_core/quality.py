"""Kalite ve uyum kuralları (U8.6, ADR-0037; F1-18, F1-26).

Bir kural, kapsamdaki (sınıf + koşul) CI'lardan hangilerinin **bulgu** olduğunu söyler. Kural türleri sabit bir
katalogdan gelir ve parametreleri burada doğrulanır; kural yönetim panelinden tanımlanır, görev çalışanı değerlendirir.
Bulgu sürerken sorumlusuna tek görev açılır, bulgu kapanınca görev kapanır (``gov.task``, ADR-0035).

Bu modül saftır: SQL'i üretir ama çalıştırmaz. Alan adları yalnızca metadata sözlüğünden çözülür, sınıflar
hiyerarşiden, roller kaynak rolü kaydından; değerler her zaman parametredir.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .governance import campaign_scope

__all__ = [
    "CHECK_KINDS",
    "DIMENSION_TR",
    "INTERVALS",
    "SEVERITY_TR",
    "TEMPLATES",
    "RulePlan",
    "compile_rule",
    "describe_details",
    "relation_violations",
]

#: Kural türleri: ne denetlenir. ``params`` her türün kendi alanlarıdır.
CHECK_KINDS: dict[str, dict[str, str]] = {
    "no_relation": {
        "label": "İlişkisi olmayan CI",
        "help": "Kapsamdaki CI, seçilen sınıflardan hiçbir CI ile ilişkili değilse bulgu olur. Ör. hiçbir uygulama "
                "veya sunucuyla ilişkisi olmayan veritabanı.",
    },
    "missing_fields": {
        "label": "Boş alan",
        "help": "Seçilen alanlardan biri boşsa bulgu olur. Ör. sahibi veya destek grubu boş üretim CI'ı.",
    },
    "not_seen_by_role": {
        "label": "Bir kaynak rolünce görülmeyen CI",
        "help": "CI bu roldeki hiçbir veri kaynağında yoksa bulgu olur. Ör. izlemede olmayan üretim sunucusu. Roldeki "
                "kaynakların son çalışması sağlıklı değilse kural sonuç vermez (bulgular değişmez).",
    },
    "matches_condition": {
        "label": "Koşula uyan CI",
        "help": "Kapsamdaki CI ek koşula da uyuyorsa bulgu olur. Ör. emekli olduğu hâlde çalışır görünen CI.",
    },
    "relation_not_allowed": {
        "label": "İzinli olmayan ilişki",
        "help": "CI'ın ebeveyn olduğu bir ilişki izinli ilişki kurallarına uymuyorsa (tip izinsiz veya yön ters) "
                "bulgu olur. Kaynaktan gelen ilişki atılmaz; burada raporlanır.",
    },
}
DIMENSION_TR = {"completeness": "Tamlık", "correctness": "Doğruluk", "compliance": "Uyum", "coverage": "Kapsam"}
SEVERITY_TR = {"low": "Düşük", "medium": "Orta", "high": "Yüksek", "critical": "Kritik"}
#: Değerlendirme sıklığı (saat)
INTERVALS = {1: "Her saat", 6: "Altı saatte bir", 24: "Her gün", 168: "Her hafta"}
MAX_LIST = 20

#: Panelde "şablondan başla" ile sunulan kurallar. Ürün adı içermez; sınıflar ve roller sözlükten.
TEMPLATES: list[dict[str, Any]] = [
    {"id": "iliskisiz-veritabani", "name": "Uygulama veya sunucuyla ilişkisi olmayan veritabanı",
     "description": "Veritabanı hiçbir uygulama, sunucu, iş uygulaması veya hizmetle ilişkili değil: kimin kullandığı "
                    "ve nerede çalıştığı bilinmiyor; etki analizi ve değişiklik değerlendirmesi eksik kalır.",
     "check_kind": "no_relation", "scope_class": "cmdb_ci_db_instance", "scope_query": "",
     "params": {"related_classes": ["cmdb_ci_appl", "cmdb_ci_computer", "cmdb_ci_business_app", "cmdb_ci_service"],
                "exclude_classes": ["cmdb_ci_db_instance"]},
     "dimension": "completeness", "severity": "medium",
     "task_title": "Veritabanının ilişkilerini tamamlayın",
     "task_instructions": "Veritabanını kullanan uygulamayı ve çalıştığı sunucuyu CI sayfasından ilişki olarak "
                          "ekleyin. Veritabanı artık kullanılmıyorsa \"Geçerli değil\" kararıyla nedenini yazın."},
    {"id": "sahipsiz-uretim", "name": "Sahibi veya destek grubu boş üretim CI'ı",
     "description": "Üretimdeki CI'ın sahibi veya destek grubu boş: olayda ve değişiklikte kime gidileceği belli değil.",
     "check_kind": "missing_fields", "scope_class": "cmdb_ci", "scope_query": "used_for=Production",
     "params": {"fields": ["owned_by", "support_group"]},
     "dimension": "compliance", "severity": "high",
     "task_title": "Sahip ve destek grubunu doldurun",
     "task_instructions": "CI'ın sahibini ve destek grubunu CI sayfasından girin."},
    {"id": "izlenmeyen-uretim-sunucusu", "name": "İzlenmeyen üretim sunucusu",
     "description": "Üretim sunucusu izleme rolündeki hiçbir kaynakta yok: arıza fark edilmeyebilir.",
     "check_kind": "not_seen_by_role", "scope_class": "cmdb_ci_server", "scope_query": "used_for=Production",
     "params": {"role": "Monitoring"}, "dimension": "coverage", "severity": "high",
     "task_title": "Sunucuyu izlemeye alın",
     "task_instructions": "Sunucuyu izleme sistemine ekleyin; izlenmesi gerekmiyorsa \"Geçerli değil\" ile nedenini "
                          "yazın."},
    {"id": "korumasiz-sunucu", "name": "Uç nokta koruması olmayan sunucu",
     "description": "Sunucu uç nokta güvenliği rolündeki hiçbir kaynakta yok.",
     "check_kind": "not_seen_by_role", "scope_class": "cmdb_ci_server", "scope_query": "",
     "params": {"role": "EndpointSecurity"}, "dimension": "coverage", "severity": "high",
     "task_title": "Uç nokta korumasını kurun",
     "task_instructions": "Sunucuya uç nokta koruma ajanını kurun veya kapsam dışıysa nedenini yazın."},
    {"id": "izinsiz-iliski", "name": "İzinli olmayan ilişki",
     "description": "CI'ın bir ilişkisi izinli ilişki kurallarına uymuyor (tip izinsiz veya yön ters).",
     "check_kind": "relation_not_allowed", "scope_class": "cmdb_ci", "scope_query": "", "params": {},
     "dimension": "correctness", "severity": "low", "open_task": False,
     "task_title": "İlişkiyi düzeltin", "task_instructions": "İlişkinin tipini veya yönünü düzeltin."},
]


@dataclass(frozen=True)
class RulePlan:
    """Bir kuralın değerlendirme planı (``c`` = cmdb.cmdb_ci).

    ``scope_*``: kapsam koşulu. ``match_*``: kapsam içinde bulgu koşulu. ``detail_sql``: bulgu başına ayrıntı
    (ör. boş alanlar dizisi; yoksa ``None``). ``relation_check``: eşleşen CI'ların ilişkileri ayrıca kurallarla
    denetlenir. ``depends_on_roles``: bu rollerin kaynakları sağlıklı değilse sonuç verilmez.
    """

    scope_sql: str
    scope_params: list[Any]
    match_sql: str
    match_params: list[Any]
    detail_sql: str | None = None
    detail_params: list[Any] = field(default_factory=list)
    relation_check: bool = False
    depends_on_roles: tuple[str, ...] = ()
    summary: str = ""

    @property
    def where(self) -> tuple[str, list[Any]]:
        """Kapsam ve bulgu koşulu birlikte."""
        return f"({self.scope_sql}) AND ({self.match_sql})", [*self.scope_params, *self.match_params]


def _str_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{name} bir ad listesi olmalı.")
    return [v.strip() for v in value]


def _classes(md: Any, names: Iterable[str]) -> list[str]:
    out: set[str] = set()
    for n in names:
        if n not in md.hierarchy:
            raise ValueError(f"Bilinmeyen sınıf: {n}")
        out |= md.hierarchy.descendants(n) | {n}
    return sorted(out)


def _label(md: Any, name: str) -> str:
    return next((c.get("label_tr") or c.get("label") or name for c in md.classes if c["name"] == name), name)


def _tr_lower(text: str) -> str:
    """Türkçe küçük harf (``İ`` → ``i``, ``I`` → ``ı``); Python'un ``lower()``'ı ``İ``'yi ``i̇`` yapar."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _tr_capital(text: str) -> str:
    head = {"i": "İ", "ı": "I"}.get(text[:1], text[:1].upper())
    return head + text[1:]


def _join_or(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " veya " + items[-1]


def _empty(expr: str) -> str:
    return f"({expr} IS NULL OR {expr}::text = '')"


def compile_rule(md: Any, scope_class: str, scope_query: str, check_kind: str, params: Mapping[str, Any]) -> RulePlan:
    """Kuralı doğrular ve SQL planına çevirir. Geçersiz kural ``ValueError`` (Türkçe neden)."""
    from .encoded_query import QueryError, compile_sql, parse
    from .metadata import metadata_field_resolver

    if check_kind not in CHECK_KINDS:
        raise ValueError(f"Bilinmeyen kural türü: {check_kind}")
    if not isinstance(params, Mapping):
        raise ValueError("Kural parametreleri bir nesne olmalı.")
    scope_sql, scope_params = campaign_scope(md, scope_class, scope_query)
    allowed = {"no_relation": {"related_classes", "exclude_classes", "rel_types"}, "missing_fields": {"fields"},
               "not_seen_by_role": {"role"}, "matches_condition": {"condition"}, "relation_not_allowed": set()}
    unknown = set(params) - allowed[check_kind]
    if unknown:
        raise ValueError(f"Bu kural türünde olmayan parametre: {', '.join(sorted(unknown))}")

    if check_kind == "no_relation":
        related = _str_list(params.get("related_classes"), "İlişkili sınıflar")
        exclude = _str_list(params.get("exclude_classes"), "Hariç sınıflar")
        rel_types = _str_list(params.get("rel_types"), "İlişki tipleri")
        known_types = {t["name"] for t in md.relationship_types}
        for t in rel_types:
            if t not in known_types:
                raise ValueError(f"Bilinmeyen ilişki tipi: {t}")
        conds, cparams = ["o.sys_id <> c.sys_id"], []
        if related:
            conds.append("o.sys_class_name = ANY(%s)")
            cparams.append(_classes(md, related))
        if exclude:
            conds.append("NOT (o.sys_class_name = ANY(%s))")
            cparams.append(_classes(md, exclude))
        if rel_types:
            conds.append("r.type IN (SELECT sys_id FROM cmdb.cmdb_rel_type WHERE name = ANY(%s))")
            cparams.append(rel_types)
        match = ("NOT EXISTS (SELECT 1 FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_ci o "  # noqa: S608 - sabit parçalar
                 "ON o.sys_id = CASE WHEN r.parent = c.sys_id THEN r.child ELSE r.parent END "
                 "WHERE (r.parent = c.sys_id OR r.child = c.sys_id) AND " + " AND ".join(conds) + ")")
        what = _join_or([_tr_lower(_label(md, n)) for n in related]) if related else "başka bir CI"
        return RulePlan(scope_sql, scope_params, match, cparams,
                        summary=_tr_capital(f"{what} ile ilişkisi yok"))

    if check_kind == "missing_fields":
        names = _str_list(params.get("fields"), "Alanlar")
        if not names:
            raise ValueError("En az bir alan seçin.")
        if len(names) > MAX_LIST:
            raise ValueError(f"En çok {MAX_LIST} alan seçilebilir.")
        resolve = metadata_field_resolver(md, scope_class, "c")
        exprs = []
        for n in names:
            try:
                exprs.append(resolve(n).sql)
            except QueryError as exc:
                raise ValueError(str(exc)) from None
        match = "(" + " OR ".join(_empty(e) for e in exprs) + ")"
        detail = "array_remove(ARRAY[" + ", ".join(f"CASE WHEN {_empty(e)} THEN %s END" for e in exprs) + "], NULL)"
        fields = md.fields_of(scope_class)
        labels = [str(fields.get(n, {}).get("label_tr") or n) for n in names]
        return RulePlan(scope_sql, scope_params, match, [], detail, list(names),
                        summary=f"Boş alan: {', '.join(labels)}")

    if check_kind == "not_seen_by_role":
        role = params.get("role")
        roles = md.sources.roles
        if not isinstance(role, str) or role not in roles:
            raise ValueError(f"Bilinmeyen kaynak rolü: {role}")
        match = ("NOT EXISTS (SELECT 1 FROM ingest.identity_link l JOIN ingest.data_source d ON d.name = l.data_source "
                 "WHERE l.ci_sys_id = c.sys_id AND l.active AND d.role = %s)")
        return RulePlan(scope_sql, scope_params, match, [role], depends_on_roles=(role,),
                        summary=f"{roles[role].label_tr} rolündeki hiçbir kaynakta görülmüyor")

    if check_kind == "matches_condition":
        cond = params.get("condition")
        if not isinstance(cond, str) or not cond.strip():
            raise ValueError("Bulgu koşulunu yazın.")
        try:
            sql, qparams, _order = compile_sql(parse(cond), metadata_field_resolver(md, scope_class, "c"),
                                               lambda name: md.hierarchy.descendants(name))
        except QueryError as exc:
            raise ValueError(f"Bulgu koşulu geçersiz: {exc}") from None
        return RulePlan(scope_sql, scope_params, f"({sql})", qparams, summary=f"Koşula uyuyor: {cond.strip()}")

    # relation_not_allowed: ebeveyni olduğu ilişkisi olan CI'lar aday; karar ilişki kurallarıyla (Python)
    match = "EXISTS (SELECT 1 FROM cmdb.cmdb_rel_ci r WHERE r.parent = c.sys_id)"
    return RulePlan(scope_sql, scope_params, match, [], relation_check=True, summary="İzinli olmayan ilişki")


def relation_violations(md: Any, rows: Sequence[tuple[str, str, str, str]]) -> list[dict[str, str]]:
    """``rows``: (ebeveyn sınıfı, ilişki tipi, çocuk sınıfı, çocuk adı). İzinli olmayan veya yönü ters olanlar."""
    rules = md.relations
    out = []
    for parent_class, rel_type, child_class, child_name in rows:
        verdict = rules.check(parent_class, rel_type, child_class)
        if not verdict.allowed:
            out.append({"type": rel_type, "child": child_name, "child_class": child_class, "verdict": verdict.outcome})
    return out


def describe_details(check_kind: str, details: Mapping[str, Any], field_labels: Mapping[str, str] | None = None) -> str:
    """Bulgu ayrıntısının tek cümlelik Türkçe açıklaması (görev açıklaması ve listeler için)."""
    if check_kind == "missing_fields":
        names = [str((field_labels or {}).get(f, f)) for f in details.get("empty", [])]
        return "Boş alan: " + ", ".join(names) if names else "Alanlar boş."
    if check_kind == "relation_not_allowed":
        rels = details.get("relations", [])
        parts = [f"{r['type']} → {r['child']}" + (" (yön ters)" if r.get("verdict") == "reversed" else "")
                 for r in rels[:3]]
        more = f" ve {len(rels) - 3} ilişki daha" if len(rels) > 3 else ""
        return "İzinli olmayan ilişki: " + "; ".join(parts) + more if parts else "İzinli olmayan ilişki."
    return str(details.get("summary") or "")

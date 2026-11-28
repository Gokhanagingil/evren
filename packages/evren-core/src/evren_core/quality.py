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
from datetime import UTC, datetime
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
    "finalize_coverage",
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
    "coverage_gap": {
        "label": "Kapsam boşluğu (zorunlu roller)",
        "help": "Kapsamdaki CI, zorunlu kaynak rollerinden biri tarafından son günlerde görülmüyorsa bulgu olur; eksik "
                "rollerin hepsi tek bulguda ve tek görevde toplanır, her rol için son görülme yazılır. Gözlemci roller "
                "seçilirse yalnızca son saatlerde çalıştığı görülen CI değerlendirilir (kapatılmış makineye görev "
                "açılmaz). Gölge ve emekli kaynak kapsam saymaz. Rollerden birinin kaynağı sağlıklı değilse kural sonuç "
                "vermez.",
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
    {"id": "kapsam-boslugu-uretim", "name": "Üretimde çalışan ama izlenmeyen veya korumasız sunucu",
     "description": "Sanallaştırma veya keşif son 48 saatte görüyor (sunucu çalışıyor), ama izleme veya uç nokta "
                    "güvenliği rolündeki hiçbir kaynak son 7 günde görmüyor. Eksik rollerin hepsi tek bulguda ve tek "
                    "görevde; giderildiğini sonraki kaynak çalışması doğrular.",
     "check_kind": "coverage_gap", "scope_class": "cmdb_ci_server", "scope_query": "used_for=Production",
     "params": {"roles": ["Monitoring", "EndpointSecurity"], "fresh_days": 7,
                "observed_by": ["Virtualization", "Discovery"], "observed_within_hours": 48},
     "dimension": "coverage", "severity": "high",
     "task_title": "Sunucunun kapsam eksiğini giderin",
     "task_instructions": "Bulgu, hangi rollerin sunucuyu görmediğini ve en son ne zaman gördüğünü yazar. Sunucuyu "
                          "eksik sistemlere ekleyin; giderildiğini sonraki kaynak çalışması doğrular ve görev "
                          "kendiliğinden kapanır. Sunucu kapsam dışıysa \"Geçerli değil\" ile nedenini yazın."},
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
               "not_seen_by_role": {"role"}, "matches_condition": {"condition"}, "relation_not_allowed": set(),
               "coverage_gap": {"roles", "fresh_days", "observed_by", "observed_within_hours"}}
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

    if check_kind == "coverage_gap":
        return _coverage_plan(md, scope_sql, scope_params, params)

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


def _int_param(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{name} {lo} ile {hi} arasında bir tam sayı olmalı.")
    return value


#: Kapsam sayan kaynaklar: etkin veya devrediliyor (gölge ve emekli saymaz; ADR-0009)
_SEEN = ("EXISTS (SELECT 1 FROM ingest.identity_link l JOIN ingest.source_record sr USING (data_source, source_key) "
         "JOIN ingest.data_source d ON d.name = l.data_source WHERE l.ci_sys_id = c.sys_id AND l.active "
         "AND d.lifecycle IN ('active', 'retiring') AND d.role = %s AND sr.last_seen >= now() - make_interval({unit} => %s))")
#: Kuyruk adayının kimlik ipucu sayılması için en düşük olasılık (kural eşleşmesi her zaman sayılır). Daha zayıf aday
#: (ör. yalnızca IP) sahibinin görevini engellemez: gerçek kapsam boşluğu kuyrukta beklerken gizlenmesin.
IDENTITY_MIN_PROBABILITY = 0.5
#: Kimlik ipuçları (P1.2): eksik rolün kaynağında bu CI'ı aday gösteren açık kuyruk kaydı ya da aynı ada/IP'ye sahip,
#: o rolün kaynağına bağlı başka bir CI (olası mükerrer). Varsa kaynak CI'ı büyük olasılıkla tanıyor, sorun eşleştirmede.
_IDENTITY = (
    "(SELECT coalesce(jsonb_agg(h), '[]'::jsonb) FROM ("
    "SELECT jsonb_build_object('role', d.role, 'kind', 'queue', 'candidate', m.sys_id, 'data_source', m.data_source) h "
    "FROM ingest.match_candidate m JOIN ingest.data_source d ON d.name = m.data_source "
    "WHERE m.status = 'open' AND d.role = ANY(%s::text[]) AND (m.candidate_ci = c.sys_id "
    "OR EXISTS (SELECT 1 FROM jsonb_array_elements(m.candidates) e WHERE e->>'sys_id' = c.sys_id::text "
    "AND coalesce((e->>'probability')::numeric, 0) >= %s::numeric)) "
    "UNION ALL "
    "SELECT DISTINCT jsonb_build_object('role', d.role, 'kind', 'duplicate', 'ci', o.sys_id, 'ci_name', o.name, "
    "'data_source', l.data_source) "
    "FROM cmdb.cmdb_ci o JOIN ingest.identity_link l ON l.ci_sys_id = o.sys_id AND l.active "
    "JOIN ingest.data_source d ON d.name = l.data_source "
    "WHERE o.sys_id <> c.sys_id AND d.role = ANY(%s::text[]) AND d.lifecycle IN ('active', 'retiring') "
    "AND ((coalesce(c.name, '') <> '' AND lower(split_part(o.name, '.', 1)) = lower(split_part(c.name, '.', 1))) "
    "OR (coalesce(c.ip_address, '') <> '' AND o.ip_address = c.ip_address)) "
    "LIMIT 10) s)")
_LAST_SEEN = ("(SELECT max(sr.last_seen) FROM ingest.identity_link l JOIN ingest.source_record sr "
              "USING (data_source, source_key) JOIN ingest.data_source d ON d.name = l.data_source "
              "WHERE l.ci_sys_id = c.sys_id AND l.active AND d.lifecycle IN ('active', 'retiring') AND d.role = %s)")


def _coverage_plan(md: Any, scope_sql: str, scope_params: list[Any], params: Mapping[str, Any]) -> RulePlan:
    """Kapsam politikası (P1.1, ADR-0046): zorunlu roller tazelikle; gözlemci roller "çalışıyor" kanıtı. Ayrıntı:
    ``{"missing": [rol], "last_seen": {rol: zaman | null}}``."""
    roles = md.sources.roles
    required = _str_list(params.get("roles"), "Zorunlu roller")
    observers = _str_list(params.get("observed_by"), "Gözlemci roller")
    if not required:
        raise ValueError("En az bir zorunlu rol seçin.")
    for r in (*required, *observers):
        if r not in roles:
            raise ValueError(f"Bilinmeyen kaynak rolü: {r}")
    if len(set(required)) != len(required) or len(set(observers)) != len(observers):
        raise ValueError("Bir rol bir kez seçilir.")
    if set(required) & set(observers):
        raise ValueError("Bir rol hem zorunlu hem gözlemci olamaz.")
    fresh = _int_param(params.get("fresh_days", 7), "Görülme tazeliği (gün)", 1, 90)
    within = _int_param(params.get("observed_within_hours", 48), "Çalışıyor kanıtı penceresi (saat)", 1, 720)
    seen_day = _SEEN.format(unit="days")
    seen_hour = _SEEN.format(unit="hours")
    missing_any = "(" + " OR ".join(f"NOT {seen_day}" for _ in required) + ")"
    mparams: list[Any] = [x for r in required for x in (r, fresh)]
    if observers:
        running = "(" + " OR ".join(seen_hour for _ in observers) + ")"
        match = f"{running} AND {missing_any}"
        mparams = [x for o in observers for x in (o, within)] + mparams
    else:
        match = missing_any
    detail = ("jsonb_build_object('missing', to_jsonb(array_remove(ARRAY["
              + ", ".join(f"CASE WHEN NOT {seen_day} THEN %s::text END" for _ in required)
              + "], NULL)), 'last_seen', jsonb_build_object("
              + ", ".join(f"%s::text, {_LAST_SEEN}" for _ in required) + "), 'identity', " + _IDENTITY + ")")
    dparams: list[Any] = ([x for r in required for x in (r, fresh, r)] + [x for r in required for x in (r, r)]
                          + [list(required), IDENTITY_MIN_PROBABILITY, list(required)])
    labels = [roles[r].label_tr for r in required]
    seen_by = f"; {_join_or([roles[o].label_tr for o in observers])} son {within} saatte görüyor" if observers else ""
    return RulePlan(scope_sql, scope_params, match, mparams, detail, dparams,
                    depends_on_roles=tuple(dict.fromkeys((*required, *observers))),
                    summary=f"{_join_or(labels)} rolünde son {fresh} günde görülmüyor{seen_by}")


def relation_violations(md: Any, rows: Sequence[tuple[str, str, str, str]]) -> list[dict[str, str]]:
    """``rows``: (ebeveyn sınıfı, ilişki tipi, çocuk sınıfı, çocuk adı). İzinli olmayan veya yönü ters olanlar."""
    rules = md.relations
    out = []
    for parent_class, rel_type, child_class, child_name in rows:
        verdict = rules.check(parent_class, rel_type, child_class)
        if not verdict.allowed:
            out.append({"type": rel_type, "child": child_name, "child_class": child_class, "verdict": verdict.outcome})
    return out


def finalize_coverage(details: Mapping[str, Any]) -> dict[str, Any]:
    """Kapsam bulgusunun ayrıntısını tamamlar (P1.2): kimlik ipuçlarından yalnızca eksik rollere ait olanlar kalır;
    ``identity_roles`` kimlik ipucu olan eksik roller, ``identity_only`` eksik rollerin hepsinde ipucu var demektir
    (sorun eşleştirmede: sahibine görev açılmaz, eşleştirme kuyruğu çözer)."""
    out = dict(details)
    missing = list(out.get("missing") or [])
    hints, seen = [], set()
    for h in out.get("identity") or []:
        key = (h.get("role"), h.get("kind"), h.get("candidate") or h.get("ci"))
        if h.get("role") in missing and key not in seen:
            seen.add(key)
            hints.append(dict(h))
    out["identity"] = hints
    roles = [r for r in missing if any(h["role"] == r for h in hints)]
    out["identity_roles"] = roles
    out["identity_only"] = bool(missing) and len(roles) == len(missing)
    return out


def describe_details(check_kind: str, details: Mapping[str, Any], field_labels: Mapping[str, str] | None = None, *,
                     role_labels: Mapping[str, str] | None = None, now: datetime | None = None) -> str:
    """Bulgu ayrıntısının tek cümlelik Türkçe açıklaması (görev açıklaması ve listeler için)."""
    if check_kind == "coverage_gap":
        now = now or datetime.now(UTC)
        parts = []
        for role in details.get("missing", []):
            label = (role_labels or {}).get(role, role)
            last = (details.get("last_seen") or {}).get(role)
            if not last:
                parts.append(f"{label} rolünde hiç görülmedi")
                continue
            try:
                days = max(0, (now - datetime.fromisoformat(str(last))).days)
            except ValueError:
                parts.append(f"{label} rolünde son görülme bilinmiyor")
                continue
            parts.append(f"{label} rolünde {days} gündür görülmüyor" if days else f"{label} rolünde bugün görülmedi")
        for i, role in enumerate(details.get("missing", [])):
            hints = [h for h in details.get("identity") or [] if h.get("role") == role]
            if any(h.get("kind") == "queue" for h in hints):
                src = next(h["data_source"] for h in hints if h.get("kind") == "queue")
                parts[i] += f" (olası kimlik boşluğu: {src} kaydı eşleştirme kuyruğunda)"
            elif hints:
                h = hints[0]
                other = h.get("ci_name") or h.get("ci")
                parts[i] += f" (olası mükerrer: {h['data_source']} aynı ad veya IP'li başka bir CI'a bağlı: {other})"
        return _tr_capital("; ".join(parts)) if parts else str(details.get("summary") or "")
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

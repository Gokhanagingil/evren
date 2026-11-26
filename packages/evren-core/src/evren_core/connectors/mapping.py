"""Bildirimsel alan eşleme: kaynak JSON → ServiceNow alan adlı ``IngestRecord``.

Yeni bir kaynak eklemek veya keşif aracını değiştirmek için çoğu zaman yalnızca
``connectors/<kaynak>/mapping.yaml`` yazmak yeterlidir. Bağlayıcı kodu yalnızca
veriyi çeker (API, sayfalama, kimlik doğrulama); anlamlandırma burada yapılır.

Yol sözdizimi: ``a.b.c``, ``a[0].b``, ``a[*].b`` (liste döndürür).

Kapsam: ``require`` koşullarının hepsi ve ``include_when`` koşullarından en az biri sağlanmalıdır (ikisi de isteğe
bağlı). Koşul ``{from: yol, matches: düzenli ifade}``; mantıksal değer ``true``/``false`` metniyle eşlenir.

``prefix``: aynı kaynak nesnesinden birden fazla kayıt üretildiğinde (ör. vCenter VM örneği ve
misafir sunucu) kaynak anahtarlarını ayırmak için kullanılır; ``(data_source, source_key)`` benzersizdir.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from ..envelope import IngestRecord, LookupData, ParentRef, RelationRef, SecurityPosture

__all__ = ["TRANSFORMS", "FieldMapping", "MappingError", "resolve_path"]


class MappingError(ValueError):
    pass


_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+|\*)\]")


def resolve_path(data: Any, path: str) -> Any:
    current: list[Any] = [data]
    wildcard = False
    for name, index in _SEGMENT.findall(path):
        nxt: list[Any] = []
        for item in current:
            if name:
                if isinstance(item, Mapping) and name in item:
                    nxt.append(item[name])
            elif index == "*":
                wildcard = True
                if isinstance(item, list):
                    nxt.extend(item)
            elif isinstance(item, list) and int(index) < len(item):
                nxt.append(item[int(index)])
        current = nxt
    if wildcard:
        return [v for v in current if v not in (None, "")]
    return current[0] if current else None


def _short_hostname(v: Any) -> Any:
    """``abc-1.corp.local`` → ``abc-1``. IP adresleri olduğu gibi kalır (ad kimliği sayılmaz)."""
    if not isinstance(v, str):
        return v
    text = v.strip()
    if re.fullmatch(r"[0-9.]+|[0-9a-fA-F:]+", text):
        return text
    return text.split(".", 1)[0]


def _to_int(v: Any) -> int | None:
    if v in (None, ""):
        return None
    return int(float(v))


TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "strip": lambda v: v.strip() if isinstance(v, str) else v,
    "lower": lambda v: v.lower() if isinstance(v, str) else v,
    "upper": lambda v: v.upper() if isinstance(v, str) else v,
    "int": _to_int,
    "float": lambda v: None if v in (None, "") else float(v),
    "bool": lambda v: None if v is None else str(v).strip().lower() in ("1", "true", "yes", "on"),
    "bytes_to_mb": lambda v: None if v in (None, "") else int(float(v) / (1024 * 1024)),
    "kb_to_mb": lambda v: None if v in (None, "") else int(float(v) / 1024),
    "gb_to_mb": lambda v: None if v in (None, "") else int(float(v) * 1024),
    "str": lambda v: None if v is None else str(v),
    "short_hostname": _short_hostname,
}


@dataclass(frozen=True)
class FieldSpec:
    source: str | None = None
    fallback: tuple[str, ...] = ()
    const: Any = None
    transforms: tuple[str, ...] = ()
    value_map: Mapping[str, Any] | None = None
    split: str | None = None
    prefix: str | None = None

    @classmethod
    def parse(cls, spec: Any) -> FieldSpec:
        if isinstance(spec, str):
            return cls(source=spec)
        if not isinstance(spec, Mapping):
            raise MappingError(f"Geçersiz alan tanımı: {spec!r}")
        unknown = set(spec) - {"from", "fallback", "const", "transform", "map", "split", "prefix"}
        if unknown:
            raise MappingError(f"Bilinmeyen anahtarlar: {sorted(unknown)}")
        transforms = spec.get("transform", ())
        transforms = (transforms,) if isinstance(transforms, str) else tuple(transforms)
        for t in transforms:
            if t not in TRANSFORMS:
                raise MappingError(f"Bilinmeyen dönüşüm: {t}")
        fallback = spec.get("fallback", ())
        return cls(
            source=spec.get("from"),
            fallback=(fallback,) if isinstance(fallback, str) else tuple(fallback),
            const=spec.get("const"),
            transforms=transforms,
            value_map={str(k): v for k, v in spec["map"].items()} if "map" in spec else None,
            split=spec.get("split"),
            prefix=spec.get("prefix"),
        )

    def extract(self, data: Any) -> Any:
        if self.const is not None:
            return self.const
        value = None
        for path in ((self.source,) if self.source else ()) + self.fallback:
            value = resolve_path(data, path)
            if value not in (None, "", []):
                break
        if value in (None, "", []):
            return None
        if self.split and isinstance(value, str):
            value = [p.strip() for p in value.split(self.split) if p.strip()]

        def apply(v: Any) -> Any:
            for t in self.transforms:
                v = TRANSFORMS[t](v)
            if self.value_map is not None:
                v = self.value_map.get(str(v), self.value_map.get("*", v))
            if self.prefix and v not in (None, ""):
                v = f"{self.prefix}{v}"
            return v

        if isinstance(value, list):
            return [x for x in (apply(v) for v in value) if x not in (None, "")]
        return apply(value)


@dataclass(frozen=True)
class ClassRule:
    path: str
    pattern: re.Pattern[str]
    class_name: str


@dataclass
class FieldMapping:
    data_source: str
    source_key: FieldSpec
    default_class: str
    attributes: dict[str, FieldSpec] = field(default_factory=dict)
    lookup: dict[str, FieldSpec] = field(default_factory=dict)
    external_ids: dict[str, FieldSpec] = field(default_factory=dict)
    class_rules: list[ClassRule] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    include_when: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    require: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    posture: dict[str, FieldSpec] = field(default_factory=dict)  # kısıtlı güvenlik duruşu (ADR-0023)
    parent: FieldSpec | None = None  # bağımlı CI'ın ana kaydının kaynak anahtarı (ADR-0029)
    relations_from: FieldSpec | None = None  # kaynağın ilişki listesi: [{type, target, direction}] (ADR-0030)
    parent_data_source: str | None = None

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> FieldMapping:
        for required in ("data_source", "source_key", "class"):
            if required not in doc:
                raise MappingError(f"Eşleme dosyasında '{required}' zorunlu")
        unknown = set(doc) - {"data_source", "source_key", "class", "class_rules", "include_when", "require",
                              "attributes", "lookup", "external_ids", "relations", "description", "verify", "posture",
                              "parent", "relations_from"}
        if unknown:
            raise MappingError(f"Eşleme dosyasında bilinmeyen anahtarlar: {sorted(unknown)}")
        lookup_keys = set((doc.get("lookup") or {}).keys())
        if not lookup_keys <= set(LookupData.model_fields):
            raise MappingError(f"Geçersiz lookup alanları: {sorted(lookup_keys - set(LookupData.model_fields))}")
        posture_keys = set((doc.get("posture") or {}).keys())
        if not posture_keys <= set(SecurityPosture.model_fields):
            raise MappingError(f"Geçersiz duruş alanları: {sorted(posture_keys - set(SecurityPosture.model_fields))}")
        rules = [
            ClassRule(r["when"]["from"], re.compile(r["when"]["matches"]), r["class"])
            for r in doc.get("class_rules", [])
        ]
        return cls(
            data_source=doc["data_source"],
            source_key=FieldSpec.parse(doc["source_key"]),
            default_class=doc["class"],
            attributes={k: FieldSpec.parse(v) for k, v in (doc.get("attributes") or {}).items()},
            lookup={k: FieldSpec.parse(v) for k, v in (doc.get("lookup") or {}).items()},
            external_ids={k: FieldSpec.parse(v) for k, v in (doc.get("external_ids") or {}).items()},
            class_rules=rules,
            relations=list(doc.get("relations", [])),
            include_when=[(w["from"], re.compile(w["matches"])) for w in doc.get("include_when", [])],
            require=[(w["from"], re.compile(w["matches"])) for w in doc.get("require", [])],
            posture={k: FieldSpec.parse(v) for k, v in (doc.get("posture") or {}).items()},
            parent=FieldSpec.parse(doc["parent"]["source_key"]) if doc.get("parent") else None,
            parent_data_source=(doc.get("parent") or {}).get("data_source"),
            relations_from=FieldSpec.parse(doc["relations_from"]) if doc.get("relations_from") else None,
        )

    @staticmethod
    def _matches(value: Any, pattern: re.Pattern[str]) -> bool:
        """Metin düzenli ifadeyle eşlenir; mantıksal değer ``true``/``false``, sayı metin olarak (ör. şablon bayrağı)."""
        values = value if isinstance(value, list) else [value]
        texts = [("true" if v else "false") if isinstance(v, bool) else str(v) if isinstance(v, int | float) else v
                 for v in values]
        return any(isinstance(v, str) and pattern.search(v) for v in texts)

    def class_for(self, raw: Any) -> str:
        for rule in self.class_rules:
            if self._matches(resolve_path(raw, rule.path), rule.pattern):
                return rule.class_name
        return self.default_class

    def included(self, raw: Any) -> bool:
        """Kayıt kapsamdadır: ``require`` koşullarının hepsi ve ``include_when`` koşullarından en az biri sağlanıyorsa
        (ör. ``require``: nesne türü VM ve şablon değil; ``include_when``: misafir adı bildirilmiş)."""
        if not all(self._matches(resolve_path(raw, path), pattern) for path, pattern in self.require):
            return False
        if not self.include_when:
            return True
        return any(self._matches(resolve_path(raw, path), pattern) for path, pattern in self.include_when)

    def apply(self, raw: Any, *, run_id: UUID, observed_at: datetime) -> IngestRecord | None:
        """Kaydı dönüştürür; ``include_when`` kapsamı dışındaysa ``None`` döner."""
        if not self.included(raw):
            return None
        key = self.source_key.extract(raw)
        if key in (None, ""):
            raise MappingError("Kaynak anahtarı boş; kayıt kimliksiz olamaz")
        attributes = {k: v for k, spec in self.attributes.items() if (v := spec.extract(raw)) not in (None, "", [])}
        lookup: dict[str, list[str]] = {}
        for k, spec in self.lookup.items():
            v = spec.extract(raw)
            if v in (None, "", []):
                continue
            lookup[k] = [str(x) for x in (v if isinstance(v, list) else [v])]
        external = {k: str(v) for k, spec in self.external_ids.items() if (v := spec.extract(raw)) not in (None, "")}
        relations = []
        for rel in self.relations:
            target = FieldSpec.parse(rel["target"]).extract(raw)
            for t in (target if isinstance(target, list) else [target]):
                if t in (None, ""):
                    continue
                relations.append(RelationRef(
                    type=rel["type"], direction=rel["direction"], target_source_key=str(t),
                    target_data_source=rel.get("target_data_source"), target_class_hint=rel.get("target_class"),
                ))
        if self.relations_from is not None:
            items = self.relations_from.extract(raw) or []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, Mapping) or not item.get("type") or not item.get("target"):
                    continue
                try:
                    relations.append(RelationRef(type=str(item["type"]), direction=item.get("direction", "parent"),
                                                 target_source_key=str(item["target"]),
                                                 target_data_source=item.get("target_data_source"),
                                                 target_class_hint=item.get("target_class")))
                except ValidationError:
                    raise MappingError("Kaynağın ilişki kaydı sözleşmeye uymuyor (değer iletiye yazılmaz)") from None
        parent = None
        if self.parent is not None:
            pk = self.parent.extract(raw)
            if pk in (None, ""):
                raise MappingError("Bağımlı kaydın ana kayıt anahtarı boş (ADR-0029)")
            parent = ParentRef(source_key=str(pk), data_source=self.parent_data_source)
        posture_values = {k: v for k, spec in self.posture.items() if (v := spec.extract(raw)) not in (None, "", [])}
        try:
            posture = SecurityPosture(**posture_values) if posture_values else None
        except ValidationError:
            raise MappingError("Güvenlik duruşu değerleri sözleşmeye uymuyor (değer iletiye yazılmaz)") from None
        return IngestRecord(
            data_source=self.data_source,
            run_id=run_id,
            source_key=str(key),
            class_hint=self.class_for(raw),
            attributes=attributes,
            lookup=LookupData(**lookup),
            external_ids=external,
            relations=relations,
            parent=parent,
            observed_at=observed_at,
            posture=posture,
        )

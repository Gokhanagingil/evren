"""Kurallar görünümü (salt okunur): metadata'dan, ekranın beklediği biçimde."""
from __future__ import annotations

from typing import Any

from evren_core.metadata import Metadata

__all__ = ["build_rules_view"]


def build_rules_view(md: Metadata) -> dict[str, Any]:
    labels = {c["name"]: c["label_tr"] for c in md.classes}
    reg = md.sources
    grid = []
    for rule in md.reconciliation.get("rules", []):
        attrs = rule.get("attributes", "*")
        fields = md.fields_of(rule["applies_to"])
        grid.append({
            "applies_to": rule["applies_to"], "class_label": labels.get(rule["applies_to"], rule["applies_to"]),
            "description": rule.get("description", ""),
            "attributes": [{"name": a, "label": fields.get(a, {}).get("label_tr", a)} for a in (attrs if attrs != "*" else [])],
            "priorities": dict(sorted(rule["priorities"].items(), key=lambda kv: kv[1])),
            "sources": {role: [s.name for s in reg.sources_in_role(role)] for role in rule["priorities"]},
        })
    ident = [{"applies_to": r["applies_to"], "class_label": labels.get(r["applies_to"], r["applies_to"]),
              "description": r.get("description", ""), "independent": r.get("independent", True),
              "entries": [{"priority": e["priority"], "attributes": e["attributes"],
                           "evren_extension": bool(e.get("evren_extension"))} for e in r["entries"]]}
             for r in md.identification]
    return {
        "reconciliation": grid,
        "refresh": [{"applies_to": d["applies_to"], "class_label": labels.get(d["applies_to"], d["applies_to"]),
                     "role": d["role"], "effective_days": d["effective_days"]} for d in md.reconciliation.get("data_refresh", [])],
        "staleness": [{**s, "class_label": labels.get(s["applies_to"], s["applies_to"])}
                      for s in md.reconciliation.get("staleness", [])],
        "identification": ident,
    }

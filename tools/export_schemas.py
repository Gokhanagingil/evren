#!/usr/bin/env python3
"""Pydantic modellerinden JSON şemalarını üretir (schemas/). Testler eşitliği doğrular."""
from __future__ import annotations

import json
import sys

from _bootstrap import REPO_ROOT
from evren_core.envelope import IngestEnvelope, RunEnvelope

SCHEMAS = {
    "ingest-envelope.schema.json": IngestEnvelope,
    "run-envelope.schema.json": RunEnvelope,
}


def render(model) -> str:
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:evren:schema:{model.__name__}:v1"
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    out = REPO_ROOT / "schemas"
    out.mkdir(exist_ok=True)
    for name, model in SCHEMAS.items():
        (out / name).write_text(render(model), encoding="utf-8")
        print(f"yazıldı: schemas/{name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

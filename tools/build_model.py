#!/usr/bin/env python3
"""Veri Modeli sayfasının demo modu verisini metadata'dan üretir (U4.4, ADR-0019).

Girdi : metadata/*.yaml (+ metadata/changes/ yayınlanmış değişiklik dosyaları)
Çıktı : apps/web/content/model.json

API modunda aynı görünüm ``GET /api/v1/schema/model`` ile canlı metadata'dan gelir (``evren_core.model_view``).
Kullanım: python tools/build_model.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import REPO_ROOT
from evren_core.metadata import load_metadata
from evren_core.model_view import model_view

OUT = REPO_ROOT / "apps" / "web" / "content" / "model.json"


def render() -> str:
    doc = model_view(load_metadata(REPO_ROOT / "metadata"))
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="model.json güncel mi? (CI)")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("apps/web/content/model.json güncel değil: python tools/build_model.py", file=sys.stderr)
            return 1
        print("model.json güncel.")
        return 0
    OUT.write_text(text, encoding="utf-8")
    doc = json.loads(text)
    print(f"Yazıldı: {OUT.relative_to(REPO_ROOT)} ({doc['stats']['classes']} sınıf, {len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

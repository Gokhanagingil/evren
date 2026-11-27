#!/usr/bin/env python3
"""Kullanım kılavuzu (U8.2, ADR-0033): docs/kilavuz/*.md → apps/web/content/guide.json.

Tek kaynak ``docs/kilavuz/`` klasörüdür (GitHub'da da okunur); uygulamadaki Kılavuz sayfası üretilen JSON'dan beslenir.
Denetimler (hata varsa üretmez):

* Katman: giriş sayfası ``start_word_limit``, konu sayfaları ``topic_word_limit`` kelimeyi aşmaz (ilk sayfa kısa kalır).
* Bağlantılar: ``kilavuz:<konu>`` var olan konuya, ``belge:<ad>`` docs altında var olan belgeye, ``#/<yol>`` arayüzdeki
  bir sayfaya gider; ``https://`` dışındaki her şey hatadır.
* Erişim: her konuya giriş sayfasından kılavuz bağlantılarıyla ulaşılabilir (sahipsiz sayfa yok).
* ``routes``: arayüzde var olan sayfalardır; sayfadaki "?" yardımı o konuya bağlanır (bir sayfa tek konuya).

    python tools/build_guide.py          # üret
    python tools/build_guide.py --check  # güncel mi (CI)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs" / "kilavuz"
DOCS = REPO / "docs"
APP_JS = REPO / "apps" / "web" / "assets" / "js" / "app.js"
OUT = REPO / "apps" / "web" / "content" / "guide.json"

LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
DOC_NAME = re.compile(r"^(?:adr/)?[0-9A-Za-z][0-9A-Za-z.-]*$")


class GuideError(RuntimeError):
    pass


def app_routes() -> set[str]:
    """Arayüzdeki sayfa yolları (app.js → ROUTES anahtarları)."""
    text = APP_JS.read_text(encoding="utf-8")
    block = text[text.index("const ROUTES = {"):text.index("};", text.index("const ROUTES = {"))]
    return set(re.findall(r"^\s{2}'?([a-z][a-z-]*)'?: \{ title:", block, re.M))


def words(markdown: str) -> int:
    """Okunan kelime sayısı: bağlantı adresleri ve biçim işaretleri sayılmaz."""
    text = LINK.sub(lambda m: m.group(1), markdown)
    text = re.sub(r"[#*>`|\-]+", " ", text)
    return len(text.split())


def load() -> dict[str, Any]:
    manifest = yaml.safe_load((GUIDE / "kilavuz.yaml").read_text(encoding="utf-8"))
    routes = app_routes()
    topics: list[dict[str, Any]] = []
    errors: list[str] = []
    ids = [t["id"] for t in manifest["topics"]]
    if len(ids) != len(set(ids)):
        errors.append("yinelenen konu kimliği")
    if manifest["start"] not in ids:
        errors.append(f"başlangıç konusu yok: {manifest['start']}")
    route_topics: dict[str, str] = {}
    links: dict[str, set[str]] = {}
    for t in manifest["topics"]:
        path = GUIDE / t["file"]
        if not path.is_file():
            errors.append(f"{t['id']}: dosya yok ({t['file']})")
            continue
        md = path.read_text(encoding="utf-8").strip() + "\n"
        first = md.splitlines()[0]
        if first != f"# {t['title']}":
            errors.append(f"{t['id']}: ilk satır başlık olmalı ('# {t['title']}'), bulunan: {first!r}")
        n = words(md)
        limit = manifest["start_word_limit"] if t["id"] == manifest["start"] else manifest["topic_word_limit"]
        if n > limit:
            errors.append(f"{t['id']}: {n} kelime (sınır {limit}); ayrıntıyı ayrı konuya taşıyın")
        links[t["id"]] = set()
        for _text, href in LINK.findall(md):
            if href.startswith("kilavuz:"):
                target = href.split(":", 1)[1]
                if target not in ids:
                    errors.append(f"{t['id']}: bilinmeyen kılavuz konusu {target}")
                links[t["id"]].add(target)
            elif href.startswith("belge:"):
                name = href.split(":", 1)[1]
                if not DOC_NAME.fullmatch(name) or not (DOCS / f"{name}.md").is_file():
                    errors.append(f"{t['id']}: belge yok: docs/{name}.md")
            elif href.startswith("#/"):
                route = href[2:].split("/", 1)[0]
                if route not in routes:
                    errors.append(f"{t['id']}: arayüzde olmayan sayfa #/{route}")
            elif not href.startswith("https://"):
                errors.append(f"{t['id']}: desteklenmeyen bağlantı {href}")
        for r in t.get("routes") or []:
            if r not in routes:
                errors.append(f"{t['id']}: arayüzde olmayan sayfa {r}")
            if r in route_topics:
                errors.append(f"{r}: iki konuya bağlı ({route_topics[r]}, {t['id']})")
            route_topics[r] = t["id"]
        topics.append({"id": t["id"], "title": t["title"], "summary": t["summary"], "words": n, "markdown": md})
    # Erişim: giriş sayfasından kılavuz bağlantılarıyla
    seen, todo = {manifest["start"]}, [manifest["start"]]
    while todo:
        for nxt in links.get(todo.pop(), set()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    orphans = [i for i in ids if i not in seen]
    if orphans:
        errors.append(f"giriş sayfasından ulaşılamayan konular: {', '.join(orphans)}")
    if errors:
        raise GuideError("\n".join(errors))
    return {"title": manifest["title"], "start": manifest["start"], "draft": bool(manifest.get("draft", False)),
            "topics": topics, "route_topics": route_topics, "source": "docs/kilavuz"}


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="guide.json güncel değilse hata ver")
    args = ap.parse_args(argv)
    try:
        text = render(load())
    except GuideError as exc:
        print(f"Kılavuz geçersiz:\n{exc}", file=sys.stderr)
        return 1
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("guide.json güncel değil: python tools/build_guide.py", file=sys.stderr)
            return 1
        print("guide.json güncel.")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"Yazıldı: {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

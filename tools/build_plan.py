#!/usr/bin/env python3
"""Adım adım izlenebilir proje planını yol haritasından üretir.

Girdi : apps/web/content/roadmap.json   (tek kaynak)
Çıktı : docs/PROJE-PLANI.md             (GitHub'da onay kutularıyla görünür)

Plan elle düzenlenmez: durum değişikliği roadmap.json'da yapılır, bu araç yeniden çalıştırılır.
Kullanım: python tools/build_plan.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import REPO_ROOT

SOURCE = REPO_ROOT / "apps" / "web" / "content" / "roadmap.json"
OUT = REPO_ROOT / "docs" / "PROJE-PLANI.md"
MARK = {"done": "x", "in_progress": " ", "planned": " ", "blocked": " ", "at_risk": " "}


def date_tr(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def render(rm: dict) -> str:
    labels = rm["statuses"]
    items = [i for p in rm["phases"] for i in p["items"]]
    done = sum(1 for i in items if i["status"] == "done")
    current = next((p for p in rm["phases"] if any(i["status"] != "done" for i in p["items"])), rm["phases"][-1])
    nxt = [i for i in current["items"] if i["status"] != "done"]
    out = [
        "# Evren CMDB — proje planı",
        "",
        "> Bu dosya `apps/web/content/roadmap.json` dosyasından üretilir (`python tools/build_plan.py`); elle "
        "düzenlemeyin. Aynı plan uygulamadaki **Yol Haritası** sayfasında, mimari ise **Büyük Resim** ve **Mimari "
        "Harita** sayfalarındadır.",
        "",
        f"**Son güncelleme:** {date_tr(rm['updated_at'])} · **Etkin faz:** {current['id']} — {current['title']} · "
        f"**İlerleme:** {done}/{len(items)} adım tamamlandı",
        "",
        "## Nasıl izlenir",
        "",
        "- Adımlar faz içinde **yapılış sırasıyla** listelenir. İşaretli kutu tamamlanan adımdır; durum etiketi "
        "devam eden, planlanan veya engelli adımı gösterir.",
        "- **Sizden gereken** satırı, adımın başlaması için kurumdan gelmesi gereken karar veya erişimdir. "
        "Bunlar zamanında gelmezse ilgili adım bekler; diğer adımlar sürer.",
        "- Bir faz, çıkış kriterlerinin tamamı karşılanınca kapanır.",
        "",
        "## Sıradaki adımlar",
        "",
    ]
    for i in nxt[:6]:
        line = f"1. **{i['id']}** {i['title']} — _{labels[i['status']]}_"
        if i.get("needs"):
            line += f"  \n   Sizden gereken: {i['needs']}"
        out.append(line)
    out += ["", "## Sizden gerekenler (karar ve erişim)", "", "| Adım | Gereken | Durum |", "| --- | --- | --- |"]
    for p in rm["phases"]:
        for i in p["items"]:
            if i.get("needs") and i["status"] != "done":
                out.append(f"| {i['id']} | {i['needs']} | {labels[i['status']]} |")
    out += ["", "### Açık sorular", ""] + [f"- {q}" for q in rm["open_questions"]]
    for p in rm["phases"]:
        pdone = sum(1 for i in p["items"] if i["status"] == "done")
        out += ["", f"## {p['id']} · {p['title']}", "",
                f"_{p['window']}_ · {pdone}/{len(p['items'])} adım", "", p["goal"], ""]
        for i in p["items"]:
            line = f"- [{MARK[i['status']]}] **{i['id']}** {i['title']}"
            if i["status"] != "done":
                line += f" — _{labels[i['status']]}_"
            out.append(line)
            if i.get("note"):
                out.append(f"  - Not: {i['note']}")
            if i.get("needs") and i["status"] != "done":
                out.append(f"  - Sizden gereken: {i['needs']}")
        out += ["", "**Çıkış kriterleri**", ""]
        out += [f"- [{'x' if c['met'] else ' '}] {c['text']}" for c in p["exit_criteria"]]
        if p.get("risks"):
            out += ["", "**Riskler**", ""] + [f"- {r}" for r in p["risks"]]
    out += ["", "## İlkeler", ""] + [f"- {x}" for x in rm["principles"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Plan güncel mi? (CI)")
    args = parser.parse_args(argv)
    text = render(json.loads(SOURCE.read_text(encoding="utf-8")))
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("docs/PROJE-PLANI.md güncel değil: python tools/build_plan.py", file=sys.stderr)
            return 1
        print("PROJE-PLANI.md güncel.")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"Yazıldı: {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Mimari haritayı tek kaynaktan üretir.

Girdi : apps/web/content/architecture.yaml
Çıktı : apps/web/content/architecture.svg   — uygulamada gösterilen ve "SVG indir" ile verilen çizim
        apps/web/content/architecture.json  — detay paneli, bölüm konumları, düğüm bilgileri

Kalite kapıları (derleme hata verir):
* Kutuya sığmayan metin, çakışan kutu, tanımsız düğüme ok, detayı olmayan düğüm.
* Çizim, CSP ile uyumludur: <style> veya style="" içermez; yalnızca sunum öznitelikleri.

Kullanım: python tools/build_architecture.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape

import yaml

from _bootstrap import REPO_ROOT
from _font_metrics import BOLD, DEFAULT_EM, REGULAR

CONTENT = REPO_ROOT / "apps" / "web" / "content"
SOURCE = CONTENT / "architecture.yaml"

W = 1280
MARGIN = 26
PAD = 24
GAP = 20
ROW_GAP = 40
HEADER_H = 168
CHAPTER_GAP = 18
FONT = "DejaVu Sans, Arial, sans-serif"

KINDS = {
    "source": {"label": "Kaynak sistem", "fill": "#e9f2f7", "stroke": "#2a617d", "title": "#173a52", "sub": "#3d5a6c"},
    "servicenow": {"label": "ServiceNow", "fill": "#eaf5ee", "stroke": "#2f7d4f", "title": "#1d4d31", "sub": "#3c6a4f"},
    "engine": {"label": "Evren bileşeni", "fill": "#183349", "stroke": "#183349", "title": "#ffffff", "sub": "#c9dceb"},
    "store": {"label": "Veri deposu", "fill": "#f0f3fb", "stroke": "#4a5fa8", "title": "#26346b", "sub": "#4a5684"},
    "human": {"label": "İnsan kararı / arayüz", "fill": "#fff6e6", "stroke": "#b07a1f", "title": "#5c3f0c", "sub": "#7a5a24"},
    "security": {"label": "Güvenlik denetimi", "fill": "#fbeeee", "stroke": "#a33a3a", "title": "#5e1c1c", "sub": "#7d3a3a"},
    "external": {"label": "Dış sistem", "fill": "#ffffff", "stroke": "#637b89", "title": "#2b3f4c", "sub": "#4d6474"},
}
PHASES = {
    "F0": ("#2a617d", "Faz 0"),
    "F1": ("#3f7f5f", "Faz 1"),
    "F2": ("#6a4fa0", "Faz 2"),
    "F3": ("#a85a22", "Faz 3"),
    "F4": ("#5d6d79", "Faz 4"),
}
EDGE = "#637b89"


class BuildError(ValueError):
    pass


def text_width(text: str, size: float, bold: bool = False) -> float:
    table = BOLD if bold else REGULAR
    return sum(table.get(ch, DEFAULT_EM) for ch in text) * size


def wrap(text: str, size: float, width: float, bold: bool = False, rest_width: float | None = None) -> list[str]:
    """Sözcük sınırından satırlara böler. ``rest_width`` verilirse ilk satırdan sonrakiler için kullanılır."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        limit = width if not lines or rest_width is None else rest_width
        if text_width(candidate, size, bold) <= limit:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def t(x: float, y: float, content: str, size: float, fill: str, *, weight: int = 400, anchor: str = "start",
      extra: str = "") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{extra}>{escape(content)}</text>')


def build(doc: dict) -> tuple[str, dict]:
    cols = int(doc.get("columns", 6))
    inner_w = W - 2 * MARGIN - 2 * PAD
    col_w = (inner_w - (cols - 1) * GAP) / cols
    x0 = MARGIN + PAD
    details = doc.get("details", {})
    svg: list[str] = []
    nodes_json: dict[str, dict] = {}
    chapters_json: list[dict] = []
    all_ids: set[str] = set()

    y = HEADER_H + 18
    body: list[str] = []
    for chapter in doc["chapters"]:
        cid = chapter["id"]
        lead_lines = wrap(chapter["lead"], 15, inner_w - 56)
        if len(lead_lines) > 2:
            raise BuildError(f"{cid}: açıklama en fazla 2 satır olmalı")
        header_h = 92 + 20 * (len(lead_lines) - 1)

        # Düğüm yerleşimi ve metin ölçümleri
        laid: dict[str, dict] = {}
        rows: dict[int, list[dict]] = {}
        for n in chapter["nodes"]:
            if n["id"] in all_ids:
                raise BuildError(f"Tekrarlanan düğüm kimliği: {n['id']}")
            all_ids.add(n["id"])
            if n["kind"] not in KINDS:
                raise BuildError(f"{n['id']}: bilinmeyen kind {n['kind']}")
            if n["id"] not in details:
                raise BuildError(f"{n['id']}: details bölümünde açıklama yok")
            span = int(n.get("span", 1))
            if n["col"] + span > cols:
                raise BuildError(f"{n['id']}: sütun taşması")
            w = col_w * span + GAP * (span - 1)
            phase = details[n["id"]].get("phase")
            if phase not in PHASES:
                raise BuildError(f"{n['id']}: faz F0–F4 olmalı")
            label_size = 15
            label_lines = [n["label"]]
            avail = w - 60  # sağ üstte faz etiketi için yer
            if text_width(n["label"], label_size, True) > avail:
                label_size = 14
                if text_width(n["label"], label_size, True) > avail:
                    label_lines = wrap(n["label"], label_size, avail, True, rest_width=w - 28)
                    if len(label_lines) > 2 or any(text_width(s, label_size, True) > w - 28 for s in label_lines[1:]):
                        raise BuildError(f"{n['id']}: başlık kutuya sığmıyor")
            subs = list(n.get("sub", []))
            sub_size = 12
            for s in subs:
                if text_width(s, sub_size) > w - 26:
                    sub_size = 11
            for s in subs:
                if text_width(s, sub_size) > w - 26:
                    raise BuildError(f"{n['id']}: alt metin sığmıyor: {s!r} ({text_width(s, sub_size):.0f}px > {w - 26:.0f}px)")
            if len(subs) > 2:
                raise BuildError(f"{n['id']}: en fazla 2 alt satır")
            h = 18 + 19 * len(label_lines) + 16 * len(subs) + 10
            item = {**n, "span": span, "w": w, "h": h, "label_lines": label_lines, "label_size": label_size,
                    "subs": subs, "sub_size": sub_size, "phase": phase}
            laid[n["id"]] = item
            rows.setdefault(n["row"], []).append(item)

        row_heights = {r: max(i["h"] for i in items) for r, items in rows.items()}
        row_y: dict[int, float] = {}
        cursor = y + header_h
        for r in sorted(rows):
            row_y[r] = cursor
            cursor += row_heights[r] + ROW_GAP
        chapter_h = (cursor - ROW_GAP) - y + PAD
        for item in laid.values():
            item["x"] = x0 + item["col"] * (col_w + GAP)
            item["y"] = row_y[item["row"]]
            item["h"] = row_heights[item["row"]]
            item["cx"] = item["x"] + item["w"] / 2
            item["cy"] = item["y"] + item["h"] / 2
        # çakışma kontrolü
        items = list(laid.values())
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if a["row"] == b["row"] and not (a["col"] + a["span"] <= b["col"] or b["col"] + b["span"] <= a["col"]):
                    raise BuildError(f"Çakışan kutular: {a['id']} / {b['id']}")

        body.append(f'<g id="ch-{cid}" data-chapter="{cid}">')
        body.append(f'<rect x="{MARGIN}" y="{y:.1f}" width="{W - 2 * MARGIN}" height="{chapter_h:.1f}" rx="14" '
                    f'fill="#ffffff" stroke="#c6d4dd"/>')
        body.append(f'<rect x="{MARGIN + 22}" y="{y + 23:.1f}" width="41" height="31" rx="8" fill="#2a617d"/>')
        body.append(t(MARGIN + 42.5, y + 44, chapter["number"], 16, "#ffffff", weight=700, anchor="middle"))
        body.append(t(MARGIN + 78, y + 46, chapter["title"], 26, "#172e42", weight=700))
        for li, line in enumerate(lead_lines):
            body.append(t(MARGIN + 79, y + 72 + 20 * li, line, 15, "#4d6474"))

        # oklar (kutuların altında kalsın diye önce çizilir)
        below_routes: dict[int, int] = {}
        for e in chapter.get("edges", []):
            if e["from"] not in laid or e["to"] not in laid:
                raise BuildError(f"{cid}: tanımsız düğüme ok: {e}")
            s, d = laid[e["from"]], laid[e["to"]]
            dashed = e.get("style") == "dashed"
            if s["row"] == d["row"]:
                left_to_right = d["x"] > s["x"]
                lo, hi = sorted((s["col"], d["col"]))
                blocked = any(o["row"] == s["row"] and o["id"] not in (s["id"], d["id"]) and
                              lo < o["col"] + o["span"] and o["col"] < hi + 1 and
                              o["col"] >= min(s["col"] + s["span"], d["col"] + d["span"]) and
                              o["col"] + o["span"] <= max(s["col"], d["col"]) for o in items)
                if blocked:
                    k = below_routes.get(s["row"], 0)
                    below_routes[s["row"]] = k + 1
                    ry = s["y"] + s["h"] + 12 + 9 * k
                    sx = s["cx"] + (-14 if left_to_right else 14)
                    dx = d["cx"] + (14 if left_to_right else -14)
                    path = f"M{sx:.1f} {s['y'] + s['h']:.1f}V{ry:.1f}H{dx:.1f}V{d['y'] + d['h']:.1f}"
                else:
                    sy = min(max(d["cy"], s["y"] + 12), s["y"] + s["h"] - 12)
                    if left_to_right:
                        sx, dx = s["x"] + s["w"], d["x"]
                    else:
                        sx, dx = s["x"], d["x"] + d["w"]
                    mx = (sx + dx) / 2
                    path = f"M{sx:.1f} {s['cy']:.1f}H{mx:.1f}V{d['cy']:.1f}H{dx:.1f}" if abs(s["cy"] - d["cy"]) > 1 \
                        else f"M{sx:.1f} {sy:.1f}H{dx:.1f}"
            else:
                down = d["row"] > s["row"]
                tx = min(max(s["cx"], d["x"] + 16), d["x"] + d["w"] - 16)
                sx = min(max(tx, s["x"] + 16), s["x"] + s["w"] - 16)
                sy = s["y"] + s["h"] if down else s["y"]
                ty = d["y"] if down else d["y"] + d["h"]
                my = (sy + ty) / 2
                path = f"M{sx:.1f} {sy:.1f}V{ty:.1f}" if abs(sx - tx) < 0.5 else \
                    f"M{sx:.1f} {sy:.1f}V{my:.1f}H{tx:.1f}V{ty:.1f}"
            dash = ' stroke-dasharray="6 5"' if dashed else ""
            body.append(f'<path data-edge="{e["from"]}:{e["to"]}" d="{path}" stroke="{EDGE}" stroke-width="2" '
                        f'fill="none" stroke-linejoin="round" marker-end="url(#arrow)"{dash}/>')

        for item in items:
            k = KINDS[item["kind"]]
            dash = ' stroke-dasharray="5 4"' if item["kind"] == "external" else ""
            aria = escape(item["label"] + (": " + " ".join(item["subs"]) if item["subs"] else ""), quote=True)
            body.append(f'<g class="node" id="n-{item["id"]}" data-node="{item["id"]}" data-chapter="{cid}" '
                        f'tabindex="0" role="button" aria-label="{aria}">')
            body.append(f'<rect x="{item["x"]:.1f}" y="{item["y"]:.1f}" width="{item["w"]:.1f}" height="{item["h"]:.1f}" '
                        f'rx="10" fill="{k["fill"]}" stroke="{k["stroke"]}" stroke-width="1.6"{dash}/>')
            pcolor, _ = PHASES[item["phase"]]
            px = item["x"] + item["w"] - 40
            ring = ' stroke="#ffffff" stroke-width="1"' if item["kind"] == "engine" else ""
            body.append(f'<rect x="{px:.1f}" y="{item["y"] + 9:.1f}" width="30" height="18" rx="9" fill="{pcolor}"{ring}/>')
            body.append(t(px + 15, item["y"] + 22, item["phase"], 11, "#ffffff", weight=700, anchor="middle"))
            ly = item["y"] + 26
            for line in item["label_lines"]:
                body.append(t(item["x"] + 14, ly, line, item["label_size"], k["title"], weight=700))
                ly += 19
            sy = ly - 1
            for s in item["subs"]:
                body.append(t(item["x"] + 14, sy, s, item["sub_size"], k["sub"]))
                sy += 16
            body.append("</g>")
            d = details[item["id"]]
            nodes_json[item["id"]] = {
                "label": item["label"], "sub": item["subs"], "kind": item["kind"], "kind_label": k["label"],
                "chapter": cid, "phase": item["phase"], "text": d.get("text", ""), "tech": d.get("tech", []),
                "security": d.get("security", []),
            }
        body.append("</g>")
        chapters_json.append({"id": cid, "number": chapter["number"], "title": chapter["title"], "lead": chapter["lead"],
                              "y": round(y, 1), "height": round(chapter_h, 1)})
        y += chapter_h + CHAPTER_GAP

    unknown_details = set(details) - all_ids
    if unknown_details:
        raise BuildError(f"Kullanılmayan detaylar: {sorted(unknown_details)}")

    height = round(y + 8)
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" id="architectureMap" viewBox="0 0 {W} {height}" width="{W}" '
               f'height="{height}" role="group" aria-labelledby="mapTitle mapDescription" font-family="{FONT}">')
    svg.append(f'<title id="mapTitle">{escape(doc["title"])} · Mimari Harita</title>')
    svg.append(f'<desc id="mapDescription">{escape(doc["heading"])}. '
               + escape(" ".join(f'{c["number"]} {c["title"]}.' for c in doc["chapters"])) + "</desc>")
    svg.append(f'<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">'
               f'<path d="M0 0 7 3.5 0 7z" fill="{EDGE}"/></marker></defs>')
    svg.append(f'<rect x="0" y="0" width="{W}" height="{height}" fill="#f3f6f8"/>')
    svg.append(f'<rect x="0" y="0" width="{W}" height="{HEADER_H}" fill="#183349"/>')
    svg.append(t(51, 33, f'{doc["title"].upper()}   /   MİMARİ HARİTA', 15, "#9edbd1", weight=700))
    svg.append(t(50, 72, doc["heading"], 34, "#ffffff", weight=700))
    svg.append(t(52, 100, doc["legend"], 14, "#d2e5ef"))
    lx = 52
    for kind in KINDS.values():
        dash = ' stroke-dasharray="4 3"' if kind is KINDS["external"] else ""
        stroke = "#9edbd1" if kind is KINDS["engine"] else kind["stroke"]
        svg.append(f'<rect x="{lx}" y="120" width="18" height="18" rx="4" fill="{kind["fill"]}" stroke="{stroke}" '
                   f'stroke-width="1.5"{dash}/>')
        svg.append(t(lx + 25, 134, kind["label"], 13, "#d2e5ef"))
        lx += 25 + text_width(kind["label"], 13) + 22
    for code, (color, _label) in PHASES.items():
        if code == "F4":
            continue
        svg.append(f'<rect x="{lx}" y="120" width="30" height="18" rx="9" fill="{color}" stroke="#ffffff" stroke-width="1"/>')
        svg.append(t(lx + 15, 133, code, 11, "#ffffff", weight=700, anchor="middle"))
        lx += 36
    if lx > W - 30:
        raise BuildError("Başlıktaki gösterge satırı sığmıyor")
    svg.extend(body)
    svg.append("</svg>")
    data = {
        "title": doc["title"], "heading": doc["heading"], "width": W, "height": height,
        "generated_from": "apps/web/content/architecture.yaml",
        "chapters": chapters_json, "nodes": nodes_json,
        "kinds": {k: v["label"] for k, v in KINDS.items()},
        "phases": {k: v[1] for k, v in PHASES.items()},
    }
    return "\n".join(svg) + "\n", data


def render_outputs() -> tuple[str, str]:
    doc = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    svg, data = build(doc)
    return svg, json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Üretilen dosyalar güncel mi? (CI)")
    args = parser.parse_args(argv)
    try:
        svg, js = render_outputs()
    except BuildError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1
    targets = {CONTENT / "architecture.svg": svg, CONTENT / "architecture.json": js}
    if args.check:
        stale = [p.name for p, c in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        if stale:
            print(f"Güncel değil: {stale} — python tools/build_architecture.py", file=sys.stderr)
            return 1
        return 0
    for path, content in targets.items():
        path.write_text(content, encoding="utf-8")
        print(f"yazıldı: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

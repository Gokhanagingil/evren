#!/usr/bin/env python3
"""Tek sayfalık "büyük resim" çizimini A3 yatay (420 × 297 mm) üretir: kaynak rolleri → alım → ara katman → kayıt
sistemi → tüketim, ServiceNow geçiş şeridi, güvenlik şeridi ve fazlar. Her kutuda ad, ne işe yaradığını anlatan tek
cümle, teknoloji ve tamamlanma yüzdesi vardır.

Kaynaklar: ``apps/web/content/components.json`` (amaç, teknoloji, yüzde, kanıt, eksik, plan bağı) ve ``roadmap.json``
(fazlar). Çıktı: ``apps/web/content/big-picture.svg`` (uygulamadaki Büyük Resim sayfası ve paylaşılabilir görünüm).

Kalite kapıları: kutuya sığmayan metin, satır sınırını aşan cümle, tuvali aşan yerleşim ve bileşen kaydı olmayan kutu
derlemeyi durdurur. CSP uyumlu: <style> ve style="" yok. 1 mm = 5 birim; yazdırıldığında gövde metni ≈ 6,5 pt.

Kullanım: python tools/build_big_picture.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape
from typing import Any

from _bootstrap import REPO_ROOT
from _font_metrics import BOLD, DEFAULT_EM, REGULAR

OUT = REPO_ROOT / "apps" / "web" / "content" / "big-picture.svg"
ROADMAP = REPO_ROOT / "apps" / "web" / "content" / "roadmap.json"
COMPONENTS = REPO_ROOT / "apps" / "web" / "content" / "components.json"
W, H = 2100, 1485  # A3 yatay: 420 × 297 mm, 1 mm = 5 birim
FONT = "Inter, 'Segoe UI', 'DejaVu Sans', Arial, sans-serif"

C = {
    "text": "#1d2026", "text2": "#3f4651", "muted": "#6b7280", "line": "#e4e6ea", "arrow": "#7b818c",
    "accent": "#d50c2d", "panel": "#ffffff", "bg": "#f3f4f6",
}
KINDS = {
    "role": {"fill": "#f6f7f9", "stroke": "#cfd3da", "title": C["text"], "sub": C["muted"]},
    "role_sn": {"fill": "#e7f6ef", "stroke": "#9ed3bb", "title": "#0b6b4a", "sub": "#3c6a4f"},
    "new": {"fill": "#ffffff", "stroke": "#9aa3af", "title": C["text2"], "sub": C["muted"], "dash": "5 4"},
    "engine": {"fill": "#eaf0fd", "stroke": "#2b5fd9", "title": "#1c3f8f", "sub": "#3d5591"},
    "human": {"fill": "#fff4e0", "stroke": "#e0a020", "title": "#7a4600", "sub": "#8a5a10"},
    "store": {"fill": "#2f3b52", "stroke": "#2f3b52", "title": "#ffffff", "sub": "#cdd3de"},
    "store2": {"fill": "#eef0f3", "stroke": "#9aa3af", "title": C["text"], "sub": C["muted"]},
    "cache": {"fill": "#ffffff", "stroke": "#9aa3af", "title": C["text2"], "sub": C["muted"], "dash": "5 4"},
    "sn": {"fill": "#e7f6ef", "stroke": "#0f8a5f", "title": "#0b6b4a", "sub": "#3c6a4f"},
    "consumer": {"fill": "#ffffff", "stroke": "#cfd3da", "title": C["text"], "sub": C["muted"]},
    "external": {"fill": "#ffffff", "stroke": "#1d2026", "title": C["text"], "sub": C["text2"], "width": 1.8},
    "security": {"fill": "#fdeef1", "stroke": "#f0a3b0", "title": "#9c1023", "sub": "#9c1023"},
}
TECH = {"store": "#9be3c3", "security": "#7a0c1c", "sn": "#0b5c40", "role_sn": "#0b5c40", "human": "#6a3c00",
        "engine": "#16357a"}
TS, PS, XS = 14.0, 11.6, 10.8      # başlık, amaç cümlesi, teknoloji yazı boyutları (birim)
LP, LX = 14.6, 13.6                # satır yükseklikleri
PCT_ON_DARK = "#9be3c3"


class BuildError(ValueError):
    pass


def pct_color(pct: int) -> str:
    """≥75 yeşil (çalışıyor), 40–74 turuncu (kısmen), <40 kırmızı (büyük ölçüde eksik)."""
    return "#0f8a5f" if pct >= 75 else "#b86e00" if pct >= 40 else "#d50c2d"


BRANDS = ("ServiceNow", "OpenSearch", "PostgreSQL", "Kafka")


def tr_upper(text: str) -> str:
    """Türkçe büyük harf: i → İ, ı → I (Python'un upper() işlevi "sistemi"ni "SISTEMI" yapar). Ürün adları Latin
    kurallarıyla büyütülür ("SERVICENOW", "SERVİCENOW" değil)."""
    return " ".join(w.upper() if any(b in w for b in BRANDS) else w.replace("i", "İ").replace("ı", "I").upper()
                    for w in text.split(" "))


def tw(text: str, size: float, bold: bool = False) -> float:
    table = BOLD if bold else REGULAR
    return sum(table.get(ch, DEFAULT_EM) for ch in text) * size


def txt(x, y, s, size, fill, *, weight=400, anchor="start", italic=False) -> str:
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{style}>{escape(s)}</text>')


class Status:
    """components.json: kutu → yüzde; grup ve ana sütun ortalamaları (``same_as`` ve ``optional`` sayılmaz)."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.items = {it["id"]: it for g in doc["groups"] for it in g["items"]}
        self.groups = {g["id"]: g for g in doc["groups"]}
        self.used: set[str] = set()

    def pct(self, cid: str) -> int:
        if cid not in self.items:
            raise BuildError(f"components.json'da bileşen yok: {cid}")
        self.used.add(cid)
        return int(self.items[cid]["pct"])

    def label(self, cid: str) -> str:
        return self.items[cid]["label"]

    @staticmethod
    def _avg(items: list[dict[str, Any]]) -> int:
        vals = [it["pct"] for it in items if not it.get("same_as") and not it.get("optional")]
        return round(sum(vals) / len(vals)) if vals else 0

    def group_pct(self, gid: str) -> int:
        return self._avg(self.groups[gid]["items"])

    def pillar_pct(self, pillar: dict[str, Any]) -> int:
        return self._avg([it for gid in pillar["groups"] for it in self.groups[gid]["items"]])

    def overall(self) -> int:
        return self._avg(list(self.items.values()))


class Canvas:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.errors: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def fit(self, text: str, size: float, width: float, bold: bool, where: str) -> None:
        if tw(text, size, bold) > width:
            self.errors.append(f"Metin sığmıyor ({where}): '{text}' {tw(text, size, bold):.0f} > {width:.0f}")

    def wrap(self, text: str, size: float, width: float, bold: bool, where: str, max_lines: int) -> list[str]:
        lines: list[str] = []
        for word in text.split(" "):
            if lines and tw(lines[-1] + " " + word, size, bold) <= width:
                lines[-1] += " " + word
            else:
                if tw(word, size, bold) > width:
                    self.errors.append(f"Sözcük kutuya sığmıyor ({where}): '{word}'")
                lines.append(word)
        if len(lines) > max_lines:
            self.errors.append(f"Metin {max_lines} satırı aşıyor ({where}): '{text}'")
        return lines

    def progress(self, x, y, w, pct, *, label_x, label_y, size=11.0, dark=False) -> None:
        color = PCT_ON_DARK if dark else pct_color(pct)
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="6" rx="3" '
                 f'fill="{"#46536d" if dark else "#e3e6eb"}"/>')
        if pct:
            self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w * pct / 100, 3):.1f}" height="6" rx="3" '
                     f'fill="{color}"/>')
        self.add(txt(label_x, label_y, f"%{pct}", size, color, weight=700, anchor="end"))

    # ------------------------------------------------------------ bileşen kutusu: ad · amaç · teknoloji · yüzde
    def layout(self, w: float, item: dict[str, Any], *, purpose_lines: int = 3, tech_lines: int = 2):
        title, inner = item["label_draw"], w - 22
        self.fit(title, TS, inner, True, f"{item['id']} başlık")
        p = self.wrap(item["purpose"], PS, inner, False, f"{item['id']} amaç", purpose_lines)
        t = self.wrap(item["tech"], XS, inner, True, f"{item['id']} teknoloji", tech_lines)
        height = 10 + TS * 1.25 + len(p) * LP + 7 + len(t) * LX + 24
        return p, t, height

    def cbox(self, x, y, w, h, kind, item, *, purpose_lines=3, tech_lines=2):
        k = KINDS[kind]
        p, t, need = self.layout(w, item, purpose_lines=purpose_lines, tech_lines=tech_lines)
        if need > h + 0.5:
            self.errors.append(f"Kutu yüksekliği yetmiyor ({item['id']}): {need:.0f} > {h:.0f}")
        dash = f' stroke-dasharray="{k["dash"]}"' if "dash" in k else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" fill="{k["fill"]}" '
                 f'stroke="{k["stroke"]}" stroke-width="{k.get("width", 1.2)}"{dash}/>')
        cy = y + 10 + TS
        self.add(txt(x + 11, cy, item["label_draw"], TS, k["title"], weight=700))
        cy += 4
        for line in p:
            cy += LP
            self.add(txt(x + 11, cy - 2, line, PS, k["sub"]))
        cy += 5
        self.add(f'<line x1="{x + 11:.1f}" y1="{cy:.1f}" x2="{x + w - 11:.1f}" y2="{cy:.1f}" '
                 f'stroke="{k["stroke"]}" stroke-width="0.8" stroke-dasharray="2 3"/>')
        for line in t:
            cy += LX
            self.add(txt(x + 11, cy - 1, line, XS, TECH.get(kind, "#2f3b52"), weight=700))
        self.progress(x + 11, y + h - 15, w - 62, item["pct"], label_x=x + w - 10, label_y=y + h - 8.5,
                      dark=kind == "store")

    def cylinder(self, x, y, w, h, item):
        k = KINDS["store"]
        e = 13
        self.add(f'<path d="M{x},{y + e} v{h - 2 * e} a{w / 2},{e} 0 0 0 {w},0 v-{h - 2 * e}" fill="{k["fill"]}" '
                 f'stroke="{k["stroke"]}"/>')
        self.add(f'<ellipse cx="{x + w / 2}" cy="{y + e}" rx="{w / 2}" ry="{e}" fill="#46536d" stroke="{k["stroke"]}"/>')
        inner = w - 40
        self.fit(item["label_draw"], 15, inner, True, item["id"])
        p = self.wrap(item["purpose"], PS, inner, False, f"{item['id']} amaç", 2)
        t = self.wrap(item["tech"], XS + 0.6, inner, True, f"{item['id']} teknoloji", 1)
        cy = y + e + 26
        self.add(txt(x + w / 2, cy, item["label_draw"], 15, "#ffffff", weight=700, anchor="middle"))
        for line in p:
            cy += LP
            self.add(txt(x + w / 2, cy, line, PS, "#cdd3de", anchor="middle"))
        for line in t:
            cy += LX + 3
            self.add(txt(x + w / 2, cy, line, XS + 0.6, PCT_ON_DARK, weight=700, anchor="middle"))
        if cy + 30 > y + h:
            self.errors.append(f"Silindir yüksekliği yetmiyor: {item['id']}")
        self.progress(x + 20, y + h - 26, w - 76, item["pct"], label_x=x + w - 18, label_y=y + h - 19.5, dark=True)

    def panel(self, x, y, w, h, number, title, caption="", pct=None):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{C["panel"]}" stroke="{C["line"]}"/>')
        label = tr_upper(f"{number} · {title}" if number else title)
        room = w - 28
        if pct is not None:
            self.add(txt(x + w - 16, y + 28, f"%{pct}", 17, pct_color(pct), weight=800, anchor="end"))
            room -= tw(f"%{pct}", 17, True) + 12
        self.fit(label, 13.5, room, True, "panel")
        self.add(txt(x + 16, y + 27, label, 13.5, C["accent"], weight=700))
        if caption:
            self.fit(caption, 12.5, w - 28, False, "panel caption")
            self.add(txt(x + 16, y + 46, caption, 12.5, C["muted"]))

    def arrow(self, points, *, dashed=False, label=None, label_at=None, color=None):
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in points)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color or C["arrow"]}" stroke-width="1.8"{dash} marker-end="url(#ah)"/>')
        if label:
            lx, ly = label_at or points[0]
            self.add(f'<rect x="{lx - tw(label, 11.5) / 2 - 4:.1f}" y="{ly - 11:.1f}" width="{tw(label, 11.5) + 8:.1f}" '
                     f'height="15" fill="{C["panel"]}"/>')
            self.add(txt(lx, ly, label, 11.5, C["muted"], anchor="middle", italic=True))


def build() -> str:
    cv = Canvas()
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    st = Status(json.loads(COMPONENTS.read_text(encoding="utf-8")))
    M = 24  # kenar boşluğu

    def item(cid: str, label: str | None = None) -> dict[str, Any]:
        st.pct(cid)
        it = dict(st.items[cid])
        it["label_draw"] = label or it["label"]
        for key in ("purpose", "tech"):
            if not it.get(key):
                cv.errors.append(f"components.json: {cid} için '{key}' yok")
                it[key] = "?"
        return it

    def uniform(w: float, items: list[dict[str, Any]], **kw) -> float:
        return max(cv.layout(w, it, **kw)[2] for it in items)

    # ---------------------------------------------------------------- başlık, açıklama, lejant
    cv.add(txt(M, 50, "Evren CMDB — büyük resim", 32, C["text"], weight=750))
    sub = ("Kaynaklar değişebilir, kurallar değişmez. Gözlemler ara katmanda anlamlanır, golden record Evren'de "
           "tutulur, ServiceNow CMDB replika olur.")
    cv.fit(sub, 15, 1150, False, "alt başlık")
    cv.add(txt(M, 80, sub, 15, C["text2"]))
    legend = [("role", "Kaynak rolü (ürün değişebilir)"), ("engine", "Evren anlamlandırma"), ("human", "İnsan kararı"),
              ("store", "Kayıt sistemi"), ("sn", "ServiceNow"), ("external", "Bağlanan dış uygulama"),
              ("security", "Güvenlik"), ("cache", "Destek bileşeni")]
    lx0 = 1240
    lx, ly = lx0, 24
    for kind, label in legend:
        k = KINDS[kind]
        width = 24 + tw(label, 12.5) + 18
        if lx + width > W - M:
            lx, ly = lx0, ly + 22
        dash = f' stroke-dasharray="{k["dash"]}"' if "dash" in k else ""
        cv.add(f'<rect x="{lx}" y="{ly}" width="15" height="15" rx="3" fill="{k["fill"]}" stroke="{k["stroke"]}" '
               f'stroke-width="{k.get("width", 1.2)}"{dash}/>')
        cv.add(txt(lx + 21, ly + 12.5, label, 12.5, C["text2"]))
        lx += width
    how = "Her kutu: ad · ne işe yaradığı (tek cümle) · teknoloji (kalın) · tamamlanma yüzdesi"
    cv.fit(how, 13, 1150, True, "kutu açıklaması")
    cv.add(txt(M, 100, how, 13, C["text"], weight=600))

    # ---------------------------------------------------------------- tamamlanma özeti (ana sütunlar)
    sy = 112
    pillars = [(p["title"], st.pillar_pct(p)) for p in st.doc["pillars"]]
    overall = st.overall()
    head_ = "TAMAMLANMA"
    cv.add(txt(M, sy + 15, head_, 13, C["accent"], weight=700))
    cv.add(txt(M, sy + 34, f"genel %{overall}", 15, pct_color(overall), weight=800))
    chip_x0 = M + max(tw(head_, 13, True), tw(f"genel %{overall}", 15, True)) + 22
    chip_w = (W - M - chip_x0 - (len(pillars) - 1) * 8) / len(pillars)
    for i, (title, pct) in enumerate(pillars):
        cx = chip_x0 + i * (chip_w + 8)
        cv.add(f'<rect x="{cx:.1f}" y="{sy}" width="{chip_w:.1f}" height="38" rx="8" fill="{C["panel"]}" '
               f'stroke="{C["line"]}"/>')
        cv.fit(title, 12.5, chip_w - 98, True, "sütun")
        cv.add(txt(cx + 10, sy + 24, title, 12.5, C["text"], weight=700))
        cv.progress(cx + chip_w - 84, sy + 16, 38, pct, label_x=cx + chip_w - 9, label_y=sy + 24, size=12.5)

    top = 162
    # sütun genişlikleri: 1 kaynaklar, 2 alım, 3 akıl katmanı, 4 CMDB, 5 tüketim
    gapx = 22
    widths = [436, 254, 612, 392, 0]
    widths[4] = W - 2 * M - sum(widths) - 4 * gapx
    xs = [M]
    for wd in widths[:-1]:
        xs.append(xs[-1] + wd + gapx)
    (x1, x2, x3, x4, x5), (w1, w2, w3, w4, w5) = xs, widths
    head = 58

    # 1 kaynaklar -----------------------------------------------------------
    roles = [("role", "src_discovery", "Keşif"), ("role", "src_virtualization", "Sanallaştırma"),
             ("role", "src_network", "Ağ keşfi"), ("role", "src_monitoring", "İzleme"),
             ("role", "src_endpoint", "Uç nokta güvenliği"), ("role_sn", "src_itsm", "ITSM / mevcut CMDB"),
             ("role_sn", "src_change", "Değişiklik kayıtları"), ("new", "src_new", "+ Yeni rol veya ürün")]
    r_items = [(k, item(c, lbl)) for k, c, lbl in roles]
    bw1 = (w1 - 24 - 12) / 2
    rh1 = uniform(bw1, [i for _, i in r_items])
    col1_h = head + 4 * rh1 + 3 * 10 + 14

    # 2 alım ------------------------------------------------------------------
    steps = [("engine", "connector_runtime", "Bağlayıcı"), ("engine", "mapping", "YAML eşleme"),
             ("security", "gateway", "Alım geçidi"), ("store2", "kafka", "Kafka olay omurgası"),
             ("cache", "raw_archive", "Ham yük arşivi")]
    s_items = [(k, item(c, lbl)) for k, c, lbl in steps]
    bw2 = w2 - 28
    rh2 = uniform(bw2, [i for _, i in s_items])

    # 3 akıl katmanı ----------------------------------------------------------
    grid = {(0, 0): ("engine", "change_filter", "Değişim süzgeci"), (1, 0): ("engine", "normalize", "Normalizasyon"),
            (2, 0): ("engine", "identity", "Kimlik çözümleme"), (0, 1): ("security", "breaker", "Devre kesici"),
            (1, 1): ("human", "match_queue", "Eşleştirme kuyruğu"), (2, 1): ("engine", "reconcile", "Uzlaştırma"),
            (0, 2): ("engine", "quality", "Kalite ve kapsam"), (1, 2): ("human", "ai_suggest", "Yapay zekâ önerisi"),
            (2, 2): ("engine", "unauthorized", "Yetkisiz değişiklik"), (0, 3): ("human", "manual_input", "Elle girdi"),
            (1, 3): ("engine", "dependent_ci", "Bağımlı CI kimliği"), (2, 3): ("engine", "lifecycle", "Eskime, emeklilik")}
    g_items = {pos: (k, item(c, lbl)) for pos, (k, c, lbl) in grid.items()}
    gcol = 24
    cw = (w3 - 28 - 2 * gcol) / 3
    rh3 = uniform(cw, [i for _, i in g_items.values()])
    grow = 34

    # 4 CMDB ------------------------------------------------------------------
    stores = [("store2", "data_model", "Veri modeli"), ("store2", "relations", "İlişkiler"),
              ("store2", "dictionary", "Metadata sözlüğü"), ("store2", "provenance", "Kaynak bilgisi"),
              ("store2", "audit", "Denetim izi"), ("store2", "outbox", "Outbox"),
              ("cache", "search", "OpenSearch arama"), ("cache", "backup", "Felaket kurtarma")]
    d_items = [(k, item(c, lbl)) for k, c, lbl in stores]
    dcw = (w4 - 32 - 12) / 2
    rh4 = uniform(dcw, [i for _, i in d_items])
    golden = item("golden", "PostgreSQL golden record")

    # 5 tüketim ---------------------------------------------------------------
    cons = [("consumer", "web_ui", "Evren arayüzü"), ("consumer", "rest_api", "REST API"),
            ("consumer", "event_stream", "Olay akışı"), ("consumer", "evren_mcp", "Evren MCP sunucusu"),
            ("external", "grc", "GRC Platformu (tedarik)"), ("external", "corp_mcp", "Kurum MCP uygulaması")]
    c_items = [(k, item(c, lbl)) for k, c, lbl in cons]
    bw5 = w5 - 28
    rh5 = uniform(bw5, [i for _, i in c_items], purpose_lines=2)

    ph = max(col1_h, head + 5 * rh2 + 4 * 26 + 16, head + 10 + 4 * rh3 + 3 * grow + 40,
             head + 150 + 4 * rh4 + 3 * 12 + 14, head + 6 * rh5 + 5 * 14 + 12)
    bottom = top + ph

    cv.panel(x1, top, w1, ph, "1", "Kaynaklar", "Rol (başlık) → bugünkü ürün (teknoloji satırı)", pct=st.group_pct("sources"))
    for i, (kind, it) in enumerate(r_items):
        cv.cbox(x1 + 12 + (i % 2) * (bw1 + 12), top + head + (i // 2) * (rh1 + 10), bw1, rh1, kind, it)

    cv.panel(x2, top, w2, ph, "2", "Alım", "Bağlayıcı CMDB'ye yazmaz", pct=st.group_pct("ingest"))
    y = top + head
    for i, (kind, it) in enumerate(s_items):
        cv.cbox(x2 + 14, y, bw2, rh2, kind, it)
        if i < 3:
            cv.arrow([(x2 + w2 / 2, y + rh2), (x2 + w2 / 2, y + rh2 + 24)])
        elif i == 3:
            cv.arrow([(x2 + w2 / 2 + 50, y + rh2 + 24), (x2 + w2 / 2 + 50, y + rh2 + 2)], dashed=True)
        y += rh2 + 26

    cv.panel(x3, top, w3, ph, "3", "Ara katman — akıl katmanı", "Evren'in asıl değeri; golden record'a tek yazan "
             "uzlaştırıcıdır", pct=st.group_pct("sense"))
    cols = [x3 + 14 + i * (cw + gcol) for i in range(3)]
    rows = [top + head + 10 + r * (rh3 + grow) for r in range(4)]
    for (c, r), (kind, it) in g_items.items():
        cv.cbox(cols[c], rows[r], cw, rh3, kind, it)
    mid = rh3 / 2
    cv.arrow([(cols[0] + cw, rows[0] + mid), (cols[1] - 2, rows[0] + mid)])
    cv.arrow([(cols[1] + cw, rows[0] + mid), (cols[2] - 2, rows[0] + mid)])
    cv.arrow([(cols[2] + cw / 2, rows[0] + rh3), (cols[2] + cw / 2, rows[1] - 2)], label="eşleşti",
             label_at=(cols[2] + cw / 2, rows[0] + rh3 + grow / 2 + 4))
    cv.arrow([(cols[2] + 16, rows[0] + rh3), (cols[1] + cw - 16, rows[1] - 2)], label="şüpheli",
             label_at=(cols[1] + cw + gcol / 2, rows[0] + rh3 + grow / 2 + 4))
    cv.arrow([(cols[1] + cw, rows[1] + mid), (cols[2] - 2, rows[1] + mid)])
    cv.arrow([(cols[1] + cw / 2, rows[2]), (cols[1] + cw / 2, rows[1] + rh3 + 2)], label="öneri",
             label_at=(cols[1] + cw / 2, rows[1] + rh3 + grow / 2 + 4))
    cv.arrow([(cols[2] + cw / 2, rows[1] + rh3), (cols[2] + cw / 2, rows[2] - 2)])
    note = "Her kararın gerekçesi saklanır: kaynak, kural, ezilen değerler."
    cv.fit(note, 12.5, w3 - 32, False, "not")
    cv.add(txt(x3 + 16, bottom - 16, note, 12.5, C["muted"]))

    cv.panel(x4, top, w4, ph, "4", "CMDB", "Kayıt sistemi", pct=st.group_pct("record"))
    cv.cylinder(x4 + 16, top + head, w4 - 32, 140, golden)
    y = top + head + 150
    for i, (kind, it) in enumerate(d_items):
        cv.cbox(x4 + 16 + (i % 2) * (dcw + 12), y + (i // 2) * (rh4 + 12), dcw, rh4, kind, it)

    cv.panel(x5, top, w5, ph, "5", "Tüketim", "Evren'in verisini kullananlar", pct=st.group_pct("consume"))
    y = top + head
    for kind, it in c_items:
        cv.cbox(x5 + 14, y, bw5, rh5, kind, it, purpose_lines=2)
        y += rh5 + 14

    ay = top + ph / 2
    cv.arrow([(x1 + w1, ay), (x2 - 2, ay)])
    cv.arrow([(x2 + w2, ay), (x3 - 2, ay)])
    cv.arrow([(cols[2] + cw, rows[1] + mid), (x4 - 2, rows[1] + mid)])
    cv.arrow([(x4 + w4, ay), (x5 - 2, ay)])

    # ürün değişimi notu ----------------------------------------------------
    ny = bottom + 10
    swap = ("Ürün değişirse: yeni bağlayıcı + eşleme → gölge mod (yazmaz, karşılaştırır) → devir → emeklilik. "
            "Kurallar, CI'lar ve sys_id'ler değişmez (ADR-0009).")
    cv.add(f'<rect x="{M}" y="{ny}" width="{W - 2 * M}" height="34" rx="8" fill="#fdeef1" stroke="#f0a3b0"/>')
    cv.fit(swap, 13.5, W - 2 * M - 32, False, "ürün değişimi")
    cv.add(txt(M + 16, ny + 22.5, swap, 13.5, "#9c1023", weight=600))

    # 6 ServiceNow geçişi ---------------------------------------------------
    lane = [("sn", "sn_load", "SN CMDB ilk yükleme"), ("store", "sn_golden", "Evren golden record"),
            ("engine", "sn_sync", "sn-sync"), ("sn", "sn_ire", "SN IRE API"), ("sn", "sn_replica", "SN CMDB (replika)"),
            ("sn", "sn_task", "task.cmdb_ci"), ("external", "ci_picker", "CI seçici (F3)")]
    l_items = [(k, item(c, lbl)) for k, c, lbl in lane]
    lgap = 34
    lw = (W - 2 * M - 32 - (len(lane) - 1) * lgap) / len(lane)
    lh = uniform(lw, [i for _, i in l_items], purpose_lines=2)
    st6 = ny + 46
    sh = 44 + lh + 40
    cv.panel(M, st6, W - 2 * M, sh, "6", "ServiceNow geçişi — Evren ana kaynak, SN CMDB replika",
             pct=st.group_pct("servicenow"))
    ly2 = st6 + 42
    centers = []
    for i, (kind, it) in enumerate(l_items):
        lx = M + 16 + i * (lw + lgap)
        cv.cbox(lx, ly2, lw, lh, kind, it, purpose_lines=2)
        centers.append(lx)
    for i in range(len(lane) - 2):
        cv.arrow([(centers[i] + lw, ly2 + lh / 2), (centers[i + 1] - 2, ly2 + lh / 2)])
    cv.arrow([(centers[6], ly2 + lh / 2), (centers[5] + lw + 2, ly2 + lh / 2)], dashed=True)
    ry2 = ly2 + lh + 22
    cv.arrow([(centers[4] + lw / 2, ly2 + lh), (centers[4] + lw / 2, ry2), (centers[1] + lw / 2, ry2),
              (centers[1] + lw / 2, ly2 + lh + 2)], dashed=True)
    back = "SN'de elle yapılan düzenleme ITSM kaynağı olarak geri çekilir ve uzlaştırmadan geçer"
    bx = (centers[4] + centers[1] + lw) / 2
    cv.add(f'<rect x="{bx - tw(back, 12) / 2 - 8:.1f}" y="{ry2 - 10}" width="{tw(back, 12) + 16:.1f}" '
           f'height="20" fill="{C["panel"]}"/>')
    cv.add(txt(bx, ry2 + 4.5, back, 12, C["muted"], anchor="middle", italic=True))

    # 7 güvenlik ------------------------------------------------------------
    chips = [("sec_entra", "Entra ID"), ("sec_rbac", "RBAC + ABAC"), ("sec_sod", "Görevler ayrılığı"),
             ("sec_schema", "Şema değişikliği"), ("sec_writer", "Tek yazar"), ("sec_least", "En az yetki"),
             ("sec_audit", "Denetim izi"), ("sec_secrets", "mTLS + sır kasası"), ("sec_supply", "Tedarik zinciri"),
             ("sec_ai", "Yapay zekâ önerir"), ("ops", "Sistem durumu")]
    k_items = [item(c, lbl) for c, lbl in chips]
    kgap = 10
    kw = (W - 2 * M - 32 - (len(chips) - 1) * kgap) / len(chips)
    kh = uniform(kw, k_items)
    gt = st6 + sh + 12
    gh = 42 + kh + 12
    cv.panel(M, gt, W - 2 * M, gh, "7", "Güvenlik, yönetişim ve işletim — her katmanda", pct=st.group_pct("security"))
    for i, it in enumerate(k_items):
        cv.cbox(M + 16 + i * (kw + kgap), gt + 40, kw, kh, "security", it)

    # fazlar ------------------------------------------------------------------
    ft = gt + gh + 12
    current = next((p for p in roadmap["phases"] if any(i["status"] != "done" for i in p["items"])), roadmap["phases"][-1])
    cv.add(txt(M, ft + 15, "YOL HARİTASI", 13, C["accent"], weight=700))
    note = ("Yüzde = hedef kapsamın çalışır ve testli kısmı (sentetik veriyle). Her eksik bir plan maddesine bağlı; "
            "kanıt ve eksikler Büyük Resim sayfasındaki tabloda (components.json).")
    cv.fit(note, 12, W - 260, False, "yüzde notu")
    cv.add(txt(W - M, ft + 15, note, 12, C["muted"], anchor="end"))
    phases = roadmap["phases"]
    pw = (W - 2 * M - (len(phases) - 1) * 18) / len(phases)
    for i, p in enumerate(phases):
        px = M + i * (pw + 18)
        on = p is current
        done = sum(1 for it in p["items"] if it["status"] == "done")
        cv.add(f'<rect x="{px:.1f}" y="{ft + 24}" width="{pw:.1f}" height="54" rx="8" fill="{"#fdeef1" if on else C["panel"]}" '
               f'stroke="{C["accent"] if on else C["line"]}" stroke-width="{1.8 if on else 1.2}"/>')
        title = f"{p['id']} · {p['title']}".replace("ServiceNow", "SN")
        cv.fit(title, 13.5, pw - 22, True, "faz")
        cv.add(txt(px + 12, ft + 46, title, 13.5, C["text"], weight=700))
        meta = f"{p['window'].split('·')[0].strip()} · {done}/{len(p['items'])} iş" + (" · şu an" if on else "")
        cv.add(txt(px + 12, ft + 66, meta, 12, C["accent"] if on else C["muted"], weight=600 if on else 400))
        if i < len(phases) - 1:
            cv.arrow([(px + pw + 2, ft + 51), (px + pw + 16, ft + 51)])

    missing = sorted(set(st.items) - st.used)
    if missing:
        cv.errors.append("Çizimde kutusu olmayan bileşen(ler): " + ", ".join(missing))
    if ft + 84 > H:
        cv.errors.append(f"A3 tuval yüksekliği yetmiyor: {ft + 84:.0f} > {H}")
    if cv.errors:
        raise BuildError("\n".join(cv.errors))
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["arrow"]}"/></marker></defs>')
    head_svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="420mm" height="297mm" role="img" '
                'aria-labelledby="bpTitle bpDesc">'
                '<title id="bpTitle">Evren CMDB — büyük resim (A3)</title>'
                '<desc id="bpDesc">Kaynak rolleri, alım, ara katman, kayıt sistemi, tüketim; ServiceNow geçiş şeridi; '
                'güvenlik ve yönetişim; yol haritası fazları. Her bileşende amaç, teknoloji ve tamamlanma yüzdesi.</desc>')
    return head_svg + defs + f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>' + "".join(cv.parts) + "</svg>\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Üretilen dosya güncel mi? (CI)")
    args = parser.parse_args(argv)
    try:
        svg = build()
    except BuildError as exc:
        print(f"Büyük resim üretilemedi:\n{exc}", file=sys.stderr)
        return 2
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != svg:
            print("big-picture.svg güncel değil: python tools/build_big_picture.py", file=sys.stderr)
            return 1
        print("big-picture.svg güncel.")
        return 0
    OUT.write_text(svg, encoding="utf-8")
    print(f"Yazıldı: {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

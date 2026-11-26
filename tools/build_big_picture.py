#!/usr/bin/env python3
"""Tek sayfalık "büyük resim" çizimini üretir: kaynak rolleri → alım → ara katman → kayıt sistemi → tüketim,
ServiceNow geçiş şeridi, güvenlik şeridi ve fazlar. Her kutuda tamamlanma yüzdesi vardır.

Kaynaklar: ``apps/web/content/components.json`` (bileşen yüzdesi, kanıt, eksik, plan bağı) ve ``roadmap.json``
(fazlar). Çıktı: ``apps/web/content/big-picture.svg`` (uygulamadaki Büyük Resim sayfası ve paylaşılabilir görünüm).

Kalite kapıları: kutuya sığmayan metin ve bileşen kaydı olmayan kutu derlemeyi durdurur. CSP uyumlu: <style> ve
style="" yok.

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
W, H = 1600, 1196
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
            self.errors.append(f"Metin sığmıyor ({where}): '{text}' {tw(text, size, bold):.0f}px > {width:.0f}px")

    def progress(self, x, y, w, pct, *, label_x, label_y, size=10.5, dark=False) -> None:
        color = PCT_ON_DARK if dark else pct_color(pct)
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="5" rx="2.5" '
                 f'fill="{"#46536d" if dark else "#e3e6eb"}"/>')
        if pct:
            self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w * pct / 100, 3):.1f}" height="5" rx="2.5" '
                     f'fill="{color}"/>')
        self.add(txt(label_x, label_y, f"%{pct}", size, color, weight=700, anchor="end"))

    def box(self, x, y, w, h, kind, title, sub=(), *, align="center", tsize=13.0, ssize=11.0, rx=8, pct=None):
        k = KINDS[kind]
        dash = f' stroke-dasharray="{k["dash"]}"' if "dash" in k else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{k["fill"]}" stroke="{k["stroke"]}" '
                 f'stroke-width="{k.get("width", 1.2)}"{dash}/>')
        body = h
        if pct is not None:  # alt şerit: ilerleme çubuğu + yüzde
            body = h - 14
            self.progress(x + 10, y + h - 13, w - 58, pct, label_x=x + w - 9, label_y=y + h - 8,
                          dark=kind == "store")
        lines = [(title, tsize, True, k["title"])] + [(s, ssize, False, k["sub"]) for s in sub]
        heights = [ln[1] * 1.28 for ln in lines]
        total = sum(heights)
        cy = y + (body - total) / 2
        for (s, size, bold, fill), lh in zip(lines, heights, strict=True):
            cy += lh
            self.fit(s, size, w - 18, bold, title)
            if align == "center":
                self.add(txt(x + w / 2, cy - lh * 0.24, s, size, fill, weight=700 if bold else 400, anchor="middle"))
            else:
                self.add(txt(x + 12, cy - lh * 0.24, s, size, fill, weight=700 if bold else 400))
        if total > body - 4:
            self.errors.append(f"Kutu yüksekliği yetmiyor: {title}")

    def cylinder(self, x, y, w, h, title, sub, pct=None):
        k = KINDS["store"]
        e = 11
        self.add(f'<path d="M{x},{y + e} v{h - 2 * e} a{w / 2},{e} 0 0 0 {w},0 v-{h - 2 * e}" fill="{k["fill"]}" '
                 f'stroke="{k["stroke"]}"/>')
        self.add(f'<ellipse cx="{x + w / 2}" cy="{y + e}" rx="{w / 2}" ry="{e}" fill="#46536d" stroke="{k["stroke"]}"/>')
        self.fit(title, 13.5, w - 16, True, title)
        self.add(txt(x + w / 2, y + 38, title, 13.5, "#ffffff", weight=700, anchor="middle"))
        for i, s in enumerate(sub):
            self.fit(s, 11, w - 16, False, title)
            self.add(txt(x + w / 2, y + 55 + i * 14, s, 11, "#cdd3de", anchor="middle"))
        if pct is not None:
            self.add(f'<rect x="{x + 14:.1f}" y="{y + h - 24:.1f}" width="{w - 66:.1f}" height="5" rx="2.5" fill="#46536d"/>')
            self.add(f'<rect x="{x + 14:.1f}" y="{y + h - 24:.1f}" width="{max((w - 66) * pct / 100, 3):.1f}" '
                     f'height="5" rx="2.5" fill="{PCT_ON_DARK}"/>')
            self.add(txt(x + w - 14, y + h - 18, f"%{pct}", 11, PCT_ON_DARK, weight=700, anchor="end"))

    def panel(self, x, y, w, h, number, title, caption="", pct=None):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{C["panel"]}" stroke="{C["line"]}"/>')
        label = tr_upper(f"{number} · {title}" if number else title)
        room = w - 24
        if pct is not None:
            self.add(txt(x + w - 14, y + 25, f"%{pct}", 14, pct_color(pct), weight=800, anchor="end"))
            room -= tw(f"%{pct}", 14, True) + 10
        self.fit(label, 11.5, room, True, "panel")
        self.add(txt(x + 14, y + 24, label, 11.5, C["accent"], weight=700))
        if caption:
            self.fit(caption, 11.5, w - 24, False, "panel caption")
            self.add(txt(x + 14, y + 41, caption, 11.5, C["muted"]))

    def arrow(self, points, *, dashed=False, label=None, label_at=None, color=None):
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in points)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color or C["arrow"]}" stroke-width="1.6"{dash} marker-end="url(#ah)"/>')
        if label:
            lx, ly = label_at or points[0]
            self.add(txt(lx, ly, label, 10.5, C["muted"], anchor="middle", italic=True))


def build() -> str:
    cv = Canvas()
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    st = Status(json.loads(COMPONENTS.read_text(encoding="utf-8")))

    # ---------------------------------------------------------------- başlık, açıklama, lejant
    cv.add(txt(24, 42, "Evren CMDB — büyük resim", 26, C["text"], weight=750))
    sub = ("Kaynaklar değişebilir, kurallar değişmez. Gözlemler ara katmanda anlamlanır, golden record Evren'de "
           "tutulur, ServiceNow CMDB replika olur.")
    cv.fit(sub, 13.5, 960, False, "alt başlık")
    cv.add(txt(24, 68, sub, 13.5, C["text2"]))
    legend = [("role", "Kaynak rolü (ürün değişebilir)"), ("engine", "Evren anlamlandırma"), ("human", "İnsan kararı"),
              ("store", "Kayıt sistemi"), ("sn", "ServiceNow"), ("external", "Bağlanan dış uygulama"),
              ("security", "Güvenlik")]
    lx, ly = 1010, 30
    for kind, label in legend:
        k = KINDS[kind]
        width = 22 + tw(label, 11.5) + 16
        if lx + width > W - 24:
            lx, ly = 1010, ly + 22
        cv.add(f'<rect x="{lx}" y="{ly}" width="14" height="14" rx="3" fill="{k["fill"]}" stroke="{k["stroke"]}" '
               f'stroke-width="{k.get("width", 1.2)}"/>')
        cv.add(txt(lx + 20, ly + 11.5, label, 11.5, C["text2"]))
        lx += width

    # ---------------------------------------------------------------- tamamlanma özeti (ana sütunlar)
    sy = 88
    pillars = [(p["title"], st.pillar_pct(p)) for p in st.doc["pillars"]]
    overall = st.overall()
    head_ = "TAMAMLANMA"
    cv.add(txt(24, sy + 13, head_, 11.5, C["accent"], weight=700))
    cv.add(txt(24, sy + 30, f"genel %{overall}", 13, pct_color(overall), weight=800))
    chip_x0 = 24 + max(tw(head_, 11.5, True), tw(f"genel %{overall}", 13, True)) + 18
    cw_ = (W - 24 - chip_x0 - (len(pillars) - 1) * 10) / len(pillars)
    for i, (title, pct) in enumerate(pillars):
        cx = chip_x0 + i * (cw_ + 10)
        cv.add(f'<rect x="{cx:.1f}" y="{sy}" width="{cw_:.1f}" height="32" rx="8" fill="{C["panel"]}" '
               f'stroke="{C["line"]}"/>')
        cv.fit(title, 12, cw_ - 104, True, "sütun")
        cv.add(txt(cx + 10, sy + 20.5, title, 12, C["text"], weight=700))
        cv.progress(cx + cw_ - 86, sy + 13.5, 40, pct, label_x=cx + cw_ - 9, label_y=sy + 21, size=12)

    top, bottom = 128, 744
    ph = bottom - top
    # ---------------------------------------------------------------- 1 kaynaklar (rol → ürün)
    x1, w1 = 24, 246
    cv.panel(x1, top, w1, ph, "1", "Kaynaklar", "Rol → bugünkü ürün", pct=st.group_pct("sources"))
    roles = [
        ("role", "src_discovery", "Keşif", "BMC Helix Discovery"),
        ("role", "src_virtualization", "Sanallaştırma", "VMware vCenter"),
        ("role", "src_network", "Ağ keşfi", "OpenText NNMi"), ("role", "src_monitoring", "İzleme", "Zabbix"),
        ("role", "src_endpoint", "Uç nokta güvenliği", "Microsoft Defender"),
        ("role_sn", "src_itsm", "ITSM / mevcut CMDB", "ServiceNow CMDB"),
        ("role_sn", "src_change", "Değişiklik kayıtları", "ServiceNow Change"),
        ("new", "src_new", "+ Yeni rol veya ürün", "bulut, Kubernetes, yedekleme…"),
    ]
    ry = top + 56
    for kind, cid, role, product in roles:
        cv.box(x1 + 12, ry, w1 - 24, 62, kind, role, [product], align="left", tsize=12.5, ssize=11, pct=st.pct(cid))
        ry += 69

    # ---------------------------------------------------------------- 2 alım (veri akış ortamı)
    x2, w2 = 296, 196
    cv.panel(x2, top, w2, ph, "2", "Alım", "Bağlayıcı CMDB'ye yazmaz", pct=st.group_pct("ingest"))
    steps = [("engine", "connector_runtime", "Bağlayıcı", ["ürüne özel · çeker,", "sayfalar, salt okunur"]),
             ("engine", "mapping", "YAML eşleme", ["kaynak alanı →", "ServiceNow alan adı"]),
             ("security", "gateway", "Alım geçidi", ["belirteç · şema ·", "kaynak başına yetki"]),
             ("store2", "kafka", "Kafka olay omurgası", ["yeniden oynatılabilir", "DLQ · outbox yayını"]),
             ("cache", "raw_archive", "Ham yük arşivi", ["sorun analizi ve", "yeniden işleme"])]
    by = top + 56
    for i, (kind, cid, title, sub_) in enumerate(steps):
        cv.box(x2 + 14, by, w2 - 28, 88, kind, title, sub_, tsize=12.5, pct=st.pct(cid))
        if i < 3:
            cv.arrow([(x2 + w2 / 2, by + 88), (x2 + w2 / 2, by + 110)])
        by += 112
    cv.arrow([(x2 + w2 / 2 + 40, by - 112 - 24), (x2 + w2 / 2 + 40, by - 112 - 2)], dashed=True)

    # ---------------------------------------------------------------- 3 ara katman (akıl katmanı)
    x3, w3 = 518, 518
    cv.panel(x3, top, w3, ph, "3", "Ara katman — akıl katmanı", "Evren'in asıl değeri; golden record'a tek yazan "
             "uzlaştırıcıdır", pct=st.group_pct("sense"))
    cols = [x3 + 14, x3 + 14 + 170, x3 + 14 + 340]
    cw, rh = 156, 90
    rows = [top + 62, top + 192, top + 322, top + 452]
    grid = {
        (0, 0): ("engine", "change_filter", "Değişim süzgeci", ["içerik özeti aynıysa", "yalnız last_seen"]),
        (1, 0): ("engine", "normalize", "Normalizasyon", ["abc_1 = ABC-1", "= abc-1.corp"]),
        (2, 0): ("engine", "identity", "Kimlik çözümleme", ["IRE kuralı + skor", "+ çelişki denetimi"]),
        (0, 1): ("security", "breaker", "Devre kesici", ["kısmi çalışmada", "eskime ve silme yok"]),
        (1, 1): ("human", "match_queue", "Eşleştirme kuyruğu", ["insan karar verir;", "birleştirme geri alınır"]),
        (2, 1): ("engine", "reconcile", "Uzlaştırma", ["rol önceliği · tazeleme", "öznitelik başına"]),
        (0, 2): ("engine", "quality", "Kalite ve kapsam", ["mükerrer, kapsam,", "gölge mod raporları"]),
        (1, 2): ("human", "ai_suggest", "Yapay zekâ önerisi", ["gerekçesiyle kayıt;", "insan onaylar"]),
        (2, 2): ("engine", "unauthorized", "Yetkisiz değişiklik", ["Change penceresi dışı", "değişen öznitelik"]),
        (0, 3): ("human", "manual_input", "Elle girdi", ["CI, ilişki, düzeltme;", "aynı uzlaştırmadan geçer"]),
        (1, 3): ("engine", "dependent_ci", "Bağımlı CI kimliği", ["uygulama, adaptör:", "üst CI bağlamında"]),
        (2, 3): ("engine", "lifecycle", "Eskime, emeklilik", ["görülmeyen CI;", "devre kesici onayıyla"]),
    }
    for (c, r), (kind, cid, title, sub_) in grid.items():
        cv.box(cols[c], rows[r], cw, rh, kind, title, sub_, tsize=12.5, ssize=10.8, pct=st.pct(cid))
    mid = (rh - 14) / 2
    cv.arrow([(cols[0] + cw, rows[0] + mid), (cols[1] - 2, rows[0] + mid)])
    cv.arrow([(cols[1] + cw, rows[0] + mid), (cols[2] - 2, rows[0] + mid)])
    cv.arrow([(cols[2] + cw / 2, rows[0] + rh), (cols[2] + cw / 2, rows[1] - 2)], label="eşleşti",
             label_at=(cols[2] + cw / 2 + 32, rows[0] + rh + 26))
    cv.arrow([(cols[2] + 14, rows[0] + rh), (cols[1] + cw - 14, rows[1] - 2)], label="şüpheli",
             label_at=(cols[1] + cw - 30, rows[0] + rh + 20))
    cv.arrow([(cols[1] + cw, rows[1] + mid), (cols[2] - 2, rows[1] + mid)])
    cv.arrow([(cols[1] + cw / 2, rows[2]), (cols[1] + cw / 2, rows[1] + rh + 2)], label="öneri",
             label_at=(cols[1] + cw / 2 + 24, rows[1] + rh + 26))
    cv.arrow([(cols[2] + cw / 2, rows[1] + rh), (cols[2] + cw / 2, rows[2] - 2)])
    note = "Her kararın gerekçesi saklanır: kaynak, kural, ezilen değerler."
    cv.fit(note, 11.5, w3 - 28, False, "not")
    cv.add(txt(x3 + 16, bottom - 16, note, 11.5, C["muted"]))

    # ---------------------------------------------------------------- 4 kayıt sistemi (CMDB)
    x4, w4 = 1062, 230
    cv.panel(x4, top, w4, ph, "4", "CMDB", pct=st.group_pct("record"))
    cv.cylinder(x4 + 16, top + 40, w4 - 32, 110, "PostgreSQL", ["golden record", "cmdb_ci · sys_id · ilişkiler"],
                pct=st.pct("golden"))
    stores = [("store2", "data_model", "Veri modeli", ["SN uyumlu sınıflar + CSDM"]),
              ("store2", "relations", "İlişkiler", ["cmdb_rel_ci · hizmet haritası"]),
              ("store2", "dictionary", "Metadata sözlüğü", ["sınıf, alan, değişiklik seti"]),
              ("store2", "provenance", "Kaynak bilgisi", ["öznitelik başına kazanan"]),
              ("store2", "audit", "Denetim izi", ["salt ekleme → SIEM"]),
              ("store2", "outbox", "Outbox", ["tek işlemde olay"]),
              ("cache", "search", "OpenSearch arama", ["dizin; kayıt sistemi değil"]),
              ("cache", "backup", "Felaket kurtarma", ["yedek · PITR · çoğaltma"])]
    sy = top + 160
    for kind, cid, title, sub_ in stores:
        cv.box(x4 + 16, sy, w4 - 32, 52, kind, title, sub_, tsize=12.5, ssize=10.8, pct=st.pct(cid))
        sy += 57

    # ---------------------------------------------------------------- 5 tüketim
    x5, w5 = 1318, 258
    cv.panel(x5, top, w5, ph, "5", "Tüketim", pct=st.group_pct("consume"))
    cons = [("consumer", "web_ui", "Evren arayüzü", ["CI 360° · kuyruk · kaynaklar"]),
            ("consumer", "rest_api", "REST API", ["+ ServiceNow uyumlu Table/IRE"]),
            ("consumer", "event_stream", "Olay akışı", ["CloudEvents · imzalı webhook"]),
            ("consumer", "evren_mcp", "Evren MCP sunucusu", ["salt okuma + öneri"]),
            ("external", "grc", "GRC Platformu (tedarik)", ["CI verisi + otomatik kontroller",
                                                             "provenance · inconclusive"]),
            ("external", "corp_mcp", "Kurum MCP uygulaması", ["yapay zekâ ajanları"])]
    cy = top + 44
    for kind, cid, title, sub_ in cons:
        hgt = 88 if len(sub_) > 1 else 74
        cv.box(x5 + 14, cy, w5 - 28, hgt, kind, title, sub_, tsize=12.5, ssize=10.8, pct=st.pct(cid))
        cy += hgt + 16

    # ana akış okları
    ay = top + ph / 2
    cv.arrow([(x1 + w1, ay), (x2 - 2, ay)])
    cv.arrow([(x2 + w2, ay), (x3 - 2, ay)])
    cv.arrow([(cols[2] + cw, rows[1] + mid), (x4 - 2, rows[1] + mid)])
    cv.arrow([(x4 + w4, ay), (x5 - 2, ay)])

    # ürün değişimi notu
    ny = bottom + 10
    swap = ("Ürün değişirse: yeni bağlayıcı + eşleme → gölge mod (yazmaz, karşılaştırır) → devir → emeklilik. "
            "Kurallar, CI'lar ve sys_id'ler değişmez (ADR-0009).")
    cv.add(f'<rect x="24" y="{ny}" width="{W - 48}" height="34" rx="8" fill="#fdeef1" stroke="#f0a3b0"/>')
    cv.fit(swap, 12.5, W - 80, False, "ürün değişimi")
    cv.add(txt(40, ny + 22, swap, 12.5, "#9c1023", weight=600))

    # ---------------------------------------------------------------- 6 ServiceNow geçişi
    st6, sh = ny + 48, 150
    cv.panel(24, st6, W - 48, sh, "6", "ServiceNow geçişi — Evren ana kaynak, SN CMDB replika",
             pct=st.group_pct("servicenow"))
    lane = [("sn", "sn_load", "SN CMDB ilk yükleme", ["sys_id korunur (F0)"], 196),
            ("store", "sn_golden", "Evren golden record", ["ana kaynak"], 196),
            ("engine", "sn_sync", "sn-sync", ["kapsamlı itme: süreç sınıfları"], 214),
            ("sn", "sn_ire", "SN IRE API", ["/identifyreconcile"], 176),
            ("sn", "sn_replica", "SN CMDB (replika)", ["aynı sys_id"], 176),
            ("sn", "sn_task", "task.cmdb_ci", ["Incident/Change kuralları aynı"], 214),
            ("external", "ci_picker", "CI seçici (F3)", ["SN formunda Evren araması"], 196)]
    gap = (W - 48 - 32 - sum(item[4] for item in lane)) / (len(lane) - 1)
    lx, ly2, lh = 40.0, st6 + 38, 68
    centers = []
    for kind, cid, title, sub_, wd in lane:
        cv.box(lx, ly2, wd, lh, kind, title, sub_, tsize=12.5, ssize=10.8, pct=st.pct(cid))
        centers.append((lx, wd))
        lx += wd + gap
    for i in range(len(lane) - 2):
        (ax, aw), (bx, _) = centers[i], centers[i + 1]
        cv.arrow([(ax + aw, ly2 + lh / 2), (bx - 2, ly2 + lh / 2)])
    (fx, fw), (gx, _) = centers[5], centers[6]
    cv.arrow([(gx, ly2 + lh / 2), (fx + fw + 2, ly2 + lh / 2)], dashed=True)
    (ex, ew), (bx2, bw2) = centers[4], centers[1]
    ry2 = ly2 + lh + 24
    cv.arrow([(ex + ew / 2, ly2 + lh), (ex + ew / 2, ry2), (bx2 + bw2 / 2, ry2), (bx2 + bw2 / 2, ly2 + lh + 2)],
             dashed=True)
    back = "SN'de elle yapılan düzenleme ITSM kaynağı olarak geri çekilir ve uzlaştırmadan geçer"
    cv.add(f'<rect x="{(ex + bx2 + bw2) / 2 - tw(back, 11) / 2 - 8:.1f}" y="{ry2 - 9}" width="{tw(back, 11) + 16:.1f}" '
           f'height="18" fill="{C["panel"]}"/>')
    cv.add(txt((ex + bx2 + bw2) / 2, ry2 + 4, back, 11, C["muted"], anchor="middle", italic=True))

    # ---------------------------------------------------------------- 7 güvenlik
    gt, gh = st6 + sh + 14, 116
    cv.panel(24, gt, W - 48, gh, "7", "Güvenlik ve yönetişim — her katmanda", pct=st.group_pct("security"))
    chips = [("sec_entra", "Entra ID", "OIDC + PKCE"), ("sec_rbac", "RBAC + ABAC", "alan hassasiyeti"),
             ("sec_sod", "Görevler ayrılığı", "veritabanında"), ("sec_schema", "Şema değişikliği", "taslak → onay → yayın"),
             ("sec_writer", "Tek yazar", "uzlaştırıcı"), ("sec_least", "En az yetki", "DB rolleri"),
             ("sec_audit", "Denetim izi", "salt ekleme → SIEM"), ("sec_secrets", "mTLS + sır kasası", "repoda sır yok"),
             ("sec_supply", "Tedarik zinciri", "hash kilidi, tarama"), ("sec_ai", "Yapay zekâ önerir", "insan onaylar")]
    cwid = (W - 48 - 32 - 9 * 10) / 10
    for i, (cid, a, b) in enumerate(chips):
        cv.box(40 + i * (cwid + 10), gt + 38, cwid, 64, "security", a, [b], tsize=11.5, ssize=10.5, pct=st.pct(cid))

    # ---------------------------------------------------------------- fazlar
    ft = gt + gh + 12
    current = next((p for p in roadmap["phases"] if any(i["status"] != "done" for i in p["items"])), roadmap["phases"][-1])
    cv.add(txt(24, ft + 14, "YOL HARİTASI", 11.5, C["accent"], weight=700))
    note = ("Yüzde = hedef kapsamın çalışır ve testli kısmı (sentetik veriyle). Her eksik bir plan maddesine bağlı; "
            "kanıt ve eksikler Büyük Resim sayfasındaki tabloda (components.json).")
    cv.fit(note, 11, W - 200, False, "yüzde notu")
    cv.add(txt(W - 24, ft + 14, note, 11, C["muted"], anchor="end"))
    phases = roadmap["phases"]
    pw = (W - 48 - (len(phases) - 1) * 16) / len(phases)
    for i, p in enumerate(phases):
        px = 24 + i * (pw + 16)
        on = p is current
        done = sum(1 for it in p["items"] if it["status"] == "done")
        cv.add(f'<rect x="{px:.1f}" y="{ft + 22}" width="{pw:.1f}" height="52" rx="8" fill="{"#fdeef1" if on else C["panel"]}" '
               f'stroke="{C["accent"] if on else C["line"]}" stroke-width="{1.8 if on else 1.2}"/>')
        title = f"{p['id']} · {p['title']}".replace("ServiceNow", "SN")
        cv.fit(title, 12.5, pw - 20, True, "faz")
        cv.add(txt(px + 12, ft + 43, title, 12.5, C["text"], weight=700))
        meta = f"{p['window'].split('·')[0].strip()} · {done}/{len(p['items'])} iş" + (" · şu an" if on else "")
        cv.add(txt(px + 12, ft + 62, meta, 11, C["accent"] if on else C["muted"], weight=600 if on else 400))
        if i < len(phases) - 1:
            cv.arrow([(px + pw + 2, ft + 48), (px + pw + 14, ft + 48)])

    missing = sorted(set(st.items) - st.used)
    if missing:
        cv.errors.append("Çizimde kutusu olmayan bileşen(ler): " + ", ".join(missing))
    if ft + 80 > H:
        cv.errors.append(f"Tuval yüksekliği yetmiyor: {ft + 80} > {H}")
    if cv.errors:
        raise BuildError("\n".join(cv.errors))
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["arrow"]}"/></marker></defs>')
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            'aria-labelledby="bpTitle bpDesc">'
            '<title id="bpTitle">Evren CMDB — büyük resim</title>'
            '<desc id="bpDesc">Kaynak rolleri, alım, ara katman, kayıt sistemi, tüketim; ServiceNow geçiş şeridi; '
            'güvenlik ve yönetişim; yol haritası fazları. Her bileşende tamamlanma yüzdesi.</desc>')
    return head + defs + f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>' + "".join(cv.parts) + "</svg>\n"


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

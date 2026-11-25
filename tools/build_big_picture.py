#!/usr/bin/env python3
"""Tek sayfalık "büyük resim" çizimini üretir: kaynak rolleri → alım → ara katman → kayıt sistemi → tüketim,
ServiceNow geçiş şeridi, güvenlik şeridi ve fazlar.

Çıktı: apps/web/content/big-picture.svg (uygulamadaki Büyük Resim sayfası ve paylaşılabilir görünüm).
Kalite kapıları: kutuya sığmayan metin derlemeyi durdurur. CSP uyumlu: <style> ve style="" yok.

Kullanım: python tools/build_big_picture.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape

from _bootstrap import REPO_ROOT
from _font_metrics import BOLD, DEFAULT_EM, REGULAR

OUT = REPO_ROOT / "apps" / "web" / "content" / "big-picture.svg"
ROADMAP = REPO_ROOT / "apps" / "web" / "content" / "roadmap.json"
W, H = 1600, 1048
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


class BuildError(ValueError):
    pass


def tw(text: str, size: float, bold: bool = False) -> float:
    table = BOLD if bold else REGULAR
    return sum(table.get(ch, DEFAULT_EM) for ch in text) * size


def txt(x, y, s, size, fill, *, weight=400, anchor="start", italic=False) -> str:
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{style}>{escape(s)}</text>')


class Canvas:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.errors: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def fit(self, text: str, size: float, width: float, bold: bool, where: str) -> None:
        if tw(text, size, bold) > width:
            self.errors.append(f"Metin sığmıyor ({where}): '{text}' {tw(text, size, bold):.0f}px > {width:.0f}px")

    def box(self, x, y, w, h, kind, title, sub=(), *, align="center", tsize=13.0, ssize=11.0, rx=8):
        k = KINDS[kind]
        dash = f' stroke-dasharray="{k["dash"]}"' if "dash" in k else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{k["fill"]}" stroke="{k["stroke"]}" '
                 f'stroke-width="{k.get("width", 1.2)}"{dash}/>')
        lines = [(title, tsize, True, k["title"])] + [(s, ssize, False, k["sub"]) for s in sub]
        heights = [ln[1] * 1.28 for ln in lines]
        total = sum(heights)
        cy = y + (h - total) / 2
        for (s, size, bold, fill), lh in zip(lines, heights, strict=True):
            cy += lh
            self.fit(s, size, w - 18, bold, title)
            if align == "center":
                self.add(txt(x + w / 2, cy - lh * 0.24, s, size, fill, weight=700 if bold else 400, anchor="middle"))
            else:
                self.add(txt(x + 12, cy - lh * 0.24, s, size, fill, weight=700 if bold else 400))
        if total > h - 6:
            self.errors.append(f"Kutu yüksekliği yetmiyor: {title}")

    def cylinder(self, x, y, w, h, title, sub):
        k = KINDS["store"]
        e = 11
        self.add(f'<path d="M{x},{y + e} v{h - 2 * e} a{w / 2},{e} 0 0 0 {w},0 v-{h - 2 * e}" fill="{k["fill"]}" '
                 f'stroke="{k["stroke"]}"/>')
        self.add(f'<ellipse cx="{x + w / 2}" cy="{y + e}" rx="{w / 2}" ry="{e}" fill="#46536d" stroke="{k["stroke"]}"/>')
        self.fit(title, 13.5, w - 16, True, title)
        self.add(txt(x + w / 2, y + h / 2 + 6, title, 13.5, "#ffffff", weight=700, anchor="middle"))
        for i, s in enumerate(sub):
            self.fit(s, 11, w - 16, False, title)
            self.add(txt(x + w / 2, y + h / 2 + 24 + i * 14, s, 11, "#cdd3de", anchor="middle"))

    def panel(self, x, y, w, h, number, title, caption=""):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{C["panel"]}" stroke="{C["line"]}"/>')
        label = f"{number} · {title}".upper() if number else title.upper()
        self.fit(label, 11.5, w - 24, True, "panel")
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

    # ---------------------------------------------------------------- başlık ve açıklama
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

    top, bottom = 104, 596
    ph = bottom - top
    # ---------------------------------------------------------------- 1 kaynaklar (rol → ürün)
    x1, w1 = 24, 246
    cv.panel(x1, top, w1, ph, "1", "Kaynaklar", "Rol → bugünkü ürün")
    roles = [
        ("role", "Keşif", "BMC Helix Discovery"), ("role", "Sanallaştırma", "VMware vCenter"),
        ("role", "Ağ keşfi", "OpenText NNMi"), ("role", "İzleme", "Zabbix"),
        ("role", "Uç nokta güvenliği", "Microsoft Defender"), ("role_sn", "ITSM / mevcut CMDB", "ServiceNow CMDB"),
        ("role_sn", "Değişiklik kayıtları", "ServiceNow Change"),
        ("new", "+ Yeni rol veya ürün", "bulut, Kubernetes, yedekleme…"),
    ]
    ry = top + 56
    for kind, role, product in roles:
        cv.box(x1 + 12, ry, w1 - 24, 46, kind, role, [product], align="left", tsize=12.5, ssize=11)
        ry += 54

    # ---------------------------------------------------------------- 2 alım
    x2, w2 = 296, 196
    cv.panel(x2, top, w2, ph, "2", "Alım", "Bağlayıcı CMDB'ye yazmaz")
    steps = [("engine", "Bağlayıcı", ["ürüne özel · çeker,", "sayfalar, salt okunur"]),
             ("engine", "YAML eşleme", ["kaynak alanı →", "ServiceNow alan adı"]),
             ("security", "Alım geçidi", ["mTLS · şema ·", "kaynak başına yetki"]),
             ("store2", "Kafka olay omurgası", ["yeniden oynatılabilir", "+ ham yük arşivi"])]
    by = top + 60
    for i, (kind, title, sub_) in enumerate(steps):
        cv.box(x2 + 14, by, w2 - 28, 74, kind, title, sub_, tsize=12.5)
        if i < len(steps) - 1:
            cv.arrow([(x2 + w2 / 2, by + 74), (x2 + w2 / 2, by + 98)])
        by += 98 + 6

    # ---------------------------------------------------------------- 3 ara katman
    x3, w3 = 518, 518
    cv.panel(x3, top, w3, ph, "3", "Ara katman — anlamlandırma", "Evren'in asıl değeri; golden record'a tek yazan uzlaştırıcıdır")
    cols = [x3 + 14, x3 + 14 + 170, x3 + 14 + 340]
    cw, rh = 156, 78
    rows = [top + 70, top + 210, top + 350]
    grid = {
        (0, 0): ("engine", "Değişim süzgeci", ["içerik özeti aynıysa", "yalnız last_seen"]),
        (1, 0): ("engine", "Normalizasyon", ["abc_1 = ABC-1", "= abc-1.corp"]),
        (2, 0): ("engine", "Kimlik çözümleme", ["IRE kuralı + skor", "+ çelişki denetimi"]),
        (0, 1): ("security", "Devre kesici", ["kısmi çalışmada", "eskime ve silme yok"]),
        (1, 1): ("human", "Eşleştirme kuyruğu", ["insan karar verir;", "birleştirme geri alınır"]),
        (2, 1): ("engine", "Uzlaştırma", ["rol önceliği · tazeleme", "öznitelik başına"]),
        (0, 2): ("engine", "Kalite ve kapsam", ["mükerrer, eksik izleme,", "gölge mod raporları"]),
        (1, 2): ("human", "Yapay zekâ önerisi", ["gerekçesiyle kayıt;", "insan onaylar"]),
        (2, 2): ("engine", "Yetkisiz değişiklik", ["Change penceresi dışı", "değişen öznitelik"]),
    }
    for (c, r), (kind, title, sub_) in grid.items():
        cv.box(cols[c], rows[r], cw, rh, kind, title, sub_, tsize=12.5, ssize=10.8)
    mid = rh / 2
    cv.arrow([(cols[0] + cw, rows[0] + mid), (cols[1] - 2, rows[0] + mid)])
    cv.arrow([(cols[1] + cw, rows[0] + mid), (cols[2] - 2, rows[0] + mid)])
    cv.arrow([(cols[2] + cw / 2, rows[0] + rh), (cols[2] + cw / 2, rows[1] - 2)], label="eşleşti",
             label_at=(cols[2] + cw / 2 + 32, rows[0] + rh + 34))
    cv.arrow([(cols[2] + 14, rows[0] + rh), (cols[1] + cw - 14, rows[1] - 2)], label="şüpheli",
             label_at=(cols[1] + cw - 30, rows[0] + rh + 26))
    cv.arrow([(cols[1] + cw, rows[1] + mid), (cols[2] - 2, rows[1] + mid)])
    cv.arrow([(cols[1] + cw / 2, rows[2]), (cols[1] + cw / 2, rows[1] + rh + 2)], label="öneri",
             label_at=(cols[1] + cw / 2 + 24, rows[1] + rh + 34))
    cv.arrow([(cols[2] + cw / 2, rows[1] + rh), (cols[2] + cw / 2, rows[2] - 2)])
    note = "Her kararın gerekçesi saklanır: kaynak, kural, ezilen değerler."
    cv.fit(note, 11.5, w3 - 28, False, "not")
    cv.add(txt(x3 + 16, bottom - 18, note, 11.5, C["muted"]))

    # ---------------------------------------------------------------- 4 kayıt sistemi
    x4, w4 = 1062, 230
    cv.panel(x4, top, w4, ph, "4", "Kayıt sistemi")
    cv.cylinder(x4 + 16, top + 44, w4 - 32, 112, "PostgreSQL", ["golden record", "cmdb_ci · sys_id · ilişkiler"])
    stores = [("store2", "Metadata sözlüğü", ["sınıf, alan, kaynak rolleri"]),
              ("store2", "Kaynak bilgisi", ["öznitelik başına kazanan"]),
              ("store2", "Denetim izi", ["salt ekleme → SIEM"]),
              ("store2", "Outbox", ["tek işlemde olay"]),
              ("cache", "OpenSearch · Valkey", ["arama ve önbellek; SoR değil"])]
    sy = top + 172
    for kind, title, sub_ in stores:
        cv.box(x4 + 16, sy, w4 - 32, 54, kind, title, sub_, tsize=12.5, ssize=10.8)
        sy += 62

    # ---------------------------------------------------------------- 5 tüketim
    x5, w5 = 1318, 258
    cv.panel(x5, top, w5, ph, "5", "Tüketim ve entegrasyon")
    cons = [("consumer", "Evren arayüzü", ["CI 360° · kuyruk · kaynaklar"]),
            ("consumer", "REST API", ["+ ServiceNow uyumlu Table/IRE"]),
            ("consumer", "Olay akışı", ["CloudEvents · imzalı webhook"]),
            ("consumer", "Evren MCP sunucusu", ["salt okuma + öneri"]),
            ("external", "GRC Platformu (tedarik)", ["CI verisi + otomatik kontroller", "provenance · inconclusive"]),
            ("external", "Kurum MCP uygulaması", ["yapay zekâ ajanları"])]
    cy = top + 44
    for kind, title, sub_ in cons:
        hgt = 70 if len(sub_) > 1 else 58
        cv.box(x5 + 14, cy, w5 - 28, hgt, kind, title, sub_, tsize=12.5, ssize=10.8)
        cy += hgt + 12

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
    st, sh = ny + 48, 140
    cv.panel(24, st, W - 48, sh, "6", "ServiceNow geçişi — Evren ana kaynak, SN CMDB replika")
    lane = [("sn", "SN CMDB ilk yükleme", ["sys_id korunur (F0)"], 196),
            ("store", "Evren golden record", ["ana kaynak"], 196),
            ("engine", "sn-sync", ["kapsamlı itme: süreç sınıfları"], 214),
            ("sn", "SN IRE API", ["/identifyreconcile"], 176),
            ("sn", "SN CMDB (replika)", ["aynı sys_id"], 176),
            ("sn", "task.cmdb_ci", ["Incident/Change kuralları aynı"], 214),
            ("external", "CI seçici (F3)", ["SN formunda Evren araması"], 196)]
    gap = (W - 48 - 32 - sum(item[3] for item in lane)) / (len(lane) - 1)
    lx, ly2, lh = 40.0, st + 40, 56
    centers = []
    for kind, title, sub_, wd in lane:
        cv.box(lx, ly2, wd, lh, kind, title, sub_, tsize=12.5, ssize=10.8)
        centers.append((lx, wd))
        lx += wd + gap
    for i in range(len(lane) - 2):
        (ax, aw), (bx, _) = centers[i], centers[i + 1]
        cv.arrow([(ax + aw, ly2 + lh / 2), (bx - 2, ly2 + lh / 2)])
    (fx, fw), (gx, _) = centers[5], centers[6]
    cv.arrow([(gx, ly2 + lh / 2), (fx + fw + 2, ly2 + lh / 2)], dashed=True)
    (ex, ew), (bx2, bw2) = centers[4], centers[1]
    ry2 = ly2 + lh + 26
    cv.arrow([(ex + ew / 2, ly2 + lh), (ex + ew / 2, ry2), (bx2 + bw2 / 2, ry2), (bx2 + bw2 / 2, ly2 + lh + 2)],
             dashed=True)
    back = "SN'de elle yapılan düzenleme ITSM kaynağı olarak geri çekilir ve uzlaştırmadan geçer"
    cv.add(f'<rect x="{(ex + bx2 + bw2) / 2 - tw(back, 11) / 2 - 8:.1f}" y="{ry2 - 9}" width="{tw(back, 11) + 16:.1f}" '
           f'height="18" fill="{C["panel"]}"/>')
    cv.add(txt((ex + bx2 + bw2) / 2, ry2 + 4, back, 11, C["muted"], anchor="middle", italic=True))

    # ---------------------------------------------------------------- 7 güvenlik
    gt, gh = st + sh + 14, 104
    cv.panel(24, gt, W - 48, gh, "7", "Güvenlik ve yönetişim — her katmanda")
    chips = [("Entra ID", "OIDC + PKCE"), ("RBAC + ABAC", "alan hassasiyeti"), ("Görevler ayrılığı", "veritabanında"),
             ("Şema değişikliği", "taslak → onay → yayın"), ("Tek yazar", "uzlaştırıcı"), ("En az yetki", "DB rolleri"),
             ("Denetim izi", "salt ekleme → SIEM"), ("mTLS + sır kasası", "repoda sır yok"),
             ("Tedarik zinciri", "hash kilidi, tarama"), ("Yapay zekâ önerir", "insan onaylar · 42001")]
    cwid = (W - 48 - 32 - 9 * 10) / 10
    for i, (a, b) in enumerate(chips):
        cv.box(40 + i * (cwid + 10), gt + 40, cwid, 48, "security", a, [b], tsize=11.5, ssize=10.5)

    # ---------------------------------------------------------------- fazlar
    ft = gt + gh + 14
    current = next((p for p in roadmap["phases"] if any(i["status"] != "done" for i in p["items"])), roadmap["phases"][-1])
    cv.add(txt(24, ft + 14, "YOL HARİTASI", 11.5, C["accent"], weight=700))
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

    if cv.errors:
        raise BuildError("\n".join(cv.errors))
    defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["arrow"]}"/></marker></defs>')
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            'aria-labelledby="bpTitle bpDesc">'
            '<title id="bpTitle">Evren CMDB — büyük resim</title>'
            '<desc id="bpDesc">Kaynak rolleri, alım, ara katman, kayıt sistemi, tüketim; ServiceNow geçiş şeridi; '
            'güvenlik ve yönetişim; yol haritası fazları.</desc>')
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

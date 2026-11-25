"""Tarayıcı duman testi: kabuk ve tüm ekranlar.

Gerçek Chromium ile, üretimle aynı güvenlik başlıklarını uygulayan yerel sunucu üzerinde çalışır.
Konsol hatası veya CSP ihlali testi başarısız kılar.

Çalıştırma:  python -m pytest apps/web/tests
Ekran görüntüsü: EVREN_SHOTS=build/shots python -m pytest apps/web/tests
Chromium yolu: EVREN_CHROME=/usr/bin/google-chrome (yoksa Playwright'ın kendi Chromium'u)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "packages" / "evren-core" / "src"))

playwright = pytest.importorskip("playwright.sync_api")

from dev_server import make_server  # noqa: E402

SHOTS = os.environ.get("EVREN_SHOTS")
DEMO = json.loads((REPO / "apps/web/content/demo.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def base_url():
    server = make_server(0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        kwargs = {"args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]}
        if os.environ.get("EVREN_CHROME"):
            kwargs["executable_path"] = os.environ["EVREN_CHROME"]
        b = p.chromium.launch(**kwargs)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="tr-TR")
    pg = context.new_page()
    problems: list[str] = []
    pg.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.problems = problems  # type: ignore[attr-defined]
    yield pg
    context.close()
    assert not problems, f"Konsol hatası/CSP ihlali: {problems}"


def shot(pg, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False, timeout=20000)


def detail_open(pg) -> bool:
    return "open" in (pg.get_attribute("#detail", "class") or "")


def test_security_headers_and_static_scope(base_url):
    with urllib.request.urlopen(base_url + "/") as res:  # noqa: S310 - yerel test sunucusu
        csp = res.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert res.headers["X-Frame-Options"] == "DENY"
        assert res.headers["X-Content-Type-Options"] == "nosniff"
    for path in ("/content/architecture.yaml", "/../README.md", "/%2e%2e/README.md", "/tests/test_web_smoke.py", "/assets/"):
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base_url + path)  # noqa: S310
        assert err.value.code == 404, path


def test_overview_and_shell(page, base_url):
    page.goto(base_url + "/")
    page.wait_for_selector("#overviewStats")
    assert page.title() == "Genel Bakış · Evren CMDB"
    assert page.locator("#nav a[aria-current=page]").inner_text().strip() == "Genel Bakış"
    pending = DEMO["summary"]["queue_review"] + DEMO["summary"]["queue_insufficient"]
    page.wait_for_selector("#nav .nav-count")
    assert page.locator("#nav .nav-count").inner_text() == str(pending)
    assert page.locator(".domain").count() >= 2
    shot(page, "web_overview")

    # Kenar menü grubu daraltılır ve açılır (klavyeyle erişilebilir düğme)
    title = page.locator(".nav-title", has_text="Platform")
    title.click()
    assert title.get_attribute("aria-expanded") == "false"
    title.click()
    assert title.get_attribute("aria-expanded") == "true"

    # Yardım: ? tuşu, Esc kapatır
    page.locator("body").press("?")
    page.wait_for_selector(".dialog")
    assert "yardım" in page.locator("#dialogTitle").inner_text()
    page.keyboard.press("Escape")
    assert page.locator(".dialog").count() == 0

    # Kart filtreyle CI listesini açar
    page.click("text=İzlenmeyen sunucu")
    page.wait_for_selector("#ciCount")
    assert page.input_value("#ciScope") == "izlenmeyen"
    assert page.locator("#ciCount").inner_text() == f"{DEMO['summary']['unmonitored']} kayıt"


def test_search_palette(page, base_url):
    page.goto(base_url + "/#/genel-bakis")
    page.wait_for_selector("#overviewStats")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#paletteInput")
    page.fill("#paletteInput", "ist-db-01")
    page.wait_for_selector(".pal-list [role=option]")
    page.keyboard.press("Enter")
    page.wait_for_function("location.hash.startsWith('#/ci/')")
    page.wait_for_selector("#detail.open")
    assert page.locator("#detailTitle").inner_text().lower() == "ist-db-01"


def test_ci_360_provenance(page, base_url):
    page.goto(base_url + "/#/ci")
    page.wait_for_selector("#ciCount")
    assert page.locator("#ciCount").inner_text() == f"{len(DEMO['cis'])} kayıt"
    page.fill("#ciSearch", "ist-muh-03")
    page.locator("tbody tr").first.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#detail.open")
    assert page.evaluate("document.activeElement.id") == "detailTitle"
    body = page.locator("#detailContent")
    assert "Yetkisiz kaynak denemesi" in body.inner_text()
    first = page.locator("#detailContent details.attr").first
    first.locator("summary").click()
    assert "Gerekçe" in first.inner_text()
    shot(page, "web_ci_detail")
    page.keyboard.press("Escape")
    assert not detail_open(page)
    assert page.evaluate("location.hash") == "#/ci"
    assert page.evaluate("document.activeElement.tagName") == "TR"


def test_queue_review_and_decision(page, base_url):
    review = next(q for q in DEMO["queue"] if q["kind"] == "review" and len(q["candidates"]) > 1)
    page.goto(base_url + f"/#/kuyruk/{review['id']}")
    page.wait_for_selector("#detail.open")
    assert page.locator("#detailKicker").inner_text().endswith(review["id"])
    assert page.locator("table.compare td.diff").count() >= 1 or page.locator("table.compare td.same").count() >= 1
    assert page.locator("input[name=cand]").count() == len(review["candidates"])
    shot(page, "web_queue_detail")
    page.click("#mergeBtn")
    assert not detail_open(page)
    page.wait_for_selector("#toast.show")
    page.click("#tab-decided")
    assert page.locator("#view tbody tr").count() == 1


def test_sources_breaker_and_roles(page, base_url):
    page.goto(base_url + "/#/kaynaklar")
    page.wait_for_selector("tbody tr")
    assert page.locator("#view tbody tr").count() >= len(DEMO["sources"])
    page.goto(base_url + "/#/kaynaklar/NNMi")
    page.wait_for_selector("#detail.open")
    text = page.locator("#detailContent").inner_text()
    assert "karantinada" in text and "Ağ keşfi" in text
    assert page.locator("svg.runs rect").count() >= 5
    shot(page, "web_source_detail")


def test_rules_grid_uses_roles_not_products(page, base_url):
    page.goto(base_url + "/#/kurallar")
    page.wait_for_selector("#ruleGrid")
    headers = page.locator("#ruleGrid th.role").all_text_contents()
    assert any(h.startswith("Keşif") and "BMC" in h for h in headers)
    page.click("#tab-tanimlama")
    assert page.locator("text=Evren uzantısı").count() >= 1


def test_controls_inconclusive_principle(page, base_url):
    page.goto(base_url + "/#/kontroller")
    page.wait_for_selector("tbody tr")
    assert page.locator("#view tbody tr").count() == len(DEMO["controls"])
    page.goto(base_url + "/#/kontroller/CMDB-01")
    page.wait_for_selector("#detail.open")
    assert "collection_status" in page.locator("#detailContent").inner_text()


def test_big_picture(page, base_url):
    page.goto(base_url + "/#/buyuk-resim")
    page.wait_for_selector(".bp-frame svg")
    assert page.locator(".bp-frame svg text", has_text="Kaynaklar").count() >= 1
    assert page.get_attribute("#bpExport", "download") == "Evren_Buyuk_Resim.svg"
    shot(page, "web_big_picture")


def test_architecture_map_interactions(page, base_url):
    page.goto(base_url + "/#/mimari")
    page.wait_for_selector("#architectureMap")
    assert page.locator(".chapter-nav button").count() == 6
    assert page.locator("#architectureMap .node").count() >= 50
    width_fit = int(page.get_attribute("#architectureMap", "width"))
    page.click("#mapZoomIn")
    assert int(page.get_attribute("#architectureMap", "width")) > width_fit
    page.click("#mapFit")
    assert int(page.get_attribute("#architectureMap", "width")) == width_fit
    assert page.get_attribute("#mapExport", "download") == "Evren_Mimari_Harita.svg"

    page.click('[data-node="identity"]')
    panel = page.locator("#mapDetail")
    assert "Kimlik çözümleme" in panel.inner_text()
    assert "F0-02" in panel.inner_text()
    assert page.evaluate("location.hash") == "#/mimari/identity"
    shot(page, "web_architecture_detail")

    page.keyboard.press("Escape")
    assert page.locator("#mapDetail h2").count() == 0

    page.fill("#mapSearch", "kafka")
    assert "hit" in page.get_attribute('[data-node="kafka"]', "class")
    assert "dim" in page.get_attribute('[data-node="entra"]', "class")
    page.fill("#mapSearch", "")

    page.focus('[data-node="sod"]')
    page.keyboard.press("Enter")
    assert "Görevler ayrılığı" in page.locator("#mapDetail h2").inner_text()

    page.locator("#mapDetail a", has_text="F0-04").click()
    page.wait_for_selector("#phases")
    assert page.evaluate("location.hash") == "#/yol-haritasi/F0/F0-04"
    page.wait_for_function("document.activeElement && document.activeElement.id === 'F0-04'")


def test_architecture_deep_link(page, base_url):
    page.goto(base_url + "/#/mimari/sn_ire")
    page.wait_for_selector("#mapDetail h2")
    assert page.locator("#mapDetail h2").inner_text() == "SN IRE API"
    assert "selected" in page.get_attribute('[data-node="sn_ire"]', "class")


def test_roadmap_filters_and_links(page, base_url):
    page.goto(base_url + "/#/yol-haritasi")
    page.wait_for_selector("#phases .phase")
    assert page.locator("#phases .phase").count() == 5
    assert page.locator(".step.current").count() == 1
    assert page.locator("#phases .need").count() >= 5
    shot(page, "web_roadmap")

    page.click('[data-status="done"]')
    chips = page.locator("#phases .item .chip.st-done").count()
    assert chips > 0 and chips == page.locator("#phases .item").count()
    page.click('[data-status="in_progress"]')
    assert page.locator("#phases .item").count() >= 1
    page.click('[data-status="all"]')

    page.fill("#roadmapSearch", "eşleştirme kuyruğu")
    assert page.locator("#phases .item").count() >= 1
    page.fill("#roadmapSearch", "")

    page.locator("#F0-02 .chip.link", has_text="Kimlik çözümleme").click()
    page.wait_for_selector("#mapDetail h2")
    assert page.evaluate("location.hash") == "#/mimari/identity"


def test_mobile_layout_has_no_horizontal_overflow(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for route in ("#/genel-bakis", "#/kuyruk", "#/ci", "#/kaynaklar", "#/kurallar", "#/kontroller", "#/buyuk-resim",
                  "#/yol-haritasi"):
        pg.goto(base_url + "/" + route)
        pg.wait_for_selector("#view > *")
        pg.wait_for_timeout(150)
        overflow = pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
        assert overflow <= 1, f"{route}: yatay taşma {overflow}px"
    # Menü düğmesi kenar menüyü açar
    pg.click("#menuToggle")
    assert pg.get_attribute("#menuToggle", "aria-expanded") == "true"
    shot(pg, "web_mobile_menu")
    context.close()
    assert not errors

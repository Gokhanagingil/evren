"""Tarayıcı duman testi: Genel Bakış, Mimari Harita ve Yol Haritası.

Gerçek Chromium ile, üretimle aynı güvenlik başlıklarını uygulayan yerel sunucu üzerinde çalışır.
Konsol hatası veya CSP ihlali testi başarısız kılar.

Çalıştırma:  python -m pytest apps/web/tests
Ekran görüntüsü: EVREN_SHOTS=build/shots python -m pytest apps/web/tests
Chromium yolu: EVREN_CHROME=/usr/bin/google-chrome (yoksa Playwright'ın kendi Chromium'u)
"""
from __future__ import annotations

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


def test_overview(page, base_url):
    page.goto(base_url + "/")
    page.wait_for_selector("#overviewStats")
    assert page.title() == "Genel Bakış · Evren CMDB"
    assert page.locator("#nav a[aria-current=page]").inner_text() == "Genel Bakış"
    shot(page, "web_overview")


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
    shot(page, "web_roadmap")

    page.click('[data-status="done"]')
    chips = page.locator("#phases .item .chip.st-done").count()
    assert chips > 0 and chips == page.locator("#phases .item").count()
    page.click('[data-status="blocked"]')
    assert "BMC" in page.locator("#phases .item").first.inner_text()
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
    for route in ("#/genel-bakis", "#/yol-haritasi"):
        pg.goto(base_url + "/" + route)
        pg.wait_for_selector("#view > *")
        overflow = pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
        assert overflow <= 1, f"{route}: yatay taşma {overflow}px"
    shot(pg, "web_roadmap_mobile")
    context.close()
    assert not errors

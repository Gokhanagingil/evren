"""Tarayıcı testi — API modu: gerçek uvicorn + FastAPI + PostgreSQL (evren_app_api rolü), dev oturumu.

Gerekenler: göç + tohum + demo verisi yüklü veritabanı (tools/load_demo_pg.py) ve ``EVREN_TEST_PG=1``.
Konsol hatası veya CSP ihlali testi başarısız kılar.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/api", REPO / "services/reconciler", REPO / "services/api/tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)
playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect
uvicorn = pytest.importorskip("uvicorn")

from evren_api import create_app  # noqa: E402
from evren_api.config import Settings  # noqa: E402

SHOTS = os.environ.get("EVREN_SHOTS")


@pytest.fixture(scope="module")
def base_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": "evren_reader,evren_match_reviewer",
                                  "EVREN_DEV_USER": "arayuz-testi", "EVREN_DEV_NAME": "Arayüz Testi",
                                  "EVREN_DB_POOL_SIZE": "4"})
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


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
    yield pg
    context.close()
    assert not problems, f"Konsol hatası/CSP ihlali: {problems}"


def shot(pg, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False, timeout=20000)


def test_overview_in_api_mode_shows_identity_and_synthetic_flag(page, base_url):
    page.goto(base_url + "/")
    page.wait_for_selector("#overviewStats")
    page.wait_for_selector("body[data-mode=api]")
    assert page.locator(".identity strong").inner_text() == "Arayüz Testi"
    assert "Eşleştirme analisti" in page.locator(".identity span").inner_text()
    assert page.locator(".demo-flag").is_visible()  # veritabanındaki veri sentetik (load_demo_pg)
    shot(page, "api_overview")


def test_ci_search_detail_and_history_come_from_api(page, base_url):
    page.goto(base_url + "/#/ci")
    page.wait_for_selector("#ciCount")
    page.fill("#ciSearch", "ist-db-01")
    # Sıkı CSP ('unsafe-eval' yok) metin ifadesiyle beklemeyi de engeller; yerleşik beklemeler kullanılır.
    # İki SN mükerrer kaydı + aynı adlı VM örneği (ad araması)
    expect(page.locator("#ciCount")).to_have_text(re.compile(r"^[2-9] kayıt"))
    assert all("ist-db-01" in t.lower() for t in page.locator("#view tbody tr .cell-primary").all_inner_texts())
    page.locator("#view tbody tr").first.click()
    page.wait_for_selector("#detail.open")
    page.wait_for_selector("#ciHistory li")
    assert "→" in page.locator("#ciHistory li").first.inner_text()
    assert page.locator("#detailContent details.attr").count() > 5
    shot(page, "api_ci_detail")


def test_queue_decision_is_persisted(page, base_url):
    from scenarios import make_review_item  # API testleriyle aynı kendi kendine yeterli senaryo

    item, target, _key = make_review_item()
    page.goto(base_url + f"/#/kuyruk/{item}")
    page.wait_for_selector("#detail.open")
    page.fill("#decisionNote", "Seri numarası doğrulandı")
    page.check(f"input[name=cand][value='{target}']")
    page.click("#mergeBtn")
    page.wait_for_selector("#toast.show")
    assert "Birleştirildi" in page.locator("#toast").inner_text()
    # Sayfa yeniden yüklense de karar kalıcıdır
    page.goto(base_url + "/#/kuyruk")
    page.wait_for_selector("#tab-decided")
    page.click("#tab-decided")
    expect(page.locator("#tab-decided")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#view tbody")).to_contain_text("Birleştirildi")
    shot(page, "api_queue_decided")

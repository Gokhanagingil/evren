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
    roles = "evren_reader,evren_match_reviewer,evren_cmdb_editor,evren_data_steward,evren_schema_designer"
    settings = Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": roles,
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
    expect(page.locator("#workerStatus")).to_contain_text("Uzlaştırıcı")  # çalışan durumu (göç 0009)
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


def test_split_from_ci_panel_end_to_end(page, base_url):
    """Yanlış birleştirmeyi CI panelinden ayırma: etkilenecek değerler önceden gösterilir, istek kalıcıdır,
    uzlaştırıcı uyguladıktan sonra kayıt yeni CI'dadır."""
    from scenarios import apply_pending_splits, make_wrong_merge

    ci, links, tag = make_wrong_merge()
    page.goto(base_url + f"/#/ci/{ci}")
    page.wait_for_selector("#detail.open #ciLinks")
    button = page.locator(f"button[data-split='{links['vCenter']}']")
    assert page.locator("button[data-split]").count() == 2
    # Klavye: Enter ile açılır, Esc ile kapanır, odak düğmeye döner
    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#splitNote")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator(".dialog")).to_have_count(0)
    expect(button).to_be_focused()
    assert page.locator("#detail.open").count() == 1  # Esc yalnızca pencereyi kapatır
    button.click()
    expect(page.locator(".dialog .notice")).to_contain_text("temizlenir")
    page.fill("#splitNote", "Seri numarası başka sunucuya ait")
    shot(page, "api_split_dialog")
    page.click("#splitConfirm")
    expect(page.locator("#toast")).to_contain_text("Ayırma isteği kaydedildi")
    expect(page.locator("#ciLinks")).to_contain_text("Ayırma bekliyor")
    expect(page.locator("#ciSplits")).to_contain_text("Uygulanmayı bekliyor")
    shot(page, "api_split_pending")

    [new] = apply_pending_splits()
    page.reload()
    page.wait_for_selector("#detail.open #ciSplits")
    expect(page.locator("#ciSplits")).to_contain_text("Uygulandı")
    assert page.locator("button[data-split]").count() == 0  # tek kaynak kaldı
    page.click("#ciSplits a")
    expect(page.locator("#detailTitle")).to_have_text(f"wm-{tag}")
    assert page.url.endswith(f"#/ci/{new}")
    expect(page.locator("#ciLinks")).to_contain_text("Ayrıldı (insan kararı)")
    shot(page, "api_split_applied")


def test_manual_edit_from_ci_panel(page, base_url):
    """Elle düzenleme: yalnızca değişen alanlar gönderilir; uzlaştırıcı uyguladıktan sonra değer Manual kaynaklıdır."""
    from scenarios import apply_pending_edits, make_ci

    ci, name = make_ci()
    page.goto(base_url + f"/#/ci/{ci}")
    page.wait_for_selector("#detail.open #ciEdit")
    page.click("#ciEdit")
    expect(page.locator(".dialog legend").first).to_have_text("Yönetişim alanları")
    page.click("#editSave")  # hiçbir şey değişmedi: istek gönderilmez
    expect(page.locator("#toast")).to_have_text("Değişiklik yok.")
    page.select_option("#edit-used_for", "Production")
    group = page.locator("#edit-support_group option").nth(1)
    expect(group).to_be_attached()
    page.select_option("#edit-support_group", index=1)
    page.fill("#editNote", "Sahiplik devri")
    shot(page, "api_edit_dialog")
    page.click("#editSave")
    expect(page.locator("#toast")).to_contain_text("Düzenleme kaydedildi")
    expect(page.locator("#ciEdits")).to_contain_text("Uygulanmayı bekliyor")

    assert apply_pending_edits() == ["2 alan uygulandı"]
    page.reload()
    page.wait_for_selector("#detail.open #ciEdits")
    expect(page.locator("#ciEdits")).to_contain_text("Kullanım amacı: uygulandı")
    used_for = page.locator("details.attr", has_text="Kullanım amacı")
    expect(used_for.locator(".src")).to_have_text("Manual")
    assert name in page.locator("#detailTitle").inner_text()
    shot(page, "api_edit_applied")


def test_shadow_report_and_evidence_snapshot(page, base_url):
    """Gölge mod raporu: kesin değil uyarısı, mükerrer önerisi, kapsam sekmeleri (klavye), kanıt kaydı ve indirme."""
    page.goto(base_url + "/#/golge-mod")
    page.wait_for_selector("#shadowStats")
    expect(page.locator("#shadowStatus")).to_contain_text("Kesin değil")
    expect(page.locator("#shadowStatus")).to_contain_text("NNMi")
    expect(page.locator("#shadowDuplicates")).to_contain_text("ist-db-01")
    expect(page.locator("#shadowDuplicates")).to_contain_text("emekliye ayırma adayı")
    # Değer farkları: yalnız yazım farkları varsayılan olarak gizli
    real_rows = page.locator("table[aria-label='Değer farkı örnekleri'] tbody tr").count()
    page.check("#driftCosmetic")
    assert page.locator("table[aria-label='Değer farkı örnekleri'] tbody tr").count() > real_rows
    page.focus("#tab-not_in_itsm")
    page.keyboard.press("Enter")
    expect(page.locator("#tab-not_in_itsm")).to_have_attribute("aria-selected", "true")
    expect(page.locator("table[aria-label=\"Keşifte olup mevcut CMDB'de olmayan CI'lar\"]")).to_be_visible()
    page.fill("#snapshotNote", "Tarayıcı testi")
    page.click("#snapshotCreate")
    expect(page.locator("#toast")).to_contain_text("değiştirilemez kanıt")
    first = page.locator("#shadowSnapshots li").first
    expect(first).to_contain_text("Tarayıcı testi")
    expect(first).to_contain_text("Kesin değil")
    href = first.locator("a").get_attribute("href")
    assert href.startswith("/api/v1/reports/shadow/snapshots/")
    shot(page, "api_shadow_report")


def test_shadow_report_mobile_has_no_horizontal_overflow(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    pg = context.new_page()
    pg.goto(base_url + "/#/golge-mod")
    pg.wait_for_selector("#shadowStats")
    pg.wait_for_timeout(150)
    assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1
    context.close()


def test_change_set_draft_submit_then_other_person_approves_and_publishes(page, base_url):
    """Şema değişiklik seti: arayüzde hazırla ve gönder; onay ve yayın başka bir kişi (görevler ayrılığı)."""
    import uuid

    from conftest import CSRF, make_client

    element = f"u_ui_{uuid.uuid4().hex[:6]}"
    page.goto(base_url + "/#/sema")
    page.wait_for_selector("#csNew")
    page.click("#csNew")
    page.fill("#csName", "Arayüz testi alanı")
    row = page.locator(".op-row").first
    row.locator("select").nth(1).select_option("cmdb_ci_server")  # sınıf
    row.locator("input[type=text]").nth(0).fill(element)
    row.locator("input[type=text]").nth(1).fill("Arayüz testi")
    shot(page, "api_changeset_dialog")
    page.click("#csSave")
    page.wait_for_selector("#detail.open #csChanges")
    expect(page.locator("#csChanges")).to_contain_text(element)
    page.click("#cs-submit")
    expect(page.locator("#detail")).to_contain_text("Onay bekliyor")
    expect(page.locator("#csImpact")).to_contain_text("DDL yok")
    assert page.locator("#cs-approve").count() == 0  # hazırlayan onaylayamaz
    set_id = page.url.rsplit("/", 1)[1]

    with make_client(("evren_reader", "evren_schema_approver"), "arayuz-onayci") as approver:
        assert approver.post(f"/api/v1/schema/change-sets/{set_id}/approve", json={}, headers=CSRF).status_code == 200
        assert approver.post(f"/api/v1/schema/change-sets/{set_id}/publish", headers=CSRF).status_code == 200
    page.reload()
    page.wait_for_selector("#detail.open #csDownload")
    expect(page.locator("#detail")).to_contain_text("Yayınlandı")
    expect(page.locator("#detail")).to_contain_text("metadata/changes/")
    assert page.locator("#csDownload").get_attribute("href").endswith(f"/{set_id}/artifact")
    shot(page, "api_changeset_published")

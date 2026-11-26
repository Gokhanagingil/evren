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
    roles = ("evren_reader,evren_match_reviewer,evren_cmdb_editor,evren_data_steward,evren_schema_designer,"
             "evren_security_reader,evren_ci_owner")
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


def test_palette_global_search_folds_turkish_case_and_opens_ci(page, base_url):
    """Ctrl+K → /api/v1/search (bu testte OpenSearch yok: PostgreSQL yedeği, Türkçe katlama)."""
    page.goto(base_url + "/")
    page.wait_for_selector("#overviewStats")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#paletteInput")
    page.fill("#paletteInput", "İST-DB-01")
    option = page.locator("#paletteList [role=option]").filter(has_text="ist-db-01").first
    expect(option).to_be_visible()
    assert page.locator("#paletteList .pal-group").first.text_content() == "Konfigürasyon öğeleri"
    page.keyboard.press("Enter")
    page.wait_for_selector("#detail.open")
    assert "ist-db-01" in page.locator("#detailContent").inner_text().lower()


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


def test_data_model_counts_from_db_and_add_field_opens_preset_change_set(page, base_url):
    """U4.4: Veri Modeli API modunda canlı metadata + veritabanı CI sayıları; "Bu sınıfa alan ekle" değişiklik
    setini sınıf seçili açar ve kayıttan sonra sete gider."""
    import uuid

    page.goto(base_url + "/#/veri-modeli")
    page.wait_for_selector("#modelStats")
    linux = page.locator("#view tbody tr[data-id='cmdb_ci_linux_server'] td").nth(3).inner_text()
    assert int(linux.split()[0]) > 0  # demo envanteri veritabanında
    assert page.locator("text=Demo modu").count() == 0
    page.goto(base_url + "/#/veri-modeli/cmdb_ci_rack")
    page.wait_for_selector("#detail.open #modelAddField")
    page.click("#modelAddField")
    page.wait_for_selector("#csName")
    row = page.locator(".op-row").first
    assert row.locator("select").nth(0).input_value() == "add_field"
    assert row.locator("select").nth(1).input_value() == "cmdb_ci_rack"
    element = f"u_oda_{uuid.uuid4().hex[:6]}"
    page.fill("#csName", "Kabin odası alanı")
    row.locator("input[type=text]").nth(0).fill(element)
    row.locator("input[type=text]").nth(1).fill("Oda")
    page.click("#csSave")
    page.wait_for_url(re.compile(r".*#/sema/[0-9a-f]{32}$"))  # sıkı CSP: metin değerlendiren bekleme kullanılmaz
    page.wait_for_selector("#detail.open #csChanges")
    expect(page.locator("#csChanges")).to_contain_text(element)
    shot(page, "api_model_change_set")


def test_source_detail_shows_connector_credentials_without_secret(page, base_url):
    """U5.3 (0018): Veri sorumlusu kaynak ayrıntısında bağlayıcı kimliklerini görür; belirteç yalnızca son 4 karakter."""
    import uuid as _uuid

    import psycopg

    from evren_core.credentials import new_token, token_hint, token_sha256

    token = new_token()
    name = f"webtest-{_uuid.uuid4().hex[:8]}"
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint, created_by,
                            expires_at) VALUES (%s, 'Zabbix', %s, %s, 'arayuz-testi', now() + interval '7 days')""",
                     (name, token_sha256(token), token_hint(token)))
    try:
        page.goto(base_url + "/#/kaynaklar/Zabbix")
        page.wait_for_selector("#detail.open")
        section = page.locator("table[aria-label='Bağlayıcı kimlikleri']")
        expect(section).to_contain_text(name)
        expect(section).to_contain_text("Etkin")
        text = page.locator("#detailContent").inner_text()
        assert token not in text and f"…{token_hint(token)}" in text
        assert "Mod" in page.locator("table[aria-label='Çalışma geçmişi'] thead").text_content()
        shot(page, "web_api_source_credentials")
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM ingest.connector_credential WHERE name = %s", (name,))


def test_connector_admin_runs_cancels_and_issues_token_once_with_keyboard(page, base_url):
    """U5.3b (ADR-0021): Veri sorumlusu "Şimdi çalıştır" ile istek açar ve iptal eder; kimlik verir, belirteci bir kez
    görür (pencere kapanınca sayfada kalmaz, veritabanında yalnızca özeti), sonra kimliği iptal eder. Klavyeyle."""
    import uuid as _uuid

    import psycopg

    from evren_core.credentials import token_sha256

    runner, inst = "webtest-runner:1", f"webtest-zbx-{_uuid.uuid4().hex[:6]}"
    cred = f"webtest-{_uuid.uuid4().hex[:8]}"
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind)
                        VALUES (%s, now(), now(), 600, 'connector')
                        ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(), stopped_at = NULL""", (runner,))
        conn.execute("""INSERT INTO ingest.connector_instance (name, connector, data_source, schedule, schedule_tz,
                            next_run_at, max_runtime_minutes, runner_id, seen_at)
                        VALUES (%s, 'zabbix', 'Zabbix', '15 */2 * * *', 'Europe/Istanbul', now() + interval '1 hour',
                                120, %s, now())""", (inst, runner))
    try:
        page.goto(base_url + "/#/kaynaklar/Zabbix")
        page.wait_for_selector("#detail.open")
        instances = page.locator("table[aria-label='Bağlayıcı örnekleri']")
        expect(instances).to_contain_text(inst)
        expect(instances).to_contain_text("Hazır")
        page.locator(f"button[data-run='{inst}']").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#runMode")).to_be_focused()
        page.locator("#runNote").fill("arayüz testi")
        page.locator("#runNote").press("Enter")  # form gönderimi
        requests = page.locator("table[aria-label='Çalıştırma istekleri']")
        expect(requests).to_contain_text("Bekliyor")
        expect(instances).to_contain_text("Çalışma sürüyor")
        shot(page, "web_api_connector_run_request")
        page.locator("button[data-cancel]").first.focus()
        page.keyboard.press("Enter")
        expect(requests).to_contain_text("İptal edildi")

        page.locator("#credIssue").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#credName")).to_be_focused()
        page.keyboard.type(cred)
        page.keyboard.press("Enter")
        field = page.locator("#tokenValue")
        expect(field).to_be_visible()
        token = field.input_value()
        assert len(token) > 20
        expect(page.locator("#tokenCopy")).to_be_focused()
        shot(page, "web_api_connector_token_once")
        with psycopg.connect(autocommit=True) as conn:
            assert conn.execute("SELECT token_sha256 FROM ingest.connector_credential WHERE name = %s",
                                (cred,)).fetchone()[0] == token_sha256(token)
        page.keyboard.press("Escape")
        expect(field).to_have_count(0)
        creds = page.locator("table[aria-label='Bağlayıcı kimlikleri']")
        expect(creds).to_contain_text(cred)
        assert token not in page.content()
        page.locator(f"button[data-revoke='{cred}']").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#revokeGo")).to_be_focused()
        page.keyboard.press("Enter")
        row = creds.locator("tr", has_text=cred)
        expect(row).to_contain_text("İptal edildi")
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM ingest.connector_run_request WHERE instance = %s", (inst,))
            conn.execute("DELETE FROM ingest.connector_instance WHERE name = %s", (inst,))
            conn.execute("DELETE FROM ingest.worker_heartbeat WHERE worker_id = %s", (runner,))
            conn.execute("DELETE FROM ingest.connector_credential WHERE name = %s", (cred,))


def test_security_reader_sees_restricted_posture_with_notice(page, base_url):
    """ADR-0023: Güvenlik okuyucu CI ayrıntısında kısıtlı duruş bölümünü görür (uyarı notuyla); görüntüleme denetimde."""
    import psycopg

    with psycopg.connect(autocommit=True) as conn:
        ci = conn.execute("""SELECT ci.sys_id FROM cmdb.cmdb_ci ci WHERE NOT EXISTS (SELECT 1 FROM
                               cmdb.ci_security_posture p WHERE p.ci_sys_id = ci.sys_id) ORDER BY ci.sys_id LIMIT 1""").fetchone()[0]
        conn.execute("""INSERT INTO cmdb.ci_security_posture (ci_sys_id, data_source, protection_status, health_status,
                                                             exposure_level, risk_score, observed_at)
                        VALUES (%s, 'Defender', 'Onboarded', 'Active', 'High', 'Low', now())""", (ci,))
    try:
        page.goto(base_url + f"/#/ci/{ci}")
        page.wait_for_selector("#detail.open")
        box = page.locator("#detailContent")
        expect(box).to_contain_text("Güvenlik duruşu (kısıtlı)")
        expect(box).to_contain_text("Kısıtlı bilgi.")
        expect(box).to_contain_text("Yüksek")
        shot(page, "web_api_ci_security_posture")
        with psycopg.connect(autocommit=True) as conn:
            assert conn.execute("""SELECT count(*) FROM audit.security_event WHERE action = 'restricted_read'
                                    AND resource = %s""", (f"cmdb_ci:{ci}",)).fetchone()[0] >= 1
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM cmdb.ci_security_posture WHERE ci_sys_id = %s", (ci,))


def test_unauthorized_change_review_with_keyboard(page, base_url):
    """ADR-0025: CI ayrıntısında gözlenen değişiklik ve değişiklik kaydı görünür; Veri sorumlusu yetkisiz değişikliği
    klavyeyle "İncelendi" ve gerekçeli "Yanlış alarm" olarak işaretler, sonra yeniden açar. Karar denetim izinde."""
    import psycopg

    with psycopg.connect(autocommit=True) as conn:
        ci = conn.execute("""SELECT sys_id FROM cmdb.cmdb_ci WHERE sys_class_name LIKE 'cmdb_ci_%%server'
                              ORDER BY sys_id DESC LIMIT 1""").fetchone()[0]
        anomaly = conn.execute(
            """INSERT INTO insight.anomaly (kind, severity, ci_sys_id, data_source, window_start, window_end,
                                            explanation)
               VALUES ('unauthorized_change', 'high', %s, 'BMC', now() - interval '6 hours', now(),
                       'webtest: RAM değişti') RETURNING sys_id""", (ci,)).fetchone()[0]
        conn.execute("""INSERT INTO insight.config_change (ci_sys_id, sys_class_name, attribute, old_value, new_value,
                            data_source, observed_from, observed_at, verdict, anomaly_sys_id, detail)
                        VALUES (%s, 'cmdb_ci_server', 'ram', '8192', '16384', 'BMC', now() - interval '6 hours', now(),
                                'unauthorized', %s, '{"explanation": "kapsayan onaylı değişiklik kaydı yok"}')""",
                     (ci, anomaly))
    try:
        page.goto(base_url + f"/#/ci/{ci}")
        page.wait_for_selector("#ciAnomalies")
        box = page.locator("#detailContent")
        expect(box).to_contain_text("Değişiklik denetimi")
        expect(box).to_contain_text("Onaysız değişiklik.")
        expect(page.locator("#ciObservations")).to_contain_text("RAM (MB)")
        status = page.locator("#ciAnomalies .pills").first  # durum etiketi (düğme etiketleri değil)
        expect(status).to_contain_text("Açık")
        page.locator("#ciAnomalies button[data-review='acknowledged']").focus()
        page.keyboard.press("Enter")
        expect(status).to_contain_text("İncelendi")
        expect(page.locator("#ciAnomalies")).to_be_focused()  # karar sonrası odak sonuçta, panelin başında değil
        page.locator("#ciAnomalies button[data-review='false_positive']").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#reviewNote")).to_be_focused()
        page.keyboard.press("Enter")  # gerekçesiz gönderilemez
        expect(page.locator("#reviewNote")).to_have_attribute("aria-invalid", "true")
        page.locator("#reviewNote").fill("Değişiklik başka bir sistemde kayıtlı (webtest)")
        page.keyboard.press("Enter")
        expect(status).to_contain_text("Yanlış alarm")
        expect(page.locator("#ciAnomalies")).to_contain_text("Gerekçe: Değişiklik başka bir sistemde kayıtlı")
        expect(box).not_to_contain_text("Onaysız değişiklik.")
        expect(page.locator("#ciObservations")).to_contain_text("Karar: Yanlış alarm")
        shot(page, "web_api_unauthorized_change_review")
        page.locator("#ciAnomalies button[data-review='open']").focus()
        page.keyboard.press("Enter")
        expect(status).to_contain_text("Açık")
        with psycopg.connect(autocommit=True) as conn:
            decisions = conn.execute("""SELECT detail->>'to' FROM audit.security_event WHERE action = 'anomaly_review'
                                         AND resource = %s ORDER BY id""", (f"anomaly:{anomaly}",)).fetchall()
            assert [d[0] for d in decisions] == ["acknowledged", "false_positive", "open"]
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM insight.config_change WHERE anomaly_sys_id = %s", (anomaly,))
            conn.execute("DELETE FROM insight.anomaly WHERE sys_id = %s", (anomaly,))


def test_create_ci_with_identity_check_using_keyboard(page, base_url):
    """U6.1 (ADR-0026): "Yeni CI" penceresi; uzlaştırıcı kimlik çözümlemesinden geçirir. Yeni CI açılır ve panelde
    görünür; aynı seriyle ikinci istek açılmaz (var olan CI gösterilir); aynı adlı sanal makine için gerekçeli karar."""
    import uuid as _uuid

    from scenarios import apply_pending_creates

    tag = _uuid.uuid4().hex[:8]
    stop = threading.Event()

    def worker():  # üretimde sürekli çalışan uzlaştırıcı
        while not stop.is_set():
            apply_pending_creates()
            stop.wait(0.3)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        page.goto(base_url + "/#/ci")
        page.wait_for_selector("#ciCount")
        page.locator("#ciCreate").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#createClass")).to_be_focused()
        expect(page.locator("#createClass")).to_have_value("cmdb_ci_linux_server")
        page.wait_for_selector("#edit-name")
        page.fill("#edit-name", f"web-elle-{tag}")
        page.fill("#edit-serial_number", f"WEBELLE{tag.upper()}")
        shot(page, "web_api_create_dialog")
        page.locator("#edit-name").press("Enter")  # örtük form gönderimi
        expect(page.locator("#toast")).to_contain_text(f"CI açıldı: web-elle-{tag}")
        expect(page.locator("#detailTitle")).to_have_text(f"web-elle-{tag}")
        expect(page.locator("#ciLinks")).to_contain_text("Manual")
        created = page.evaluate("location.hash").split("/")[-1]

        # aynı seri: yeni CI açılmaz, var olan gösterilir
        page.keyboard.press("Escape")
        page.locator("#ciCreate").click()
        page.wait_for_selector("#edit-name")
        page.fill("#edit-name", f"baska-{tag}")
        page.fill("#edit-serial_number", f"WEBELLE{tag.upper()}")
        page.click("#createSave")
        result = page.locator("#createResult")
        expect(result).to_contain_text("Bu CI zaten var.")
        expect(result).to_contain_text(f"web-elle-{tag}")
        result.locator("a").click()
        expect(page.locator(".dialog")).to_have_count(0)
        expect(page.locator("#detailTitle")).to_have_text(f"web-elle-{tag}")
        assert page.evaluate("location.hash").endswith(created)

        # sanal makine (ad kuralı yok): aynı ad karar ister; gerekçesiz zorlanamaz, gerekçeyle açılır
        for attempt in range(2):
            page.keyboard.press("Escape")
            page.locator("#ciCreate").click()
            page.select_option("#createClass", "cmdb_ci_vm_instance")
            page.wait_for_selector("#edit-name")
            page.fill("#edit-name", f"vm-web-{tag}")
            page.click("#createSave")
            if attempt == 0:
                expect(page.locator("#toast")).to_contain_text(f"CI açıldı: vm-web-{tag}")
        expect(page.locator("#createResult")).to_contain_text("Karar gerekiyor. Benzer CI'lar var")
        expect(page.locator("#createCandidates")).to_contain_text(f"vm-web-{tag}")
        expect(page.locator("#createReason")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#createReason")).to_have_attribute("aria-invalid", "true")
        page.fill("#createReason", "Aynı adlı ikinci sanal makine (farklı küme)")
        expect(page.locator("#createReason")).not_to_have_attribute("aria-invalid", "true")
        shot(page, "web_api_create_decision")
        page.keyboard.press("Enter")
        expect(page.locator("#toast")).to_contain_text(f"CI açıldı: vm-web-{tag}")
        import psycopg

        with psycopg.connect(autocommit=True) as conn:
            rows = conn.execute("""SELECT status, force_new, note FROM ingest.manual_create
                                    WHERE attributes->>'name' = %s ORDER BY requested_at""",
                                (f"vm-web-{tag}",)).fetchall()
        assert rows == [("created", False, None), ("needs_decision", False, None),
                        ("created", True, "Aynı adlı ikinci sanal makine (farklı küme)")]
    finally:
        stop.set()
        t.join(timeout=5)


def test_create_dialog_mobile_has_no_horizontal_overflow(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    pg = context.new_page()
    pg.goto(base_url + "/#/ci")
    pg.wait_for_selector("#ciCount")
    pg.locator("#ciCreate").click()
    pg.wait_for_selector("#edit-name")
    pg.wait_for_timeout(150)
    assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1
    assert pg.evaluate("document.querySelector('.dialog').scrollWidth - document.querySelector('.dialog').clientWidth") <= 1
    context.close()


def test_manual_relation_add_and_remove_with_keyboard(page, base_url):
    """U6.2 (ADR-0026 §5): CI panelinden izinli ilişki eklenir (Manual kaynağıyla) ve kaldırılır; klavyeyle."""
    import uuid as _uuid

    import psycopg
    from scenarios import apply_pending_relations

    tag = _uuid.uuid4().hex[:6]
    with psycopg.connect(autocommit=True) as conn:
        app = conn.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name) VALUES ('cmdb_ci_appl', %s) RETURNING sys_id",
                           (f"web-app-{tag}",)).fetchone()[0]
        conn.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name) VALUES ('cmdb_ci_linux_server', %s)",
                     (f"web-srv-{tag}",))
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            apply_pending_relations()
            stop.wait(0.3)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        page.goto(base_url + f"/#/ci/{app}")
        page.wait_for_selector("#relAdd")
        page.locator("#relAdd").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#relKind")).to_be_focused()
        label = page.locator("#relKind option", has_text="Üzerinde çalışır → Bilgisayar").first.inner_text()
        page.select_option("#relKind", label=label)
        page.fill("#relSearch", f"web-srv-{tag}")
        expect(page.locator("#relTarget option")).to_have_text([f"web-srv-{tag} · Linux sunucu"])
        page.locator("#relSearch").press("ArrowDown")
        expect(page.locator("#relTarget")).to_be_focused()
        shot(page, "web_api_relation_dialog")
        page.keyboard.press("Enter")
        expect(page.locator("#toast")).to_contain_text("İlişki eklendi.")
        rel = page.locator("#ciRelations li", has_text=f"web-srv-{tag}")
        expect(rel).to_contain_text("Runs on")
        expect(rel).to_contain_text("Manual")
        rel.locator("button[data-unrelate]").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#unrelNote")).to_be_focused()
        page.fill("#unrelNote", "Uygulama taşındı (webtest)")
        page.keyboard.press("Enter")
        expect(page.locator("#toast")).to_contain_text("İlişki kaldırıldı.")
        expect(page.locator("#ciRelations")).to_have_count(0)
    finally:
        stop.set()
        t.join(timeout=5)


def test_csv_import_preview_commit_and_row_results(page, base_url):
    """U6.3 (ADR-0026 §6): CSV önizlemesi (Türkçe başlık, noktalı virgül, satır hataları), onay ve satır sonuçları;
    dosya içi mükerrer satır "Zaten var" olur. Klavye ile erişilebilir düğmeler."""
    import uuid as _uuid

    from scenarios import apply_pending_creates

    tag = _uuid.uuid4().hex[:6].upper()
    content = ("Ad;Seri numarası;Kullanım amacı\n"
               f"web-csv-{tag}-1;WEBCSV{tag}1;Üretim\n"
               f"web-csv-{tag}-2;WEBCSV{tag}2;Test\n"
               f"=CMD();WEBCSV{tag}3;\n"
               f"web-csv-{tag}-4;WEBCSV{tag}1;\n").encode()
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            apply_pending_creates()
            stop.wait(0.3)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        page.goto(base_url + "/#/ci")
        page.wait_for_selector("#ciCount")
        page.locator("#ciImport").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#importClass")).to_be_focused()
        page.set_input_files("#importFile", files=[{"name": "sunucular.csv", "mimeType": "text/csv", "buffer": content}])
        page.click("#importPreview")
        box = page.locator("#importPreviewBox")
        expect(box).to_contain_text("4 satır · 3 geçerli · 1 hatalı")
        expect(box).to_contain_text("ayraç: noktalı virgül")
        expect(box).to_contain_text("Seri numarası")
        expect(box).to_contain_text("Linux sunucu")  # sınıf Türkçe etiketiyle
        expect(box).to_contain_text("formül olabilecek değer")
        expect(page.locator("#importCommit")).to_have_text("İçe aktar (3 satır)")
        expect(page.locator("#importCommit")).to_be_focused()
        shot(page, "web_api_import_preview")
        page.keyboard.press("Enter")
        expect(page.locator("#importProgress")).to_contain_text("İçe aktarma tamamlandı.", timeout=20000)
        expect(page.locator("#importProgress")).to_contain_text("açılan 2 · zaten var 1")
        problems = page.locator("#importProblems")
        expect(problems).to_contain_text("Zaten var")
        expect(problems).to_contain_text(f"web-csv-{tag}-1")
        shot(page, "web_api_import_done")
    finally:
        stop.set()
        t.join(timeout=5)


def test_attestation_bulk_approve_and_reject_with_keyboard(page, base_url):
    """U6.4 (ADR-0027): "Bende" görünümünde toplu onay; CI panelinden gerekçeli ret (Enter/Esc), ret anomali açar;
    sahipsiz görünümü; uzlaştırıcı arka planda uygular."""
    from scenarios import apply_pending_attestations, make_owned_ci

    first, first_name, _ = make_owned_ci("arayuz-testi")
    second, _, _ = make_owned_ci("arayuz-testi", used_for="Test")
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            apply_pending_attestations()
            stop.wait(0.3)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        page.goto(base_url + "/#/sahiplik-onayi")
        expect(page.locator("#attCards")).to_contain_text("Kapsamda")
        expect(page.locator("#tab-mine")).to_have_attribute("aria-selected", "true")
        page.fill("#attSearch", "onay-")
        table = page.locator("#attTable")
        expect(table).to_contain_text(first_name)
        expect(table).to_contain_text("Henüz incelenmedi")
        page.locator(f'input[data-pick="{first}"]').check()
        page.locator(f'input[data-pick="{second}"]').check()
        expect(page.locator("#attBulk")).to_have_text("Seçilenleri onayla (2)")
        expect(page.locator("#attPolicies")).to_contain_text("Kullanım amacı: Üretim")
        assert "null" not in page.locator("main").inner_text()  # boş dallar metin olarak basılmaz
        shot(page, "web_api_attestation_mine")
        page.locator("#attBulk").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#attStatus")).to_contain_text("2 istek uygulandı", timeout=20000)
        row = page.locator("#attTable tr", has_text=first_name)
        expect(row).to_contain_text("Onaylı")
        # CI panelinden ret: neden + gerekçe; Esc vazgeçer, Enter gönderir
        page.goto(base_url + f"/#/ci/{second}")
        box = page.locator("#ciAttestation")
        expect(box).to_contain_text("Onaylı")
        expect(box).to_contain_text("Diğer altyapı")  # Test kullanımı → genel politika (365 gün)
        page.locator("#ciAttestReject").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#attReason")).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#attReason")).to_have_count(0)
        page.locator("#ciAttestReject").click()
        page.select_option("#attReason", "data_wrong")
        page.locator("#attNote").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#attNote")).to_have_attribute("aria-invalid", "true")  # gerekçesiz gönderilmez
        page.fill("#attNote", "Seri numarası kasadakiyle uyuşmuyor")
        page.keyboard.press("Enter")
        expect(page.locator("#ciAttestation")).to_contain_text("Reddedildi", timeout=20000)
        expect(page.locator("#ciAttestation")).to_contain_text("Kayıttaki bilgiler yanlış")
        expect(page.locator("#ciAttestation")).to_contain_text("Veri sorumlusunda")
        shot(page, "web_api_attestation_rejected")
        page.goto(base_url + "/#/sahiplik-onayi")
        page.locator("#tab-orphan").click()
        expect(page.locator("#attList")).to_contain_text("sahibi ve yöneteni boş")
    finally:
        stop.set()
        t.join(timeout=5)


def test_attestation_page_mobile_has_no_horizontal_overflow(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, locale="tr-TR")
    pg = context.new_page()
    pg.goto(base_url + "/#/sahiplik-onayi")
    pg.wait_for_selector("#attCards .metric")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    context.close()


def test_staleness_page_dismiss_and_retire_recheck_with_keyboard(page, base_url):
    """U7.1 (ADR-0028): aday ve eskimiş öneriler; "Koru…" gerekçeyle (Enter), "Emekliye ayır…" uzlaştırıcı yeniden
    denetler (CI görülüyorsa uygulanmaz); CI panelinde Eskime bölümü; Esc pencereyi kapatır."""
    from scenarios import apply_pending_retirements, make_stale_case

    cand_ci, cand_name, _ = make_stale_case("candidate")
    _, stale_name, stale_case = make_stale_case("stale", days=20)
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            apply_pending_retirements()
            stop.wait(0.3)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        page.goto(base_url + "/#/eskime")
        expect(page.locator("#staleCards")).to_contain_text("Emekliye ayırma adayı")
        expect(page.locator("#staleSources")).to_contain_text("BMC")
        page.fill("#staleSearch", cand_name)
        expect(page.locator("#staleTable")).to_contain_text(cand_name)
        assert "null" not in page.locator("main").inner_text()
        shot(page, "web_api_staleness")
        # Eskimiş öneriyi koru (klavye)
        page.locator("#tab-stale").click()
        page.fill("#staleSearch", stale_name)
        expect(page.locator("#staleTable")).to_contain_text(stale_name)
        expect(page.locator(f'button[data-retire="{stale_case}"]')).to_have_count(0)  # eskimiş: emekliye ayrılamaz
        page.locator(f'button[data-dismiss="{stale_case}"]').focus()
        page.keyboard.press("Enter")
        expect(page.locator("#staleNote")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#staleNote")).to_have_attribute("aria-invalid", "true")  # gerekçesiz gönderilmez
        page.keyboard.type("Felaket kurtarma yedeği")
        page.keyboard.press("Enter")
        expect(page.locator("#staleStatus")).to_contain_text("Korundu", timeout=20000)
        # CI panelinden emekliye ayır: CI'ın keşif kaydı görülüyor, uzlaştırıcı uygulamaz
        page.goto(base_url + f"/#/ci/{cand_ci}")
        box = page.locator("#ciStaleness")
        expect(box).to_contain_text("Emekliye ayırma adayı")
        page.locator("#ciRetire").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#staleNote")).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#staleNote")).to_have_count(0)
        page.locator("#ciRetire").click()
        page.fill("#staleNote", "Donanım iade edildi")
        page.keyboard.press("Enter")
        expect(page.locator("#toast")).to_contain_text("yeniden görüldü", timeout=20000)
        expect(page.locator("#ciStaleness")).to_contain_text("Emekliye ayırma adayı")  # CI emekliye ayrılmadı
    finally:
        stop.set()
        t.join(timeout=5)


def test_staleness_page_mobile_has_no_horizontal_overflow(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, locale="tr-TR")
    pg = context.new_page()
    pg.goto(base_url + "/#/eskime")
    pg.wait_for_selector("#staleCards .metric")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    context.close()

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
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/api", REPO / "services/reconciler", REPO / "services/api/tests",
          REPO / "services/governance"):
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
    # U8.7: alan sihirbazı sınıf seçili olarak alan adımında açılır
    expect(page.locator("#wizStepTitle")).to_have_text("2. Alanlar")
    expect(page.locator("#wf0-label")).to_be_focused()
    element = f"u_oda_{uuid.uuid4().hex[:6]}"
    page.keyboard.type("Oda")
    expect(page.locator("#wf0-name")).to_have_value("u_oda")
    page.fill("#wf0-name", element)
    page.click("#wizNext")
    expect(page.locator("#wizValid")).to_contain_text("Doğrulandı")
    expect(page.locator("#wizChanges")).to_contain_text(f"cmdb_ci_rack.{element}")
    page.fill("#wizSetName", "Kabin odası alanı")
    page.click("#wizNext")
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

        # aynı seri: yeni CI açılmaz, var olan gösterilir. Esc önce açık pencereyi kapatır; pencere kapanınca panel kapanır
        expect(page.locator(".overlay")).to_have_count(0)
        page.keyboard.press("Escape")
        expect(page.locator("#detail")).not_to_have_class(re.compile(r"\bopen\b"))
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
            page.wait_for_selector('#createFields[data-class="cmdb_ci_linux_server"]')
            page.fill("#edit-name", f"vm-web-{tag}")
            page.select_option("#createClass", "cmdb_ci_vm_instance")
            # Form yeni sınıfa göre yeniden çizilir; yazılmış ad korunur (yarış yok: data-class çizimden sonra konur)
            page.wait_for_selector('#createFields[data-class="cmdb_ci_vm_instance"]')
            expect(page.locator("#edit-name")).to_have_value(f"vm-web-{tag}")
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


def test_dependent_ci_section_links_parent_and_adapter(page, base_url):
    """U7.2 (ADR-0029): sunucunun panelinde bağımlı ağ adaptörü (yerel anahtarıyla); adaptörün panelinde ana CI."""
    from scenarios import make_dependent_pair

    host, _, name = make_dependent_pair()
    page.goto(base_url + f"/#/ci/{host}")
    box = page.locator("#ciDependents")
    expect(box).to_contain_text("Ağ adaptörü")
    expect(box).to_contain_text("mac_address: 02:00:")
    box.get_by_role("link", name="eth0").click()
    expect(page.locator("#ciDependents")).to_contain_text(name)
    expect(page.locator("#ciDependents")).to_contain_text("bağlamında tanımlanır")
    shot(page, "web_api_dependent")


def test_impact_section_shows_affected_services_and_dependencies(page, base_url):
    """U7.3 (ADR-0030): sunucunun panelinde etkilenen hizmetler (özet + düzey), iş uygulamasının panelinde bağımlılıklar."""
    from scenarios import make_service_chain

    ids = make_service_chain()
    page.goto(base_url + f"/#/ci/{ids['server']}")
    box = page.locator("#ciImpact")
    expect(box).to_contain_text("Bu CI düşerse 3 hizmet veya iş uygulaması etkilenir")
    expect(box).to_contain_text("1 tanesi üretimde")
    # Doğrudan bağlantıda odak panelde kalır: arka planda biten etki/geçmiş istekleri odağı ana alana çekmez
    expect(page.locator("#ciHistory")).not_to_contain_text("Yükleniyor")
    expect(page.locator("#detailTitle")).to_be_focused()
    up = page.locator("#ciImpactUp")
    expect(up).to_contain_text("etki-hizmet-")
    expect(up).to_contain_text("2. düzey")
    shot(page, "web_api_impact")
    up.get_by_role("link", name=re.compile("^etki-uygulama-")).click()
    expect(page.locator("#ciImpactDown")).to_contain_text("etki-srv-")
    assert "null" not in page.locator("#ciImpact").inner_text()


def _local_input(page, when):
    """datetime-local değeri İstanbul saatiyle (arayüz bütün saatleri İstanbul saatiyle gösterir ve girdiyi öyle okur)."""
    return when.astimezone(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%dT%H:%M:%S")


def test_relation_map_levels_keyboard_recenter_and_past_moment(page, base_url):
    """U7.4 (ADR-0031): bağımlılık altta, bağımlı üstte; düğüm klavyeyle seçilir (ilişkiler ve bildiren kaynak);
    "Merkeze al" haritayı taşır; geçmiş an o andaki ilişkileri çizer."""
    from scenarios import make_history_ci

    ids = make_history_ci(gap=1.1)
    page.goto(base_url + f"/#/iliski-haritasi/{ids['web']}")
    nodes = page.locator("#graphScroll .gnode")
    expect(nodes).to_have_count(3)  # web, db, cache (lb elle kaldırıldı)
    expect(page.locator(f'.gnode.center[data-id="{ids["web"]}"]')).to_have_count(1)
    expect(page.locator("#graphSummary")).to_contain_text("3 CI, 2 ilişki")
    web_box = page.locator(f'.gnode[data-id="{ids["web"]}"]').bounding_box()
    db_box = page.locator(f'.gnode[data-id="{ids["db"]}"]').bounding_box()
    assert db_box["y"] > web_box["y"]  # bağımlılık altta
    # Klavye: düğüme odaklan, Enter ile seç; sağda ilişkileri ve bildiren kaynak
    page.locator(f'.gnode[data-id="{ids["db"]}"]').focus()
    page.keyboard.press("Enter")
    detail = page.locator("#graphDetail")
    expect(detail).to_contain_text("gecmis-")
    expect(page.locator("#graphNodeRels")).to_contain_text("Kullanılır")
    expect(page.locator("#graphNodeRels")).to_contain_text("Bildiren: BMC")
    assert "null" not in page.locator("main").inner_text()
    shot(page, "web_api_relation_map")
    # Merkeze al: db merkezde, web (db'ye bağımlı) üstte
    page.locator("#graphRecenter").focus()
    page.keyboard.press("Enter")
    expect(page.locator(f'.gnode.center[data-id="{ids["db"]}"]')).to_have_count(1)
    web_box = page.locator(f'.gnode[data-id="{ids["web"]}"]').bounding_box()
    db_box = page.locator(f'.gnode[data-id="{ids["db"]}"]').bounding_box()
    assert web_box["y"] < db_box["y"]
    # Geçmiş an: web'in o andaki çevresinde lb vardı, cache yoktu
    page.goto(base_url + f"/#/iliski-haritasi/{ids['web']}")
    expect(nodes).to_have_count(3)
    page.fill("#graphAt", _local_input(page, ids["between"]))
    page.locator("#graphAt").press("Enter")
    expect(page.locator(f'.gnode[data-id="{ids["lb"]}"]')).to_have_count(1)
    expect(page.locator(f'.gnode[data-id="{ids["cache"]}"]')).to_have_count(0)
    expect(page.locator("#graphSummary")).to_contain_text("anındaki ilişkiler")
    page.click("#graphAtNow")
    expect(page.locator(f'.gnode[data-id="{ids["cache"]}"]')).to_have_count(1)
    # Metin alternatifi
    page.locator("#graphList summary").click()
    expect(page.locator("#graphEdgeList")).to_contain_text("Bağımlı")


def test_relation_map_search_and_mobile_without_horizontal_overflow(browser, base_url):
    from scenarios import make_history_ci

    ids = make_history_ci()
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    pg = context.new_page()
    pg.goto(base_url + "/#/iliski-haritasi")
    expect(pg.locator("#graphEmpty")).to_be_visible()
    pg.fill("#graphSearch", "gecmis-")
    pg.locator("#graphResults a").first.wait_for()
    pg.goto(base_url + f"/#/iliski-haritasi/{ids['web']}")
    expect(pg.locator("#graphScroll .gnode")).to_have_count(3)
    pg.wait_for_timeout(150)
    assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1
    context.close()


def test_ci_panel_past_moment_and_timeline(page, base_url):
    """F1-19: CI panelinde seçilen andaki öznitelik ve ilişki farkı; zaman çizelgesi anlara ayrılmış; oluşmadan önceki
    an açıkça söylenir."""
    from scenarios import make_history_ci

    ids = make_history_ci(gap=1.1)
    page.goto(base_url + f"/#/ci/{ids['web']}")
    expect(page.locator("#ciMap")).to_have_attribute("href", f"#/iliski-haritasi/{ids['web']}")
    timeline = page.locator("#ciHistory")
    expect(timeline).to_contain_text("İlişki kalktı: Bağımlı →")
    expect(timeline).to_contain_text("Elle düzenleme · gecmis-testi")
    expect(timeline).to_contain_text("İşletim sistemi: RHEL 8 → RHEL 9")
    page.fill("#ciAsOfAt", _local_input(page, ids["between"]))
    page.locator("#ciAsOfAt").press("Enter")
    expect(page.locator("#ciAsOfChanged")).to_contain_text("İşletim sistemi")
    expect(page.locator("#ciAsOfChanged")).to_contain_text("RHEL 8")
    expect(page.locator("#ciAsOfChanged")).to_contain_text("Bugün: RHEL 9")
    expect(page.locator("#ciAsOfRemoved")).to_contain_text("-lb")
    expect(page.locator("#ciAsOfAdded")).to_contain_text("-cache")
    expect(page.locator("#ciAsOfMap")).to_have_attribute("href", re.compile(rf"#/iliski-haritasi/{ids['web']}@"))
    assert "null" not in page.locator("#detailContent").inner_text()
    shot(page, "web_api_ci_as_of")
    # Oluşmadan önce
    page.fill("#ciAsOfAt", _local_input(page, ids["between"] - timedelta(days=30)))
    page.click("#ciAsOfShow")
    expect(page.locator("#ciAsOfNone")).to_contain_text("henüz yoktu")


def test_system_status_page_groups_states_and_refreshes_with_keyboard(page, base_url):
    """U8.1 (ADR-0032): sistem durumu; bileşenler üç grupta, durumları ve gerekçeleriyle; "Yenile" klavyeyle."""
    page.goto(base_url + "/#/sistem-durumu")
    expect(page.locator("#nav a[data-route='sistem-durumu']")).to_have_attribute("aria-current", "page")
    expect(page.locator("#sysOverall")).to_contain_text("Genel durum:")
    expect(page.locator(".sys-group")).to_have_count(3)
    expect(page.locator("#sys-database")).to_contain_text("PostgreSQL")
    expect(page.locator("#sys-database .pill")).to_have_text("Çalışıyor")
    expect(page.locator("#sys-opensearch")).to_contain_text("Bu kurulumda yok")  # testte OpenSearch yok: yedek
    expect(page.locator("#sys-sources")).to_contain_text("Kaynak çalışmaları")
    expect(page.locator("#sysCounts .metric")).to_have_count(4)
    text = page.locator("main").inner_text()
    assert "null" not in text and "undefined" not in text
    shot(page, "web_api_system_status")
    before = page.locator("#sysOverall").inner_text()
    page.wait_for_timeout(1100)
    page.locator("#sysRefresh").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#sysOverall")).not_to_have_text(before)  # son denetim zamanı yenilendi
    expect(page.locator("#sysRefresh")).to_be_enabled()


def test_system_status_mobile_has_no_horizontal_overflow(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    pg = context.new_page()
    pg.goto(base_url + "/#/sistem-durumu")
    expect(pg.locator("#sys-database")).to_be_visible()
    pg.wait_for_timeout(150)
    assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1
    context.close()


def test_admin_panel_guides_tasks_by_permission_and_deep_links(page, base_url):
    """U8.2 (ADR-0033): yönetim paneli; size düşen işler, görev arama, yetkisiz görevde gereken rol, derin bağlantıyla
    görev başlatma (Yeni CI penceresi), "Nasıl yapılır?" kılavuza götürür."""
    page.goto(base_url + "/#/yonetim")
    expect(page.locator("#adminAttention h2")).to_have_text("Size düşen işler")
    expect(page.locator(".task-group")).to_have_count(7)
    # Bu oturumda Şema onaylayıcı yok: görev açılmaz, hangi rolün gerektiğini söyler
    expect(page.locator("#task-sema-onay .task-locked")).to_contain_text("Şema onaylayıcı rolü gerekir")
    expect(page.locator("#task-kullanici .task-locked")).to_contain_text("Yetki yöneticisi rolü gerekir")
    expect(page.locator("#task-denetim")).to_contain_text("Hazırlanıyor")
    # Yönetişim görevleri: kampanya Konfigürasyon yöneticisi ister (F1-25); kural görevi hazırlanıyor (F1-26)
    expect(page.locator("#task-zamanli-gorev .task-locked")).to_contain_text("rolü gerekir")
    expect(page.locator("#task-kural-gorevi .task-locked")).to_contain_text("rolü gerekir")
    expect(page.locator("#task-saglik")).not_to_contain_text("Hazırlanıyor")
    # Arama: "alan" yazınca ilgili görevler kalır; Enter ilk göreve odaklar
    page.fill("#adminSearch", "alan")
    expect(page.locator("#task-alan")).to_be_visible()
    expect(page.locator("#task-csv")).to_be_hidden()
    page.locator("#adminSearch").press("Enter")
    expect(page.locator("#task-alan-go")).to_be_focused()
    page.fill("#adminSearch", "olmayan-bir-sey")
    expect(page.locator("#adminEmpty")).to_be_visible()
    page.fill("#adminSearch", "")
    assert "null" not in page.locator("main").inner_text()
    shot(page, "web_api_admin_panel")
    # Derin bağlantı: görev penceresi açılır
    page.goto(base_url + "/#/yonetim/yeni-ci")
    expect(page.locator(".dialog h2")).to_have_text("Yeni CI")
    page.keyboard.press("Escape")
    expect(page.locator(".overlay")).to_have_count(0)
    # İlişki ekle: önce CI seçici, sonra ilişki penceresi
    page.goto(base_url + "/#/yonetim")
    page.click("#task-iliski-go")
    page.fill("#pickCiSearch", "ist-db")
    page.locator("#pickCiResults .pick-item").first.click()
    expect(page.locator(".dialog h2")).to_contain_text("İlişki ekle ·")
    page.keyboard.press("Escape")
    # Nasıl yapılır → kılavuz konusu
    page.locator("#task-alan .task-help").click()
    expect(page.locator("#guideArticle")).to_contain_text("değişiklik seti")
    expect(page.locator("#pageTitle")).to_have_text("Sınıf ve alan eklemek")
    shot(page, "web_api_guide_topic")


def test_help_button_opens_the_screens_guide_topic_and_guide_reaches_technical_docs(page, base_url):
    """Her ekranın "?" yardımı kılavuzdaki konusuna bağlanır; kılavuzdan teknik belge uygulama içinde açılır."""
    page.goto(base_url + "/#/ci")
    page.wait_for_selector("#ciCount")
    page.click("#helpOpen")
    link = page.locator("#helpGuideLink")
    expect(link).to_have_text("Kılavuzda aç: CI bulmak ve incelemek")
    link.click()
    expect(page.locator(".overlay")).to_have_count(0)
    expect(page.locator("#pageTitle")).to_have_text("CI bulmak ve incelemek")
    # Teknik ayrıntı bağlantısı → oturumlu belge
    page.locator("#guideArticle a[href='#/belge/03-kimlik-ve-uzlastirma']").click()
    expect(page.locator("#docArticle")).to_contain_text("uzlaştırma")
    expect(page.locator("#docArticle table").first).to_be_visible()  # belgedeki tablolar işlenir
    assert "undefined" not in page.locator("main").inner_text()  # belge "null" sözcüğünü kendisi içerir
    shot(page, "web_api_technical_doc")
    page.goto(base_url + "/#/belge/adr/README")
    expect(page.locator("#docArticle a[href^='#/belge/adr/0031']")).to_have_count(1)  # ADR'ler arası göreli bağlantı


def test_admin_panel_and_guide_have_no_horizontal_overflow_on_mobile(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    pg = context.new_page()
    for path, ready in (("/#/yonetim", "#task-yeni-ci"), ("/#/kilavuz", "#guideArticle h2"),
                        ("/#/kilavuz/kullanicilar", "#guideArticle table")):
        pg.goto(base_url + path)
        expect(pg.locator(ready).first).to_be_visible()
        pg.wait_for_timeout(150)
        assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1, path
    context.close()


def test_users_and_roles_page_grants_revokes_and_explains_with_keyboard(page, base_url, browser):
    """U8.3 (ADR-0034): Yetki yöneticisi kişi ekler, gerekçeli ve süreli rol verir, görevler ayrılığı önceden
    uyarılır ve reddedilir, rol gerekçeyle geri alınır; matris, rolü taşıyanlar, yetki geçmişi; kendi kaydında
    değişiklik yok; mobilde taşma yok. Rol veritabanından verildiği için oturum açmadan hemen geçerli olur."""
    import uuid as _uuid

    import psycopg

    tag = _uuid.uuid4().hex[:6]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, expires_at,
                                                        justification)
                        VALUES ('user', 'arayuz-testi', 'evren_iam_admin', 'test-kurulum', now() + interval '1 hour',
                                'Arayüz testi') ON CONFLICT DO NOTHING""")
    try:
        page.goto(base_url + "/#/kullanicilar/yeni")
        expect(page.locator("#newUser")).to_be_focused()
        page.keyboard.type(f"web.kisi.{tag}")
        page.fill("#newName", f"Web Kişi {tag}")
        shot(page, "web_api_users_add")
        page.locator("#newName").press("Enter")
        expect(page.locator("#toast")).to_contain_text(f"Web Kişi {tag} eklendi")
        expect(page.locator("#detailTitle")).to_have_text(f"Web Kişi {tag}")
        expect(page.locator("#personRoles")).to_contain_text("Rolü yok")

        # Yetkili rol: süre kendiliğinden en uzun sınıra gelir; gerekçe kısa ise gönderilmez
        page.click("#grantOpen")
        expect(page.locator("#grantRole")).to_be_focused()
        page.select_option("#grantRole", "evren_schema_approver")
        expect(page.locator("#grantExpiryHelp")).to_contain_text("bitiş tarihi zorunlu")
        expect(page.locator("#grantRoleInfo")).to_contain_text("Şema değişiklik setlerini onaylar")
        expect(page.locator(".dialog")).not_to_contain_text("null")
        assert page.locator("#grantExpiry").input_value()
        page.fill("#grantWhy", "kısa")
        page.locator("#grantWhy").press("Enter")
        expect(page.locator("#grantWhy")).to_have_attribute("aria-invalid", "true")
        page.fill("#grantWhy", f"TALEP-{tag}: şema onay görevi")
        shot(page, "web_api_users_grant")
        page.locator("#grantWhy").press("Enter")
        expect(page.locator("#toast")).to_contain_text("Şema onaylayıcı rolü verildi")
        expect(page.locator("#personRoles")).to_contain_text("Şema onaylayıcı")
        expect(page.locator("#personRoles")).to_contain_text(f"TALEP-{tag}")

        # Görevler ayrılığı: önceden uyarı, gönderilirse API reddeder
        page.click("#grantOpen")
        page.select_option("#grantRole", "evren_schema_designer")
        expect(page.locator("#grantRoleInfo")).to_contain_text("Görevler ayrılığı")
        expect(page.locator("#grantSend")).to_be_disabled()  # API de reddeder (API testi); boşuna istek yok
        page.select_option("#grantRole", "evren_cmdb_editor")
        expect(page.locator("#grantSend")).to_be_enabled()
        page.keyboard.press("Escape")
        expect(page.locator(".dialog")).to_have_count(0)

        # Geri alma: gerekçe zorunlu
        page.locator("#personRoles .revoke-btn").first.click()
        expect(page.locator("#revokeWhy")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#revokeWhy")).to_have_attribute("aria-invalid", "true")
        page.fill("#revokeWhy", "Görev değişti, TALEP-99")
        page.keyboard.press("Enter")
        expect(page.locator("#toast")).to_contain_text("geri alındı")
        expect(page.locator("#personRoles")).to_contain_text("Rolü yok")
        page.keyboard.press("Escape")

        # Rol–yetki matrisi ve rolü taşıyanlar
        page.click("#tab-roller")
        expect(page.locator("#roleMatrix")).to_contain_text("Konfigürasyon yöneticisi")
        expect(page.locator("#sodList")).to_contain_text("Şema onaylayıcı")
        shot(page, "web_api_users_matrix")
        page.locator(".role-link", has_text="Yetki yöneticisi").click()
        expect(page.locator("#detailTitle")).to_have_text("Yetki yöneticisi")
        page.click("#roleHolders")
        expect(page.locator("#iamRoleFilter")).to_have_value("evren_iam_admin")
        expect(page.locator("#iamList")).to_contain_text("Arayuz Testi")

        # Yetki geçmişi
        page.click("#tab-gecmis")
        expect(page.locator("#iamBody")).to_contain_text("Rol geri alındı")
        expect(page.locator("#iamBody")).to_contain_text("Görev değişti, TALEP-99")

        # Kendi kaydı: değişiklik düğmesi yok
        page.click("#tab-kisiler")
        page.fill("#iamSearch", "arayuz-testi")
        page.locator("#iamList tbody tr").first.press("Enter")
        expect(page.locator("#detailContent")).to_contain_text("Bu sizsiniz")
        expect(page.locator("#grantOpen")).to_have_count(0)

        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        pg = context.new_page()
        for route, ready in (("#/kullanicilar", "#iamList table"), ("#/kullanicilar/roller", "#roleMatrix"),
                             ("#/kullanicilar/gecmis", "#iamBody table")):
            pg.goto(base_url + "/" + route)
            pg.wait_for_selector(ready)
            pg.wait_for_timeout(200)
            # Tanı: görünür alanın dışına taşan mutlak konumlu öğeler (kaydırma kutusundan kaçıp sayfayı genişletenler)
            wide = pg.evaluate("""() => [...document.querySelectorAll('#view *')]
                .filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1
                  && ['absolute', 'fixed'].includes(getComputedStyle(e).position))
                .slice(0, 5).map((e) => `${e.tagName}.${e.className}:${Math.round(e.getBoundingClientRect().right)}`)""")
            assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1, (route, wide)
        context.close()
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM iam.principal_role WHERE principal_id = 'arayuz-testi'")


def test_tasks_page_opens_decides_and_shows_channels(page, base_url, browser):
    """U8.4 (ADR-0035): Konfigürasyon yöneticisi tek seferlik görev açar (CI seçilir, sorumlu CI'ın sahibi), sorumlu
    kararı gerekçeyle verir; kanallar sekmesi varsayılanı ve ServiceNow ayarını gösterir; mobilde taşma yok."""
    import psycopg
    from scenarios import make_owned_ci

    _ci, ci_name, _ = make_owned_ci("arayuz-testi")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, expires_at,
                                                        justification)
                        VALUES ('user', 'arayuz-testi', 'evren_config_manager', 'test-kurulum', now() + interval '1 hour',
                                'Arayüz testi') ON CONFLICT DO NOTHING""")
    try:
        page.goto(base_url + "/#/gorevler/yeni")
        expect(page.locator("#newTaskTitle")).to_be_focused()
        page.keyboard.type("Uygulama verilerini teyit edin")
        page.fill("#newTaskDesc", "Sahip ve destek grubu doğru mu?")
        page.fill("#newTaskCi", ci_name)
        page.locator("#newTaskCiResults .pick-item").first.click()
        expect(page.locator("#newTaskCiChosen")).to_contain_text(ci_name)
        shot(page, "web_api_tasks_new")
        page.click("#newTaskSend")
        expect(page.locator("#toast")).to_contain_text("açıldı (CI sahibi)")
        expect(page.locator("#detailTitle")).to_have_text("Uygulama verilerini teyit edin")
        expect(page.locator("#detailContent")).to_contain_text(ci_name)
        expect(page.locator("#detailContent")).to_contain_text("Teslim bekliyor")
        page.click("#taskDecide")
        expect(page.locator("#taskOutcome")).to_be_focused()
        page.select_option("#taskOutcome", "corrected")
        page.click("#taskSend")
        expect(page.locator("#taskNote")).to_have_attribute("aria-invalid", "true")
        page.fill("#taskNote", "Destek grubu güncellendi")
        shot(page, "web_api_tasks_decide")
        page.click("#taskSend")
        expect(page.locator("#toast")).to_contain_text("kapatıldı: Düzeltildi")
        expect(page.locator("#detailContent")).to_contain_text("Destek grubu güncellendi")
        expect(page.locator("#taskDecide")).to_have_count(0)
        page.keyboard.press("Escape")
        page.click("#tab-channels")
        expect(page.locator("#channelList")).to_contain_text("ServiceNow")
        expect(page.locator("#channelList")).to_contain_text("sc_task")
        expect(page.locator("#channelList")).to_contain_text("Varsayılan")
        shot(page, "web_api_tasks_channels")
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        pg = context.new_page()
        for route, ready in (("#/gorevler/tumu", "#taskList table"), ("#/gorevler/kanallar", "#channelList")):
            pg.goto(base_url + "/" + route)
            pg.wait_for_selector(ready)
            pg.wait_for_timeout(200)
            assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1, route
        context.close()
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM iam.principal_role WHERE principal_id = 'arayuz-testi'")


def test_campaign_defined_previewed_activated_run_and_tasks_listed(page, base_url, browser):
    """U8.5 (ADR-0036): Konfigürasyon yöneticisi panelden kampanya tanımlar; geçersiz koşul alanda gösterilir,
    önizleme kapsamı ve sorumluları sayar; etkinleştirir, gerekçeyle "şimdi çalıştır" ister; görev çalışanı turunda
    her CI'a bir görev açılır; kampanyanın görevleri listelenir; duraklatılan kampanya düzenlenebilir; mobilde
    taşma yok."""
    import uuid

    import psycopg
    from scenarios import make_owned_ci

    from evren_core.governance import ServiceNowTaskSimulator
    from evren_governance.config import GovernanceConfig
    from evren_governance.worker import GovernanceWorker

    tag = f"kmpui{uuid.uuid4().hex[:5]}"
    cis = [make_owned_ci(owner)[0] for owner in ("kampanya-sahibi", "kampanya-sahibi", None)]
    with psycopg.connect(autocommit=True) as conn:
        for i, ci in enumerate(cis):
            conn.execute("UPDATE cmdb.cmdb_ci SET name = %s WHERE sys_id = %s", (f"{tag}-{i}", ci))
        conn.execute("""INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, expires_at,
                                                        justification)
                        VALUES ('user', 'arayuz-testi', 'evren_config_manager', 'test-kurulum', now() + interval '1 hour',
                                'Arayüz testi') ON CONFLICT DO NOTHING""")
    try:
        page.goto(base_url + "/#/gorevler/kampanyalar/yeni")
        expect(page.locator("#campKind")).to_be_focused()
        expect(page.locator("#campKindHelp")).to_contain_text("sahiplik onayı")
        page.fill("#campName", f"Çeyrek teyidi {tag}")
        page.fill("#campTitle", "Uygulama verilerini teyit edin")
        page.fill("#campInstr", "Sahip, destek grubu ve ortam doğru mu?")
        page.select_option("#campClass", "cmdb_ci_server")
        page.fill("#campQuery", "olmayan_alan=1")
        page.click("#campPreviewBtn")
        expect(page.locator("#campQuery")).to_have_attribute("aria-invalid", "true")
        expect(page.locator("#campError [role=alert]")).to_be_visible()
        page.fill("#campQuery", f"nameSTARTSWITH{tag}")
        page.locator("#campQuery").press("Enter")
        expect(page.locator("#campPreview")).to_contain_text("3 CI kapsamda")
        expect(page.locator("#campPreview")).to_contain_text("1 kişiye")
        expect(page.locator("#campSample li")).to_have_count(3)
        shot(page, "web_api_campaign_new")
        page.click("#campActivate")
        expect(page.locator("#toast")).to_contain_text("Kampanya etkin")
        expect(page.locator("#detailTitle")).to_have_text(f"Çeyrek teyidi {tag}")
        expect(page.locator("#detailContent")).to_contain_text("Her çeyrek")
        cid = page.url.rsplit("/", 1)[-1]
        assert re.fullmatch(r"[0-9a-f]{32}", cid), page.url
        page.click("#campRunNow")
        expect(page.locator("#campWhy")).to_be_focused()
        page.fill("#campWhy", "kısa")
        page.click("#campWhySend")
        expect(page.locator("#campWhy")).to_have_attribute("aria-invalid", "true")
        page.fill("#campWhy", "Denetim öncesi erken başlatma")
        page.locator("#campWhy").press("Enter")
        expect(page.locator("#toast")).to_contain_text("Çalışma istendi")
        expect(page.locator("#campRuns")).to_contain_text("İstendi")
        # Görev çalışanı bir tur döner (ServiceNow simülatörüyle)
        cfg = GovernanceConfig(conninfo="", public_url="https://evren.ornek.test",
                               worker_id=f"arayuz-gov-{tag}", sync_minutes=1)
        worker = GovernanceWorker(cfg, transport=ServiceNowTaskSimulator(), connect=lambda: psycopg.connect())
        with worker.open() as conn:
            worker.cycle(conn)
        page.reload()
        expect(page.locator("#campRuns")).to_contain_text("3 kapsamda, 3 görev açıldı")
        page.wait_for_timeout(400)  # panel kayarak açılır
        shot(page, "web_api_campaign_detail")
        page.click("#campTasks")
        expect(page.locator("#taskCampaignScope")).to_be_visible()
        expect(page.locator("#taskList tbody tr")).to_have_count(3)
        expect(page.locator("#taskList")).to_contain_text(f"Uygulama verilerini teyit edin — {tag}-0")
        expect(page.locator("#taskList")).to_contain_text("Atanmamış")
        page.locator("#taskList tbody tr").first.click()
        expect(page.locator("#detailContent")).to_contain_text("Kampanyayı aç")
        page.keyboard.press("Escape")
        # Duraklat → düzenlenebilir
        page.goto(base_url + f"/#/gorevler/kampanyalar/{cid}")
        page.click("[data-transition=paused]")
        page.fill("#campWhy", "Dönem sonu dondurma süresi")
        page.click("#campWhySend")
        expect(page.locator("#toast")).to_contain_text("duraklatıldı")
        expect(page.locator("#campEdit")).to_be_visible()
        expect(page.locator("#campRunNow")).to_have_count(0)
        page.click("#campEdit")
        expect(page.locator("#campName")).to_be_focused()
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        expect(page.locator("#campaignList")).to_contain_text("Duraklatıldı")
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        pg = context.new_page()
        pg.goto(base_url + "/#/gorevler/kampanyalar")
        pg.wait_for_selector("#campaignList table")
        pg.wait_for_timeout(200)
        assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1
        context.close()
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM iam.principal_role WHERE principal_id = 'arayuz-testi'")


def test_quality_rule_from_template_findings_task_exception_and_health(page, base_url, browser):
    """U8.6 (ADR-0037): Konfigürasyon yöneticisi şablondan "ilişkisiz veritabanı" kuralını tanımlar, bulguları
    önizler, etkinleştirir; görev çalışanı turunda bulgular ve sorumluya görev açılır; sorumlu görevde "Geçerli
    değil" der → bulgu istisna; sağlık panosu puanı, boyut ölçümlerini ve klavyeyle gezilen eğilimi gösterir; mobil."""
    import uuid

    import psycopg
    from scenarios import make_owned_ci

    from evren_core.governance import ServiceNowTaskSimulator
    from evren_governance.config import GovernanceConfig
    from evren_governance.worker import GovernanceWorker

    tag = f"qui{uuid.uuid4().hex[:5]}"
    make_owned_ci("arayuz-testi")  # kişi kaydı (bağımlı veritabanı CI'ı uzlaştırıcıda ana CI ister: doğrudan açılır)
    with psycopg.connect(autocommit=True) as conn:
        (me,) = conn.execute("SELECT sys_id FROM ref.sys_user WHERE user_name = 'arayuz-testi'").fetchone()
        _mine, orphan = (conn.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name, owned_by) VALUES "
                                     "('cmdb_ci_db_instance', %s, %s) RETURNING sys_id", (name, who)).fetchone()[0]
                        for name, who in ((f"{tag}-benim", me), (f"{tag}-sahipsiz", None)))
        conn.execute("""INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, expires_at,
                                                        justification)
                        VALUES ('user', 'arayuz-testi', 'evren_config_manager', 'test-kurulum', now() + interval '1 hour',
                                'Arayüz testi') ON CONFLICT DO NOTHING""")

    def worker_cycle():
        cfg = GovernanceConfig(conninfo="", public_url="https://evren.ornek.test", worker_id=f"arayuz-kural-{tag}",
                               sync_minutes=1)
        w = GovernanceWorker(cfg, transport=ServiceNowTaskSimulator(), connect=lambda: psycopg.connect())
        with w.open() as conn:
            w.cycle(conn)

    try:
        page.goto(base_url + "/#/saglik/kurallar/yeni")
        expect(page.locator("#ruleTemplate")).to_be_focused()
        page.select_option("#ruleTemplate", "iliskisiz-veritabani")
        expect(page.locator("#ruleName")).to_have_value("Uygulama veya sunucuyla ilişkisi olmayan veritabanı")
        page.fill("#ruleName", f"İlişkisiz veritabanı {tag}")
        expect(page.locator("#ruleRelated option:checked")).to_have_count(4)
        page.fill("#ruleQuery", f"nameSTARTSWITH{tag}")
        page.locator("#ruleQuery").press("Enter")
        expect(page.locator("#rulePreview")).to_contain_text("2 bulgu")
        expect(page.locator("#ruleSample li")).to_have_count(2)
        expect(page.locator("#ruleSample")).to_contain_text("ilişkisi yok")
        shot(page, "web_api_rule_new")
        page.click("#ruleActivate")
        expect(page.locator("#toast")).to_contain_text("Kural etkin")
        expect(page.locator("#detailTitle")).to_have_text(f"İlişkisiz veritabanı {tag}")
        rid = page.url.rsplit("/", 1)[-1]
        assert re.fullmatch(r"[0-9a-f]{32}", rid), page.url
        worker_cycle()
        page.reload()
        expect(page.locator("#ruleRuns")).to_contain_text("2 bulgu (2 yeni")
        page.click("#ruleFindings")
        expect(page.locator("#findingScope")).to_be_visible()
        expect(page.locator("#findingList tbody tr")).to_have_count(2)
        expect(page.locator("#findingList")).to_contain_text("Atanmamış")
        page.locator("#findingList tbody tr", has_text=f"{tag}-benim").click()
        expect(page.locator("#findingText")).to_contain_text("ilişkisi yok")
        task_link = page.locator("#findingTasks a").first
        task_number = task_link.inner_text()
        shot(page, "web_api_finding_detail")
        task_link.click()
        expect(page.locator("#taskFinding")).to_contain_text("ilişkisi yok")
        page.click("#taskDecide")
        page.select_option("#taskOutcome", "not_applicable")
        page.fill("#taskNote", "Arşiv veritabanı; bilinçli olarak bağımsız")
        page.click("#taskSend")
        expect(page.locator("#toast")).to_contain_text(f"{task_number} kapatıldı")
        page.goto(base_url + f"/#/saglik/bulgular/kural/{rid}")
        page.locator("#findingState").select_option("excepted")
        expect(page.locator("#findingList tbody tr")).to_have_count(1)
        expect(page.locator("#findingList")).to_contain_text("İstisna")
        # CI sayfasında kalite bulgusu
        page.goto(base_url + f"/#/ci/{orphan}")
        expect(page.locator("#ciFindings")).to_contain_text(f"İlişkisiz veritabanı {tag}")
        # Sağlık panosu
        page.goto(base_url + "/#/saglik")
        expect(page.locator("#healthOverall")).not_to_be_empty()
        expect(page.locator("#healthDims .metric")).to_have_count(4)
        expect(page.locator("#dimPanel-completeness")).to_contain_text(f"İlişkisiz veritabanı {tag}")
        page.locator("#trendChart").focus()
        page.keyboard.press("ArrowLeft")
        expect(page.locator("#trendReadout")).to_contain_text("genel puan")
        page.wait_for_timeout(300)
        shot(page, "web_api_health")
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        pg = context.new_page()
        for route, ready in (("#/saglik", "#healthDims"), ("#/saglik/bulgular", "#findingList table"),
                             ("#/saglik/kurallar", "#ruleList table")):
            pg.goto(base_url + "/" + route)
            pg.wait_for_selector(ready)
            pg.wait_for_timeout(200)
            assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1, route
        context.close()
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM iam.principal_role WHERE principal_id = 'arayuz-testi'")


def test_class_wizard_builds_class_with_fields_and_choices_and_sends_for_approval(page, base_url):
    """U8.7 (ADR-0038): yönetim panelinden "Yeni sınıf ekle" sihirbazı: üst sınıf aranır ve seçilir (devralınan alan ve
    kimlik kuralı görünür), teknik ad Türkçe addan önerilir, seçim listeli alan değerleriyle eklenir, gözden geçirmede
    sunucuda doğrulanır, taslak oluşturulup onaya gönderilir; hatalı ad geri dönüp düzeltilir; klavyeyle ilerlenir."""
    import uuid

    tag = uuid.uuid4().hex[:5]
    page.goto(base_url + "/#/yonetim/sinif")
    expect(page.locator("#wizStepTitle")).to_have_text("1. Nerede duracak?")
    expect(page.locator(".wiz-step.current")).to_contain_text("Nerede duracak?")
    page.fill("#wizClassSearch", "donanım")
    page.locator("#wizClassList button[data-cls='cmdb_ci_hardware']").click()
    expect(page.locator("#wizClassInfo")).to_contain_text("alan devralınır")
    page.click("#wizNext")
    expect(page.locator("#wizClassLabel")).to_be_focused()
    page.keyboard.type(f"Kiosk cihazı {tag}")
    expect(page.locator("#wizClassName")).to_have_value(f"u_cmdb_ci_kiosk_cihazi_{tag}")
    page.fill("#wizClassName", "kiosk")
    page.locator("#wizClassName").press("Enter")
    expect(page.locator("#wizError")).to_contain_text("u_ ile başlamalı")
    page.fill("#wizClassName", f"u_cmdb_ci_kiosk_{tag}")
    page.click("#wizNext")
    expect(page.locator("#wizStepTitle")).to_have_text("3. Alanlar (isteğe bağlı)")
    page.click("#wizAddField")
    page.fill("#wf0-label", "Ekran boyutu")
    page.select_option("#wf0-type", "integer")
    expect(page.locator("#wf0-len")).to_be_hidden()
    page.click("#wizAddField")
    page.fill("#wf1-label", "Konum tipi")
    page.locator("label[for=wf1-choice]").click()
    page.fill("#wf1-values", "Lobi\nŞube girişi")
    shot(page, "web_api_class_wizard_fields")
    page.click("#wizNext")
    expect(page.locator("#wizValid")).to_contain_text("Doğrulandı")
    expect(page.locator("#wizChanges li")).to_have_count(5)
    expect(page.locator("#wizChanges")).to_contain_text("sube_girisi")
    expect(page.locator("#wizSetName")).to_have_value(f"Yeni sınıf: Kiosk cihazı {tag}")
    page.click("#wizNext")
    page.wait_for_url(re.compile(r".*#/sema/[0-9a-f]{32}$"))
    page.wait_for_selector("#detail.open #csChanges")
    expect(page.locator("#csChanges")).to_contain_text(f"u_cmdb_ci_kiosk_{tag}")
    expect(page.locator("#detail")).to_contain_text("Onay bekliyor")


def test_integration_wizard_from_catalog_to_trial_run(page, base_url, browser):
    """U8.7 (ADR-0038): "Yeni entegrasyon kur" sihirbazı: kaynak türü (rol ve kimlik türüyle), bağlantı (sır yerine
    kasadaki dosya yolu), zamanlama, geçit kimliği (belirteç bir kez), kurulum paketi (çalıştırıcı bloğu ve sır listesi;
    Evren'de saklanmaz), doğrulama: çalıştırıcı örneği bildirince deneme çalışması; mobilde taşma yok."""
    import uuid

    import psycopg

    name = f"zabbix-ui-{uuid.uuid4().hex[:5]}"
    runner = f"arayuz-runner-{uuid.uuid4().hex[:5]}"
    try:
        page.goto(base_url + "/#/yonetim/yeni-entegrasyon")
        expect(page.locator("#wizStepTitle")).to_have_text("1. Kaynak türü")
        page.click("#wizNext")
        expect(page.locator("#wizError")).to_contain_text("Bir kaynak türü seçin")
        page.locator("label[for=intConnector-zabbix]").click()
        expect(page.locator("label[for=intConnector-zabbix]")).to_contain_text("İzleme")
        page.click("#wizNext")
        expect(page.locator("#intName")).to_be_focused()
        page.fill("#intName", name)
        page.fill("#intUrl", "http://zabbix.kurum.local")
        page.click("#wizNext")
        expect(page.locator("#wizError")).to_contain_text("https://")
        page.fill("#intUrl", "https://zabbix.kurum.local/api_jsonrpc.php")
        page.fill("#int-token_file", f"/run/secrets/{name}_token")
        shot(page, "web_api_integration_connection")
        page.click("#wizNext")
        page.locator("label[for=intSchedule-manual]").click()
        page.click("#wizNext")
        expect(page.locator("#intGatewayPath")).to_have_value(f"/run/secrets/evren_gateway_{name}")
        page.click("#wizNext")
        expect(page.locator("#wizError")).to_contain_text("Kimlik ver")
        page.click("#intIssue")
        expect(page.locator("#intToken")).to_be_focused()
        assert len(page.locator("#intToken").input_value()) > 20
        page.click("#wizNext")
        expect(page.locator("#intYaml")).to_contain_text(f"name: {name}")
        expect(page.locator("#intYaml")).to_contain_text("schedule: null")
        expect(page.locator("#intSecrets tbody tr")).to_have_count(2)
        expect(page.locator("#intSecrets")).to_contain_text("Evren alım geçidi belirteci")
        shot(page, "web_api_integration_package")
        with psycopg.connect(autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM ingest.connector_instance WHERE name = %s", (name,)).fetchone()[0] == 0
        page.click("#wizNext")
        expect(page.locator("#intChecks")).to_contain_text("Bekleniyor")
        expect(page.locator("#intChecks li.ok")).to_have_count(2)  # veri kaynağı + kimlik
        with psycopg.connect(autocommit=True) as conn:  # çalıştırıcı örneği bildirir (kurulum)
            conn.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind)
                            VALUES (%s, now(), now(), 15, 'connector')""", (runner,))
            conn.execute("""INSERT INTO ingest.connector_instance (name, connector, data_source, schedule_tz,
                                                                   max_runtime_minutes, runner_id, seen_at)
                            VALUES (%s, 'zabbix', 'Zabbix', 'Europe/Istanbul', 60, %s, now())""", (name, runner))
        expect(page.locator("#intTrial")).to_be_visible(timeout=8000)
        page.click("#intTrial")
        expect(page.locator("#intChecks")).to_contain_text("Sırada", timeout=8000)
        shot(page, "web_api_integration_verify")
        page.click("#wizNext")  # Kapat
        expect(page.locator(".overlay")).to_have_count(0)
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        pg = context.new_page()
        pg.goto(base_url + "/#/yonetim/yeni-entegrasyon")
        pg.wait_for_selector("#wizStepTitle")
        pg.wait_for_timeout(200)
        assert pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth") <= 1
        context.close()
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("UPDATE ingest.connector_run_request SET status = 'cancelled', finished_at = now() "
                         "WHERE instance = %s AND status IN ('pending', 'running')", (name,))

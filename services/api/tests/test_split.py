"""Birleştirmeyi ayırma uç noktası (F0-12): yetki, CSRF, idempotency, engelleyici durumlar, uçtan uca uygulama."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF
from .scenarios import apply_pending_splits, make_wrong_merge


def _key() -> str:
    return f"t-{uuid.uuid4()}"


def test_detail_exposes_links_and_split_state(client, reader):
    ci, links, _ = make_wrong_merge()
    doc = client.get(f"/api/v1/ci/{ci}").json()
    assert doc["can_split"] is True
    by_ds = {lk["data_source"]: lk for lk in doc["links"]}
    assert by_ds["vCenter"]["id"] == links["vCenter"] and by_ds["vCenter"]["split"]["allowed"] is True
    ro = reader.get(f"/api/v1/ci/{ci}").json()
    assert ro["can_split"] is False and not any(lk["split"]["allowed"] for lk in ro["links"])


def test_split_end_to_end_with_idempotency(client):
    ci, links, tag = make_wrong_merge()
    body = {"link_id": links["vCenter"], "note": "Seri yanlış bildirilmiş", "idempotency_key": _key()}
    assert client.post(f"/api/v1/ci/{ci}/split", json=body).status_code == 403  # CSRF başlığı yok
    r = client.post(f"/api/v1/ci/{ci}/split", json=body, headers=CSRF)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending" and r.json()["replayed"] is False
    again = client.post(f"/api/v1/ci/{ci}/split", json=body, headers=CSRF)
    assert again.status_code == 202 and again.json()["replayed"] is True and again.json()["id"] == r.json()["id"]
    conflict = client.post(f"/api/v1/ci/{ci}/split", json={**body, "note": "başka"}, headers=CSRF)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_cakismasi"
    dup = client.post(f"/api/v1/ci/{ci}/split", json={**body, "idempotency_key": _key()}, headers=CSRF)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "bekleyen_ayirma"
    pending = client.get(f"/api/v1/ci/{ci}").json()
    vlink = next(lk for lk in pending["links"] if lk["data_source"] == "vCenter")
    assert vlink["split"] == {"allowed": False, "pending": True, "reason": "Ayırma isteği uygulanmayı bekliyor"}

    [new] = [c for c in apply_pending_splits() if c]
    after = client.get(f"/api/v1/ci/{ci}").json()
    assert after["sources"] == ["BMC"] and not any(a["attribute"] == "ram" for a in after["attributes"])
    assert after["splits"][0]["status"] == "applied" and after["splits"][0]["result_ci"] == new
    moved = client.get(f"/api/v1/ci/{new}").json()
    assert moved["sources"] == ["vCenter"] and moved["name"] == f"wm-{tag}"
    assert moved["links"][0]["method"] == "split" and "dev:test-user" in moved["links"][0]["summary"]
    assert moved["splits"][0]["side"] == "result"
    hist = client.get(f"/api/v1/ci/{ci}/history").json()["items"]
    assert any(h["reason"] == "split" and h["attribute"] == "ram" and h["new"] is None for h in hist)


def test_split_blockers(client):
    ci, links, _ = make_wrong_merge()
    url = f"/api/v1/ci/{ci}/split"
    other_ci, other_links, _ = make_wrong_merge()
    r = client.post(url, json={"link_id": other_links["BMC"], "idempotency_key": _key()}, headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bag_yok"
    r = client.post(url, json={"link_id": links["vCenter"], "target_ci": ci, "idempotency_key": _key()}, headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == "hedef_ayni"
    r = client.post(url, json={"link_id": links["vCenter"], "target_ci": "f" * 32, "idempotency_key": _key()},
                    headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == "hedef_yok"
    # Seçilen mevcut CI'a taşıma da desteklenir
    r = client.post(url, json={"link_id": links["vCenter"], "target_ci": other_ci, "idempotency_key": _key()},
                    headers=CSRF)
    assert r.status_code == 202
    assert other_ci in apply_pending_splits()
    # Artık CI'da tek kayıt var
    r = client.post(url, json={"link_id": links["BMC"], "idempotency_key": _key()}, headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "tek_kaynak"


def test_split_requires_split_permission_and_is_audited(reader):
    ci, links, _ = make_wrong_merge()
    r = reader.post(f"/api/v1/ci/{ci}/split", json={"link_id": links["vCenter"], "idempotency_key": _key()},
                    headers=CSRF)
    assert r.status_code == 403 and r.json()["error"]["code"] == "yetki_yok"
    with psycopg.connect() as conn:
        conn.execute("SET ROLE evren_auditor_ro")
        row = conn.execute("SELECT decision FROM audit.security_event WHERE principal = %s AND action = %s "
                           "ORDER BY id DESC LIMIT 1", ("dev:salt-okuyucu", "split:cmdb_ci_linux_server")).fetchone()
    assert row == ("deny",)


@pytest.mark.parametrize(("body", "code"), [
    ({"link_id": "x", "idempotency_key": "kisa"}, "gecersiz_istek"),
    ({"link_id": "a" * 32, "idempotency_key": "gecerli-anahtar", "fazla": 1}, "gecersiz_istek"),
    ({"link_id": "a" * 32, "idempotency_key": "gecerli-anahtar", "note": "x" * 2001}, "gecersiz_istek"),
])
def test_split_body_is_strictly_validated(client, body, code):
    r = client.post(f"/api/v1/ci/{'a' * 32}/split", json=body, headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_split_on_unknown_ci_is_404_and_bad_id_400(client):
    body = {"link_id": "a" * 32, "idempotency_key": _key()}
    assert client.post(f"/api/v1/ci/{'0' * 32}/split", json=body, headers=CSRF).status_code == 404
    assert client.post("/api/v1/ci/NOT-AN-ID/split", json=body, headers=CSRF).status_code == 400

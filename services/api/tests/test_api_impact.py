"""Etki analizi (U7.3, ADR-0030): yukarı (etkilenenler) ve aşağı (bağımlılıklar) yürüyüş, ilişki tipinin etki yönü,
döngü, hizmet özeti, doğrulama. Veriler sentetiktir."""
from __future__ import annotations

from .scenarios import make_service_chain


def test_server_outage_reaches_application_and_business_services(reader):
    ids = make_service_chain()
    doc = reader.get(f"/api/v1/ci/{ids['server']}/impact").json()
    up = {n["sys_id"]: n for n in doc["upstream"]["nodes"]}
    assert up[ids["app_service"]]["depth"] == 1 and up[ids["app_service"]]["phrase"] == "Bağımlı"
    assert up[ids["business_app"]]["depth"] == 2 and up[ids["business_app"]]["phrase"] == "Tüketir"
    assert up[ids["business_app"]]["via"] == ids["app_service"] and up[ids["business_app"]]["via_name"].startswith(
        "etki-hizmet-")
    assert up[ids["business_service"]]["depth"] == 2 and ids["server"] not in up  # döngü başlangıca dönmez
    # aşağı: sunucu → iş uygulaması → uygulama hizmeti; hizmet yeniden sunucuya döner, döngü kesilir
    assert doc["summary"] == {"affected_services": 3, "affected_production_services": 1, "affected": 3,
                              "dependencies": 2}
    assert [n["service"] for n in doc["upstream"]["nodes"]] == [True, True, True]


def test_business_application_depends_on_service_and_server(reader):
    ids = make_service_chain()
    down = {n["sys_id"]: n["depth"] for n in
            reader.get(f"/api/v1/ci/{ids['business_app']}/impact").json()["downstream"]["nodes"]}
    assert down == {ids["app_service"]: 1, ids["server"]: 2}
    limited = reader.get(f"/api/v1/ci/{ids['business_app']}/impact", params={"depth": 1}).json()
    assert [n["sys_id"] for n in limited["downstream"]["nodes"]] == [ids["app_service"]]


def test_validation(reader):
    assert reader.get("/api/v1/ci/kisa/impact").status_code == 400
    assert reader.get(f"/api/v1/ci/{'0' * 32}/impact").status_code == 404
    assert reader.get(f"/api/v1/ci/{'0' * 32}/impact", params={"depth": 11}).status_code == 422

"""Zaman içinde sorgu ve ilişki haritası (U7.4, F1-19, ADR-0031): CI'ın geçmiş bir andaki öznitelikleri ve ilişkileri,
değişiklik zaman çizelgesi, harita (düzey, o an), doğrulama. Veriler sentetiktir; geçmiş gerçek uzlaştırıcıyla oluşur."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .conftest import make_client
from .scenarios import make_history_ci


def _names(items):
    return sorted(i["other_name"].rsplit("-", 1)[1] for i in items)


def test_as_of_rewinds_attributes_and_relations_to_the_moment(reader):
    ids = make_history_ci()
    doc = reader.get(f"/api/v1/ci/{ids['web']}/as-of", params={"at": ids["between"].isoformat()}).json()
    assert doc["existed"] is True and doc["class"] == "cmdb_ci_linux_server"
    os_row = next(a for a in doc["attributes"] if a["attribute"] == "os")
    assert (os_row["then"], os_row["now"], os_row["changed"]) == ("RHEL 8", "RHEL 9", True)
    assert doc["changed_count"] == 1 and doc["changes_since"] >= 1
    assert next(a for a in doc["attributes"] if a["attribute"] == "name")["changed"] is False
    assert all(a["attribute"] not in ("first_discovered", "last_discovered") for a in doc["attributes"])
    rel = doc["relations"]
    # o an: db (keşif) ve lb (elle); sonra cache eklendi, lb kaldırıldı
    assert _names(rel["then"]) == ["db", "lb"]
    assert _names(rel["added_since"]) == ["cache"] and _names(rel["removed_since"]) == ["lb"]
    assert {i["phrase"] for i in rel["then"]} == {"Bağımlı"} and rel["then"][0]["direction"] == "parent"
    # Bağımlılığın kendi bakışından: db o anda web tarafından "Kullanılır"dı
    db_doc = reader.get(f"/api/v1/ci/{ids['db']}/as-of", params={"at": ids["between"].isoformat()}).json()
    assert [(i["phrase"], i["direction"]) for i in db_doc["relations"]["then"]] == [("Kullanılır", "child")]


def test_as_of_before_the_ci_existed_and_now(reader):
    ids = make_history_ci()
    before = reader.get(f"/api/v1/ci/{ids['web']}/as-of",
                        params={"at": (ids["between"] - timedelta(days=30)).isoformat()}).json()
    assert before["existed"] is False and before["attributes"] == [] and before["relations"]["then"] == []
    now = reader.get(f"/api/v1/ci/{ids['web']}/as-of", params={"at": datetime.now(UTC).isoformat()}).json()
    assert now["changed_count"] == 0 and now["relations"]["added_since"] == [] == now["relations"]["removed_since"]
    assert _names(now["relations"]["then"]) == ["cache", "db"]


def test_timeline_groups_changes_into_moments_with_relations(reader):
    ids = make_history_ci()
    doc = reader.get(f"/api/v1/ci/{ids['web']}/timeline").json()
    moments = doc["moments"]
    # yeniden eskiye: elle kaldırma → ikinci keşif (os + cache) → elle ekleme → ilk keşif (oluşma + db)
    assert [m["reason"] for m in moments] == ["manual", "reconciliation", "manual", "reconciliation"]
    removal, second, addition, first = moments
    assert removal["relations"][0]["action"] == "removed" and removal["relations"][0]["other_name"].endswith("-lb")
    assert removal["actor"] == "gecmis-testi" and removal["reason_label"] == "Elle düzenleme"
    assert [(c["label"], c["old"], c["new"]) for c in second["changes"]] == [("İşletim sistemi", "RHEL 8", "RHEL 9")]
    assert [(r["action"], r["other_name"].rsplit("-", 1)[1]) for r in second["relations"]] == [("added", "cache")]
    assert addition["relations"][0]["action"] == "added" and addition["relations"][0]["no_effect"] is False
    assert {c["attribute"] for c in first["changes"]} >= {"name", "os", "serial_number"}
    assert [r["other_name"].rsplit("-", 1)[1] for r in first["relations"]] == ["db"]
    assert doc["next_cursor"] is None


def test_timeline_pages_without_splitting_a_moment(reader):
    ids = make_history_ci()
    first = reader.get(f"/api/v1/ci/{ids['web']}/timeline", params={"limit": 5}).json()
    assert first["next_cursor"]
    seen = list(first["moments"])
    cursor = first["next_cursor"]
    while cursor:
        page = reader.get(f"/api/v1/ci/{ids['web']}/timeline", params={"limit": 5, "before": cursor}).json()
        seen += page["moments"]
        cursor = page["next_cursor"]
    full = reader.get(f"/api/v1/ci/{ids['web']}/timeline").json()["moments"]
    assert [(m["at"], len(m["changes"]), len(m["relations"])) for m in seen] == [
        (m["at"], len(m["changes"]), len(m["relations"])) for m in full]


def test_graph_levels_follow_dependency_direction_and_rewind_to_the_moment(reader):
    ids = make_history_ci()
    doc = reader.get(f"/api/v1/ci/{ids['web']}/graph", params={"depth": 1}).json()
    nodes = {n["sys_id"]: n for n in doc["nodes"]}
    assert set(nodes) == {ids["web"], ids["db"], ids["cache"]}
    assert nodes[ids["web"]]["center"] and nodes[ids["web"]]["level"] == 0
    assert nodes[ids["db"]]["level"] == 1 and nodes[ids["db"]]["depth"] == 1  # bağımlılık altta
    edge = next(e for e in doc["edges"] if e["child"] == ids["db"])
    assert (edge["impact"], edge["dependent"], edge["parent_phrase"], edge["sources"]) == (
        "parent", ids["web"], "Bağımlı", ["BMC"])
    # db'nin haritasında web üstte (db'ye bağımlı), cache web üzerinden ikinci adımda web'in altında
    up = {n["sys_id"]: n for n in reader.get(f"/api/v1/ci/{ids['db']}/graph", params={"depth": 2}).json()["nodes"]}
    assert up[ids["web"]]["level"] == -1 and up[ids["cache"]]["level"] == 0 and up[ids["cache"]]["depth"] == 2
    # Geçmiş an: lb vardı, cache yoktu
    then = reader.get(f"/api/v1/ci/{ids['web']}/graph", params={"depth": 1, "at": ids["between"].isoformat()}).json()
    assert {n["sys_id"] for n in then["nodes"]} == {ids["web"], ids["db"], ids["lb"]}
    assert then["at"] and then["existed"] is True


def test_validation_and_permissions(reader):
    ids = make_history_ci()
    web = ids["web"]
    assert reader.get("/api/v1/ci/kisa/as-of", params={"at": "2026-11-01T00:00:00Z"}).status_code == 400
    assert reader.get(f"/api/v1/ci/{'0' * 32}/as-of", params={"at": "2026-11-01T00:00:00Z"}).status_code == 404
    naive = reader.get(f"/api/v1/ci/{web}/as-of", params={"at": "2026-11-01T00:00:00"})
    assert naive.status_code == 422 and naive.json()["error"]["code"] == "saat_dilimi_yok"
    future = reader.get(f"/api/v1/ci/{web}/as-of", params={"at": (datetime.now(UTC) + timedelta(days=1)).isoformat()})
    assert future.status_code == 422 and future.json()["error"]["code"] == "gelecek_zaman"
    assert reader.get(f"/api/v1/ci/{web}/as-of").status_code == 422  # an zorunlu
    assert reader.get(f"/api/v1/ci/{web}/timeline", params={"before": "bozuk!"}).status_code == 400
    assert reader.get(f"/api/v1/ci/{web}/graph", params={"depth": 4}).status_code == 422
    with make_client(("evren_iam_admin",), "yetki-yoneticisi") as admin:  # CMDB okuma izni yok
        for path in ("as-of?at=2026-11-01T00:00:00Z", "timeline", "graph"):
            assert admin.get(f"/api/v1/ci/{web}/{path}").status_code == 403

"""Uygulama içi Mimari Harita ve Yol Haritası içeriğinin tutarlılığı."""
import json
import re
import sys

from evren_core.web import CONTENT_SECURITY_POLICY, SECURITY_HEADERS

STATUSES = {"done", "in_progress", "planned", "blocked", "at_risk"}


def test_architecture_outputs_are_up_to_date(repo_root):
    sys.path.insert(0, str(repo_root / "tools"))
    import build_architecture

    svg, js = build_architecture.render_outputs()
    content = repo_root / "apps/web/content"
    assert (content / "architecture.svg").read_text(encoding="utf-8") == svg, "python tools/build_architecture.py"
    assert (content / "architecture.json").read_text(encoding="utf-8") == js, "python tools/build_architecture.py"


def test_svg_is_csp_safe(repo_root):
    svg = (repo_root / "apps/web/content/architecture.svg").read_text(encoding="utf-8")
    assert "<script" not in svg and "<style" not in svg and " style=" not in svg
    assert not re.search(r"\son[a-z]+=", svg)


def test_roadmap_structure_and_component_links(repo_root):
    rm = json.loads((repo_root / "apps/web/content/roadmap.json").read_text(encoding="utf-8"))
    arch = json.loads((repo_root / "apps/web/content/architecture.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rm["updated_at"])
    assert set(rm["statuses"]) == STATUSES
    ids = set()
    phase_ids = [p["id"] for p in rm["phases"]]
    assert phase_ids == ["F0", "F1", "F2", "F3", "F4"]
    for phase in rm["phases"]:
        assert phase["goal"] and phase["window"] and phase["exit_criteria"]
        for item in phase["items"]:
            assert item["id"].startswith(phase["id"] + "-"), item["id"]
            assert item["id"] not in ids
            ids.add(item["id"])
            assert item["status"] in STATUSES
            if item["status"] in ("blocked", "at_risk", "in_progress"):
                assert item.get("note"), f"{item['id']}: durum için açıklama (note) zorunlu"
            for c in item.get("components", []):
                assert c in arch["nodes"], f"{item['id']}: haritada olmayan bileşen {c}"
    for node_id, node in arch["nodes"].items():
        assert node["phase"] in phase_ids, node_id


def test_durum_md_mentions_every_in_progress_or_blocked_item(repo_root):
    rm = json.loads((repo_root / "apps/web/content/roadmap.json").read_text(encoding="utf-8"))
    durum = (repo_root / "DURUM.md").read_text(encoding="utf-8")
    for phase in rm["phases"]:
        for item in phase["items"]:
            if item["status"] in ("in_progress", "blocked", "at_risk"):
                assert item["id"] in durum, f"DURUM.md {item['id']} kalemini anmalı"


def test_security_headers_policy():
    assert "unsafe-inline" not in CONTENT_SECURITY_POLICY
    assert "unsafe-eval" not in CONTENT_SECURITY_POLICY
    assert "default-src 'none'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"


def test_frontend_never_writes_html_from_strings(repo_root):
    js_files = list((repo_root / "apps/web/assets/js").rglob("*.js"))
    assert js_files
    for f in js_files:
        text = f.read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert forbidden not in text, f"{f.name}: {forbidden} kullanılmamalı"
    html = (repo_root / "apps/web/index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and "style=" not in html and "<style" not in html

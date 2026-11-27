"""Kullanım kılavuzu kuralları (U8.2, ADR-0033): giriş sayfası kısa, her konu girişten ulaşılabilir, bağlantılar var
olan konu/belge/sayfaya gider; kural bozulursa üretici reddeder (olumsuz senaryolar geçici klasörde)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("build_guide", REPO / "tools" / "build_guide.py")
bg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bg)


def test_real_guide_is_layered_and_complete():
    doc = bg.load()
    by = {t["id"]: t for t in doc["topics"]}
    start = by[doc["start"]]
    assert start["words"] <= 260
    assert isinstance(doc["draft"], bool)  # taslak notu arayüzde görünür
    assert all(t["words"] <= 900 for t in doc["topics"])
    # Ekranların yardımı kılavuza bağlanır: temel ekranların hepsinin bir konusu var
    for route in ("ci", "kuyruk", "sema", "kaynaklar", "sistem-durumu", "yonetim", "eskime", "sahiplik-onayi"):
        assert route in doc["route_topics"], route
    assert (REPO / "apps/web/content/guide.json").read_text(encoding="utf-8") == bg.render(doc)


def _guide(tmp_path, monkeypatch, start_md: str, other_md: str = "# Diğer\n\nMetin.\n", limit: int = 260):
    (tmp_path / "kilavuz.yaml").write_text(f"""title: T
start: a
start_word_limit: {limit}
topic_word_limit: 900
topics:
  - {{id: a, file: a.md, title: "Başla", summary: "s"}}
  - {{id: b, file: b.md, title: "Diğer", summary: "s", routes: [ci]}}
""", encoding="utf-8")
    (tmp_path / "a.md").write_text(start_md, encoding="utf-8")
    (tmp_path / "b.md").write_text(other_md, encoding="utf-8")
    monkeypatch.setattr(bg, "GUIDE", tmp_path)


def test_valid_minimal_guide(tmp_path, monkeypatch):
    _guide(tmp_path, monkeypatch, "# Başla\n\n[diğer](kilavuz:b) [CI](#/ci) [güvenlik](belge:06-guvenlik)\n")
    assert [t["id"] for t in bg.load()["topics"]] == ["a", "b"]


@pytest.mark.parametrize(("start_md", "limit", "error"), [
    ("# Başla\n\nBağlantı yok.\n", 260, "ulaşılamayan konular: b"),
    ("# Başla\n\n[x](kilavuz:yok) [b](kilavuz:b)\n", 260, "bilinmeyen kılavuz konusu yok"),
    ("# Başla\n\n[x](belge:../CLAUDE) [b](kilavuz:b)\n", 260, "belge yok"),
    ("# Başla\n\n[x](#/olmayan-sayfa) [b](kilavuz:b)\n", 260, "arayüzde olmayan sayfa #/olmayan-sayfa"),
    ("# Başla\n\n[x](javascript:alert(1)) [b](kilavuz:b)\n", 260, "desteklenmeyen bağlantı"),
    ("# Yanlış başlık\n\n[b](kilavuz:b)\n", 260, "ilk satır başlık olmalı"),
    ("# Başla\n\n" + "kelime " * 40 + "[b](kilavuz:b)\n", 20, "kelime (sınır 20)"),
])
def test_rule_violations_are_rejected(tmp_path, monkeypatch, start_md, limit, error):
    _guide(tmp_path, monkeypatch, start_md, limit=limit)
    with pytest.raises(bg.GuideError, match=error.replace("(", r"\(").replace(")", r"\)")):
        bg.load()

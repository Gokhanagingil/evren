"""Uzlaştırıcı PostgreSQL testleri: göç + tohum uygulanmış veritabanı ve ``EVREN_TEST_PG=1`` gerekir.

Her test tek bir dış işlem içinde çalışır ve sonunda geri alınır (``force_rollback``); deponun kendi
işlem blokları iç içe kayıt noktası (savepoint) olur. Uzlaştırıcı ``evren_app_reconciler`` rolüyle,
yani üretimdeki en az yetkiyle çalışır.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/reconciler", REPO / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")

from evren_core.metadata import load_metadata  # noqa: E402


@pytest.fixture(scope="session")
def metadata():
    return load_metadata(REPO / "metadata")


@pytest.fixture
def conn() -> Iterator[Any]:
    with psycopg.connect(autocommit=True) as c, c.transaction(force_rollback=True):
        yield c


@pytest.fixture
def as_reconciler(conn):
    conn.execute("SET ROLE evren_app_reconciler")
    yield conn
    conn.execute("RESET ROLE")

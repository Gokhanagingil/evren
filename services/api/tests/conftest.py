"""API testleri: göç + tohum + demo verisi yüklenmiş PostgreSQL ve ``EVREN_TEST_PG=1``.

Uygulama gerçek bir bağlantı havuzuyla ve ``evren_app_api`` rolüyle çalışır (üretimle aynı en az yetki).
Karar testleri kendi kuyruk kaydını uzlaştırıcıyla oluşturur; ortak veriye bağımlı değildir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/api", REPO / "services/reconciler", REPO / "tools",
          REPO / "services/governance"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from evren_api import create_app  # noqa: E402
from evren_api.config import Settings  # noqa: E402

CSRF = {"X-Evren-Request": "1"}


def make_client(roles: tuple[str, ...] = ("evren_reader", "evren_match_reviewer"), user: str = "test-user") -> TestClient:
    env = {"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": ",".join(roles), "EVREN_DEV_USER": user,
           "EVREN_DB_POOL_SIZE": "2"}
    return TestClient(create_app(Settings.from_env(env)))


@pytest.fixture(scope="module")
def client():
    with make_client() as c:
        yield c


@pytest.fixture(scope="module")
def reader():
    with make_client(("evren_reader",), "salt-okuyucu") as c:
        yield c

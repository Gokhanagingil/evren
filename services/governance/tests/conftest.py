"""Görev çalışanı testleri: gerçek PostgreSQL (``EVREN_TEST_PG=1``, göç ve tohum uygulanmış) ve bellek içi ServiceNow."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "packages/evren-core/src", REPO / "services/governance"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evren_core.identity import (
    IdentityEngine,
    IdentityResolver,
    InMemoryCandidateIndex,
    InMemoryLinkStore,
    RuleSet,
)
from evren_core.metadata import load_metadata

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def metadata():
    return load_metadata(REPO_ROOT / "metadata")


@pytest.fixture(scope="session")
def hierarchy(metadata):
    return metadata.hierarchy


@pytest.fixture
def engine(metadata) -> IdentityEngine:
    counter = iter(range(1, 10_000))
    rules = RuleSet.from_metadata(metadata.identification, metadata.hierarchy)
    resolver = IdentityResolver(rules, InMemoryCandidateIndex(), InMemoryLinkStore())
    return IdentityEngine(resolver, id_factory=lambda: f"{next(counter):032x}")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 11, 25, 9, 0, tzinfo=UTC)

"""Kısıtlı güvenlik duruşu (ADR-0023): yalnızca ``restricted`` okuma yetkisiyle; her gösterim denetim izinde."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import make_client


@pytest.fixture
def posture_ci():
    with psycopg.connect(autocommit=True) as c:
        sys_id = c.execute("""SELECT ci.sys_id FROM cmdb.cmdb_ci ci
                               WHERE NOT EXISTS (SELECT 1 FROM cmdb.ci_security_posture p WHERE p.ci_sys_id = ci.sys_id)
                               ORDER BY ci.sys_id LIMIT 1""").fetchone()[0]
        c.execute("""INSERT INTO cmdb.ci_security_posture (ci_sys_id, data_source, protection_status, health_status,
                                                          exposure_level, risk_score, observed_at)
                     VALUES (%s, 'Defender', 'Onboarded', 'Active', 'High', 'Medium', now())""", (sys_id,))
    yield sys_id
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM cmdb.ci_security_posture WHERE ci_sys_id = %s", (sys_id,))


def reads(sys_id: str, principal: str) -> int:
    with psycopg.connect(autocommit=True) as c:
        return c.execute("""SELECT count(*) FROM audit.security_event WHERE action = 'restricted_read'
                             AND principal = %s AND resource = %s""", (principal, f"cmdb_ci:{sys_id}")).fetchone()[0]


def test_reader_never_sees_posture(posture_ci, reader):
    doc = reader.get(f"/api/v1/ci/{posture_ci}").json()
    assert "security_posture" not in doc and "High" not in str(doc.get("attributes"))
    assert reads(posture_ci, "dev:salt-okuyucu") == 0


@pytest.mark.parametrize("role", ["evren_security_reader", "evren_auditor"])
def test_restricted_roles_see_posture_and_reading_is_audited(posture_ci, role):
    user = f"kisitli-{uuid.uuid4().hex[:6]}"
    with make_client(("evren_reader", role), user) as c:
        doc = c.get(f"/api/v1/ci/{posture_ci}").json()
    p = doc["security_posture"]
    assert p["exposure_level"] == "High" and p["risk_score"] == "Medium" and p["data_source"] == "Defender"
    assert p["health_status"] == "Active" and p["observed_at"]
    assert reads(posture_ci, f"dev:{user}") == 1

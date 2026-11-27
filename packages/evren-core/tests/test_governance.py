"""Yönetişim görevleri çekirdeği (U8.4, ADR-0035): sorumlu seçimi, kanal ayarı doğrulaması, ServiceNow'a yalnızca
standart alanlar, durum yorumu, simülatörle uçtan uca kanal; veritabanı gerekmez."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evren_core.connectors.http import HttpClient, HttpError
from evren_core.governance import (
    ALLOWED_SN_FIELDS,
    ChannelSettings,
    Party,
    ServiceNowTaskChannel,
    ServiceNowTaskSimulator,
    TaskForChannel,
    pick_assignee,
)

U1, U2, G1 = "1" * 32, "2" * 32, "3" * 32


@pytest.mark.parametrize(("owned", "managed", "group", "expected"), [
    (Party(U1), Party(U2), Party(G1), (U1, G1, "owned_by")),
    (Party(U1, active=False), Party(U2), Party(G1), (U2, G1, "managed_by")),
    (None, None, Party(G1), (None, G1, "support_group")),
    (Party(U1, active=False), None, Party(G1, active=False), (None, None, "unassigned")),
    (None, None, None, (None, None, "unassigned")),
])
def test_assignee_is_owner_then_manager_then_support_group(owned, managed, group, expected):
    a = pick_assignee(owned_by=owned, managed_by=managed, support_group=group)
    assert (a.user, a.group, a.basis) == expected


def test_channel_settings_validation():
    assert ChannelSettings.from_json({}).table == "sc_task"
    for bad in ({"table": "sys_user"}, {"table": "cmdb_ci"}, {"table": "Görev"}, {"close_state": "kapalı"},
                {"closed_states": {"3": "bitti"}}, {"default_group": "grup"}):
        with pytest.raises(ValueError):
            ChannelSettings.from_json(bad)
    s = ChannelSettings.from_json({"table": "vtb_task", "close_state": "3", "cancel_state": "7",
                                   "closed_states": {"3": "done", "7": "cancelled"}, "default_group": G1})
    assert s.to_json()["default_group"] == G1 and s.cancel_state == "7"


def _channel(sim: ServiceNowTaskSimulator, **settings) -> ServiceNowTaskChannel:
    http = HttpClient("https://ornek.service-now.test", transport=sim, auth_header=HttpClient.basic("u", "p"),
                      retries=0)
    return ServiceNowTaskChannel(http, ChannelSettings.from_json(settings))


def task(**kw) -> TaskForChannel:
    base = {"sys_id": "a" * 32, "number": "EGT0001001", "title": "Veri teyidi", "description": "Bakın.",
            "due_at": datetime(2026, 12, 31, 20, 0, tzinfo=UTC), "assigned_to": U1}
    return TaskForChannel(**{**base, **kw})


def test_payload_has_only_standard_task_fields_and_link():
    sim = ServiceNowTaskSimulator()
    ch = _channel(sim)
    body = ch.payload(task(assignment_group=G1, cmdb_ci="c" * 32, link="https://evren/#/gorevler/x"))
    assert set(body) <= ALLOWED_SN_FIELDS
    assert body["correlation_id"] == "a" * 32 and body["due_date"] == "2026-12-31 20:00:00"
    assert body["short_description"].startswith("[Evren EGT0001001]") and "https://evren/#/gorevler/x" in body["description"]
    assert ch.deliverable(task(assigned_to=None)) is False
    assert _channel(sim, default_group=G1).deliverable(task(assigned_to=None)) is True


def test_simulated_round_trip_create_find_close_and_interpret():
    sim = ServiceNowTaskSimulator()
    ch = _channel(sim)
    rec = ch.create(task())
    assert ch.find("a" * 32) == rec and ch.create(task()) == rec  # ikinci açma yok
    assert ch.interpret(ch.get(rec.sys_id)) == "open"
    closed = ch.close(rec.sys_id, outcome="cancelled", note="İptal")
    assert closed.state == "4" and not closed.active and ch.interpret(closed) == "cancelled"
    assert not sim.unknown_fields
    sim.fail_next = 1
    with pytest.raises(HttpError) as err:
        ch.get(rec.sys_id)
    assert err.value.status == 503 and "u:p" not in str(err.value)

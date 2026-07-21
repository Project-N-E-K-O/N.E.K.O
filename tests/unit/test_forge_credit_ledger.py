from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from main_logic import forge_credit_ledger as ledger


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(tmp_path))


def test_grant_is_installation_local_and_idempotent() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    payload = {"trigger_type": "emotion_combo", "idem_key": "drop-idem-1"}
    first = ledger.grant_credit(payload, now=now, rarity="SR")
    duplicate = ledger.grant_credit(payload, now=now, rarity="N")

    assert first["granted"] is True
    assert duplicate["reason"] == "duplicate"
    assert duplicate["rarity"] == "SR"
    assert ledger.list_credits(now)["count"] == 1


def test_reserve_commit_and_replay_are_idempotent() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "drop-idem-2"},
        now=now,
        rarity="R",
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "11111111-1111-4111-8111-111111111111"
    card_id = "22222222-2222-4222-8222-222222222222"

    first = ledger.reserve_credit(credit_id, operation_id, now=now)
    replay = ledger.reserve_credit(credit_id, operation_id, now=now + timedelta(seconds=1))
    assert first["credit"]["status"] == replay["credit"]["status"] == "reserved"
    assert ledger.list_credits(now)["count"] == 0
    assert len(ledger.list_credits(now)["reservations"]) == 1

    assert ledger.commit_credit(credit_id, operation_id, card_id, now=now)["committed"]
    assert ledger.commit_credit(credit_id, operation_id, card_id, now=now)["committed"]


def test_release_and_expiry() -> None:
    now = datetime(2026, 7, 13, 23, 59, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "idle", "idem_key": "drop-idem-3"}, now=now, rarity="N"
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "33333333-3333-4333-8333-333333333333"
    ledger.reserve_credit(credit_id, operation_id, now=now)
    ledger.release_credit(credit_id, operation_id, now=now)
    assert ledger.list_credits(now)["count"] == 1
    assert ledger.list_credits(now + timedelta(minutes=2))["count"] == 0


@pytest.mark.parametrize("transition", ["commit", "release"])
def test_expired_reservation_cannot_transition_back_to_active_or_consumed(
    transition: str,
) -> None:
    now = datetime(2026, 7, 13, 23, 59, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "idle", "idem_key": f"expired-{transition}-idem"},
        now=now,
        rarity="N",
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "33333333-3333-4333-8333-333333333334"
    ledger.reserve_credit(credit_id, operation_id, now=now)
    expired_at = now + timedelta(minutes=2)

    with pytest.raises(RuntimeError, match="reservation_not_active"):
        if transition == "commit":
            ledger.commit_credit(
                credit_id,
                operation_id,
                "44444444-4444-4444-8444-444444444444",
                now=expired_at,
            )
        else:
            ledger.release_credit(credit_id, operation_id, now=expired_at)

    persisted = ledger._load()["credits"][0]
    assert persisted["status"] == "expired"
    assert persisted["expired_at"] == ledger._iso(expired_at)


def test_daily_and_trigger_caps() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    for index in range(2):
        assert ledger.grant_credit(
            {"trigger_type": "5rounds", "idem_key": f"round-{index}-idem"},
            now=now,
            rarity="N",
        )["granted"]
    blocked = ledger.grant_credit(
        {"trigger_type": "5rounds", "idem_key": "round-blocked"}, now=now, rarity="N"
    )
    assert blocked == {
        "granted": False,
        "reason": "trigger_daily_cap",
        "available": 4,
        "active_count": 2,
    }

    for index in range(4):
        assert ledger.grant_credit(
            {
                "trigger_type": "emotion_combo",
                "idem_key": f"emotion-{index}-idem",
            },
            now=now,
            rarity="N",
        )["granted"]
    daily_blocked = ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "emotion-blocked-idem"},
        now=now,
        rarity="N",
    )
    assert daily_blocked == {
        "granted": False,
        "reason": "daily_cap",
        "available": 0,
        "active_count": 6,
    }

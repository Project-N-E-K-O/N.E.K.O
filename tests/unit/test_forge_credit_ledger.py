from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from main_logic import forge_credit_ledger as ledger

OWNER_A_ID = "11111111-1111-4111-8111-111111111111"
OWNER_B_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_USER_DATA_DIR", str(tmp_path))


def test_high_rarity_drop_weight_is_four_percent() -> None:
    assert sum(ledger.RARITY_WEIGHTS.values()) == 100
    assert sum(
        ledger.RARITY_WEIGHTS[rarity] for rarity in ("SR", "SSR", "UR")
    ) == 4
    assert ledger.RARITY_WEIGHTS == {
        "UR": 0,
        "SSR": 0.5,
        "SR": 3.5,
        "R": 26,
        "N": 70,
    }


def test_grant_is_installation_local_and_idempotent() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    payload = {"trigger_type": "emotion_combo", "idem_key": "drop-idem-1"}
    first = ledger.grant_credit(payload, now=now, rarity="SR")
    duplicate = ledger.grant_credit(payload, now=now, rarity="N")

    assert first["granted"] is True
    assert duplicate["reason"] == "duplicate"
    assert duplicate["rarity"] == "SR"
    assert ledger.list_credits(now)["count"] == 1


@pytest.mark.parametrize("mutation", ["rarity", "inject", "delete"])
def test_signed_ledger_rejects_out_of_band_credit_tampering(mutation: str) -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "signed-ledger-credit"},
        now=now,
        rarity="N",
    )
    path = ledger._ledger_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "rarity":
        raw["credits"][0]["rarity"] = "UR"
    elif mutation == "inject":
        injected = dict(raw["credits"][0])
        injected["id"] = "99999999-9999-4999-8999-999999999999"
        injected["idem_key"] = "injected-credit-idem"
        raw["credits"].append(injected)
    else:
        raw["credits"].clear()
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ledger.LedgerIntegrityError, match="ledger_integrity_failed"):
        ledger.list_credits(now)
    with pytest.raises(ledger.LedgerIntegrityError, match="ledger_integrity_failed"):
        ledger.grant_credit(
            {"trigger_type": "emotion_combo", "idem_key": "cannot-reset-after-tamper"},
            now=now,
            rarity="N",
        )


def test_unsigned_legacy_ledger_is_migrated_only_before_signing_key_exists() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    legacy = {
        "version": 1,
        "credits": [
            {
                "id": "AAAAAAAAAAAA4AAA8AAAAAAAAAAAAAAA",
                "rarity": "R",
                "trigger_type": "emotion_combo",
                "idem_key": "legacy-credit-idem",
                "status": "reserved",
                "created_at": ledger._iso(now),
                "expires_at": ledger._iso(now + timedelta(hours=16)),
                "operation_id": "BBBBBBBBBBBB4BBB8BBBBBBBBBBBBBBB",
                "reservation_owner_id": OWNER_A_ID.replace("-", "").upper(),
                "reserved_at": ledger._iso(now),
            }
        ],
    }
    path = ledger._ledger_path()
    path.write_text(json.dumps(legacy), encoding="utf-8")

    snapshot = ledger.list_credits(now, reservation_owner_id=OWNER_A_ID)
    assert snapshot["count"] == 0
    assert snapshot["reservations"][0]["id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert snapshot["reservations"][0]["operation_id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == ledger.LEDGER_VERSION
    assert migrated["integrity"]["algorithm"] == "hmac-sha256"
    if os.name != "nt":
        assert ledger._signing_key_path().stat().st_mode & 0o777 == 0o600

    migrated.pop("integrity")
    migrated["version"] = 1
    path.write_text(json.dumps(migrated), encoding="utf-8")
    with pytest.raises(ledger.LedgerIntegrityError, match="ledger_version_invalid"):
        ledger.list_credits(now)


def test_deleting_a_signed_ledger_does_not_reset_the_daily_cap() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "delete-ledger-idem"},
        now=now,
        rarity="N",
    )
    ledger._ledger_path().unlink()

    with pytest.raises(ledger.LedgerIntegrityError, match="ledger_missing"):
        ledger.list_credits(now)
    with pytest.raises(ledger.LedgerIntegrityError, match="ledger_missing"):
        ledger.grant_credit(
            {"trigger_type": "emotion_combo", "idem_key": "delete-reset-idem"},
            now=now,
            rarity="N",
        )


def test_interrupted_first_save_recovers_from_pending_key(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    original_mark = ledger._mark_signing_key_established
    interrupted = True

    def fail_before_established(key):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            raise ledger.LedgerIntegrityError("simulated_interruption")
        original_mark(key)

    monkeypatch.setattr(ledger, "_mark_signing_key_established", fail_before_established)
    with pytest.raises(ledger.LedgerIntegrityError, match="simulated_interruption"):
        ledger.grant_credit(
            {"trigger_type": "emotion_combo", "idem_key": "interrupted-first-save"},
            now=now,
            rarity="N",
        )

    # The signed ledger was already replaced before the simulated interruption.
    # A pending key must authenticate it and promote itself instead of bricking
    # the installation as an apparent deleted-ledger event.
    assert ledger.list_credits(now)["count"] == 1
    raw_key = ledger._signing_key_path().read_bytes()
    assert raw_key[:1] == ledger._KEY_ESTABLISHED_PREFIX


def test_pending_key_without_ledger_retries_initial_save() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    _key, created, established = ledger._read_or_create_signing_key()
    assert created is True
    assert established is False
    assert not ledger._ledger_path().exists()

    result = ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "pending-key-retry"},
        now=now,
        rarity="N",
    )

    assert result["granted"] is True
    assert ledger._signing_key_path().read_bytes().startswith(
        ledger._KEY_ESTABLISHED_PREFIX
    )
    assert ledger.list_credits(now)["count"] == 1


def test_pending_key_retries_interrupted_legacy_migration() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    legacy = {
        "version": 1,
        "credits": [
            {
                "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "rarity": "N",
                "trigger_type": "emotion_combo",
                "idem_key": "interrupted-legacy-migration",
                "status": "active",
                "created_at": ledger._iso(now),
                "expires_at": ledger._iso(now + timedelta(hours=16)),
            }
        ],
    }
    ledger._ledger_path().write_text(json.dumps(legacy), encoding="utf-8")
    _key, created, established = ledger._read_or_create_signing_key()
    assert created is True
    assert established is False

    assert ledger.list_credits(now)["count"] == 1
    assert ledger._signing_key_path().read_bytes().startswith(
        ledger._KEY_ESTABLISHED_PREFIX
    )
    migrated = json.loads(ledger._ledger_path().read_text(encoding="utf-8"))
    assert migrated["version"] == ledger.LEDGER_VERSION
    assert migrated["integrity"]["algorithm"] == "hmac-sha256"
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

    first = ledger.reserve_credit(
        credit_id,
        operation_id,
        OWNER_A_ID,
        now=now,
    )
    replay = ledger.reserve_credit(
        credit_id,
        operation_id,
        OWNER_A_ID,
        now=now + timedelta(seconds=1),
    )
    assert first["credit"]["status"] == replay["credit"]["status"] == "reserved"
    assert ledger.list_credits(now)["count"] == 0
    assert len(
        ledger.list_credits(now, reservation_owner_id=OWNER_A_ID)["reservations"]
    ) == 1

    assert ledger.commit_credit(
        credit_id,
        operation_id,
        card_id,
        OWNER_A_ID,
        now=now,
    )["committed"]


def test_operation_uuid_inputs_are_canonicalized_before_signing() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "uuid-normalization"},
        now=now,
        rarity="R",
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "33333333-3333-4333-8333-333333333333"
    card_id = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"

    reserved = ledger.reserve_credit(
        credit_id.replace("-", ""),
        operation_id.replace("-", ""),
        OWNER_A_ID.replace("-", ""),
        now=now,
    )
    committed = ledger.commit_credit(
        credit_id.upper(),
        operation_id.upper(),
        card_id.replace("-", ""),
        OWNER_A_ID,
        now=now,
    )

    assert reserved["operation_id"] == operation_id
    assert reserved["credit"]["operation_id"] == operation_id
    assert committed["credit"]["card_id"] == card_id.lower()
    assert ledger.commit_credit(
        credit_id,
        operation_id,
        card_id,
        OWNER_A_ID,
        now=now,
    )["committed"]


def test_release_and_expiry() -> None:
    now = datetime(2026, 7, 13, 23, 59, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "idle", "idem_key": "drop-idem-3"}, now=now, rarity="N"
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "33333333-3333-4333-8333-333333333333"
    ledger.reserve_credit(credit_id, operation_id, OWNER_A_ID, now=now)
    ledger.release_credit(credit_id, operation_id, OWNER_A_ID, now=now)
    assert ledger.release_credit(
        credit_id,
        operation_id,
        OWNER_A_ID,
        now=now,
    )["released"]
    assert ledger.list_credits(now)["count"] == 1
    assert ledger.list_credits(now + timedelta(minutes=2))["count"] == 0


def test_reserved_credit_can_commit_after_credit_deadline() -> None:
    now = datetime(2026, 7, 13, 23, 59, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "idle", "idem_key": "late-commit-idem"},
        now=now,
        rarity="N",
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "33333333-3333-4333-8333-333333333334"
    ledger.reserve_credit(credit_id, operation_id, OWNER_A_ID, now=now)
    committed_at = now + timedelta(minutes=2)
    card_id = "44444444-4444-4444-8444-444444444444"

    snapshot = ledger.list_credits(
        committed_at,
        reservation_owner_id=OWNER_A_ID,
    )
    assert snapshot["count"] == 0
    assert len(snapshot["reservations"]) == 1
    assert ledger.commit_credit(
        credit_id,
        operation_id,
        card_id,
        OWNER_A_ID,
        now=committed_at,
    )["committed"]
    persisted = ledger._load()["credits"][0]
    assert persisted["status"] == "consumed"
    assert persisted["card_id"] == card_id


def test_releasing_reservation_after_credit_deadline_expires_credit() -> None:
    now = datetime(2026, 7, 13, 23, 59, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "idle", "idem_key": "late-release-idem"},
        now=now,
        rarity="N",
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "33333333-3333-4333-8333-333333333335"
    ledger.reserve_credit(credit_id, operation_id, OWNER_A_ID, now=now)
    released_at = now + timedelta(minutes=2)

    released = ledger.release_credit(
        credit_id,
        operation_id,
        OWNER_A_ID,
        now=released_at,
    )
    replay = ledger.release_credit(
        credit_id,
        operation_id,
        OWNER_A_ID,
        now=released_at,
    )

    assert released["credit"]["status"] == "expired"
    assert replay == released
    assert ledger.list_credits(released_at) == {
        "count": 0,
        "credits": [],
        "reservations": [],
    }
    persisted = ledger._load()["credits"][0]
    assert persisted["status"] == "expired"
    assert persisted["expired_at"] == ledger._iso(released_at)


def test_reservations_are_visible_and_mutable_only_by_their_owner() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "owner-bound-reservation"},
        now=now,
        rarity="SR",
    )
    active_for_a = ledger.list_credits(now, reservation_owner_id=OWNER_A_ID)
    active_for_b = ledger.list_credits(now, reservation_owner_id=OWNER_B_ID)
    credit_id = active_for_a["credits"][0]["id"]
    operation_id = "55555555-5555-4555-8555-555555555555"
    card_id = "66666666-6666-4666-8666-666666666666"

    assert active_for_a["credits"] == active_for_b["credits"]
    ledger.reserve_credit(credit_id, operation_id, OWNER_A_ID, now=now)

    visible_to_a = ledger.list_credits(now, reservation_owner_id=OWNER_A_ID)
    visible_to_b = ledger.list_credits(now, reservation_owner_id=OWNER_B_ID)
    assert [item["operation_id"] for item in visible_to_a["reservations"]] == [
        operation_id
    ]
    assert "reservation_owner_id" not in visible_to_a["reservations"][0]
    assert visible_to_b["reservations"] == []
    assert visible_to_b["credits"] == []
    assert ledger._load()["credits"][0]["reservation_owner_id"] == OWNER_A_ID

    with pytest.raises(RuntimeError, match="reservation_owner_mismatch"):
        ledger.commit_credit(
            credit_id,
            operation_id,
            card_id,
            OWNER_B_ID,
            now=now,
        )
    with pytest.raises(RuntimeError, match="reservation_owner_mismatch"):
        ledger.release_credit(
            credit_id,
            operation_id,
            OWNER_B_ID,
            now=now,
        )

    assert ledger.commit_credit(
        credit_id,
        operation_id,
        card_id,
        OWNER_A_ID,
        now=now,
    )["committed"]


def test_legacy_unbound_reservation_cannot_be_adopted_or_released() -> None:
    now = datetime(2026, 7, 13, 8, tzinfo=UTC)
    ledger.grant_credit(
        {"trigger_type": "emotion_combo", "idem_key": "legacy-unbound-owner"},
        now=now,
        rarity="R",
    )
    credit_id = ledger.list_credits(now)["credits"][0]["id"]
    operation_id = "77777777-7777-4777-8777-777777777777"
    card_id = "88888888-8888-4888-8888-888888888888"
    ledger.reserve_credit(credit_id, operation_id, OWNER_A_ID, now=now)
    data = ledger._load()
    data["credits"][0].pop("reservation_owner_id")
    ledger._save(data)

    assert ledger.list_credits(
        now,
        reservation_owner_id=OWNER_A_ID,
    )["reservations"] == []
    assert ledger.list_credits(
        now,
        reservation_owner_id=OWNER_B_ID,
    )["reservations"] == []
    for owner_id in (OWNER_A_ID, OWNER_B_ID):
        with pytest.raises(RuntimeError, match="reservation_owner_mismatch"):
            ledger.reserve_credit(
                credit_id,
                operation_id,
                owner_id,
                now=now,
            )
        with pytest.raises(RuntimeError, match="reservation_owner_mismatch"):
            ledger.commit_credit(
                credit_id,
                operation_id,
                card_id,
                owner_id,
                now=now,
            )
        with pytest.raises(RuntimeError, match="reservation_owner_mismatch"):
            ledger.release_credit(
                credit_id,
                operation_id,
                owner_id,
                now=now,
            )

    persisted = ledger._load()["credits"][0]
    assert persisted["status"] == "reserved"
    assert "reservation_owner_id" not in persisted


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

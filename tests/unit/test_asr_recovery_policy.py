"""Failure provenance and replacement budgets must fail closed."""

import pytest

from main_logic.asr_client.recovery import (
    FailureSource,
    RecoveryBudget,
    RecoveryDisposition,
    classify_failure,
)


@pytest.mark.parametrize("code, source", [
    ("ASR_PROVIDER_FINAL_TIMEOUT", FailureSource.RUNTIME),
    ("ASR_PROVIDER_FINAL_TIMEOUT", FailureSource.PROVIDER),
    ("ASR_QWEN_READ_DISCONNECTED", FailureSource.PROVIDER),
])
def test_only_known_faults_with_correct_provenance_recover(code, source):
    assert classify_failure(code, source=source) is RecoveryDisposition.RECOVER
    for other in FailureSource:
        if other != source:
            if code == "ASR_PROVIDER_FINAL_TIMEOUT" and other in {
                FailureSource.RUNTIME, FailureSource.PROVIDER,
            }:
                continue
            assert classify_failure(code, source=other) is RecoveryDisposition.STOP


def test_connect_retry_is_distinct_from_replacing_an_active_connection():
    assert classify_failure(
        "ASR_QWEN_CONNECTION_FAILED", source=FailureSource.CONNECT,
    ) is RecoveryDisposition.RETRY_CONNECT
    assert classify_failure(
        "ASR_QWEN_CONNECTION_FAILED", source=FailureSource.PROVIDER,
    ) is RecoveryDisposition.STOP


@pytest.mark.parametrize("code", [
    "ASR_CREDENTIALS_REJECTED", "ASR_CREDENTIALS_MISSING", "ASR_INVALID_CONFIG",
    "ASR_ENDPOINTING_NOT_SUPPORTED", "ASR_ENDPOINTING_FAILED",
    "ASR_AUDIO_ORDERING_FAILED", "ASR_INPUT_DELIVERY_UNCERTAIN",
    "ASR_INPUT_DELIVERY_FAILED", "ASR_INGRESS_BACKPRESSURE",
    "ASR_QWEN_CONNECTION_CLOSED", "ASR_QWEN_WORKER_FAILED",
    "ASR_QWEN_PROVIDER_ERROR", "ASR_WORKER_FAILED", "ASR_START_STALE",
    "ASR_UNKNOWN_FUTURE_ERROR", "ASR_PROVIDER_FINAL_TIMEOUT: timeout",
    "connection closed unexpectedly", "", None,
])
def test_ambiguous_and_fatal_codes_never_recover_even_if_the_message_sounds_retryable(code):
    for source in FailureSource:
        assert classify_failure(code, source=source) is RecoveryDisposition.STOP


def test_cleanup_and_backoff_spend_the_same_deadline_as_connection_attempts():
    budget = RecoveryBudget()
    assert not budget.claim_attempt(100)
    assert budget.begin(100) == 112
    # Ten seconds have been spent retiring the old transport.
    assert budget.remaining_seconds(110) == 2
    assert budget.claim_attempt(110)
    assert budget.remaining_seconds(111.5) == 0.5
    assert not budget.claim_attempt(112)
    assert budget.attempts_used == 1
    assert budget.remaining_seconds(120) == 0


def test_handshake_followed_by_another_failure_does_not_replenish_attempts():
    budget = RecoveryBudget()
    budget.begin(0)
    assert budget.claim_attempt(1)
    # A replacement connected, then failed before any useful accepted final.
    budget.begin(20)
    assert budget.claim_attempt(21)
    budget.begin(40)
    assert not budget.claim_attempt(41)
    assert budget.attempts_used == 2


def test_two_attempts_maximum_even_when_deadline_has_time_left():
    budget = RecoveryBudget()
    budget.begin(0)
    assert budget.claim_attempt(0)
    assert budget.claim_attempt(0.1)
    assert not budget.claim_attempt(0.2)


def test_useful_accepted_final_or_explicit_new_session_resets_failure_streak():
    budget = RecoveryBudget()
    budget.begin(0)
    assert budget.claim_attempt(1)
    assert budget.claim_attempt(2)
    budget.mark_completed_turn()
    assert budget.attempts_used == 0
    assert budget.deadline is None
    assert not budget.claim_attempt(3)
    budget.begin(3)
    assert budget.claim_attempt(4)
    new_session_budget = RecoveryBudget()
    assert new_session_budget.attempts_used == 0
    assert new_session_budget.deadline is None


@pytest.mark.parametrize("kwargs", [
    {"max_attempts": 0}, {"total_seconds": 0}, {"total_seconds": float("inf")},
    {"total_seconds": float("nan")}, {"attempts_used": -1}, {"attempts_used": 3},
    {"deadline": float("nan")},
])
def test_invalid_budget_is_rejected(kwargs):
    with pytest.raises(ValueError):
        RecoveryBudget(**kwargs)


@pytest.mark.parametrize("now", [float("inf"), float("nan")])
def test_invalid_clock_cannot_create_an_unbounded_operation(now):
    budget = RecoveryBudget()
    with pytest.raises(ValueError):
        budget.begin(now)
    with pytest.raises(ValueError):
        budget.claim_attempt(now)

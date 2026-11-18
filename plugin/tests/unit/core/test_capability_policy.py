from __future__ import annotations

import pytest

from plugin.core import capability_policy as policy

pytestmark = pytest.mark.plugin_unit

GRANTS = {
    "a": frozenset({"memory.read", "messages.push"}),
    "b": frozenset(),
    "c": frozenset({"memory.read"}),
}


def granted(plugin_id: str) -> frozenset[str]:
    return GRANTS.get(plugin_id, frozenset())


def test_every_plugin_on_the_chain_must_hold_the_capability():
    decision = policy.decide("memory.read", ("c", "a"), granted)
    assert decision.judged and decision.allowed and decision.reason is None


def test_laundering_through_a_granted_plugin_is_a_caller_chain_denial():
    # B lacks memory.read, calls A which has it: A's use is not B's to borrow.
    decision = policy.decide("memory.read", ("b", "a"), granted)
    assert not decision.allowed
    assert decision.blocked_by == ("b",)
    assert decision.caller_blocked and not decision.self_blocked
    assert decision.reason == policy.REASON_CALLER_CHAIN


def test_self_not_approved_without_callers():
    decision = policy.decide("memory.read", ("b",), granted)
    assert decision.reason == policy.REASON_SELF_NOT_APPROVED
    assert decision.blocked_by == ("b",)


def test_caller_chain_wins_when_both_are_missing():
    decision = policy.decide("messages.push", ("b", "c"), granted)
    assert decision.blocked_by == ("b", "c")
    assert decision.reason == policy.REASON_CALLER_CHAIN


def test_reentry_by_the_same_plugin_is_not_a_caller_denial():
    decision = policy.decide("memory.read", ("b", "b"), granted)
    assert not decision.caller_blocked and decision.self_blocked


def test_capabilities_outside_the_vocabulary_are_not_judged():
    decision = policy.decide("screen.capture", ("b",), granted)
    assert not decision.judged and decision.allowed


def test_observe_stage_never_rejects():
    denial = policy.decide("memory.read", ("b", "a"), granted)
    for mode in policy.POLICY_MODES:
        assert policy.should_reject(denial, mode) is False


def test_error_shape_is_defined():
    error = policy.not_granted_error(policy.decide("memory.read", ("b", "a"), granted))
    assert error["code"] == policy.CAPABILITY_NOT_GRANTED == "CAPABILITY_NOT_GRANTED"
    assert error["details"] == {
        "capability": "memory.read",
        "blocked_by": ["b"],
        "reason": "caller_chain",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("off", "off"), ("observe", "observe"), ("enforce", "enforce"), ("bogus", "observe")],
)
def test_current_mode_reads_the_setting(monkeypatch, raw, expected):
    from plugin import settings

    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", raw)
    assert policy.current_mode() == expected
    assert policy.policy_enabled() is (expected != "off")


@pytest.mark.parametrize(
    ("env", "expected"),
    [(None, "observe"), (" OFF ", "off"), ("enforce", "enforce"), ("loud", "observe")],
)
def test_setting_parses_the_environment(tmp_path, env, expected):
    # A child process, so the parent's imported settings stay untouched.
    import os
    import subprocess
    import sys

    from tests.process_isolation import child_process_env

    base = {k: v for k, v in os.environ.items() if k != "NEKO_PLUGIN_CAPABILITY_POLICY"}
    if env is not None:
        base["NEKO_PLUGIN_CAPABILITY_POLICY"] = env
    result = subprocess.run(
        [sys.executable, "-c", "from plugin import settings; print(settings.PLUGIN_CAPABILITY_POLICY)"],
        env=child_process_env(tmp_path, base),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == expected

"""Strict-review counterexamples; all application roots are isolated by conftest."""
from types import SimpleNamespace

import pytest

from services.chat_sync import host
from services.proactive_feedback.policy import PolicyRegistry


@pytest.mark.parametrize("wall_jump", [7200.0, -3600.0], ids=["clock-forward", "clock-backward"])
def test_same_owner_credential_refresh_preserves_running_monotonic_cooldown(wall_jump):
    clock = SimpleNamespace(wall=1_000_000.0, tick=100.0)
    registry = PolicyRegistry(wall_clock=lambda: clock.wall, clock=lambda: clock.tick)
    owner = {"key": "same-account"}
    policy = {"news": {"revision": 1, "decline_count": 1.0,
                       "cooldown_until": clock.wall + 1200, "updated_at": clock.wall}}
    first = registry.publish("synthetic-db", owner, "a" * 32, policy)
    assert not first.guard(["news"])()
    clock.wall += 900 + wall_jump
    clock.tick += 900
    # Supported credential writers do this on ordinary same-account refresh.
    host.invalidate_saved_session()
    refreshed = registry.publish("synthetic-db", owner, "a" * 32, policy)
    assert not first.guard(["music"])(), "Credential refresh still retires the old delivery lease"
    assert not refreshed.guard(["news"])(), "Only 15 minutes elapsed; refreshing a token cannot end the cooldown"
    clock.tick += 300
    clock.wall += 300
    assert refreshed.guard(["news"])(), "The unchanged deadline must expire after the original 20 minutes"


def test_bootstrap_verified_replacement_does_not_keep_prebootstrap_cached_policy(tmp_path):
    from main_logic.recommendation_feedback import RecommendationStore
    from tests.unit.test_recommendation_policy import OWNER, UID, _control, _event, _offer

    store = RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=2)
    previous_control = _control(incarnation="previous-account")
    store.apply_control(OWNER, previous_control)
    card = _offer(store, control=previous_control)
    store.record(OWNER, card["id"], _event(card))
    registry = PolicyRegistry()
    # During startup control_snapshot() is None until the runtime has an anchor.
    initial_context = store.get_policy_context(OWNER, UID)
    assert initial_context["account_incarnation"] == "previous-account"
    initial = registry.publish(store.path, OWNER, UID, initial_context["policy"],
                               incarnation=initial_context["account_incarnation"])
    assert not initial.guard(["news"])()
    replacement = _control(incarnation="replacement-account", revision=2)
    store.apply_control(OWNER, replacement)
    replacement_context = store.get_policy_context(OWNER, UID, control=replacement)
    assert replacement_context == {"policy": {}, "account_incarnation": "replacement-account"}
    replacement_view = registry.publish(store.path, OWNER, UID, replacement_context["policy"],
                                        incarnation=replacement_context["account_incarnation"])
    assert replacement_view.guard(["news"])(), "The verified replacement account must not inherit retired refusals"
    assert not initial.guard(["music"])()


def test_read_context_hides_a_fresh_replacement_without_deleting_previous_projection(tmp_path):
    from main_logic.recommendation_feedback import RecommendationStore
    from tests.unit.test_recommendation_policy import OWNER, UID, _control, _event, _offer

    store = RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=2)
    previous = _control(incarnation="previous-account")
    store.apply_control(OWNER, previous)
    card = _offer(store, control=previous)
    saved = store.record(OWNER, card["id"], _event(card))
    assert saved["policy_account_incarnation"] == "previous-account"
    replacement = _control(incarnation="replacement-account", revision=2)
    assert store.get_policy_context(OWNER, UID, control=replacement) == {
        "policy": {}, "account_incarnation": "replacement-account",
    }
    assert store.get_policy_context(OWNER, UID) == {
        "policy": saved["policy"], "account_incarnation": "previous-account",
    }


def test_logout_then_same_account_login_retains_deadline_but_not_old_lease():
    clock = SimpleNamespace(wall=1_000_000.0, tick=100.0)
    registry = PolicyRegistry(wall_clock=lambda: clock.wall, clock=lambda: clock.tick)
    owner = {"key": "same-account"}
    policy = {"news": {"revision": 1, "decline_count": 1.0,
                       "cooldown_until": clock.wall + 1200, "updated_at": clock.wall}}
    old = registry.publish("synthetic-db", owner, "a" * 32, policy, incarnation="account-instance")
    registry.publish("synthetic-db", {"key": "anonymous"}, "a" * 32, {})
    clock.tick += 900
    clock.wall -= 3600
    current = registry.publish("synthetic-db", owner, "a" * 32, policy, incarnation="account-instance")
    assert not old.guard(["music"])()
    assert not current.guard(["news"])()
    clock.tick += 300
    assert current.guard(["news"])()


def test_late_previous_incarnation_cannot_restore_a_replacement_cache():
    registry = PolicyRegistry()
    owner = {"key": "same-account"}
    original = registry.observe("synthetic-db", owner, incarnation="original")
    old = registry.publish("synthetic-db", owner, "a" * 32, {}, identity=original)
    current = registry.publish("synthetic-db", owner, "a" * 32, {}, incarnation="replacement")
    registry.publish("synthetic-db", owner, "a" * 32, {}, identity=original)
    assert current.guard(["news"])()
    assert not old.guard(["news"])()

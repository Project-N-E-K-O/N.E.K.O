"""api.call authorization whitelist must not block on plugin @ui.context providers.

actions derive entirely from @ui.action decorator metadata, without running plugin code. Missing
providers or errors previously failed whole UI context queries, depriving frontend of actions;
every panel button then returned unrelated 500 errors.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from plugin.core import host as host_module
from plugin.core.host import PluginProcessHost


async def _run_fixture(tmp_path, plugin_id: str, class_name: str, *, timeout: float = 10.0) -> dict:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nname='ui_context_fixture'\n", encoding="utf-8")

    host = PluginProcessHost(
        plugin_id=plugin_id,
        entry_point=f"tests.fixtures.plugin_test_ui_context_fixture:{class_name}",
        config_path=config_path,
    )
    try:
        await host.start(message_target_queue=asyncio.Queue())
        return await host.get_ui_context("main", timeout=timeout)
    finally:
        await host.shutdown(timeout=2.0)


def _action_ids(result: dict) -> list[str]:
    return [str(item.get("id")) for item in result.get("actions") or []]


@pytest.mark.asyncio
async def test_actions_survive_a_missing_context_provider(tmp_path) -> None:
    result = await _run_fixture(tmp_path, "ui_ctx_missing", "MissingUiContextFixturePlugin")

    assert _action_ids(result) == ["ping"]
    assert "not found" in str(result.get("context_error"))


@pytest.mark.asyncio
async def test_actions_survive_a_throwing_context_provider(tmp_path) -> None:
    result = await _run_fixture(tmp_path, "ui_ctx_failing", "FailingUiContextFixturePlugin")

    assert _action_ids(result) == ["ping"]
    assert "provider exploded" in str(result.get("context_error"))
    # Provider failures leave state empty, never publish partial results as real state.
    assert result.get("state") == {}


@pytest.mark.asyncio
async def test_healthy_provider_still_returns_state_without_error(tmp_path) -> None:
    result = await _run_fixture(tmp_path, "ui_ctx_healthy", "HealthyUiContextFixturePlugin")

    assert _action_ids(result) == ["ping"]
    assert result.get("state") == {"greeting": "hi"}
    assert result.get("context_error") is None


@pytest.mark.asyncio
async def test_actions_survive_a_hanging_context_provider(tmp_path) -> None:
    """Child process must stop before caller timeout, or fallback results cannot arrive."""
    result = await _run_fixture(
        tmp_path, "ui_ctx_hanging", "HangingUiContextFixturePlugin", timeout=2.0,
    )

    assert _action_ids(result) == ["ping"]
    assert "timed out" in str(result.get("context_error"))
    assert result.get("state") == {}


@pytest.mark.asyncio
async def test_mapping_valued_action_metadata_survives_serialization(tmp_path) -> None:
    """confirm is declared Mapping; non-dict mappings must not be stringified."""
    result = await _run_fixture(
        tmp_path, "ui_ctx_mapping_confirm", "MappingConfirmUiContextFixturePlugin",
    )

    actions = result.get("actions") or []
    assert len(actions) == 1
    assert actions[0].get("confirm") == {"title": "sure?"}


@pytest.mark.asyncio
async def test_state_survives_a_model_dump_without_a_mode_keyword(tmp_path) -> None:
    """model_dump uses duck typing; handwritten implementations may reject mode keyword."""
    result = await _run_fixture(
        tmp_path, "ui_ctx_bare_dump", "BareModelDumpUiContextFixturePlugin",
    )

    assert result.get("state") == {"greeting": "hi"}
    assert result.get("context_error") is None


@pytest.mark.asyncio
async def test_actions_survive_a_provider_that_leaks_cancelled_error(tmp_path) -> None:
    """CancelledError inherits BaseException, bypassing except Exception while success=False is returned."""
    result = await _run_fixture(tmp_path, "ui_ctx_cancelling", "CancellingUiContextFixturePlugin")

    assert _action_ids(result) == ["ping"]
    assert str(result.get("context_error"))
    assert result.get("state") == {}


@pytest.mark.asyncio
async def test_actions_survive_state_that_cannot_cross_the_process_boundary(tmp_path) -> None:
    """Replies pass pickle before HTTP; unserializable state must not sacrifice actions too."""
    result = await _run_fixture(
        tmp_path, "ui_ctx_unserializable", "UnserializableUiContextFixturePlugin",
    )

    assert _action_ids(result) == ["ping"]
    # Crucial: flattened state must arrive directly, not via finally's fallback packet stripping state,
    # which produces {}; distinguish both paths.
    state = result.get("state") or {}
    assert set(state) == {"lock", "tags"}
    assert all(isinstance(value, str) for value in state.values())


@pytest.mark.asyncio
async def test_actions_survive_a_provider_that_swallows_cancellation(tmp_path) -> None:
    """Providers swallowing cancellation must not defeat timeouts.

    After timeout asyncio.wait_for cancels then waits for completion; swallowing
    CancelledError makes budgets ineffective, letting parent timeout before fallback returns.
    """
    result = await _run_fixture(
        tmp_path, "ui_ctx_uncancellable", "UncancellableUiContextFixturePlugin", timeout=3.0,
    )

    assert _action_ids(result) == ["ping"]
    assert "timed out" in str(result.get("context_error"))
    assert result.get("state") == {}


@pytest.mark.parametrize(
    "budget",
    [0.05, 0.1, 0.2, 0.5, 1.0, 1.25, 1.5, 2.0, 5.0, 30.0, 1e16, 1e300],
)
def test_provider_budget_is_always_shorter_than_the_caller_budget(budget: float) -> None:
    """Invariant: child provider budget is strictly shorter than caller budget, even for tiny budgets.

    Subtracting fixed margin lets a floor exceed caller budget when it is smaller than margin,
    preventing fallback delivery. Assert the invariant itself, not one numerical example.
    """
    resolved = host_module._ui_context_provider_budget(budget)

    # Even enormous budgets where subtraction rounds away must comply, or strictly-shorter has no meaning.
    assert 0 < resolved < budget


@pytest.mark.parametrize(
    "requested",
    [
        None,
        "not-a-number",
        0,
        -1.0,
        float("nan"),
        float("inf"),
        float("-inf"),
        10 ** 309,
        # Budgets shorter than one IPC round trip: proportional multiplication underflows to zero, violating positivity.
        math.nextafter(0.0, 1.0),
        1e-300,
        1e-9,
    ],
)
def test_provider_budget_falls_back_below_the_default(requested: object) -> None:
    """Missing caller budget falls back to default, still reserving return-trip margin."""
    resolved = host_module._ui_context_provider_budget(requested)

    # nan bypasses comparisons, inf minus margin stays inf; neither can be a deadline for
    # asyncio.wait_for. Fall back to default budget for both.
    assert math.isfinite(resolved)
    assert 0 < resolved < host_module._UI_CONTEXT_DEFAULT_BUDGET


@pytest.mark.asyncio
async def test_concurrent_card_helpers_use_invocation_target_in_both_context_apis(tmp_path):
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nname='scoped_cards'\n", encoding="utf-8")
    host = PluginProcessHost(
        plugin_id="scoped_cards",
        entry_point="tests.fixtures.plugin_test_ui_context_fixture:ScopedCardTargetFixturePlugin",
        config_path=config_path,
    )
    try:
        await host.start(message_target_queue=asyncio.Queue())
        alice, bob = await asyncio.gather(
            host.trigger("emit", {"label": "first", "wait": True, "_ctx": {"lanlan_name": "Alice"}}, timeout=10),
            host.trigger("emit", {"label": "second", "peer": True, "_ctx": {"lanlan_name": "Bob"}}, timeout=10),
        )
        assert alice == (["Alice"] * 4 + ["Explicit"]) * 2
        assert bob == (["Bob"] * 4 + ["Explicit"]) * 2
        no_target = await host.trigger("emit", {"label": "untargeted"}, timeout=10)
        assert no_target == ([None] * 4 + ["Explicit"]) * 2
        custom = await host.trigger_custom_event("test", "emit_custom", {
            "label": "custom", "_ctx": {"lanlan_name": "Carol"},
        }, timeout=10)
        assert custom == (["Carol"] * 4 + ["Explicit"]) * 2
        no_custom_target = await host.trigger_custom_event("test", "emit_custom", {"label": "custom-untargeted"}, timeout=10)
        assert no_custom_target == ([None] * 4 + ["Explicit"]) * 2
    finally:
        await host.shutdown(timeout=2)

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import textwrap

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.server.application.plugin_cli import service as plugin_cli_service
from plugin.server.application.plugins import operation_lock
from plugin.server.application.plugins.installation_transactions import (
    replace as replacement_transaction,
)
from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit


def _replacement_call_keywords(function: object) -> set[str | None]:
    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == "replace_plugin"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace_plugin"
        )
    ]
    assert len(calls) == 1
    return {keyword.arg for keyword in calls[0].keywords}


def test_replacement_callers_share_one_narrow_typed_transaction() -> None:
    assert (
        plugin_cli_service.replacement_transaction.replace_plugin
        is replacement_transaction.replace_plugin
    )
    assert market_bridge.replace_plugin is replacement_transaction.replace_plugin

    transaction_parameters = inspect.signature(
        replacement_transaction.replace_plugin
    ).parameters
    assert set(transaction_parameters) == {
        "layout",
        "install_new",
        "additional_targets",
        "preserve_targets",
        "initialize_runtime_config",
        "validate_backup",
        "validate_channel_specific",
        "on_rollback_start",
    }

    cli_keywords = _replacement_call_keywords(plugin_cli_service.PluginCliService.install)
    market_keywords = _replacement_call_keywords(
        market_bridge._replace_market_plugin_transaction.__wrapped__
    )
    constant_dependencies = {"is_running", "stop", "start", "cleanup_backup"}
    assert cli_keywords.isdisjoint(constant_dependencies)
    assert market_keywords.isdisjoint(constant_dependencies)
    assert None not in market_keywords
    assert "replace_kwargs" not in inspect.signature(
        market_bridge._replace_market_plugin_transaction
    ).parameters


@pytest.mark.asyncio
async def test_market_channel_validation_runs_inside_operation_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    entry = market_bridge.LockEntry(
        root_id="user",
        directory_name="demo",
        plugin_id="demo",
        package_id="demo",
        channel="market",
        reason="user_requested",
        installed_at="2026-08-31T00:00:00.000000Z",
        updated_at="2026-08-31T00:00:00.000000Z",
        last_seen_at="2026-08-31T00:00:00.000000Z",
    )
    manager = type(
        "Manager",
        (),
        {"find_active_user_entry": lambda self, _plugin_id: entry},
    )()

    async def not_running(_plugin_id: str) -> bool:
        return False

    monkeypatch.setattr(replacement_transaction, "_plugin_is_running", not_running)

    async def install_new() -> dict[str, object]:
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nid = "demo"\nversion = "2.0.0"\n',
            encoding="utf-8",
        )
        return {"installed": True}

    lock_states: list[bool] = []

    async def validate_channel_specific() -> None:
        lock_states.append(operation_lock._operation_lock_is_held_by_current_task())

    result = await market_bridge._replace_market_plugin_transaction(
        manager=manager,  # type: ignore[arg-type]
        expected_plugin_id="demo",
        original_entry=entry,
        original_entry_fingerprint=market_bridge._market_entry_fingerprint(entry),
        installed_package_id="demo",
        plugin_dir=plugin_dir,
        layout=resolve_plugin_layout(
            "demo",
            plugin_dir,
            storage_root=tmp_path / "runtime-data",
        ),
        install_new=install_new,
        validate_channel_specific=validate_channel_specific,
    )

    assert result.install_result == {"installed": True}
    assert lock_states == [True]

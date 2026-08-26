"""api.call 的授权白名单不能被插件自己的 @ui.context provider 卡住。

actions 完全由 @ui.action 的装饰器元数据推导，不跑插件代码。provider 缺失
或抛错时，宿主以前直接把整次 UI context 查询判失败，前端拿不到 actions，
面板上每个按钮都返回一条与真实原因无关的 500。
"""

from __future__ import annotations

import asyncio

import pytest

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
    # provider 失败时 state 必须留空，不能把半个结果当成真状态发出去。
    assert result.get("state") == {}


@pytest.mark.asyncio
async def test_healthy_provider_still_returns_state_without_error(tmp_path) -> None:
    result = await _run_fixture(tmp_path, "ui_ctx_healthy", "HealthyUiContextFixturePlugin")

    assert _action_ids(result) == ["ping"]
    assert result.get("state") == {"greeting": "hi"}
    assert result.get("context_error") is None


@pytest.mark.asyncio
async def test_actions_survive_a_hanging_context_provider(tmp_path) -> None:
    """子进程必须赶在调用方超时之前收手，否则降级结果根本送不出去。"""
    result = await _run_fixture(
        tmp_path, "ui_ctx_hanging", "HangingUiContextFixturePlugin", timeout=2.0,
    )

    assert _action_ids(result) == ["ping"]
    assert "timed out" in str(result.get("context_error"))
    assert result.get("state") == {}

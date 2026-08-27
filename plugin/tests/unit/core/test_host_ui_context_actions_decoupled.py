"""api.call 的授权白名单不能被插件自己的 @ui.context provider 卡住。

actions 完全由 @ui.action 的装饰器元数据推导，不跑插件代码。provider 缺失
或抛错时，宿主以前直接把整次 UI context 查询判失败，前端拿不到 actions，
面板上每个按钮都返回一条与真实原因无关的 500。
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


@pytest.mark.asyncio
async def test_mapping_valued_action_metadata_survives_serialization(tmp_path) -> None:
    """confirm 的声明类型是 Mapping，非 dict 的 Mapping 不能被拍成字符串。"""
    result = await _run_fixture(
        tmp_path, "ui_ctx_mapping_confirm", "MappingConfirmUiContextFixturePlugin",
    )

    actions = result.get("actions") or []
    assert len(actions) == 1
    assert actions[0].get("confirm") == {"title": "sure?"}


@pytest.mark.asyncio
async def test_state_survives_a_model_dump_without_a_mode_keyword(tmp_path) -> None:
    """model_dump 是鸭子类型认出来的，手写实现未必收 mode 关键字。"""
    result = await _run_fixture(
        tmp_path, "ui_ctx_bare_dump", "BareModelDumpUiContextFixturePlugin",
    )

    assert result.get("state") == {"greeting": "hi"}
    assert result.get("context_error") is None


@pytest.mark.asyncio
async def test_actions_survive_a_provider_that_leaks_cancelled_error(tmp_path) -> None:
    """CancelledError 继承 BaseException，会穿透 except Exception 把 success=False 送回去。"""
    result = await _run_fixture(tmp_path, "ui_ctx_cancelling", "CancellingUiContextFixturePlugin")

    assert _action_ids(result) == ["ping"]
    assert str(result.get("context_error"))
    assert result.get("state") == {}


@pytest.mark.asyncio
async def test_actions_survive_state_that_cannot_cross_the_process_boundary(tmp_path) -> None:
    """回包要过 pickle 再进 HTTP：state 不可序列化时不能把 actions 一起赔进去。"""
    result = await _run_fixture(
        tmp_path, "ui_ctx_unserializable", "UnserializableUiContextFixturePlugin",
    )

    assert _action_ids(result) == ["ping"]
    # 关键：state 必须是被压平后送达的，而不是靠 finally 里剥掉 state 的补发包
    # 兜住的——后者会让 state 变成 {}，两条路径必须能分辨。
    state = result.get("state") or {}
    assert set(state) == {"lock", "tags"}
    assert all(isinstance(value, str) for value in state.values())


@pytest.mark.asyncio
async def test_actions_survive_a_provider_that_swallows_cancellation(tmp_path) -> None:
    """吞掉取消信号的 provider 不能把超时拖成空转。

    asyncio.wait_for 超时后会 cancel 再 **等** 那个协程结束，provider 只要吞掉
    CancelledError，设定的预算就形同虚设，父进程照样先超时、降级结果送不出去。
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
    """不变量：子进程的 provider 预算永远严格短于调用方，短预算也不例外。

    只扣一个固定余量在 budget 小于余量时会被下限反超——那正是降级结果送不
    出去的原因，所以这里断的是不变量本身，不是某组具体数字。
    """
    resolved = host_module._ui_context_provider_budget(budget)

    # 大到让减法被舍入吃掉的预算也要满足，否则「严格更短」只是句空话。
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
        # 比一次 IPC 往返还短的预算：份额乘法会下溢成 0，正数不变量就没了。
        math.nextafter(0.0, 1.0),
        1e-300,
        1e-9,
    ],
)
def test_provider_budget_falls_back_below_the_default(requested: object) -> None:
    """拿不到调用方预算时回落到默认值，且同样要留出回程余量。"""
    resolved = host_module._ui_context_provider_budget(requested)

    # nan 能溜过所有比较、inf 减掉余量还是 inf，两者都不能当 deadline 交给
    # asyncio.wait_for，必须一起落到默认预算上。
    assert math.isfinite(resolved)
    assert 0 < resolved < host_module._UI_CONTEXT_DEFAULT_BUDGET

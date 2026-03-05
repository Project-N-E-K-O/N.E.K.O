from __future__ import annotations

import asyncio

import pytest

from plugin.sdk.decorators import hook
from plugin.sdk.hook_executor import HookExecutorMixin


class _Executor(HookExecutorMixin):
    def __init__(self) -> None:
        self._logger = None
        self.__init_hook_executor__()

    def _get_hook_logger(self):
        return self._logger

    def _get_hook_owner_name(self) -> str:
        return "Exec"

    @hook("entry", "before", priority=2)
    async def before_modify(self, entry_id: str, params: dict[str, object], **kwargs):
        out = dict(params)
        out["x"] = 2
        return out

    @hook("entry", "after", priority=1)
    async def after_modify(self, entry_id: str, params: dict[str, object], result: dict[str, object], **kwargs):
        out = dict(result)
        out["after"] = True
        return out

    @hook("entry", "around", priority=1)
    async def around_wrap(self, entry_id: str, params: dict[str, object], next_handler, **kwargs):
        result = await next_handler(params)
        out = dict(result)
        out["around"] = True
        return out

    @hook("entry_replace", "replace", priority=1)
    async def replace_it(self, entry_id: str, params: dict[str, object], original_handler, **kwargs):
        return {"replaced": True, "entry_id": entry_id}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_hook_executor_collect_and_execute_chain() -> None:
    ex = _Executor()
    ex.collect_hooks()

    hooks = ex.get_hooks_for_entry("entry")
    assert len(hooks) >= 3

    go, early, params = await ex.execute_before_hooks("entry", {"x": 1})
    assert go is True
    assert early is None
    assert params["x"] == 2

    after = await ex.execute_after_hooks("entry", params, {"ok": True})
    assert after["after"] is True

    wrapped = ex._wrap_handler_with_hooks("entry", lambda **kwargs: {"ok": True, "x": kwargs.get("x")})
    out = await wrapped(x=1)
    assert out["ok"] is True
    assert out["around"] is True
    assert out["after"] is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_hook_executor_replace_hook() -> None:
    ex = _Executor()
    ex.collect_hooks()

    replace = ex.get_replace_hook("entry_replace", {"x": 1})
    assert replace is not None

    wrapped = ex._wrap_handler_with_hooks("entry_replace", lambda **kwargs: {"orig": True})
    out = await wrapped(x=1)
    assert out["replaced"] is True


@pytest.mark.plugin_unit
def test_hook_executor_sync_handler_call() -> None:
    ex = _Executor()

    async def _run() -> dict[str, object]:
        return await ex._call_handler(lambda **kwargs: {"x": kwargs["x"]}, {"x": 7}, is_async=False)

    out = asyncio.run(_run())
    assert out["x"] == 7

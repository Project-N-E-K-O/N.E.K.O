from __future__ import annotations

from types import MethodType

import pytest

from brain.task_executor import DirectTaskExecutor
from brain import task_executor as task_executor_module


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeAsyncClient:
    last_post_json: dict[str, object] | None = None

    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, json: dict[str, object]) -> _FakeResponse:
        self.__class__.last_post_json = json
        return _FakeResponse(
            {
                "run_id": "run-1",
                "run_token": "token-1",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )


class _AlwaysFailGetClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> "_AlwaysFailGetClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str):
        raise RuntimeError("connection lost")


@pytest.mark.asyncio
async def test_execute_user_plugin_treats_entry_timeout_zero_as_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = [{"id": "nekoclaw", "entries": [{"id": "run", "timeout": 0}]}]
    _FakeAsyncClient.last_post_json = None

    observed: dict[str, object] = {}

    async def _fake_await_run_completion(
        self,
        run_id: str,
        *,
        timeout: float | None = 300.0,
        on_progress=None,
        **_: object,
    ) -> dict[str, object]:
        observed["run_id"] = run_id
        observed["timeout"] = timeout
        return {"status": "succeeded", "success": True, "data": {"ok": True}}

    monkeypatch.setattr(task_executor_module.httpx, "AsyncClient", _FakeAsyncClient)
    executor._await_run_completion = MethodType(_fake_await_run_completion, executor)

    result = await executor._execute_user_plugin(
        "task-1",
        plugin_id="nekoclaw",
        plugin_args={},
        entry_id="run",
    )

    assert result.success is True
    assert observed["run_id"] == "run-1"
    assert observed["timeout"] is None
    assert _FakeAsyncClient.last_post_json is not None
    assert _FakeAsyncClient.last_post_json["args"]["_ctx"]["entry_timeout"] is None


@pytest.mark.asyncio
async def test_execute_user_plugin_honors_ctx_entry_timeout_zero_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = [{"id": "nekoclaw", "entries": [{"id": "run", "timeout": 120}]}]
    _FakeAsyncClient.last_post_json = None

    observed: dict[str, object] = {}

    async def _fake_await_run_completion(
        self,
        run_id: str,
        *,
        timeout: float | None = 300.0,
        on_progress=None,
        **_: object,
    ) -> dict[str, object]:
        observed["timeout"] = timeout
        return {"status": "succeeded", "success": True, "data": {"ok": True}}

    monkeypatch.setattr(task_executor_module.httpx, "AsyncClient", _FakeAsyncClient)
    executor._await_run_completion = MethodType(_fake_await_run_completion, executor)

    result = await executor._execute_user_plugin(
        "task-2",
        plugin_id="nekoclaw",
        plugin_args={"_ctx": {"entry_timeout": 0}},
        entry_id="run",
    )

    assert result.success is True
    assert observed["timeout"] is None
    assert _FakeAsyncClient.last_post_json is not None
    assert _FakeAsyncClient.last_post_json["args"]["_ctx"]["entry_timeout"] is None


@pytest.mark.asyncio
async def test_await_run_completion_stops_after_consecutive_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    monkeypatch.setattr(task_executor_module.httpx, "AsyncClient", _AlwaysFailGetClient)

    result = await executor._await_run_completion("run-err", timeout=None, poll_interval=0)

    assert result["status"] == "failed"
    assert result["success"] is False
    assert "consecutive transport errors" in result["error"]

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
    last_timeout: object | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.__class__.last_timeout = kwargs.get("timeout")
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
        response = _FakeResponse({"status": "running"})
        response.status_code = 503
        response.text = "service unavailable"
        return response


class _RunCompletionClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> "_RunCompletionClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, params: dict[str, object] | None = None) -> _FakeResponse:
        if url.endswith("/export"):
            return _FakeResponse({
                "items": [
                    {"type": "json", "json": {"data": {"ok": True}, "meta": {"source": "primary"}}},
                    {"type": "binary_url", "binary_url": "https://example.test/image.png", "mime": "IMAGE/PNG"},
                    {"type": "text", "text": "supplement", "description": "extra"},
                ],
            })
        return _FakeResponse({"status": "succeeded", "progress": 1.0, "stage": "done", "message": "ok"})


@pytest.mark.asyncio
async def test_execute_user_plugin_treats_entry_timeout_zero_as_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = [{"id": "dummy_plugin", "entries": [{"id": "run", "timeout": 0}]}]
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
        plugin_id="dummy_plugin",
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
    executor.plugin_list = [{"id": "dummy_plugin", "entries": [{"id": "run", "timeout": 120}]}]
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
        plugin_id="dummy_plugin",
        plugin_args={"_ctx": {"entry_timeout": 0}},
        entry_id="run",
    )

    assert result.success is True
    assert observed["timeout"] is None
    assert _FakeAsyncClient.last_post_json is not None
    assert _FakeAsyncClient.last_post_json["args"]["_ctx"]["entry_timeout"] is None


@pytest.mark.asyncio
async def test_execute_user_plugin_forwards_lang_and_attachment_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = [{"id": "dummy_plugin", "entries": [{"id": "run", "timeout": 30}]}]
    _FakeAsyncClient.last_post_json = None
    _FakeAsyncClient.last_timeout = None

    async def _fake_await_run_completion(
        self,
        run_id: str,
        *,
        timeout: float | None = 300.0,
        on_progress=None,
        **_: object,
    ) -> dict[str, object]:
        return {"status": "succeeded", "success": True, "data": {"ok": True}}

    monkeypatch.setattr(task_executor_module.httpx, "AsyncClient", _FakeAsyncClient)
    executor._await_run_completion = MethodType(_fake_await_run_completion, executor)

    attachment_url = "data:image/png;base64," + ("a" * (1024 * 1024))
    result = await executor._execute_user_plugin(
        "task-attachment",
        plugin_id="dummy_plugin",
        plugin_args={"_attachments": [{"type": "image_url", "url": attachment_url}]},
        entry_id="run",
        lang="zh-CN",
        latest_user_request="看这张图",
    )

    assert result.success is True
    assert _FakeAsyncClient.last_post_json is not None
    args = _FakeAsyncClient.last_post_json["args"]
    assert args["_attachments"][0]["url"] == attachment_url
    assert args["_ctx"]["lang"] == "zh-CN"
    assert args["_ctx"]["latest_user_request"] == "看这张图"
    assert getattr(_FakeAsyncClient.last_timeout, "read", 0) > 10.0


@pytest.mark.asyncio
async def test_await_run_completion_stops_after_consecutive_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    monkeypatch.setattr(task_executor_module.httpx, "AsyncClient", _AlwaysFailGetClient)

    result = await executor._await_run_completion("run-err", timeout=None, poll_interval=0)

    assert result["status"] == "failed"
    assert result["success"] is False
    assert "consecutive" in result["error"]


@pytest.mark.asyncio
async def test_await_run_completion_collects_media_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object.__new__(DirectTaskExecutor)
    monkeypatch.setattr(task_executor_module.httpx, "AsyncClient", _RunCompletionClient)

    result = await executor._await_run_completion("run-media", timeout=None, poll_interval=0)

    assert result["success"] is True
    assert result["data"] == {"ok": True}
    assert result["meta"] == {"source": "primary"}
    assert result["media"] == [
        {
            "type": "binary_url",
            "mime": "image/png",
            "description": None,
            "metadata": None,
            "binary_url": "https://example.test/image.png",
        },
        {
            "type": "text",
            "text": "supplement",
            "description": "extra",
            "metadata": None,
        },
    ]

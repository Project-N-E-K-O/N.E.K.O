import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from plugin.sdk.shared.contracts.memory import MemorySubject, MemoryTarget
from plugin.sdk.shared.memory.client import MemoryClient
from plugin.sdk.shared.models.result import Ok, Err
from plugin.server.application import memory as adapter
from plugin.server.domain.errors import ServerDomainError


class Context:
    def __init__(self):
        self.deadline = time.monotonic() + 5

    def remaining(self):
        return max(0, self.deadline - time.monotonic())


@pytest.mark.asyncio
async def test_slow_memory_admission_is_bounded_and_cancellation_releases_capacity(monkeypatch):
    entered = asyncio.Event()
    replacement_entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
        elif calls == 3:
            replacement_entered.set()
        await release.wait()
        return httpx.Response(200, json={"sections": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(adapter, "get_internal_http_client", lambda: http)
    monkeypatch.setattr(adapter, "_MAX_MEMORY_HTTP_REQUESTS", 2)
    request = {"target": MemoryTarget.private("YUI").to_wire()}
    tasks = [asyncio.create_task(adapter.get_persona(Context(), request)) for _ in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(ServerDomainError) as full:
            await adapter.get_persona(Context(), request)
        assert full.value.code == "BUSY" and calls == 2
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        tasks.append(asyncio.create_task(adapter.get_persona(Context(), request)))
        await asyncio.wait_for(replacement_entered.wait(), 1)
        release.set()
        assert all(result["sections"] == [] for result in await asyncio.gather(*tasks[1:]))
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await http.aclose()
    assert adapter._active_memory_requests == 0


def test_subject_wire_matches_native_and_rejects_empty_scope():
    from memory.scopes import MemorySubject as Native

    for constructor, args in [
        ("group_chat", ("qq:a%", "g:x")),
        ("participant", ("qq", "a:b%")),
        ("group_participant", ("qq", "g:x", "a%b")),
    ]:
        ours = getattr(MemorySubject, constructor)(*args)
        native = getattr(Native, constructor)(*args)
        assert ours.to_wire() == native.as_entry_fields()
    with pytest.raises(ValueError):
        MemoryTarget.scoped("YUI", [])
    with pytest.raises(ValueError):
        MemorySubject.create("group_chat", "qq:g", scope="")


@pytest.mark.asyncio
async def test_search_and_persona_through_http_and_client(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sections": [
                        {
                            "entity": "future",
                            "entries": [
                                {
                                    "id": None,
                                    "text": "tea",
                                    "source": "manual",
                                    "protected": True,
                                    "suppressed": True,
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "text": "tea",
                        "tier": "future",
                        "scope": "legacy_private",
                        "score": None,
                    }
                ],
                "query": "",
                "candidates_total": 1,
                "elapsed_ms": 1.0,
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(adapter, "get_internal_http_client", lambda: http)

    class Sender:
        async def _send_request_and_wait_async(
            self, *, request_type, request_data, **kwargs
        ):
            return await (
                adapter.search(Context(), request_data)
                if request_type == "MEMORY_SEARCH"
                else adapter.get_persona(Context(), request_data)
            )

    client = MemoryClient(Sender())
    target = MemoryTarget.private("YUI")
    result = await client.search(target=target, time="2026-10", budget_ms=100)
    assert isinstance(result, Ok) and result.value.items[0].subject is None
    assert result.value.items[0].id is None and result.value.items[0].kind == "future"
    assert json.loads(requests[0].content) == {"time": "2026-10", "budget_ms": 100}
    result = await client.get_persona(target=target)
    assert isinstance(result, Ok) and result.value.sections[0].entries[0].suppressed
    assert requests[1].url.path == "/internal/memory/YUI/persona"
    await http.aclose()


@pytest.mark.asyncio
async def test_scoped_search_wire_and_business_error(monkeypatch):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"error_code": "hybrid_recall_failed", "results": []}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(adapter, "get_internal_http_client", lambda: http)
    subject = MemorySubject.group_chat("qq", "x:y")
    with pytest.raises(ServerDomainError) as error:
        await adapter.search(
            Context(),
            {"target": MemoryTarget.scoped("YUI", [subject]).to_wire(), "query": "tea"},
        )
    assert error.value.details["upstream_code"] == "hybrid_recall_failed"
    assert captured[0]["subjects"] == [subject.to_wire()]
    await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [False, 0, ""])
async def test_malformed_business_error_is_not_successful_empty_memory(monkeypatch, code):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"error_code": code, "results": []})
    ))
    monkeypatch.setattr(adapter, "get_internal_http_client", lambda: client)
    try:
        with pytest.raises(ServerDomainError) as failure:
            await adapter.search(Context(), {
                "target": MemoryTarget.private("YUI").to_wire(), "query": "tea",
            })
        assert failure.value.code == "MEMORY_PROTOCOL_ERROR"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invalid_response_and_remote_errors(monkeypatch):
    async def fetch(*args, **kwargs):
        return {
            "results": [
                {
                    "text": "x",
                    "tier": "fact",
                    "subject_kind": "group_chat",
                    "scope": "legacy_private",
                }
            ],
            "query": "x",
            "candidates_total": 1,
            "elapsed_ms": 0,
        }

    monkeypatch.setattr(adapter, "_fetch", fetch)
    with pytest.raises(ServerDomainError) as error:
        await adapter.search(
            Context(), {"target": MemoryTarget.private("YUI").to_wire(), "query": "x"}
        )
    assert error.value.code == "MEMORY_PROTOCOL_ERROR"

    class RemoteError(Exception):
        code = "UNKNOWN_OPERATION"
        details = {"x": 1}

    class Sender:
        async def _send_request_and_wait_async(self, **kwargs):
            raise RemoteError("missing")

    client = MemoryClient(Sender())
    result = await client.get_persona(target=MemoryTarget.private("YUI"))
    assert (
        isinstance(result, Err)
        and result.error.code == "CAPABILITY_UNAVAILABLE"
        and result.error.details == {"x": 1}
    )
    assert (
        await client.search(target=MemoryTarget.private("YUI"))
    ).error.code == "INVALID_ARGUMENT"
    assert (
        await client.get_persona(
            target=MemoryTarget.scoped("YUI", [MemorySubject.group_chat("qq", "x")])
        )
    ).error.code == "CAPABILITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_http_status_and_oversize(monkeypatch):
    for status, payload, code in [
        (409, {"detail": "locked"}, "MEMORY_STORAGE_RESTRICTED"),
        (503, {"detail": "not ready"}, "MEMORY_NOT_READY"),
        (404, {"detail": "Not Found"}, "CAPABILITY_UNAVAILABLE"),
        (
            413,
            {"detail": {"code": "MEMORY_RESPONSE_TOO_LARGE"}},
            "MEMORY_RESPONSE_TOO_LARGE",
        ),
    ]:
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=payload)
            )
        )
        monkeypatch.setattr(adapter, "get_internal_http_client", lambda: http)
        with pytest.raises(ServerDomainError) as error:
            await adapter.get_persona(
                Context(), {"target": MemoryTarget.private("YUI").to_wire()}
            )
        assert error.value.code == code
        await http.aclose()


@pytest.mark.asyncio
async def test_timeout_cancellation_and_oversize(monkeypatch):
    cancelled = asyncio.Event()

    class Sender:
        async def _send_request_and_wait_async(self, **kwargs):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()

    result = await MemoryClient(Sender()).search(
        target=MemoryTarget.private("YUI"), query="tea", timeout=0.01
    )
    assert isinstance(result, Err) and result.error.code == "MEMORY_TIMEOUT"
    assert cancelled.is_set()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"x" * (adapter.MAX_WIRE_BYTES + 1)
            )
        )
    )
    monkeypatch.setattr(adapter, "get_internal_http_client", lambda: http)
    with pytest.raises(ServerDomainError) as error:
        await adapter.get_persona(
            Context(), {"target": MemoryTarget.private("YUI").to_wire()}
        )
    assert error.value.code == "MEMORY_RESPONSE_TOO_LARGE"
    await http.aclose()

@pytest.mark.asyncio
async def test_invalid_character_is_not_reported_as_missing_capability(monkeypatch):
    calls = []
    async def fetch(*args, **kwargs):
        calls.append(args)
        return {'sections': []}
    monkeypatch.setattr(adapter, '_fetch', fetch)
    for name in ('bad/name', 'bad\\name', 'x' * 51):
        with pytest.raises(ServerDomainError) as failure:
            await adapter.get_persona(Context(), {'target': MemoryTarget.private(name).to_wire()})
        assert failure.value.code == 'INVALID_ARGUMENT'
    assert calls == []


@pytest.mark.asyncio
async def test_invalid_character_status_is_invalid_argument(monkeypatch):
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={'detail': 'Invalid lanlan_name'})
    ))
    monkeypatch.setattr(adapter, 'get_internal_http_client', lambda: http)
    try:
        with pytest.raises(ServerDomainError) as failure:
            await adapter.get_persona(Context(), {'target': MemoryTarget.private('YUI').to_wire()})
        assert failure.value.code == 'INVALID_ARGUMENT'
    finally:
        await http.aclose()

# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Wire-snapshot tests for ``memory/scoped_client.py``.

Each fixture under ``fixtures/scoped_client_wire`` records one call (method,
character name, keyword arguments) and the exact request it must produce:
HTTP method, full URL and the body as a string, compared byte-for-byte.
Fixtures marked with ``qq_reference`` were recorded once against the
``b0b283e34`` QQ ``memory_bridge`` with the same inputs and matched it byte
for byte; the rest exercise fields that reference never sent.

Every POST fixture body must also parse with the memory_server request model
it targets, and must not carry keys that model does not declare (pydantic
ignores unknown keys, so a misspelled field would otherwise parse "fine").
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest

from memory.scoped_client import (
    SCOPED_WRITE_RETRY_DELAYS_S,
    ScopedMemoryClient,
    ScopedMemoryError,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scoped_client_wire"
FIXTURE_PATHS = sorted(FIXTURE_DIR.glob("*.json"))
BASE_URL = "http://127.0.0.1:48912"

#: Fields the server learns in a later change (visit PR-08). Until then the
#: request models ignore them, so the key check below allows exactly these.
_PENDING_SERVER_FIELDS = {"idempotency_key", "client_requested_at"}

_SUBJECT = {
    "subject_kind": "group_chat",
    "subject_id": "neko_visit:0123456789abcdef01234567",
}
_MESSAGES = [{"role": "user", "content": "hello"}]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ok_response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/scoped_context"):
        return httpx.Response(200, text="rendered")
    if path.endswith("/scoped_subjects"):
        return httpx.Response(200, json={"subjects": []})
    if path.endswith("/scoped_history"):
        body = json.loads(request.content)
        if "segments" in body:
            return httpx.Response(200, json={
                "status": "processed",
                "segments": [{"status": "ok"} for _ in body["segments"]],
            })
        return httpx.Response(200, json={"status": "processed"})
    if path.endswith("/scoped_forget"):
        return httpx.Response(200, json={"status": "forgotten"})
    return httpx.Response(200, json={"status": "recorded"})


class _Recorder:
    """MockTransport handler that records requests and replays responses."""

    def __init__(self, responder=_ok_response):
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


class _SleepSpy:
    """Injected ``sleep`` that records delays instead of waiting."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _client(recorder: _Recorder, sleep: _SleepSpy | None = None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    client = ScopedMemoryClient(
        base_url=BASE_URL, http=http, sleep=sleep or _SleepSpy(),
    )
    return client, http


def _request_models() -> dict:
    # 与 tests/unit 里既有先例一致（test_group_memory_scopes 等）：在用例内
    # 惰性 import 路由模块，只取 pydantic 请求模型，不启动任何 runtime。
    from app.memory_server.routes import (
        ScopedContextRequest,
        ScopedForgetRequest,
        ScopedHistoryRequest,
        ScopedHistorySegment,
        ScopedMentionsRequest,
    )

    return {
        "ScopedContextRequest": ScopedContextRequest,
        "ScopedMentionsRequest": ScopedMentionsRequest,
        "ScopedForgetRequest": ScopedForgetRequest,
        "ScopedHistoryRequest": ScopedHistoryRequest,
        "ScopedHistorySegment": ScopedHistorySegment,
    }


def test_fixture_set_covers_every_method_twice():
    """Each client method is pinned by at least two wire vectors."""
    counts: dict[str, int] = {}
    for path in FIXTURE_PATHS:
        method = _load(path)["call"]["method"]
        counts[method] = counts.get(method, 0) + 1
    expected = {
        "fetch_bootstrap", "post_mentions", "post_forget",
        "post_history", "post_history_batch", "list_scoped_subjects",
    }
    assert set(counts) == expected
    assert all(count >= 2 for count in counts.values()), counts


@pytest.mark.asyncio
@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.stem)
async def test_request_matches_wire_snapshot_bytes(path: Path):
    """The produced method, URL and body bytes equal the fixture exactly."""
    fixture = _load(path)
    call = fixture["call"]
    expected = fixture["request"]
    recorder = _Recorder()
    client, http = _client(recorder)
    async with http:
        await getattr(client, call["method"])(call["lanlan"], **call["kwargs"])
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == expected["method"]
    assert str(request.url) == expected["url"]
    expected_body = expected["body"]
    if expected_body is None:
        assert request.content == b""
    else:
        assert request.content == expected_body.encode("utf-8")
        assert request.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    "path",
    [p for p in FIXTURE_PATHS if _load(p)["model"] is not None],
    ids=lambda p: p.stem,
)
def test_fixture_body_parses_with_server_request_model(path: Path):
    """Fixture bodies validate against memory_server's request models."""
    fixture = _load(path)
    models = _request_models()
    model = models[fixture["model"]]
    body = fixture["request"]["body"]
    parsed = model.model_validate_json(body)
    raw = json.loads(body)

    allowed = set(model.model_fields)
    if fixture["model"] == "ScopedHistoryRequest":
        allowed |= _PENDING_SERVER_FIELDS
    assert set(raw) <= allowed, set(raw) - allowed

    if fixture["model"] == "ScopedHistoryRequest":
        kwargs = fixture["call"]["kwargs"]
        if "segments" in raw:
            segment_fields = set(models["ScopedHistorySegment"].model_fields)
            for wire, given in zip(raw["segments"], kwargs["segments"]):
                assert set(wire) <= segment_fields, set(wire) - segment_fields
                assert json.loads(wire["input_history"]) == given["messages"]
            assert len(parsed.segments) == len(kwargs["segments"])
        else:
            assert json.loads(raw["input_history"]) == kwargs["messages"]
            assert parsed.subject is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post_history", "post_history_batch"])
async def test_retry_identity_fields_absent_when_none(method: str):
    """``None`` idempotency fields never appear in the body."""
    recorder = _Recorder()
    client, http = _client(recorder)
    kwargs = (
        {"subject": _SUBJECT, "messages": _MESSAGES}
        if method == "post_history"
        else {"segments": [{
            "messages": _MESSAGES, "subject": _SUBJECT, "speaker_label": "A",
        }]}
    )
    async with http:
        assert await getattr(client, method)(
            "Lanlan", idempotency_key=None, client_requested_at=None, **kwargs,
        )
    body = json.loads(recorder.requests[0].content)
    assert "idempotency_key" not in body
    assert "client_requested_at" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post_history", "post_history_batch"])
async def test_retry_identity_fields_sent_verbatim(method: str):
    """Non-``None`` idempotency fields are sent unchanged."""
    recorder = _Recorder()
    client, http = _client(recorder)
    kwargs = (
        {"subject": _SUBJECT, "messages": _MESSAGES}
        if method == "post_history"
        else {"segments": [{
            "messages": _MESSAGES, "subject": _SUBJECT, "speaker_label": "A",
        }]}
    )
    async with http:
        assert await getattr(client, method)(
            "Lanlan",
            idempotency_key="visit-digest:abc:0:group:1",
            client_requested_at=1790000000.125,
            **kwargs,
        )
    body = json.loads(recorder.requests[0].content)
    assert body["idempotency_key"] == "visit-digest:abc:0:group:1"
    assert body["client_requested_at"] == 1790000000.125


_WRITE_CALLS = {
    "post_mentions": {"subjects": [_SUBJECT], "response_text": "hi"},
    "post_forget": {"subject": _SUBJECT},
    "post_history": {"subject": _SUBJECT, "messages": _MESSAGES},
    "post_history_batch": {"segments": [{
        "messages": _MESSAGES, "subject": _SUBJECT, "speaker_label": "A",
    }]},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(_WRITE_CALLS))
async def test_write_retries_502_on_5_15_45_then_gives_up(method: str):
    """A persistent 502 is retried exactly three times, then reported False."""
    assert SCOPED_WRITE_RETRY_DELAYS_S == (5.0, 15.0, 45.0)
    recorder = _Recorder(lambda request: httpx.Response(502))
    sleep = _SleepSpy()
    client, http = _client(recorder, sleep)
    async with http:
        ok = await getattr(client, method)("Lanlan", **_WRITE_CALLS[method])
    assert bool(ok) is False   # batch 返回 ScopedBatchResult，其余返回 bool
    assert len(recorder.requests) == 4
    assert sleep.delays == [5.0, 15.0, 45.0]
    bodies = {request.content for request in recorder.requests}
    assert len(bodies) == 1, "a retry must resend the identical body"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(_WRITE_CALLS))
async def test_write_recovers_after_transient_502(method: str):
    """Two 502s followed by success: three requests, two sleeps, True."""
    statuses = [502, 502]

    def responder(request):
        if statuses:
            return httpx.Response(statuses.pop(0))
        return _ok_response(request)

    recorder = _Recorder(responder)
    sleep = _SleepSpy()
    client, http = _client(recorder, sleep)
    async with http:
        ok = await getattr(client, method)("Lanlan", **_WRITE_CALLS[method])
    assert bool(ok) is True
    assert len(recorder.requests) == 3
    assert sleep.delays == [5.0, 15.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 422, 500, 503])
async def test_write_does_not_retry_other_errors(status: int):
    """Only 502 is retried; any other error fails on the first answer."""
    recorder = _Recorder(lambda request: httpx.Response(status))
    sleep = _SleepSpy()
    client, http = _client(recorder, sleep)
    async with http:
        ok = await client.post_history(
            "Lanlan", subject=_SUBJECT, messages=_MESSAGES,
        )
    assert ok is False
    assert len(recorder.requests) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_write_transport_error_reports_false():
    """A refused connection is a failed write, not an exception."""
    def responder(request):
        raise httpx.ConnectError("refused", request=request)

    client, http = _client(_Recorder(responder))
    async with http:
        assert await client.post_forget("Lanlan", subject=_SUBJECT) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method,kwargs", [
    ("fetch_bootstrap", {"subjects": [_SUBJECT], "lang": "en", "max_tokens": 100}),
    ("list_scoped_subjects", {"platform": "neko_visit"}),
])
async def test_reads_fail_fast_on_502(method: str, kwargs: dict):
    """Reads raise on 502 without the write back-off."""
    recorder = _Recorder(lambda request: httpx.Response(502))
    sleep = _SleepSpy()
    client, http = _client(recorder, sleep)
    async with http:
        with pytest.raises(ScopedMemoryError):
            await getattr(client, method)("Lanlan", **kwargs)
    assert len(recorder.requests) == 1
    assert sleep.delays == []


def test_include_legacy_private_defaults_to_false():
    """The bootstrap signature defaults ``include_legacy_private`` to False."""
    parameter = inspect.signature(
        ScopedMemoryClient.fetch_bootstrap,
    ).parameters["include_legacy_private"]
    assert parameter.default is False


@pytest.mark.asyncio
async def test_include_legacy_private_true_is_rejected_without_a_request():
    """Asking the scoped endpoint for legacy private memory is an error."""
    recorder = _Recorder()
    client, http = _client(recorder)
    async with http:
        with pytest.raises(ValueError, match="legacy private"):
            await client.fetch_bootstrap(
                "Lanlan", subjects=[_SUBJECT], lang="en",
                include_legacy_private=True, max_tokens=100,
            )
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_bootstrap_body_never_carries_legacy_or_budget_fields():
    """The scoped_context body holds only fields its request model declares."""
    recorder = _Recorder()
    client, http = _client(recorder)
    async with http:
        await client.fetch_bootstrap(
            "Lanlan", subjects=[_SUBJECT], lang="en", max_tokens=100,
        )
    body = json.loads(recorder.requests[0].content)
    assert set(body) == {"subjects", "language"}


@pytest.mark.asyncio
async def test_bootstrap_truncates_rendered_text_to_max_tokens():
    """``max_tokens`` caps the returned text, since the server has no budget."""
    from utils.tokenize import count_tokens

    rendered ="".join(f"memory line number {i}.\n" for i in range(400))

    recorder = _Recorder(lambda request: httpx.Response(200, text=f"  {rendered}  "))
    client, http = _client(recorder)
    async with http:
        text = await client.fetch_bootstrap(
            "Lanlan", subjects=[_SUBJECT], lang="en", max_tokens=50,
        )
        untouched = await client.fetch_bootstrap(
            "Lanlan", subjects=[_SUBJECT], lang="en", max_tokens=100000,
        )
    assert 0 < count_tokens(text) <= 50
    assert rendered.strip().startswith(text)
    assert untouched == rendered.strip()


@pytest.mark.asyncio
async def test_empty_inputs_send_nothing():
    """No subjects / blank reply text short-circuit without a request."""
    recorder = _Recorder()
    client, http = _client(recorder)
    async with http:
        assert await client.fetch_bootstrap(
            "Lanlan", subjects=[], lang="en", max_tokens=100,
        ) == ""
        assert await client.post_mentions(
            "Lanlan", subjects=[], response_text="hi",
        ) is True
        assert await client.post_mentions(
            "Lanlan", subjects=[_SUBJECT], response_text="   ",
        ) is True
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_batch_is_false_unless_every_segment_is_ok():
    """A batch with one failed segment is not reported as written."""
    def responder(request):
        return httpx.Response(200, json={
            "status": "processed",
            "segments": [{"status": "ok"}, {"status": "failed"}],
        })

    segment = {"messages": _MESSAGES, "subject": _SUBJECT, "speaker_label": "A"}
    client, http = _client(_Recorder(responder))
    async with http:
        result = await client.post_history_batch(
            "Lanlan", segments=[segment, dict(segment, speaker_label="B")],
        )
    assert not result
    # 已成功的段要报出来：服务端已提交它们，调用方只重试失败的位置
    assert result.segments_ok == (True, False)
    assert result.failed_positions == (1,)


@pytest.mark.asyncio
async def test_list_scoped_subjects_returns_dict_rows():
    """The subjects list is returned with non-dict rows dropped."""
    row = {"subject_kind": "participant", "subject_id": "neko_visit:u", "facts": 2}

    def responder(request):
        return httpx.Response(200, json={"subjects": [row, "junk", 3]})

    client, http = _client(_Recorder(responder))
    async with http:
        assert await client.list_scoped_subjects(
            "Lanlan", platform="neko_visit",
        ) == [row]


@pytest.mark.asyncio
async def test_list_scoped_subjects_rejects_malformed_payload():
    """A payload without a subjects list raises instead of reading as empty."""
    client, http = _client(_Recorder(lambda request: httpx.Response(200, json={})))
    async with http:
        with pytest.raises(ScopedMemoryError):
            await client.list_scoped_subjects("Lanlan", platform="neko_visit")


@pytest.mark.asyncio
async def test_default_http_client_is_the_internal_one(monkeypatch):
    """Without ``http`` the shared internal client is looked up per request."""
    recorder = _Recorder()
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    lookups: list[int] = []

    def fake_get_internal_http_client():
        lookups.append(1)
        return http

    monkeypatch.setattr(
        "memory.scoped_client.get_internal_http_client", fake_get_internal_http_client,
    )
    client = ScopedMemoryClient(base_url=BASE_URL + "/")
    async with http:
        assert await client.post_forget("Lanlan", subject=_SUBJECT) is True
    assert lookups == [1]
    assert str(recorder.requests[0].url) == (
        BASE_URL + "/internal/memory/Lanlan/scoped_forget"
    )


@pytest.mark.asyncio
async def test_character_name_cannot_escape_its_path_segment():
    """Path and query metacharacters in a name are percent-encoded."""
    recorder = _Recorder()
    client, http = _client(recorder)
    async with http:
        await client.post_forget("a/b?c#d", subject=_SUBJECT)
    url = recorder.requests[0].url
    assert url.raw_path == b"/internal/memory/a%2Fb%3Fc%23d/scoped_forget"
    assert url.query == b""


@pytest.mark.asyncio
async def test_ok_segment_with_unpersisted_trust_must_be_retried():
    """``status: ok`` + ``trust.persisted: false`` means retain and retry
    (memory_server ``_trust_response_block``)."""
    def responder(request):
        return httpx.Response(200, json={"status": "processed", "segments": [
            {"status": "ok", "trust": {"persisted": False}},
            {"status": "ok", "trust": {"persisted": None}},
            {"status": "ok", "trust": {"persisted": True}},
        ]})

    segment = {"messages": _MESSAGES, "subject": _SUBJECT, "speaker_label": "A"}
    client, http = _client(_Recorder(responder))
    async with http:
        result = await client.post_history_batch("Lanlan", segments=[segment] * 3)
    assert result.segments_ok == (False, True, True)


@pytest.mark.asyncio
async def test_single_subject_history_with_unpersisted_trust_is_not_done():
    def responder(request):
        return httpx.Response(200, json={"status": "processed", "trust": {"persisted": False}})

    client, http = _client(_Recorder(responder))
    async with http:
        ok = await client.post_history("Lanlan", subject=_SUBJECT, messages=_MESSAGES)
    assert ok is False


@pytest.mark.asyncio
async def test_non_json_2xx_history_response_is_a_failed_write():
    def responder(request):
        return httpx.Response(200, content=b"<html>proxy</html>")

    client, http = _client(_Recorder(responder))
    async with http:
        ok = await client.post_history("Lanlan", subject=_SUBJECT, messages=_MESSAGES)
    assert ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"null", b"[]", b"{}", b'{"status": "failed"}'])
async def test_history_response_must_confirm_processing(body):
    def responder(request):
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    client, http = _client(_Recorder(responder))
    async with http:
        ok = await client.post_history("Lanlan", subject=_SUBJECT, messages=_MESSAGES)
    assert ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"null", b"{}", b"<html/>", b'{"status": "ok"}'])
async def test_forget_requires_a_forgotten_confirmation(body):
    def responder(request):
        return httpx.Response(200, content=body)

    client, http = _client(_Recorder(responder))
    async with http:
        assert await client.post_forget("Lanlan", subject=_SUBJECT) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"null", b"{}", b"<html/>", b'{"status": "error"}', b'{"stat'])
async def test_mentions_require_a_recorded_confirmation(body):
    def responder(request):
        return httpx.Response(200, content=body)

    client, http = _client(_Recorder(responder))
    async with http:
        assert await client.post_mentions(
            "Lanlan", subjects=[_SUBJECT], response_text="hi",
        ) is False

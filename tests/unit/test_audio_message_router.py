"""Voice-message glue: /api/audio-message transcribes with offline ASR."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from main_logic.asr_client.offline import AsrOfflineError
from main_routers import audio_message_router as router_module


PCM = b"\xb8\x0b\x48\xf4" * 8_000  # 1 s


class _FakeTranscriber:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []
        self.provider_key = "glm"
        self.fingerprint = "old-key"

    @property
    def credential_fingerprint(self) -> str:
        return self.fingerprint

    async def transcribe(self, pcm16: bytes, **kwargs: Any) -> str:
        self.calls.append({"bytes": len(pcm16), **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def env(monkeypatch):
    router_module._CACHE.clear()
    state: dict[str, Any] = {
        "transcriber": _FakeTranscriber([]),
        "resolved": [],
        "csrf_ok": True,
    }

    def resolve(core_type: str, *, provider_preference: str | None = None):
        state["resolved"].append((core_type, provider_preference))
        return state["transcriber"]

    async def route_context(target: str) -> dict[str, Any]:
        return {"core_type": "glm", "language": "zh-CN", "provider_preference": "auto"}

    def validate(request, **kwargs):
        if state["csrf_ok"]:
            return None
        return JSONResponse({"ok": False, "error_code": "csrf_validation_failed"}, status_code=403)

    monkeypatch.setattr(router_module, "resolve_offline_transcriber", resolve)
    monkeypatch.setattr(router_module, "_route_context", route_context)
    monkeypatch.setattr(router_module, "_validate_local_mutation_request", validate)
    app = FastAPI()
    app.include_router(router_module.router)
    state["client"] = TestClient(app)
    yield state
    router_module._CACHE.clear()


def _payload(**changes: Any) -> dict[str, Any]:
    payload = {
        "requestId": "1764000000000-abc",
        "target": "Neko",
        "audio": base64.b64encode(PCM).decode(),
        "sampleRateHz": 16000,
    }
    payload.update(changes)
    return payload


def test_transcribes_and_passes_route_context(env) -> None:
    env["transcriber"].outcomes.append("你好")
    response = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert response.status_code == 200
    assert response.json() == {"ok": True, "text": "你好"}
    assert env["resolved"] == [("glm", "auto")]
    (call,) = env["transcriber"].calls
    assert call == {"bytes": len(PCM), "sample_rate_hz": 16000, "language": "zh-CN"}


def test_retry_with_same_request_id_reuses_the_transcript(env) -> None:
    env["transcriber"].outcomes.append("只转写一次")
    first = env["client"].post("/api/audio-message/transcribe", json=_payload())
    retry = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert first.json() == retry.json() == {"ok": True, "text": "只转写一次"}
    assert len(env["transcriber"].calls) == 1
    other = _payload(requestId="1764000000001-def")
    env["transcriber"].outcomes.append("新的一条")
    assert env["client"].post("/api/audio-message/transcribe", json=other).json()["text"] == "新的一条"
    assert len(env["transcriber"].calls) == 2


def test_failures_are_not_cached_and_map_to_status(env) -> None:
    env["transcriber"].outcomes.extend(
        [AsrOfflineError("ASR_RATE_LIMITED", provider_key="glm"), "第二次成功"]
    )
    busy = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert busy.status_code == 429
    assert busy.json() == {"ok": False, "error_code": "ASR_RATE_LIMITED"}
    retry = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert retry.json() == {"ok": True, "text": "第二次成功"}


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("ASR_OFFLINE_NOT_SUPPORTED", 409),
        ("ASR_CREDENTIALS_MISSING", 409),
        ("ASR_OFFLINE_TIMEOUT", 504),
        ("ASR_OFFLINE_PROVIDER_FAILED", 502),
        ("ASR_OFFLINE_INVALID_AUDIO", 400),
    ],
)
def test_offline_error_codes(env, code, status) -> None:
    env["transcriber"].outcomes.append(AsrOfflineError(code))
    response = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert response.status_code == status
    assert response.json()["error_code"] == code


def test_unexpected_crash_is_a_provider_failure(env) -> None:
    env["transcriber"].outcomes.append(RuntimeError("boom"))
    response = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert response.status_code == 502
    assert response.json()["error_code"] == "ASR_OFFLINE_PROVIDER_FAILED"


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"audio": "not base64!"}, 400, "ASR_OFFLINE_INVALID_AUDIO"),
        ({"sampleRateHz": 48000}, 400, "ASR_OFFLINE_INVALID_AUDIO"),
        ({"requestId": "bad id with spaces"}, 400, "ASR_OFFLINE_INVALID_REQUEST"),
        ({"target": ""}, 400, "ASR_OFFLINE_INVALID_REQUEST"),
        ({"audio": None}, 400, "ASR_OFFLINE_INVALID_REQUEST"),
    ],
)
def test_invalid_requests(env, changes, status, code) -> None:
    response = env["client"].post("/api/audio-message/transcribe", json=_payload(**changes))
    assert (response.status_code, response.json()["error_code"]) == (status, code)
    assert env["transcriber"].calls == []


def test_recording_longer_than_the_cap_is_refused(env) -> None:
    too_long = b"\x00\x00" * (16_000 * 32)
    response = env["client"].post(
        "/api/audio-message/transcribe",
        json=_payload(audio=base64.b64encode(too_long).decode()),
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "ASR_OFFLINE_AUDIO_TOO_LONG"


def test_mutation_guard_is_enforced(env) -> None:
    env["csrf_ok"] = False
    response = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
    assert env["transcriber"].calls == []


def test_status_reports_offline_capability(env, monkeypatch) -> None:
    assert env["client"].get("/api/audio-message/status", params={"target": "Neko"}).json() == {
        "supported": True,
        "provider": "glm",
        "error_code": None,
    }
    assert env["resolved"] == [("glm", "auto")]

    def unsupported(core_type: str, *, provider_preference: str | None = None):
        raise AsrOfflineError("ASR_OFFLINE_NOT_SUPPORTED", provider_key="step")

    monkeypatch.setattr(router_module, "resolve_offline_transcriber", unsupported)
    assert env["client"].get("/api/audio-message/status", params={"target": "Neko"}).json() == {
        "supported": False,
        "provider": "step",
        "error_code": "ASR_OFFLINE_NOT_SUPPORTED",
    }


def test_a_rejected_key_stays_unsupported_until_it_is_replaced(env) -> None:
    env["transcriber"].outcomes.append(AsrOfflineError("ASR_CREDENTIALS_REJECTED", provider_key="glm"))
    rejected = env["client"].post("/api/audio-message/transcribe", json=_payload())
    assert rejected.json()["error_code"] == "ASR_CREDENTIALS_REJECTED"
    # The key is still present, so route resolution alone would say "supported".
    status = env["client"].get("/api/audio-message/status", params={"target": "Neko"}).json()
    assert status == {"supported": False, "provider": "glm", "error_code": "ASR_CREDENTIALS_REJECTED"}

    env["transcriber"].fingerprint = "new-key"
    assert env["client"].get("/api/audio-message/status", params={"target": "Neko"}).json()["supported"] is True


async def test_concurrent_retry_joins_the_running_transcription() -> None:
    cache = router_module._TranscriptCache()
    release = asyncio.Event()
    starts = 0

    async def start() -> str:
        nonlocal starts
        starts += 1
        await release.wait()
        return "合并"

    first = asyncio.create_task(cache.transcribe("k", start))
    await asyncio.sleep(0)
    second = asyncio.create_task(cache.transcribe("k", start))
    await asyncio.sleep(0)
    release.set()
    assert await first == await second == "合并"
    assert starts == 1


async def test_disconnected_caller_does_not_cancel_the_shared_transcription() -> None:
    cache = router_module._TranscriptCache()
    release = asyncio.Event()

    async def start() -> str:
        await release.wait()
        return "还在"

    first = asyncio.create_task(cache.transcribe("k", start))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()

    async def never() -> str:
        raise AssertionError("must join the running transcription")

    assert await cache.transcribe("k", never) == "还在"


async def test_route_context_uses_the_character_session(monkeypatch) -> None:
    class _Manager:
        core_api_type = "Gemini"
        user_language = "ja"

    monkeypatch.setattr(router_module, "get_session_manager", lambda: {"Neko": _Manager()})

    async def settings() -> dict[str, Any]:
        return {"independentAsrProviderPreference": "faster_whisper"}

    monkeypatch.setattr(router_module, "aload_global_conversation_settings", settings)
    assert await router_module._route_context("Neko") == {
        "core_type": "gemini",
        "language": "ja",
        "provider_preference": "faster_whisper",
    }

    class _Config:
        def get_core_config(self) -> dict[str, Any]:
            return {"CORE_API_TYPE": "qwen"}

    monkeypatch.setattr(router_module, "get_config_manager", lambda: _Config())
    context = await router_module._route_context("Unknown")
    assert context["core_type"] == "qwen"
    assert context["language"] is None


async def test_failure_after_the_caller_left_is_not_replayed_to_retries() -> None:
    cache = router_module._TranscriptCache()
    release = asyncio.Event()
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await release.wait()
            raise AsrOfflineError("ASR_OFFLINE_TIMEOUT")
        return "重试成功"

    first = asyncio.create_task(cache.transcribe("k", flaky))
    await asyncio.sleep(0)
    first.cancel()  # the client disconnected mid-transcription
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert await cache.transcribe("k", flaky) == "重试成功"
    assert attempts == 2


async def test_success_after_the_caller_left_is_cached_and_expires() -> None:
    cache = router_module._TranscriptCache()
    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "后台完成"

    first = asyncio.create_task(cache.transcribe("k", slow))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)
    stored_at, value = cache._entries["k"]
    assert value == "后台完成"  # settled to a plain transcript, so it can expire
    # Age the entry past its TTL instead of faking the clock.
    cache._entries["k"] = (stored_at - router_module._CACHE_TTL_SECONDS - 1, value)

    async def fresh() -> str:
        return "重新转写"

    assert await cache.transcribe("k", fresh) == "重新转写"


def test_a_silent_recording_does_not_clear_a_rejected_key(env) -> None:
    env["transcriber"].outcomes.extend(
        [AsrOfflineError("ASR_CREDENTIALS_REJECTED", provider_key="glm"), "", "你好"]
    )
    env["client"].post("/api/audio-message/transcribe", json=_payload())
    silent = base64.b64encode(b"\x00\x00" * 16_000).decode()
    response = env["client"].post(
        "/api/audio-message/transcribe",
        json=_payload(requestId="silent-1", audio=silent),
    )
    assert response.json() == {"ok": True, "text": ""}
    # Nothing reached the provider, so the key is still known to be refused.
    status = env["client"].get("/api/audio-message/status", params={"target": "Neko"}).json()
    assert status["supported"] is False
    # A spoken recording that succeeds with the same key does prove it works.
    env["client"].post("/api/audio-message/transcribe", json=_payload(requestId="spoken-2"))
    assert env["client"].get("/api/audio-message/status", params={"target": "Neko"}).json()["supported"] is True


async def test_running_transcriptions_are_capped_but_retries_still_join() -> None:
    cache = router_module._TranscriptCache()
    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "完成"

    limit = router_module._MAX_RUNNING_TRANSCRIPTIONS
    running = [asyncio.create_task(cache.transcribe(f"k{i}", slow)) for i in range(limit)]
    await asyncio.sleep(0)
    with pytest.raises(AsrOfflineError) as caught:
        await cache.transcribe("one-too-many", slow)
    assert caught.value.code == "ASR_RATE_LIMITED"
    # A retry of a request that is already running joins it instead.
    joined = asyncio.create_task(cache.transcribe("k0", slow))
    await asyncio.sleep(0)
    release.set()
    assert await joined == "完成"
    assert [await task for task in running] == ["完成"] * limit
    # Capacity is back once they finish.
    async def quick() -> str:
        return "又可以了"
    assert await cache.transcribe("one-too-many", quick) == "又可以了"

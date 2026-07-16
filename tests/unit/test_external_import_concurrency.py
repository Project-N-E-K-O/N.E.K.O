"""Concurrent external-import behaviour: persona fusion gather + daily extraction.

Covers the parallelization of the import commit path:
- persona entities (master/neko) fuse concurrently; error priority is
  all-too-large -> 413, any retryable failure -> 500 partial (retry converges:
  succeeded entities fingerprint-skip, then a pure too_large surfaces as 413).
- daily journals extract under bounded concurrency; one crashing day is
  counted in failed_days without aborting the others (best-effort per day).
"""
from __future__ import annotations

import asyncio
import json

import pytest

import app.memory_server.routes as routes_mod
from app.memory_server.routes import (
    ExternalMemoryImportRequest,
    import_external_markdown,
)
from memory.persona.fusion import (
    ExternalMemoryFusionError,
    ExternalMemoryImportTooLargeError,
)
from memory.facts import FactStore


# ── routes 层 harness ────────────────────────────────────────────────


def _persona_cand(entity: str, text: str) -> dict:
    return {
        "target": "persona",
        "entity": entity,
        "text": text,
        "kind": "user" if entity == "master" else "soul",
        "source_file": "USER.md" if entity == "master" else "SOUL.md",
        "source_section": "",
        "event_date": None,
    }


class _FakePersonaManager:
    """behavior: entity -> dict result | Exception to raise | async callable."""

    def __init__(self, behavior: dict):
        self.behavior = behavior

    async def afuse_external_facts(self, name, entity, candidates, source_format):
        action = self.behavior[entity]
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return await action(entity)
        return action


class _FakeFactStore:
    async def _apersist_new_facts(self, name, extracted, *, default_source, semantic_dedup):
        return []

    async def aimport_external_daily(self, name, candidates, source_format, imported_at):
        return {"added": 0, "days": 0, "failed_days": 0}


@pytest.fixture
def wire(monkeypatch):
    def _wire(persona_manager):
        monkeypatch.setattr(routes_mod.runtime, "persona_manager", persona_manager, raising=False)
        monkeypatch.setattr(routes_mod.runtime, "fact_store", _FakeFactStore(), raising=False)
        monkeypatch.setattr(routes_mod.runtime, "_config_manager", object(), raising=False)
        monkeypatch.setattr(routes_mod, "assert_cloudsave_writable", lambda *a, **k: None)
        monkeypatch.setattr(routes_mod, "validate_lanlan_name", lambda n: n)
    return _wire


def _request() -> ExternalMemoryImportRequest:
    return ExternalMemoryImportRequest(
        character_name="Neko",
        source_format="openclaw",
        imported_files=["USER.md", "SOUL.md"],
        candidates=[_persona_cand("master", "likes tea"), _persona_cand("neko", "warm but direct")],
    )


def _body(response) -> dict:
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_persona_entities_fuse_concurrently(wire):
    # 交叉握手：master 的融合要等 neko 已启动（反之亦然）才返回。串行实现里
    # 第一个 entity 永远等不到第二个启动 → 超时；gather 并发则双双通过。
    started = {"master": asyncio.Event(), "neko": asyncio.Event()}

    async def fuse(entity):
        started[entity].set()
        other = "neko" if entity == "master" else "master"
        await asyncio.wait_for(started[other].wait(), timeout=5)
        return {"added": 1, "skipped": 0, "fused": True}

    wire(_FakePersonaManager({"master": fuse, "neko": fuse}))

    result = await asyncio.wait_for(import_external_markdown(_request()), timeout=5)

    assert result["status"] == "success"
    assert result["added_persona"] == 2


@pytest.mark.asyncio
async def test_persona_all_too_large_returns_413(wire):
    wire(_FakePersonaManager({
        "master": ExternalMemoryImportTooLargeError("master too large"),
        "neko": ExternalMemoryImportTooLargeError("neko too large"),
    }))

    response = await import_external_markdown(_request())

    assert response.status_code == 413
    body = _body(response)
    assert body["error_code"] == "external_import_too_large"
    assert body["partial_import"]["added_persona"] == 0


@pytest.mark.asyncio
async def test_persona_success_plus_too_large_returns_413_with_partial_counts(wire):
    # 一个 entity 成功、另一个确定性太大：剩余的唯一问题就是 too_large（重试
    # 无用，成功侧已有指纹幂等）→ 413「拆分」是正确引导；partial_import 里带上
    # 已落盘计数，前端 too_large 分支据此广播 memory_edited。
    wire(_FakePersonaManager({
        "master": {"added": 3, "skipped": 0, "fused": True},
        "neko": ExternalMemoryImportTooLargeError("neko too large"),
    }))

    response = await import_external_markdown(_request())

    assert response.status_code == 413
    body = _body(response)
    assert body["error_code"] == "external_import_too_large"
    assert body["partial_import"]["added_persona"] == 3


@pytest.mark.asyncio
async def test_persona_too_large_mixed_with_retryable_failure_returns_partial(wire):
    # too_large 与「可重试失败」并存：先返回 partial（500）让可重试侧收敛，
    # 收敛后只剩 too_large 自然浮出 413。
    wire(_FakePersonaManager({
        "master": ExternalMemoryFusionError("transient fusion failure"),
        "neko": ExternalMemoryImportTooLargeError("neko too large"),
    }))

    response = await import_external_markdown(_request())

    assert response.status_code == 500
    body = _body(response)
    assert body["error_code"] == "external_import_partial"
    assert body["partial_import"]["added_persona"] == 0


@pytest.mark.asyncio
async def test_persona_one_retryable_failure_still_counts_successful_entity(wire):
    wire(_FakePersonaManager({
        "master": {"added": 2, "skipped": 1, "fused": True},
        "neko": ExternalMemoryFusionError("fusion LLM failed"),
    }))

    response = await import_external_markdown(_request())

    assert response.status_code == 500
    body = _body(response)
    assert body["error_code"] == "external_import_partial"
    assert body["partial_import"]["added_persona"] == 2


# ── daily 有界并发 ───────────────────────────────────────────────────


class _DailyConcurrencyHarness(FactStore):
    """FactStore stand-in: async-controllable extraction stub, no real init."""

    def __init__(self, extract):
        self._extract = extract
        self.persisted: list[list[dict]] = []

    async def _allm_extract_facts(self, lanlan_name, messages):
        text = "\n".join(getattr(m, "content", "") for m in messages)
        return await self._extract(text)

    async def _apersist_new_facts(
        self, lanlan_name, extracted, *,
        default_source="user_observation", semantic_dedup=True,
    ):
        self.persisted.append([dict(f) for f in extracted])
        return list(extracted)


def _daily(source_file, event_date, text):
    return {"text": text, "source_file": source_file, "source_section": "", "event_date": event_date}


@pytest.mark.asyncio
async def test_daily_days_extract_concurrently():
    # 与 persona 同款交叉握手：两天互相等待对方的 LLM 调用已启动。串行实现
    # 会在第一天上超时；有界并发（上限≥2）双双通过。
    started = {"a": asyncio.Event(), "b": asyncio.Event()}

    async def extract(text):
        key = "a" if "day-a" in text else "b"
        started[key].set()
        other = "b" if key == "a" else "a"
        await asyncio.wait_for(started[other].wait(), timeout=5)
        return [{"text": f"fact {key}", "importance": 5}]

    harness = _DailyConcurrencyHarness(extract)
    result = await asyncio.wait_for(
        harness.aimport_external_daily(
            "Neko",
            [_daily("memories/2026-07-12.md", "2026-07-12", "day-a"),
             _daily("memories/2026-07-13.md", "2026-07-13", "day-b")],
            "hermes", "t",
        ),
        timeout=5,
    )

    assert result == {"added": 2, "days": 2, "failed_days": 0}


@pytest.mark.asyncio
async def test_daily_crashing_day_is_counted_failed_and_others_survive():
    # 单日抽取崩溃（异常而非 None）也必须 best-effort：计入 failed_days，
    # 不拖垮其他天（gather return_exceptions 语义）。
    async def extract(text):
        if "bad" in text:
            raise RuntimeError("provider exploded")
        return [{"text": "good fact", "importance": 5}]

    harness = _DailyConcurrencyHarness(extract)
    result = await harness.aimport_external_daily(
        "Neko",
        [_daily("memories/2026-07-12.md", "2026-07-12", "bad day"),
         _daily("memories/2026-07-13.md", "2026-07-13", "fine day")],
        "hermes", "t",
    )

    assert result == {"added": 1, "days": 2, "failed_days": 1}
    assert len(harness.persisted) == 1

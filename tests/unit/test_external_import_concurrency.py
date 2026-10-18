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
from memory.persona.fusion import (
    ExternalMemoryFusionError,
    ExternalMemoryImportTooLargeError,
)
from memory.facts import FactStore
from utils.language_utils import get_global_language_full


# -- Routes-layer harness --


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
    """daily_outcome: dict to return from aimport_external_daily, or Exception."""

    def __init__(self, daily_outcome=None):
        self.daily_outcome = daily_outcome or {
            "added": 0, "days": 0, "failed_days": 0, "skipped_days": 0,
        }

    async def _apersist_new_facts(self, name, extracted, *, default_source, semantic_dedup):
        return []

    async def aimport_external_daily(self, name, candidates, source_format, imported_at):
        if isinstance(self.daily_outcome, Exception):
            raise self.daily_outcome
        return self.daily_outcome


@pytest.fixture
def wire(monkeypatch):
    def _wire(persona_manager, fact_store=None):
        monkeypatch.setattr(routes_mod.runtime, "persona_manager", persona_manager, raising=False)
        monkeypatch.setattr(routes_mod.runtime, "fact_store", fact_store or _FakeFactStore(), raising=False)
        monkeypatch.setattr(routes_mod.runtime, "_config_manager", object(), raising=False)
        monkeypatch.setattr(routes_mod, "assert_cloudsave_writable", lambda *a, **k: None)
        monkeypatch.setattr(routes_mod, "validate_lanlan_name", lambda n: n)
        monkeypatch.setattr(
            routes_mod.locale_state,
            "allocate_character_prompt_locale_order",
            lambda _name: 1,
        )
        monkeypatch.setattr(
            routes_mod.locale_state,
            "reserve_character_prompt_locale_order",
            lambda _name, *, order: order,
        )
        monkeypatch.setattr(
            routes_mod.locale_state,
            "record_character_prompt_locale",
            lambda *_args, **_kwargs: None,
        )
    return _wire


def _daily_cand(source_file: str, event_date: str) -> dict:
    return {
        "target": "facts",
        "entity": "master",
        "text": f"journal for {event_date}",
        "kind": "daily",
        "source_file": source_file,
        "source_section": "",
        "event_date": event_date,
    }


def _request(
    extra_candidates: list | None = None,
    *,
    language: str | None = None,
    render_language: str | None = None,
) -> "routes_mod.ExternalMemoryImportRequest":
    return routes_mod.ExternalMemoryImportRequest(
        character_name="Neko",
        source_format="openclaw",
        imported_files=["USER.md", "SOUL.md"],
        candidates=[
            _persona_cand("master", "likes tea"),
            _persona_cand("neko", "warm but direct"),
        ] + (extra_candidates or []),
        language=language,
        render_language=render_language,
    )


def _body(response) -> dict:
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_persona_entities_fuse_concurrently(wire):
    # Cross-handshake: master merging waits for neko to start, and vice versa. Serial execution leaves
    # the first entity waiting forever for the second and times out; concurrent gather lets both pass.
    started = {"master": asyncio.Event(), "neko": asyncio.Event()}

    async def fuse(entity):
        started[entity].set()
        other = "neko" if entity == "master" else "master"
        await asyncio.wait_for(started[other].wait(), timeout=5)
        return {"added": 1, "skipped": 0, "fused": True}

    wire(_FakePersonaManager({"master": fuse, "neko": fuse}))

    result = await asyncio.wait_for(routes_mod.import_external_markdown(_request()), timeout=5)

    assert result["status"] == "success"
    assert result["added_persona"] == 2


@pytest.mark.asyncio
async def test_daily_extraction_keeps_request_locale(wire):
    class _LocaleRecordingFactStore(_FakeFactStore):
        language = None

        async def aimport_external_daily(
            self,
            name,
            candidates,
            source_format,
            imported_at,
        ):
            self.language = get_global_language_full()
            return await super().aimport_external_daily(
                name,
                candidates,
                source_format,
                imported_at,
            )

    fact_store = _LocaleRecordingFactStore()
    wire(
        _FakePersonaManager({
            "master": {"added": 1, "skipped": 0, "fused": True},
            "neko": {"added": 1, "skipped": 0, "fused": True},
        }),
        fact_store,
    )

    result = await routes_mod.import_external_markdown(
        _request(
            [_daily_cand("memory/2026-07-31.md", "2026-07-31")],
            language="zh-TW",
            render_language="ja",
        )
    )

    assert result["status"] == "success"
    assert fact_store.language == "zh-TW"


@pytest.mark.asyncio
async def test_external_import_persists_explicit_request_locale(
    wire,
    monkeypatch,
):
    wire(_FakePersonaManager({
        "master": {"added": 1, "skipped": 0, "fused": True},
        "neko": {"added": 1, "skipped": 0, "fused": True},
    }))
    recorded = []
    allocated = []
    monkeypatch.setattr(
        routes_mod.locale_state,
        "allocate_character_prompt_locale_order",
        lambda name: allocated.append(name) or 73,
    )
    monkeypatch.setattr(
        routes_mod.locale_state,
        "reserve_character_prompt_locale_order",
        lambda name, *, order: order,
    )
    monkeypatch.setattr(
        routes_mod.locale_state,
        "record_character_prompt_locale",
        lambda name, language, *, order: recorded.append(
            (name, language, order)
        ),
    )
    result = await routes_mod.import_external_markdown(
        _request(language="zh-TW", render_language="ja")
    )

    assert result["status"] == "success"
    assert allocated == ["Neko"]
    assert recorded == [("Neko", "zh-TW", 73)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("durable_locale", "expected_locale"),
    (("ja", "ja"), (None, "zh-TW")),
)
async def test_external_import_prefers_durable_then_render_without_persisting_fallback(
    wire,
    monkeypatch,
    durable_locale,
    expected_locale,
):
    observed_languages = {}

    async def fuse(entity):
        observed_languages[f"persona:{entity}"] = get_global_language_full()
        return {"added": 1, "skipped": 0, "fused": True}

    class _LocaleRecordingFactStore(_FakeFactStore):
        async def aimport_external_daily(
            self,
            name,
            candidates,
            source_format,
            imported_at,
        ):
            observed_languages["daily"] = get_global_language_full()
            return await super().aimport_external_daily(
                name,
                candidates,
                source_format,
                imported_at,
            )

    wire(
        _FakePersonaManager({"master": fuse, "neko": fuse}),
        _LocaleRecordingFactStore(),
    )
    durable_reads = []
    locale_writes = []
    monkeypatch.setattr(
        routes_mod.locale_state,
        "get_character_prompt_locale",
        lambda name: durable_reads.append(name) or durable_locale,
    )
    monkeypatch.setattr(
        routes_mod.locale_state,
        "allocate_character_prompt_locale_order",
        lambda name: locale_writes.append(("allocate", name)),
    )
    monkeypatch.setattr(
        routes_mod.locale_state,
        "reserve_character_prompt_locale_order",
        lambda name, *, order: locale_writes.append(("reserve", name, order)),
    )
    monkeypatch.setattr(
        routes_mod.locale_state,
        "record_character_prompt_locale",
        lambda name, language, *, order: locale_writes.append(
            ("record", name, language, order)
        ),
    )

    result = await routes_mod.import_external_markdown(
        _request(
            [_daily_cand("memory/2026-07-31.md", "2026-07-31")],
            render_language="zh-TW",
        )
    )

    assert result["status"] == "success"
    assert durable_reads == ["Neko"]
    assert locale_writes == []
    assert observed_languages == {
        "persona:master": expected_locale,
        "persona:neko": expected_locale,
        "daily": expected_locale,
    }


@pytest.mark.asyncio
async def test_persona_all_too_large_returns_413(wire):
    wire(_FakePersonaManager({
        "master": ExternalMemoryImportTooLargeError("master too large"),
        "neko": ExternalMemoryImportTooLargeError("neko too large"),
    }))

    response = await routes_mod.import_external_markdown(_request())

    assert response.status_code == 413
    body = _body(response)
    assert body["error_code"] == "external_import_too_large"
    assert body["partial_import"]["added_persona"] == 0


@pytest.mark.asyncio
async def test_persona_success_plus_too_large_returns_413_with_partial_counts(wire):
    # One entity succeeds while another is deterministically too large: only too_large remains, with retries
    # useless and fingerprint idempotence protecting the successful side. Return 413 with splitting guidance and include
    # persisted counts in partial_import so the frontend's too_large branch broadcasts memory_edited.
    wire(_FakePersonaManager({
        "master": {"added": 3, "skipped": 0, "fused": True},
        "neko": ExternalMemoryImportTooLargeError("neko too large"),
    }))

    response = await routes_mod.import_external_markdown(_request())

    assert response.status_code == 413
    body = _body(response)
    assert body["error_code"] == "external_import_too_large"
    assert body["partial_import"]["added_persona"] == 3


@pytest.mark.asyncio
async def test_persona_too_large_mixed_with_retryable_failure_returns_partial(wire):
    # When too_large coexists with retryable failure, return partial (500) first so the retryable side converges;
    # once resolved, only too_large remains and naturally surfaces as 413.
    wire(_FakePersonaManager({
        "master": ExternalMemoryFusionError("transient fusion failure"),
        "neko": ExternalMemoryImportTooLargeError("neko too large"),
    }))

    response = await routes_mod.import_external_markdown(_request())

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

    response = await routes_mod.import_external_markdown(_request())

    assert response.status_code == 500
    body = _body(response)
    assert body["error_code"] == "external_import_partial"
    assert body["partial_import"]["added_persona"] == 2


@pytest.mark.asyncio
async def test_daily_failed_days_returns_retryable_partial(wire):
    # Failed daily days must not return success, depriving users of retry signals (Greptile P1); return
    # partial with added_facts counting persisted MEMORY.md and successful daily entries.
    wire(
        _FakePersonaManager({
            "master": {"added": 1, "skipped": 0, "fused": True},
            "neko": {"added": 1, "skipped": 0, "fused": True},
        }),
        fact_store=_FakeFactStore(
            {"added": 4, "days": 3, "failed_days": 1, "skipped_days": 0}
        ),
    )

    response = await routes_mod.import_external_markdown(
        _request([_daily_cand("memories/2026-07-12.md", "2026-07-12")])
    )

    assert response.status_code == 500
    body = _body(response)
    assert body["error_code"] == "external_import_partial"
    assert body["partial_import"]["added_persona"] == 2
    assert body["partial_import"]["added_facts"] == 4


@pytest.mark.asyncio
async def test_daily_over_cap_returns_413_too_large(wire):
    # aimport_external_daily raising a day-count-cap error -> 413 too_large, prompting frontend splitting guidance.
    wire(
        _FakePersonaManager({
            "master": {"added": 1, "skipped": 0, "fused": True},
            "neko": {"added": 1, "skipped": 0, "fused": True},
        }),
        fact_store=_FakeFactStore(
            ExternalMemoryImportTooLargeError("daily import spans 46 new journal days")
        ),
    )

    response = await routes_mod.import_external_markdown(
        _request([_daily_cand("memories/2026-07-12.md", "2026-07-12")])
    )

    assert response.status_code == 413
    body = _body(response)
    assert body["error_code"] == "external_import_too_large"
    assert body["partial_import"]["added_persona"] == 2


# -- Bounded daily concurrency --


class _DailyConcurrencyHarness(FactStore):
    """FactStore stand-in: async-controllable extraction stub, no real init."""

    def __init__(self, extract):
        super().__init__()
        self._extract = extract
        self.persisted: list[list[dict]] = []

    async def aload_facts(self, lanlan_name):
        return []

    async def aload_facts_full(self, lanlan_name):
        # Isolate real disk state: _acollect_day_fp_sources now reads active and archive sources.
        return []

    # Isolate sidecar file IO, or days without fact carriers access real memory_dir.
    def _load_external_import_state(self, name):
        return {}

    async def _arecord_unpersisted_day_fp(self, lanlan_name, fingerprint):
        return None

    async def _aclear_day_fps(self, lanlan_name, fingerprints):
        # Isolate cleanup for successful/persist-failed days too: real _clear_day_fps_locked calls
        # ensure_character_dir before its exists early return, creating phantom
        # character directories in real memory_dir.
        return None

    async def _allm_extract_facts(self, lanlan_name, messages, **_kwargs):
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
    # Cross-handshake as with persona: each day waits for the other's LLM call to start. Serial execution
    # times out on day one; bounded concurrency with a limit >=2 lets both pass.
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

    assert result == {"added": 2, "days": 2, "failed_days": 0, "skipped_days": 0}


@pytest.mark.asyncio
async def test_daily_concurrency_is_bounded_by_semaphore():
    # Cross-handshaking proves only parallelism; unbounded gather also passes. An active counter proves
    # Semaphore caps peak concurrency at EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY
    # (removing throttling fails this test), while a peak >=2 confirms actual concurrency (CodeRabbit).
    from config import EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY

    active = 0
    peak = 0

    async def extract(text):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)  # Make all concurrent slots genuinely overlap.
        active -= 1
        return [{"text": f"fact for {text[:12]}", "importance": 5}]

    harness = _DailyConcurrencyHarness(extract)
    days = [
        _daily("memories/2026-07-%02d.md" % (10 + i), "2026-07-%02d" % (10 + i), f"day-{i}")
        for i in range(EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY + 2)
    ]
    result = await asyncio.wait_for(
        harness.aimport_external_daily("Neko", days, "hermes", "t"), timeout=10,
    )

    assert result["failed_days"] == 0
    assert result["added"] == len(days)
    assert 2 <= peak <= EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY


@pytest.mark.asyncio
async def test_daily_crashing_day_is_counted_failed_and_others_survive():
    # A day's extraction exception, rather than None, must also remain best-effort: count failed_days
    # without breaking other days, following gather return_exceptions semantics.
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

    assert result == {"added": 1, "days": 2, "failed_days": 1, "skipped_days": 0}
    assert len(harness.persisted) == 1

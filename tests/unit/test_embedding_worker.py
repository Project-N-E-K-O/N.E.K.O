# -*- coding: utf-8 -*-
"""Unit tests for memory.embedding_worker.EmbeddingWarmupWorker.

The worker is the only piece of P2 that runs as a background coroutine,
so the tests focus on:

  * the warmup wait correctly races (delay, first_process, stop)
  * a disabled service short-circuits and exits without doing any work
  * the sweep walks persona / reflection / fact stores and stamps the
    embedding triple in place via the configured EmbeddingService
  * a per-character fact's existing embedding is left untouched when
    text + model_id still match (cache hit path)
  * the per-tick budget bound is honoured so a large backlog can't
    monopolise the loop

We stub the EmbeddingService so the test is hermetic — no ONNX session,
no model file. The stub records every call so tests can assert on the
embed_batch trace as well as the on-disk side effects."""
from __future__ import annotations

import asyncio
import json

import pytest

from memory.embedding_worker import (
    EmbeddingWarmupWorker,
    MAX_ENTRIES_PER_TICK,
)
from memory.embeddings import (
    _embedding_text_sha256,
    reset_embedding_service_for_tests,
)


# ── stub embedding service ───────────────────────────────────────────


class _FakeService:
    """Hand-rolled stub matching the surface area embedding_worker uses.
    Avoids monkeypatching the singleton, which would fight the
    parallel-test isolation pytest-asyncio gives us."""

    def __init__(
        self, *, available: bool = True, model_id: str = "fake-128d-int8",
        disabled: bool = False, vector_factory=None,
    ) -> None:
        self._available = available
        self._model_id = model_id
        self._disabled = disabled
        self._reason = "test_reason" if disabled else "none"
        self._vector_factory = vector_factory or (lambda text: [0.5] * 4)
        self.embed_batch_calls: list[list[str]] = []
        self.load_called = False

    def is_available(self) -> bool:
        return self._available

    def is_disabled(self) -> bool:
        return self._disabled

    def disable_reason(self) -> str:
        return self._reason

    def model_id(self):
        return None if self._disabled else self._model_id

    async def request_load(self) -> bool:
        self.load_called = True
        return self._available and not self._disabled

    async def embed_batch(self, texts):
        self.embed_batch_calls.append(list(texts))
        return [self._vector_factory(t) if t else None for t in texts]

    # `flip_disabled_after_n_calls` etc. would be additional knobs;
    # tests below only need the basic shape so we keep the stub lean.


@pytest.fixture(autouse=True)
def _isolate_singleton():
    """Rebuild the EmbeddingService singleton between tests so a stub
    swap in one test never leaks into the next."""
    reset_embedding_service_for_tests()
    yield
    reset_embedding_service_for_tests()


# ── memory subsystem stubs ───────────────────────────────────────────


class _PersonaStub:
    """In-memory stand-in for PersonaManager.  Mirrors the per-character
    asyncio.Lock + ``_aensure_persona_locked`` API the worker now uses
    (CodeRabbit/Codex PR-956 P1 — lock the load+mutate+save sequence)."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.save_calls = 0
        self._alocks: dict[str, asyncio.Lock] = {}

    def _get_alock(self, name: str) -> asyncio.Lock:
        if name not in self._alocks:
            self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    async def _aensure_persona_locked(self, name: str) -> dict:
        return self.store.setdefault(name, {
            "master": {"facts": []},
            "neko": {"facts": []},
        })

    async def aensure_persona(self, name: str) -> dict:
        async with self._get_alock(name):
            return await self._aensure_persona_locked(name)

    async def asave_persona(self, name: str, persona: dict) -> None:
        # Rebind so serialisation → load round trips lose nothing.
        self.store[name] = json.loads(json.dumps(persona))
        self.save_calls += 1


class _ReflectionStub:
    """Mirrors the two ReflectionEngine methods the worker calls plus the
    per-character asyncio.Lock helper (same lock-around-load+save
    contract synthesis already uses)."""

    def __init__(self) -> None:
        self.store: dict[str, list[dict]] = {}
        self.save_calls = 0
        self._alocks: dict[str, asyncio.Lock] = {}

    def _get_alock(self, name: str) -> asyncio.Lock:
        if name not in self._alocks:
            self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    async def _aload_reflections_full(self, name: str) -> list[dict]:
        return self.store.setdefault(name, [])

    async def asave_reflections(self, name: str, items: list[dict]) -> None:
        self.store[name] = json.loads(json.dumps(items))
        self.save_calls += 1


class _FactStub:
    def __init__(self) -> None:
        self.store: dict[str, list[dict]] = {}
        self.save_calls = 0

    async def aload_facts(self, name: str) -> list[dict]:
        return self.store.setdefault(name, [])

    async def asave_facts(self, name: str) -> None:
        self.save_calls += 1


def _build_worker(
    *, service, persona, reflection, fact, names=("小天",), warmup_delay=0.01,
    dedup_resolver=None,
) -> EmbeddingWarmupWorker:
    w = EmbeddingWarmupWorker(
        get_persona_manager=lambda: persona,
        get_reflection_engine=lambda: reflection,
        get_fact_store=lambda: fact,
        get_character_names=lambda: list(names),
        warmup_delay_seconds=warmup_delay,
        get_dedup_resolver=(lambda: dedup_resolver) if dedup_resolver is not None else None,
    )
    # Hand the stub in by replacement — get_embedding_service() was
    # called in __init__, so we override the attribute directly.
    w._service = service
    return w


class _DedupResolverStub:
    """Captures aenqueue_candidates calls so the integration test can
    assert the worker forwards the right pairs after a fact sweep."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []

    async def aenqueue_candidates(self, name: str, pairs: list[dict]) -> int:
        self.calls.append((name, list(pairs)))
        return len(pairs)


# ── tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_exits_when_service_disabled_at_construction():
    """Sticky-disabled service ⇒ worker logs and returns immediately;
    no warmup wait, no sweep, no save calls."""
    service = _FakeService(disabled=True, available=False)
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    task = w.start()
    await asyncio.wait_for(task, timeout=1.0)
    assert service.load_called is False
    assert persona.save_calls == 0
    assert reflection.save_calls == 0
    assert fact.save_calls == 0


@pytest.mark.asyncio
async def test_worker_exits_when_load_fails():
    """Service constructor said is_available, but request_load returns
    False (model file missing). Worker must exit, not loop forever."""
    service = _FakeService(available=False, disabled=False)
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    task = w.start()
    await asyncio.wait_for(task, timeout=1.0)
    assert service.load_called is True
    assert persona.save_calls == 0


@pytest.mark.asyncio
async def test_warmup_unblocks_on_first_process_signal():
    """The wait must end on notify_first_process even if the
    warmup_delay hasn't elapsed — the test uses a 60s delay so the
    only way the load runs in time is via the signal."""
    service = _FakeService(available=True)
    # Park the loop after one sweep by stubbing the sweep to set
    # stop_event so the test can complete deterministically.
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
        warmup_delay=60.0,
    )

    # Patch out the sweep and stop after first call so the loop terminates.
    sweep_calls = {"n": 0}

    async def _stub_sweep():
        sweep_calls["n"] += 1
        w._stop_event.set()
        return 0

    w._sweep_once = _stub_sweep  # type: ignore
    task = w.start()
    await asyncio.sleep(0.05)
    assert service.load_called is False  # warmup_delay not elapsed yet
    w.notify_first_process()
    await asyncio.wait_for(task, timeout=1.0)
    assert service.load_called is True
    assert sweep_calls["n"] >= 1


@pytest.mark.asyncio
async def test_sweep_embeds_persona_reflection_and_facts_in_place():
    """End-to-end: seed entries into all three stores with embedding=None,
    run one sweep, assert every entry got the embedding triple stamped
    AND that the model_id matches the service's id (no cross-cache leak)."""
    service = _FakeService(
        available=True, model_id="fake-128d-int8",
        vector_factory=lambda text: [float(len(text))] * 4,
    )
    persona = _PersonaStub()
    persona.store["小天"] = {
        "master": {
            "facts": [
                {"text": "主人喜欢猫", "embedding": None,
                 "embedding_text_sha256": None, "embedding_model_id": None},
                {"text": "主人住东京", "embedding": None,
                 "embedding_text_sha256": None, "embedding_model_id": None},
            ],
        },
    }
    reflection = _ReflectionStub()
    reflection.store["小天"] = [
        {"id": "r1", "text": "reflection one", "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None},
    ]
    fact = _FactStub()
    fact.store["小天"] = [
        {"id": "f1", "text": "raw fact", "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None},
    ]
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    processed = await w._sweep_once()
    assert processed == 4

    persona_facts = persona.store["小天"]["master"]["facts"]
    for entry in persona_facts:
        assert entry["embedding"] == [float(len(entry["text"]))] * 4
        assert entry["embedding_model_id"] == "fake-128d-int8"
        assert entry["embedding_text_sha256"] == _embedding_text_sha256(entry["text"])
    assert reflection.store["小天"][0]["embedding"] is not None
    assert reflection.store["小天"][0]["embedding_model_id"] == "fake-128d-int8"
    assert fact.store["小天"][0]["embedding"] is not None
    assert persona.save_calls == 1
    assert reflection.save_calls == 1
    assert fact.save_calls == 1


@pytest.mark.asyncio
async def test_sweep_skips_entries_with_valid_cache():
    """Cache hit path: text + sha + model_id all match the running
    service ⇒ entry is NOT re-embedded. Exercises the contract that
    lets the worker run on every poll without burning CPU."""
    service = _FakeService(
        available=True, model_id="fake-128d-int8",
    )
    persona = _PersonaStub()
    text = "stable text"
    persona.store["小天"] = {
        "master": {
            "facts": [
                {
                    "text": text,
                    "embedding": [0.9] * 4,
                    "embedding_text_sha256": _embedding_text_sha256(text),
                    "embedding_model_id": "fake-128d-int8",
                },
            ],
        },
    }
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    processed = await w._sweep_once()
    assert processed == 0
    assert service.embed_batch_calls == []
    # save must not be called when nothing was embedded — that would
    # rewrite persona.json on every tick, racing with real writers.
    assert persona.save_calls == 0


@pytest.mark.asyncio
async def test_sweep_re_embeds_when_model_id_flipped():
    """Same text + valid sha but a different model_id ⇒ stale cache;
    must re-embed under the new id. Mirrors the dim/quant flip case."""
    service = _FakeService(
        available=True, model_id="fake-256d-fp32",
    )
    persona = _PersonaStub()
    text = "stable text"
    persona.store["小天"] = {
        "master": {
            "facts": [
                {
                    "text": text,
                    "embedding": [0.9] * 4,
                    "embedding_text_sha256": _embedding_text_sha256(text),
                    "embedding_model_id": "fake-128d-int8",
                },
            ],
        },
    }
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    processed = await w._sweep_once()
    assert processed == 1
    entry = persona.store["小天"]["master"]["facts"][0]
    assert entry["embedding_model_id"] == "fake-256d-fp32"


@pytest.mark.asyncio
async def test_sweep_respects_per_tick_budget(monkeypatch):
    """A backlog larger than MAX_ENTRIES_PER_TICK must yield after
    spending the budget. Remaining work picks up on the next sweep."""
    service = _FakeService(available=True, model_id="fake-128d-int8")
    persona = _PersonaStub()
    persona.store["小天"] = {
        "master": {
            "facts": [
                {"text": f"fact {i}", "embedding": None,
                 "embedding_text_sha256": None, "embedding_model_id": None}
                for i in range(MAX_ENTRIES_PER_TICK + 10)
            ],
        },
    }
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    processed = await w._sweep_once()
    # Budget bounds the per-tick work; the persona sweep alone should
    # consume the whole budget here.
    assert processed == MAX_ENTRIES_PER_TICK
    # The remaining 10 entries are still None — proves the budget was
    # actually enforced rather than the worker accidentally embedding
    # everything in one go.
    remaining_none = sum(
        1 for e in persona.store["小天"]["master"]["facts"]
        if e["embedding"] is None
    )
    assert remaining_none == 10


@pytest.mark.asyncio
async def test_sweep_handles_empty_text_entries_gracefully():
    """Entries with empty text shouldn't be queued for embedding (no
    point) and shouldn't crash the sweep."""
    service = _FakeService(available=True, model_id="fake-128d-int8")
    persona = _PersonaStub()
    persona.store["小天"] = {
        "master": {
            "facts": [
                {"text": "", "embedding": None,
                 "embedding_text_sha256": None, "embedding_model_id": None},
                {"text": "valid", "embedding": None,
                 "embedding_text_sha256": None, "embedding_model_id": None},
            ],
        },
    }
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    processed = await w._sweep_once()
    assert processed == 1
    assert persona.store["小天"]["master"]["facts"][1]["embedding"] is not None
    assert persona.store["小天"]["master"]["facts"][0]["embedding"] is None


@pytest.mark.asyncio
async def test_notify_first_process_idempotent():
    """Multiple notifications collapse to a single set — second call
    must be a no-op (event.set is idempotent but we want to be explicit
    about the contract for callers)."""
    service = _FakeService(available=True, model_id="fake-128d-int8")
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    w.notify_first_process()
    w.notify_first_process()
    w.notify_first_process()
    assert w._first_process_event.is_set()


@pytest.mark.asyncio
async def test_fact_sweep_forwards_dedup_candidates_when_resolver_present():
    """End-to-end integration with FactDedupResolver: after the worker
    embeds new fact rows, it should call aenqueue_candidates with the
    cosine-collision pairs detected against the rest of the pool.
    Confirms the wiring step 2 added in memory_server.py — without
    this, the dedup queue stays empty even with vectors enabled."""
    service = _FakeService(
        available=True, model_id="fake-128d-int8",
        # Force every embedding to be the same vector so cosine = 1.0
        # for every pair, guaranteed above the 0.85 threshold.
        vector_factory=lambda text: [1.0, 0.0, 0.0, 0.0],
    )
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    fact.store["小天"] = [
        # Pre-seeded existing row with a vector under the SAME model_id
        # — this is the cosine-collision target.
        {"id": "e1", "text": "主人喜欢猫", "entity": "master",
         "embedding": [1.0, 0.0, 0.0, 0.0],
         "embedding_text_sha256": _embedding_text_sha256("主人喜欢猫"),
         "embedding_model_id": "fake-128d-int8",
         "absorbed": False},
        # New row needing embedding — worker fills it in this sweep
        # and then detects the collision against e1.
        {"id": "c1", "text": "对猫咪很感兴趣", "entity": "master",
         "embedding": None, "embedding_text_sha256": None,
         "embedding_model_id": None, "absorbed": False},
    ]
    dedup = _DedupResolverStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
        dedup_resolver=dedup,
    )
    processed = await w._sweep_once()
    assert processed >= 1
    # Worker must have called the resolver for the freshly-embedded row.
    assert dedup.calls, "worker did not forward dedup candidates"
    name, pairs = dedup.calls[0]
    assert name == "小天"
    assert any(p["candidate_id"] == "c1" and p["existing_id"] == "e1" for p in pairs)


@pytest.mark.asyncio
async def test_fact_sweep_skips_dedup_when_resolver_is_none():
    """Without a resolver attached, the worker still embeds fact rows
    but never calls into the dedup path. This preserves the legacy
    hash-only dedup behaviour for installations that haven't enabled
    the LLM arbitration loop yet."""
    service = _FakeService(available=True, model_id="fake-128d-int8")
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    fact.store["小天"] = [
        {"id": "c1", "text": "x", "entity": "master",
         "embedding": None, "embedding_text_sha256": None,
         "embedding_model_id": None, "absorbed": False},
    ]
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
        dedup_resolver=None,
    )
    processed = await w._sweep_once()
    # Embedding still happened; no dedup machinery to crash on.
    assert processed >= 1
    assert fact.store["小天"][0]["embedding"] is not None


@pytest.mark.asyncio
async def test_stop_during_warmup_skips_load():
    """Shutdown while still in warmup wait → don't trigger the model
    load. Catches the case where the FastAPI shutdown hook fires before
    the user has had a chance to /process."""
    service = _FakeService(available=True, model_id="fake-128d-int8")
    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FactStub()
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
        warmup_delay=60.0,
    )
    task = w.start()
    await asyncio.sleep(0.02)
    await w.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert service.load_called is False


@pytest.mark.asyncio
async def test_live_getters_observe_reload_swap():
    """Regression for /reload (CodeRabbit PR #956): a sweep after
    swapping the underlying manager+character list must hit the NEW
    instances, not the snapshot captured at construction time. Locks
    in the worker exiting any "snapshot persona_manager / fact_store
    in __init__" regression."""
    service = _FakeService(available=True, model_id="fake-128d-int8")
    state = {
        "persona": _PersonaStub(),
        "reflection": _ReflectionStub(),
        "fact": _FactStub(),
        "names": ["小天"],
    }
    state["fact"].store["小天"] = [
        {"text": "pre-reload", "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None}
    ]
    w = EmbeddingWarmupWorker(
        get_persona_manager=lambda: state["persona"],
        get_reflection_engine=lambda: state["reflection"],
        get_fact_store=lambda: state["fact"],
        get_character_names=lambda: list(state["names"]),
        warmup_delay_seconds=0.01,
    )
    w._service = service

    processed = await w._sweep_once()
    assert processed == 1
    assert state["fact"].save_calls == 1
    assert state["fact"].store["小天"][0]["embedding"] is not None

    # Simulate /reload: build fresh stubs + a new character, swap them
    # through the same dict the getters close over.
    new_persona = _PersonaStub()
    new_reflection = _ReflectionStub()
    new_fact = _FactStub()
    new_fact.store["小蓝"] = [
        {"text": "post-reload", "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None}
    ]
    state["persona"] = new_persona
    state["reflection"] = new_reflection
    state["fact"] = new_fact
    state["names"] = ["小蓝"]

    processed = await w._sweep_once()
    assert processed == 1
    # New instance saw the work — proves live-getter resolution.
    assert new_fact.save_calls == 1
    assert new_fact.store["小蓝"][0]["embedding"] is not None


@pytest.mark.asyncio
async def test_save_failure_returns_zero_to_avoid_hot_loop():
    """Regression: a sweep that successfully embedded N entries but
    failed to persist them must report 0 progress, not N — otherwise
    the no-sleep saturated-budget branch in _run() hot-loops on a
    persistent disk error (full / RO / permission)."""
    service = _FakeService(available=True, model_id="fake-128d-int8")

    class _FailingFact(_FactStub):
        async def asave_facts(self, name: str) -> None:
            self.save_calls += 1
            raise OSError("disk full")

    persona = _PersonaStub()
    reflection = _ReflectionStub()
    fact = _FailingFact()
    fact.store["小天"] = [
        {"text": f"fact {i}", "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None}
        for i in range(3)
    ]
    w = _build_worker(
        service=service, persona=persona, reflection=reflection, fact=fact,
    )
    processed = await w._sweep_once()
    # Embedding ran (in-memory stamps applied), but the save raised —
    # progress accounting must reflect 0 so the outer loop backs off.
    assert fact.save_calls == 1
    assert processed == 0


@pytest.mark.asyncio
async def test_dedup_resolver_observed_via_live_getter():
    """Regression for the resolver's /reload swap: reload_memory_components
    rebuilds fact_dedup_resolver against the new FactStore, but the
    embedding worker has to look up the current resolver per sweep —
    otherwise post-reload enqueue still hits the OLD resolver while
    idle-maintenance's resolve runs against the NEW one, racing on the
    same facts_pending_dedup.json without a shared lock.

    This test pins the getter pattern; if someone reverts to a snapshot
    on `__init__`, the second sweep would still call the old resolver
    and the assertion on `new_resolver.calls` would fail."""
    service = _FakeService(available=True, model_id="fake-128d-int8")

    persona = _PersonaStub()
    reflection = _ReflectionStub()
    state = {
        "fact": _FactStub(),
        "resolver": _DedupResolverStub(),
    }
    # Two pre-existing facts with embeddings + one fresh fact — the
    # detect_candidates pass needs an embedded neighbour to flag the
    # fresh one as a paraphrase candidate. Cosine >= 0.9 threshold;
    # identical 4-vec gives cosine 1.0.
    base_emb = [0.5, 0.5, 0.5, 0.5]
    state["fact"].store["小天"] = [
        {"id": "fact-old", "entity": "user", "text": "old",
         "embedding": list(base_emb), "embedding_text_sha256": "x",
         "embedding_model_id": "fake-128d-int8", "absorbed": False},
        {"id": "fact-new", "entity": "user", "text": "old paraphrase",
         "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None,
         "absorbed": False},
    ]
    w = EmbeddingWarmupWorker(
        get_persona_manager=lambda: persona,
        get_reflection_engine=lambda: reflection,
        get_fact_store=lambda: state["fact"],
        get_character_names=lambda: ["小天"],
        warmup_delay_seconds=0.01,
        get_dedup_resolver=lambda: state["resolver"],
    )
    w._service = service
    # First sweep: original resolver receives the enqueue.
    await w._sweep_once()
    assert len(state["resolver"].calls) >= 1
    original_resolver = state["resolver"]

    # Simulate /reload: new fact store + new resolver, queued candidate.
    new_fact = _FactStub()
    new_fact.store["小天"] = [
        {"id": "fact-a", "entity": "user", "text": "alpha",
         "embedding": list(base_emb), "embedding_text_sha256": "y",
         "embedding_model_id": "fake-128d-int8", "absorbed": False},
        {"id": "fact-b", "entity": "user", "text": "alpha rephrased",
         "embedding": None,
         "embedding_text_sha256": None, "embedding_model_id": None,
         "absorbed": False},
    ]
    new_resolver = _DedupResolverStub()
    state["fact"] = new_fact
    state["resolver"] = new_resolver

    await w._sweep_once()
    # The NEW resolver got the enqueue, not the old one.
    assert len(new_resolver.calls) >= 1
    assert len(original_resolver.calls) == 1  # unchanged after reload

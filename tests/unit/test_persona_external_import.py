from __future__ import annotations

import asyncio

import pytest

from memory.evidence import initial_reinforcement_from_importance
from memory.persona import fusion as fusion_module
from memory.persona.facts import FactsMixin
from memory.persona.fusion import ExternalFusionMixin, ExternalMemoryFusionError
from utils.tokenize import count_tokens


class _FusionHarness(ExternalFusionMixin, FactsMixin):
    """Minimal PersonaManager stand-in for exercising afuse_external_facts.

    The fusion LLM (`_allm_call_fusion`) is stubbed with a deterministic return
    so the 3-phase locked/unlocked flow can be tested without a real model call.
    """

    def __init__(self, persona: dict, stub_fused):
        self.persona = persona
        self.lock = asyncio.Lock()
        self.save_count = 0
        self.llm_call_count = 0
        self._stub_fused = stub_fused
        # Records the candidate list handed to each _allm_call_fusion call, so
        # tests can assert what got folded (existing bucket + new candidates).
        self.fusion_inputs: list[list[dict]] = []
        # Optional hook fired inside _allm_call_fusion (Phase 2, unlocked) to
        # simulate a concurrent import mutating the bucket before Phase 3's CAS.
        self.fusion_side_effect = None

    def _get_alock(self, _name: str) -> asyncio.Lock:
        return self.lock

    async def _aensure_persona_locked(self, _name: str) -> dict:
        return self.persona

    async def asave_persona(self, _name: str, _persona: dict) -> None:
        self.save_count += 1

    async def _allm_call_fusion(self, _name, _entity, _candidates, _budget):
        # Deterministic stub — no real LLM. Returns whatever the test configured
        # (a list of {text, importance}) or None to simulate terminal failure.
        self.llm_call_count += 1
        self.fusion_inputs.append([dict(c) for c in _candidates])
        if self.fusion_side_effect is not None:
            self.fusion_side_effect()
        if self._stub_fused is None:
            return None
        # Return a fresh copy so callers can mutate freely.
        return [dict(item) for item in self._stub_fused]


def _candidates(*texts: str) -> list[dict]:
    return [
        {
            "text": t,
            "entity": "master",
            "source_file": "USER.md",
            "source_section": "About",
            "event_date": None,
        }
        for t in texts
    ]


@pytest.mark.asyncio
async def test_first_fusion_persists_with_importance_reinforcement():
    harness = _FusionHarness(
        {"master": {"facts": []}},
        stub_fused=[
            {"text": "Master lives in Osaka and works as a teacher.", "importance": 10},
            {"text": "Master enjoys long walks at night.", "importance": 7},
        ],
    )

    result = await harness.afuse_external_facts(
        "Neko", "master", _candidates("raw a", "raw b"), "openclaw",
    )

    assert result["fused"] is True
    assert result["added"] == 2
    assert result["skipped"] == 0
    assert harness.save_count == 1
    assert harness.llm_call_count == 1

    facts = harness.persona["master"]["facts"]
    assert len(facts) == 2
    assert all(f["source"] == "external_import" for f in facts)
    # reinforcement seeded from importance: 10 -> 5.0, 7 -> 3.5
    by_text = {f["text"]: f for f in facts}
    assert by_text["Master lives in Osaka and works as a teacher."]["reinforcement"] == (
        initial_reinforcement_from_importance(10)
    )
    assert by_text["Master enjoys long walks at night."]["reinforcement"] == (
        initial_reinforcement_from_importance(7)
    )
    # provenance metadata stamped for idempotent re-import
    assert all("fusion_fingerprint" in f["external_import"] for f in facts)


@pytest.mark.asyncio
async def test_reimport_same_candidates_is_idempotent_skip_no_llm():
    harness = _FusionHarness(
        {"master": {"facts": []}},
        stub_fused=[{"text": "A fused impression.", "importance": 8}],
    )
    cands = _candidates("raw a", "raw b")

    first = await harness.afuse_external_facts("Neko", "master", cands, "openclaw")
    assert first["added"] == 1
    assert harness.llm_call_count == 1

    # Same candidate set → fingerprint hit → whole batch skipped, LLM not called.
    second = await harness.afuse_external_facts("Neko", "master", cands, "openclaw")
    assert second == {"added": 0, "skipped": len(cands), "fused": False}
    assert harness.llm_call_count == 1  # unchanged
    # persisted entries untouched
    assert len(harness.persona["master"]["facts"]) == 1


@pytest.mark.asyncio
async def test_budget_top_n_truncation(monkeypatch):
    # Shrink the master budget so the greedy accumulator has to drop entries.
    monkeypatch.setitem(fusion_module._ENTITY_BUDGET, "master", 12)
    stub = [
        {"text": f"Distinct impression number {i} about the master's habits.", "importance": 10 - i}
        for i in range(8)
    ]
    harness = _FusionHarness({"master": {"facts": []}}, stub_fused=stub)

    result = await harness.afuse_external_facts(
        "Neko", "master", _candidates("raw"), "openclaw",
    )

    facts = harness.persona["master"]["facts"]
    assert result["added"] == len(facts)
    # Truncation actually happened (fewer than the 8 the LLM produced) ...
    assert 0 < len(facts) < len(stub)
    # ... and the kept entries fit the (shrunk) budget.
    total_tokens = sum(count_tokens(f["text"]) for f in facts)
    assert total_tokens <= 12


@pytest.mark.asyncio
async def test_fusion_only_evicts_external_import_entries():
    persona = {
        "master": {
            "facts": [
                {"text": "card fact", "source": "character_card", "protected": True, "id": "card_1"},
                {"text": "reflection fact", "source": "reflection", "id": "prom_x"},
                {
                    "text": "old external fact",
                    "source": "external_import",
                    "id": "ext_old",
                    "external_import": {"fusion_fingerprint": "OLD_DIFFERENT_FP"},
                },
            ],
        },
    }
    harness = _FusionHarness(
        persona,
        stub_fused=[{"text": "new fused impression", "importance": 9}],
    )

    result = await harness.afuse_external_facts(
        "Neko", "master", _candidates("raw a"), "openclaw",
    )
    assert result["fused"] is True

    facts = harness.persona["master"]["facts"]
    texts = {f["text"] for f in facts}
    # protected card + reflection survive untouched
    assert "card fact" in texts
    assert "reflection fact" in texts
    # stale external_import entry evicted, new fused entry added
    assert "old external fact" not in texts
    assert "new fused impression" in texts
    new_entry = next(f for f in facts if f["text"] == "new fused impression")
    assert new_entry["source"] == "external_import"


@pytest.mark.asyncio
async def test_llm_failure_raises_fusion_error():
    harness = _FusionHarness({"master": {"facts": []}}, stub_fused=None)

    with pytest.raises(ExternalMemoryFusionError):
        await harness.afuse_external_facts(
            "Neko", "master", _candidates("raw a"), "openclaw",
        )
    # Nothing persisted on terminal failure.
    assert harness.save_count == 0
    assert harness.persona["master"]["facts"] == []


@pytest.mark.asyncio
async def test_second_source_folds_existing_bucket_and_accumulates():
    # A different source (different candidate set) must accumulate into the bucket
    # by re-fusing the existing digest together with the new candidates, not
    # clobber it. The fusion LLM sees both, so cross-source duplicates merge.
    harness = _FusionHarness(
        {"master": {"facts": []}},
        stub_fused=[{"text": "Digest after fold.", "importance": 8}],
    )
    cands_a = _candidates("a1", "a2")
    cands_b = _candidates("b1", "b2")
    a_fp = ExternalFusionMixin._fusion_fingerprint(cands_a)
    b_fp = ExternalFusionMixin._fusion_fingerprint(cands_b)

    await harness.afuse_external_facts("Neko", "master", cands_a, "openclaw")
    assert harness.llm_call_count == 1
    assert harness.persona["master"]["facts"][0]["external_import"][
        "folded_fingerprints"
    ] == [a_fp]

    await harness.afuse_external_facts("Neko", "master", cands_b, "hermes")
    assert harness.llm_call_count == 2

    # The 2nd fusion folded BOTH the existing digest text AND the new candidates.
    second_list = [c["text"] for c in harness.fusion_inputs[1]]
    second_input = set(second_list)
    assert "Digest after fold." in second_input   # existing bucket carried in
    assert {"b1", "b2"} <= second_input           # new source's candidates
    # Existing digest is fed BEFORE the new candidates so tail-truncation on a
    # large re-fold can never drop the already-accumulated persona (Codex P2).
    assert second_list.index("Digest after fold.") < min(
        second_list.index("b1"), second_list.index("b2"),
    )

    # Bucket rewritten to the merged digest; folded set names both sources now.
    facts = harness.persona["master"]["facts"]
    assert all(f["source"] == "external_import" for f in facts)
    assert set(facts[0]["external_import"]["folded_fingerprints"]) == {a_fp, b_fp}


@pytest.mark.asyncio
async def test_reimport_any_folded_source_is_idempotent_skip():
    # After two sources are folded in, re-importing EITHER one (unchanged) is a
    # strict no-op — no LLM call, bucket untouched — even though the bucket also
    # holds the other source's material.
    harness = _FusionHarness(
        {"master": {"facts": []}},
        stub_fused=[{"text": "Digest.", "importance": 8}],
    )
    cands_a = _candidates("a1", "a2")
    cands_b = _candidates("b1", "b2")
    await harness.afuse_external_facts("Neko", "master", cands_a, "openclaw")
    await harness.afuse_external_facts("Neko", "master", cands_b, "hermes")
    assert harness.llm_call_count == 2

    r_a = await harness.afuse_external_facts("Neko", "master", cands_a, "openclaw")
    r_b = await harness.afuse_external_facts("Neko", "master", cands_b, "hermes")
    assert r_a["fused"] is False and r_b["fused"] is False
    assert harness.llm_call_count == 2  # no further LLM calls
    assert len(harness.persona["master"]["facts"]) == 1  # bucket unchanged


@pytest.mark.asyncio
async def test_concurrent_import_change_triggers_cas_and_preserves_state():
    # A concurrent import that lands while the (unlocked) fusion LLM runs changes
    # the bucket's folded set. Phase 3's CAS must detect that its Phase-1 snapshot
    # went stale and bail retriably instead of clobbering the other import.
    persona = {"master": {"facts": []}}
    harness = _FusionHarness(persona, stub_fused=[{"text": "my digest", "importance": 5}])

    def inject_concurrent_import():
        persona["master"]["facts"].append({
            "text": "concurrently imported",
            "source": "external_import",
            "id": "concurrent",
            "external_import": {"folded_fingerprints": ["CONCURRENT_FP"]},
        })

    harness.fusion_side_effect = inject_concurrent_import

    with pytest.raises(ExternalMemoryFusionError):
        await harness.afuse_external_facts(
            "Neko", "master", _candidates("x"), "openclaw",
        )
    # The concurrently-added entry survives; our stale digest was never written.
    texts = {f["text"] for f in persona["master"]["facts"]}
    assert "concurrently imported" in texts
    assert "my digest" not in texts
    assert harness.save_count == 0


@pytest.mark.asyncio
async def test_fusion_llm_client_construction_failure_returns_none():
    # A bad correction-model config / client construction must be converted to
    # None (→ the caller raises ExternalMemoryFusionError → external_import_partial),
    # not propagate as a raw exception that bypasses the partial-import contract
    # and surfaces as a generic 500 on the second entity (Codex P2).
    class _FailingConfigManager:
        async def aget_character_data(self):
            return (None, None, None, None, {}, None, None, None, None)

        def get_model_api_config(self, _tier):
            raise RuntimeError("invalid correction model config")

    class _CallHarness(ExternalFusionMixin):
        def __init__(self):
            self._config_manager = _FailingConfigManager()

    result = await _CallHarness()._allm_call_fusion(
        "Neko", "master", [{"text": "some candidate"}], 600,
    )
    assert result is None


@pytest.mark.asyncio
async def test_fusion_llm_close_failure_does_not_mask_result(monkeypatch):
    # A failure while closing the client is cleanup noise; it must not replace the
    # parsed result with a raw exception that propagates past
    # ExternalMemoryFusionError into a generic 500 (Codex P2).
    class _FakeResp:
        content = '[{"text": "fused", "importance": 7}]'

    class _FakeLLM:
        async def ainvoke(self, _prompt):
            return _FakeResp()

        async def aclose(self):
            raise RuntimeError("close boom")

    async def _fake_create(*_a, **_k):
        return _FakeLLM()

    monkeypatch.setattr("utils.llm_client.create_chat_llm_async", _fake_create)

    class _CfgMgr:
        async def aget_character_data(self):
            return (None, None, None, None, {}, None, None, None, None)

        def get_model_api_config(self, _tier):
            return {"model": "m", "base_url": "u", "api_key": "k", "provider_type": None}

    class _CallHarness(ExternalFusionMixin):
        def __init__(self):
            self._config_manager = _CfgMgr()

    result = await _CallHarness()._allm_call_fusion(
        "Neko", "master", [{"text": "cand"}], 600,
    )
    # close failure swallowed; the parsed result survives
    assert result == [{"text": "fused", "importance": 7}]


@pytest.mark.asyncio
async def test_fused_duplicate_texts_are_deduped_keeping_highest_importance():
    # An LLM that repeats a line must not mint two persona entries sharing one
    # timestamp+text-hash id (Codex P2): _trim_fused_to_budget dedups by
    # normalized text, keeping the highest importance.
    harness = _FusionHarness(
        {"master": {"facts": []}},
        stub_fused=[
            {"text": "Master lives in Osaka.", "importance": 6},
            {"text": "master lives in osaka.", "importance": 9},   # dup (case/space)
            {"text": "Master enjoys tea.", "importance": 5},
        ],
    )
    await harness.afuse_external_facts("Neko", "master", _candidates("raw"), "openclaw")

    facts = harness.persona["master"]["facts"]
    osaka = [f for f in facts if "osaka" in f["text"].casefold()]
    assert len(osaka) == 1  # the repeated line collapsed to one entry
    assert osaka[0]["reinforcement"] == initial_reinforcement_from_importance(9)
    assert any(f["text"] == "Master enjoys tea." for f in facts)

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

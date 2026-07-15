from __future__ import annotations

import pytest

from memory.facts import FactStore


class _DailyHarness(FactStore):
    """Minimal FactStore stand-in exercising aimport_external_daily.

    The Stage-1 extraction LLM (_allm_extract_facts) and persistence
    (_apersist_new_facts) are stubbed so the per-day grouping / event_date
    stamping / best-effort skipping can be tested without a model or DB.
    """

    def __init__(self, stub):
        # Deliberately skip FactStore.__init__ — the method under test only
        # touches the two stubbed helpers below (plus pure helpers it imports).
        self._stub = stub                       # journal_text -> list[dict] | None
        self.extract_inputs: list[str] = []
        self.persisted: list[list[dict]] = []

    async def _allm_extract_facts(self, lanlan_name, messages):
        text = "\n".join(getattr(m, "content", "") for m in messages)
        self.extract_inputs.append(text)
        return self._stub(text)

    async def _apersist_new_facts(
        self, lanlan_name, extracted, *,
        default_source="user_observation", semantic_dedup=True,
    ):
        # Pretend every extracted fact is new; capture what got stamped.
        self.persisted.append([dict(f) for f in extracted])
        return list(extracted)


def _daily(source_file, event_date, *texts):
    return [
        {"text": t, "source_file": source_file, "source_section": "", "event_date": event_date}
        for t in texts
    ]


@pytest.mark.asyncio
async def test_daily_grouped_by_day_and_event_date_stamped():
    def stub(journal):
        return [{"text": f"fact from: {journal[:20]}", "importance": 6}]

    harness = _DailyHarness(stub)
    candidates = (
        _daily("memories/2026-07-12.md", "2026-07-12", "woke early", "shipped fix")
        + _daily("memories/2026-07-13.md", "2026-07-13", "reviewed PRs")
    )

    result = await harness.aimport_external_daily(
        "Neko", candidates, "hermes", "2026-07-15T00:00:00",
    )

    assert result == {"added": 2, "days": 2, "failed_days": 0}
    # One LLM call per day; same-day fragments joined into one journal turn.
    assert len(harness.extract_inputs) == 2
    assert "woke early\nshipped fix" in harness.extract_inputs
    # Every persisted fact carries its own day's event_date + daily provenance.
    all_persisted = [f for batch in harness.persisted for f in batch]
    assert {f["_external_import"]["event_date"] for f in all_persisted} == {
        "2026-07-12", "2026-07-13",
    }
    assert all(f["_external_import"]["section"] == "daily" for f in all_persisted)
    assert all(f["_external_import"]["format"] == "hermes" for f in all_persisted)


@pytest.mark.asyncio
async def test_daily_extraction_failure_is_best_effort_skipped():
    def stub(journal):
        return None if "boom" in journal else [{"text": "ok fact", "importance": 5}]

    harness = _DailyHarness(stub)
    candidates = (
        _daily("memories/2026-07-12.md", "2026-07-12", "boom")    # this day fails
        + _daily("memories/2026-07-13.md", "2026-07-13", "good")  # this day succeeds
    )

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")

    assert result == {"added": 1, "days": 2, "failed_days": 1}
    persisted = [f for batch in harness.persisted for f in batch]
    assert len(persisted) == 1
    assert persisted[0]["_external_import"]["event_date"] == "2026-07-13"


@pytest.mark.asyncio
async def test_daily_empty_extraction_adds_nothing_and_is_not_a_failure():
    harness = _DailyHarness(lambda journal: [])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual")

    result = await harness.aimport_external_daily("Neko", candidates, "openclaw", "t")

    assert result == {"added": 0, "days": 1, "failed_days": 0}
    assert harness.persisted == []

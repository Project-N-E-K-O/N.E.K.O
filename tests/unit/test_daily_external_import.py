from __future__ import annotations

import pytest

from memory.facts import FactStore


class _DailyHarness(FactStore):
    """Minimal FactStore stand-in exercising aimport_external_daily.

    The Stage-1 extraction LLM (_allm_extract_facts), persistence
    (_apersist_new_facts) and the fingerprint source (aload_facts) are stubbed
    so per-day grouping / event_date stamping / best-effort skipping /
    re-import idempotency can be tested without a model or DB.
    """

    def __init__(self, stub):
        super().__init__()
        self._stub = stub                       # journal_text -> list[dict] | None
        self.extract_inputs: list[str] = []
        self.persisted: list[list[dict]] = []
        self.store: list[dict] = []             # simulated on-disk facts (active)
        self.archive: list[dict] = []           # simulated facts_archive.json
        self.state_fps: set[str] = set()        # simulated sidecar (external_import_state.json)

    async def aload_facts(self, lanlan_name):
        return self.store

    async def aload_facts_full(self, lanlan_name):
        # Mirror real load_facts_full: active + archive, including archived-day provenance.
        return self.store + self.archive

    # Stub sidecar I/O only; _arecord_unpersisted_day_fp's has_carrier check,
    # locks, and best-effort wrappers use real implementations, with state_fps as disk.
    def _load_external_import_state(self, name):
        return {"daily": {"imported_day_fingerprints": sorted(self.state_fps)}}

    def _record_imported_day_fp_locked(self, name, fingerprint):
        self.state_fps.add(fingerprint)

    def _clear_day_fps_locked(self, name, fingerprints):
        self.state_fps -= fingerprints

    async def _allm_extract_facts(
        self, lanlan_name, messages, *, treat_malformed_as_failure=False,
    ):
        text = "\n".join(getattr(m, "content", "") for m in messages)
        self.extract_inputs.append(text)
        result = self._stub(text)
        # Faithfully simulate _allm_extract_facts nonarray handling: strict daily -> None,
        # malformed days fail/retry; nonstrict -> [], tolerated by conversation paths.
        if result is not None and not isinstance(result, list):
            return None if treat_malformed_as_failure else []
        return result

    async def _apersist_new_facts(
        self, lanlan_name, extracted, *,
        default_source="user_observation", semantic_dedup=True,
    ):
        # Pretend every extracted fact is new; capture what got stamped and
        # mirror provenance into the simulated store (so re-import sees the
        # day_fingerprint exactly like the real persistence path).
        self.persisted.append([dict(f) for f in extracted])
        for fact in extracted:
            entry = dict(fact)
            meta = fact.get("_external_import")
            if isinstance(meta, dict):
                entry["external_import"] = dict(meta)
            self.store.append(entry)
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

    assert result == {"added": 2, "days": 2, "failed_days": 0, "skipped_days": 0}
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

    assert result == {"added": 1, "days": 2, "failed_days": 1, "skipped_days": 0}
    persisted = [f for batch in harness.persisted for f in batch]
    assert len(persisted) == 1
    assert persisted[0]["_external_import"]["event_date"] == "2026-07-13"


@pytest.mark.asyncio
async def test_daily_empty_extraction_adds_nothing_and_is_not_a_failure():
    harness = _DailyHarness(lambda journal: [])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual")

    result = await harness.aimport_external_daily("Neko", candidates, "openclaw", "t")

    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert harness.persisted == []


@pytest.mark.asyncio
async def test_daily_reimport_of_unchanged_day_skips_llm():
    # Per-day fingerprint idempotence mirrors persona folded_fingerprints: unchanged days
    # skip entirely with zero LLM calls on journal reimports (Codex P2).
    def stub(journal):
        return [{"text": "extracted fact", "importance": 6}]

    harness = _DailyHarness(stub)
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "went hiking")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 1, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 1

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    # Reimport invokes no new LLM calls.
    assert len(harness.extract_inputs) == 1


@pytest.mark.asyncio
async def test_daily_oversized_day_is_split_into_batches_not_truncated():
    # Days exceeding extraction input ceilings split into batches, one LLM call each,
    # never truncate and lose later entries (Greptile P1).
    def stub(journal):
        return [{"text": f"fact-{len(journal)}", "importance": 5}]

    harness = _DailyHarness(stub)
    # "word " is approximately one token; 7,000 words exceed EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS(6000).
    long_journal = "word " * 7000
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", long_journal)

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")

    assert result["failed_days"] == 0
    assert len(harness.extract_inputs) >= 2  # Batch rather than truncate.
    # Recombined input covers the whole journal without tail loss.
    total_words = sum(len(t.split()) for t in harness.extract_inputs)
    assert total_words == 7000
    # Every batch's facts for one day share event_date.
    persisted = [f for batch in harness.persisted for f in batch]
    assert {f["_external_import"]["event_date"] for f in persisted} == {"2026-07-12"}


@pytest.mark.asyncio
async def test_daily_new_day_count_over_cap_raises_too_large():
    from config import EXTERNAL_IMPORT_DAILY_MAX_FILES
    from memory.persona.fusion import ExternalMemoryImportTooLargeError

    harness = _DailyHarness(lambda journal: [])
    candidates = []
    for i in range(EXTERNAL_IMPORT_DAILY_MAX_FILES + 1):
        candidates += _daily(
            f"memories/2026-01-{i:02d}.md", "2026-01-01", f"journal {i}"
        )

    with pytest.raises(ExternalMemoryImportTooLargeError):
        await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    # Apply caps before any LLM calls.
    assert harness.extract_inputs == []


# ── Daily dedup keys include event_date; real _apersist_new_facts, stub storage/FTS ──


class _FakeTimeIndexed:
    """asearch_similar_facts returns preconfigured hits; index is a no-op."""

    def __init__(self, hits):
        self._hits = hits  # list[(fact_id, overlap)]

    async def asearch_similar_facts(self, lanlan_name, text, limit):
        return self._hits

    async def aindex_fact(self, lanlan_name, fact_id, text):
        return None


class _PersistHarness(FactStore):
    """Real _apersist_new_facts over an in-memory store (no disk, no model)."""

    def __init__(self, time_indexed=None):
        super().__init__(time_indexed_memory=time_indexed)
        self._mem: list[dict] = []

    async def aload_facts(self, lanlan_name):
        return self._mem

    async def asave_facts(self, lanlan_name):
        return None


def _daily_fact(text, event_date):
    return {
        "text": text, "importance": 6, "entity": "master",
        "_external_import": {
            "format": "hermes", "file": f"memories/{event_date}.md",
            "section": "daily", "event_date": event_date,
            "imported_at": "t", "day_fingerprint": "fp-" + event_date,
        },
    }


@pytest.mark.asyncio
async def test_same_text_on_different_days_both_persist():
    # Exact key = event_date + text: consecutive days with 去了健身房 both persist, each retaining
    # provenance, never suppressing one another for identical text (CodeRabbit).
    harness = _PersistHarness()
    first = await harness._apersist_new_facts(
        "Neko", [_daily_fact("went to the gym", "2026-07-12")], semantic_dedup=False,
    )
    second = await harness._apersist_new_facts(
        "Neko", [_daily_fact("went to the gym", "2026-07-13")], semantic_dedup=False,
    )
    assert len(first) == 1 and len(second) == 1
    dates = {f["external_import"]["event_date"] for f in harness._mem}
    assert dates == {"2026-07-12", "2026-07-13"}


@pytest.mark.asyncio
async def test_same_text_same_day_retry_is_idempotent():
    harness = _PersistHarness()
    first = await harness._apersist_new_facts(
        "Neko", [_daily_fact("went to the gym", "2026-07-12")], semantic_dedup=False,
    )
    retry = await harness._apersist_new_facts(
        "Neko", [_daily_fact("went to the gym", "2026-07-12")], semantic_dedup=False,
    )
    assert len(first) == 1 and len(retry) == 0
    assert len(harness._mem) == 1


@pytest.mark.asyncio
async def test_fts_dedup_exempts_cross_date_daily_hits():
    # Existing approximate FTS5 daily facts from **different dates** are exempt, persisting recurring events
    # separately; same-date approximations still block unstable reextraction retries for idempotence.
    harness = _PersistHarness()
    await harness._apersist_new_facts(
        "Neko", [_daily_fact("morning workout at the gym", "2026-07-12")],
        semantic_dedup=False,
    )
    existing_id = harness._mem[0]["id"]
    # Legacy rows may truly lack hashes, excluding them from stage-1 exact
    # sets; stage 2 alone blocks same-date paraphrases.
    harness._mem[0].pop("hash", None)
    harness._time_indexed = _FakeTimeIndexed([(existing_id, 1.0)])

    cross_date = await harness._apersist_new_facts(
        "Neko", [_daily_fact("workout at the gym in the morning", "2026-07-13")],
        semantic_dedup=True,
    )
    assert len(cross_date) == 1  # Different dates: exempt, persist.

    same_date = await harness._apersist_new_facts(
        "Neko", [_daily_fact("morning workout at the gym", "2026-07-12")],
        semantic_dedup=True,
    )
    assert len(same_date) == 0  # Same-date approximation remains duplicate.


@pytest.mark.asyncio
async def test_multi_batch_day_with_failed_batch_persists_nothing_and_retries_fully():
    # Any batch failure atomically abandons the entire day: persist no batches/fingerprints, retrying all
    # extraction from scratch. Otherwise early batches persist whole-day fingerprints, causing retries to skip the failed
    # batches forever (Greptile P1).
    calls = {"n": 0}

    def stub(journal):
        calls["n"] += 1
        if calls["n"] == 2:
            return None  # First pass's second batch fails.
        return [{"text": f"fact#{calls['n']}", "importance": 5}]

    harness = _DailyHarness(stub)
    long_journal = "word " * 7000  # Over 6,000 tokens, split into two batches.
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", long_journal)

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first["failed_days"] == 1
    assert harness.persisted == []          # Entire day unpersisted.
    assert harness.store == []              # No leftover fingerprints.
    assert harness.state_fps == set()       # No sidecar fingerprints either.

    retry = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert retry["failed_days"] == 0
    assert retry["skipped_days"] == 0       # Not wrongly fingerprint-skipped.
    assert retry["added"] == 2              # Both batches reextract successfully.


@pytest.mark.asyncio
async def test_reordered_journal_changes_fingerprint_and_reextracts():
    # Journals are narratives: reorderings such as stopping/restarting medication change meaning. Preserve order in fingerprints;
    # same-content reordered journals must reextract rather than skip (Greptile P1).
    harness = _DailyHarness(lambda j: [{"text": f"fact:{j[:30]}", "importance": 5}])
    day_a = _daily("memories/2026-07-12.md", "2026-07-12",
                   "stopped medication", "started medication")
    await harness.aimport_external_daily("Neko", day_a, "hermes", "t1")
    assert len(harness.extract_inputs) == 1

    day_b = _daily("memories/2026-07-12.md", "2026-07-12",
                   "started medication", "stopped medication")
    result = await harness.aimport_external_daily("Neko", day_b, "hermes", "t2")
    assert result["skipped_days"] == 0      # Reordered is not unchanged; do not skip.
    assert len(harness.extract_inputs) == 2  # LLM invoked again.


@pytest.mark.asyncio
async def test_identical_journal_text_on_new_date_is_not_skipped():
    # Include event_date in fingerprints: verbatim recurring journal entries on new dates cannot be skipped by
    # prior dates' fingerprints (Codex P2).
    harness = _DailyHarness(lambda j: [{"text": "routine fact", "importance": 5}])
    await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "gym then work"),
        "hermes", "t1",
    )
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-13.md", "2026-07-13", "gym then work"),
        "hermes", "t2",
    )
    assert result["skipped_days"] == 0
    assert len(harness.extract_inputs) == 2  # Extract next day normally.


@pytest.mark.asyncio
async def test_single_huge_day_over_batch_cap_raises_too_large(monkeypatch):
    # Cap **total extraction calls**: even one huge journal exceeding batch limits deterministically returns 413,
    # never escaping through len(pending)==1 to hit the 240s wall (Codex P2). Lower constants to avoid
    # constructing megabyte test inputs.
    from memory.persona.fusion import ExternalMemoryImportTooLargeError

    monkeypatch.setattr("memory.facts.EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS", 10)
    monkeypatch.setattr("memory.facts.EXTERNAL_IMPORT_DAILY_MAX_FILES", 3)

    harness = _DailyHarness(lambda j: [])
    long_journal = "word " * 200  # Ten tokens per batch, far above three batches.
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", long_journal)

    with pytest.raises(ExternalMemoryImportTooLargeError):
        await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    assert harness.extract_inputs == []  # Cap before any LLM call.


@pytest.mark.asyncio
async def test_concurrent_import_detected_before_persist_is_dropped():
    # Concurrent-window recheck: another request persists this fingerprint during our LLM call; reread before
    # persistence and abandon, preventing paraphrased duplicate facts bypassing exact deduplication (Codex P2).
    harness_holder = {}

    def stub(journal):
        # Simulate concurrent request persisting the same day/fingerprint during LLM invocation.
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
        h.store.append({
            "text": "someone else's phrasing", "importance": 5,
            "external_import": {"day_fingerprint": fp},
        })
        return [{"text": "my phrasing", "importance": 5}]

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )

    assert result["added"] == 0
    assert result["failed_days"] == 0
    assert harness.persisted == []  # This write abandoned.


# ── external_import_state sidecar: idempotent reimports for days lacking fact carriers, Codex P2 follow-up ──


@pytest.mark.asyncio
async def test_daily_empty_extraction_day_records_fingerprint_and_skips_reimport():
    # Empty-extraction days, where LLM finds no facts, lack fact fingerprint carriers; store processed
    # fingerprints in sidecars, giving zero-call reimports without cap consumption.
    harness = _DailyHarness(lambda journal: [])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 1
    assert len(harness.state_fps) == 1      # Empty days persist processed fingerprints too.

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1  # Zero new LLM calls on reimport.


@pytest.mark.asyncio
async def test_daily_all_deduped_day_does_not_record_sidecar_and_reextracts():
    # User-selected option B: fully deduplicated days whose facts all match same-date entries (_apersist_new_facts returns
    # []) **do not record sidecars**. Existing facts carry content with different fingerprints/outside the same
    # cloudsave rollback unit; sidecar remnants would suppress reextraction after fact rollback (Codex). Reextract on
    # every import, added=0 without data loss; only genuinely fact-free LLM output uses sidecars.
    # sidecar。
    class _AllDedupedHarness(_DailyHarness):
        async def _apersist_new_facts(
            self, lanlan_name, extracted, *,
            default_source="user_observation", semantic_dedup=True,
        ):
            self.persisted.append([dict(f) for f in extracted])
            return []  # All facts duplicate on the same date; none persist.

    harness = _AllDedupedHarness(
        lambda journal: [{"text": "already known", "importance": 5}]
    )
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "edited entry")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 1
    assert harness.state_fps == set()        # Fully deduplicated days omit sidecars.

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 2  # No fingerprint carrier: reextract on reimport, added=0 without losing data.


@pytest.mark.asyncio
async def test_successful_day_uses_provenance_not_sidecar():
    # Successful fact-bearing days **omit sidecars**: fingerprints live in fact provenance within facts'
    # cloudsave sync/rollback unit. Extra sidecars would retain fingerprints after cloud rollback of facts.json,
    # permanently suppressing day reextraction and losing memories (regression A). Reimport skips through provenance,
    # with zero new LLM calls.
    harness = _DailyHarness(lambda journal: [{"text": "fact", "importance": 5}])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "went hiking")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 1, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert harness.state_fps == set()       # Successful days never write sidecars.
    fp = harness._daily_fingerprint(["went hiking"], event_date="2026-07-12")
    assert any(
        f.get("external_import", {}).get("day_fingerprint") == fp for f in harness.store
    )                                        # Fingerprints reside in fact provenance.

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1  # Provenance skips with zero new LLM calls.


@pytest.mark.asyncio
async def test_archived_day_provenance_still_skips_reimport():
    # After _archive_absorbed moves imported facts from active into facts_archive.json,
    # reimports must still skip through archived provenance; _aload_imported_day_fps defaults
    # include_archive to cover archived days, avoiding reextraction without successful-day sidecar fallback.
    harness = _DailyHarness(lambda journal: [{"text": "fact", "importance": 5}])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "went hiking")
    await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")

    # Simulate archival: move active facts into archive.
    harness.archive.extend(harness.store)
    harness.store.clear()

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1  # Reimport after archival still skips with zero new LLM calls.


@pytest.mark.asyncio
async def test_sidecar_write_failure_does_not_fail_the_day():
    # Best-effort swallow sidecar write failures (disk full/permissions): fact-free days must not become
    # failed_day and whole-package HTTP 500 (regression B). Use real _arecord_unpersisted_day_fp,
    # making only underlying disk writes raise OSError.
    class _FailingSidecarHarness(_DailyHarness):
        # Parent harness already uses real _arecord_unpersisted_day_fp and best-effort wrapper;
        # make only underlying disk writes raise OSError.
        def _record_imported_day_fp_locked(self, name, fingerprint):
            raise OSError("disk full")

    harness = _FailingSidecarHarness(lambda journal: [])  # Empty-extraction day.
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual")

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    # Swallowed write failure leaves the day successful, added=0/not failed; next reimport reextracts.
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}


@pytest.mark.asyncio
async def test_imported_day_fps_merges_sidecar_and_fact_provenance():
    # Readers merge sidecars (new) + fact provenance (legacy imports before sidecars),
    # each carrying one day's fingerprint; both days skip on reimport with zero LLM calls.
    harness = _DailyHarness(lambda journal: [{"text": "f", "importance": 5}])
    fp_sidecar = harness._daily_fingerprint(["day one"], event_date="2026-07-12")
    fp_provenance = harness._daily_fingerprint(["day two"], event_date="2026-07-13")
    harness.state_fps.add(fp_sidecar)
    harness.store.append({
        "text": "legacy fact", "importance": 5,
        "external_import": {"day_fingerprint": fp_provenance},
    })

    result = await harness.aimport_external_daily(
        "Neko",
        _daily("memories/2026-07-12.md", "2026-07-12", "day one")
        + _daily("memories/2026-07-13.md", "2026-07-13", "day two"),
        "hermes", "t",
    )
    assert result == {"added": 0, "days": 2, "failed_days": 0, "skipped_days": 2}
    assert harness.extract_inputs == []


@pytest.mark.asyncio
async def test_concurrent_sidecar_only_does_not_suppress_real_facts():
    # Codex: concurrent-window rechecks use active fact provenance only, **not sidecars**. Concurrent sidecar-only
    # results (empty/fully deduplicated, lacking fact carriers) must not suppress this request's genuine
    # extracted facts, or fact-free sidecars erase real content.
    harness_holder = {}

    def stub(journal):
        # Simulate another request marking this day processed via sidecar only during LLM invocation.
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
        h.state_fps.add(fp)
        return [{"text": "my phrasing", "importance": 5}]  # This request extracts genuine facts.

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    assert result["added"] == 1              # Genuine facts persist, unsuppressed by sidecar-only state.
    assert len(harness.persisted) == 1


@pytest.mark.asyncio
async def test_success_clears_stale_sidecar_from_concurrent_empty_race():
    # Codex: another sidecar-only request first records the day's fingerprint; this request then persists
    # genuine facts. Clear stale sidecar fingerprints on success, maintaining fact-bearing days absent from
    # sidecars, or residual fingerprints suppress healing after facts rollback.
    harness_holder = {}

    def stub(journal):
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
        h.state_fps.add(fp)                       # Other sidecar-only request writes first.
        return [{"text": "real fact", "importance": 5}]  # This request extracts genuine facts.

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    assert result["added"] == 1
    assert harness.state_fps == set()             # Stale sidecar fingerprints cleared.


@pytest.mark.asyncio
async def test_concurrent_real_facts_before_persist_still_drops_this_write():
    # Preserve positive concurrent rechecks: another request persisted **genuine facts** with provenance for this day;
    # abandon before persistence on active-provenance hits, since paraphrases bypass exact deduplication.
    harness_holder = {}

    def stub(journal):
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
        h.store.append({
            "text": "someone else's phrasing", "importance": 5,
            "external_import": {"day_fingerprint": fp},
        })
        return [{"text": "my phrasing", "importance": 5}]

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    assert result["added"] == 0
    assert harness.persisted == []           # This write abandoned.


@pytest.mark.asyncio
async def test_stale_sidecar_healed_before_skip():
    # Pre-skip healing (CodeRabbit + Codex): sidecar fingerprints also held by fact provenance are
    # stale: fact carriers exist, so sidecars should not, due to exact-hash upgrades or failed/interrupted successful-day
    # cleanup after persistence, which skips would bypass forever. Clear initially,
    # preventing stale sidecars suppressing reextraction after facts rollback.
    harness = _DailyHarness(lambda journal: [{"text": "x", "importance": 5}])
    fp = harness._daily_fingerprint(["went hiking"], event_date="2026-07-12")
    harness.state_fps.add(fp)                     # Stale sidecar fingerprint.
    harness.store.append({                        # Fact provenance carries the day too.
        "text": "carried fact", "importance": 5,
        "external_import": {"day_fingerprint": fp},
    })

    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    # Day skips through provenance; stale sidecars heal before skipping.
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert harness.state_fps == set()             # Stale sidecars cleared.
    assert harness.extract_inputs == []           # Zero LLM calls.


@pytest.mark.asyncio
async def test_malformed_extraction_is_failed_day_not_sidecar_checkpoint():
    # Codex: malformed nonarrays such as {"facts": [...]} are model-format failures, not confirmed empty days. daily
    # uses treat_malformed_as_failure=True for retryable failed_day, **without** sidecars,
    # or empty-day checkpoints would silently skip future extraction and lose facts.
    harness = _DailyHarness(lambda journal: {"facts": ["wrapped, not a list"]})
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "some journal")

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    assert result == {"added": 0, "days": 1, "failed_days": 1, "skipped_days": 0}
    assert harness.state_fps == set()             # Malformed output never checkpoints sidecars.


@pytest.mark.asyncio
async def test_malformed_array_without_objects_is_failed_day():
    # Codex: string arrays (["Master likes tea"]) pass isinstance(list), bypassing
    # treat_malformed_as_failure, but dict filtering empties day_extracted. This is
    # schema failure, not confirmed empty extraction; fail retryably without sidecar checkpoints.
    harness = _DailyHarness(lambda journal: ["Master likes tea", "not an object"])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "some journal")

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    assert result == {"added": 0, "days": 1, "failed_days": 1, "skipped_days": 0}
    assert harness.state_fps == set()             # Object-free arrays never checkpoint.


@pytest.mark.asyncio
async def test_persist_failure_clears_concurrent_sidecar():
    # Codex: persistence exceptions also clear concurrent empty-day sidecars, which could otherwise become
    # sole carriers and permanently skip unchanged-journal retries without persistence.
    harness_holder = {}

    class _FailPersistHarness(_DailyHarness):
        async def _apersist_new_facts(
            self, lanlan_name, extracted, *,
            default_source="user_observation", semantic_dedup=True,
        ):
            # Simulate concurrent empty extraction recording sidecars, then this persistence failing.
            h = harness_holder["h"]
            fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
            h.state_fps.add(fp)
            raise RuntimeError("FTS write failed")

    harness = _FailPersistHarness(
        lambda journal: [{"text": "real fact", "importance": 5}]
    )
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    assert result["failed_days"] == 1             # Persistence failure gives a failed day, reextracting on retry.
    assert harness.state_fps == set()             # Except clears residual sidecars.


@pytest.mark.asyncio
async def test_provenance_upgraded_day_uses_provenance_not_sidecar():
    # Codex: exact-hash hits upgrade **this day's** day_fingerprint onto existing ai_disclosure
    # facts; _apersist_new_facts returns [] but fact carriers exist with this day's fingerprint.
    # Omit sidecars and skip by provenance, unlike fully deduplicated days whose current
    # fingerprint has no carrier (existing facts hold others), requiring reextraction.
    class _UpgradeHarness(_DailyHarness):
        async def _apersist_new_facts(
            self, lanlan_name, extracted, *,
            default_source="user_observation", semantic_dedup=True,
        ):
            self.persisted.append([dict(f) for f in extracted])
            # Simulate upgrades: no new facts ([]), but existing facts receive **this day's** fingerprint.
            for fact in extracted:
                meta = fact.get("_external_import")
                if isinstance(meta, dict):
                    self.store.append({
                        "text": "pre-existing ai_disclosure fact",
                        "external_import": dict(meta),
                    })
            return []

    harness = _UpgradeHarness(
        lambda journal: [{"text": "corroborated", "importance": 6}]
    )
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "user corroboration")

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert harness.state_fps == set()        # Provenance carrier exists: omit sidecars.

    # Current-day fingerprints on existing provenance skip reimports with zero new LLM calls.
    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1


@pytest.mark.asyncio
async def test_empty_day_skips_sidecar_when_concurrent_import_persisted_facts():
    # Codex: empty-extraction branches also check active provenance before sidecars. Concurrent genuine
    # fact persistence gives carriers, so empty requests must not record sidecars for fact-bearing
    # days and break rollback healing.
    harness_holder = {}

    def stub(journal):
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["nothing factual"], event_date="2026-07-12")
        h.store.append({
            "text": "concurrent real fact", "importance": 5,
            "external_import": {"day_fingerprint": fp},
        })
        return []  # This request extracts nothing.

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual"),
        "hermes", "t",
    )
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert harness.state_fps == set()        # Concurrent provenance exists: omit sidecars.


# ── Sidecar file layer: real reads/writes under temporary directories ──


class _SidecarFileHarness(FactStore):
    """Real sidecar file IO redirected into a tmp dir (no facts, no model)."""

    def __init__(self, tmp_path):
        super().__init__()
        self._tmp = tmp_path

    def _external_import_state_path(self, name):
        return str(self._tmp / f"{name}-external_import_state.json")

    async def aload_facts(self, name):
        return []

    async def aload_facts_full(self, name):
        return []


@pytest.mark.asyncio
async def test_sidecar_roundtrip_dedups_and_sorts(tmp_path):
    import json

    harness = _SidecarFileHarness(tmp_path)
    await harness._arecord_unpersisted_day_fp("Neko", "fp-b")
    await harness._arecord_unpersisted_day_fp("Neko", "fp-a")
    await harness._arecord_unpersisted_day_fp("Neko", "fp-b")  # Repeated recording is idempotent.

    assert await harness._aload_imported_day_fps("Neko") == {"fp-a", "fp-b"}
    with open(harness._external_import_state_path("Neko"), encoding="utf-8") as f:
        payload = json.load(f)
    # Mirror persona folded_fingerprints: set semantics, sorted persistence.
    assert payload["daily"]["imported_day_fingerprints"] == ["fp-a", "fp-b"]
    assert payload["version"] == 1


@pytest.mark.asyncio
async def test_sidecar_clear_removes_only_targeted_fps(tmp_path):
    import json
    import os

    # Symmetric roundtrip tests: record has dedicated file coverage; clear's intersection,
    # sorted rewrite, and absent-file early return also require real implementations, or disk-no-op clear
    # regressions pass while pre-skip healing/persist-failure/successful-day cleanup silently fail.
    harness = _SidecarFileHarness(tmp_path)
    await harness._arecord_unpersisted_day_fp("Neko", "fp-a")
    await harness._arecord_unpersisted_day_fp("Neko", "fp-b")

    # Clear only target fingerprints, including unknown supplied values, never everything indiscriminately.
    await harness._aclear_day_fps("Neko", {"fp-a", "fp-unknown"})
    with open(harness._external_import_state_path("Neko"), encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["daily"]["imported_day_fingerprints"] == ["fp-b"]

    # No intersection: file bytes unchanged.
    await harness._aclear_day_fps("Neko", {"fp-gone"})
    with open(harness._external_import_state_path("Neko"), encoding="utf-8") as f:
        assert json.load(f) == payload

    # No sidecar file: return after one stat without creating files.
    await harness._aclear_day_fps("hermes", {"fp-x"})
    assert not os.path.exists(harness._external_import_state_path("hermes"))


@pytest.mark.asyncio
async def test_sidecar_corrupt_file_degrades_to_empty_and_recovers(tmp_path):
    harness = _SidecarFileHarness(tmp_path)
    path = harness._external_import_state_path("Neko")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{corrupt")

    # Corrupt files degrade to empty sets; worst case reextract once, never abort import...
    assert await harness._aload_imported_day_fps("Neko") == set()
    # ...and next record atomically rebuilds the file.
    await harness._arecord_unpersisted_day_fp("Neko", "fp-new")
    assert await harness._aload_imported_day_fps("Neko") == {"fp-new"}


@pytest.mark.asyncio
async def test_load_facts_full_non_utf8_archive_degrades_to_active(tmp_path):
    # Codex: read paths now scan archived provenance through aload_facts_full before per-day isolation.
    # Invalid-UTF-8 archives raise UnicodeDecodeError, not JSONDecodeError/OSError, aborting
    # whole imports; load_facts_full must degrade to active-only.
    class _H(FactStore):
        def _facts_path(self, name):
            return str(tmp_path / "facts.json")

        def _facts_archive_path(self, name):
            return str(tmp_path / "facts_archive.json")

    from utils.file_utils import atomic_write_json as _awj

    _awj(str(tmp_path / "facts.json"), [{"id": "a", "text": "active"}],
         ensure_ascii=False, indent=2)
    with open(tmp_path / "facts_archive.json", "wb") as f:
        f.write(b"\xff\xfe not utf-8 \x80\x81")

    harness = _H()
    result = harness.load_facts_full("Neko")       # No exception.
    assert [f["id"] for f in result] == ["a"]      # Degrade to active-only.


@pytest.mark.asyncio
async def test_sidecar_non_utf8_file_degrades_to_empty(tmp_path):
    # Codex: invalid UTF-8 makes json.load raise UnicodeDecodeError (ValueError subclass,
    # not JSONDecodeError/OSError). _aload_imported_day_fps runs **before** per-day isolation;
    # degrade to empty sets like textual corruption instead of aborting whole imports.
    harness = _SidecarFileHarness(tmp_path)
    path = harness._external_import_state_path("Neko")
    with open(path, "wb") as f:
        f.write(b"\xff\xfe not valid utf-8 \x80\x81")

    assert await harness._aload_imported_day_fps("Neko") == set()
    # Next record atomically rebuilds files too.
    await harness._arecord_unpersisted_day_fp("Neko", "fp-new")
    assert await harness._aload_imported_day_fps("Neko") == {"fp-new"}

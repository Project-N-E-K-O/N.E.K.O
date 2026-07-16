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
        self.store: list[dict] = []             # simulated on-disk facts

    async def aload_facts(self, lanlan_name):
        return self.store

    async def _allm_extract_facts(self, lanlan_name, messages):
        text = "\n".join(getattr(m, "content", "") for m in messages)
        self.extract_inputs.append(text)
        return self._stub(text)

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
    # 逐日指纹幂等（对偶 persona folded_fingerprints）：同一份日记重导时，
    # 内容未变的天整体 skip、零 LLM 调用（Codex P2）。
    def stub(journal):
        return [{"text": "extracted fact", "importance": 6}]

    harness = _DailyHarness(stub)
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "went hiking")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 1, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 1

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    # 重导没有产生新的 LLM 调用。
    assert len(harness.extract_inputs) == 1


@pytest.mark.asyncio
async def test_daily_oversized_day_is_split_into_batches_not_truncated():
    # 超过单次抽取输入上限的一天必须拆成多个批次（每批一次 LLM 调用），
    # 而不是截断丢掉后半天（Greptile P1）。
    def stub(journal):
        return [{"text": f"fact-{len(journal)}", "importance": 5}]

    harness = _DailyHarness(stub)
    # "word " ≈ 1 token；7000 词 > EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS(6000)。
    long_journal = "word " * 7000
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", long_journal)

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")

    assert result["failed_days"] == 0
    assert len(harness.extract_inputs) >= 2  # 拆批而非截断
    # 拼回的输入覆盖了整份日记（无尾部丢失）。
    total_words = sum(len(t.split()) for t in harness.extract_inputs)
    assert total_words == 7000
    # 同一天所有批次抽出的 fact 打同一个 event_date。
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
    # cap 在任何 LLM 调用之前生效。
    assert harness.extract_inputs == []


# ── daily 去重键含 event_date（真 _apersist_new_facts，stub 存储/FTS）──


class _FakeTimeIndexed:
    """asearch_facts returns preconfigured hits; index is a no-op."""

    def __init__(self, hits):
        self._hits = hits  # list[(fact_id, score)]

    async def asearch_facts(self, lanlan_name, text, limit):
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
    # 精确去重键 = event_date + 文本：连着两天「去了健身房」都要落盘、各留
    # provenance，不因文本相同互吞（CodeRabbit）。
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
    # FTS5 近似命中的既存 fact 若是「不同日期的 daily」→ 豁免（跨日期重复事件
    # 各自落盘）；同日期近似命中仍挡（兜 LLM 重抽输出不稳定的重试幂等）。
    harness = _PersistHarness()
    await harness._apersist_new_facts(
        "Neko", [_daily_fact("morning workout at the gym", "2026-07-12")],
        semantic_dedup=False,
    )
    existing_id = harness._mem[0]["id"]
    harness._time_indexed = _FakeTimeIndexed([(existing_id, -10.0)])

    cross_date = await harness._apersist_new_facts(
        "Neko", [_daily_fact("workout at the gym in the morning", "2026-07-13")],
        semantic_dedup=True,
    )
    assert len(cross_date) == 1  # 不同日期：豁免，落盘

    same_date = await harness._apersist_new_facts(
        "Neko", [_daily_fact("gym workout in the early morning", "2026-07-12")],
        semantic_dedup=True,
    )
    assert len(same_date) == 0  # 同日期近似：仍判重复


@pytest.mark.asyncio
async def test_multi_batch_day_with_failed_batch_persists_nothing_and_retries_fully():
    # 多批天任一批失败 → 整天原子放弃（不落盘任何批、不留指纹），重试从头
    # 重抽——否则早批带全天指纹落盘后，重试被指纹整天 skip、失败批内容永久
    # 丢失（Greptile P1）。
    calls = {"n": 0}

    def stub(journal):
        calls["n"] += 1
        if calls["n"] == 2:
            return None  # 第一轮的第二批失败
        return [{"text": f"fact#{calls['n']}", "importance": 5}]

    harness = _DailyHarness(stub)
    long_journal = "word " * 7000  # > 6000 token，拆 2 批
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", long_journal)

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first["failed_days"] == 1
    assert harness.persisted == []          # 整天没落盘
    assert harness.store == []              # 无指纹残留

    retry = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert retry["failed_days"] == 0
    assert retry["skipped_days"] == 0       # 没被指纹误 skip
    assert retry["added"] == 2              # 两批都重抽成功


@pytest.mark.asyncio
async def test_reordered_journal_changes_fingerprint_and_reextracts():
    # 日记是叙事：条目重排（如「停药」「复药」互换）语义不同，指纹保序 →
    # 重排后的同内容日记必须重新抽取而非被 skip（Greptile P1）。
    harness = _DailyHarness(lambda j: [{"text": f"fact:{j[:30]}", "importance": 5}])
    day_a = _daily("memories/2026-07-12.md", "2026-07-12",
                   "stopped medication", "started medication")
    await harness.aimport_external_daily("Neko", day_a, "hermes", "t1")
    assert len(harness.extract_inputs) == 1

    day_b = _daily("memories/2026-07-12.md", "2026-07-12",
                   "started medication", "stopped medication")
    result = await harness.aimport_external_daily("Neko", day_b, "hermes", "t2")
    assert result["skipped_days"] == 0      # 重排 ≠ 未变，不能 skip
    assert len(harness.extract_inputs) == 2  # 重新走了 LLM

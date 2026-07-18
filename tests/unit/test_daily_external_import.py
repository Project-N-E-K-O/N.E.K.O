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
        # 对偶真实 load_facts_full：active + archive（含已归档天的 provenance）。
        return self.store + self.archive

    # 只 stub sidecar 文件 IO 层：_arecord_unpersisted_day_fp 的 has_carrier 检查
    # + 锁 + best-effort 包裹走真实实现（state_fps 充当磁盘）。
    def _load_external_import_state(self, name):
        return {"daily": {"imported_day_fingerprints": sorted(self.state_fps)}}

    def _record_imported_day_fp_locked(self, name, fingerprint):
        self.state_fps.add(fingerprint)

    def _clear_day_fp_locked(self, name, fingerprint):
        self.state_fps.discard(fingerprint)

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
    assert harness.state_fps == set()       # sidecar 同样无指纹残留

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


@pytest.mark.asyncio
async def test_identical_journal_text_on_new_date_is_not_skipped():
    # 指纹掺 event_date：例行日记逐字重复出现在新的一天，不能被旧日期的
    # 指纹 skip（Codex P2）。
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
    assert len(harness.extract_inputs) == 2  # 第二天照常抽取


@pytest.mark.asyncio
async def test_single_huge_day_over_batch_cap_raises_too_large(monkeypatch):
    # cap 按「总抽取调用数」：单个超大日记文件拆出的批数超限也要确定性 413，
    # 不能借 len(pending)==1 溜过去撞 240s 墙（Codex P2）。缩小常量免得测试
    # 构造兆级文本。
    from memory.persona.fusion import ExternalMemoryImportTooLargeError

    monkeypatch.setattr("memory.facts.EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS", 10)
    monkeypatch.setattr("memory.facts.EXTERNAL_IMPORT_DAILY_MAX_FILES", 3)

    harness = _DailyHarness(lambda j: [])
    long_journal = "word " * 200  # 10 token/批 → 远超 3 批
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", long_journal)

    with pytest.raises(ExternalMemoryImportTooLargeError):
        await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    assert harness.extract_inputs == []  # cap 在任何 LLM 调用前生效


@pytest.mark.asyncio
async def test_concurrent_import_detected_before_persist_is_dropped():
    # 并发缩窗重查：本请求 LLM 期间另一请求把同指纹落盘 → persist 前重读
    # 发现即放弃写入，避免措辞不同的重复 facts 绕过精确去重（Codex P2）。
    harness_holder = {}

    def stub(journal):
        # 模拟「LLM 期间」并发请求已把同一天（同指纹）落盘
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
    assert harness.persisted == []  # 本次写入被放弃


# ── external_import_state sidecar：无 fact 载体天的重导幂等（Codex P2 follow-up）──


@pytest.mark.asyncio
async def test_daily_empty_extraction_day_records_fingerprint_and_skips_reimport():
    # 空抽取天（LLM 判定该日记无 fact）没有 fact 载体存指纹——processed 指纹
    # 落 sidecar，重导零 LLM 调用、不再占 cap 配额。
    harness = _DailyHarness(lambda journal: [])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 1
    assert len(harness.state_fps) == 1      # 空抽取天也落 processed 指纹

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1  # 重导零新 LLM 调用


@pytest.mark.asyncio
async def test_daily_all_deduped_day_does_not_record_sidecar_and_reextracts():
    # 方案 B（用户拍板）：抽出的 facts 全被同日期去重命中（_apersist_new_facts 返回
    # []）的全去重天**不记 sidecar**——内容已被既有 fact 承载（指纹不同、不在同一
    # cloudsave 回滚单元），记 sidecar 会在 facts 回滚后残留压制重抽（Codex）。退回
    # 每次重导重抽（added=0、不丢数据）；只有 LLM 判定完全无 fact 的空抽取天靠
    # sidecar。
    class _AllDedupedHarness(_DailyHarness):
        async def _apersist_new_facts(
            self, lanlan_name, extracted, *,
            default_source="user_observation", semantic_dedup=True,
        ):
            self.persisted.append([dict(f) for f in extracted])
            return []  # 全部判同日期重复，一条都没落盘

    harness = _AllDedupedHarness(
        lambda journal: [{"text": "already known", "importance": 5}]
    )
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "edited entry")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 1
    assert harness.state_fps == set()        # 全去重天不落 sidecar

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert len(harness.extract_inputs) == 2  # 无指纹载体 → 重导重抽（added=0 不丢数据）


@pytest.mark.asyncio
async def test_successful_day_uses_provenance_not_sidecar():
    # 有 fact 落盘的成功天**不**记 sidecar：指纹随 fact 存进 provenance、与 facts
    # 同处 cloudsave 同步/回滚单元。若额外记 sidecar，facts.json 被云端回滚后
    # sidecar 残留指纹会永久压制该天重抽 → 记忆丢失（回归 A）。重导靠 provenance
    # skip，零新 LLM。
    harness = _DailyHarness(lambda journal: [{"text": "fact", "importance": 5}])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "went hiking")

    first = await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")
    assert first == {"added": 1, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert harness.state_fps == set()       # 成功天不写 sidecar
    fp = harness._daily_fingerprint(["went hiking"], event_date="2026-07-12")
    assert any(
        f.get("external_import", {}).get("day_fingerprint") == fp for f in harness.store
    )                                        # 指纹落在 fact provenance 里

    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1  # 靠 provenance skip，零新 LLM


@pytest.mark.asyncio
async def test_archived_day_provenance_still_skips_reimport():
    # 已导入天的 fact 被 _archive_absorbed 移进 facts_archive.json（从 active 移出）
    # 后，重导仍要靠 archive 里的 provenance skip——_aload_imported_day_fps 默认
    # include_archive 覆盖归档天，避免归档后重抽（成功天不再依赖 sidecar 兜底）。
    harness = _DailyHarness(lambda journal: [{"text": "fact", "importance": 5}])
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "went hiking")
    await harness.aimport_external_daily("Neko", candidates, "hermes", "t1")

    # 模拟归档：active 的 fact 移到 archive。
    harness.archive.extend(harness.store)
    harness.store.clear()

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1  # 归档后重导仍 skip，零新 LLM


@pytest.mark.asyncio
async def test_sidecar_write_failure_does_not_fail_the_day():
    # sidecar 写失败（磁盘满/权限）best-effort 吞掉：空抽取天不因此升级为
    # failed_day → 整包 HTTP 500（回归 B）。走真实 _arecord_unpersisted_day_fp，
    # 只让底层写盘抛 OSError。
    class _FailingSidecarHarness(_DailyHarness):
        # 父 harness 已走真实 _arecord_unpersisted_day_fp（best-effort 包裹）；这里
        # 只让底层写盘抛 OSError。
        def _record_imported_day_fp_locked(self, name, fingerprint):
            raise OSError("disk full")

    harness = _FailingSidecarHarness(lambda journal: [])  # 空抽取天
    candidates = _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual")

    result = await harness.aimport_external_daily("Neko", candidates, "hermes", "t")
    # 写失败被吞：该天仍算成功（added 0、非 failed），退回下次重导重抽。
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}


@pytest.mark.asyncio
async def test_imported_day_fps_merges_sidecar_and_fact_provenance():
    # 读取侧合并双源：sidecar（新）+ fact provenance（sidecar 之前导入的存量，
    # 向后兼容）——两边各持一天的指纹，重导两天都 skip、零 LLM 调用。
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
    # Codex：并发缩窗重查只认 active fact provenance、**不查 sidecar**。对方并发
    # 的 sidecar-only（空抽取/全去重、无 fact 载体）结果不能挤掉本请求已抽出的
    # 真实 facts——否则「无 fact 的 sidecar」压掉真实抽取内容。
    harness_holder = {}

    def stub(journal):
        # 模拟「LLM 期间」对方并发请求把同一天以 sidecar-only 形式标记 processed
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
        h.state_fps.add(fp)
        return [{"text": "my phrasing", "importance": 5}]  # 本请求抽出真实 facts

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    assert result["added"] == 1              # 真实 facts 落盘，不被 sidecar-only 挤掉
    assert len(harness.persisted) == 1


@pytest.mark.asyncio
async def test_success_clears_stale_sidecar_from_concurrent_empty_race():
    # Codex：并发下对方 sidecar-only（空抽取/全去重）先写这天指纹，本请求随后抽出
    # 真实 facts 落盘 → 成功天要清掉陈旧 sidecar 指纹，维持「有 fact 载体的天不在
    # sidecar」不变式（否则 facts 回滚后残留指纹压制自愈）。
    harness_holder = {}

    def stub(journal):
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["went hiking"], event_date="2026-07-12")
        h.state_fps.add(fp)                       # 对方 sidecar-only 先写
        return [{"text": "real fact", "importance": 5}]  # 本请求抽出真实 facts

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "went hiking"),
        "hermes", "t",
    )
    assert result["added"] == 1
    assert harness.state_fps == set()             # 陈旧 sidecar 指纹被清


@pytest.mark.asyncio
async def test_concurrent_real_facts_before_persist_still_drops_this_write():
    # 并发重查的正路径保留：对方并发已把这天**真实 facts**（有 provenance）落盘，
    # 本请求 persist 前查 active provenance 命中即放弃（措辞不同的重复绕过精确去重）。
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
    assert harness.persisted == []           # 本次写入被放弃


@pytest.mark.asyncio
async def test_provenance_upgraded_day_uses_provenance_not_sidecar():
    # Codex：exact-hash 命中把**本天** day_fingerprint upgrade 到既有 ai_disclosure
    # fact 时，_apersist_new_facts 返回 [] 但这天有 fact 载体（本天指纹打在既有 fact
    # 上）。此天不记 sidecar、靠 provenance skip——与「全去重天」相反：全去重天的本天
    # 指纹谁都没有（既有 fact 带别的指纹）→ 无载体、重导重抽。
    class _UpgradeHarness(_DailyHarness):
        async def _apersist_new_facts(
            self, lanlan_name, extracted, *,
            default_source="user_observation", semantic_dedup=True,
        ):
            self.persisted.append([dict(f) for f in extracted])
            # 模拟 upgrade：不新增 fact（返回 []），但把**本天**指纹打到既有 fact 上。
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
    assert harness.state_fps == set()        # 有 provenance 载体 → 不记 sidecar

    # 本天指纹在既有 fact 的 provenance 上 → 重导靠 provenance skip、零新 LLM。
    second = await harness.aimport_external_daily("Neko", candidates, "hermes", "t2")
    assert second == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 1}
    assert len(harness.extract_inputs) == 1


@pytest.mark.asyncio
async def test_empty_day_skips_sidecar_when_concurrent_import_persisted_facts():
    # Codex：空抽取分支写 sidecar 前也要查 active provenance。若并发对方已把这天
    # 真实 facts 落盘（有载体），本空抽取请求不该再记 sidecar（那会给「现在有 fact」
    # 的天留 sidecar、破坏回滚自愈）。
    harness_holder = {}

    def stub(journal):
        h = harness_holder["h"]
        fp = h._daily_fingerprint(["nothing factual"], event_date="2026-07-12")
        h.store.append({
            "text": "concurrent real fact", "importance": 5,
            "external_import": {"day_fingerprint": fp},
        })
        return []  # 本请求空抽取

    harness = _DailyHarness(stub)
    harness_holder["h"] = harness
    result = await harness.aimport_external_daily(
        "Neko", _daily("memories/2026-07-12.md", "2026-07-12", "nothing factual"),
        "hermes", "t",
    )
    assert result == {"added": 0, "days": 1, "failed_days": 0, "skipped_days": 0}
    assert harness.state_fps == set()        # 并发对方已落 provenance → 不记 sidecar


# ── sidecar 文件层（真实读写，tmp 目录）──


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
    await harness._arecord_unpersisted_day_fp("Neko", "fp-b")  # 重复 record 幂等

    assert await harness._aload_imported_day_fps("Neko") == {"fp-a", "fp-b"}
    with open(harness._external_import_state_path("Neko"), encoding="utf-8") as f:
        payload = json.load(f)
    # 对偶 persona folded_fingerprints：集合语义、sorted 落盘。
    assert payload["daily"]["imported_day_fingerprints"] == ["fp-a", "fp-b"]
    assert payload["version"] == 1


@pytest.mark.asyncio
async def test_sidecar_corrupt_file_degrades_to_empty_and_recovers(tmp_path):
    harness = _SidecarFileHarness(tmp_path)
    path = harness._external_import_state_path("Neko")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{corrupt")

    # 损坏文件降级为空集（最坏重抽一遍，不炸导入）……
    assert await harness._aload_imported_day_fps("Neko") == set()
    # ……且下一次 record 原子重建文件。
    await harness._arecord_unpersisted_day_fp("Neko", "fp-new")
    assert await harness._aload_imported_day_fps("Neko") == {"fp-new"}


@pytest.mark.asyncio
async def test_load_facts_full_non_utf8_archive_degrades_to_active(tmp_path):
    # Codex：读路径现经 aload_facts_full 扫 archive provenance（在 per-day 隔离前）。
    # 非法 UTF-8 archive 抛 UnicodeDecodeError（非 JSONDecodeError/OSError）会 abort
    # 整个导入 → load_facts_full 必须把它降级为仅 active。
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
    result = harness.load_facts_full("Neko")       # 不抛
    assert [f["id"] for f in result] == ["a"]      # 降级仅 active


@pytest.mark.asyncio
async def test_sidecar_non_utf8_file_degrades_to_empty(tmp_path):
    # Codex：非法 UTF-8 字节让 json.load 抛 UnicodeDecodeError（ValueError 子类、
    # 非 JSONDecodeError/OSError）。_aload_imported_day_fps 在 per-day 隔离**前**
    # 跑，不降级会 abort 整个导入——必须降级为空集，与文本损坏同等对待。
    harness = _SidecarFileHarness(tmp_path)
    path = harness._external_import_state_path("Neko")
    with open(path, "wb") as f:
        f.write(b"\xff\xfe not valid utf-8 \x80\x81")

    assert await harness._aload_imported_day_fps("Neko") == set()
    # 且下一次 record 原子重建文件。
    await harness._arecord_unpersisted_day_fp("Neko", "fp-new")
    assert await harness._aload_imported_day_fps("Neko") == {"fp-new"}

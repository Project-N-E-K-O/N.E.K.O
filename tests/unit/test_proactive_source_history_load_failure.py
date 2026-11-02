"""A failed source-history load must never be mistaken for a loaded one."""

# _record_source_used overwrites the full file: entries is the entire _source_history. Thus
# whether memory currently represents disk is a safety property. A failed read leaves memory empty;
# marking it loaded would let the next record truncate all disk history to one entry.
# proactive_source_history.json records previously used material; truncating it makes her repeat
# recent topics. All cases assert actual disk content rather than substituting internal flags.

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from main_logic.proactive_chat import state
from utils.file_utils import read_json as _real_read_json


@pytest.fixture(autouse=True)
def _isolate_source_history_globals():
    """Restore module-level history globals so cases cannot leak into each other."""
    saved_history = dict(state._source_history)
    saved_loaded = state._source_history_loaded
    saved_path = state._source_history_loaded_path
    saved_skipped = state._source_history_skipped_records
    saved_failures = state._source_history_read_failures
    saved_authoritative = state._source_history_authoritative
    # Several cases actually contend on _source_history_lock. asyncio.Lock binds via _get_loop()
    # only under contention and remains bound to that loop. pytest-asyncio gives each case a new loop;
    # binding the module lock here makes later concurrent tests fail with
    # "bound to a different event loop". Replace it for the whole case, then restore it; tested code
    # looks up the state module's global name and still uses the same real critical section.
    saved_lock = state._source_history_lock
    state._source_history_lock = asyncio.Lock()
    state._source_history.clear()
    state._source_history_loaded = False
    state._source_history_loaded_path = None
    state._source_history_skipped_records = 0
    state._source_history_read_failures = 0
    state._source_history_authoritative = False
    yield
    state._source_history.clear()
    state._source_history.update(saved_history)
    state._source_history_loaded = saved_loaded
    state._source_history_loaded_path = saved_path
    state._source_history_skipped_records = saved_skipped
    state._source_history_read_failures = saved_failures
    state._source_history_authoritative = saved_authoritative
    state._source_history_lock = saved_lock


def _history_path(root: Path) -> Path:
    return root / state._SOURCE_HISTORY_FILENAME


def _seed_history(root: Path, count: int) -> dict[str, dict]:
    """Write *count* fresh entries to disk and return them."""
    # ts=now gives _source_skip_probability=1.0, preventing both load-time forgetting and
    # _record_source_used pruning, so assertions that nothing is lost are meaningful.
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    entries = {
        f"seedhash{index:02d}": {"ts": now, "kind": "web", "title": f"seed-{index}"}
        for index in range(count)
    }
    _history_path(root).write_text(
        json.dumps(
            {"v": state._SOURCE_HISTORY_SCHEMA_VERSION, "entries": entries},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return entries


def _disk_entries(root: Path) -> dict[str, dict]:
    return json.loads(_history_path(root).read_text(encoding="utf-8"))["entries"]


@pytest.mark.asyncio
async def test_transient_read_failure_never_truncates_disk_history(
    monkeypatch, tmp_path
) -> None:
    """One unreadable read must not let the next record overwrite the file."""
    # Main regression: transient Windows antivirus/indexer locks reproduce #2528-family failures; one
    # PermissionError on read made the next recording truncate history to one entry.
    root = tmp_path / "memory"
    seeded = _seed_history(root, 7)

    def exploding_read(path):
        raise PermissionError(13, "file used by another process")

    monkeypatch.setattr(state, "read_json", exploding_read)

    await state._record_source_used(
        url="https://example.test/fresh",
        kind="web",
        title="fresh",
        memory_dir=root,
    )

    # Assert verbatim equality rather than len>=1, catching both truncation to one entry and blind appending,
    # which still means overwrite occurred while memory happened not to be empty this round.
    assert _disk_entries(root) == seeded
    assert state._source_history_loaded is False, "读失败不许被标记成已加载"
    assert state._source_history_loaded_path is None


@pytest.mark.asyncio
async def test_missing_file_is_a_normal_empty_history(tmp_path) -> None:
    """A file that never existed is an ordinary empty history, not a failure."""
    # Preserve FileNotFoundError behavior: first launch / cleared caches legitimately lack the file;
    # mark loaded and record normally or the first entry can never be written.
    root = tmp_path / "memory"
    root.mkdir()
    assert not _history_path(root).exists()

    assert await state._ensure_source_history_loaded(memory_dir=root) is True
    assert state._source_history_loaded is True
    assert state._source_history_loaded_path == _history_path(root)

    url = "https://example.test/first"
    await state._record_source_used(
        url=url, kind="web", title="first", memory_dir=root
    )

    assert set(_disk_entries(root)) == {state._source_hash(url, "first")}


@pytest.mark.asyncio
async def test_recording_without_an_explicit_load_keeps_disk_history_intact(
    tmp_path,
) -> None:
    """_record_source_used must load by itself, not rely on the caller's line order."""
    # Guardrail: previously only handle_proactive_chat statement order guaranteed loading before recording
    # (load at 53, record at 115/170, separated by thousands of lines). Record here without preloading.
    root = tmp_path / "memory"
    seeded = _seed_history(root, 7)

    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root
    )

    after = _disk_entries(root)
    assert set(after) == set(seeded) | {state._source_hash(url, "fresh")}
    assert len(after) == 8


@pytest.mark.asyncio
async def test_records_resume_once_the_read_recovers(monkeypatch, tmp_path) -> None:
    """Skipping one record must not degrade into never recording again."""
    # Skipping unreadable files must be temporary: retry load every call and resume recording when disk recovers.
    root = tmp_path / "memory"
    seeded = _seed_history(root, 7)
    failing = {"now": True}

    def flaky_read(path):
        if failing["now"]:
            raise OSError(5, "access denied")
        return _real_read_json(path)

    monkeypatch.setattr(state, "read_json", flaky_read)

    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root
    )
    assert _disk_entries(root) == seeded, "读失败这一轮不该动盘"

    failing["now"] = False
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root
    )

    after = _disk_entries(root)
    assert set(after) == set(seeded) | {state._source_hash(url, "fresh")}


@pytest.mark.asyncio
async def test_corrupt_content_starts_empty_and_self_heals(tmp_path) -> None:
    """Unparseable content has nothing worth protecting, so recording must go on."""
    # Separate corrupt content from temporary unreadability: corrupt content never succeeds on retry;
    # treating it as retryable would halt recording permanently. Allow empty history and rebuild by overwrite.
    root = tmp_path / "memory"
    root.mkdir()
    _history_path(root).write_text("{ this is not json", encoding="utf-8")

    assert await state._ensure_source_history_loaded(memory_dir=root) is True

    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root
    )

    assert set(_disk_entries(root)) == {state._source_hash(url, "fresh")}


@pytest.mark.asyncio
async def test_failed_reload_leaves_the_previous_root_fully_intact(
    monkeypatch, tmp_path
) -> None:
    """A failed reload of another root must not disturb the root already in memory."""
    # Another route to the same truncation: read failure while changing roots. Parse into a local dictionary;
    # failed loads touch no globals, leaving flag / path / memory consistently describing root_a.
    # This is stronger than merely clearing a flag: it neither overwrites root_a from empty memory nor discards
    # already loaded root_a history merely because root_b had one read failure.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    seeded_a = _seed_history(root_a, 7)
    root_b.mkdir()

    await state._ensure_source_history_loaded(memory_dir=root_a)
    assert set(state._source_history) == set(seeded_a)

    def exploding_read(path):
        raise OSError(5, "access denied")

    monkeypatch.setattr(state, "read_json", exploding_read)

    assert await state._ensure_source_history_loaded(memory_dir=root_b) is False
    # Assert all three individually: changing any causes root_a's next record to write incorrect content.
    assert state._source_history_loaded is True
    assert state._source_history_loaded_path == _history_path(root_a)
    assert set(state._source_history) == set(seeded_a)

    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root_a
    )

    # root_a recording continues normally from its own memory, preserving all entries and appending normally.
    after = _disk_entries(root_a)
    assert set(after) == set(seeded_a) | {state._source_hash(url, "fresh")}
    assert {key: after[key] for key in seeded_a} == seeded_a
    assert not _history_path(root_b).exists(), "读失败的那个根不许被写出文件"


@pytest.mark.asyncio
async def test_cancelling_a_load_leaves_no_poisoned_state(monkeypatch, tmp_path) -> None:
    """A cancelled load must not leave an empty memory wearing the previous root's flag."""
    # CancelledError is BaseException, caught by neither except; the to_thread read is this function's
    # only suspension point. Previously _source_history.clear() preceded try, so cancellation left
    # flag=True / path=root_a / empty memory. A later healthy record(root_a) then truncated
    # seven entries to one. Parsing into a local dictionary makes cancellation change nothing.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    seeded_a = _seed_history(root_a, 7)
    _seed_history(root_b, 3)

    await state._ensure_source_history_loaded(memory_dir=root_a)
    assert set(state._source_history) == set(seeded_a)

    read_started = threading.Event()
    release_read = threading.Event()

    def blocking_read(path):
        read_started.set()
        assert release_read.wait(5), "读线程没被放行"
        return _real_read_json(path)

    monkeypatch.setattr(state, "read_json", blocking_read)

    task = asyncio.create_task(state._ensure_source_history_loaded(memory_dir=root_b))
    await asyncio.to_thread(read_started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Release the reader thread and await actual completion so it cannot leak into other tests.
    release_read.set()
    await asyncio.sleep(0)

    assert state._source_history_loaded is True
    assert state._source_history_loaded_path == _history_path(root_a)
    assert set(state._source_history) == set(seeded_a), "取消不许把内存清空"

    monkeypatch.setattr(state, "read_json", _real_read_json)
    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root_a
    )

    after = _disk_entries(root_a)
    assert set(after) == set(seeded_a) | {state._source_hash(url, "fresh")}
    assert len(after) == 8


@pytest.mark.asyncio
async def test_one_unparseable_entry_does_not_take_the_rest_down(tmp_path) -> None:
    """A single damaged entry must be skipped, not turned into a whole-file wipe."""
    # Partial corruption, one bad item among hundreds, is more common than wholly invalid JSON. If float(ts)'s
    # ValueError or _half_life_for(kind)'s TypeError for unhashable kinds reaches
    # the corrupt-content branch and clears memory, the next overwrite erases valid disk entries too.
    root = tmp_path / "memory"
    seeded = _seed_history(root, 7)
    raw = json.loads(_history_path(root).read_text(encoding="utf-8"))
    raw["entries"]["seedhash03"]["ts"] = "not-a-number"
    raw["entries"]["seedhash05"]["kind"] = ["unhashable"]
    _history_path(root).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert await state._ensure_source_history_loaded(memory_dir=root) is True
    survivors = set(seeded) - {"seedhash03", "seedhash05"}
    assert set(state._source_history) == survivors

    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root
    )

    # Verbatim equality: forget the two invalid entries and retain all five valid ones; old behavior retained only the new entry.
    assert set(_disk_entries(root)) == survivors | {state._source_hash(url, "fresh")}


@pytest.mark.asyncio
async def test_root_swapped_between_load_and_write_aborts_the_record(
    monkeypatch, tmp_path
) -> None:
    """Another root swapping the memory mid-flight must abort the overwrite, not ride it."""
    # The guard runs outside the lock (_ensure acquires it), while overwrite holds it, leaving a window: _ensure
    # awaits I/O under lock, then another root's _ensure can acquire it before this recorder and replace memory
    # entirely. Without rechecking under lock, root_a's file gets root_b's content.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    seeded_a = _seed_history(root_a, 7)
    seeded_b = _seed_history(root_b, 3)

    a_read_started = threading.Event()
    release_a_read = threading.Event()

    def controlled_read(path):
        if path == _history_path(root_a):
            a_read_started.set()
            assert release_a_read.wait(5), "root_a 的读线程没被放行"
        return _real_read_json(path)

    monkeypatch.setattr(state, "read_json", controlled_read)

    url = "https://example.test/fresh"
    recorder = asyncio.create_task(
        state._record_source_used(
            url=url, kind="web", title="fresh", memory_dir=root_a
        )
    )
    # Wait for recorder's _ensure(root_a) to hold the critical section and block on disk read.
    await asyncio.to_thread(a_read_started.wait, 5)

    swapper = asyncio.create_task(
        state._ensure_source_history_loaded(memory_dir=root_b)
    )
    # Run swapper until it queues for _source_history_lock before recorder queues its write;
    # asyncio.Lock is FIFO, so this ordering reproduces the race.
    for _ in range(5):
        await asyncio.sleep(0)

    release_a_read.set()
    assert await swapper is True
    await recorder

    # Memory now belongs to root_b, so abandon root_a's overwrite.
    assert state._source_history_loaded_path == _history_path(root_b)
    assert _disk_entries(root_a) == seeded_a, "root_a 的历史不许被别的根的内容覆盖"
    assert _disk_entries(root_b) == seeded_b, "root_b 只是被加载，不该被写"
    assert state._source_hash(url, "fresh") not in _disk_entries(root_a)


@pytest.mark.asyncio
async def test_permanent_read_failure_logs_at_a_throttled_rate(
    monkeypatch, tmp_path
) -> None:
    """A permanently unreadable history must stay visible without one warning per record."""
    # Skipping unreadable files may be permanent when file read ACLs break while the directory remains writable.
    # Recording stalls, appearing as repeated topics to users. Logs are the only diagnostic, so neither
    # flood every call nor remain invisible.
    root = tmp_path / "memory"
    _seed_history(root, 7)

    def exploding_read(path):
        raise PermissionError(13, "file used by another process")

    monkeypatch.setattr(state, "read_json", exploding_read)

    warnings: list[str] = []

    class _Recorder:
        def warning(self, fmt, *args):
            warnings.append(fmt % args)

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(state, "logger", _Recorder())

    for _ in range(60):
        await state._record_source_used(
            url="https://example.test/fresh",
            kind="web",
            title="fresh",
            memory_dir=root,
        )

    skips = [line for line in warnings if "跳过本次 source 记录" in line]
    reads = [line for line in warnings if "本次不视为已加载" in line]
    # First three plus fiftieth failure equals four logs. Merely asserting <60 permits logging
    # every other call, which is still flooding.
    assert len(skips) == 4, warnings
    # Loading also repeats each call. Throttling only recording still leaves 60 load logs,
    # drowning the stalled-state signal; throttle both sides together.
    assert len(reads) == 4, warnings
    assert len(warnings) == 8, warnings
    assert "连续跳过 50 次" in skips[-1], "降频之后的日志必须带累计数，否则看不出是停摆"
    assert "连续失败 50 次" in reads[-1]

    # No disk entries lost: throttling changes logs only, preserving the choice to miss records rather than truncate history.
    assert len(_disk_entries(root)) == 7

    # Read recovery -> successful recording -> reset consecutive-skip count. A new failure must report immediately,
    # not continue the old throttle until count 100.
    monkeypatch.setattr(state, "read_json", _real_read_json)
    await state._record_source_used(
        url="https://example.test/ok", kind="web", title="ok", memory_dir=root
    )
    assert state._source_history_skipped_records == 0
    assert state._source_history_read_failures == 0

    monkeypatch.setattr(state, "read_json", exploding_read)
    state._source_history_loaded = False
    state._source_history_loaded_path = None
    warnings.clear()
    await state._record_source_used(
        url="https://example.test/again", kind="web", title="again", memory_dir=root
    )
    assert [line for line in warnings if "连续跳过 1 次" in line], warnings
    assert [line for line in warnings if "连续失败 1 次" in line], warnings


# -- Three review findings: corrupt content raises beyond ValueError / old-root history remains visible after read failure. --


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_oversized_timestamp_only_drops_that_one_entry(tmp_path):
    # float() raises OverflowError for huge integers; that inherits ArithmeticError,
    # not ValueError. Omitting it from per-entry exception handling lets it escape
    # _ensure_source_history_loaded and _record_source_used, breaking every proactive
    # chat despite per-entry skipping existing specifically for potentially corrupt files.
    root = tmp_path / "mem"
    root.mkdir()
    good = {"ts": time.time(), "kind": "web", "title": "good"}
    _history_path(root).write_text(
        json.dumps(
            {
                "v": state._SOURCE_HISTORY_SCHEMA_VERSION,
                "entries": {
                    "goodhash": good,
                    # JSON supports arbitrary-precision integers that float() cannot convert.
                    "hugehash": {"ts": int("9" * 400), "kind": "web", "title": "huge"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert await state._ensure_source_history_loaded(memory_dir=root) is True
    assert set(state._source_history) == {"goodhash"}, "坏的那条该丢，好的那条不该陪葬"
    assert state._source_history["goodhash"] == good


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_recursion_error_from_the_decoder_is_treated_as_corrupt_not_transient(
    tmp_path,
):
    # Deeply nested JSON raises RecursionError, neither ValueError nor OSError;
    # treating it as transient is worst: a permanent content failure becomes
    # retry-next-time, so _record_source_used refuses writes forever and never self-heals.
    root = tmp_path / "mem"
    root.mkdir()

    def recursing_read(path):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(state, "read_json", recursing_read)
        assert await state._ensure_source_history_loaded(memory_dir=root) is True, (
            "内容坏掉必须按空历史放行、由覆盖写自愈，不能挂在瞬时失败上"
        )
    assert state._source_history == {}
    assert state._source_history_loaded is True
    assert state._source_history_loaded_path == _history_path(root)

    # Self-healing requires the next record to actually write the file.
    await state._record_source_used(
        url="https://example.test/heal", kind="web", title="heal", memory_dir=root
    )
    written = json.loads(_history_path(root).read_text(encoding="utf-8"))
    assert list(written["entries"]) == [state._source_hash("https://example.test/heal", "heal")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_reload_hides_the_previous_root_from_readers(tmp_path):
    # Root change plus failed new-root read: return values protect writes, but memory still contains root_a history.
    # _should_skip_source reads synchronously without memory_dir, so root_b requests are suppressed
    # by root_a's already-discussed list, hiding material never mentioned to this character.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_b.mkdir()
    seeded_a = _seed_history(root_a, 3)
    hash_a = next(iter(seeded_a))

    assert await state._ensure_source_history_loaded(memory_dir=root_a) is True
    assert state._get_source_history_entry(hash_a) == seeded_a[hash_a]

    def exploding_read(path):
        raise OSError("root_b is unreadable right now")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(state, "read_json", exploding_read)
        assert await state._ensure_source_history_loaded(memory_dir=root_b) is False

    # Do not clear memory: that recreates loaded flag / root_a path / empty-memory state, making
    # root_a's next record overwrite its entire history. Instead hide stale memory from read paths.
    assert state._source_history, "内存不该被清空"
    assert state._get_source_history_entry(hash_a) is None, (
        "读失败之后，上一个根的历史不许再被读路径当权威"
    )

    # Returning to root_a through the loaded fast path must restore readability; fail-open is not one-way.
    assert await state._ensure_source_history_loaded(memory_dir=root_a) is True
    assert state._get_source_history_entry(hash_a) == seeded_a[hash_a]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_readers_stay_open_when_the_very_first_load_fails(tmp_path):
    # First-load failure leaves memory empty, so reads already find nothing; assert the direction:
    # fail open (not found -> do not suppress), never fail closed (suppress everything).
    root = tmp_path / "mem"
    root.mkdir()

    def exploding_read(path):
        raise OSError("nope")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(state, "read_json", exploding_read)
        assert await state._ensure_source_history_loaded(memory_dir=root) is False

    from main_logic.proactive_chat.decisions import _should_skip_source

    assert _should_skip_source("anyhash") is False, "读不出历史时必须放行候选，不是全压掉"

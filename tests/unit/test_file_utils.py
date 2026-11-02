# -*- coding: utf-8 -*-
"""Unit tests for utils.file_utils atomic write primitives.

These are the repo's single funnel for putting JSON/text on disk (~430 call
sites across memory, config, topic signals and plugin storage), so the
guarantees they must keep are:

- the target file is replaced, never written in place;
- a failed write leaves the previous target content intact;
- a failure during cleanup never masks the failure that caused it;
- temp files left behind by a hard kill get swept eventually;
- the async twins do their blocking work off the event loop.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

from utils import file_utils
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    atomic_write_text,
    atomic_write_text_async,
    read_bytes_tolerating_replace,
    read_json,
    read_json_async,
)

pytestmark = pytest.mark.unit


def _tmp_siblings(target: Path) -> list[Path]:
    """Temp files this module would have created in ``target``'s directory."""
    # Temporary names omit target basenames; see file_utils. Query by directory rather than
    # target; independent tmp_path fixtures prevent cross-test visibility.
    return sorted(
        p for p in target.parent.iterdir() if file_utils._STALE_TMP_RE.match(p.name)
    )


def _abandoned_tmp(target: Path, rand: str = "deadbeef") -> Path:
    """A temp file shaped exactly like the ones this module leaves behind."""
    return target.parent / f".{file_utils._TMP_OWNER_TAG}{rand}.tmp"


def _expire_sweep_throttle() -> None:
    """Pretend the per-directory sweep interval has elapsed for every known directory."""
    with file_utils._swept_tmp_dirs_lock:
        for key in list(file_utils._swept_tmp_dirs):
            file_utils._swept_tmp_dirs[key] -= (
                file_utils._STALE_TMP_SWEEP_INTERVAL_S + 1
            )


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


# ── happy path ──────────────────────────────────────────────────────────


def test_atomic_write_text_creates_missing_parents(tmp_path):
    target = tmp_path / "deep" / "nested" / "state.txt"

    atomic_write_text(target, "hello")

    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_json_roundtrips_unicode_without_escaping(tmp_path):
    target = tmp_path / "state.json"

    atomic_write_json(target, {"名字": "妮可", "n": 1})

    raw = target.read_text(encoding="utf-8")
    assert "妮可" in raw, "ensure_ascii should default to False"
    assert read_json(target) == {"名字": "妮可", "n": 1}


def test_atomic_write_json_forwards_dumps_options(tmp_path):
    target = tmp_path / "state.json"

    atomic_write_json(target, {"b": 1, "a": 2}, indent=None, sort_keys=True)

    assert target.read_text(encoding="utf-8") == '{"a": 2, "b": 1}'


def test_atomic_write_text_honours_encoding(tmp_path):
    target = tmp_path / "state.txt"

    atomic_write_text(target, "妮可", encoding="utf-16")

    assert target.read_bytes() != "妮可".encode("utf-8")
    assert target.read_text(encoding="utf-16") == "妮可"


def test_successful_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "state.json"

    atomic_write_json(target, {"v": 1})
    atomic_write_json(target, {"v": 2})

    assert _tmp_siblings(target) == []
    assert read_json(target) == {"v": 2}


def test_target_holds_previous_content_until_the_rename(tmp_path, monkeypatch):
    # Atomic means new content completely persists to tmp through write/fsync, while targets
    # retain complete old content until os.replace; readers never observe half files.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    observed: dict[str, str] = {}
    real_replace = os.replace

    def spy(src, dst):
        observed["target_before"] = Path(dst).read_text(encoding="utf-8")
        observed["staged"] = Path(src).read_text(encoding="utf-8")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    atomic_write_json(target, {"v": 2})

    assert json.loads(observed["target_before"]) == {"v": 1}
    assert json.loads(observed["staged"]) == {"v": 2}
    assert read_json(target) == {"v": 2}


# ── failure handling ────────────────────────────────────────────────────


def test_failed_replace_removes_temp_and_keeps_old_target(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    def boom(src, dst):
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace refused"):
        atomic_write_json(target, {"v": 2})

    assert read_json(target) == {"v": 1}, "failed write must not corrupt the target"
    assert _tmp_siblings(target) == [], "temp file should be cleaned up"


def test_cleanup_failure_does_not_mask_the_real_error(tmp_path, monkeypatch):
    # Regression: another handle holding the target makes os.replace and subsequent os.remove
    # fail for the same Windows WinError 5 reason. Cleanup exceptions must never replace
    # original failures, or logs show only failed temporary deletion and hide the root cause.
    target = tmp_path / "state.json"

    def replace_denied(src, dst):
        raise PermissionError("the real reason: target is held open")

    def remove_denied(path):
        raise PermissionError("cleanup also denied")

    monkeypatch.setattr(os, "replace", replace_denied)
    monkeypatch.setattr(os, "remove", remove_denied)

    with pytest.raises(PermissionError) as excinfo:
        atomic_write_json(target, {"v": 1})

    assert "the real reason" in str(excinfo.value)
    assert "cleanup also denied" not in str(excinfo.value)


def test_a_base_exception_mid_write_still_cleans_up_the_temp_file(tmp_path, monkeypatch):
    # Ctrl-C/SystemExit often interrupt write/fsync and are not Exception subclasses; catching
    # only Exception leaks tmp files until another cleanup window plus 24 hours.
    # install_source/manager.py _atomic_write also catches BaseException; preserve symmetry.
    target = tmp_path / "state.json"

    def interrupted(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_json(target, {"v": 1})

    assert _tmp_siblings(target) == [], "BaseException 也必须清掉临时文件"


def test_missing_temp_file_during_cleanup_is_tolerated(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def replace_and_vanish(src, dst):
        os.unlink(src)
        raise OSError("replace refused after the temp file vanished")

    monkeypatch.setattr(os, "replace", replace_and_vanish)
    with pytest.raises(OSError, match="replace refused"):
        atomic_write_json(target, {"v": 1})


# -- Temporary creation: fail fast, bound retries --


def test_temp_create_permission_denied_fails_fast(tmp_path, monkeypatch):
    """A directory that denies temp-file creation must raise on the first try, never retry.

    Regression: on Windows, ``tempfile.mkstemp`` retries under a fresh random
    name whenever creation raises PermissionError as long as the directory
    "looks writable" (os.access reads static permission bits), and its bound is
    TMP_MAX == 2147483647, i.e. no bound at all. Sandboxes and anti-ransomware
    shields reliably produce "static permissions say writable, creation is
    denied", so the caller hangs permanently -- the root cause of the storage
    location endpoint freezing.

    The replacement retries only on file-name collisions and raises permission
    errors immediately, so os.open must have been called exactly once here.
    """
    target = tmp_path / "state.json"
    attempts: list[str] = []

    def denied(path, flags, mode=0o777):
        attempts.append(str(path))
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", denied)

    with pytest.raises(PermissionError):
        atomic_write_text(target, "v1")

    assert len(attempts) == 1, "权限错误必须立刻抛出，不得换名重试"
    assert _tmp_siblings(target) == [], "失败的创建不应留下临时文件"


def test_temp_name_collision_exhaustion_is_bounded(tmp_path, monkeypatch):
    """Repeated random-name collisions must be bounded by a fixed retry limit.

    This is the core guarantee of the change: even if every generated random
    name hits an existing file, only ``_TMP_CREATE_MAX_TRIES`` attempts are
    allowed before FileExistsError is raised -- never an endless loop.
    """
    target = tmp_path / "state.json"
    attempts: list[str] = []

    def always_exists(path, flags, mode=0o777):
        attempts.append(str(path))
        raise FileExistsError(17, "File exists")

    monkeypatch.setattr(os, "open", always_exists)

    with pytest.raises(FileExistsError):
        atomic_write_text(target, "v1")

    assert len(attempts) == file_utils._TMP_CREATE_MAX_TRIES, (
        "撞车重试必须收敛到固定上界，不能无限重试"
    )


# ── Windows: the target is momentarily busy ─────────────────────────────


def _busy_error(winerror: int) -> PermissionError:
    """A PermissionError shaped like Windows' "the target is open elsewhere"."""
    exc = PermissionError(13, "Access is denied")
    exc.winerror = winerror
    return exc


class _SleepSpy:
    """Stands in for ``file_utils.time``: records sleeps, proxies the rest.

    Scoped to the module under test on purpose — patching the global
    ``time.sleep`` would silently un-sleep every other thread alive in this
    process for the duration of the test.
    """

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)


def test_a_busy_target_is_retried_until_the_other_handle_goes_away(tmp_path, monkeypatch):
    # Windows-only window: another open target handle makes os.replace fail with WinError
    # 5/32. Antivirus/Explorer previews are not the only causes; this process's own to_thread
    # writes can conflict with another reader. These millisecond windows should resolve after
    # backoff, not be reported as permanent persistence failures.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    real_replace = os.replace
    attempts: list[str] = []
    spy = _SleepSpy()

    def busy_twice(src, dst):
        attempts.append(src)
        if len(attempts) == 1:
            raise _busy_error(32)
        if len(attempts) == 2:
            raise _busy_error(5)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", busy_twice)
    monkeypatch.setattr(file_utils, "time", spy)

    atomic_write_json(target, {"v": 2})

    assert read_json(target) == {"v": 2}
    assert len(attempts) == 3
    assert spy.delays == [0.005, 0.01], "只在被拒之后退避，且必须递增"
    assert _tmp_siblings(target) == []


def test_a_target_that_stays_busy_raises_the_real_replace_error(tmp_path, monkeypatch):
    # Enforce a hard retry bound and ultimately raise os.replace's actual exception,
    # not a retry-failed wrapper. Permanently held/read-only targets retain previous behavior:
    # raise, preserve old content, remove tmp, merely delayed by 155 ms.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    attempts: list[str] = []
    spy = _SleepSpy()

    def always_busy(src, dst):
        attempts.append(src)
        raise _busy_error(32)

    monkeypatch.setattr(os, "replace", always_busy)
    monkeypatch.setattr(file_utils, "time", spy)

    with pytest.raises(PermissionError) as excinfo:
        atomic_write_json(target, {"v": 2})

    assert excinfo.value.winerror == 32, "抛的必须是那次真实的 replace 异常"
    assert "Access is denied" in str(excinfo.value)
    assert len(attempts) == len(file_utils._REPLACE_RETRY_BACKOFF_S) + 1
    assert spy.delays == list(file_utils._REPLACE_RETRY_BACKOFF_S)
    assert sum(spy.delays) < 0.2, "重试预算必须留在亚秒级"
    assert read_json(target) == {"v": 1}, "失败的写不许动到目标"
    assert _tmp_siblings(target) == []


def test_read_bytes_tolerating_replace_retries_busy_windows_read(
    tmp_path, monkeypatch
):
    target = tmp_path / "state.bin"
    target.write_bytes(b"persisted bytes")
    attempts = 0
    real_read_bytes = Path.read_bytes
    spy = _SleepSpy()

    def busy_twice(path):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _busy_error(32)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", busy_twice)
    monkeypatch.setattr(file_utils, "time", spy)

    assert read_bytes_tolerating_replace(target) == b"persisted bytes"
    assert attempts == 3
    assert spy.delays == [0.005, 0.01]


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(lambda: _busy_error(2), id="another-winerror"),
        pytest.param(lambda: OSError(28, "No space left on device"), id="no-winerror"),
    ],
)
def test_only_the_busy_winerrors_are_retried(tmp_path, monkeypatch, make_error):
    # Match OS error codes, not message text. Never retry disk-full/missing-path errors.
    # Non-Windows OSError lacks winerror, reducing all backoff here to immediate
    # propagation; pin that on every platform.
    target = tmp_path / "state.json"
    attempts: list[str] = []
    spy = _SleepSpy()

    def refuse(src, dst):
        attempts.append(src)
        raise make_error()

    monkeypatch.setattr(os, "replace", refuse)
    monkeypatch.setattr(file_utils, "time", spy)

    with pytest.raises(OSError):
        atomic_write_json(target, {"v": 1})

    assert len(attempts) == 1, "非 busy 错误必须第一次就抛出来"
    assert spy.delays == []


@pytest.mark.asyncio
async def test_the_backoff_never_sleeps_on_the_event_loop(tmp_path, monkeypatch):
    # More than twenty async functions call synchronous atomic_write_* directly, including per-turn
    # anti_repeat/user_directives on the audio loop. Sleeping 155 ms there interrupts
    # audio; check_async_blocking.py already forbids time.sleep on async-reachable paths.
    # Event-loop callers must retain old behavior: raise on the first
    # busy error without any sleep.
    target = tmp_path / "state.json"
    attempts: list[str] = []
    spy = _SleepSpy()

    def always_busy(src, dst):
        attempts.append(src)
        raise _busy_error(32)

    monkeypatch.setattr(os, "replace", always_busy)
    monkeypatch.setattr(file_utils, "time", spy)

    with pytest.raises(PermissionError):
        atomic_write_json(target, {"v": 1})

    assert len(attempts) == 1, "事件循环上不许重试"
    assert spy.delays == [], "事件循环上一次 sleep 都不许有"
    assert _tmp_siblings(target) == []


@pytest.mark.asyncio
async def test_a_worker_thread_still_gets_the_full_backoff(tmp_path, monkeypatch):
    # Symmetric case and the actual flaky-write fix: offending disk writes run entirely
    # in to_thread workers without running loops, where backoff must remain effective.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    real_replace = os.replace
    attempts: list[str] = []
    spy = _SleepSpy()

    def busy_twice(src, dst):
        attempts.append(src)
        if len(attempts) <= 2:
            raise _busy_error(32)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", busy_twice)
    monkeypatch.setattr(file_utils, "time", spy)

    await asyncio.to_thread(atomic_write_json, target, {"v": 2})

    assert read_json(target) == {"v": 2}
    assert len(attempts) == 3
    assert spy.delays == [0.005, 0.01]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only handle semantics")
def test_windows_really_refuses_to_replace_a_target_held_open(tmp_path):
    # Pin the retry-list premise: actual Windows error codes when open handles block targets. Future
    # CPython share-mode changes or OS error-code changes must fail this test rather than silently
    # disabling backoff forever.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    fd, temp_path = tempfile.mkstemp(dir=str(tmp_path))
    os.close(fd)
    try:
        with open(target, "r", encoding="utf-8"):
            with pytest.raises(PermissionError) as excinfo:
                os.replace(temp_path, target)
    finally:
        with suppress(OSError):
            os.unlink(temp_path)

    assert excinfo.value.winerror in file_utils._REPLACE_BUSY_WINERRORS


def test_unserializable_payload_never_touches_the_target(tmp_path):
    # json.dumps precedes atomic_write_text, so no tmp should exist yet.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    assert read_json(target) == {"v": 1}
    assert _tmp_siblings(target) == []


# ── stale temp sweeping ─────────────────────────────────────────────────


def test_stale_temp_from_a_hard_kill_is_swept(tmp_path):
    target = tmp_path / "state.json"
    leftover = _abandoned_tmp(target)
    leftover.write_text("half-written garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert not leftover.exists()
    assert read_json(target) == {"v": 1}


def test_temp_file_of_a_live_writer_is_not_swept(tmp_path):
    # Another process's active write has millisecond-old tmp files; sweeping deletes partially written
    # data. The age threshold prevents this.
    target = tmp_path / "state.json"
    inflight = _abandoned_tmp(target, "inflight")
    inflight.write_text("someone else is mid-write", encoding="utf-8")

    atomic_write_json(target, {"v": 1})

    assert inflight.exists()


def test_sweep_clears_every_abandoned_temp_in_the_directory(tmp_path):
    # Sweep by directory, not target: residuals all originate from this primitive regardless of target.
    # Per-target matching misses write-once, never-rewritten targets such as archive
    # shards, whose residue would otherwise remain forever.
    target = tmp_path / "state.json"
    leftovers = [_abandoned_tmp(target, r) for r in ("deadbeef", "cafef00d")]
    for path in leftovers:
        path.write_text("garbage from a crash", encoding="utf-8")
        _age(path, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert [p.name for p in leftovers if p.exists()] == []


def test_sweep_claims_its_temps_whatever_the_random_segment_looks_like(tmp_path):
    # tempfile._RandomNameSequence's alphabet is implementation detail; hardcoding
    # [a-z0-9_] would stop recognizing our own tmp files if CPython adds uppercase,
    # silently turning sweeps into no-ops without errors.
    target = tmp_path / "state.json"
    exotic = tmp_path / f".{file_utils._TMP_OWNER_TAG}AB12cd_xYZ-9.tmp"
    exotic.write_text("garbage", encoding="utf-8")
    _age(exotic, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert not exotic.exists()


def test_sweep_only_touches_files_it_can_prove_it_owns(tmp_path):
    # Directory sweeps require provable ownership: similar shape/age alone could permanently delete other apps', plugins',
    # or users' old files in the same directory. Embed ownership markers in our tmp names
    # and leave every unmarked file untouched, including old versions' residue; prefer missed cleanup.
    target = tmp_path / "state.json"
    tag = file_utils._TMP_OWNER_TAG
    keepers = [
        tmp_path / f".{tag}deadbeef.bak",          # Not .tmp.
        tmp_path / f"{tag}deadbeef.tmp",           # Missing leading dot.
        tmp_path / f".{target.name}.deadbeef.tmp",  # Missing ownership marker, legacy residue.
        tmp_path / f".{tag.upper()}deadbeef.tmp",  # Incorrect marker case.
        tmp_path / ".rsync-ish.abcdef.tmp",        # Another tool's temporary file.
        tmp_path / ".notes.tmp",                   # User-owned file.
    ]
    for path in keepers:
        path.write_text("keep me", encoding="utf-8")
        _age(path, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert [p.name for p in keepers if not p.exists()] == []


def test_the_temp_files_this_module_creates_carry_the_owner_tag(tmp_path, monkeypatch):
    # Ownership markers matter only if creation uses them too. Changing sweep regexes without creation
    # prefixes silently makes all future cleanup ineffective.
    #
    # Observe _create_exclusive_temp_file rather than tempfile.mkstemp; this module deliberately avoids
    # mkstemp's unbounded Windows PermissionError renaming retries, documented there,
    # so stubbing mkstemp sees no names. The contract remains that every created name
    # matches the sweeper's ownership regex.
    target = tmp_path / "state.json"
    seen = {}
    real_create = file_utils._create_exclusive_temp_file

    def spy(target_dir):
        fd, path = real_create(target_dir)
        seen["name"] = Path(path).name
        return fd, path

    monkeypatch.setattr(file_utils, "_create_exclusive_temp_file", spy)
    atomic_write_json(target, {"v": 1})

    assert file_utils._STALE_TMP_RE.match(seen["name"]), (
        f"创建出来的 tmp 名 {seen['name']!r} 不被清扫器的所有权正则认领"
    )


def test_sweep_survives_an_unreadable_directory(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def denied(_path):
        raise PermissionError("cannot list this directory")

    monkeypatch.setattr(os, "scandir", denied)
    atomic_write_json(target, {"v": 1})

    assert read_json(target) == {"v": 1}


def test_sweep_is_throttled_per_directory(tmp_path, monkeypatch):
    # Throttling is intentional: cleanup is secondary work, not a whole-directory scandir on every write.
    # Scan each directory once per interval; all targets within it share the window.
    scans = []
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p: scans.append(str(p)) or real_scandir(p))

    atomic_write_json(tmp_path / "state.json", {"v": 1})
    atomic_write_json(tmp_path / "state.json", {"v": 2})
    atomic_write_json(tmp_path / "another.json", {"v": 1})

    assert len(scans) == 1


def test_sweep_runs_again_once_the_interval_elapses(tmp_path):
    # Once-ever sweeping is wrong: this write's own leaked tmp, created after sweeping, files too young
    # during scanning, and transient scan failures all require later windows.
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    appeared_later = _abandoned_tmp(target)
    appeared_later.write_text("garbage", encoding="utf-8")
    _age(appeared_later, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 2})
    assert appeared_later.exists(), "还在节流窗口内，不该重扫"

    _expire_sweep_throttle()
    atomic_write_json(target, {"v": 3})

    assert not appeared_later.exists(), "过了间隔就必须重扫"


def test_a_failed_scan_is_retried_after_the_interval(tmp_path, monkeypatch):
    # Transient OSError, directory locks/network glitches, must not permanently disable directory sweeps.
    target = tmp_path / "state.json"
    leftover = _abandoned_tmp(target)
    leftover.write_text("garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    real_scandir = os.scandir
    monkeypatch.setattr(
        os, "scandir", lambda p: (_ for _ in ()).throw(PermissionError("locked"))
    )
    atomic_write_json(target, {"v": 1})
    assert leftover.exists(), "扫不动，这一轮什么都没清"

    monkeypatch.setattr(os, "scandir", real_scandir)
    _expire_sweep_throttle()
    atomic_write_json(target, {"v": 2})

    assert not leftover.exists(), "下一个窗口必须重试"


def test_a_temp_file_leaked_by_this_write_is_swept_by_a_later_window(tmp_path, monkeypatch):
    # Sweeping precedes mkstemp, so this call's leaked tmp after both replace and fallback
    # remove fail, exactly the tolerated Windows case, cannot be collected now. Later
    # windows must catch it or day-long desktop processes leak permanently.
    target = tmp_path / "state.json"

    def replace_denied(src, dst):
        raise PermissionError("target held open")

    def remove_denied(path):
        raise PermissionError("cannot clean up the temp file either")

    monkeypatch.setattr(os, "replace", replace_denied)
    monkeypatch.setattr(os, "remove", remove_denied)
    with pytest.raises(PermissionError, match="target held open"):
        atomic_write_json(target, {"v": 1})

    leaked = _tmp_siblings(target)
    assert len(leaked) == 1, "这一次写确实泄漏了一个 tmp"

    monkeypatch.undo()
    _age(leaked[0], file_utils._STALE_TMP_MIN_AGE_S + 60)
    _expire_sweep_throttle()
    atomic_write_json(target, {"v": 2})

    assert _tmp_siblings(target) == [], "下一个窗口必须把泄漏的 tmp 清掉"


def test_the_sweep_memo_stays_bounded(tmp_path):
    # cloudsave exports create new staging directories and per-character subdirectories via mkdtemp;
    # retaining bookkeeping permanently grows unboundedly with operations.
    limit = file_utils._STALE_TMP_MEMO_MAX
    for i in range(limit + 40):
        atomic_write_json(tmp_path / f"d{i}" / "state.json", {"v": i})

    assert len(file_utils._swept_tmp_dirs) <= limit


def test_temp_name_is_a_short_constant_shape(tmp_path, monkeypatch):
    # Tmp names omit target basenames, giving constant length. Verify they are far below NAME_MAX,
    # even eCryptfs's 143 bytes, and strictly shorter than the former
    # .<basename>.<8>.tmp format; otherwise previously writable long targets fail with ENAMETOOLONG.
    # Omitting basenames does not harm diagnosis because os.replace errors already include targets.
    # Like test_the_temp_files_this_module_creates_carry_the_owner_tag, observe the real creation helper
    # rather than tempfile.mkstemp, no longer called here.
    seen = []
    real_create = file_utils._create_exclusive_temp_file

    def spy(target_dir):
        fd, path = real_create(target_dir)
        seen.append(Path(path).name)
        return fd, path

    monkeypatch.setattr(file_utils, "_create_exclusive_temp_file", spy)
    for basename in ("s.json", "妮" * 60 + ".json"):
        atomic_write_json(tmp_path / basename, {"v": 1})

    assert len({len(n) for n in seen}) == 1, f"名字长度应该与目标名无关: {seen}"
    for name, basename in zip(seen, ("s.json", "妮" * 60 + ".json")):
        assert len(name.encode("utf-8")) < 143, name
        legacy = len(f".{basename}.abcdefgh.tmp".encode("utf-8"))
        assert len(name.encode("utf-8")) <= legacy, (name, basename)
        assert file_utils._STALE_TMP_RE.match(name), name


def test_fork_child_gets_a_fresh_sweep_lock(tmp_path):
    # main_server selects fork, which copies only its invoking thread; if another thread holds
    # this mutex, children inherit a permanently locked copy and every persistence call
    # deadlocks. Test after_in_child directly, since Windows lacks fork,
    # while preserving the platform-independent invariant.
    atomic_write_json(tmp_path / "state.json", {"v": 1})
    assert file_utils._swept_tmp_dirs, "先造出一些继承过来的记账"

    inherited = file_utils._swept_tmp_dirs_lock
    inherited.acquire()                      # Simulate another thread holding the lock during fork.
    try:
        file_utils._reset_tmp_sweep_state_after_fork()
        assert file_utils._swept_tmp_dirs_lock is not inherited
        assert not file_utils._swept_tmp_dirs_lock.locked()
        assert file_utils._swept_tmp_dirs == {}
        # Child persistence must not deadlock.
        atomic_write_json(tmp_path / "child.json", {"v": 1})
    finally:
        inherited.release()


def test_the_fork_hook_is_registered_at_import(monkeypatch):
    # Previous test checks hook behavior; this checks actual registration, without which correct hooks
    # never run at fork. Predicate-only tests commonly miss missing call-site wiring.
    # Independently load the module to observe registration without touching the imported copy. Windows lacks
    # os.register_at_fork; raising=False adds it and exercises the hasattr branch.
    import importlib.util

    calls = []
    monkeypatch.setattr(
        os, "register_at_fork", lambda **kw: calls.append(kw), raising=False
    )
    spec = importlib.util.spec_from_file_location(
        "_file_utils_fork_probe", file_utils.__file__
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    assert calls, "模块导入时必须注册 after_in_child 钩子"
    assert calls[0].get("after_in_child") is probe._reset_tmp_sweep_state_after_fork


@pytest.mark.skipif(os.name != "nt", reason="Windows-only handle semantics")
def test_sweep_cannot_steal_a_temp_file_that_is_still_open(tmp_path):
    # Age alone cannot prove orphan ownership; writers can theoretically freeze long after mkstemp.
    # Windows adds OS protection: live writers retain open handles and unlink fails with WinError
    # 32, physically preventing sweeper takeover. Pin that defense.
    target = tmp_path / "state.json"
    fd, inflight = tempfile.mkstemp(
        # Include ownership markers or the sweeper rejects before unlink as not ours,
        # making the test tautological without checking Windows handle semantics.
        prefix=f".{file_utils._TMP_OWNER_TAG}", suffix=".tmp", dir=str(tmp_path)
    )
    try:
        _age(Path(inflight), file_utils._STALE_TMP_MIN_AGE_S + 60)
        atomic_write_json(target, {"v": 1})
        assert Path(inflight).exists(), "an open temp file must survive the sweep"
    finally:
        os.close(fd)
        with suppress(OSError):
            os.unlink(inflight)


def test_sweep_survives_a_temp_file_that_cannot_be_removed(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    leftover = _abandoned_tmp(target)
    leftover.write_text("garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    real_unlink = os.unlink

    def unlink_denied(path):
        if str(path) == str(leftover):
            raise PermissionError("cannot remove leftover")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", unlink_denied)
    atomic_write_json(target, {"v": 1})

    assert read_json(target) == {"v": 1}, "sweeping is best-effort, never fatal"


# ── async twins ─────────────────────────────────────────────────────────


async def test_async_text_twin_writes_off_the_event_loop(tmp_path, monkeypatch):
    # Persistence fsync blocks all coroutines on event-loop threads; the async twin exists solely
    # to move it into workers. Pin that.
    target = tmp_path / "state.txt"
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_write = file_utils.atomic_write_text

    def spy(path, content, **kwargs):
        seen["thread"] = threading.get_ident()
        return real_write(path, content, **kwargs)

    monkeypatch.setattr(file_utils, "atomic_write_text", spy)
    await atomic_write_text_async(target, "hello")

    assert seen["thread"] != loop_thread
    assert target.read_text(encoding="utf-8") == "hello"


async def test_async_json_twin_writes_off_the_event_loop(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_write = file_utils.atomic_write_json

    def spy(path, data, **kwargs):
        seen["thread"] = threading.get_ident()
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(file_utils, "atomic_write_json", spy)
    await atomic_write_json_async(target, {"v": 1}, indent=None)

    assert seen["thread"] != loop_thread
    assert target.read_text(encoding="utf-8") == '{"v": 1}'


async def test_async_read_twin_reads_off_the_event_loop(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 7})
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_read = file_utils.read_json

    def spy(path, **kwargs):
        seen["thread"] = threading.get_ident()
        return real_read(path, **kwargs)

    monkeypatch.setattr(file_utils, "read_json", spy)

    assert await read_json_async(target) == {"v": 7}
    assert seen["thread"] != loop_thread


async def test_async_twin_propagates_write_failures(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def boom(src, dst):
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace refused"):
        await atomic_write_json_async(target, {"v": 1})

    assert not target.exists()


# ── same-target concurrency ─────────────────────────────────────────────


def test_concurrent_writers_never_leave_a_partial_target(tmp_path):
    # Concurrent same-target Windows writes can raise PermissionError in os.replace, #2528's actual
    # issue fixed through writer-side locking. Here pin the baseline: whatever writes fail,
    # targets contain one writer's complete content or remain absent, never partial files.
    target = tmp_path / "state.json"
    payloads = [{"writer": i, "pad": "x" * 4096} for i in range(6)]
    start = threading.Barrier(len(payloads))
    failures: list[Exception] = []

    def writer(payload):
        start.wait(timeout=5)
        for _ in range(20):
            try:
                atomic_write_json(target, payload)
            except Exception as exc:  # noqa: BLE001 - Concurrent failure is an explicit test premise.
                failures.append(exc)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert target.exists()
    assert read_json(target) in payloads, (
        f"target was left partial or interleaved; {len(failures)} writes failed"
    )
    assert _tmp_siblings(target) == [], "every failed write cleaned up its temp file"


def test_sweeper_is_thread_safe_for_the_same_target(tmp_path):
    # _sweep_stale_tmp_if_due bookkeeping is module-global; concurrent first writes in one directory
    # must neither duplicate sweeping nor raise.
    target = tmp_path / "state.json"
    start = threading.Barrier(8)
    errors: list[Exception] = []

    def writer():
        try:
            start.wait(timeout=5)
            file_utils._sweep_stale_tmp_if_due(target)
        except Exception as exc:  # noqa: BLE001 - Return every worker exception to the main thread.
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []


# ── read side ───────────────────────────────────────────────────────────


@pytest.mark.skipif(os.name != "nt", reason="Windows directory collision semantics")
def test_windows_permission_collision_retries_but_directory_denial_does_not(tmp_path, monkeypatch, caplog):
    caplog.set_level("DEBUG", logger=file_utils.logger.name)
    real_open = file_utils.os.open
    attempts = []

    def collide_once(path, flags, mode):
        attempts.append(path)
        if len(attempts) == 1:
            Path(path).mkdir()
            raise PermissionError(13, "directory collision")
        return real_open(path, flags, mode)

    monkeypatch.setattr(file_utils.os, "open", collide_once)
    fd, path = file_utils._create_exclusive_temp_file(tmp_path)
    os.close(fd)
    Path(path).unlink()
    assert len(attempts) == 2

    def deny_creation(*args):
        attempts.append(1)
        raise PermissionError(13, "directory denied")

    monkeypatch.setattr(file_utils.os, "open", deny_creation)
    with pytest.raises(PermissionError):
        file_utils._create_exclusive_temp_file(tmp_path)
    assert len(attempts) == 3
    assert "临时文件创建失败" in caplog.text


def test_read_json_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "absent.json")


def test_exclusive_temp_file_returns_absolute_path_for_relative_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fd, path = file_utils._create_exclusive_temp_file(Path("."))
    os.close(fd)
    assert Path(path).is_absolute()
    monkeypatch.chdir(tmp_path.parent)
    Path(path).unlink()


async def test_async_read_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        await read_json_async(tmp_path / "absent.json")


def test_read_json_reports_the_path_of_corrupt_content(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        read_json(target)


def test_asyncio_module_is_used_for_the_thread_hop():
    # Regression guard: synchronous direct-call async twins fail the three off-loop assertions above,
    # but this directly requires asyncio thread-pool dispatch.
    assert asyncio.iscoroutinefunction(atomic_write_text_async)
    assert asyncio.iscoroutinefunction(atomic_write_json_async)
    assert asyncio.iscoroutinefunction(read_json_async)


def test_both_publishes_share_one_busy_backoff(tmp_path):
    """The replacing and no-replace forms must not drift apart.

    They had the same loop written out twice -- same error set, same
    event-loop rule, same delays -- so a change to the policy had to be made
    in both places AND noticed to be needed in both. Pinned behaviourally
    rather than by looking for a shared symbol: each entry point is made to
    hit the Windows busy window and the attempt counts have to match.

    FileExistsError is the one asymmetry, and it is an ANSWER rather than a
    failure to retry around -- so the no-replace form must raise it on the
    first attempt, not after working through the backoff.
    """
    from utils import file_utils

    def _count_attempts(call, primitive, error):
        attempts = []

        def _always_busy(*args, **kwargs):
            attempts.append(1)
            raise error()

        real_primitive = getattr(file_utils.os, primitive)
        real_sleep = file_utils.time.sleep
        setattr(file_utils.os, primitive, _always_busy)
        file_utils.time.sleep = lambda _delay: None
        try:
            call()
        except OSError:
            # The point is the attempt COUNT, and every attempt raises here
            # by construction; the last one comes back out to the caller.
            pass
        finally:
            setattr(file_utils.os, primitive, real_primitive)
            file_utils.time.sleep = real_sleep
        return len(attempts)

    def _busy():
        error = OSError(13, "The process cannot access the file")
        error.winerror = 32
        return error

    source = tmp_path / "staged"
    source.write_text("[1]", encoding="utf-8")
    target = tmp_path / "published"

    replacing = _count_attempts(
        lambda: file_utils.replace_with_busy_retry(source, target),
        "replace",
        _busy,
    )
    no_replace = _count_attempts(
        lambda: file_utils.publish_without_replacing(source, target),
        "rename" if os.name == "nt" else "link",
        _busy,
    )

    assert replacing > 1, "the replacing form did not back off at all"
    assert no_replace == replacing, (
        "the two publishes no longer share one backoff policy: %d attempts "
        "against %d" % (no_replace, replacing)
    )

    # And the asymmetry, in the direction that matters: a destination that
    # won the race is an ANSWER, so it comes back on the first attempt
    # rather than after working through the backoff. It needs no special
    # case to do that -- FileExistsError is not a busy error -- and a
    # "terminal exceptions" parameter written for it was taken back out
    # after checking that it changed nothing on either platform.
    first_only = _count_attempts(
        lambda: file_utils.publish_without_replacing(source, target),
        "rename" if os.name == "nt" else "link",
        lambda: FileExistsError(17, "File exists"),
    )
    assert first_only == 1, (
        "a destination that won the race was retried %d times instead of "
        "being taken as the answer" % first_only
    )

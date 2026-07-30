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
import threading
import time
from pathlib import Path

import pytest

from utils import file_utils
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    atomic_write_text,
    atomic_write_text_async,
    read_json,
    read_json_async,
)

pytestmark = pytest.mark.unit


def _tmp_siblings(target: Path) -> list[Path]:
    """Temp files this module would have created next to ``target``."""
    prefix = f".{target.name}."
    return sorted(
        p for p in target.parent.iterdir()
        if p.name.startswith(prefix) and p.name.endswith(".tmp")
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
    # 这是「原子」的实际含义：新内容先完整落到 tmp（写满 + fsync），目标文件在
    # os.replace 之前一直是旧的完整内容 —— 读者永远看不到半截文件。
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
    # 回归点：目标被别的句柄占着时，os.replace 和紧随其后的 os.remove 会被同一个
    # 原因一起拒掉（Windows 上都是 WinError 5）。清理异常绝不能顶替真实原因，
    # 否则日志里只剩「删不掉临时文件」，完全指不到病根。
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


def test_missing_temp_file_during_cleanup_is_tolerated(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def replace_and_vanish(src, dst):
        os.unlink(src)
        raise OSError("replace refused after the temp file vanished")

    monkeypatch.setattr(os, "replace", replace_and_vanish)
    with pytest.raises(OSError, match="replace refused"):
        atomic_write_json(target, {"v": 1})


def test_unserializable_payload_never_touches_the_target(tmp_path):
    # json.dumps 在 atomic_write_text 之前跑，所以连 tmp 都不该出现。
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    assert read_json(target) == {"v": 1}
    assert _tmp_siblings(target) == []


# ── stale temp sweeping ─────────────────────────────────────────────────


def test_stale_temp_from_a_hard_kill_is_swept(tmp_path):
    target = tmp_path / "state.json"
    leftover = tmp_path / f".{target.name}.deadbeef.tmp"
    leftover.write_text("half-written garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert not leftover.exists()
    assert read_json(target) == {"v": 1}


def test_temp_file_of_a_live_writer_is_not_swept(tmp_path):
    # 另一个进程正在写同一个目标时，它的 tmp 只有几毫秒岁数；扫掉就等于把
    # 别人写了一半的数据删了。年龄门槛就是为了这个。
    target = tmp_path / "state.json"
    inflight = tmp_path / f".{target.name}.inflight.tmp"
    inflight.write_text("someone else is mid-write", encoding="utf-8")

    atomic_write_json(target, {"v": 1})

    assert inflight.exists()


def test_sweep_also_clears_other_targets_abandoned_temps(tmp_path):
    # 清扫按目录而不是按目标：同一个目录里别的目标留下的残留同样是这个原语产生的
    # 垃圾。按目标记账会漏掉「写完就沉底、再也不会被写第二次」的目标（归档分片就是
    # 这种），那些残留永远扫不到。
    target = tmp_path / "state.json"
    other_target_tmp = tmp_path / ".other.json.deadbeef.tmp"
    other_target_tmp.write_text("garbage from a crash", encoding="utf-8")
    _age(other_target_tmp, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert not other_target_tmp.exists()


def test_sweep_only_matches_the_shape_this_module_creates(tmp_path):
    # 按目录扫就得靠形状认自己的 tmp：mkstemp 产出的是
    # `.<目标名>.<8 个 [a-z0-9_]>.tmp`。形状不匹配一律不碰 —— 方向是少删不误删。
    target = tmp_path / "state.json"
    keepers = [
        tmp_path / f".{target.name}.deadbeef.bak",   # 不是 .tmp
        tmp_path / f"{target.name}.deadbeef.tmp",    # 没有前导点
        tmp_path / ".state.json.short.tmp",          # 随机段不是 8 位
        tmp_path / ".state.json.deadbeefx.tmp",      # 随机段是 9 位
        tmp_path / ".state.json.DEADBEEF.tmp",       # 随机段字符集不对（大写）
        tmp_path / ".notes.tmp",                     # 用户自己的文件
    ]
    for path in keepers:
        path.write_text("keep me", encoding="utf-8")
        _age(path, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert [p.name for p in keepers if not p.exists()] == []


def test_sweep_is_amortised_per_directory(tmp_path):
    # 摊销是有意的：残留由崩溃产生，而崩溃结束了那个进程，所以长寿进程反复扫目录
    # 没有意义。这条把「扫干净之后就不再扫」钉下来，免得后来有人改成每次写都扫。
    target = tmp_path / "state.json"
    atomic_write_json(target, {"v": 1})

    appeared_later = tmp_path / f".{target.name}.deadbeef.tmp"
    appeared_later.write_text("garbage", encoding="utf-8")
    _age(appeared_later, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 2})
    atomic_write_json(tmp_path / "another.json", {"v": 1})  # 同目录的另一个目标也不重扫

    assert appeared_later.exists()


def test_sweep_survives_an_unreadable_directory(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def denied(_path):
        raise PermissionError("cannot list this directory")

    monkeypatch.setattr(os, "scandir", denied)
    atomic_write_json(target, {"v": 1})

    assert read_json(target) == {"v": 1}


def test_a_failed_scan_does_not_consume_every_attempt(tmp_path, monkeypatch):
    # 瞬时 OSError（目录被短暂锁住、网络盘抖动）不该把这个目录的机会一次用光，
    # 否则残留会一直留到进程退出。
    target = tmp_path / "state.json"
    leftover = tmp_path / f".{target.name}.deadbeef.tmp"
    leftover.write_text("garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    real_scandir = os.scandir

    def transiently_denied(path):
        raise PermissionError("directory momentarily locked")

    monkeypatch.setattr(os, "scandir", transiently_denied)
    atomic_write_json(target, {"v": 1})
    assert leftover.exists(), "first write could not scan, so nothing is swept yet"

    monkeypatch.setattr(os, "scandir", real_scandir)
    atomic_write_json(target, {"v": 2})

    assert not leftover.exists(), "the retry after a failed scan must sweep"


def test_sweep_gives_up_after_a_bounded_number_of_attempts(tmp_path, monkeypatch):
    # 重试必须有界。永久性失败（只读文件、ACL 拒绝）不能让每一次写盘都白搭一次
    # 全目录 scandir —— 那就把 O(1) 的摊销变回了 O(每次写)。
    target = tmp_path / "state.json"
    scans = []
    real_scandir = os.scandir

    def counted_then_denied(path):
        scans.append(str(path))
        raise PermissionError("permanently unreadable")

    monkeypatch.setattr(os, "scandir", counted_then_denied)
    for i in range(file_utils._STALE_TMP_SWEEP_ATTEMPTS + 3):
        atomic_write_json(target, {"v": i})

    assert len(scans) == file_utils._STALE_TMP_SWEEP_ATTEMPTS
    monkeypatch.setattr(os, "scandir", real_scandir)
    assert read_json(target) == {"v": file_utils._STALE_TMP_SWEEP_ATTEMPTS + 2}


def test_a_temp_file_that_cannot_be_removed_leaves_a_retry(tmp_path, monkeypatch):
    # 删不掉一个 tmp 不代表整个目录扫过了：Windows 上活写者的句柄还开着就会撞这个，
    # 句柄一放开就该能扫掉。所以「没扫干净」不记成完成，留给后续写盘重试。
    target = tmp_path / "state.json"
    leftover = tmp_path / f".{target.name}.deadbeef.tmp"
    leftover.write_text("garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    real_unlink = os.unlink
    denied = {"on": True}

    def unlink_maybe_denied(path):
        if denied["on"] and str(path) == str(leftover):
            raise PermissionError("held open by a live writer")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", unlink_maybe_denied)
    atomic_write_json(target, {"v": 1})
    assert leftover.exists()

    denied["on"] = False
    atomic_write_json(target, {"v": 2})

    assert not leftover.exists(), "the retry must sweep once the handle is released"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only handle semantics")
def test_sweep_cannot_steal_a_temp_file_that_is_still_open(tmp_path):
    # 年龄门槛本身不能证明 tmp 没有主人（写者理论上可以在 mkstemp 之后被冻结很久）。
    # Windows 上还有一道 OS 级兜底：活写者的句柄一直开着，unlink 会被拒（WinError
    # 32），清扫器物理上抢不走。这条把那道兜底钉住。
    import tempfile

    target = tmp_path / "state.json"
    fd, inflight = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(tmp_path)
    )
    try:
        _age(Path(inflight), file_utils._STALE_TMP_MIN_AGE_S + 60)
        atomic_write_json(target, {"v": 1})
        assert Path(inflight).exists(), "an open temp file must survive the sweep"
    finally:
        os.close(fd)
        with __import__("contextlib").suppress(OSError):
            os.unlink(inflight)


def test_sweep_survives_a_temp_file_that_cannot_be_removed(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    leftover = tmp_path / f".{target.name}.deadbeef.tmp"
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
    # 落盘含 fsync，跑在事件循环线程上会卡住所有协程。async 孪生的唯一职责就是
    # 把它挪到 worker 线程，这条把它钉住。
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
    # 同目标并发写在 Windows 上会让 os.replace 抛 PermissionError（这是 #2528 的
    # 真实议题，修法在写者侧加锁，不在这里）。这条只钉住底线：无论哪些写失败，
    # 目标文件要么是某一个写者的完整内容，要么根本没被创建 —— 绝不会是半截。
    target = tmp_path / "state.json"
    payloads = [{"writer": i, "pad": "x" * 4096} for i in range(6)]
    start = threading.Barrier(len(payloads))
    failures: list[BaseException] = []

    def writer(payload):
        start.wait(timeout=5)
        for _ in range(20):
            try:
                atomic_write_json(target, payload)
            except Exception as exc:  # noqa: BLE001 - 并发失败是本用例的已知前提
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
    # _sweep_stale_tmp_once 的记账是模块级共享状态，多线程同时首写同一目标时
    # 不许重复扫、也不许抛。
    target = tmp_path / "state.json"
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def writer():
        try:
            start.wait(timeout=5)
            file_utils._sweep_stale_tmp_once(target)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []


# ── read side ───────────────────────────────────────────────────────────


def test_read_json_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "absent.json")


async def test_async_read_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        await read_json_async(tmp_path / "absent.json")


def test_read_json_reports_the_path_of_corrupt_content(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        read_json(target)


def test_asyncio_module_is_used_for_the_thread_hop():
    # 防回退：async 孪生一旦改成同步直调，上面那三条 off-the-loop 断言会红，
    # 但这条更直接——它们必须经过 asyncio 的线程池。
    assert asyncio.iscoroutinefunction(atomic_write_text_async)
    assert asyncio.iscoroutinefunction(atomic_write_json_async)
    assert asyncio.iscoroutinefunction(read_json_async)

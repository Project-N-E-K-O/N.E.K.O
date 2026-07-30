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


def _abandoned_tmp(target: Path, rand: str = "deadbeef") -> Path:
    """A temp file shaped exactly like the ones this module leaves behind."""
    return target.parent / f".{target.name}.{file_utils._TMP_OWNER_TAG}{rand}.tmp"


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
    leftover = _abandoned_tmp(target)
    leftover.write_text("half-written garbage", encoding="utf-8")
    _age(leftover, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert not leftover.exists()
    assert read_json(target) == {"v": 1}


def test_temp_file_of_a_live_writer_is_not_swept(tmp_path):
    # 另一个进程正在写同一个目标时，它的 tmp 只有几毫秒岁数；扫掉就等于把
    # 别人写了一半的数据删了。年龄门槛就是为了这个。
    target = tmp_path / "state.json"
    inflight = _abandoned_tmp(target, "inflight")
    inflight.write_text("someone else is mid-write", encoding="utf-8")

    atomic_write_json(target, {"v": 1})

    assert inflight.exists()


def test_sweep_also_clears_other_targets_abandoned_temps(tmp_path):
    # 清扫按目录而不是按目标：同一个目录里别的目标留下的残留同样是这个原语产生的
    # 垃圾。按目标记账会漏掉「写完就沉底、再也不会被写第二次」的目标（归档分片就是
    # 这种），那些残留永远扫不到。
    target = tmp_path / "state.json"
    other_target_tmp = _abandoned_tmp(tmp_path / "other.json")
    other_target_tmp.write_text("garbage from a crash", encoding="utf-8")
    _age(other_target_tmp, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert not other_target_tmp.exists()


def test_sweep_only_touches_files_it_can_prove_it_owns(tmp_path):
    # 按目录扫的前提是能**证明**所有权：光靠「形状像 + 够老」会把别的程序、插件或
    # 用户放在同一个目录里的旧文件永久删掉。所以自己的 tmp 名里嵌了所有权标记，
    # 没有这个标记的一律不碰 —— 包括本模块早先版本留下的无标记 tmp（宁可漏清）。
    target = tmp_path / "state.json"
    tag = file_utils._TMP_OWNER_TAG
    keepers = [
        tmp_path / f".{target.name}.{tag}deadbeef.bak",  # 不是 .tmp
        tmp_path / f"{target.name}.{tag}deadbeef.tmp",   # 没有前导点
        tmp_path / f".{target.name}.deadbeef.tmp",       # 没有所有权标记（旧版残留）
        tmp_path / f".{target.name}.{tag.upper()}deadbeef.tmp",  # 标记大小写不对
        tmp_path / ".rsync-ish.abcdef.tmp",              # 别的工具的临时文件
        tmp_path / ".notes.tmp",                         # 用户自己的文件
    ]
    for path in keepers:
        path.write_text("keep me", encoding="utf-8")
        _age(path, file_utils._STALE_TMP_MIN_AGE_S + 60)

    atomic_write_json(target, {"v": 1})

    assert [p.name for p in keepers if not p.exists()] == []


def test_the_temp_files_this_module_creates_carry_the_owner_tag(tmp_path, monkeypatch):
    # 所有权标记只有在**创建**时也带上才有意义：只改清扫器的正则、不改 mkstemp 的
    # 前缀，就会变成「以后再也扫不到任何东西」的静默失效。
    target = tmp_path / "state.json"
    seen = {}
    real_mkstemp = tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        seen["name"] = Path(path).name
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)
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
    # 节流是有意的：清扫是清理型的副业，不能让每次写盘都白搭一次全目录 scandir。
    # 同一个目录在一个间隔内只扫一次，同目录的别的目标也共享这个窗口。
    scans = []
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p: scans.append(str(p)) or real_scandir(p))

    atomic_write_json(tmp_path / "state.json", {"v": 1})
    atomic_write_json(tmp_path / "state.json", {"v": 2})
    atomic_write_json(tmp_path / "another.json", {"v": 1})

    assert len(scans) == 1


def test_sweep_runs_again_once_the_interval_elapses(tmp_path):
    # 「只扫一次」是错的不变量：本次写自己泄漏的 tmp（清扫跑在 mkstemp 之前）、扫描当时
    # 还太年轻的 tmp、扫描本身瞬时失败的目录，都得靠下一个窗口兜住。
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
    # 瞬时 OSError（目录被短暂锁住、网络盘抖动）不该让这个目录永久失去清扫。
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
    # 清扫跑在 mkstemp 之前，所以本次调用自己泄漏出来的 tmp（replace 失败且兜底的
    # remove 也被拒 —— 正是这个模块要容忍的 Windows 场景）这一轮清不掉。它必须被下一个
    # 窗口兜住，否则长寿进程（桌面端一开一整天）里就是永久泄漏。
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
    # cloudsave 的 staging 每次导出都 mkdtemp 出一批新目录（含 per-character 子目录），
    # 记账永久留着会随操作次数无界增长。
    limit = file_utils._STALE_TMP_MEMO_MAX
    for i in range(limit + 40):
        atomic_write_json(tmp_path / f"d{i}" / "state.json", {"v": i})

    assert len(file_utils._swept_tmp_dirs) <= limit


def test_temp_name_stays_within_the_filesystem_name_limit(tmp_path, monkeypatch):
    # 前缀里嵌完整目标名是为了可诊断，但 basename 本身合法（多数文件系统 NAME_MAX 是
    # 255 字节）时，不能因为加了所有权标记就把 tmp 名顶过限制、让原本能写的目标写不动。
    # `.` + 名字 + `.` + 标记 + 随机(8) + `.tmp`(4)
    overhead = 8 + len(".tmp")
    for basename in ("state.json", "a" * 240 + ".json", "妮" * 200 + ".json"):
        prefix = file_utils._tmp_prefix(basename)
        assert len(prefix.encode("utf-8")) + overhead <= 255, basename
        # 截断之后仍然要被清扫器认领，否则这些目标的残留就永远扫不到了
        assert file_utils._STALE_TMP_RE.match(f"{prefix}abcdefgh.tmp"), basename

    # 上面是纯函数，还得钉住 atomic_write_text 真的走了它（只测谓词不测调用点是本仓库
    # 反复踩的坑）。用 CJK 名字：79 个汉字 = 237 字节，不截断的话 tmp 名 257 字节就超了
    # NAME_MAX，但**字符数**只有 98，不会先撞 Windows 的 MAX_PATH(260)。
    seen = {}
    real_mkstemp = tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        seen["name"] = Path(path).name
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    atomic_write_json(tmp_path / ("妮" * 79 + ".json"), {"v": 1})

    assert len(seen["name"].encode("utf-8")) <= 255, seen["name"]
    assert file_utils._STALE_TMP_RE.match(seen["name"])


def test_fork_child_gets_a_fresh_sweep_lock(tmp_path):
    # app/main_server/__init__.py:56 选的是 fork 启动方式，而 fork 只复制调用它的那
    # 一个线程：别的线程正持着这把锁时，子进程继承到的是一把永远锁着的 mutex，子进程
    # 里任何一次落盘都会死锁。这条直接验 after_in_child 钩子（Windows 上没有 fork，
    # 所以只能直接调它，但要守的不变量是平台无关的）。
    atomic_write_json(tmp_path / "state.json", {"v": 1})
    assert file_utils._swept_tmp_dirs, "先造出一些继承过来的记账"

    inherited = file_utils._swept_tmp_dirs_lock
    inherited.acquire()                      # 模拟「fork 时别的线程正持着锁」
    try:
        file_utils._reset_tmp_sweep_state_after_fork()
        assert file_utils._swept_tmp_dirs_lock is not inherited
        assert not file_utils._swept_tmp_dirs_lock.locked()
        assert file_utils._swept_tmp_dirs == {}
        # 子进程里落盘不该死锁
        atomic_write_json(tmp_path / "child.json", {"v": 1})
    finally:
        inherited.release()


def test_the_fork_hook_is_registered_at_import(monkeypatch):
    # 上一条只验了钩子函数本身干得对；这条验它真的被挂上去了 —— 否则函数写得再对也
    # 不会在 fork 时跑（护栏测试最常见的失效就是只测谓词、不测调用点）。
    # 独立加载一份模块来观察注册动作，不动已经导入的那份。Windows 上 os 本来没有
    # register_at_fork，raising=False 会把它加上，于是那个 hasattr 分支也会走到。
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
    # 年龄门槛本身不能证明 tmp 没有主人（写者理论上可以在 mkstemp 之后被冻结很久）。
    # Windows 上还有一道 OS 级兜底：活写者的句柄一直开着，unlink 会被拒（WinError
    # 32），清扫器物理上抢不走。这条把那道兜底钉住。
    target = tmp_path / "state.json"
    fd, inflight = tempfile.mkstemp(
        # 必须带所有权标记，否则清扫器在 unlink 之前就把它按「不是我的」拒了，
        # 这条用例就变成恒真、验不到 Windows 的句柄语义。
        prefix=file_utils._tmp_prefix(target.name), suffix=".tmp", dir=str(tmp_path)
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
    failures: list[Exception] = []

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
    # _sweep_stale_tmp_if_due 的记账是模块级共享状态，多线程同时首写同一目录时
    # 不许重复扫、也不许抛。
    target = tmp_path / "state.json"
    start = threading.Barrier(8)
    errors: list[Exception] = []

    def writer():
        try:
            start.wait(timeout=5)
            file_utils._sweep_stale_tmp_if_due(target)
        except Exception as exc:  # noqa: BLE001 - 线程体里任何异常都要交回主线程
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

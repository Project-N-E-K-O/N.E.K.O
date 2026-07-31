"""A failed source-history load must never be mistaken for a loaded one."""

# _record_source_used 是全量覆盖写（entries 就是整个 _source_history）。所以
# 「内存现在能不能代表盘上那份」是个安全属性：一次读盘失败之后内存是空的，若被
# 标记成「已加载」，紧接着的一次记录就把盘上整段历史截成 1 条。
# proactive_source_history.json 是「这个素材我已经用过」的清单，截掉之后她会把刚
# 聊过的东西再聊一遍。这些用例全部走真实磁盘断言，不看内部标志代替文件内容。

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
    # 本文件里有几条会真的把 _source_history_lock 抢起来的用例。asyncio.Lock 只在
    # 「有竞争」那条路上才 _get_loop()，一旦绑定就跟那个事件循环绑死；pytest-asyncio
    # 每个用例一个新循环，模块级那把锁被这里绑上之后，别处的并发用例就会撞
    # "bound to a different event loop"。整个用例期间换一把新锁，跑完还回去——被测代码
    # 取的是 state 模块的全局名，换掉之后走的仍然是同一条真实临界区。
    saved_lock = state._source_history_lock
    state._source_history_lock = asyncio.Lock()
    state._source_history.clear()
    state._source_history_loaded = False
    state._source_history_loaded_path = None
    state._source_history_skipped_records = 0
    state._source_history_read_failures = 0
    yield
    state._source_history.clear()
    state._source_history.update(saved_history)
    state._source_history_loaded = saved_loaded
    state._source_history_loaded_path = saved_path
    state._source_history_skipped_records = saved_skipped
    state._source_history_read_failures = saved_failures
    state._source_history_lock = saved_lock


def _history_path(root: Path) -> Path:
    return root / state._SOURCE_HISTORY_FILENAME


def _seed_history(root: Path, count: int) -> dict[str, dict]:
    """Write *count* fresh entries to disk and return them."""
    # ts=now → _source_skip_probability 返回 1.0，既不会被加载时的遗忘过滤掉，
    # 也不会被 _record_source_used 的 prune 扫掉；断言里的「一条不少」才成立。
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
    # 主回归。Windows 上杀软/索引器短暂占住文件正是 #2528 这一族的现场：读一次
    # PermissionError，历史就被下一次记录截成 1 条。
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

    # 逐字等值而不是 len>=1：既挡住「截成 1 条」，也挡住「盲目追加一条」——后者
    # 意味着覆盖写照样发生过，只是这一轮内存恰好不空。
    assert _disk_entries(root) == seeded
    assert state._source_history_loaded is False, "读失败不许被标记成已加载"
    assert state._source_history_loaded_path is None


@pytest.mark.asyncio
async def test_missing_file_is_a_normal_empty_history(tmp_path) -> None:
    """A file that never existed is an ordinary empty history, not a failure."""
    # 别把 FileNotFoundError 一起改坏：首启 / 清理过缓存时文件本就不在，
    # 这条路径必须照旧标记已加载并正常记录，否则第一条记录永远写不下去。
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
    # 护栏。此前「记录之前一定先加载过」只由 handle_proactive_chat 的语句顺序维持
    # （加载第 53 条、记录第 115/170 条，中间上千行）。这里完全不预先加载，直接记录。
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
    # 「读不出来就跳过」的代价必须是一次性的：加载在每次调用时重试，盘一恢复就继续记。
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
    # 内容坏掉与「暂时读不到」必须分流：坏内容重试永远失败，若也走重试分支，
    # 一个坏文件就让记录永久停摆。这里钉住它按空历史放行、由覆盖写重建。
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
    # 同一个截断缺陷的另一扇门：切根时读失败。解析进局部 dict 之后，失败的那次加载
    # 一个全局都不碰，于是「flag / path / 内存」三件套仍然自洽地描述着 root_a——
    # 这比「把标记清掉」更强：既不会拿空内存去覆盖 root_a，也不用为 root_b 的一次
    # 读失败白白丢掉 root_a 已经读好的历史。
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
    # 三件套逐项钉死：谁被动过，root_a 的下一次记录就会写错东西。
    assert state._source_history_loaded is True
    assert state._source_history_loaded_path == _history_path(root_a)
    assert set(state._source_history) == set(seeded_a)

    url = "https://example.test/fresh"
    await state._record_source_used(
        url=url, kind="web", title="fresh", memory_dir=root_a
    )

    # root_a 的记录照常进行（内存本来就是它的），一条不丢、正常追加。
    after = _disk_entries(root_a)
    assert set(after) == set(seeded_a) | {state._source_hash(url, "fresh")}
    assert {key: after[key] for key in seeded_a} == seeded_a
    assert not _history_path(root_b).exists(), "读失败的那个根不许被写出文件"


@pytest.mark.asyncio
async def test_cancelling_a_load_leaves_no_poisoned_state(monkeypatch, tmp_path) -> None:
    """A cancelled load must not leave an empty memory wearing the previous root's flag."""
    # CancelledError 是 BaseException，两个 except 都接不住，而 to_thread 那次读是本
    # 函数唯一的挂起点。旧写法在 try 之前就 _source_history.clear()，于是取消会留下
    # 「flag=True、path=root_a、内存空」——之后一次读盘完全正常的 record(root_a) 就把
    # 7 条截成 1 条。解析进局部 dict 之后，取消什么都改不到。
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
    # 放行读线程并等它真的收尾，免得它跑到别的用例里去。
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
    # 半损坏（几百条里坏一条）比整份 JSON 坏掉常见得多。解析循环里 float(ts) 抛
    # ValueError、_half_life_for(kind) 对不可 hash 的 kind 抛 TypeError，若让它们冒到
    # 「内容损坏」分支并清空内存，合法条目就会被紧接着的覆盖写从盘上一起抹掉。
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

    # 逐字等值：坏的两条该被遗忘，其余五条一条不许少（旧行为只剩新写的那 1 条）。
    assert set(_disk_entries(root)) == survivors | {state._source_hash(url, "fresh")}


@pytest.mark.asyncio
async def test_root_swapped_between_load_and_write_aborts_the_record(
    monkeypatch, tmp_path
) -> None:
    """Another root swapping the memory mid-flight must abort the overwrite, not ride it."""
    # 护栏判定在锁外（_ensure 自己要取这把锁），覆盖写在锁内，中间有窗口：_ensure 走
    # IO 分支时持锁 await，放锁之后本协程还没拿到锁，另一个 root 的 _ensure 能插进来
    # 把内存整个换掉。没有锁内复核的话，root_a 的文件会被写成 root_b 的内容。
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
    # 等 recorder 的 _ensure(root_a) 进临界区并卡在读盘上。
    await asyncio.to_thread(a_read_started.wait, 5)

    swapper = asyncio.create_task(
        state._ensure_source_history_loaded(memory_dir=root_b)
    )
    # 让 swapper 跑到「排队等 _source_history_lock」为止：它必须排在 recorder 的
    # 写入取锁之前（asyncio.Lock 是 FIFO），才复现出那个窗口。
    for _ in range(5):
        await asyncio.sleep(0)

    release_a_read.set()
    assert await swapper is True
    await recorder

    # 内存此刻属于 root_b，于是 root_a 的那次覆盖写必须被放弃。
    assert state._source_history_loaded_path == _history_path(root_b)
    assert _disk_entries(root_a) == seeded_a, "root_a 的历史不许被别的根的内容覆盖"
    assert _disk_entries(root_b) == seeded_b, "root_b 只是被加载，不该被写"
    assert state._source_hash(url, "fresh") not in _disk_entries(root_a)


@pytest.mark.asyncio
async def test_permanent_read_failure_logs_at_a_throttled_rate(
    monkeypatch, tmp_path
) -> None:
    """A permanently unreadable history must stay visible without one warning per record."""
    # 「读不出来就跳过」的代价可能是永久的：文件的读 ACL 坏掉而目录仍可写时，记录会
    # 一直停摆，对用户只表现为她反复聊同一个东西。日志是这件事唯一的出口，所以既不能
    # 每次刷一条把自己淹掉，也不能安静到看不见。
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
    # 前 3 次 + 第 50 次 = 4 条。写成「< 60」不够：那样把节流退化成「每 2 次报一次」
    # 也算过，而那依然是刷屏。
    assert len(skips) == 4, warnings
    # 加载侧同样每次调用都重来一遍。只降记录侧的频，日志里照样是 60 行，「停摆」
    # 一样被淹掉 —— 两侧必须一起降。
    assert len(reads) == 4, warnings
    assert len(warnings) == 8, warnings
    assert "连续跳过 50 次" in skips[-1], "降频之后的日志必须带累计数，否则看不出是停摆"
    assert "连续失败 50 次" in reads[-1]

    # 盘上一条没少 —— 降频只改日志，不改「宁可漏记也不截库」这个取舍。
    assert len(_disk_entries(root)) == 7

    # 读恢复 → 记录成功 → 连跳计数清零；再坏掉时必须立刻重新报，而不是接着上一轮的
    # 节流继续数到 100 才吭声。
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

"""Concurrent-write protection for ``<model>.model3.json``."""

# 原先同一个文件被 5 个端点写、无锁，其中两个还是同步 GET（补齐缺省配置项后写回）。
# 把写挂在 GET 上意味着读流量变写流量，而且同步 GET 跑在 anyio 线程池里、和 async POST
# 的 asyncio 锁不在同一个互斥域 —— 两个线程 os.replace 同一目标在 Windows 上互相
# PermissionError(WinError 5)。这里钉三件事：GET 不再落盘、三个 POST 的读→改→写整段
# 互斥、锁按解析后的文件路径分桶。

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path

import pytest

from main_routers import live2d_router

_ROUTER_SOURCE = Path(live2d_router.__file__)

_BASE_CONFIG = {
    "Version": 3,
    "FileReferences": {
        "Moc": "m.moc3",
        "Textures": ["m.2048/texture_00.png"],
        "Motions": {},
        "Expressions": [],
    },
}


@pytest.fixture(autouse=True)
def _clear_model_config_write_locks():
    """Clear the lock registry between tests: an awaited asyncio.Lock binds to its loop."""
    # pytest.ini 是 asyncio_mode=auto + function-scope loop，用例之间循环会换掉；留着
    # 上一个用例争用过的锁，下一个用例 await 它就 RuntimeError（loop 不匹配）。生产上
    # 只有一个循环（merged 单进程），所以这是测试侧的约束、不是设计缺陷。
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()
    yield
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()


def _make_model_dir(tmp_path: Path, *, name: str = "m", config: dict | None = None) -> Path:
    model_dir = tmp_path / name
    model_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(config if config is not None else _BASE_CONFIG))
    (model_dir / f"{name}.model3.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    return model_dir


class _FakeRequest:
    """Minimal stand-in: the handlers only use ``await request.json()``."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


# --- (1) 两个同步 GET 不得落盘 ---------------------------------------------------
#
# 主断言用「目标文件字节逐字节相同」而不是 spy `live2d_router.atomic_write_json`：
# 修复之后那个名字在文件里已经没有调用点（import 也删掉了），spy 目标会消失，
# 测试会因为 AttributeError 报错而不是因为断言失败。字节比对不依赖任何内部符号。


def test_get_model_config_never_writes_disk(monkeypatch, tmp_path) -> None:
    # 盘上是「老模型」：连 FileReferences 都没有，原实现一定会触发写回。
    model_dir = _make_model_dir(tmp_path, config={"Version": 3})
    model_json = model_dir / "m.model3.json"
    before = model_json.read_bytes()
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/m")
    )

    result = live2d_router.get_model_config("m")

    assert model_json.read_bytes() == before, "GET 不得写盘"
    # 补齐逻辑本身必须留着——它是这个端点对前端的契约，别被顺手清理掉。
    assert result["success"] is True
    assert result["config"]["FileReferences"]["Motions"] == {}
    assert result["config"]["FileReferences"]["Expressions"] == []


def test_get_model_config_by_id_never_writes_disk(monkeypatch, tmp_path) -> None:
    model_dir = _make_model_dir(tmp_path, config={"Version": 3})
    model_json = model_dir / "m.model3.json"
    before = model_json.read_bytes()
    monkeypatch.setattr(
        live2d_router,
        "find_workshop_item_by_id",
        lambda model_id: (str(model_dir), "/workshop/123"),
    )

    result = live2d_router.get_model_config_by_id("123")

    assert model_json.read_bytes() == before, "GET 不得写盘"
    assert result["success"] is True
    assert result["config"]["FileReferences"]["Motions"] == {}
    assert result["config"]["FileReferences"]["Expressions"] == []


def test_no_get_handler_writes_model_config() -> None:
    """Auto-discovering ratchet: no ``@router.get`` endpoint may write to disk."""
    # 不写死函数名清单 —— 遍历装饰器，新增的 GET 端点自动进入覆盖范围（清单式的守卫
    # 迟早漏项）。同步 GET 跑在 anyio 线程池里，与三个 POST 的 asyncio 锁不在同一个
    # 互斥域，任何新的 GET 落盘都会重新引入 WinError 5。
    writers = {
        "atomic_write_json",
        "atomic_write_json_async",
        "write_json",
        "write_text",
        "write_bytes",
    }
    tree = ast.parse(_ROUTER_SOURCE.read_text(encoding="utf-8"))

    checked: list[str] = []
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_get = any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "get"
            and isinstance(dec.func.value, ast.Name)
            and dec.func.value.id == "router"
            for dec in node.decorator_list
        )
        if not is_get:
            continue
        checked.append(node.name)
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else "")
            )
            if name in writers:
                offenders.append(f"{node.name}:{call.lineno} 调用 {name}")
            elif (
                name == "replace"
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                # 只认 os.replace；str.replace 在这个文件里到处都是。
                offenders.append(f"{node.name}:{call.lineno} 调用 os.replace")
            elif name == "open":
                mode = None
                if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                    mode = call.args[1].value
                for keyword in call.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        mode = keyword.value.value
                if mode and any(flag in str(mode) for flag in "wax+"):
                    offenders.append(f"{node.name}:{call.lineno} open(mode={mode!r})")

    # 先证明遍历真的找到了端点，否则「无违规」可能只是没扫到东西。
    assert "get_model_config" in checked
    assert "get_model_config_by_id" in checked
    assert len(checked) >= 8
    assert offenders == []


# --- (2) 三个 POST 的读→改→写整段互斥 -------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_model_config_posts_do_not_lose_each_others_edits(
    monkeypatch, tmp_path
) -> None:
    """Two different POSTs racing on one model3.json: both edits must survive."""
    # 真文件、真 handler、不 mock 写。两个改动落在互不相交的键上（emotion_mapping 传空
    # expressions → emotion_prefixes 为空 → 现存 Expressions 全部 preserved；
    # model_config 的 payload 里没有 Motions → Motions 不动），所以和执行顺序无关 ——
    # 串行化之后两个都该在。
    model_dir = _make_model_dir(tmp_path)
    model_json = model_dir / "m.model3.json"
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/m")
    )

    real_read = live2d_router.read_json_async

    async def slow_read(path, **kwargs):
        data = await real_read(path, **kwargs)
        # 撑开「读到写」的窗口：丢写发生在这一段，只锁 os.replace 抓不到。
        await asyncio.sleep(0.02)
        return data

    monkeypatch.setattr(live2d_router, "read_json_async", slow_read)

    results = await asyncio.gather(
        live2d_router.update_emotion_mapping(
            "m", _FakeRequest({"motions": {"happy": ["motions/a.motion3.json"]}, "expressions": {}})
        ),
        live2d_router.update_model_config(
            "m",
            _FakeRequest(
                {
                    "FileReferences": {
                        "Expressions": [{"Name": "x_1", "File": "expressions/x.exp3.json"}]
                    }
                }
            ),
        ),
    )
    assert all(item.get("success") for item in results), results

    final = json.loads(model_json.read_text(encoding="utf-8"))
    file_refs = final["FileReferences"]
    assert "happy" in file_refs["Motions"], "emotion_mapping 的编辑被覆盖了"
    assert any(
        isinstance(item, dict) and item.get("Name") == "x_1" for item in file_refs["Expressions"]
    ), "model_config 的编辑被覆盖了"


@pytest.mark.asyncio
async def test_cancelling_a_post_does_not_release_the_lock_before_the_write_ends(
    monkeypatch, tmp_path
) -> None:
    """A cancelled POST must keep the per-file lock until its in-flight write finishes."""
    # atomic_write_json_async 内部是 asyncio.to_thread，交出去就取消不掉：线程会一直跑到
    # os.replace 结束。如果 async with 在 CancelledError 穿过的一刻就放锁（直接 await 就是
    # 这样），第二个 handler 会在第一次 replace 还在飞的时候进临界区 —— 正是本 PR 要修的
    # 那个并发 replace。请求被客户端断开或超时中间件取消是常态，不是边角情况。
    model_dir = _make_model_dir(tmp_path)
    model_json = model_dir / "m.model3.json"
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/m")
    )

    write_started = asyncio.Event()
    let_write_finish = asyncio.Event()

    async def blocked_write(path, data, **kwargs):
        write_started.set()
        await let_write_finish.wait()

    monkeypatch.setattr(live2d_router, "atomic_write_json_async", blocked_write)

    task = asyncio.create_task(
        live2d_router.update_model_config(
            "m",
            _FakeRequest(
                {"FileReferences": {"Expressions": [{"Name": "x", "File": "e.exp3.json"}]}}
            ),
        )
    )
    await asyncio.wait_for(write_started.wait(), timeout=5)

    lock = live2d_router._model_config_write_lock(model_json)
    assert lock.locked(), "前置条件：写在飞的时候锁是持有状态"

    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert lock.locked(), "写还在飞，锁不许因为取消就提前放掉"

    let_write_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked(), "写收尾之后锁必须放掉"


@pytest.mark.asyncio
async def test_by_name_and_by_id_posts_share_one_lock(monkeypatch, tmp_path) -> None:
    """by-name and by-id hitting the same file must share one lock."""
    # 按入参（model_name / model_id）分桶就会漏：两条路由可以落到同一个文件上。
    model_dir = _make_model_dir(tmp_path)
    model_json = model_dir / "m.model3.json"
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/m")
    )
    monkeypatch.setattr(
        live2d_router,
        "find_workshop_item_by_id",
        lambda model_id: (str(model_dir), "/workshop/123"),
    )

    in_flight = 0
    max_in_flight = 0
    real_write = live2d_router.atomic_write_json_async

    async def tracking_write(path, data, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)
        await real_write(path, data, **kwargs)
        in_flight -= 1

    monkeypatch.setattr(live2d_router, "atomic_write_json_async", tracking_write)

    await asyncio.gather(
        live2d_router.update_model_config(
            "m", _FakeRequest({"FileReferences": {"Motions": {"a": []}}})
        ),
        live2d_router.update_model_config_by_id(
            "123", _FakeRequest({"FileReferences": {"Motions": {"b": []}}})
        ),
    )

    assert max_in_flight == 1, "两条路由落到同一个文件却没被同一把锁串起来"
    assert model_json.exists()


def test_write_lock_is_keyed_on_the_resolved_file(tmp_path) -> None:
    first = _make_model_dir(tmp_path, name="one") / "one.model3.json"
    second = _make_model_dir(tmp_path, name="two") / "two.model3.json"

    lock = live2d_router._model_config_write_lock(first)
    # 同一个文件的另一种拼法（realpath 折掉 "."）必须拿到同一把锁。
    aliased = live2d_router._model_config_write_lock(first.parent / "." / first.name)
    assert aliased is lock
    if os.name == "nt":
        # Windows 大小写不敏感，normcase 必须把它折平。
        assert live2d_router._model_config_write_lock(str(first).upper()) is lock

    # 不同文件必须是不同的锁对象（退化成一把全局锁时这条红）。
    assert live2d_router._model_config_write_lock(second) is not lock

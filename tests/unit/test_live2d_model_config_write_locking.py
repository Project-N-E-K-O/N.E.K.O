"""Concurrent-write protection for ``<model>.model3.json``."""

# Originally, five endpoints wrote the same file without a lock, and two were synchronous GETs (writing back missing defaults).
# Putting writes in GET makes read traffic become write traffic; synchronous GET also runs in an anyio thread pool alongside async POST
# do not share the same mutex domain with the asyncio lock — two threads calling os.replace on the same target can get
# PermissionError(WinError 5) on Windows. This enforces three rules: GET does not persist, the full read→modify→write of all three POSTs
# is mutually exclusive, and locks are bucketed by resolved file path.

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
    # pytest.ini uses asyncio_mode=auto + function-scope loop, so loops are replaced between tests; retaining
    # a lock contended by the previous test makes the next test's await fail with RuntimeError (loop mismatch). In production
    # there is only one loop (merged single process), so this is a test constraint, not a design flaw.
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


# --- (1) Neither synchronous GET may write to disk --------------------------------
#
# The primary assertion compares the target file byte-for-byte instead of spying on `live2d_router.atomic_write_json`:
# after the fix, that name has no call sites in the file (and its import is gone), so the spy target would disappear
# and the test would fail with AttributeError instead of an assertion failure. Byte comparison does not depend on internal symbols.


def test_get_model_config_never_writes_disk(monkeypatch, tmp_path) -> None:
    # Disk contains an "old model" with no FileReferences; the old implementation would definitely write it back.
    model_dir = _make_model_dir(tmp_path, config={"Version": 3})
    model_json = model_dir / "m.model3.json"
    before = model_json.read_bytes()
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/m")
    )

    result = live2d_router.get_model_config("m")

    assert model_json.read_bytes() == before, "GET 不得写盘"
    # Keep the backfill logic itself — it is part of this endpoint's frontend contract; do not clean it up incidentally.
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
    # Do not hard-code a list of function names — traverse decorators so newly added GET endpoints are automatically covered (a checklist guard
    # will eventually miss one). Synchronous GET runs in anyio's thread pool and does not share an asyncio-lock
    # mutex domain with the three POSTs; any new GET write would reintroduce WinError 5.
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
                # Match only os.replace; str.replace appears throughout this file.
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

    # First prove traversal found the endpoint; otherwise "no violation" could mean the scan found nothing.
    assert "get_model_config" in checked
    assert "get_model_config_by_id" in checked
    assert len(checked) >= 8
    assert offenders == []


# --- (2) Serialize the full read→modify→write of all three POSTs ------------------


@pytest.mark.asyncio
async def test_concurrent_model_config_posts_do_not_lose_each_others_edits(
    monkeypatch, tmp_path
) -> None:
    """Two different POSTs racing on one model3.json: both edits must survive."""
    # Real file, real handler, no mocked writes. The two updates touch disjoint keys (empty
    # expressions passed for emotion_mapping → empty emotion_prefixes → preserve every existing Expressions entry;
    # model_config payload has no Motions → leave Motions untouched), so order does not matter —
    # both should remain after serialization.
    model_dir = _make_model_dir(tmp_path)
    model_json = model_dir / "m.model3.json"
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/m")
    )

    real_read = live2d_router.read_json_async

    async def slow_read(path, **kwargs):
        data = await real_read(path, **kwargs)
        # Widen the "read to write" window: a lost update occurs here, so guarding only os.replace is insufficient.
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
    # atomic_write_json_async uses asyncio.to_thread internally and cannot cancel the worker once submitted: the thread runs until
    # os.replace finishes. If async with releases the lock as soon as CancelledError passes through (what a direct await does),
    # the second handler can enter the critical section while the first replace is still in flight — exactly the concurrent replace this PR fixes.
    # Client disconnects and timeout middleware cancellations are normal, not edge cases.
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
    # Do not guess cancellation propagation with "a fixed number of yields"; use a short real-time interval and recheck on every loop.
    # Also cover repeated cancellation — timeout cancellation followed by app shutdown is a realistic combination.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 0.1
    while loop.time() < deadline:
        await asyncio.sleep(0.005)
        task.cancel()
        assert lock.locked(), "写还在飞，锁不许因为取消就提前放掉"
        assert not task.done(), "写还没放行，任务不该已经收尾"

    let_write_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked(), "写收尾之后锁必须放掉"


@pytest.mark.asyncio
async def test_by_name_and_by_id_posts_share_one_lock(monkeypatch, tmp_path) -> None:
    """by-name and by-id hitting the same file must share one lock."""
    # Bucketing by input (model_name / model_id) fails when two routes target the same file.
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
    # A second spelling of the same file (realpath collapsing ".") must resolve to the same lock.
    aliased = live2d_router._model_config_write_lock(first.parent / "." / first.name)
    assert aliased is lock
    if os.name == "nt":
        # Windows is case-insensitive, so normcase must fold case.
        assert live2d_router._model_config_write_lock(str(first).upper()) is lock

    # Different files must get distinct lock objects (this fails if they collapse to one global lock).
    assert live2d_router._model_config_write_lock(second) is not lock

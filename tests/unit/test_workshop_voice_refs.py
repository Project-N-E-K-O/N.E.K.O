# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The reference-audio swap must be all-or-nothing under cancellation.

``upload_reference_audio`` replaces a pair of files: the audio sample and
the manifest that points at it. A client disconnect cancels the handler,
and ``CancelledError`` is a ``BaseException`` — the route's ``except
Exception`` never sees it, so nothing rolls back. Any ``await`` between
the first and last mutation is therefore a window where the pair can be
observed half-replaced.

The swap is a single ``asyncio.to_thread`` unit for exactly that reason:
cancelling the awaiting coroutine does not kill the worker thread, so the
three steps still run to completion.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

from tests.atomic_read import read_text_tolerating_replace

from main_routers.workshop_router.voice_manifest import (
    WORKSHOP_VOICE_MANIFEST_NAME,
    resolve_voice_reference_serialized,
)
from main_routers.workshop_router.voice_refs import _replace_voice_reference

pytestmark = pytest.mark.unit


def _seed_existing_reference(folder, audio_name: str = "voice_sample.mp3") -> None:
    (folder / audio_name).write_bytes(b"old-audio")
    (folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({"version": 1, "reference_audio": audio_name, "prefix": "old"}),
        encoding="utf-8",
    )


def _manifest(folder) -> dict:
    # 走容忍 replace 的读法：Windows 上 atomic_write_json 的 os.replace 与并发
    # open() 互斥，裸 read_text 会偶发 PermissionError（见 tests/atomic_read.py）。
    return json.loads(read_text_tolerating_replace(folder / WORKSHOP_VOICE_MANIFEST_NAME))


def test_the_swap_replaces_both_halves(tmp_path):
    _seed_existing_reference(tmp_path)

    _replace_voice_reference(
        str(tmp_path),
        str(tmp_path / "voice_sample.wav"),
        b"new-audio",
        str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
        {"version": 1, "reference_audio": "voice_sample.wav", "prefix": "new"},
    )

    assert (tmp_path / "voice_sample.wav").read_bytes() == b"new-audio"
    assert _manifest(tmp_path)["reference_audio"] == "voice_sample.wav"
    assert not (tmp_path / "voice_sample.mp3").exists(), (
        "换扩展名时旧音频必须被清掉，否则留下孤儿文件"
    )


@pytest.mark.asyncio
async def test_cancelling_the_upload_cannot_leave_a_half_replaced_pair(tmp_path):
    """Cancel the awaiting coroutine mid-swap; the pair must still be whole.

    This is the property the reviewer asked about, asserted directly rather
    than by inspecting how the handler is written.
    """
    _seed_existing_reference(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _slow_swap(*args):
        # 让取消**一定**落在 swap 已经开始之后。
        loop.call_soon_threadsafe(started.set)
        asyncio.run_coroutine_threadsafe(_wait_release(), loop).result(timeout=5)
        _replace_voice_reference(*args)

    async def _wait_release() -> None:
        await release.wait()

    task = asyncio.create_task(
        asyncio.to_thread(
            _slow_swap,
            str(tmp_path),
            str(tmp_path / "voice_sample.wav"),
            b"new-audio",
            str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
            {"version": 1, "reference_audio": "voice_sample.wav", "prefix": "new"},
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 等 worker 自己跑完——取消的是等待方，不是线程。
    #
    # ⚠️ 完成信号必须盯 manifest，不能盯音频：manifest 是 swap 的**最后**一步，
    # 音频出现时它可能还没写。盯错产物这条用例会在 CI 上间歇红（实测 run
    # 30570157903 就是这么挂的）——和 PR #2596 修的是同一类错误。
    deadline = loop.time() + 5.0
    swapped = None
    while loop.time() < deadline:
        try:
            candidate = _manifest(tmp_path)
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            candidate = None
        if candidate and candidate.get("prefix") == "new":
            swapped = candidate
            break
        await asyncio.sleep(0.01)

    assert swapped is not None, "worker 在 5s 内没有把 swap 跑完"
    assert (tmp_path / "voice_sample.wav").read_bytes() == b"new-audio"
    assert swapped["reference_audio"] == "voice_sample.wav", (
        "manifest 必须跟音频一起换掉——半套状态意味着用户拿到一个指不到文件的引用"
    )
    assert not (tmp_path / "voice_sample.mp3").exists()


def test_every_mutation_lives_in_the_offloaded_unit():
    """No mutation may sit in the handler body, where an await can split it.

    The pair is only atomic because all three steps are inside the one
    synchronous helper. Moving any of them back into the coroutine — even
    "just the cleanup" — reopens the window, and nothing else would fail.
    """
    # 用 AST 而不是文本匹配：注释和 docstring 里出现 "await" / "open(" 这些词
    # 是常事，按子串判会把散文当代码。
    import ast
    import inspect

    from main_routers.workshop_router import voice_refs

    module = ast.parse(inspect.getsource(voice_refs))
    by_name = {
        node.name: node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def _called_names(node: ast.AST) -> set[str]:
        names = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
        return names

    MUTATIONS = {"_cleanup_workshop_voice_reference", "open", "atomic_write_json"}

    handler = by_name["upload_reference_audio"]
    leaked = MUTATIONS & _called_names(handler)
    assert not leaked, (
        f"{sorted(leaked)} 回到了协程体里——它和其余两步之间的 await 就是可观测的半套窗口"
    )
    assert "to_thread" in _called_names(handler)

    unit = by_name["_replace_voice_reference"]
    assert isinstance(unit, ast.FunctionDef), "这个单元必须是同步 def"
    assert MUTATIONS <= _called_names(unit), "三步必须都在这个同步单元里"
    assert not any(isinstance(n, ast.Await) for n in ast.walk(unit)), (
        "这个单元里出现 await 就说明它不再是不可分割的"
    )


@pytest.mark.asyncio
async def test_two_uploads_to_one_folder_never_mix_halves(tmp_path, monkeypatch):
    """Concurrent swaps must not leave B's audio next to A's manifest.

    Each swap is atomic against the event loop, but two of them run on two
    worker threads and interleave at the OS level. Before the offload both ran
    on the loop thread and could not; the per-folder lock restores that.

    The interleaving is forced rather than raced for: the first swap is parked
    inside its manifest write until the second one has had its chance to run.
    A version without the lock therefore fails every time instead of once in a
    hundred runs.
    """
    from main_routers.workshop_router import voice_refs

    _seed_existing_reference(tmp_path)
    audio_path = str(tmp_path / "voice_sample.wav")
    manifest_path = str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME)

    at_gate = threading.Event()
    release = threading.Event()
    real_write = voice_refs.atomic_write_json
    first = {"seen": False}

    def _gated_write(*args, **kwargs):
        if not first["seen"]:
            first["seen"] = True
            at_gate.set()
            release.wait(timeout=5)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(voice_refs, "atomic_write_json", _gated_write)

    async def _swap(tag: str) -> None:
        await asyncio.to_thread(
            voice_refs._replace_voice_reference,
            str(tmp_path),
            audio_path,
            f"audio-{tag}".encode(),
            manifest_path,
            {"version": 1, "reference_audio": "voice_sample.wav", "prefix": tag},
        )

    a = asyncio.create_task(_swap("a"))
    await asyncio.to_thread(at_gate.wait, 5)   # A 已经卡在写 manifest 里
    b = asyncio.create_task(_swap("b"))
    await asyncio.sleep(0.05)                  # 给 B 一个真正插进来的机会
    release.set()
    await asyncio.gather(a, b)

    audio = (tmp_path / "voice_sample.wav").read_bytes().decode()
    prefix = _manifest(tmp_path)["prefix"]
    assert audio == f"audio-{prefix}", (
        f"音频来自 {audio}、manifest 来自 {prefix} —— 两半来自不同请求"
    )


@pytest.mark.asyncio
async def test_a_reader_never_observes_a_half_swapped_pair(tmp_path, monkeypatch):
    """Publishing resolves the reference while an upload may be mid-swap.

    A bare read can land between "old pair deleted" and "new manifest
    committed" and fail the publish as an invalid manifest, even though the
    replacement completes right after. The serialized reader takes the same
    per-folder lock — and it already runs in a worker thread, so no event-loop
    code ever waits on it.
    """
    from main_routers.workshop_router import voice_refs

    _seed_existing_reference(tmp_path)
    mid_swap = threading.Event()
    release = threading.Event()
    real_write = voice_refs.atomic_write_json

    def _park_before_manifest(*args, **kwargs):
        mid_swap.set()
        release.wait(timeout=5)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(voice_refs, "atomic_write_json", _park_before_manifest)

    swap = asyncio.create_task(
        asyncio.to_thread(
            voice_refs._replace_voice_reference,
            str(tmp_path),
            str(tmp_path / "voice_sample.wav"),
            b"new-audio",
            str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
            {"version": 1, "reference_audio": "voice_sample.wav", "prefix": "new"},
        )
    )
    await asyncio.to_thread(mid_swap.wait, 5)   # 旧的已删、新 manifest 还没写

    reader = asyncio.create_task(
        asyncio.to_thread(resolve_voice_reference_serialized, str(tmp_path))
    )
    await asyncio.sleep(0.05)
    assert not reader.done(), "读者没被锁挡住，正读在半套状态上"

    release.set()
    await asyncio.gather(swap, reader)
    voice_ref = reader.result()
    assert voice_ref is not None
    assert voice_ref["manifest"]["prefix"] == "new"

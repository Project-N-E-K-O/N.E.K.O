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
import os
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

    MUTATIONS = {
        "_cleanup_workshop_voice_reference", "open", "fdopen", "mkstemp",
        "atomic_write_json", "replace", "remove",
    }

    handler = by_name["upload_reference_audio"]
    leaked = MUTATIONS & _called_names(handler)
    assert not leaked, (
        f"{sorted(leaked)} 回到了协程体里——它和其余步骤之间的 await 就是可观测的半套窗口"
    )
    assert "to_thread" in _called_names(handler)

    unit = by_name["_replace_voice_reference"]
    assert isinstance(unit, ast.FunctionDef), "这个单元必须是同步 def"
    assert {"mkstemp", "replace", "atomic_write_json"} <= _called_names(unit), (
        "暂存音频 → os.replace 顶上去 → 原子写 manifest，三步必须都在这个同步单元里"
    )
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


def test_every_reader_outside_the_swap_takes_the_lock():
    """The rule is uniform: readers and writers of a pair share its lock.

    Pinned structurally because the alternative argument — "these particular
    readers look at Steam's install tree, and uploads only ever write under
    WorkshopExport, so they cannot collide" — is true today and invisible
    tomorrow. A reader that quietly switches back to the unlocked helper would
    otherwise fail nothing.

    The one legitimate unlocked caller is the cleanup reached from inside the
    swap: threading.Lock is not reentrant, so it must stay unlocked.
    """
    import ast
    import inspect

    from main_routers.workshop_router import ugc, voice_manifest, voice_refs

    allowed_unlocked = {
        # 在锁内被调用，必须保持不加锁，否则死锁
        ("voice_manifest", "_cleanup_workshop_voice_reference"),
        # 串行化包装自己就是那把锁
        ("voice_manifest", "resolve_voice_reference_serialized"),
    }

    offenders = []
    for module in (voice_refs, voice_manifest, ugc):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = (module.__name__.rsplit(".", 1)[-1], node.name)
            if key in allowed_unlocked:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if name == "_resolve_workshop_voice_reference":
                    offenders.append(f"{key[0]}.{node.name}:{child.lineno}")

    assert not offenders, (
        f"这些地方绕过了 voice_reference_lock 直接裸读：{offenders}"
    )


def test_a_failed_write_leaves_the_previous_pair_intact(tmp_path, monkeypatch):
    """A failed upload must not destroy the reference the user already had.

    The swap writes the new audio to a staged file and renames it into place
    before anything is deleted, so a disk-full / permission failure on either
    write leaves the previous pair exactly as it was. Deleting first — which is
    what the code did before — turns one failed upload into lost data the user
    cannot get back.
    """
    from main_routers.workshop_router import voice_refs

    _seed_existing_reference(tmp_path)          # voice_sample.mp3 + manifest(prefix=old)

    def _boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(voice_refs, "atomic_write_json", _boom)

    with pytest.raises(OSError):
        voice_refs._replace_voice_reference(
            str(tmp_path),
            str(tmp_path / "voice_sample.wav"),
            b"new-audio",
            str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
            {"version": 1, "reference_audio": "voice_sample.wav", "prefix": "new"},
        )

    assert (tmp_path / "voice_sample.mp3").read_bytes() == b"old-audio", (
        "写失败把用户原来的参考语音删掉了"
    )
    assert _manifest(tmp_path)["prefix"] == "old", "旧 manifest 也必须还在"


def test_a_failed_audio_write_stages_nothing(tmp_path, monkeypatch):
    """The staged temp file must not survive a failure either."""
    from main_routers.workshop_router import voice_refs

    _seed_existing_reference(tmp_path)
    real_replace = os.replace

    def _boom(src, dst):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        voice_refs._replace_voice_reference(
            str(tmp_path),
            str(tmp_path / "voice_sample.wav"),
            b"new-audio",
            str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
            {"version": 1, "reference_audio": "voice_sample.wav", "prefix": "new"},
        )
    monkeypatch.setattr(os, "replace", real_replace)

    assert (tmp_path / "voice_sample.mp3").read_bytes() == b"old-audio"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"失败路径留下了暂存文件：{leftovers}"


def test_a_failed_manifest_write_never_touches_the_live_pair(tmp_path, monkeypatch):
    """The commit point is the manifest; nothing before it may be destructive.

    The new audio lands under its own filename, so the file the current
    manifest points at is never overwritten. A failed manifest write therefore
    leaves the previous pair byte-for-byte intact — the earlier "overwrite then
    restore from a backup" shape had a window where the pair came from two
    different uploads, and every rollback path was one more thing that could
    itself fail.
    """
    from main_routers.workshop_router import voice_refs

    (tmp_path / "voice_sample_aaaaaaaaaaaa.wav").write_bytes(b"old-audio")
    (tmp_path / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({"version": 1, "reference_audio": "voice_sample_aaaaaaaaaaaa.wav", "prefix": "old"}),
        encoding="utf-8",
    )

    def _boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(voice_refs, "atomic_write_json", _boom)

    with pytest.raises(OSError):
        voice_refs._replace_voice_reference(
            str(tmp_path),
            str(tmp_path / "voice_sample_bbbbbbbbbbbb.wav"),
            b"new-audio",
            str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
            {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
        )

    assert (tmp_path / "voice_sample_aaaaaaaaaaaa.wav").read_bytes() == b"old-audio"
    assert _manifest(tmp_path)["prefix"] == "old", "旧 manifest 必须原样还在"
    assert (tmp_path / "voice_sample_aaaaaaaaaaaa.wav").exists(), (
        "提交点之前不许动当前 manifest 指着的那个文件"
    )


def test_a_successful_replace_leaves_no_staging_behind(tmp_path):
    """No temp file may outlive a successful swap."""
    from main_routers.workshop_router import voice_refs

    (tmp_path / "voice_sample_aaaaaaaaaaaa.wav").write_bytes(b"old-audio")
    (tmp_path / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({"version": 1, "reference_audio": "voice_sample_aaaaaaaaaaaa.wav", "prefix": "old"}),
        encoding="utf-8",
    )

    voice_refs._replace_voice_reference(
        str(tmp_path),
        str(tmp_path / "voice_sample_bbbbbbbbbbbb.wav"),
        b"new-audio",
        str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
        {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
    )

    assert (tmp_path / "voice_sample_bbbbbbbbbbbb.wav").read_bytes() == b"new-audio"
    assert _manifest(tmp_path)["prefix"] == "new"
    leftovers = sorted(p.name for p in tmp_path.iterdir() if ".tmp" in p.name)
    assert leftovers == [], f"成功路径留下了暂存文件：{leftovers}"


def test_each_upload_gets_its_own_audio_filename(tmp_path):
    """The handler must never reuse the filename the live manifest points at.

    That is what makes the manifest write the single commit point: if the new
    audio could land on the live filename, a failure between the two writes
    would leave new audio wearing old metadata, and the resolver — which checks
    only the manifest-named file's existence — would accept the mismatched
    pair as valid.
    """
    import ast
    import inspect

    from main_routers.workshop_router import voice_refs

    tree = ast.parse(inspect.getsource(voice_refs.upload_reference_audio))
    assigned = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "reference_audio_name"
            for t in node.targets
        )
    ]
    assert len(assigned) == 1
    source = ast.unparse(assigned[0].value)
    assert "uuid" in source, (
        f"参考音频文件名不再是每次唯一的（{source}）——manifest 写就不再是唯一提交点"
    )




def test_the_lock_key_resolves_symlinks(tmp_path):
    """The same physical directory must map to one lock, however it is reached.

    `abspath` is purely lexical, so a junction/symlink pointing at the same
    folder would silently hand out two different locks and the serialization
    would stop working with no error anywhere.
    """
    from main_routers.workshop_router.voice_manifest import voice_reference_lock

    real_dir = tmp_path / "content"
    real_dir.mkdir()
    link_dir = tmp_path / "linked"
    try:
        os.symlink(real_dir, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"这个环境不允许建符号链接: {exc}")

    assert voice_reference_lock(str(real_dir)) is voice_reference_lock(str(link_dir)), (
        "同一个目录经由 symlink 进来时拿到了两把锁——串行化会静默失效"
    )




def test_only_the_previously_referenced_audio_is_deleted(tmp_path):
    """Ownership is proven by the manifest, never guessed from the filename.

    A content folder is the user's own publish directory. Matching by name
    shape — even the exact `voice_sample_<12 hex>.<ext>` this feature
    generates — is still probability: a user file that happens to fit the
    shape gets silently deleted. The one file this module can prove it owns is
    the one the outgoing manifest pointed at.
    """
    from main_routers.workshop_router import voice_refs

    (tmp_path / "voice_sample_aaaaaaaaaaaa.mp3").write_bytes(b"ours-previous")
    (tmp_path / "voice_sample_cccccccccccc.wav").write_bytes(b"user-file-same-shape")
    (tmp_path / "voice_sample.mp3").write_bytes(b"user-file-legacy-shape")
    (tmp_path / "voice_sample_theme.mp3").write_bytes(b"user-file")
    (tmp_path / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({
            "version": 1,
            "reference_audio": "voice_sample_aaaaaaaaaaaa.mp3",
            "prefix": "old",
        }),
        encoding="utf-8",
    )

    voice_refs._replace_voice_reference(
        str(tmp_path),
        str(tmp_path / "voice_sample_bbbbbbbbbbbb.wav"),
        b"new-audio",
        str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
        {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
    )

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [
        WORKSHOP_VOICE_MANIFEST_NAME,
        "voice_sample.mp3",
        "voice_sample_bbbbbbbbbbbb.wav",
        "voice_sample_cccccccccccc.wav",
        "voice_sample_theme.mp3",
    ], f"删了不属于自己的文件，或者没删掉上一份引用：{remaining}"


def test_nothing_is_deleted_when_no_manifest_claims_anything(tmp_path):
    """A folder with no manifest claims nothing; the swap must add, not remove."""
    from main_routers.workshop_router import voice_refs

    (tmp_path / "voice_sample.wav").write_bytes(b"user-file")

    voice_refs._replace_voice_reference(
        str(tmp_path),
        str(tmp_path / "voice_sample_bbbbbbbbbbbb.wav"),
        b"new-audio",
        str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
        {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
    )

    assert (tmp_path / "voice_sample.wav").read_bytes() == b"user-file"


def test_a_manifest_pointing_outside_the_folder_deletes_nothing(tmp_path):
    """`reference_audio` is untrusted input; never delete outside the folder.

    The manifest can arrive from a subscribed item or be hand-edited. An
    absolute path or `../../x` in `reference_audio` would otherwise make the
    post-commit cleanup remove a file elsewhere on disk.
    """
    from main_routers.workshop_router import voice_refs

    content = tmp_path / "content"
    content.mkdir()
    outsider = tmp_path / "precious.wav"
    outsider.write_bytes(b"not-ours")

    for hostile in ("../precious.wav", str(outsider)):
        (content / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "reference_audio": hostile, "prefix": "old"}),
            encoding="utf-8",
        )
        assert voice_refs._current_reference_audio_path(str(content)) is None, (
            f"{hostile!r} 被当成了可删除的目标"
        )

        voice_refs._replace_voice_reference(
            str(content),
            str(content / "voice_sample_bbbbbbbbbbbb.wav"),
            b"new-audio",
            str(content / WORKSHOP_VOICE_MANIFEST_NAME),
            {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
        )
        assert outsider.read_bytes() == b"not-ours", f"{hostile!r} 让清理删到了目录外面"


def test_a_manifest_naming_a_non_audio_asset_deletes_nothing(tmp_path):
    """`reference_audio` must look like reference audio before we own it.

    A hand-edited manifest naming an in-folder asset such as `preview.png`
    would otherwise make an ordinary upload permanently remove unrelated
    workshop content from the publish directory.
    """
    from main_routers.workshop_router import voice_refs

    (tmp_path / "preview.png").write_bytes(b"user-artwork")
    (tmp_path / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({"version": 1, "reference_audio": "preview.png", "prefix": "old"}),
        encoding="utf-8",
    )

    assert voice_refs._current_reference_audio_path(str(tmp_path)) is None

    voice_refs._replace_voice_reference(
        str(tmp_path),
        str(tmp_path / "voice_sample_bbbbbbbbbbbb.wav"),
        b"new-audio",
        str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
        {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
    )

    assert (tmp_path / "preview.png").read_bytes() == b"user-artwork", (
        "清理把用户的工坊素材删了"
    )


def test_a_nested_reference_is_not_treated_as_owned(tmp_path):
    """This module only ever writes directly into the content folder.

    `assets/theme.mp3` passes containment and the audio-extension check, but a
    reference with a directory component was never written by us — and
    `_normalize_workshop_voice_manifest` basenames it anyway, so no normal
    reader resolves it to the nested file either. Deleting it would remove
    unrelated user content from a subdirectory.
    """
    from main_routers.workshop_router import voice_refs

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "theme.mp3").write_bytes(b"user-track")
    (tmp_path / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({"version": 1, "reference_audio": "assets/theme.mp3", "prefix": "old"}),
        encoding="utf-8",
    )

    assert voice_refs._current_reference_audio_path(str(tmp_path)) is None

    voice_refs._replace_voice_reference(
        str(tmp_path),
        str(tmp_path / "voice_sample_bbbbbbbbbbbb.wav"),
        b"new-audio",
        str(tmp_path / WORKSHOP_VOICE_MANIFEST_NAME),
        {"version": 1, "reference_audio": "voice_sample_bbbbbbbbbbbb.wav", "prefix": "new"},
    )

    assert (assets / "theme.mp3").read_bytes() == b"user-track", "删了子目录里的用户素材"

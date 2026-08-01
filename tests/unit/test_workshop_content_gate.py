# -*- coding: utf-8 -*-
"""What a publish validates must be what a publish ships.

The first half of this file pins the claim registry's own semantics. The
second half asks the question the registry exists to answer: with the real
publish and reference-audio code paths running concurrently, is the window
between "preflight approved this pair" and "Steam finished reading the
folder" actually closed?

That distinction matters because the registry can be perfectly correct and
the window still open -- it was, before ``content_gate`` existed, with the
per-folder ``threading.Lock`` released the moment the preflight returned.
So the tests below drive ``_preflight_and_publish`` and
``_replace_voice_reference`` against real files and compare the bytes Steam
sees against the bytes the preflight approved, rather than asserting on
where the claims happen to sit.

Two structural guards close out the file. Both defend rules that nothing
else would notice being broken: a claim must never be taken on the event
loop (cancellation would release the folder with the worker still writing
to it), and every call that consumes or destroys a content folder must sit
lexically *inside* a claim, not merely in a function that takes one
somewhere.
"""

import ast
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import inspect
import json
import os
from pathlib import Path
import threading
import time

import pytest

from tests.atomic_read import read_text_tolerating_replace

from main_routers.workshop_router import content_gate, publish
from main_routers.workshop_router.content_gate import (
    CLEANUP_PURPOSE,
    PUBLISH_PURPOSE,
    ContentFolderBusy,
    claim_content_folder,
    claim_partial_writer,
    claim_reference_pair,
)
from main_routers.workshop_router.voice_manifest import WORKSHOP_VOICE_MANIFEST_NAME


@pytest.fixture(autouse=True)
def _registry_must_be_empty_afterwards():
    """Every claim taken in a test must be gone by the end of it.

    A leaked claim is not cosmetic bookkeeping: that folder answers 409
    forever. Asserting it once here covers every test in the file, instead
    of one dedicated case that only ever proves the happy path.
    """
    yield
    leaked_exclusive = dict(content_gate._EXCLUSIVE)
    leaked_partial = dict(content_gate._PARTIAL_WRITERS)
    content_gate._EXCLUSIVE.clear()
    content_gate._PARTIAL_WRITERS.clear()
    assert leaked_exclusive == {}, f"独占占用泄漏：{leaked_exclusive}"
    assert leaked_partial == {}, f"共享占用泄漏：{leaked_partial}"


def _raise_inside(claim):
    with claim:
        raise RuntimeError('boom')


def test_exclusive_claim_rejects_another_exclusive_claim(tmp_path):
    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
        with pytest.raises(ContentFolderBusy, match='正在发布'):
            with claim_content_folder(str(tmp_path), purpose=CLEANUP_PURPOSE):
                pass


def test_exclusive_claim_rejects_reference_writer(tmp_path):
    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
        with pytest.raises(ContentFolderBusy, match='正在发布'):
            with claim_reference_pair(str(tmp_path)):
                pass


def test_reference_writer_rejects_exclusive_claim(tmp_path):
    with claim_reference_pair(str(tmp_path)):
        with pytest.raises(ContentFolderBusy, match='局部文件正在写入'):
            with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
                pass


def test_partial_writers_remain_shared(tmp_path):
    with claim_reference_pair(str(tmp_path)):
        with claim_partial_writer(str(tmp_path), purpose='上传预览图'):
            pass
        with pytest.raises(ContentFolderBusy, match='局部文件正在写入'):
            with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
                pass


def test_parent_and_descendant_claims_conflict_in_both_directions(tmp_path):
    child = tmp_path / 'nested'
    child.mkdir()

    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
        with pytest.raises(ContentFolderBusy):
            with claim_partial_writer(str(child), purpose='上传预览图'):
                pass

    with claim_partial_writer(str(child), purpose='上传预览图'):
        with pytest.raises(ContentFolderBusy):
            with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
                pass


def test_claim_releases_after_exception(tmp_path):
    with pytest.raises(RuntimeError, match='boom'):
        _raise_inside(
            claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE)
        )

    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE) as token:
        assert token is None


def test_reference_claim_releases_after_exception(tmp_path):
    with pytest.raises(RuntimeError, match='boom'):
        _raise_inside(claim_reference_pair(str(tmp_path)))

    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE) as token:
        assert token is None


def test_claim_key_collapses_relative_aliases(tmp_path, monkeypatch):
    child = tmp_path / 'item'
    child.mkdir()
    monkeypatch.chdir(tmp_path)

    with claim_content_folder(str(child), purpose=PUBLISH_PURPOSE):
        with pytest.raises(ContentFolderBusy):
            with claim_reference_pair('item'):
                pass


def test_unrelated_folders_do_not_block_each_other(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()

    with claim_content_folder(str(first), purpose=PUBLISH_PURPOSE):
        with claim_content_folder(str(second), purpose=PUBLISH_PURPOSE):
            pass


def test_concurrent_loser_gets_busy_instead_of_waiting(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def hold_folder():
        with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
            entered.set()
            assert release.wait(timeout=2)

    worker = threading.Thread(target=hold_folder)
    worker.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(ContentFolderBusy):
            with claim_reference_pair(str(tmp_path)):
                pass
    finally:
        release.set()
        worker.join(timeout=2)

    assert not worker.is_alive()


def test_publish_holds_the_folder_across_preflight_and_steam_upload(
    tmp_path, monkeypatch
):
    observed = []

    def resolve(folder):
        observed.append(('preflight', folder))
        with pytest.raises(ContentFolderBusy):
            with claim_reference_pair(folder):
                pass
        return None

    def upload(*args):
        folder = args[3]
        observed.append(('upload', folder))
        with pytest.raises(ContentFolderBusy):
            with claim_reference_pair(folder):
                pass
        return 123

    monkeypatch.setattr(publish, 'resolve_voice_reference_serialized', resolve)
    monkeypatch.setattr(publish, '_publish_workshop_item', upload)

    result = publish._preflight_and_publish(
        object(), 'title', 'description', str(tmp_path), '', 0, [], 'note'
    )

    assert result == 123
    assert observed == [
        ('preflight', str(tmp_path)),
        ('upload', str(tmp_path)),
    ]
    with claim_reference_pair(str(tmp_path)):
        pass


def test_failed_publish_releases_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(
        publish, 'resolve_voice_reference_serialized', lambda _folder: None
    )

    def fail(*_args):
        raise RuntimeError('upload failed')

    monkeypatch.setattr(publish, '_publish_workshop_item', fail)

    with pytest.raises(RuntimeError, match='upload failed'):
        publish._preflight_and_publish(
            object(), 'title', 'description', str(tmp_path), '', 0, [], 'note'
        )

    with claim_reference_pair(str(tmp_path)):
        pass


def test_rejected_voice_preflight_releases_the_folder(tmp_path, monkeypatch):
    def reject(_folder):
        raise ValueError('bad manifest')

    monkeypatch.setattr(publish, 'resolve_voice_reference_serialized', reject)

    with pytest.raises(publish._VoicePreflightError, match='bad manifest'):
        publish._preflight_and_publish(
            object(), 'title', 'description', str(tmp_path), '', 0, [], 'note'
        )

    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
        pass


# ── does the real window actually close? ────────────────────────────────


def _seed_pair(folder, audio_name: str, audio: bytes, *, prefix: str) -> None:
    (folder / audio_name).write_bytes(audio)
    (folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({'version': 1, 'reference_audio': audio_name, 'prefix': prefix}),
        encoding='utf-8',
    )


def _snapshot_pair(content_folder: str) -> dict:
    """Read the pair the way a consumer of the whole folder would see it."""
    manifest = json.loads(
        read_text_tolerating_replace(
            os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME)
        )
    )
    audio_path = os.path.join(content_folder, manifest['reference_audio'])
    audio = None
    if os.path.exists(audio_path):
        with open(audio_path, 'rb') as f:
            audio = f.read()
    return {
        'reference_audio': manifest['reference_audio'],
        'prefix': manifest.get('prefix'),
        'audio': audio,
    }


def _publish_args(content_folder: str) -> tuple:
    return (object(), 'title', 'description', content_folder, '', 0, [], 'note', None)


def _swap_args(content_folder, audio_name: str, audio: bytes, prefix: str) -> tuple:
    return (
        str(content_folder),
        os.path.join(str(content_folder), audio_name),
        audio,
        os.path.join(str(content_folder), WORKSHOP_VOICE_MANIFEST_NAME),
        {'version': 1, 'reference_audio': audio_name, 'prefix': prefix},
    )


# 宽到只有真的挂住了才会到点。放行永远由 `_worker_parked_at` 的 finally 保证，
# 所以短超时买不到任何安全性，只会在负载高的 runner 上把交错悄悄拆掉：假 worker
# 提前离开它该卡住的位置、提前放开占用，于是竞争方合法地拿到了 claim，用例把这
# 报成一次「互斥失效」——一个纯粹由超时造出来的假回归。
_SYNC_TIMEOUT = 30.0
_DRAIN_TIMEOUT = 5.0


async def _drain(task, *, timeout: float = _DRAIN_TIMEOUT) -> bool:
    """Bound cleanup and retrieve the outcome without masking the test verdict."""
    _, pending = await asyncio.wait({task}, timeout=timeout)
    if pending:
        task.cancel()
        await asyncio.wait({task}, timeout=1.0)
        return False
    if not task.cancelled():
        task.exception()
    return True


def _run_worker(done: threading.Event, func, *args):
    """Expose completion separately from the cancellable asyncio wrapper."""
    try:
        return func(*args)
    finally:
        done.set()


@pytest.mark.asyncio
async def test_drain_is_bounded():
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())

    assert await _drain(task, timeout=0.01) is False
    assert task.cancelled()


@asynccontextmanager
async def _worker_parked_at(
    gate: threading.Event,
    release: threading.Event,
    task,
    what: str,
    *,
    worker_done: threading.Event | None = None,
):
    """Run the body while ``task``'s worker sits parked, then always let it go.

    Three things have to hold together here, and each one was its own defect
    before it did:

    * the checkpoint is **asserted**, so a synchronisation timeout says "the
      interleaving never happened" instead of letting the body fail as
      ``DID NOT RAISE`` -- which reads like the exclusion is broken;
    * the checkpoint sits **inside** the cleanup, so a failed checkpoint still
      releases the parked worker instead of leaving it holding the claim;
    * the task is **drained**, so a late worker cannot acquire the claim after
      teardown has begun -- that surfaces as a registry-leak error pointing at
      the wrong thing, or as the worker running on after monkeypatch put the
      real upload function back.
    """
    failed = False
    try:
        assert await asyncio.to_thread(gate.wait, _SYNC_TIMEOUT), (
            f'{what} 没在 {_SYNC_TIMEOUT:.0f}s 内就位——交错没建立起来，'
            f'后面的断言证明不了任何东西'
        )
        yield
    except BaseException:
        failed = True
        raise
    finally:
        release.set()
        worker_finished = True
        if worker_done is not None:
            worker_finished = await asyncio.to_thread(
                worker_done.wait, _DRAIN_TIMEOUT
            )
        drained = await _drain(task)
        if not failed:
            assert worker_finished, (
                f'{what} 的 asyncio 等待方已经结束，但 worker 没有真正收尾'
            )
            assert drained, f'{what} 放行后仍未在 {_DRAIN_TIMEOUT:.0f}s 内结束'


def _wait_until_nobody_holds(content_folder: str, *, timeout: float = _SYNC_TIMEOUT) -> bool:
    """Poll until no claim of either kind is left, so a test can assert release.

    Probes with the *exclusive* claim deliberately. ``claim_reference_pair``
    is the natural-looking probe and is useless for this: it is excluded only
    by exclusive holders, so a shared claim held forever sails straight
    through it and the assertion becomes unconditionally true.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            with claim_content_folder(content_folder, purpose=CLEANUP_PURPOSE):
                return True
        except ContentFolderBusy:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


async def test_a_reference_swap_cannot_slip_into_the_steam_upload(tmp_path, monkeypatch):
    """Publish parked inside SetItemContent; a concurrent upload must be refused.

    The pair Steam reads when the upload starts and the pair it reads when the
    upload ends have to be the one the preflight approved. Without the claim
    the swap lands in between and the item ships audio nothing ever validated.

    The interleaving is forced rather than raced for -- the fake upload blocks
    until the swap has had its turn -- so an unguarded build fails every run
    instead of one in a hundred.
    """
    from main_routers.workshop_router import voice_refs

    _seed_pair(tmp_path, 'voice_sample_aaaaaaaaaaaa.wav', b'validated-audio', prefix='validated')

    uploading = threading.Event()
    finish = threading.Event()
    seen: list = []

    def _fake_steam_upload(steamworks, title, description, content_folder, *rest):
        seen.append(_snapshot_pair(content_folder))    # SetItemContent 开始读目录
        uploading.set()
        assert finish.wait(timeout=_SYNC_TIMEOUT), '放行信号没来——worker 提前离开了它该卡住的位置'
        seen.append(_snapshot_pair(content_folder))    # 读完
        return 4242

    monkeypatch.setattr(publish, '_publish_workshop_item', _fake_steam_upload)

    preflighted: dict = {}
    real_resolve = publish.resolve_voice_reference_serialized

    def _recording_resolve(folder):
        resolved = real_resolve(folder)
        preflighted['manifest'] = dict(resolved['manifest'])
        return resolved

    monkeypatch.setattr(publish, 'resolve_voice_reference_serialized', _recording_resolve)

    publishing = asyncio.create_task(
        asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))
    )
    async with _worker_parked_at(uploading, finish, publishing, '假 SetItemContent'):
        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(
                voice_refs._replace_voice_reference,
                *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'sneaked-in', 'sneaked'),
            )

    assert publishing.result() == 4242

    assert seen[0] == seen[1], f'Steam 读的过程中这对文件被换掉了：{seen}'
    assert seen[0]['reference_audio'] == preflighted['manifest']['reference_audio'], (
        '发出去的 manifest 跟 preflight 校验的不是同一份'
    )
    assert seen[0]['audio'] == b'validated-audio'
    assert not (tmp_path / 'voice_sample_bbbbbbbbbbbb.wav').exists(), (
        '被拒绝的上传不许在目录里留下任何东西——它会跟着这次发布传上去'
    )


async def test_a_delete_cannot_slip_into_the_steam_upload(tmp_path, monkeypatch):
    """Removing the pair mid-upload is the same defect, one step worse."""
    from main_routers.workshop_router import voice_refs

    _seed_pair(tmp_path, 'voice_sample.wav', b'validated-audio', prefix='validated')

    uploading = threading.Event()
    finish = threading.Event()

    def _fake_steam_upload(steamworks, title, description, content_folder, *rest):
        uploading.set()
        assert finish.wait(timeout=_SYNC_TIMEOUT), '放行信号没来——worker 提前离开了它该卡住的位置'
        return 7

    monkeypatch.setattr(publish, '_publish_workshop_item', _fake_steam_upload)

    publishing = asyncio.create_task(
        asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))
    )
    async with _worker_parked_at(uploading, finish, publishing, '假 SetItemContent'):
        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(voice_refs._remove_voice_reference, str(tmp_path))

    assert publishing.result() == 7, '发布本身必须照常跑完——被挡的是删，不是它'

    assert (tmp_path / 'voice_sample.wav').read_bytes() == b'validated-audio'
    assert _snapshot_pair(str(tmp_path))['prefix'] == 'validated'


async def test_a_publish_cannot_start_on_top_of_a_running_swap(tmp_path, monkeypatch):
    """The exclusion runs both ways, or the loser just wins by arriving second."""
    from main_routers.workshop_router import voice_refs

    _seed_pair(tmp_path, 'voice_sample.wav', b'old-audio', prefix='old')

    mid_swap = threading.Event()
    finish = threading.Event()
    real_write = voice_refs.atomic_write_json

    def _park_before_commit(*args, **kwargs):
        mid_swap.set()
        assert finish.wait(timeout=_SYNC_TIMEOUT), '放行信号没来——worker 提前离开了它该卡住的位置'
        return real_write(*args, **kwargs)

    monkeypatch.setattr(voice_refs, 'atomic_write_json', _park_before_commit)
    monkeypatch.setattr(
        publish, '_publish_workshop_item',
        lambda *a, **kw: pytest.fail('发布不该在 swap 还没结束时就开始'),
    )

    swapping = asyncio.create_task(
        asyncio.to_thread(
            voice_refs._replace_voice_reference,
            *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'new-audio', 'new'),
        )
    )
    async with _worker_parked_at(mid_swap, finish, swapping, 'swap 的提交点'):
        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))

    assert _snapshot_pair(str(tmp_path))['prefix'] == 'new', (
        '被挡下的发布不该影响 swap 自己——它必须照常提交完'
    )
    assert not (tmp_path / 'voice_sample.wav').exists(), (
        '提交新 pair 后旧录音必须被删掉，否则 Steam 仍会把它一起上传'
    )


async def test_cancelling_the_publish_does_not_free_the_folder_early(tmp_path, monkeypatch):
    """Cancelling the waiter does not stop Steam; the folder must stay claimed.

    This is the case that decides where the claim lives. Taken on the event
    loop and released in the coroutine's ``finally``, a client disconnect would
    free the folder with the upload still reading it -- the exact window the
    gate exists to close, reopened by its own cleanup path.
    """
    from main_routers.workshop_router import voice_refs

    _seed_pair(tmp_path, 'voice_sample_aaaaaaaaaaaa.wav', b'validated-audio', prefix='validated')

    uploading = threading.Event()
    finish = threading.Event()
    worker_done = threading.Event()

    def _slow_upload(steamworks, title, description, content_folder, *rest):
        uploading.set()
        assert finish.wait(timeout=_SYNC_TIMEOUT), '放行信号没来——worker 提前离开了它该卡住的位置'
        return 99

    monkeypatch.setattr(publish, '_publish_workshop_item', _slow_upload)

    task = asyncio.create_task(
        asyncio.to_thread(
            _run_worker,
            worker_done,
            publish._preflight_and_publish,
            *_publish_args(str(tmp_path)),
        )
    )
    async with _worker_parked_at(
        uploading, finish, task, '假 SetItemContent', worker_done=worker_done
    ):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(
                voice_refs._replace_voice_reference,
                *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'sneaked-in', 'sneaked'),
            )

    assert await asyncio.to_thread(_wait_until_nobody_holds, str(tmp_path)), (
        'worker 跑完之后目录还是被占着——占用泄漏了'
    )


async def test_cancelling_the_upload_does_not_free_the_pair_early(tmp_path, monkeypatch):
    """Same rule on the mutation side: the swap thread keeps its claim.

    Released by the cancelled coroutine instead, a publish could claim the
    folder and preflight it *before* this thread even reaches
    ``voice_reference_lock`` -- reading the old pair, then handing Steam a
    directory the swap is about to rewrite.
    """
    from main_routers.workshop_router import voice_refs

    _seed_pair(tmp_path, 'voice_sample.wav', b'old-audio', prefix='old')

    swapping = threading.Event()
    finish = threading.Event()
    worker_done = threading.Event()
    real_write = voice_refs.atomic_write_json

    def _park_before_commit(*args, **kwargs):
        swapping.set()
        assert finish.wait(timeout=_SYNC_TIMEOUT), '放行信号没来——worker 提前离开了它该卡住的位置'
        return real_write(*args, **kwargs)

    monkeypatch.setattr(voice_refs, 'atomic_write_json', _park_before_commit)

    task = asyncio.create_task(
        asyncio.to_thread(
            _run_worker,
            worker_done,
            voice_refs._replace_voice_reference,
            *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'new-audio', 'new'),
        )
    )
    async with _worker_parked_at(
        swapping, finish, task, 'swap 的提交点', worker_done=worker_done
    ):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))

    assert await asyncio.to_thread(_wait_until_nobody_holds, str(tmp_path)), (
        'swap 跑完之后这对文件还是被占着——占用泄漏了'
    )


# ── the routes answer 409, not 500 ──────────────────────────────────────


class _JsonRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


class _StubUploadFile:
    def __init__(self, filename: str, data: bytes, content_type: str = 'audio/wav') -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FormRequest:
    def __init__(self, fields: dict) -> None:
        self._fields = fields

    async def form(self) -> dict:
        return self._fields


@pytest.fixture
def export_folder(tmp_path, monkeypatch):
    """A content folder under WorkshopExport, which is all the routes accept."""
    from main_routers.workshop_router import voice_refs

    folder = tmp_path / 'WorkshopExport' / 'item_abc123'
    folder.mkdir(parents=True)

    async def _workshop_path():
        return str(tmp_path)

    monkeypatch.setattr(voice_refs, 'get_workshop_path_async', _workshop_path)
    monkeypatch.setattr(publish, 'get_workshop_path_async', _workshop_path)
    return folder


async def test_uploading_during_a_publish_answers_409(export_folder):
    from main_routers.workshop_router import voice_refs

    with claim_content_folder(str(export_folder), purpose=PUBLISH_PURPOSE):
        response = await voice_refs.upload_reference_audio(_FormRequest({
            'file': _StubUploadFile('sample.wav', b'audio-bytes'),
            'content_folder': str(export_folder),
            'prefix': 'neko',
        }))

    assert response.status_code == 409, (
        '被发布挡住是「等会儿再来」，不是 500——500 会让前端把它当成坏掉了'
    )
    assert json.loads(response.body)['success'] is False
    assert list(export_folder.iterdir()) == [], '被拒绝的上传不许落盘'


async def test_uploading_a_preview_image_during_a_publish_answers_409(
    export_folder,
):
    from main_routers.workshop_router import preview_cards

    with claim_content_folder(str(export_folder), purpose=PUBLISH_PURPOSE):
        response = await preview_cards.upload_preview_image(_FormRequest({
            'file': _StubUploadFile(
                'cover.png', b'png-bytes', content_type='image/png'
            ),
            'content_folder': str(export_folder),
        }))

    assert response.status_code == 409, json.loads(response.body)
    assert list(export_folder.iterdir()) == [], '被拒绝的预览图不许落盘'


def test_a_cleaned_up_folder_is_not_recreated_by_the_preview_write(tmp_path):
    from main_routers.workshop_router import preview_cards

    folder = tmp_path / 'gone'
    with pytest.raises(preview_cards._PreviewContentFolderMissing):
        preview_cards._write_claimed_preview_image(
            str(folder), str(folder / 'preview.png'), b'png-bytes'
        )

    assert not folder.exists(), '内容目录已被清理时不许把它重新建出来'


async def test_removing_during_a_publish_answers_409(export_folder):
    from main_routers.workshop_router import voice_refs

    _seed_pair(export_folder, 'voice_sample.wav', b'audio', prefix='p')

    with claim_content_folder(str(export_folder), purpose=PUBLISH_PURPOSE):
        response = await voice_refs.remove_reference_audio(
            _JsonRequest({'content_folder': str(export_folder)})
        )

    assert response.status_code == 409
    assert (export_folder / 'voice_sample.wav').exists(), '409 之后文件必须还在'


async def test_deleting_the_temp_folder_during_a_publish_answers_409(export_folder):
    """The cancel-upload button in the frontend reaches here mid-publish.

    ``rmtree`` under a running SetItemContent cancels nothing -- it makes the
    upload fail in a way nobody can read.
    """
    (export_folder / 'keep.txt').write_text('x', encoding='utf-8')

    with claim_content_folder(str(export_folder), purpose=PUBLISH_PURPOSE):
        response = await publish.cleanup_temp_folder(
            _JsonRequest({'temp_folder': str(export_folder)})
        )

    assert response.status_code == 409
    assert export_folder.exists(), '发布还在跑的时候把内容目录删掉了'


async def test_deleting_the_temp_folder_during_a_reference_swap_answers_409(export_folder):
    """Cleanup needs an exclusive claim; pair writers are shared with each other."""
    (export_folder / 'keep.txt').write_text('x', encoding='utf-8')

    with claim_reference_pair(str(export_folder)):
        response = await publish.cleanup_temp_folder(
            _JsonRequest({'temp_folder': str(export_folder)})
        )

    assert response.status_code == 409
    assert export_folder.exists(), '参考语音还在改写时把整个内容目录删掉了'


async def test_publishing_during_a_publish_answers_409(export_folder, monkeypatch):
    """Exercise the public handler, including its exception ordering."""
    (export_folder / 'content.txt').write_text('payload', encoding='utf-8')
    monkeypatch.setattr(publish, 'get_steamworks', object)
    monkeypatch.setattr(publish, '_is_workshop_publish_native_crash_risk', bool)

    with claim_content_folder(str(export_folder), purpose=PUBLISH_PURPOSE):
        response = await publish.publish_to_workshop(_JsonRequest({
            'title': 'busy item',
            'content_folder': str(export_folder),
            'visibility': 0,
        }))

    assert response.status_code == 409, json.loads(response.body)
    assert json.loads(response.body)['success'] is False


async def test_publishing_without_a_claim_returns_the_published_id(export_folder, monkeypatch):
    """The public handler must still finish its ordinary success path."""
    (export_folder / 'content.txt').write_text('payload', encoding='utf-8')
    monkeypatch.setattr(publish, 'get_steamworks', object)
    monkeypatch.setattr(publish, '_is_workshop_publish_native_crash_risk', bool)

    def _published_id(*args, **kwargs):
        return 4242

    monkeypatch.setattr(publish, '_publish_workshop_item', _published_id)
    response = await publish.publish_to_workshop(_JsonRequest({
        'title': 'ordinary item',
        'content_folder': str(export_folder),
        'visibility': 0,
    }))

    assert response.status_code == 200, json.loads(response.body)
    assert json.loads(response.body)['published_file_id'] == 4242


async def test_the_routes_still_work_when_nothing_holds_the_folder(export_folder):
    """The gate must not 409 the ordinary path -- that would be worse."""
    from main_routers.workshop_router import voice_refs

    response = await voice_refs.upload_reference_audio(_FormRequest({
        'file': _StubUploadFile('sample.wav', b'audio-bytes'),
        'content_folder': str(export_folder),
        'prefix': 'neko',
    }))
    assert response.status_code == 200, json.loads(response.body)

    manifest = json.loads(
        read_text_tolerating_replace(export_folder / WORKSHOP_VOICE_MANIFEST_NAME)
    )
    assert (export_folder / manifest['reference_audio']).read_bytes() == b'audio-bytes'

    removed = await voice_refs.remove_reference_audio(
        _JsonRequest({'content_folder': str(export_folder)})
    )
    assert removed.status_code == 200
    assert not (export_folder / WORKSHOP_VOICE_MANIFEST_NAME).exists()

    cleaned = await publish.cleanup_temp_folder(
        _JsonRequest({'temp_folder': str(export_folder)})
    )
    assert cleaned.status_code == 200, json.loads(cleaned.body)
    assert not export_folder.exists(), '正常 cleanup 必须真的删掉目录'


# ── structural guards ───────────────────────────────────────────────────


_CLAIM_CALLS = {
    'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
}
_CALLBACK_ITERATORS = {'filter', 'map', 'starmap'}
_EAGER_ITERATOR_CONSUMERS = {
    'all', 'any', 'dict', 'frozenset', 'list', 'max', 'min', 'set', 'sorted', 'sum', 'tuple',
}


def _tail_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _claim_aliases(func, before_line: int | None = None) -> dict[str, str]:
    """Resolve imports and assignments that rename a claim factory."""
    parameters = {
        arg.arg
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        )
    }
    parameters.update(
        arg.arg for arg in (func.args.vararg, func.args.kwarg) if arg is not None
    )
    qualified_aliases = set(getattr(func, '_claim_module_aliases', set())) - parameters
    bare_aliases = {
        name: canonical
        for name, canonical in getattr(
            func,
            '_claim_bare_aliases',
            {name: name for name in _CLAIM_CALLS},
        ).items()
        if name not in parameters
    }
    def merge(left, right):
        return {
            name: value
            for name, value in left.items()
            if right.get(name) == value
        }

    def after(statements, state):
        state = dict(state)
        def invalidate(name):
            for key in list(state):
                if key == name or key.startswith(f'{name}.'):
                    state.pop(key, None)
        for node in statements:
            if before_line is not None and node.lineno >= before_line:
                continue
            if isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    canonical = state.get(imported.name)
                    local_name = imported.asname or imported.name
                    if canonical:
                        state[local_name] = canonical
                    else:
                        invalidate(local_name)
            elif isinstance(node, ast.Import):
                for imported in node.names:
                    invalidate(imported.asname or imported.name.split('.')[0])
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                canonical = state.get(value.id) if isinstance(value, ast.Name) else None
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        for nested in ast.walk(target):
                            if isinstance(nested, ast.Name):
                                invalidate(nested.id)
                        continue
                    if canonical:
                        state[target.id] = canonical
                    else:
                        invalidate(target.id)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                invalidate(node.name)
            elif isinstance(node, ast.If):
                state = merge(after(node.body, state), after(node.orelse, state))
            elif isinstance(node, ast.Match):
                paths = []
                for case in node.cases:
                    case_state = dict(state)
                    captured = {
                        pattern.name
                        for pattern in ast.walk(case.pattern)
                        if isinstance(pattern, (ast.MatchAs, ast.MatchStar))
                        and pattern.name is not None
                    }
                    captured.update(
                        pattern.rest
                        for pattern in ast.walk(case.pattern)
                        if isinstance(pattern, ast.MatchMapping)
                        and pattern.rest is not None
                    )
                    for name in captured:
                        for key in list(case_state):
                            if key == name or key.startswith(f'{name}.'):
                                case_state.pop(key, None)
                    paths.append(after(case.body, case_state))
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in node.cases
                )
                if not exhaustive:
                    paths.append(state)
                if paths:
                    state = paths[0]
                    for path in paths[1:]:
                        state = merge(state, path)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                body_state = dict(state)
                for name in _assigned_names(node):
                    for key in list(body_state):
                        if key == name or key.startswith(f'{name}.'):
                            body_state.pop(key, None)
                state = merge(state, after(node.body, body_state))
                state = after(node.orelse, state)
            elif isinstance(node, ast.While):
                state = merge(state, after(node.body, state))
                state = after(node.orelse, state)
            elif isinstance(node, ast.With):
                body_state = dict(state)
                for item in node.items:
                    if item.optional_vars is None:
                        continue
                    for name in {
                        child.id
                        for child in ast.walk(item.optional_vars)
                        if isinstance(child, ast.Name)
                    }:
                        for key in list(body_state):
                            if key == name or key.startswith(f'{name}.'):
                                body_state.pop(key, None)
                state = after(node.body, body_state)
            elif isinstance(node, ast.Try):
                prefix_states = [state]
                body_state = state
                for statement in node.body:
                    body_state = after([statement], body_state)
                    prefix_states.append(body_state)
                paths = [after(node.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in node.handlers
                    for prefix_state in prefix_states
                )
                merged = paths[0]
                for path in paths[1:]:
                    merged = merge(merged, path)
                state = after(node.finalbody, merged)
        return state

    return after(
        func.body,
        {
            **bare_aliases,
            **{
                f'{alias}.{name}': name
                for alias in qualified_aliases
                for name in _CLAIM_CALLS
            },
        },
    )


def _resolved_claim_factory(call, aliases: dict[str, str]) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    if isinstance(call.func, ast.Name):
        return aliases.get(call.func.id)
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
    ):
        return aliases.get(f'{call.func.value.id}.{call.func.attr}')
    return None


def _claim_folder_expression(call: ast.Call):
    if call.args:
        return call.args[0]
    keyword = next(
        (item for item in call.keywords if item.arg == 'content_folder'), None
    )
    return keyword.value if keyword else None


def _eager_definition_nodes(node) -> list[ast.AST]:
    """Expressions evaluated while a nested function object is created."""
    eager = list(node.decorator_list) + list(node.args.defaults)
    eager.extend(default for default in node.args.kw_defaults if default)
    eager.extend(
        arg.annotation
        for arg in (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if arg.annotation is not None
    )
    for arg in (node.args.vararg, node.args.kwarg):
        if arg is not None and arg.annotation is not None:
            eager.append(arg.annotation)
    if node.returns is not None:
        eager.append(node.returns)
    return eager


def _walk_own_scope(func):
    """Walk one function body without attributing nested defs to its parent."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.Lambda) and getattr(
            func, '_prune_lambda_bodies', False
        ):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # The body runs later, but decorators/defaults/annotations run now.
            stack.extend(_eager_definition_nodes(node))
            continue
        stack.extend(ast.iter_child_nodes(node))


def _claim_calls_in_own_scope(func) -> list:
    """Claim calls whose *nearest enclosing function* is ``func`` itself.

    ``ast.walk`` cannot express that. It queues every descendant up front, so
    skipping a nested ``def`` node still visits that def's body afterwards and
    attributes its calls to the outer function. A handler that defines a
    synchronous worker locally and hands it to ``asyncio.to_thread`` -- the
    one shape that legitimately takes a claim from inside a coroutine's source
    text -- would then be reported as a violation. Prune by refusing to
    descend, not by skipping a single node.
    """
    own_scope = list(_walk_own_scope(func))
    eager_generator_consumers = {
        'all', 'any', 'deque', 'list', 'max', 'min', 'set', 'sorted', 'sum', 'tuple'
    }
    invoked_names = {
        node.func.id
        for node in own_scope
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    iterated_names = {
        node.iter.id
        for node in own_scope
        if isinstance(node, (ast.For, ast.AsyncFor))
        and isinstance(node.iter, ast.Name)
    }
    iterated_names.update({
        node.args[0].id
        for node in own_scope
        if isinstance(node, ast.Call)
        and _tail_name(node) in eager_generator_consumers
        and node.args
        and isinstance(node.args[0], ast.Name)
    })
    found = []
    stack = [(func, node) for node in ast.iter_child_nodes(func)]
    while stack:
        parent, node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend((node, child) for child in _eager_definition_nodes(node))
            continue
        if isinstance(node, ast.Lambda):
            # Defaults are evaluated when the lambda is created; its body is a
            # deferred nested scope and must be attributed when it is invoked.
            eager = list(node.args.defaults)
            eager.extend(default for default in node.args.kw_defaults if default)
            stored_name = (
                parent.targets[0].id
                if isinstance(parent, ast.Assign)
                and len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Name)
                else parent.target.id
                if isinstance(parent, ast.AnnAssign)
                and isinstance(parent.target, ast.Name)
                else None
            )
            if (
                isinstance(parent, ast.Call) and parent.func is node
                or stored_name in invoked_names
            ):
                eager.append(node.body)
            stack.extend((node, child) for child in eager)
            continue
        if isinstance(node, ast.GeneratorExp):
            # Only the outermost iterable is eager; the element, conditions,
            # and nested iterables execute when the generator is consumed.
            stored_name = (
                parent.targets[0].id
                if isinstance(parent, ast.Assign)
                and len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Name)
                else parent.target.id
                if isinstance(parent, ast.AnnAssign)
                and isinstance(parent.target, ast.Name)
                else None
            )
            consumed_now = (
                isinstance(parent, (ast.For, ast.AsyncFor)) and parent.iter is node
                or isinstance(parent, ast.Call)
                and _tail_name(parent) in eager_generator_consumers
                or stored_name in iterated_names
            )
            if consumed_now:
                stack.extend((node, child) for child in ast.iter_child_nodes(node))
            else:
                stack.append((node, node.generators[0].iter))
            continue
        if isinstance(node, ast.Call):
            aliases = _claim_aliases(func, node.lineno)
            name = _resolved_claim_factory(node, aliases)
            if name:
                found.append(node)
        stack.extend((node, child) for child in ast.iter_child_nodes(node))
    return found


def _parent_map(func) -> dict[int, ast.AST]:
    parents = {}
    for node in _walk_own_scope(func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in _eager_definition_nodes(node):
                parents[id(child)] = node
            continue
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _worker_callable(call: ast.Call):
    """Return the expression an offload API will invoke in the worker."""
    index = 1 if _tail_name(call) == 'run_in_executor' else 0
    return call.args[index] if len(call.args) > index else None


def _known_event_loop_names(func, before_line: int) -> set[str]:
    def after(statements, names):
        names = set(names)
        for node in statements:
            if node.lineno >= before_line:
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                is_event_loop = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id
                    in _known_asyncio_module_names(func, node.lineno)
                    and value.func.attr in {'get_event_loop', 'get_running_loop'}
                )
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if is_event_loop:
                        names.add(target.id)
                    else:
                        names.discard(target.id)
            elif isinstance(node, ast.If):
                names = after(node.body, names) & after(node.orelse, names)
            elif isinstance(node, ast.Match):
                paths = [after(case.body, names) for case in node.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in node.cases
                )
                if not exhaustive:
                    paths.append(names)
                if paths:
                    names = set.intersection(*paths)
            elif isinstance(node, ast.Try):
                if node.lineno < before_line <= node.end_lineno:
                    containing = next((
                        branch
                        for branch in (
                            [node.body, node.orelse, node.finalbody]
                            + [handler.body for handler in node.handlers]
                        )
                        if any(
                            statement.lineno <= before_line <= statement.end_lineno
                            for statement in branch
                        )
                    ), None)
                    if containing is not None:
                        names = after(containing, names)
                        continue
                body_names = after(node.body, names)
                paths = [after(node.orelse, body_names)]
                paths.extend(after(handler.body, names) for handler in node.handlers)
                names = paths[0]
                for path in paths[1:]:
                    names &= path
                names = after(node.finalbody, names)
        return names

    return after(func.body, set())


def _known_to_thread_names(
    func, before_line: int, module_aliases: set[str] | None = None
) -> set[str]:
    """Verified bare-name aliases of ``asyncio.to_thread`` at one call site."""
    parameters = {
        arg.arg
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        )
    }
    parameters.update(
        arg.arg for arg in (func.args.vararg, func.args.kwarg) if arg is not None
    )
    initial = set(module_aliases or ()) - parameters

    def branch_containing(branches):
        return next((
            branch
            for branch in branches
            if any(
                statement.lineno <= before_line <= statement.end_lineno
                for statement in branch
            )
        ), None)

    def after(statements, names):
        names = set(names)
        for node in statements:
            if node.lineno >= before_line:
                continue
            if isinstance(node, ast.ImportFrom) and node.module == 'asyncio':
                for alias in node.names:
                    if alias.name == 'to_thread':
                        names.add(alias.asname or alias.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                is_alias = isinstance(value, ast.Name) and value.id in names
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if is_alias:
                        names.add(target.id)
                    else:
                        names.discard(target.id)
            elif isinstance(node, ast.If):
                containing = branch_containing([node.body, node.orelse])
                if node.lineno < before_line <= node.end_lineno and containing:
                    names = after(containing, names)
                else:
                    names = after(node.body, names) & after(node.orelse, names)
            elif isinstance(node, ast.Match):
                paths = [after(case.body, names) for case in node.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in node.cases
                )
                if not exhaustive:
                    paths.append(names)
                if paths:
                    names = set.intersection(*paths)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                names &= after(node.body, names)
                names = after(node.orelse, names)
            elif isinstance(node, ast.Try):
                branches = (
                    [node.body, node.orelse, node.finalbody]
                    + [handler.body for handler in node.handlers]
                )
                containing = branch_containing(branches)
                if node.lineno < before_line <= node.end_lineno and containing:
                    names = after(containing, names)
                    continue
                body_names = after(node.body, names)
                paths = [after(node.orelse, body_names)]
                paths.extend(after(handler.body, names) for handler in node.handlers)
                merged = paths[0]
                for path in paths[1:]:
                    merged &= path
                names = after(node.finalbody, merged)
        return names

    return after(func.body, initial)


def _is_worker_offload_call(
    call: ast.Call, func, to_thread_aliases: set[str] | None = None
) -> bool:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id in _known_to_thread_names(
            func, call.lineno, to_thread_aliases
        )
    if not isinstance(target, ast.Attribute):
        return False
    if (
        target.attr == 'to_thread'
        and isinstance(target.value, ast.Name)
        and target.value.id in _known_asyncio_module_names(func, call.lineno)
    ):
        return True
    return (
        target.attr == 'run_in_executor'
        and isinstance(target.value, ast.Name)
        and target.value.id in _known_event_loop_names(func, call.lineno)
    )


def _known_module_names(func, module: str, before_line: int) -> set[str]:
    parameters = {
        arg.arg
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        )
    }
    parameters.update(
        arg.arg for arg in (func.args.vararg, func.args.kwarg) if arg is not None
    )

    def after(statements, names):
        names = set(names)
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    local = alias.asname or alias.name.split('.')[0]
                    if alias.name == module:
                        names.add(local)
                    else:
                        names.discard(local)
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.discard(target.id)
            elif isinstance(statement, ast.If):
                names = after(statement.body, names) & after(statement.orelse, names)
            elif isinstance(statement, ast.Match):
                paths = [after(case.body, names) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(names)
                names = set.intersection(*paths)
            elif isinstance(statement, ast.With):
                names = after(statement.body, names)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                names &= after(statement.body, names)
                names = after(statement.orelse, names)
            elif isinstance(statement, ast.Try):
                body_names = after(statement.body, names)
                paths = [after(statement.orelse, body_names)]
                paths.extend(after(handler.body, names) for handler in statement.handlers)
                names = set.intersection(*paths)
                names = after(statement.finalbody, names)
        return names

    return after(func.body, {module} - parameters)


def _known_asyncio_module_names(func, before_line: int) -> set[str]:
    return _known_module_names(func, 'asyncio', before_line)


def _known_os_module_names(func, before_line: int) -> set[str]:
    return _known_module_names(func, 'os', before_line)


def _contains_node(root, target) -> bool:
    return any(node is target for node in ast.walk(root))


def _is_invoked_inside(node, root, parents: dict[int, ast.AST]) -> bool:
    """Whether evaluating ``root`` calls the callable referenced by ``node``."""
    current = node
    invoked = False
    while current is not root and id(current) in parents:
        parent = parents[id(current)]
        if isinstance(parent, ast.Call):
            if parent.func is current:
                invoked = True
            elif not invoked:
                return False
        if isinstance(parent, (ast.Lambda, ast.GeneratorExp)) and parent is not root:
            return False
        current = parent
    return invoked and current is root


def _is_deferred_reference(
    node,
    parents: dict[int, ast.AST],
    stop,
    to_thread_aliases: set[str] | None = None,
) -> bool:
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, ast.Call) and _is_worker_offload_call(
            current, stop, to_thread_aliases
        ):
            callable_arg = _worker_callable(current)
            if callable_arg is node:
                return True
            if (
                isinstance(callable_arg, ast.Lambda)
                and _contains_node(callable_arg.body, node)
            ):
                return _is_invoked_inside(node, callable_arg, parents)
            if (
                isinstance(callable_arg, ast.Call)
                and _tail_name(callable_arg) == 'partial'
                and callable_arg.args
                and callable_arg.args[0] is node
            ):
                return True
            return False
        if current is stop:
            break
    return False


def _resolved_claim_name(
    call,
    claiming: set[str],
    attribute_claiming: set[tuple[str, str]],
    class_scope: bool,
) -> str | None:
    """Resolve only call targets that are visible in the current scope."""
    if isinstance(call.func, ast.Name) and call.func.id in claiming:
        return call.func.id
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        base = call.func.value.id
        if class_scope and base in {'self', 'cls'} and call.func.attr in claiming:
            return call.func.attr
        if (base, call.func.attr) in attribute_claiming:
            return f'{base}.{call.func.attr}'
    return None


def _resolved_claim_reference(
    node,
    claiming: set[str],
    attribute_claiming: set[tuple[str, str]],
    class_scope: bool,
) -> str | None:
    """Resolve a callable reference without requiring it to be called here."""
    if isinstance(node, ast.Name) and node.id in claiming:
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        base = node.value.id
        if class_scope and base in {'self', 'cls'} and node.attr in claiming:
            return node.attr
        if (base, node.attr) in attribute_claiming:
            return f'{base}.{node.attr}'
    return None


def _callback_claim_name(
    call,
    claiming: set[str],
    attribute_claiming: set[tuple[str, str]],
    class_scope: bool,
) -> str | None:
    if _tail_name(call) not in _CALLBACK_ITERATORS or not call.args:
        return None
    return _resolved_claim_reference(
        call.args[0], claiming, attribute_claiming, class_scope
    )


def _expression_escapes(node, parents: dict[int, ast.AST]) -> bool:
    """Whether a lazy iterator/context result leaves the current call frame."""
    current = node
    while id(current) in parents:
        parent = parents[id(current)]
        if isinstance(parent, ast.withitem) and parent.context_expr is current:
            return False
        if isinstance(parent, ast.Call):
            return not (
                _tail_name(parent) in _EAGER_ITERATOR_CONSUMERS
                and current in list(parent.args) + [kw.value for kw in parent.keywords]
            )
        if isinstance(parent, ast.For) and parent.iter is current:
            return False
        if isinstance(parent, (ast.Lambda, ast.GeneratorExp)):
            return True
        current = parent
    return True


def _generator_expression_escapes(node, parents: dict[int, ast.AST]) -> bool:
    """A generator is safe only when a known eager consumer drains it here."""
    return _expression_escapes(node, parents)


def _function_is_generator(func) -> bool:
    return any(
        isinstance(child, (ast.Yield, ast.YieldFrom))
        for child in _walk_own_scope(func)
    )


def _call_is_in_deferred_expression(
    call, func, parents: dict[int, ast.AST]
) -> bool:
    """Whether a call is hidden in a callable/iterator that escapes ``func``."""
    current = call
    while current is not func and id(current) in parents:
        parent = parents[id(current)]
        if isinstance(parent, ast.Lambda):
            lambda_parent = parents.get(id(parent))
            if not (
                isinstance(lambda_parent, ast.Call)
                and lambda_parent.func is parent
            ):
                return True
        if isinstance(parent, ast.GeneratorExp):
            return _generator_expression_escapes(parent, parents)
        current = parent
    return False


def _function_defers_claiming_call(
    func,
    claiming: set[str],
    attribute_claiming: set[tuple[str, str]],
    class_scope: bool,
    generator_claiming: set[str] | None = None,
    attribute_generators: set[tuple[str, str]] | None = None,
) -> bool:
    """Whether calling ``func`` only constructs deferred claim-owning work."""
    generator_claiming = generator_claiming or set()
    attribute_generators = attribute_generators or set()
    parents = _parent_map(func)
    if func.name in claiming and _function_is_generator(func):
        return True
    for child in _walk_own_scope(func):
        if not isinstance(child, ast.Call):
            continue
        direct_name = _resolved_claim_name(
            child, claiming, attribute_claiming, class_scope
        )
        callback_name = _callback_claim_name(
            child, claiming, attribute_claiming, class_scope
        )
        if not direct_name and not callback_name:
            continue
        if callback_name and _expression_escapes(child, parents):
            return True
        if direct_name and _resolved_claim_name(
            child, generator_claiming, attribute_generators, class_scope
        ) and _expression_escapes(child, parents):
            return True
        if _call_is_in_deferred_expression(child, func, parents):
            return True
    return False


def _claiming_worker_inventory(
    functions,
    seed_claiming: set[str] | None = None,
    seed_generators: set[str] | None = None,
    attribute_claiming: set[tuple[str, str]] | None = None,
    attribute_generators: set[tuple[str, str]] | None = None,
    class_scope: bool = False,
) -> tuple[set[str], set[str]]:
    """Return claim owners and owners deferred by yield/generator expressions."""
    attribute_claiming = attribute_claiming or set()
    attribute_generators = attribute_generators or set()
    claiming = set(seed_claiming or ()) | {
        node.name for node in functions if _claim_calls_in_own_scope(node)
    }
    generator_owners = set(seed_generators or ())
    generator_owners.update(
        node.name
        for node in functions
        if _function_defers_claiming_call(
            node,
            claiming,
            attribute_claiming,
            class_scope,
            generator_owners,
            attribute_generators,
        )
    )
    while True:
        wrappers = {
            func.name
            for func in functions
            if func.name not in claiming
            and any(
                isinstance(node, ast.Call)
                and (
                    _resolved_claim_name(
                        node, claiming, attribute_claiming, class_scope
                    )
                    or _callback_claim_name(
                        node, claiming, attribute_claiming, class_scope
                    )
                )
                for node in _walk_own_scope(func)
            )
        }
        if not wrappers:
            return claiming, generator_owners
        generator_owners.update(
            func.name
            for func in functions
            if func.name in wrappers
            and (
                _function_defers_claiming_call(
                    func,
                    claiming,
                    attribute_claiming,
                    class_scope,
                    generator_owners,
                    attribute_generators,
                )
            )
        )
        claiming.update(wrappers)


def _claiming_decorator_names(functions) -> set[str]:
    memo = {}

    def returns_claiming_wrapper(func, visiting=None):
        if id(func) in memo:
            return memo[id(func)]
        visiting = set(visiting or ())
        if id(func) in visiting:
            return False
        visiting.add(id(func))
        helpers = [
            node for node in _walk_own_scope(func)
            if isinstance(node, ast.FunctionDef)
        ]
        claiming_helpers = {
            helper.name
            for helper in helpers
            if _function_claims_via_nested_helpers(helper)
            or returns_claiming_wrapper(helper, visiting)
        }
        result = any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id in claiming_helpers
            for node in _walk_own_scope(func)
        )
        memo[id(func)] = result
        return result

    return {func.name for func in functions if returns_claiming_wrapper(func)}


def _claiming_decorated_functions(
    functions, claiming_decorators: set[str] | None = None
) -> set[str]:
    claiming_decorators = (
        _claiming_decorator_names(functions)
        if claiming_decorators is None
        else claiming_decorators
    )
    return {
        func.name
        for func in functions
        if any(
            _reference_name(decorator) in claiming_decorators
            for decorator in func.decorator_list
        )
    }


def _function_claims_via_nested_helpers(func, memo=None, visiting=None) -> bool:
    """Whether ``func`` directly or transitively runs a local claim owner."""
    memo = memo if memo is not None else {}
    visiting = visiting if visiting is not None else set()
    if id(func) in memo:
        return memo[id(func)]
    if id(func) in visiting:
        return False
    visiting.add(id(func))
    if _claim_calls_in_own_scope(func):
        result = True
    else:
        helpers = [
            node
            for node in _walk_own_scope(func)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        nested_claiming = {
            helper.name
            for helper in helpers
            if _function_claims_via_nested_helpers(helper, memo, visiting)
        }
        claiming, _ = _claiming_worker_inventory(
            helpers, seed_claiming=nested_claiming
        )
        result = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in claiming
            for node in _walk_own_scope(func)
        )
    visiting.remove(id(func))
    memo[id(func)] = result
    return result


def _claiming_helpers_called_on_loop(
    func, to_thread_aliases: set[str] | None = None
) -> list:
    """Nested claim helpers are legal only when every use is offloaded."""
    helpers = [
        node
        for node in _walk_own_scope(func)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not helpers:
        return []

    memo = {}
    nested_claiming = {
        helper.name
        for helper in helpers
        if _function_claims_via_nested_helpers(helper, memo)
    }
    claiming, generators = _claiming_worker_inventory(
        helpers, seed_claiming=nested_claiming
    )
    return _claiming_names_called_on_loop(
        func, claiming, generators, to_thread_aliases
    )


def _reference_name(node) -> str | None:
    if isinstance(node, ast.Call):
        return _reference_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _claiming_names_called_on_loop(
    func,
    claiming_names: set[str],
    generator_names: set[str] | None = None,
    to_thread_aliases: set[str] | None = None,
) -> list:
    """References to claim-owning sync workers must be worker callables."""
    if not claiming_names:
        return []

    generator_names = generator_names or set()
    parents = _parent_map(func)
    offenders = []
    for node in _walk_own_scope(func):
        name = _reference_name(node)
        if name not in claiming_names:
            continue
        if name in generator_names or not _is_deferred_reference(
            node, parents, func, to_thread_aliases
        ):
            offenders.append((name, node.lineno))
    return offenders


def _resolve_imported_module(
    module: str,
    node: ast.ImportFrom,
    module_names: set[str],
    imported_name: str | None = None,
) -> str | None:
    suffix = node.module or imported_name or ''
    if node.module and imported_name:
        suffix = f'{node.module}.{imported_name}'
    if node.level:
        is_package = module == '__init__' or any(
            name.startswith(f'{module}.') for name in module_names
        )
        package = [] if module == '__init__' else module.split('.')
        if not is_package:
            package = package[:-1]
        drop = node.level - 1
        if drop:
            package = package[:-drop] if drop <= len(package) else []
        candidate = '.'.join(package + suffix.split('.'))
    else:
        candidate = suffix
    if candidate in module_names:
        return candidate
    matches = [
        name for name in module_names
        if suffix and (suffix == name or suffix.endswith(f'.{name}'))
    ]
    return matches[0] if len(matches) == 1 else None


def _walk_module_scope(statements):
    """Module bindings, descending through control flow but not defs/classes."""
    for node in statements:
        yield node
        if isinstance(node, ast.If):
            yield from _walk_module_scope(node.body)
            yield from _walk_module_scope(node.orelse)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            yield from _walk_module_scope(node.body)
            yield from _walk_module_scope(node.orelse)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                yield from _walk_module_scope(case.body)
        elif isinstance(node, ast.With):
            yield from _walk_module_scope(node.body)
        elif isinstance(node, ast.Try):
            yield from _walk_module_scope(node.body)
            for handler in node.handlers:
                yield from _walk_module_scope(handler.body)
            yield from _walk_module_scope(node.orelse)
            yield from _walk_module_scope(node.finalbody)


def _module_to_thread_aliases(tree) -> set[str]:
    def merge(left, right):
        return left & right

    def after(statements, state):
        state = set(state)
        for node in statements:
            if isinstance(node, ast.ImportFrom) and node.module == 'asyncio':
                state.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == 'to_thread'
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                is_alias = isinstance(value, ast.Name) and value.id in state
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if is_alias:
                        state.add(target.id)
                    else:
                        state.discard(target.id)
            elif isinstance(node, ast.If):
                state = merge(after(node.body, state), after(node.orelse, state))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                state = merge(state, after(node.body, state))
                state = after(node.orelse, state)
            elif isinstance(node, ast.Match):
                paths = [after(case.body, state) for case in node.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in node.cases
                )
                if not exhaustive:
                    paths.append(state)
                if paths:
                    state = set.intersection(*paths)
            elif isinstance(node, ast.With):
                state = after(node.body, state)
            elif isinstance(node, ast.Try):
                prefix_states = [state]
                body_state = state
                for statement in node.body:
                    body_state = after([statement], body_state)
                    prefix_states.append(body_state)
                paths = [after(node.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in node.handlers
                    for prefix_state in prefix_states
                )
                merged = paths[0]
                for path in paths[1:]:
                    merged = merge(merged, path)
                state = after(node.finalbody, merged)
        return state

    return after(tree.body, set())


def _module_level_claiming_workers(trees):
    """Find owners per module/class scope, including direct import aliases."""
    scope_functions = {}
    class_base_nodes = {}
    module_trees = dict(trees)
    module_nodes = {
        module: list(_walk_module_scope(tree.body)) for module, tree in trees
    }
    for module, tree in trees:
        scope_functions[(module, None)] = [
            node for node in module_nodes[module] if isinstance(node, ast.FunctionDef)
        ]
        for node in module_nodes[module]:
            if isinstance(node, ast.ClassDef):
                class_scope = (module, node.name)
                scope_functions[class_scope] = [
                    child
                    for child in _walk_module_scope(node.body)
                    if isinstance(child, ast.FunctionDef)
                ]
                class_base_nodes[class_scope] = node.bases

    claiming = {}
    generators = {}
    decorators = {}
    for scope, functions in scope_functions.items():
        decorators[scope] = _claiming_decorator_names(functions)
        claiming[scope], generators[scope] = _claiming_worker_inventory(
            functions,
            seed_claiming=_claiming_decorated_functions(functions),
            class_scope=scope[1] is not None,
        )

    module_names = set(module_trees)

    module_aliases = {module: {} for module in module_names}
    imported_classes = {module: {} for module in module_names}
    to_thread_aliases = {
        module: _module_to_thread_aliases(tree) for module, tree in trees
    }
    for module, tree in trees:
        for node in module_nodes[module]:
            if isinstance(node, ast.ImportFrom):
                direct_source = _resolve_imported_module(
                    module, node, module_names
                )
                for alias in node.names:
                    imported_module = _resolve_imported_module(
                        module, node, module_names, alias.name
                    )
                    if imported_module:
                        module_aliases[module][alias.asname or alias.name] = imported_module
                    if (
                        direct_source
                        and (direct_source, alias.name) in scope_functions
                    ):
                        imported_classes[module][alias.asname or alias.name] = (
                            direct_source, alias.name
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    source_module = next((
                        name for name in module_names
                        if alias.name == name or alias.name.endswith(f'.{name}')
                    ), None)
                    if source_module and alias.asname:
                        module_aliases[module][alias.asname] = source_module

    for module, tree in trees:
        claim_module_aliases = {
            alias
            for alias, source_module in module_aliases[module].items()
            if source_module.rsplit('.', 1)[-1] == 'content_gate'
        }
        module_scope = ast.FunctionDef(
            name='<module>',
            args=ast.arguments(
                posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
                kw_defaults=[], kwarg=None, defaults=[]
            ),
            body=tree.body,
            decorator_list=[],
        )
        claim_bare_aliases = {
            name: canonical
            for name, canonical in _claim_aliases(module_scope).items()
            if '.' not in name
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node._claim_module_aliases = claim_module_aliases
                node._claim_bare_aliases = claim_bare_aliases

    class_bases = {}
    for class_scope, bases in class_base_nodes.items():
        module, _ = class_scope
        resolved = []
        for base in bases:
            if isinstance(base, ast.Name):
                resolved.append(
                    imported_classes[module].get(base.id, (module, base.id))
                )
            elif (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id in module_aliases[module]
            ):
                resolved.append((module_aliases[module][base.value.id], base.attr))
        class_bases[class_scope] = resolved

    while True:
        changed = False
        for module, tree in trees:
            scope = (module, None)
            imported_claiming = set(claiming[scope])
            imported_generators = set(generators[scope])
            visible_decorators = set(decorators[scope])
            for node in module_nodes[module]:
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                source_module = _resolve_imported_module(
                    module, node, module_names
                )
                if source_module is None:
                    continue
                source_scope = (source_module, None)
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if alias.name in claiming.get(source_scope, set()):
                        imported_claiming.add(local_name)
                    if alias.name in generators.get(source_scope, set()):
                        imported_generators.add(local_name)
                    if alias.name in decorators.get(source_scope, set()):
                        visible_decorators.add(local_name)
            attribute_claiming = {
                (local_alias, name)
                for local_alias, source_module in module_aliases[module].items()
                for name in claiming.get((source_module, None), set())
            }
            attribute_generators = {
                (local_alias, name)
                for local_alias, source_module in module_aliases[module].items()
                for name in generators.get((source_module, None), set())
            }
            while True:
                aliases_changed = False
                for node in module_nodes[module]:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = node.value
                    reference = (
                        value.args[0]
                        if isinstance(value, ast.Call)
                        and _tail_name(value) == 'partial'
                        and value.args
                        else value
                    )
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    resolved_claim = _resolved_claim_reference(
                        reference, imported_claiming, attribute_claiming, False
                    )
                    resolved_generator = _resolved_claim_reference(
                        reference, imported_generators, attribute_generators, False
                    )
                    for target in targets:
                        if not isinstance(target, ast.Name):
                            continue
                        if resolved_claim and target.id not in imported_claiming:
                            imported_claiming.add(target.id)
                            aliases_changed = True
                        if (
                            resolved_generator
                            and target.id not in imported_generators
                        ):
                            imported_generators.add(target.id)
                            aliases_changed = True
                if not aliases_changed:
                    break
            next_claiming, next_generators = _claiming_worker_inventory(
                scope_functions[scope],
                imported_claiming | _claiming_decorated_functions(
                    scope_functions[scope], visible_decorators
                ),
                imported_generators,
                attribute_claiming,
                attribute_generators,
            )
            if next_claiming != claiming[scope] or next_generators != generators[scope]:
                claiming[scope] = next_claiming
                generators[scope] = next_generators
                changed = True
            if visible_decorators != decorators[scope]:
                decorators[scope] = visible_decorators
                changed = True
            for node in module_nodes[module]:
                if not isinstance(node, ast.ClassDef):
                    continue
                class_scope = (module, node.name)
                local_names = {
                    func.name for func in scope_functions[class_scope]
                }
                inherited_claiming = set().union(*(
                    claiming.get(base, set())
                    for base in class_bases[class_scope]
                )) if class_bases[class_scope] else set()
                inherited_generators = set().union(*(
                    generators.get(base, set())
                    for base in class_bases[class_scope]
                )) if class_bases[class_scope] else set()
                visible_claiming = (
                    claiming[scope] | inherited_claiming
                ) - local_names
                visible_generators = (
                    generators[scope] | inherited_generators
                ) - local_names
                decorated_methods = _claiming_decorated_functions(
                    scope_functions[class_scope], decorators[scope]
                )
                all_claiming, all_generators = _claiming_worker_inventory(
                    scope_functions[class_scope],
                    visible_claiming | decorated_methods,
                    visible_generators,
                    attribute_claiming,
                    attribute_generators,
                    class_scope=True,
                )
                next_class_claiming = (
                    inherited_claiming - local_names
                ) | (all_claiming & local_names)
                next_class_generators = (
                    inherited_generators - local_names
                ) | (all_generators & local_names)
                next_class_generators.update(
                    func.name
                    for func in scope_functions[class_scope]
                    if func.name in next_class_claiming
                    and any(
                        _reference_name(decorator) in {'property', 'cached_property'}
                        for decorator in func.decorator_list
                    )
                )
                if (
                    next_class_claiming != claiming[class_scope]
                    or next_class_generators != generators[class_scope]
                ):
                    claiming[class_scope] = next_class_claiming
                    generators[class_scope] = next_class_generators
                    changed = True
        if not changed:
            import_facts = {
                module: {
                    'classes': imported_classes[module],
                    'to_thread': to_thread_aliases[module],
                }
                for module in module_names
            }
            return claiming, generators, module_aliases, import_facts


def _storage_key(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _storage_key(node.value)
        return f'{base}.{node.attr}' if base else None
    return None


def _class_protocol_claim(
    node,
    module: str,
    claiming_by_scope,
    parents: dict[int, ast.AST],
    instance_classes_before,
    class_aliases: dict[str, tuple[str, str]],
) -> set[tuple[tuple[str, str], str]]:
    """Resolve implicit constructor/context protocol calls for a class name."""
    parent = parents.get(id(node))
    stored_classes = instance_classes_before(
        _storage_key(node) or '', node.lineno
    )
    if (
        stored_classes
        and isinstance(parent, ast.withitem)
        and parent.context_expr is node
    ):
        return {
            (scope, method)
            for scope in stored_classes
            for method in ('__enter__', '__exit__', '__aenter__', '__aexit__')
            if method in claiming_by_scope.get(scope, set())
        }
    if stored_classes and isinstance(parent, ast.Call) and parent.func is node:
        return {
            (scope, '__call__')
            for scope in stored_classes
            if '__call__' in claiming_by_scope.get(scope, set())
        }
    if not isinstance(node, ast.Name):
        return set()
    if not isinstance(parent, ast.Call) or parent.func is not node:
        return set()
    scope = class_aliases.get(node.id, (module, node.id))
    methods = claiming_by_scope.get(scope, set())
    if '__init__' in methods:
        return {(scope, '__init__')}
    current = parent
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, ast.withitem):
            for method in ('__enter__', '__exit__', '__aenter__', '__aexit__'):
                if method in methods:
                    return {(scope, method)}
            return set()
        if isinstance(current, (ast.For, ast.AsyncFor)) and _contains_node(
            current.iter, node
        ):
            protocol = (
                ('__aiter__', '__anext__')
                if isinstance(current, ast.AsyncFor)
                else ('__iter__', '__next__')
            )
            return {
                (scope, method) for method in protocol if method in methods
            }
        if isinstance(current, (ast.Call, ast.Lambda, ast.GeneratorExp)):
            return set()
    return set()


def _scope_claiming_names_called_on_loop(
    func,
    module: str,
    class_name: str | None,
    claiming_by_scope,
    generators_by_scope,
    module_aliases,
    import_facts=None,
) -> list:
    """Resolve module names and ``self`` methods without cross-scope collisions."""
    parents = _parent_map(func)
    local_names = {}
    local_module_aliases = dict(module_aliases.get(module, {}))
    module_imports = (import_facts or {}).get(module, {})
    local_class_aliases = dict(module_imports.get('classes', {}))
    to_thread_aliases = set(module_imports.get('to_thread', set()))
    module_names = {scope[0] for scope in claiming_by_scope}
    own_scope = list(_walk_own_scope(func))
    class_names = {
        scope[1] for scope in claiming_by_scope
        if scope[0] == module and scope[1] is not None
    }
    for child in sorted(own_scope, key=lambda node: getattr(node, 'lineno', -1)):
        if isinstance(child, ast.ImportFrom):
            if child.module:
                source_module = _resolve_imported_module(
                    module, child, module_names
                )
                if source_module:
                    source_scope = (source_module, None)
                    for alias in child.names:
                        local_name = alias.asname or alias.name
                        class_scope = (source_module, alias.name)
                        if class_scope in claiming_by_scope:
                            local_class_aliases[local_name] = class_scope
                        if alias.name in claiming_by_scope.get(source_scope, set()):
                            local_names[local_name] = (
                                source_scope, alias.name
                            )
            if child.level == 0 and child.module == 'asyncio':
                to_thread_aliases.update(
                    alias.asname or alias.name
                    for alias in child.names
                    if alias.name == 'to_thread'
                )
            else:
                for alias in child.names:
                    source_module = _resolve_imported_module(
                        module, child, module_names, alias.name
                    )
                    if source_module:
                        local_module_aliases[alias.asname or alias.name] = source_module
        elif isinstance(child, ast.Import):
            for alias in child.names:
                source_module = alias.name.rsplit('.', 1)[-1]
                if source_module in module_names and alias.asname:
                    local_module_aliases[alias.asname] = source_module
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            source_module = (
                local_module_aliases.get(child.value.id)
                if isinstance(child.value, ast.Name)
                else None
            )
            targets = (
                child.targets if isinstance(child, ast.Assign) else [child.target]
            )
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if source_module:
                    local_module_aliases[target.id] = source_module
                else:
                    local_module_aliases.pop(target.id, None)
    def merge_callable_states(*states):
        return {
            name: set().union(*(state.get(name, set()) for state in states))
            for name in set().union(*(set(state) for state in states))
        }

    def constructed_class(value):
        if not isinstance(value, ast.Call):
            return None
        if isinstance(value.func, ast.Name):
            candidate = local_class_aliases.get(
                value.func.id, (module, value.func.id)
            )
        elif (
            isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in local_module_aliases
        ):
            candidate = (
                local_module_aliases[value.func.value.id], value.func.attr
            )
        else:
            return None
        return candidate if candidate in claiming_by_scope else None

    def instance_classes_before(name: str, before_line: int):
        def after(statements, state):
            state = {key: set(values) for key, values in state.items()}
            for statement in statements:
                if statement.lineno >= before_line:
                    continue
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    value = statement.value
                    constructed = constructed_class(value)
                    stored_classes = {constructed} if constructed else set()
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    for target in targets:
                        key = _storage_key(target)
                        if key is not None:
                            state[key] = set(stored_classes)
                elif isinstance(statement, ast.If):
                    state = merge_callable_states(
                        after(statement.body, state),
                        after(statement.orelse, state),
                    )
                elif isinstance(statement, ast.Match):
                    paths = [after(case.body, state) for case in statement.cases]
                    exhaustive = any(
                        case.guard is None
                        and isinstance(case.pattern, ast.MatchAs)
                        and case.pattern.pattern is None
                        for case in statement.cases
                    )
                    if not exhaustive:
                        paths.append(state)
                    state = merge_callable_states(*paths)
                elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                    state = merge_callable_states(
                        state, after(statement.body, state)
                    )
                    state = after(statement.orelse, state)
                elif isinstance(statement, ast.With):
                    state = after(statement.body, state)
                elif isinstance(statement, ast.Try):
                    prefix_states = [state]
                    body_state = state
                    for nested in statement.body:
                        body_state = after([nested], body_state)
                        prefix_states.append(body_state)
                    paths = [after(statement.orelse, body_state)]
                    paths.extend(
                        after(handler.body, prefix_state)
                        for handler in statement.handlers
                        for prefix_state in prefix_states
                    )
                    state = after(
                        statement.finalbody, merge_callable_states(*paths)
                    )
            return state

        return after(func.body, {}).get(name, set())

    def callable_aliases_before(before_line: int):
        def resolved_name(name, state):
            if name in state:
                return set(state[name])
            candidate = local_names.get(name, ((module, None), name))
            return (
                {candidate}
                if candidate[1] in claiming_by_scope.get(candidate[0], set())
                else set()
            )

        def resolved_value(value, state):
            if isinstance(value, ast.Name):
                return resolved_name(value.id, state)
            if (
                isinstance(value, ast.Call)
                and _tail_name(value) == 'partial'
                and value.args
                and isinstance(value.args[0], ast.Name)
            ):
                return resolved_name(value.args[0].id, state)
            return set()

        def after(statements, state):
            state = {name: set(values) for name, values in state.items()}
            for statement in statements:
                if statement.lineno >= before_line:
                    continue
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    value = statement.value
                    resolutions = resolved_value(value, state)
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            state[target.id] = set(resolutions)
                elif isinstance(
                    statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    state[statement.name] = set()
                elif isinstance(statement, ast.If):
                    state = merge_callable_states(
                        after(statement.body, state),
                        after(statement.orelse, state),
                    )
                elif isinstance(statement, ast.Match):
                    paths = [after(case.body, state) for case in statement.cases]
                    exhaustive = any(
                        case.guard is None
                        and isinstance(case.pattern, ast.MatchAs)
                        and case.pattern.pattern is None
                        for case in statement.cases
                    )
                    if not exhaustive:
                        paths.append(state)
                    state = merge_callable_states(*paths)
                elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                    state = merge_callable_states(
                        state, after(statement.body, state)
                    )
                    state = after(statement.orelse, state)
                elif isinstance(statement, ast.With):
                    state = after(statement.body, state)
                elif isinstance(statement, ast.Try):
                    prefix_states = [state]
                    body_state = state
                    for nested in statement.body:
                        body_state = after([nested], body_state)
                        prefix_states.append(body_state)
                    paths = [after(statement.orelse, body_state)]
                    paths.extend(
                        after(handler.body, prefix_state)
                        for handler in statement.handlers
                        for prefix_state in prefix_states
                    )
                    state = after(
                        statement.finalbody, merge_callable_states(*paths)
                    )
            return state

        return after(func.body, {})

    alias_source_ids = set()
    for child in own_scope:
        if not isinstance(child, (ast.Assign, ast.AnnAssign)):
            continue
        value = child.value
        source = (
            value.args[0]
            if isinstance(value, ast.Call)
            and _tail_name(value) == 'partial'
            and value.args
            and isinstance(value.args[0], ast.Name)
            else value
        )
        if not isinstance(source, ast.Name):
            continue
        state = callable_aliases_before(child.lineno)
        candidate = (
            state[source.id]
            if source.id in state
            else {local_names.get(source.id, ((module, None), source.id))}
        )
        if any(
            name in claiming_by_scope.get(scope, set())
            for scope, name in candidate
        ):
            alias_source_ids.add(id(source))
    offenders = []
    for node in own_scope:
        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                continue
            if id(node) in alias_source_ids:
                continue
            name = node.id
            protocol_claims = _class_protocol_claim(
                node,
                module,
                claiming_by_scope,
                parents,
                instance_classes_before,
                local_class_aliases,
            )
            if protocol_claims:
                resolutions = protocol_claims
            else:
                state = callable_aliases_before(node.lineno)
                resolutions = (
                    state[name]
                    if name in state
                    else {local_names.get(name, ((module, None), name))}
                )
        elif isinstance(node, ast.Attribute):
            name = node.attr
            protocol_claims = _class_protocol_claim(
                node,
                module,
                claiming_by_scope,
                parents,
                instance_classes_before,
                local_class_aliases,
            )
            receiver_key = _storage_key(node.value)
            stored_classes = instance_classes_before(
                receiver_key or '', node.lineno
            )
            if protocol_claims:
                resolutions = protocol_claims
            elif (
                isinstance(node.value, ast.Name)
                and node.value.id in {'self', 'cls'}
                and class_name
            ):
                resolutions = {((module, class_name), name)}
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == 'super'
                and not node.value.args
                and not node.value.keywords
                and class_name
            ):
                resolutions = {((module, class_name), name)}
            elif stored_classes:
                resolutions = {
                    (stored_class, name) for stored_class in stored_classes
                }
            elif (constructed := constructed_class(node.value)) is not None:
                resolutions = {(constructed, name)}
            elif (
                isinstance(node.value, ast.Name)
                and (module, node.value.id) in claiming_by_scope
            ):
                resolutions = {((module, node.value.id), name)}
            elif (
                isinstance(node.value, ast.Name)
                and node.value.id in local_class_aliases
            ):
                resolutions = {(local_class_aliases[node.value.id], name)}
            elif (
                isinstance(node.value, ast.Name)
                and node.value.id in local_module_aliases
            ):
                resolutions = {
                    ((local_module_aliases[node.value.id], None), name)
                }
            else:
                continue
        else:
            continue
        for scope, resolved_name in resolutions:
            if resolved_name not in claiming_by_scope.get(scope, set()):
                continue
            if (
                resolved_name in generators_by_scope.get(scope, set())
                or not _is_deferred_reference(
                    node, parents, func, to_thread_aliases
                )
            ):
                offenders.append((
                    f'{scope[0]}.{scope[1] or "<module>"}.{resolved_name}',
                    node.lineno,
                ))
    return offenders


def test_the_event_loop_guard_prunes_nested_worker_bodies():
    """The guard below must not fire on the one shape it explicitly permits.

    A false positive here is not harmless: it would block the correct fix
    (define the worker locally, hand it to ``to_thread``) and push whoever
    hits it toward hoisting the claim onto the loop instead -- the precise
    regression the guard exists to prevent.
    """
    tree = ast.parse(
        'async def handler():\n'
        '    def _unit():\n'
        '        with claim_reference_pair(folder):\n'
        '            pass\n'
        '    await asyncio.to_thread(_unit)\n'
        '\n'
        'async def eager_helper():\n'
        '    def _unit():\n'
        '        with claim_reference_pair(folder):\n'
        '            pass\n'
        '    await asyncio.to_thread(_unit())\n'
        '\n'
        'async def direct_helper():\n'
        '    def _unit():\n'
        '        with claim_reference_pair(folder):\n'
        '            pass\n'
        '    _unit()\n'
        '\n'
        'async def hoisted():\n'
        "    with claim_content_folder(folder, purpose='x'):\n"
        '        pass\n'
        '\n'
        'async def wrapped_helper():\n'
        '    def owner():\n'
        '        with claim_reference_pair(folder):\n'
        '            pass\n'
        '    def wrapper():\n'
        '        return owner()\n'
        '    wrapper()\n'
        '\n'
        'async def offloaded_wrapper():\n'
        '    def owner():\n'
        '        with claim_reference_pair(folder):\n'
        '            pass\n'
        '    def wrapper():\n'
        '        return owner()\n'
        '    await asyncio.to_thread(wrapper)\n'
        '\n'
        'async def deeply_nested_wrapper():\n'
        '    def wrapper():\n'
        '        def owner():\n'
        '            with claim_reference_pair(folder):\n'
        '                pass\n'
        '        owner()\n'
        '    wrapper()\n'
        'async def nested_async_helper():\n'
        '    async def helper():\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '    await helper()\n'
    )
    functions = {node.name: node for node in tree.body}

    assert _claim_calls_in_own_scope(functions['handler']) == [], (
        '嵌套的同步 worker 里拿占用是合法的，守卫不该报它'
    )
    assert _claiming_helpers_called_on_loop(functions['handler']) == []
    assert _claiming_helpers_called_on_loop(functions['eager_helper']), (
        '`to_thread(_unit())` 会先在事件循环调用 helper，不能算 offload'
    )
    assert _claiming_helpers_called_on_loop(functions['direct_helper']), (
        '带 claim 的嵌套 helper 被直接调用时仍在事件循环上，必须报出来'
    )
    assert _claim_calls_in_own_scope(functions['hoisted']), (
        '直接写在协程体里的占用必须被报出来，否则守卫什么都没守'
    )
    assert _claiming_helpers_called_on_loop(functions['wrapped_helper']), (
        'nested claim owner 的同步 wrapper 被协程直调时必须报出来'
    )
    assert _claiming_helpers_called_on_loop(functions['offloaded_wrapper']) == []
    assert _claiming_helpers_called_on_loop(functions['deeply_nested_wrapper']), (
        'wrapper 内部定义并调用的 claim owner 也必须传递到外层 handler'
    )
    assert _claiming_helpers_called_on_loop(functions['nested_async_helper']), (
        'nested async claim helper 在 event loop 上 await 时也必须被发现'
    )


def test_the_event_loop_guard_checks_module_level_claim_owners():
    tree = ast.parse(
        'from asyncio import to_thread as offload\n'
        'def _claiming_worker():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
        '\n'
        'while register:\n'
        '    def _while_claiming_worker():\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        'match register:\n'
        '    case _:\n'
        '        def _match_claiming_worker():\n'
        '            with claim_content_folder(folder, purpose=p):\n'
        '                pass\n'
        '\n'
        'def _claiming_wrapper():\n'
        '    return _claiming_worker()\n'
        '\n'
        'def _generator_owner():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        yield 1\n'
        '\n'
        'def _generator_expression_wrapper():\n'
        '    return (_claiming_worker() for _ in items)\n'
        '\n'
        'def _eager_generator_wrapper():\n'
        '    _claiming_worker()\n'
        '    return list(x for x in items)\n'
        '\n'
        'def _callable_wrapper():\n'
        '    return lambda: _claiming_worker()\n'
        '\n'
        'def _for_generator_wrapper():\n'
        '    for value in (_claiming_worker() for _ in items):\n'
        '        pass\n'
        '\n'
        'def _eager_callback_wrapper():\n'
        '    list(map(_claiming_worker, items))\n'
        '\n'
        'def _lazy_callback_wrapper():\n'
        '    return map(_claiming_worker, items)\n'
        '\n'
        '@contextmanager\n'
        'def _context_owner():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        yield\n'
        '\n'
        'def _context_worker():\n'
        '    with _context_owner():\n'
        '        pass\n'
        '\n'
        'class Safe:\n'
        '    def _claiming_worker(self):\n'
        '        return 1\n'
        '\n'
        'def safe_attribute_wrapper():\n'
        '    return Safe()._claiming_worker()\n'
        '\n'
        'async def offloaded():\n'
        '    await asyncio.to_thread(_claiming_wrapper)\n'
        '\n'
        'async def imported_to_thread():\n'
        '    await offload(_claiming_wrapper)\n'
        '\n'
        'async def local_imported_to_thread():\n'
        '    from asyncio import to_thread as local_offload\n'
        '    await local_offload(_claiming_wrapper)\n'
        '\n'
        'dispatch = offload\n'
        'dispatch = dispatcher\n'
        'loop_dispatch = offload\n'
        'while register:\n'
        '    loop_dispatch = dispatcher\n'
        'match_dispatch = offload\n'
        'match register:\n'
        '    case _:\n'
        '        match_dispatch = dispatcher\n'
        'try_dispatch = offload\n'
        'try:\n'
        '    try_dispatch = dispatcher\n'
        '    may_raise()\n'
        '    try_dispatch = offload\n'
        'except Exception:\n'
        '    pass\n'
        'async def rebound_imported_to_thread():\n'
        '    await dispatch(_claiming_wrapper)\n'
        'async def module_loop_to_thread_alias():\n'
        '    await loop_dispatch(_claiming_wrapper)\n'
        'async def module_match_to_thread_alias():\n'
        '    await match_dispatch(_claiming_wrapper)\n'
        'async def module_try_to_thread_alias():\n'
        '    await try_dispatch(_claiming_wrapper)\n'
        'async def nested_module_owners_direct():\n'
        '    _while_claiming_worker()\n'
        '    _match_claiming_worker()\n'
        '\n'
        'async def direct():\n'
        '    _claiming_wrapper()\n'
        '\n'
        'async def lambda_body():\n'
        '    await asyncio.to_thread(lambda: _claiming_worker())\n'
        '\n'
        'async def lambda_returns_owner():\n'
        '    await asyncio.to_thread(lambda: _claiming_worker)\n'
        '\n'
        'async def lambda_returns_nested_lambda():\n'
        '    await asyncio.to_thread(lambda: (lambda: _claiming_worker()))\n'
        '\n'
        'async def lambda_returns_generator():\n'
        '    await asyncio.to_thread(lambda: (_claiming_worker() for _ in items))\n'
        '\n'
        'async def eager_lambda_default():\n'
        '    await asyncio.to_thread(lambda ignored=_claiming_worker(): None)\n'
        '\n'
        'async def eager_nested_default():\n'
        '    def helper(ignored=_claiming_worker()):\n'
        '        pass\n'
        '\n'
        'async def eager_nested_decorator():\n'
        '    @_claiming_worker()\n'
        '    def helper():\n'
        '        pass\n'
        '\n'
        'async def eager_nested_annotations():\n'
        '    def helper(arg: _claiming_worker()) -> _claiming_worker():\n'
        '        pass\n'
        '\n'
        'async def generator_constructor_offloaded():\n'
        '    await asyncio.to_thread(_generator_owner)\n'
        '\n'
        'async def generator_expression_offloaded():\n'
        '    await asyncio.to_thread(_generator_expression_wrapper)\n'
        '\n'
        'async def eager_generator_offloaded():\n'
        '    await asyncio.to_thread(_eager_generator_wrapper)\n'
        '\n'
        'async def callable_wrapper_offloaded():\n'
        '    await asyncio.to_thread(_callable_wrapper)\n'
        '\n'
        'async def for_generator_offloaded():\n'
        '    await asyncio.to_thread(_for_generator_wrapper)\n'
        '\n'
        'async def eager_callback_direct():\n'
        '    _eager_callback_wrapper()\n'
        '\n'
        'async def eager_callback_offloaded():\n'
        '    await asyncio.to_thread(_eager_callback_wrapper)\n'
        '\n'
        'async def lazy_callback_offloaded():\n'
        '    await asyncio.to_thread(_lazy_callback_wrapper)\n'
        '\n'
        'async def context_worker_offloaded():\n'
        '    await asyncio.to_thread(_context_worker)\n'
        '\n'
        'async def aliased_claim_direct():\n'
        '    from .content_gate import claim_content_folder as claim\n'
        "    with claim(folder, purpose='x'):\n"
        '        pass\n'
        '\n'
        'async def assigned_claim_direct():\n'
        '    claim = claim_content_folder\n'
        "    with claim(folder, purpose='x'):\n"
        '        pass\n'
        '\n'
        'async def safe_attribute_handler():\n'
        '    safe_attribute_wrapper()\n'
        '\n'
        'class Service:\n'
        '    def worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '\n'
        '    def wrapper(self):\n'
        '        return self.worker()\n'
        '\n'
        '    async def handler(self):\n'
        '        self.wrapper()\n'
        '\n'
        'class Other:\n'
        '    def worker(self):\n'
        '        return 1\n'
        '\n'
        '    async def safe_handler(self):\n'
        '        self.worker()\n'
        '\n'
        'class ClaimingWorker:\n'
        '    def __init__(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '\n'
        'async def constructor_direct():\n'
        '    ClaimingWorker()\n'
        '\n'
        'async def constructor_offloaded():\n'
        '    await asyncio.to_thread(ClaimingWorker)\n'
        '\n'
        'class ContextGuard:\n'
        '    def __enter__(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '        return self\n'
        '    def __exit__(self, *args):\n'
        '        pass\n'
        '\n'
        'async def context_protocol_direct():\n'
        '    with ContextGuard():\n'
        '        pass\n'
        '\n'
        'async def stored_context_protocol_direct():\n'
        '    guard = ContextGuard()\n'
        '    with guard:\n'
        '        pass\n'
        '\n'
        'async def stored_attribute_context_protocol_direct():\n'
        '    holder.guard = ContextGuard()\n'
        '    with holder.guard:\n'
        '        pass\n'
        '\n'
        'async def ambiguous_submit():\n'
        '    dispatcher.submit(_claiming_worker)\n'
        '\n'
        'partial_owner = functools.partial(_claiming_worker, value)\n'
        'async def partial_alias_direct():\n'
        '    partial_owner()\n'
        'async def partial_alias_offloaded():\n'
        '    await asyncio.to_thread(partial_owner)\n'
        '\n'
        'async def ambiguous_to_thread():\n'
        '    dispatcher.to_thread(_claiming_worker)\n'
        'async def ambiguous_run_in_executor():\n'
        '    dispatcher.run_in_executor(None, _claiming_worker)\n'
        'async def rebound_run_in_executor():\n'
        '    loop = asyncio.get_running_loop()\n'
        '    loop = dispatcher\n'
        '    loop.run_in_executor(None, _claiming_worker)\n'
        'async def conditional_run_in_executor():\n'
        '    if flag:\n'
        '        loop = dispatcher\n'
        '    else:\n'
        '        loop = asyncio.get_running_loop()\n'
        '    loop.run_in_executor(None, _claiming_worker)\n'
        'async def match_run_in_executor(value):\n'
        '    loop = asyncio.get_running_loop()\n'
        '    match value:\n'
        '        case _:\n'
        '            loop = dispatcher\n'
        '    loop.run_in_executor(None, _claiming_worker)\n'
        'async def try_run_in_executor():\n'
        '    try:\n'
        '        loop = asyncio.get_running_loop()\n'
        '        raise RuntimeError\n'
        '    except RuntimeError:\n'
        '        loop = dispatcher\n'
        '    loop.run_in_executor(None, _claiming_worker)\n'
        'async def shadowed_offload(offload):\n'
        '    await offload(_claiming_worker)\n'
        'async def conditional_to_thread_alias():\n'
        '    if flag:\n'
        '        dispatch = dispatcher\n'
        '    else:\n'
        '        dispatch = offload\n'
        '    await dispatch(_claiming_worker)\n'
        'async def loop_to_thread_alias(items):\n'
        '    dispatch = offload\n'
        '    for item in items:\n'
        '        dispatch = dispatcher\n'
        '    await dispatch(_claiming_worker)\n'
        'async def match_to_thread_alias(value):\n'
        '    dispatch = offload\n'
        '    match value:\n'
        '        case _:\n'
        '            dispatch = dispatcher\n'
        '    await dispatch(_claiming_worker)\n'
        'async def local_alias_offloaded():\n'
        '    callback = _claiming_worker\n'
        '    await asyncio.to_thread(callback)\n'
        'async def local_alias_direct():\n'
        '    callback = _claiming_worker\n'
        '    callback()\n'
        'async def local_partial_offloaded():\n'
        '    callback = functools.partial(_claiming_worker, value)\n'
        '    await asyncio.to_thread(callback)\n'
        'async def local_partial_direct():\n'
        '    callback = functools.partial(_claiming_worker, value)\n'
        '    callback()\n'
        'async def deferred_lambda_claim():\n'
        '    worker = lambda: _raise_inside(claim_content_folder(folder))\n'
        '    await asyncio.to_thread(worker)\n'
        'async def eager_lambda_default_claim():\n'
        '    worker = lambda guard=claim_content_folder(folder): guard\n'
        '    await asyncio.to_thread(worker)\n'
        'async def immediate_lambda_claim():\n'
        '    (lambda: claim_content_folder(folder))()\n'
        'async def conditional_local_alias():\n'
        '    callback = _claiming_worker\n'
        '    if flag:\n'
        '        callback = harmless\n'
        '    callback()\n'
        'async def while_local_alias(flag):\n'
        '    callback = harmless\n'
        '    while flag:\n'
        '        callback = _claiming_worker\n'
        '        break\n'
        '    callback()\n'
        'async def match_local_alias(value):\n'
        '    callback = harmless\n'
        '    match value:\n'
        '        case _:\n'
        '            callback = _claiming_worker\n'
        '    callback()\n'
        'async def overwritten_local_alias():\n'
        '    callback = _claiming_worker\n'
        '    callback = harmless\n'
        'async def exceptional_prefix_local_alias():\n'
        '    callback = harmless\n'
        '    try:\n'
        '        callback = _claiming_worker\n'
        '        may_raise()\n'
        '        callback = harmless\n'
        '    except Exception:\n'
        '        pass\n'
        '    callback()\n'
        'async def shadowed_asyncio(asyncio):\n'
        '    await asyncio.to_thread(_claiming_worker)\n'
        'async def shadowed_executor_loop(asyncio):\n'
        '    loop = asyncio.get_running_loop()\n'
        '    await loop.run_in_executor(None, _claiming_worker)\n'
        'async def stored_lambda_claim_direct():\n'
        '    worker = lambda: claim_content_folder(folder)\n'
        '    worker()\n'
        'async def consumed_generator_claim_direct(items):\n'
        '    work = (claim_content_folder(folder) for _ in items)\n'
        '    list(work)\n'
        'async def deferred_generator_claim(items):\n'
        '    work = (_raise_inside(claim_content_folder(folder)) for _ in items)\n'
        '    await asyncio.to_thread(list, work)\n'
        'async def eager_generator_iterable_claim():\n'
        '    work = (item for item in _raise_inside(claim_content_folder(folder)))\n'
        '    await asyncio.to_thread(list, work)\n'
        '\n'
        'class ModuleWrapperService:\n'
        '    def module_wrapper(self):\n'
        '        _claiming_worker()\n'
        '    async def module_wrapper_handler(self):\n'
        '        self.module_wrapper()\n'
        '\n'
        'class BaseService:\n'
        '    def inherited_worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        'class DerivedService(BaseService):\n'
        '    async def inherited_handler(self):\n'
        '        self.inherited_worker()\n'
        '    async def inherited_super_handler(self):\n'
        '        super().inherited_worker()\n'
        '\n'
        'class PropertyService:\n'
        '    @property\n'
        '    def property_worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '        return callback\n'
        '    async def property_handler(self):\n'
        '        await asyncio.to_thread(self.property_worker)\n'
        '    @functools.cached_property\n'
        '    def cached_worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '        return callback\n'
        '    async def cached_property_handler(self):\n'
        '        await asyncio.to_thread(self.cached_worker)\n'
        '\n'
        'async def stored_method_direct():\n'
        '    service = Service()\n'
        '    service.worker()\n'
        'async def conditional_stored_method():\n'
        '    service = Service()\n'
        '    if flag:\n'
        '        service = Other()\n'
        '    service.worker()\n'
        'class AttributeService:\n'
        '    async def attribute_handler(self):\n'
        '        self.service = Service()\n'
        '        self.service.worker()\n'
        '\n'
        'def with_claim(func):\n'
        '    def wrapped(*args):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            return func(*args)\n'
        '    return wrapped\n'
        '@with_claim\n'
        'def decorated_work():\n'
        '    pass\n'
        'async def decorated_handler():\n'
        '    decorated_work()\n'
        'def with_claim_factory(purpose):\n'
        '    def decorate(func):\n'
        '        def wrapped(*args):\n'
        '            with claim_content_folder(folder, purpose=purpose):\n'
        '                return func(*args)\n'
        '        return wrapped\n'
        '    return decorate\n'
        "@with_claim_factory(purpose='publish')\n"
        'def factory_decorated_work():\n'
        '    pass\n'
        'async def factory_decorated_handler():\n'
        '    factory_decorated_work()\n'
        'class CallableService:\n'
        '    def __call__(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        'async def callable_instance_direct():\n'
        '    service = CallableService()\n'
        '    service()\n'
        'async def callable_instance_offloaded():\n'
        '    service = CallableService()\n'
        '    await asyncio.to_thread(service)\n'
    )
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    claiming, generators, aliases, import_facts = _module_level_claiming_workers([
        ('synthetic', tree)
    ])

    def module_offenders(name):
        return _scope_claiming_names_called_on_loop(
            functions[name],
            'synthetic',
            None,
            claiming,
            generators,
            aliases,
            import_facts,
        )

    assert module_offenders('offloaded') == []
    assert module_offenders('imported_to_thread') == []
    assert module_offenders('local_imported_to_thread') == []
    assert module_offenders('rebound_imported_to_thread'), (
        'module-level to_thread alias 被普通 dispatcher 重绑定后必须失效'
    )
    assert module_offenders('module_loop_to_thread_alias'), (
        'module-level to_thread alias 必须合并 while 的 zero/nonzero 路径'
    )
    assert module_offenders('module_match_to_thread_alias'), (
        'module-level to_thread alias 必须合并 match 的全部可达 case'
    )
    assert module_offenders('module_try_to_thread_alias'), (
        'module-level to_thread alias 必须合并 try body 的全部异常前缀'
    )
    assert len(module_offenders('nested_module_owners_direct')) == 2, (
        'while 和 match 下定义的 module claim owners 都必须被发现'
    )
    assert module_offenders('direct'), (
        '模块级 claim owner 的同步 wrapper 被协程直调时也必须报出来'
    )
    assert module_offenders('lambda_body') == []
    assert module_offenders('lambda_returns_owner'), (
        'offload lambda 只返回 owner 时并没有在 worker 执行它，必须报出来'
    )
    assert module_offenders('lambda_returns_nested_lambda'), (
        'offload lambda 返回的 nested lambda body 仍在 worker 之外，必须报出来'
    )
    assert module_offenders('lambda_returns_generator'), (
        'offload lambda 返回的 generator body 仍在 worker 之外，必须报出来'
    )
    assert module_offenders('eager_lambda_default'), (
        'lambda 默认值在 offload 前求值，里面的 claim owner 必须报出来'
    )
    assert module_offenders('eager_nested_default'), (
        'nested def 默认值在定义 helper 时求值，里面的 claim owner 必须报出来'
    )
    assert module_offenders('eager_nested_decorator'), (
        'nested def decorator 在定义 helper 时求值，里面的 claim owner 必须报出来'
    )
    assert len(module_offenders('eager_nested_annotations')) == 2, (
        'nested def 参数和返回注解都在定义 helper 时求值，必须报出来'
    )
    assert _scope_claiming_names_called_on_loop(
        functions['handler'],
        'synthetic',
        'Service',
        claiming,
        generators,
        aliases,
        import_facts,
    ), 'class method claim owner 的同步 wrapper 被 async method 直调时必须报出来'
    assert _scope_claiming_names_called_on_loop(
        functions['safe_handler'],
        'synthetic',
        'Other',
        claiming,
        generators,
        aliases,
        import_facts,
    ) == [], '另一个 class 的同名安全方法不能被误报'
    assert module_offenders('generator_constructor_offloaded'), (
        'offload generator function 只会构造 generator，不会在 worker 执行 claim body'
    )
    assert module_offenders('generator_expression_offloaded'), (
        'offload generator-expression wrapper 也只会构造 generator，必须报出来'
    )
    assert module_offenders('eager_generator_offloaded') == [], (
        '已由 list 当场耗尽的 generator expression 不应把 worker 误报成构造器'
    )
    assert module_offenders('callable_wrapper_offloaded'), (
        'offload 只会拿到 named wrapper 返回的 lambda，claim body 仍未执行'
    )
    assert module_offenders('for_generator_offloaded') == [], (
        '同步 for 会在 worker 内当场耗尽 generator expression，不应误报'
    )
    assert module_offenders('eager_callback_direct'), (
        'list(map(owner, ...)) 会当场执行 owner，直调 wrapper 必须报出来'
    )
    assert module_offenders('eager_callback_offloaded') == []
    assert module_offenders('lazy_callback_offloaded'), (
        'offload 返回惰性 map 并没有在 worker 执行 callback，必须报出来'
    )
    assert module_offenders('context_worker_offloaded') == [], (
        'with 会在 worker 内完整进入并退出 contextmanager generator，不应误报'
    )
    assert _claim_calls_in_own_scope(functions['aliased_claim_direct'])
    assert _claim_calls_in_own_scope(functions['assigned_claim_direct'])
    assert module_offenders('constructor_direct'), (
        'claim-owning __init__ 的类在事件循环实例化时必须报出来'
    )
    assert module_offenders('constructor_offloaded') == []
    assert module_offenders('context_protocol_direct'), (
        'with Guard() 隐式调用 claim-owning __enter__ 时必须报出来'
    )
    assert module_offenders('stored_context_protocol_direct'), (
        '先存入局部变量的 context instance 也必须解析到 claim-owning __enter__'
    )
    assert module_offenders('stored_attribute_context_protocol_direct'), (
        '存入 attribute 的 context instance 也必须解析到 claim-owning __enter__'
    )
    assert module_offenders('ambiguous_submit'), (
        '无法证明 receiver 是 executor 的 submit 不能被当作安全 offload'
    )
    assert module_offenders('partial_alias_direct'), (
        'partial(owner, ...) 必须继承 module owner 身份'
    )
    assert module_offenders('partial_alias_offloaded') == []
    assert module_offenders('ambiguous_to_thread'), (
        '只有 asyncio.to_thread 才能算已证明的 worker offload'
    )
    assert module_offenders('ambiguous_run_in_executor'), (
        '未知 receiver 的 run_in_executor 不能被当成 event loop executor'
    )
    assert module_offenders('rebound_run_in_executor'), (
        'event-loop variable 重绑定后不能保留 offload 身份'
    )
    assert module_offenders('conditional_run_in_executor'), (
        '只有所有 if 分支都证明是 event loop receiver 才能接受 offload'
    )
    assert module_offenders('match_run_in_executor'), (
        'match case 重绑定 event-loop receiver 后不能保留 offload 身份'
    )
    assert module_offenders('try_run_in_executor'), (
        'event-loop receiver 必须合并 try/except 的所有退出路径'
    )
    assert module_offenders('shadowed_offload'), (
        '同名参数会遮蔽 module-level to_thread alias'
    )
    assert module_offenders('conditional_to_thread_alias'), (
        'local to_thread alias 只有所有分支一致时才能算已证明 offload'
    )
    assert module_offenders('loop_to_thread_alias'), (
        'local to_thread alias 必须合并 loop 的 zero/nonzero iteration paths'
    )
    assert module_offenders('match_to_thread_alias'), (
        'local to_thread alias 必须合并 match 的全部可达 case'
    )
    assert module_offenders('local_alias_offloaded') == []
    assert module_offenders('local_alias_direct'), (
        'local callable alias 直调仍必须继承 claim owner 身份'
    )
    assert module_offenders('local_partial_offloaded') == []
    assert module_offenders('local_partial_direct'), (
        'local partial 直调必须继承 claim owner 身份'
    )
    assert _claim_calls_in_own_scope(functions['deferred_lambda_claim']) == [], (
        'lambda body 是 deferred scope，不能归到 async handler 的直接 claim'
    )
    assert _claim_calls_in_own_scope(functions['eager_lambda_default_claim']), (
        'lambda default 在 handler 中立即求值，里面的 claim 仍必须报出'
    )
    assert _claim_calls_in_own_scope(functions['immediate_lambda_claim']), (
        '立即调用的 lambda body 会在 event loop 执行，里面的 claim 必须报出'
    )
    assert module_offenders('conditional_local_alias'), (
        '任一 conditional path 仍指向 claim owner 时，local alias 直调必须报出'
    )
    assert module_offenders('while_local_alias'), (
        'local callable owner alias 必须合并 while 的 zero/nonzero 路径'
    )
    assert module_offenders('match_local_alias'), (
        'local callable owner alias 必须合并 match 的全部可达 case'
    )
    assert module_offenders('overwritten_local_alias') == [], (
        '只保存后安全覆盖的 callable owner 不应把 Store target 当成调用'
    )
    assert module_offenders('exceptional_prefix_local_alias'), (
        'try body 的任一异常前缀都必须保留 local callable owner identity'
    )
    assert module_offenders('shadowed_asyncio'), (
        '同名参数遮蔽 asyncio module 后不能把 receiver 当成已证明 offload'
    )
    assert module_offenders('shadowed_executor_loop'), (
        '被参数遮蔽的 asyncio 不能伪造 event-loop receiver 身份'
    )
    assert _claim_calls_in_own_scope(functions['stored_lambda_claim_direct']), (
        'event loop 上同步调用 stored lambda 时必须扫描它的 body'
    )
    assert _claim_calls_in_own_scope(
        functions['consumed_generator_claim_direct']
    ), 'event loop 上被立即耗尽的 stored generator 必须扫描其 body'
    assert _claim_calls_in_own_scope(functions['deferred_generator_claim']) == [], (
        'generator body 是 deferred scope，在 worker 消费时不属于 handler 直接 claim'
    )
    assert _claim_calls_in_own_scope(functions['eager_generator_iterable_claim']), (
        'generator 最外层 iterable 在构造时立即求值，其中的 claim 必须报出'
    )
    assert _scope_claiming_names_called_on_loop(
        functions['module_wrapper_handler'],
        'synthetic', 'ModuleWrapperService', claiming, generators, aliases,
    ), 'class wrapper 直调 module-level claim owner 时必须传播 ownership'
    assert _scope_claiming_names_called_on_loop(
        functions['inherited_handler'],
        'synthetic', 'DerivedService', claiming, generators, aliases,
    ), '继承来的 claim-owning method 也必须在 derived async handler 中被发现'
    assert _scope_claiming_names_called_on_loop(
        functions['inherited_super_handler'],
        'synthetic', 'DerivedService', claiming, generators, aliases,
    ), 'super() 直调继承的 claim-owning method 也必须被发现'
    assert _scope_claiming_names_called_on_loop(
        functions['property_handler'],
        'synthetic', 'PropertyService', claiming, generators, aliases,
    ), 'property getter 会在 to_thread 收参前执行，不能算安全 offload'
    assert _scope_claiming_names_called_on_loop(
        functions['cached_property_handler'],
        'synthetic', 'PropertyService', claiming, generators, aliases,
    ), 'cached_property getter 也会在 to_thread 收参前在 event loop 执行'
    assert module_offenders('stored_method_direct'), (
        'stored class instance 的普通 claim-owning method 也必须解析'
    )
    assert module_offenders('conditional_stored_method'), (
        'stored instance class 必须保留所有 conditional path 的可能身份'
    )
    assert _scope_claiming_names_called_on_loop(
        functions['attribute_handler'],
        'synthetic', 'AttributeService', claiming, generators, aliases,
    ), 'self.attribute 中保存的 instance 也必须解析 claim-owning method'
    assert module_offenders('decorated_handler'), (
        '返回 claim-owning wrapper 的 local decorator 必须把 ownership 传给 work'
    )
    assert module_offenders('factory_decorated_handler'), (
        '参数化 decorator factory 返回的 claim-owning wrapper 也必须传播 ownership'
    )
    assert module_offenders('callable_instance_direct'), (
        'stored callable instance 的 claim-owning __call__ 必须被发现'
    )
    assert module_offenders('callable_instance_offloaded') == []
    assert module_offenders('safe_attribute_handler') == [], (
        'Safe().owner() 不能因 tail name 与模块 owner 相同而污染 wrapper'
    )

    source = ast.parse(
        'def owner():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
        'class Service:\n'
        '    def worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
    )
    consumer = ast.parse(
        'from .source import owner as imported_owner\n'
        'from . import source as workers\n'
        'assigned_owner = imported_owner\n'
        'async def imported_direct():\n'
        '    imported_owner()\n'
        'async def imported_offloaded():\n'
        '    await asyncio.to_thread(imported_owner)\n'
        'async def qualified_direct():\n'
        '    workers.owner()\n'
        'async def qualified_offloaded():\n'
        '    await asyncio.to_thread(workers.owner)\n'
        'async def assigned_direct():\n'
        '    assigned_owner()\n'
        'async def assigned_offloaded():\n'
        '    await asyncio.to_thread(assigned_owner)\n'
        'async def local_import_direct():\n'
        '    from .source import owner as local_owner\n'
        '    local_owner()\n'
        'async def local_import_offloaded():\n'
        '    from .source import owner as local_owner\n'
        '    await asyncio.to_thread(local_owner)\n'
        'async def local_module_direct():\n'
        '    from . import source as local_workers\n'
        '    local_workers.owner()\n'
        'async def assigned_module_direct():\n'
        '    dispatchers = workers\n'
        '    dispatchers.owner()\n'
        'async def immediate_instance_direct():\n'
        '    workers.Service().worker()\n'
    )
    unrelated = ast.parse(
        'def owner():\n'
        '    return 1\n'
        'async def safe_same_name():\n'
        '    owner()\n'
    )
    imported_claiming, imported_generators, imported_aliases, imported_facts = (
        _module_level_claiming_workers([
        ('source', source),
        ('consumer', consumer),
        ('unrelated', unrelated),
        ])
    )
    imported_functions = {
        node.name: node
        for tree in (consumer, unrelated)
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
    }

    assert _scope_claiming_names_called_on_loop(
        imported_functions['imported_direct'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
        imported_facts,
    ), '新模块里的 owner 经直接 import 后仍必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['imported_offloaded'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
        imported_facts,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['qualified_direct'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
        imported_facts,
    ), 'module alias 上的 owner 直调必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['qualified_offloaded'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
        imported_facts,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['assigned_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ), '模块级 assignment alias 必须继承 claim owner 身份'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['assigned_offloaded'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['local_import_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ), 'handler 内 import 的 claim owner 直调也必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['local_import_offloaded'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['local_module_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ), 'handler 内 module alias 的 owner 直调也必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['assigned_module_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ), '赋给局部变量的 module alias 仍必须解析 claim-owning owner'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['immediate_instance_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
        imported_facts,
    ), '立即构造的 imported class instance 也必须解析 claim-owning method'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['safe_same_name'],
        'unrelated',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
        imported_facts,
    ) == [], '另一个模块的同名安全函数不能被误报'


def _workshop_router_trees(package_dir: Path | None = None):
    package_dir = package_dir or Path(content_gate.__file__).resolve().parent
    return [
        (
            '.'.join(
                path.relative_to(package_dir).with_suffix('').parts[:-1]
                if path.stem == '__init__'
                else path.relative_to(package_dir).with_suffix('').parts
            ) or '__init__',
            ast.parse(path.read_text(encoding='utf-8')),
        )
        for path in sorted(package_dir.rglob('*.py'))
        if not {'__pycache__', '.pytest_cache'} & set(path.parts)
    ]


def _module_lambda_functions(tree) -> list[ast.FunctionDef]:
    """Expose module-level lambda bodies to the folder-operation guard."""
    functions = []
    for statement in _walk_module_scope(tree.body):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        if not isinstance(value, ast.Lambda):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        name = next(
            (_storage_key(target) for target in targets if _storage_key(target)),
            f'<lambda@{value.lineno}>',
        )
        expression = ast.copy_location(ast.Expr(value=value.body), value.body)
        function = ast.copy_location(
            ast.FunctionDef(
                name=name,
                args=value.args,
                body=[expression],
                decorator_list=[],
            ),
            value,
        )
        functions.append(ast.fix_missing_locations(function))
    return functions


def _module_executable_function(tree) -> ast.FunctionDef:
    """Represent import-time module/class statements without deferred bodies."""
    function = ast.FunctionDef(
        name='<module>',
        args=ast.arguments(
            posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
            kw_defaults=[], kwarg=None, defaults=[]
        ),
        body=tree.body,
        decorator_list=[],
    )
    function._prune_lambda_bodies = True
    return ast.fix_missing_locations(function)


def test_nested_router_imports_preserve_claim_ownership():
    source = ast.parse(
        'def owner():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
        'class Service:\n'
        '    def worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
    )
    consumer = ast.parse(
        'from .publish import owner\n'
        'from . import publish as workers\n'
        'async def handler():\n'
        '    owner()\n'
        'async def qualified_instance_handler():\n'
        '    service = workers.Service()\n'
        '    service.worker()\n'
    )
    package_initializer = ast.parse('from .publish import owner\n')
    package_consumer = ast.parse(
        'from .workers import owner\n'
        'async def package_handler():\n'
        '    owner()\n'
    )
    local_consumer = ast.parse(
        'async def local_handler():\n'
        '    from .workers.publish import owner\n'
        '    owner()\n'
    )
    conditional = ast.parse(
        'if enabled:\n'
        '    def conditional_owner():\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        'async def conditional_handler():\n'
        '    conditional_owner()\n'
    )
    decorator_source = ast.parse(
        'def with_claim(func):\n'
        '    def wrapped(*args):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            return func(*args)\n'
        '    return wrapped\n'
    )
    decorated_consumer = ast.parse(
        'from .decorators import with_claim\n'
        '@with_claim\n'
        'def decorated_work():\n'
        '    pass\n'
        'async def decorated_handler():\n'
        '    decorated_work()\n'
    )
    claim_module = ast.parse('VALUE = 1\n')
    qualified_claim = ast.parse(
        'from . import content_gate\n'
        'def qualified_owner():\n'
        '    with content_gate.claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
        'async def qualified_handler():\n'
        '    qualified_owner()\n'
    )
    base = ast.parse(
        'class Base:\n'
        '    def worker(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '    @staticmethod\n'
        '    def static_worker():\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
    )
    derived = ast.parse(
        'from .workers.base import Base\n'
        'class ImportedService(Base):\n'
        '    async def imported_base_handler(self):\n'
        '        self.worker()\n'
        'async def imported_instance_handler():\n'
        '    service = Base()\n'
        '    service.worker()\n'
        'async def imported_instance_offloaded():\n'
        '    service = Base()\n'
        '    await asyncio.to_thread(service.worker)\n'
        'async def imported_class_direct():\n'
        '    Base.static_worker()\n'
        'async def imported_class_offloaded():\n'
        '    await asyncio.to_thread(Base.static_worker)\n'
        'class ConditionalService:\n'
        '    if enabled:\n'
        '        def conditional_worker(self):\n'
        '            with claim_content_folder(folder, purpose=p):\n'
        '                pass\n'
        '    async def conditional_method_handler(self):\n'
        '        self.conditional_worker()\n'
    )
    claiming, generators, aliases, import_facts = _module_level_claiming_workers([
        ('workers.publish', source),
        ('workers.consumer', consumer),
        ('workers', package_initializer),
        ('package_consumer', package_consumer),
        ('consumer', local_consumer),
        ('conditional', conditional),
        ('decorators', decorator_source),
        ('decorated_consumer', decorated_consumer),
        ('content_gate', claim_module),
        ('qualified', qualified_claim),
        ('workers.base', base),
        ('derived', derived),
    ])
    handler = next(
        node for node in consumer.body if isinstance(node, ast.AsyncFunctionDef)
    )

    assert _scope_claiming_names_called_on_loop(
        handler, 'workers.consumer', None, claiming, generators, aliases, import_facts
    ), '递归发现的 subpackage module import 也必须保留 claim ownership'
    qualified_instance_handler = next(
        node for node in consumer.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == 'qualified_instance_handler'
    )
    assert _scope_claiming_names_called_on_loop(
        qualified_instance_handler,
        'workers.consumer', None, claiming, generators, aliases, import_facts,
    ), 'module alias 上构造的 class instance 必须解析 claim-owning method'
    package_handler = next(
        node for node in package_consumer.body if isinstance(node, ast.AsyncFunctionDef)
    )
    assert _scope_claiming_names_called_on_loop(
        package_handler,
        'package_consumer', None, claiming, generators, aliases, import_facts,
    ), 'subpackage __init__ re-export 的 claim owner 必须保留 ownership'
    local_handler = next(
        node for node in local_consumer.body if isinstance(node, ast.AsyncFunctionDef)
    )
    assert _scope_claiming_names_called_on_loop(
        local_handler, 'consumer', None, claiming, generators, aliases, import_facts
    ), 'handler-local relative import 必须保留完整 dotted module path'
    imported_handler = next(
        node for node in ast.walk(derived)
        if isinstance(node, ast.AsyncFunctionDef)
    )
    assert _scope_claiming_names_called_on_loop(
        imported_handler,
        'derived', 'ImportedService', claiming, generators, aliases, import_facts,
    ), 'imported base class 的 claim-owning method 必须传播到 derived scope'
    derived_functions = {
        node.name: node
        for node in derived.body
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert _scope_claiming_names_called_on_loop(
        derived_functions['imported_instance_handler'],
        'derived', None, claiming, generators, aliases, import_facts,
    ), 'imported class instance 的 claim-owning method 必须解析到源 class scope'
    assert _scope_claiming_names_called_on_loop(
        derived_functions['imported_instance_offloaded'],
        'derived', None, claiming, generators, aliases, import_facts,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        derived_functions['imported_class_direct'],
        'derived', None, claiming, generators, aliases, import_facts,
    ), 'imported class receiver 上的 claim-owning static method 必须解析'
    assert _scope_claiming_names_called_on_loop(
        derived_functions['imported_class_offloaded'],
        'derived', None, claiming, generators, aliases, import_facts,
    ) == []
    conditional_handler = next(
        node for node in conditional.body if isinstance(node, ast.AsyncFunctionDef)
    )
    assert _scope_claiming_names_called_on_loop(
        conditional_handler,
        'conditional', None, claiming, generators, aliases, import_facts,
    ), 'module control flow 下定义的 claim owner 必须进入 inventory'
    decorated_handler = next(
        node for node in decorated_consumer.body
        if isinstance(node, ast.AsyncFunctionDef)
    )
    assert _scope_claiming_names_called_on_loop(
        decorated_handler,
        'decorated_consumer', None, claiming, generators, aliases, import_facts,
    ), 'imported claim-taking decorator 必须传播到 decorated function'
    qualified_handler = next(
        node for node in qualified_claim.body if isinstance(node, ast.AsyncFunctionDef)
    )
    assert _scope_claiming_names_called_on_loop(
        qualified_handler,
        'qualified', None, claiming, generators, aliases, import_facts,
    ), 'verified module-qualified claim factory 必须进入 owner inventory'
    conditional_method_handler = next(
        node for node in ast.walk(derived)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == 'conditional_method_handler'
    )
    assert _scope_claiming_names_called_on_loop(
        conditional_method_handler,
        'derived', 'ConditionalService', claiming, generators, aliases, import_facts,
    ), 'class control flow 下定义的 claim-owning method 必须进入 inventory'


def test_module_claim_factories_must_keep_their_verified_binding():
    tree = ast.parse(
        'from contextlib import nullcontext as claim_content_folder\n'
        'def unsafe(content_folder):\n'
        '    with claim_content_folder(content_folder):\n'
        '        shutil.rmtree(content_folder)\n'
    )
    _module_level_claiming_workers([('shadowed_claim', tree)])
    unsafe = next(node for node in tree.body if isinstance(node, ast.FunctionDef))

    assert _unclaimed_folder_operations(unsafe, 'shadowed_claim'), (
        'module import 覆盖 bare claim factory 后不能再把同名调用当作真实 claim'
    )


def test_iterator_protocol_claims_are_counted_as_event_loop_work():
    tree = ast.parse(
        'class IterOwner:\n'
        '    def __iter__(self):\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            pass\n'
        '        return self\n'
        '    def __next__(self):\n'
        '        raise StopIteration\n'
        'async def handler():\n'
        '    for item in IterOwner():\n'
        '        pass\n'
    )
    claiming, generators, aliases, import_facts = _module_level_claiming_workers([
        ('synthetic_iterator', tree)
    ])
    handler = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
    )

    assert _scope_claiming_names_called_on_loop(
        handler,
        'synthetic_iterator',
        None,
        claiming,
        generators,
        aliases,
        import_facts,
    ), 'for 必须解析 claim-owning __iter__ / __next__ protocol calls'


def test_workshop_router_discovery_is_recursive(tmp_path):
    (tmp_path / 'top.py').write_text('VALUE = 1\n', encoding='utf-8')
    workers = tmp_path / 'workers'
    workers.mkdir()
    (workers / 'publish.py').write_text('VALUE = 2\n', encoding='utf-8')

    modules = {name for name, _ in _workshop_router_trees(tmp_path)}
    assert modules == {'top', 'workers.publish'}
    nested_purge = ast.parse(
        'def purge(content_folder):\n'
        '    shutil.rmtree(content_folder)\n'
    ).body[0]
    assert _unclaimed_folder_operations(nested_purge, 'workers.cleanup'), (
        'nested router module 也必须启用 generic folder operations'
    )

    lambda_tree = ast.parse(
        'purge = lambda content_folder: shutil.rmtree(content_folder)\n'
    )
    lambda_functions = _module_lambda_functions(lambda_tree)
    assert len(lambda_functions) == 1
    assert _unclaimed_folder_operations(
        lambda_functions[0], 'workers.cleanup'
    ), 'module-level lambda body 也必须进入 folder-operation guard'

    executable_tree = ast.parse(
        "content_folder = os.environ['WORKSHOP_FOLDER']\n"
        'shutil.rmtree(content_folder)\n'
    )
    assert _unclaimed_folder_operations(
        _module_executable_function(executable_tree), 'workers.cleanup'
    ), 'module import-time executable operations 也必须进入 folder-operation guard'


def test_no_claim_is_ever_taken_on_the_event_loop():
    """Rule 1, pinned: a claim taken in an ``async def`` is released by cancellation.

    Nothing else would fail if someone hoisted the claim up into the handler.
    It would read more tidily, every other test would stay green, and the
    folder would quietly go free the moment a client disconnected -- with the
    worker still writing into it.
    """
    trees = _workshop_router_trees()
    claiming_workers, generator_workers, module_aliases, import_facts = (
        _module_level_claiming_workers(trees)
    )

    offenders = []
    for short, tree in trees:
        parents = {
            id(child): parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            class_name = None
            current = node
            while id(current) in parents:
                current = parents[id(current)]
                if isinstance(current, ast.ClassDef):
                    class_name = current.name
                    break
            # 嵌套的 async def 会被这层 walk 单独取到，各查各的作用域。
            for call in _claim_calls_in_own_scope(node):
                offenders.append(f'{short}.{node.name}:{call.lineno}')
            for helper, line in _claiming_helpers_called_on_loop(
                node, import_facts[short]['to_thread']
            ):
                offenders.append(f'{short}.{node.name}:{line} -> {helper}（在事件循环直调）')
            for worker, line in _scope_claiming_names_called_on_loop(
                node,
                short,
                class_name,
                claiming_workers,
                generator_workers,
                module_aliases,
                import_facts,
            ):
                offenders.append(f'{short}.{node.name}:{line} -> {worker}（模块 worker 未 offload）')

    assert not offenders, f'这些占用是在协程里拿的，取消会把它们提前放开：{offenders}'


# 会消费或摧毁整个内容目录、或者改动那对参考语音文件的操作。引用到它们的函数必须把
# 这次引用放在 claim 的 with **里面** —— 不是「同一个函数里也有个 claim」，否则把工作
# 挪到 with 外面一行就能绕过去。
_MUST_BE_CLAIMED = {
    '_publish_workshop_item',             # Steam 把整个目录读走
    '_cleanup_workshop_voice_reference',  # 删掉这对文件
    'atomic_write_json',                  # 提交新 manifest，swap 的唯一提交点
    'rmtree',                             # 删掉整个目录
    # 预览图也是「Steam 会一起读走的字节」，跟那对参考语音没有区别。
    'copy', 'copy2', 'copyfile', 'copytree', 'move',
}

# These names are domain-specific enough to scan in every router module. The
# generic file APIs above stay module/unit scoped to avoid treating unrelated
# metadata writes as content-folder mutations.
_PACKAGE_WIDE_OPERATIONS = {
    '_publish_workshop_item',
    '_cleanup_workshop_voice_reference',
    'atomic_write_bytes',
}

# Common pathlib mutation methods are too generic to scan by tail name alone.
# They become package-wide operations only when their receiver flows from an
# explicit content-folder value.
_CONTENT_PATH_MUTATION_METHODS = {
    'mkdir', 'rename', 'replace', 'rmdir', 'touch', 'unlink',
    'write_bytes', 'write_text',
}

_OS_CONTENT_PATH_MUTATION_ARGS = {
    'mkdir': ((0, 'path'),),
    'makedirs': ((0, 'name'),),
    'remove': ((0, 'path'),),
    'unlink': ((0, 'path'),),
    'rmdir': ((0, 'path'),),
    'rename': ((0, 'src'), (1, 'dst')),
    'replace': ((0, 'src'), (1, 'dst')),
    'truncate': ((0, 'path'),),
}

_COPY_CONTENT_PATH_ARGS = {
    'copy': ((0, 'src'), (1, 'dst')),
    'copy2': ((0, 'src'), (1, 'dst')),
    'copyfile': ((0, 'src'), (1, 'dst')),
    'copytree': ((0, 'src'), (1, 'dst')),
    'move': ((0, 'src'), (1, 'dst')),
}

_OPEN_WRITE_OPERATION = '<open-write>'

_PACKAGE_OPERATION_CLAIMS = {
    '_publish_workshop_item': {'claim_content_folder'},
    'atomic_write_bytes': {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
    '_cleanup_workshop_voice_reference': {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
}

_FOLDER_OPERATION_CLAIMS = {
    'rmtree': {'claim_content_folder'},
    'copy': {'claim_content_folder'},
    'copy2': {'claim_content_folder'},
    'copyfile': {'claim_content_folder'},
    'copytree': {'claim_content_folder'},
    'move': {'claim_content_folder'},
    'remove': {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
    'mkdir': {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
    'makedirs': {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
    'truncate': {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
    _OPEN_WRITE_OPERATION: {
        'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
    },
    **{
        name: {
            'claim_content_folder', 'claim_partial_writer', 'claim_reference_pair',
        }
        for name in _CONTENT_PATH_MUTATION_METHODS
    },
}

_FOLDER_OPERATION_ARGS = {
    'rmtree': 0,
    'copy': 1,
    'copy2': 1,
    'copyfile': 1,
    'copytree': 1,
    'move': 1,
    **{name: arguments[0][0] for name, arguments in _OS_CONTENT_PATH_MUTATION_ARGS.items()},
}

_PACKAGE_OPERATION_FOLDER_ARGS = {
    '_publish_workshop_item': 3,
    '_cleanup_workshop_voice_reference': 0,
    'atomic_write_bytes': 0,
}

_PACKAGE_OPERATION_FOLDER_KEYWORDS = {
    '_publish_workshop_item': 'content_folder',
    '_cleanup_workshop_voice_reference': 'content_folder',
    'atomic_write_bytes': 'path',
    'rmtree': 'path',
    'copy': 'dst',
    'copy2': 'dst',
    'copyfile': 'dst',
    'copytree': 'dst',
    'move': 'dst',
}

# 这些名字过于通用，不能全模块扫描；但在指定 worker 单元里，它们正是内容目录的
# 完整读写边界。把它们列出来，移动 tmp 创建、音频 replace/remove 或 preflight 到
# claim 外面都会被抓住，而不会把别处无关的 ``write`` 当成目录竞态。
_UNIT_OPERATIONS = {
    ('publish', '_preflight_and_publish'): {
        'resolve_voice_reference_serialized': 1,
        '_publish_workshop_item': 1,
    },
    ('voice_refs', '_replace_voice_reference'): {
        '_current_reference_audio_path': 1,
        'mkstemp': 1,
        'fdopen': 1,
        'write': 1,
        'flush': 1,
        'fsync': 1,
        'replace': 1,
        'atomic_write_json': 1,
        'remove': 3,
    },
    ('voice_refs', '_remove_voice_reference'): {'_cleanup_workshop_voice_reference': 1},
    ('publish', '_delete_content_folder'): {'rmtree': 1},
    ('preview_cards', '_write_claimed_preview_image'): {'atomic_write_bytes': 1},
}

_UNIT_CLAIMS = {
    ('publish', '_preflight_and_publish'): 'claim_content_folder',
    ('voice_refs', '_replace_voice_reference'): 'claim_reference_pair',
    ('voice_refs', '_remove_voice_reference'): 'claim_reference_pair',
    ('publish', '_delete_content_folder'): 'claim_content_folder',
    ('preview_cards', '_write_claimed_preview_image'): 'claim_partial_writer',
}

# 把工作推迟到别处去跑的原语。哨兵名出现在它们的**实参**里时，「写在 with 里面」
# 什么都不证明 —— `with claim: executor.submit(_publish_workshop_item, ...)` 的
# 上传会在占用放开之后才真正发生，正是这条守卫要防的那个竞态，而按词法包含判定
# 它是绿的。所以这种形状一律算越界，不看它嵌在哪儿。
_DEFERRAL_CALLS = {
    'to_thread', 'run_in_executor', 'submit', 'map', 'Thread',
    'create_task', 'ensure_future', 'partial',
}

# 结构性豁免：prepare 创建的目录还没返回给任何人；publish/cleanup 本身则是
# package-wide sentinel，所有调用点都必须持有 claim，因此内部 I/O 继承调用方占用。
_ALLOWED_UNCLAIMED = {
    ('publish', 'prepare_workshop_upload'),
    ('publish', '_publish_workshop_item'),
    ('preview_cards', '_write_preview_image'),
    ('voice_manifest', '_cleanup_workshop_voice_reference'),
}


def _is_allowed_unclaimed(
    module: str, function: str, *, top_level: bool
) -> bool:
    return top_level and (module, function) in _ALLOWED_UNCLAIMED

# ⚠️ 已知缺口，不是豁免。放在这里是为了让它**可见**、而且会随代码漂移被重新审视：
# publish_to_workshop 把预览图 copy2 进内容目录发生在 claim **之前**，同一目录被
# 重复发布时仍能撕裂预览图。独立的 /upload-preview-image 路径已由 #2627 改成
# worker 内的 partial claim + atomic write，因此不再列为欠账。
_KNOWN_GAPS = {
    # 绑定到具体源码位置，而不是同函数同名操作的数量；旧点被修、新点冒出来不能互换。
    ('publish', 'publish_to_workshop', 596, 'copy2'),
    ('publish', 'publish_to_workshop', 620, 'copy2'),
}


def _operation_name(node) -> str | None:
    if isinstance(node, ast.NamedExpr):
        return _operation_name(node.value)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return node.attr
    return None


def _known_operation_names() -> set[str]:
    names = set(_PACKAGE_WIDE_OPERATIONS) | set(_MUST_BE_CLAIMED)
    names |= set(_OS_CONTENT_PATH_MUTATION_ARGS)
    names |= set(_CONTENT_PATH_MUTATION_METHODS)
    for inventory in _UNIT_OPERATIONS.values():
        names.update(inventory)
    return names


def _operation_aliases(
    nodes, seed=None, before_line: int | None = None
) -> dict[str, str]:
    """Resolve protected-operation aliases in source order."""
    known = _known_operation_names()
    def operation_set(value):
        values = {value} if isinstance(value, str) else set(value or ())
        return frozenset(values & known)

    def merge_possible(left, right):
        return {
            name: operation_set(left.get(name)) | operation_set(right.get(name))
            for name in set(left) | set(right)
            if operation_set(left.get(name)) | operation_set(right.get(name))
        }

    def after(statements, state):
        state = {
            name: operations
            for name, value in state.items()
            if (operations := operation_set(value))
        }
        for node in statements:
            if before_line is not None and node.lineno >= before_line:
                continue
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    canonical = operation_set(state.get(alias.name, alias.name))
                    local = alias.asname or alias.name
                    if canonical:
                        state[local] = canonical
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name != 'os':
                        continue
                    local = alias.asname or alias.name
                    for operation in _OS_CONTENT_PATH_MUTATION_ARGS:
                        state[f'{local}.{operation}'] = frozenset({operation})
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                reference = (
                    value.args[0]
                    if isinstance(value, ast.Call)
                    and _tail_name(value) == 'partial'
                    and value.args
                    else value
                )
                reference_name = _operation_name(reference) or ''
                qualified = _storage_key(reference) or reference_name
                canonical = operation_set(
                    state.get(qualified, state.get(reference_name, reference_name))
                )
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    target_key = _storage_key(target)
                    if target_key is None:
                        continue
                    if canonical:
                        state[target_key] = canonical
                    else:
                        state.pop(target_key, None)
            elif isinstance(node, ast.If):
                state = merge_possible(
                    after(node.body, state), after(node.orelse, state)
                )
            elif isinstance(node, ast.With):
                state = after(node.body, state)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                state = merge_possible(state, after(node.body, state))
                state = after(node.orelse, state)
            elif isinstance(node, ast.Match):
                paths = [after(case.body, state) for case in node.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in node.cases
                )
                if not exhaustive:
                    paths.append(state)
                if paths:
                    state = paths[0]
                    for path in paths[1:]:
                        state = merge_possible(state, path)
            elif isinstance(node, ast.Try):
                prefix_states = [state]
                body_state = state
                for statement in node.body:
                    body_state = after([statement], body_state)
                    prefix_states.append(body_state)
                paths = [after(node.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in node.handlers
                    for prefix_state in prefix_states
                )
                state = paths[0]
                for path in paths[1:]:
                    state = merge_possible(state, path)
                state = after(node.finalbody, state)
        return state

    return after(list(nodes), dict(seed or {}))


def _is_operation_alias_source(node, parents: dict[int, ast.AST]) -> bool:
    """An assignment RHS defines an alias; only its later uses execute work."""
    parent = parents.get(id(node))
    if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
        return True
    if isinstance(parent, ast.NamedExpr) and parent.value is node:
        return True
    return (
        isinstance(parent, ast.Call)
        and _tail_name(parent) == 'partial'
        and bool(parent.args)
        and parent.args[0] is node
        and isinstance(parents.get(id(parent)), (ast.Assign, ast.AnnAssign))
    )


def _looks_like_content_folder(name: str) -> bool:
    return name == 'folder' or name == 'content_folder' or name.endswith(
        '_content_folder'
    )


def _path_origins(func, before_line: int) -> dict[str, set[str]]:
    """Original function arguments that each path-like local may derive from."""
    arguments = {
        arg.arg
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        )
    }
    arguments.update(
        arg.arg for arg in (func.args.vararg, func.args.kwarg) if arg is not None
    )

    def expression_origins(node, state):
        return set().union(*(
            state.get(child.id, {child.id})
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        )) if node is not None else set()

    def merge(left, right):
        return {
            name: left.get(name, set()) | right.get(name, set())
            for name in set(left) | set(right)
        }

    def eager_expressions(statement):
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return [statement.value]
        if isinstance(statement, ast.Expr):
            return [statement.value]
        if isinstance(statement, (ast.If, ast.While)):
            return [statement.test]
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            return [statement.iter]
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            return [item.context_expr for item in statement.items]
        if isinstance(statement, ast.Match):
            return [statement.subject]
        if isinstance(statement, ast.Assert):
            return [statement.test, statement.msg]
        if isinstance(statement, (ast.Return, ast.Yield, ast.YieldFrom, ast.Raise)):
            return [getattr(statement, 'value', None)]
        return []

    def named_expressions(root):
        stack = [root] if root is not None else []
        while stack:
            node = stack.pop()
            if isinstance(node, ast.NamedExpr):
                yield node
            if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
                continue
            stack.extend(ast.iter_child_nodes(node))

    def after(statements, state):
        state = {name: set(origins) for name, origins in state.items()}
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            for root in eager_expressions(statement):
                for named in named_expressions(root):
                    origins = expression_origins(named.value, state)
                    for name in _assigned_names(named):
                        if origins:
                            state[name] = origins
                        else:
                            state.pop(name, None)
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                origins = expression_origins(statement.value, state)
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    bound_names = {
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name)
                        and isinstance(child.ctx, ast.Store)
                    }
                    for name in bound_names:
                        named_origins = set(origins)
                        if _looks_like_content_folder(name):
                            named_origins.add(name)
                        if named_origins:
                            state[name] = named_origins
                        else:
                            state.pop(name, None)
            elif isinstance(statement, (ast.If, ast.While)):
                if isinstance(statement, ast.If):
                    containing = next((
                        branch
                        for branch in (statement.body, statement.orelse)
                        if any(
                            nested.lineno <= before_line <= nested.end_lineno
                            for nested in branch
                        )
                    ), None)
                    if statement.lineno < before_line <= statement.end_lineno and containing:
                        state = after(containing, state)
                        continue
                    # A later write is relevant if either reachable path can
                    # still point into the content folder.
                    state = merge(
                        after(statement.body, state), after(statement.orelse, state)
                    )
                else:
                    state = merge(state, after(statement.body, state))
                    state = after(statement.orelse, state)
            elif isinstance(statement, ast.Match):
                containing = next((
                    case.body
                    for case in statement.cases
                    if any(
                        nested.lineno <= before_line <= nested.end_lineno
                        for nested in case.body
                    )
                ), None)
                if statement.lineno < before_line <= statement.end_lineno and containing:
                    state = after(containing, state)
                    continue
                paths = [after(case.body, state) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(state)
                if paths:
                    state = paths[0]
                    for path in paths[1:]:
                        state = merge(state, path)
            elif isinstance(statement, ast.With):
                state = after(statement.body, state)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                state = merge(state, after(statement.body, state))
                state = after(statement.orelse, state)
            elif isinstance(statement, ast.Try):
                prefix_states = [state]
                body_state = state
                for nested in statement.body:
                    body_state = after([nested], body_state)
                    prefix_states.append(body_state)
                paths = [after(statement.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in statement.handlers
                    for prefix_state in prefix_states
                )
                state = paths[0]
                for path in paths[1:]:
                    state = merge(state, path)
                state = after(statement.finalbody, state)
        return state

    return after(func.body, {name: {name} for name in arguments})


def _expression_path_origins(node, func, before_line: int) -> set[str]:
    origins = _path_origins(func, before_line)
    return set().union(*(
        origins.get(child.id, {child.id})
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    )) if node is not None else set()


def _expression_argument_origins(node, func, before_line: int) -> set[str]:
    arguments = {
        arg.arg
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        )
    }
    arguments.update(
        arg.arg for arg in (func.args.vararg, func.args.kwarg) if arg is not None
    )
    return _expression_path_origins(node, func, before_line) & arguments


def _content_path_mutation_nodes(func) -> list[tuple[ast.AST, str]]:
    found = []
    for node in _walk_own_scope(func):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr not in _CONTENT_PATH_MUTATION_METHODS
        ):
            continue
        origins = _expression_path_origins(node.func.value, func, node.lineno)
        if node.func.attr == 'replace' and len(node.args) > 1:
            # ``str.replace(old, new[, count])`` is not ``Path.replace(target)``.
            continue
        if any(_looks_like_content_folder(name) for name in origins):
            found.append((node.func, node.func.attr))
    return found


def _known_open_flag_names(func, before_line: int) -> dict[str, set[str]]:
    """Possible ``os.open`` flag constants stored in local variables."""
    flag_names = {'O_WRONLY', 'O_RDWR', 'O_APPEND', 'O_CREAT', 'O_TRUNC'}

    def merge(*states):
        return {
            name: set().union(*(state.get(name, set()) for state in states))
            for name in set().union(*(set(state) for state in states))
        }

    def expression_flags(node, state):
        flags = {
            name
            for child in ast.walk(node)
            if (name := _reference_name(child)) in flag_names
        }
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                flags.update(state.get(child.id, set()))
        return flags

    def after(statements, state):
        state = {name: set(flags) for name, flags in state.items()}
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                flags = expression_flags(statement.value, state)
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    for name in {
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name)
                    }:
                        if flags:
                            state[name] = set(flags)
                        else:
                            state.pop(name, None)
            elif isinstance(statement, ast.If):
                state = merge(
                    after(statement.body, state), after(statement.orelse, state)
                )
            elif isinstance(statement, ast.Match):
                paths = [after(case.body, state) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(state)
                state = merge(*paths)
            elif isinstance(statement, ast.With):
                state = after(statement.body, state)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                state = merge(state, after(statement.body, state))
                state = after(statement.orelse, state)
            elif isinstance(statement, ast.Try):
                prefix_states = [state]
                body_state = state
                for nested in statement.body:
                    body_state = after([nested], body_state)
                    prefix_states.append(body_state)
                paths = [after(statement.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in statement.handlers
                    for prefix_state in prefix_states
                )
                state = after(statement.finalbody, merge(*paths))
        return state

    return after(func.body, {})


def _open_write_nodes(func) -> list[tuple[ast.AST, str]]:
    write_flags = {'O_WRONLY', 'O_RDWR', 'O_APPEND', 'O_CREAT', 'O_TRUNC'}
    handle_methods = {'write', 'writelines', 'truncate', 'flush', 'close'}
    parents = _parent_map(func)
    handle_paths = {}
    found = []
    for node in _walk_own_scope(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        is_os_open = (
            isinstance(target, ast.Attribute)
            and target.attr == 'open'
            and isinstance(target.value, ast.Name)
            and target.value.id in _known_os_module_names(func, node.lineno)
        )
        is_builtin_open = (
            isinstance(target, ast.Name)
            and target.id in _known_builtin_open_names(func, node.lineno)
        )
        is_path_open = (
            isinstance(target, ast.Attribute)
            and target.attr == 'open'
            and not is_os_open
        )
        if not (is_os_open or is_builtin_open or is_path_open):
            continue
        if is_os_open:
            flags = node.args[1] if len(node.args) > 1 else next((
                item.value for item in node.keywords if item.arg == 'flags'
            ), None)
            writes = flags is not None and any(
                _reference_name(child) in write_flags
                for child in ast.walk(flags)
            )
            if isinstance(flags, ast.Name):
                writes = writes or bool(
                    _known_open_flag_names(func, node.lineno).get(flags.id, set())
                    & write_flags
                )
        else:
            mode_index = 0 if is_path_open else 1
            mode = (
                node.args[mode_index]
                if len(node.args) > mode_index
                else next((
                    item.value for item in node.keywords if item.arg == 'mode'
                ), None)
            )
            mode_values = (
                {mode.value}
                if isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                else _known_string_constants(func, node.lineno).get(mode.id, set())
                if isinstance(mode, ast.Name)
                else set()
            )
            writes = (
                mode is not None and not mode_values
            ) or any(flag in value for value in mode_values for flag in 'wax+')
        if writes:
            path = (
                target.value
                if is_path_open
                else node.args[0]
                if node.args
                else next((
                    item.value
                    for item in node.keywords
                    if item.arg in {'file', 'path'}
                ), None)
            )
            origins = _expression_path_origins(path, func, node.lineno)
            if any(_looks_like_content_folder(origin) for origin in origins):
                found.append((target, _OPEN_WRITE_OPERATION))
                assignment = parents.get(id(node))
                if isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        assignment.targets
                        if isinstance(assignment, ast.Assign)
                        else [assignment.target]
                    )
                    for assigned in targets:
                        if (key := _storage_key(assigned)) is not None:
                            handle_paths[key] = (path, node.lineno)
    for node in _walk_own_scope(func):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr not in handle_methods
        ):
            continue
        key = _storage_key(node.func.value)
        path_and_line = handle_paths.get(key)
        if path_and_line is None or node.lineno <= path_and_line[1]:
            continue
        node.func._open_handle_path = path_and_line[0]
        found.append((node.func, _OPEN_WRITE_OPERATION))
    return found


def _known_builtin_open_names(func, before_line: int) -> set[str]:
    """Names proven to refer to the builtin ``open`` on every path."""
    parameters = {
        arg.arg
        for arg in (
            list(func.args.posonlyargs)
            + list(func.args.args)
            + list(func.args.kwonlyargs)
        )
    }
    parameters.update(
        arg.arg for arg in (func.args.vararg, func.args.kwarg) if arg is not None
    )

    def merge(*states):
        return set.intersection(*(set(state) for state in states))

    def after(statements, names):
        names = set(names)
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                is_open = isinstance(value, ast.Name) and value.id in names
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        if is_open:
                            names.add(target.id)
                        else:
                            names.discard(target.id)
            elif isinstance(
                statement, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                            ast.AsyncFunctionDef, ast.ClassDef)
            ):
                bound = (
                    [alias.asname or alias.name.split('.')[0] for alias in statement.names]
                    if isinstance(statement, (ast.Import, ast.ImportFrom))
                    else [statement.name]
                )
                names.difference_update(bound)
            elif isinstance(statement, ast.If):
                names = merge(
                    after(statement.body, names), after(statement.orelse, names)
                )
            elif isinstance(statement, ast.Match):
                paths = [after(case.body, names) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(names)
                names = merge(*paths)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                names = merge(names, after(statement.body, names))
                names = after(statement.orelse, names)
            elif isinstance(statement, ast.With):
                names = after(statement.body, names)
            elif isinstance(statement, ast.Try):
                prefix_states = [names]
                body_names = names
                for nested in statement.body:
                    body_names = after([nested], body_names)
                    prefix_states.append(body_names)
                paths = [after(statement.orelse, body_names)]
                paths.extend(
                    after(handler.body, prefix)
                    for handler in statement.handlers
                    for prefix in prefix_states
                )
                names = after(statement.finalbody, merge(*paths))
        return names

    return after(func.body, {'open'} - parameters)


def _known_bound_mutation_receivers(func, before_line: int):
    """Possible receivers retained by aliases of bound pathlib mutators."""
    def merge(*states):
        return {
            name: set().union(*(state.get(name, set()) for state in states))
            for name in set().union(*(set(state) for state in states))
        }

    def after(statements, state):
        state = {name: set(receivers) for name, receivers in state.items()}
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                receivers = (
                    {value.value}
                    if isinstance(value, ast.Attribute)
                    and value.attr in _CONTENT_PATH_MUTATION_METHODS
                    else set(state.get(_storage_key(value) or '', set()))
                )
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    key = _storage_key(target)
                    if key is None:
                        continue
                    if receivers:
                        state[key] = set(receivers)
                    else:
                        state.pop(key, None)
            elif isinstance(statement, ast.If):
                state = merge(
                    after(statement.body, state), after(statement.orelse, state)
                )
            elif isinstance(statement, ast.Match):
                paths = [after(case.body, state) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(state)
                state = merge(*paths)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                state = merge(state, after(statement.body, state))
                state = after(statement.orelse, state)
            elif isinstance(statement, ast.With):
                state = after(statement.body, state)
            elif isinstance(statement, ast.Try):
                prefix_states = [state]
                body_state = state
                for nested in statement.body:
                    body_state = after([nested], body_state)
                    prefix_states.append(body_state)
                paths = [after(statement.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in statement.handlers
                    for prefix_state in prefix_states
                )
                state = after(statement.finalbody, merge(*paths))
        return state

    return after(func.body, {})


def _inventory_for_function(mapping, short: str, name: str):
    exact = mapping.get((short, name))
    if exact is not None:
        return (short, name), exact
    matches = [(key, value) for key, value in mapping.items() if key[1] == name]
    return matches[0] if len(matches) == 1 else (None, None)


def _operation_nodes(
    func, short: str, aliases: dict[str, str] | None = None
) -> list[tuple[ast.AST, str]]:
    required = set(_PACKAGE_WIDE_OPERATIONS)
    required |= set(_FOLDER_OPERATION_ARGS)
    if short in {'publish', 'voice_refs'}:
        required |= _MUST_BE_CLAIMED
    _, unit_inventory = _inventory_for_function(
        _UNIT_OPERATIONS, short, func.name
    )
    required |= set(unit_inventory or {})
    seed_aliases = dict(aliases or {})
    parents = _parent_map(func)
    found = []
    for node in _walk_own_scope(func):
        if _is_operation_alias_source(node, parents):
            continue
        name = _operation_name(node)
        aliases_at_node = _operation_aliases(
            func.body, seed_aliases, before_line=getattr(node, 'lineno', None)
        )
        reference = node.value if isinstance(node, ast.NamedExpr) else node
        qualified = _storage_key(reference) if isinstance(reference, ast.Attribute) else None
        canonical_values = aliases_at_node.get(
            qualified,
            aliases_at_node.get(name, frozenset({name}) if name else frozenset()),
        )
        canonicals = (
            {canonical_values}
            if isinstance(canonical_values, str)
            else set(canonical_values)
        )
        for canonical in canonicals:
            keyword_name = _PACKAGE_OPERATION_FOLDER_KEYWORDS.get(canonical)
            parent_call = parents.get(id(node))
            if (
                keyword_name is not None
                and isinstance(parent_call, ast.Call)
                and parent_call.func is node
            ):
                unpacked = _unpacked_keyword_expressions(
                    parent_call, keyword_name, func
                )
                if unpacked:
                    node._unpacked_folder_expressions = unpacked
            if canonical in _CONTENT_PATH_MUTATION_METHODS:
                receivers = _known_bound_mutation_receivers(
                    func, node.lineno
                ).get(_storage_key(reference) or '', set())
                if receivers:
                    node._bound_mutation_receivers = receivers
                    found.append((node, canonical))
                    continue
            if canonical in _OS_CONTENT_PATH_MUTATION_ARGS and not (
                qualified == f'os.{canonical}'
                or qualified in aliases_at_node
                or isinstance(reference, ast.Name)
                and name in aliases_at_node
            ):
                continue
            if (
                canonical in _FOLDER_OPERATION_ARGS
                and short not in {'publish', 'voice_refs'}
                and not any(
                    _looks_like_content_folder(origin)
                    for origin in _operation_folder_origins(
                        node, canonical, parents, func
                    )
                )
            ):
                continue
            if canonical in required:
                found.append((node, canonical))
    found.extend(_content_path_mutation_nodes(func))
    found.extend(_open_write_nodes(func))
    return found


def _path_expression_key(node) -> str | None:
    while (
        isinstance(node, ast.Call)
        and _tail_name(node) in {'Path', 'str'}
        and len(node.args) == 1
        and not node.keywords
    ):
        node = node.args[0]
    if any(isinstance(child, ast.Call) for child in ast.walk(node)):
        return None
    return ast.dump(node, include_attributes=False)


def _path_expression_names(node) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _assigned_names(node) -> set[str]:
    targets = []
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [
            item.optional_vars for item in node.items if item.optional_vars is not None
        ]
    elif isinstance(node, ast.comprehension):
        targets = [node.target]
    elif isinstance(node, ast.NamedExpr):
        targets = [node.target]
    return {
        child.id
        for target in targets
        for child in ast.walk(target)
        if isinstance(child, ast.Name)
    }


def _walk_rebinding_scope(root):
    """Walk executed bindings without importing nested local scopes."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _known_keyword_mappings(func, before_line: int):
    """Possible string-keyed mapping entries held by local variables."""
    def mapping(value, state):
        if isinstance(value, ast.Name):
            return {
                key: set(expressions)
                for key, expressions in state.get(value.id, {}).items()
            }
        entries = {}
        if isinstance(value, ast.Dict):
            pairs = zip(value.keys, value.values)
        elif isinstance(value, ast.Call) and _tail_name(value) == 'dict':
            pairs = (
                (ast.Constant(keyword.arg), keyword.value)
                for keyword in value.keywords
                if keyword.arg is not None
            )
        else:
            return entries
        for key, expression in pairs:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                entries.setdefault(key.value, set()).add(expression)
        return entries

    def merge(*states):
        names = set().union(*(set(state) for state in states))
        return {
            name: {
                key: set().union(*(
                    state.get(name, {}).get(key, set()) for state in states
                ))
                for key in set().union(*(
                    set(state.get(name, {})) for state in states
                ))
            }
            for name in names
        }

    def after(statements, state):
        state = {
            name: {key: set(values) for key, values in entries.items()}
            for name, entries in state.items()
        }
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                entries = mapping(statement.value, state)
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if entries:
                        state[target.id] = {
                            key: set(values) for key, values in entries.items()
                        }
                    else:
                        state.pop(target.id, None)
            elif isinstance(statement, ast.If):
                state = merge(
                    after(statement.body, state), after(statement.orelse, state)
                )
            elif isinstance(statement, ast.Match):
                paths = [after(case.body, state) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(state)
                state = merge(*paths)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                state = merge(state, after(statement.body, state))
                state = after(statement.orelse, state)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                state = after(statement.body, state)
            elif isinstance(statement, ast.Try):
                prefix_states = [state]
                body_state = state
                for nested in statement.body:
                    body_state = after([nested], body_state)
                    prefix_states.append(body_state)
                paths = [after(statement.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in statement.handlers
                    for prefix_state in prefix_states
                )
                state = after(statement.finalbody, merge(*paths))
        return state

    return after(func.body, {})


def _unpacked_keyword_expressions(call, keyword_name: str, func) -> set[ast.AST]:
    mappings = _known_keyword_mappings(func, call.lineno)
    expressions = set()
    for keyword in call.keywords:
        if keyword.arg is not None:
            continue
        value = keyword.value
        if isinstance(value, ast.Dict):
            for key, expression in zip(value.keys, value.values):
                if isinstance(key, ast.Constant) and key.value == keyword_name:
                    expressions.add(expression)
        elif isinstance(value, ast.Name):
            expressions.update(mappings.get(value.id, {}).get(keyword_name, set()))
    return expressions


def _operation_folder_expressions(
    operation, name: str, parents: dict[int, ast.AST]
) -> list:
    parent = parents.get(id(operation))
    reference = operation.value if isinstance(operation, ast.NamedExpr) else operation
    if isinstance(parent, ast.Call) and _tail_name(parent) in _DEFERRAL_CALLS:
        callable_index = 1 if _tail_name(parent) == 'run_in_executor' else 0
        if (
            len(parent.args) > callable_index
            and parent.args[callable_index] is operation
        ):
            return list(parent.args[callable_index + 1:])
    bound_receivers = getattr(operation, '_bound_mutation_receivers', set())
    if name in _CONTENT_PATH_MUTATION_METHODS and bound_receivers:
        return list(bound_receivers)
    unpacked = list(getattr(operation, '_unpacked_folder_expressions', set()))
    open_handle_path = getattr(operation, '_open_handle_path', None)
    if name == _OPEN_WRITE_OPERATION and open_handle_path is not None:
        return [open_handle_path]
    if (
        name == _OPEN_WRITE_OPERATION
        and isinstance(parent, ast.Call)
        and parent.func is operation
    ):
        if (
            isinstance(operation, ast.Attribute)
            and not (
                isinstance(operation.value, ast.Name)
                and operation.value.id == 'os'
            )
        ):
            return [operation.value]
        folder = parent.args[0] if parent.args else next((
            item.value
            for item in parent.keywords
            if item.arg in {'file', 'path'}
        ), None)
        return [folder] if folder is not None else []
    if (
        name in _COPY_CONTENT_PATH_ARGS
        and isinstance(parent, ast.Call)
        and parent.func is operation
    ):
        folders = []
        for index, keyword_name in _COPY_CONTENT_PATH_ARGS[name]:
            if len(parent.args) > index:
                folders.append(parent.args[index])
                continue
            keyword = next(
                (item for item in parent.keywords if item.arg == keyword_name),
                None,
            )
            if keyword is not None:
                folders.append(keyword.value)
        return folders
    is_os_style = isinstance(reference, ast.Name) or (
        isinstance(reference, ast.Attribute)
        and isinstance(reference.value, ast.Name)
    )
    if (
        name in _OS_CONTENT_PATH_MUTATION_ARGS
        and is_os_style
        and isinstance(parent, ast.Call)
        and parent.func is operation
    ):
        folders = []
        for index, keyword_name in _OS_CONTENT_PATH_MUTATION_ARGS[name]:
            if len(parent.args) > index:
                folders.append(parent.args[index])
                continue
            keyword = next(
                (item for item in parent.keywords if item.arg == keyword_name),
                None,
            )
            if keyword is not None:
                folders.append(keyword.value)
        return folders
    if name in _CONTENT_PATH_MUTATION_METHODS and isinstance(
        operation, ast.Attribute
    ):
        folders = [operation.value]
        if (
            name in {'rename', 'replace'}
            and isinstance(parent, ast.Call)
            and parent.func is operation
        ):
            destination = parent.args[0] if parent.args else next(
                (item.value for item in parent.keywords if item.arg == 'target'), None
            )
            if destination is not None:
                folders.append(destination)
        return folders
    index = _PACKAGE_OPERATION_FOLDER_ARGS.get(
        name, _FOLDER_OPERATION_ARGS.get(name)
    )
    if index is None or not isinstance(parent, ast.Call) or parent.func is not operation:
        return unpacked
    if len(parent.args) > index:
        return [parent.args[index], *unpacked]
    keyword_name = _PACKAGE_OPERATION_FOLDER_KEYWORDS.get(name)
    if keyword_name is None:
        return unpacked
    keyword = next(
        (item for item in parent.keywords if item.arg == keyword_name), None
    )
    return [keyword.value, *unpacked] if keyword else unpacked


def _operation_folder_expression(
    operation, name: str, parents: dict[int, ast.AST]
):
    folders = _operation_folder_expressions(operation, name, parents)
    return folders[0] if folders else None


def _operation_folder_key(
    operation, name: str, parents: dict[int, ast.AST]
) -> str | None:
    folder = _operation_folder_expression(operation, name, parents)
    return _path_expression_key(folder) if folder is not None else None


def _operation_folder_origins(
    operation, name: str, parents: dict[int, ast.AST], func
) -> set[str]:
    return set().union(*(
        _expression_path_origins(folder, func, operation.lineno)
        for folder in _operation_folder_expressions(operation, name, parents)
    ))


def _operation_folder_origin_sets(
    operation, name: str, parents: dict[int, ast.AST], func
) -> list[set[str]]:
    return [
        _expression_path_origins(folder, func, operation.lineno)
        for folder in _operation_folder_expressions(operation, name, parents)
    ]


def _operation_folder_argument_origin_sets(
    operation, name: str, parents: dict[int, ast.AST], func
) -> list[set[str]]:
    return [
        _expression_argument_origins(folder, func, operation.lineno)
        for folder in _operation_folder_expressions(operation, name, parents)
    ]


def _literal_path_escapes(value: str) -> bool:
    return (
        '..' in value.replace('\\', '/').split('/')
        or value.startswith(('/', '\\'))
        or (
            len(value) >= 3
            and value[1] == ':'
            and value[2] in {'/', '\\'}
        )
    )


def _known_string_constants(func, before_line: int) -> dict[str, set[str]]:
    def merge(*states):
        return {
            name: set().union(*(state.get(name, set()) for state in states))
            for name in set().union(*(set(state) for state in states))
        }

    def after(statements, state):
        state = {name: set(values) for name, values in state.items()}
        for statement in statements:
            if statement.lineno >= before_line:
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                values = (
                    {value.value}
                    if isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    else set()
                )
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    bound_names = {
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name)
                        and isinstance(child.ctx, ast.Store)
                    }
                    for bound in bound_names:
                        if values:
                            state[bound] = values
                        else:
                            state.pop(bound, None)
            elif isinstance(statement, ast.If):
                state = merge(
                    after(statement.body, state), after(statement.orelse, state)
                )
            elif isinstance(statement, ast.Match):
                paths = [after(case.body, state) for case in statement.cases]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(state)
                state = merge(*paths)
            elif isinstance(statement, ast.With):
                state = after(statement.body, state)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                state = merge(state, after(statement.body, state))
                state = after(statement.orelse, state)
            elif isinstance(statement, ast.Try):
                prefix_states = [state]
                body_state = state
                for nested in statement.body:
                    body_state = after([nested], body_state)
                    prefix_states.append(body_state)
                paths = [after(statement.orelse, body_state)]
                paths.extend(
                    after(handler.body, prefix_state)
                    for handler in statement.handlers
                    for prefix_state in prefix_states
                )
                state = after(statement.finalbody, merge(*paths))
        return state

    return after(func.body, {})


def _stays_within_claimed_tree(
    operation, name: str, parents: dict[int, ast.AST], func
) -> bool:
    folders = _operation_folder_expressions(operation, name, parents)
    if not folders:
        return False
    constants = _known_string_constants(func, operation.lineno)
    return not any(
        (
            isinstance(node, ast.Attribute)
            and node.attr in {'parent', 'parents'}
        )
        or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _literal_path_escapes(node.value)
        )
        or (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and any(_literal_path_escapes(value) for value in constants.get(node.id, ()))
        )
        for folder in folders
        for node in ast.walk(folder)
    )


def _static_path_shape(node) -> tuple[str, tuple[str, ...]] | None:
    """Best-effort root plus literal suffix for directional claim checks."""
    if isinstance(node, ast.Name):
        return node.id, ()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _static_path_shape(node.left)
        if base and isinstance(node.right, ast.Constant) and isinstance(
            node.right.value, str
        ):
            return base[0], base[1] + (node.right.value,)
        return None
    if isinstance(node, ast.Call) and _tail_name(node) in {'Path', 'str', 'join'}:
        if not node.args:
            return None
        base = _static_path_shape(node.args[0])
        if base is None:
            return None
        suffix = list(base[1])
        for component in node.args[1:]:
            if not isinstance(component, ast.Constant) or not isinstance(
                component.value, str
            ):
                return None
            suffix.append(component.value)
        return base[0], tuple(suffix)
    return None


def _paths_fit_claim(
    operation_folders: list[ast.AST], claim_shapes: set[tuple[str, tuple[str, ...]]]
) -> bool:
    operation_shapes = {
        shape
        for folder in operation_folders
        if (shape := _static_path_shape(folder)) is not None
        and _looks_like_content_folder(shape[0])
    }
    comparable_claims = {
        shape for shape in claim_shapes if _looks_like_content_folder(shape[0])
    }
    if not operation_shapes or not comparable_claims:
        return True
    return all(
        any(
            claim_root == operation_root
            and operation_suffix[:len(claim_suffix)] == claim_suffix
            for claim_root, claim_suffix in comparable_claims
        )
        for operation_root, operation_suffix in operation_shapes
    )


def _mutates_claim_root(
    name: str,
    operation_folders: list[ast.AST],
    claim_shapes: set[tuple[str, tuple[str, ...]]],
) -> bool:
    if name not in {'rmtree', 'rmdir', 'rename', 'replace', 'move'}:
        return False
    operation_shapes = {
        shape for folder in operation_folders
        if (shape := _static_path_shape(folder)) is not None
    }
    return bool(operation_shapes & claim_shapes)


def _unclaimed_folder_operations(
    func, short: str, aliases: dict[str, str] | None = None
) -> list:
    """Sentinel operations in ``func`` that no claim covers.

    Two ways to be uncovered, and the second one is why lexical containment
    alone is not enough: the reference sits outside every claiming ``with``,
    or it is handed to something that runs it later (see ``_DEFERRAL_CALLS``),
    in which case being inside the ``with`` says nothing about when the work
    actually touches the folder.
    """
    def claim_value(value):
        expressions = (
            [value.body, value.orelse]
            if isinstance(value, ast.IfExp)
            else [value]
        )
        branch_kinds = [
            {_resolved_claim_factory(branch, _claim_aliases(func, branch.lineno))}
            - {None}
            for branch in expressions
        ]
        branch_targets = [
            {key}
            if _resolved_claim_factory(
                branch, _claim_aliases(func, branch.lineno)
            )
            and (folder := _claim_folder_expression(branch)) is not None
            and (key := _path_expression_key(folder)) is not None
            else set()
            for branch in expressions
        ]
        branch_target_names = [
            _path_expression_names(folder)
            | _expression_path_origins(folder, func, branch.lineno)
            if _resolved_claim_factory(
                branch, _claim_aliases(func, branch.lineno)
            )
            and (folder := _claim_folder_expression(branch)) is not None
            else set()
            for branch in expressions
        ]
        branch_target_roots = [
            _expression_argument_origins(folder, func, branch.lineno)
            if _resolved_claim_factory(
                branch, _claim_aliases(func, branch.lineno)
            )
            and (folder := _claim_folder_expression(branch)) is not None
            else set()
            for branch in expressions
        ]
        branch_pairs = []
        branch_shapes = []
        for branch in expressions:
            aliases = _claim_aliases(func, branch.lineno)
            kind = _resolved_claim_factory(branch, aliases)
            folder = _claim_folder_expression(branch) if kind else None
            key = _path_expression_key(folder) if folder is not None else None
            branch_pairs.append({(kind, key)} if kind and key is not None else set())
            shape = _static_path_shape(folder) if folder is not None else None
            branch_shapes.append({shape} if kind and shape is not None else set())
        kinds = set.intersection(*branch_kinds) if branch_kinds else set()
        folder_targets = (
            set.intersection(*branch_targets) if branch_targets else set()
        )
        target_names = (
            set.intersection(*branch_target_names)
            if branch_target_names else set()
        )
        target_roots = (
            set.union(*branch_target_roots) if branch_target_roots else set()
        )
        pairs = set.intersection(*branch_pairs) if branch_pairs else set()
        shapes = set().union(*branch_shapes) if branch_shapes else set()
        if branch_kinds and all(branch_kinds):
            kinds.add('<all-branches-claimed>')
        return (
            kinds, folder_targets, target_names, target_roots, pairs, shapes
        ) if kinds else None

    def merge_claim_states(left, right):
        return {
            name: value
            for name, value in left.items()
            if name in right and right[name] == value
        }

    claim_states_by_with = {}

    def claim_state_after(statements, state):
        state = dict(state)
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = claim_value(statement.value)
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if value:
                        state[target.id] = value
                    else:
                        state.pop(target.id, None)
            elif isinstance(statement, ast.If):
                body_state = claim_state_after(statement.body, state)
                else_state = claim_state_after(statement.orelse, state)
                state = merge_claim_states(body_state, else_state)
            elif isinstance(statement, ast.Match):
                paths = [
                    claim_state_after(case.body, state)
                    for case in statement.cases
                ]
                exhaustive = any(
                    case.guard is None
                    and isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.pattern is None
                    for case in statement.cases
                )
                if not exhaustive:
                    paths.append(state)
                merged = paths[0]
                for path in paths[1:]:
                    merged = merge_claim_states(merged, path)
                state = merged
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                state = merge_claim_states(
                    state, claim_state_after(statement.body, state)
                )
                state = claim_state_after(statement.orelse, state)
            elif isinstance(statement, ast.Try):
                prefix_states = [state]
                body_state = state
                for nested in statement.body:
                    body_state = claim_state_after([nested], body_state)
                    prefix_states.append(body_state)
                paths = [claim_state_after(statement.orelse, body_state)]
                paths.extend(
                    claim_state_after(handler.body, prefix_state)
                    for handler in statement.handlers
                    for prefix_state in prefix_states
                )
                merged = paths[0]
                for path in paths[1:]:
                    merged = merge_claim_states(merged, path)
                state = claim_state_after(statement.finalbody, merged)
            elif isinstance(statement, ast.With):
                claim_states_by_with[id(statement)] = dict(state)
                state = claim_state_after(statement.body, state)
        return state

    claim_state_after(func.body, {})

    parents = _parent_map(func)

    def rebinding_can_reach(rebinding, operation) -> bool:
        current = rebinding
        while id(current) in parents:
            parent = parents[id(current)]
            if isinstance(parent, ast.If):
                rebinding_branch = next((
                    index
                    for index, branch in enumerate((parent.body, parent.orelse))
                    if any(_contains_node(statement, rebinding) for statement in branch)
                ), None)
                operation_branch = next((
                    index
                    for index, branch in enumerate((parent.body, parent.orelse))
                    if any(_contains_node(statement, operation) for statement in branch)
                ), None)
                if (
                    rebinding_branch is not None
                    and operation_branch is not None
                    and rebinding_branch != operation_branch
                ):
                    return False
            elif isinstance(parent, ast.Match):
                rebinding_case = next((
                    index
                    for index, case in enumerate(parent.cases)
                    if any(_contains_node(statement, rebinding) for statement in case.body)
                ), None)
                operation_case = next((
                    index
                    for index, case in enumerate(parent.cases)
                    if any(_contains_node(statement, operation) for statement in case.body)
                ), None)
                if (
                    rebinding_case is not None
                    and operation_case is not None
                    and rebinding_case != operation_case
                ):
                    return False
            current = parent
        return True

    claimed_scopes = []
    for block in _walk_own_scope(func):
        if not isinstance(block, ast.With):
            continue
        claim_kinds = set()
        claim_targets = set()
        claim_target_names = set()
        claim_target_roots = set()
        claim_pairs = set()
        claim_shapes = set()
        claim_contexts = claim_states_by_with.get(id(block), {})
        for item in block.items:
            context = item.context_expr
            claim_kind = _resolved_claim_factory(
                context, _claim_aliases(func, context.lineno)
            )
            if claim_kind:
                claim_kinds.add(claim_kind)
                folder = _claim_folder_expression(context)
                if folder is not None:
                    key = _path_expression_key(folder)
                    if key is not None:
                        claim_targets.add(key)
                        claim_pairs.add((claim_kind, key))
                    shape = _static_path_shape(folder)
                    if shape is not None:
                        claim_shapes.add(shape)
                    claim_target_names.update(
                        _path_expression_names(folder)
                        | _expression_path_origins(folder, func, context.lineno)
                    )
                    claim_target_roots.update(
                        _expression_argument_origins(
                            folder, func, context.lineno
                        )
                    )
            elif isinstance(context, ast.Name):
                (
                    alias_kinds,
                    alias_targets,
                    alias_target_names,
                    alias_target_roots,
                    alias_pairs,
                    alias_shapes,
                ) = claim_contexts.get(
                    context.id, (set(), set(), set(), set(), set(), set())
                )
                claim_kinds.update(alias_kinds)
                claim_targets.update(alias_targets)
                claim_target_names.update(alias_target_names)
                claim_target_roots.update(alias_target_roots)
                claim_pairs.update(alias_pairs)
                claim_shapes.update(alias_shapes)
        if not claim_kinds:
            continue
        claimed_scopes.append((
            claim_kinds,
            claim_targets,
            claim_target_names,
            claim_target_roots,
            claim_pairs,
            claim_shapes,
            [
                (
                    inner,
                    _assigned_names(inner),
                )
                for statement in block.body
                for inner in _walk_rebinding_scope(statement)
                if _assigned_names(inner)
            ],
            {
                id(inner)
                for statement in block.body
                for inner in ast.walk(statement)
            },
        ))

    deferred_nodes = set()
    for call in _walk_own_scope(func):
        if not isinstance(call, ast.Call):
            continue
        tail = _tail_name(call)
        if tail not in _DEFERRAL_CALLS:
            continue
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            deferred_nodes.update(id(node) for node in ast.walk(arg))
    for node in _walk_own_scope(func):
        if isinstance(node, ast.Lambda):
            parent = parents.get(id(node))
            if isinstance(parent, ast.Call) and parent.func is node:
                continue
            deferred_nodes.update(id(child) for child in ast.walk(node.body))
        elif isinstance(node, ast.GeneratorExp):
            deferred_nodes.update(id(child) for child in ast.walk(node.elt))
            for index, comprehension in enumerate(node.generators):
                if index:
                    deferred_nodes.update(
                        id(child) for child in ast.walk(comprehension.iter)
                    )
                for condition in comprehension.ifs:
                    deferred_nodes.update(id(child) for child in ast.walk(condition))

    operations = _operation_nodes(func, short, aliases)
    for operation, _ in operations:
        current = operation
        while id(current) in parents:
            parent = parents[id(current)]
            if isinstance(parent, ast.Call):
                if parent.func is current:
                    break
                deferred_nodes.add(id(operation))
                break
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                deferred_nodes.add(id(operation))
                break
            if isinstance(parent, (ast.Return, ast.Yield, ast.YieldFrom)):
                deferred_nodes.add(id(operation))
                break
            current = parent
            if current is func:
                break

    unit_key, unit_inventory = _inventory_for_function(
        _UNIT_OPERATIONS, short, func.name
    )
    offenders = []
    for child, name in operations:
        if id(child) in deferred_nodes:
            offenders.append((short, func.name, child.lineno, name, '交给别处延后跑'))
            continue
        folder_key = _operation_folder_key(child, name, parents)
        folder_origins = _operation_folder_origins(child, name, parents, func)
        folder_origin_sets = _operation_folder_origin_sets(
            child, name, parents, func
        )
        folder_argument_origin_sets = _operation_folder_argument_origin_sets(
            child, name, parents, func
        )
        operation_folders = _operation_folder_expressions(child, name, parents)
        content_folder_argument_origin_sets = [
            origins
            for origins in folder_argument_origin_sets
            if any(_looks_like_content_folder(origin) for origin in origins)
        ]
        content_folder_origin_sets = [
            origins
            for origins, argument_origins in zip(
                folder_origin_sets, folder_argument_origin_sets
            )
            if any(
                _looks_like_content_folder(origin)
                for origin in argument_origins
            )
        ]
        stays_within_tree = _stays_within_claimed_tree(
            child, name, parents, func
        )
        folder_rule_kinds = _FOLDER_OPERATION_CLAIMS.get(name)
        if (
            name in _CONTENT_PATH_MUTATION_METHODS
            and not any(_looks_like_content_folder(item) for item in folder_origins)
        ):
            folder_rule_kinds = None
        required_kinds = _PACKAGE_OPERATION_CLAIMS.get(name, folder_rule_kinds)
        protected = any(
            id(child) in scope
            and (
                not required_kinds
                or (
                    unit_inventory
                    and name in unit_inventory
                    and _UNIT_CLAIMS[unit_key] in kinds
                )
                or (
                    name in _PACKAGE_OPERATION_CLAIMS
                    and any(
                        kind in required_kinds and target == folder_key
                        for kind, target in pairs
                    )
                )
                or (
                    folder_rule_kinds is not None
                    and stays_within_tree
                    and bool(content_folder_origin_sets)
                    and all(
                        origins & target_names
                        for origins in content_folder_origin_sets
                    )
                    and len(target_roots) == 1
                    and bool(content_folder_argument_origin_sets)
                    and all(
                        origins == target_roots
                        for origins in content_folder_argument_origin_sets
                    )
                    and bool(required_kinds & kinds)
                    and _paths_fit_claim(operation_folders, claim_shapes)
                    and (
                        'claim_content_folder' in kinds
                        or name not in {'rmtree', 'rmdir', 'rename', 'replace', 'move'}
                        or (
                            bool(claim_shapes)
                            and not _mutates_claim_root(
                                name, operation_folders, claim_shapes
                            )
                        )
                    )
                )
            )
            and not any(
                getattr(rebinding, 'lineno', -1) <= child.lineno
                and names & target_names
                and rebinding_can_reach(rebinding, child)
                for rebinding, names in rebindings
            )
            for (
                kinds,
                targets,
                target_names,
                target_roots,
                pairs,
                claim_shapes,
                rebindings,
                scope,
            ) in claimed_scopes
        )
        if not protected:
            offenders.append((short, func.name, child.lineno, name, '未占用'))

    if unit_inventory:
        actual = Counter(name for _, name in operations if name in unit_inventory)
        expected = Counter(unit_inventory)
        if actual != expected:
            offenders.append((
                short,
                func.name,
                func.lineno,
                'operation-inventory',
                f'操作清单漂移：expected={dict(expected)}, actual={dict(actual)}',
            ))
        unit_ids = {id(node) for node, name in operations if name in unit_inventory}
        required_claim = _UNIT_CLAIMS[unit_key]
        unit_folder_names = {
            arg.arg
            for arg in (
                list(func.args.posonlyargs)
                + list(func.args.args)
                + list(func.args.kwonlyargs)
            )
            if _looks_like_content_folder(arg.arg)
        }
        if not unit_folder_names:
            unit_folder_names = set().union(*(
                _operation_folder_origins(node, name, parents, func)
                for node, name in operations
                if name in unit_inventory
            ))
        if unit_ids and not any(
            required_claim in kinds
            and unit_ids <= scope
            and (not unit_folder_names or bool(unit_folder_names & target_names))
            for kinds, _, target_names, _, _, _, _, scope in claimed_scopes
        ):
            offenders.append((
                short,
                func.name,
                func.lineno,
                'continuous-claim',
                f'没有一把 {required_claim} 连续占用覆盖整个单元',
            ))
    return offenders


def _format_offender(offender) -> str:
    short, func, line, name, reason = offender
    return f'{short}.{func}:{line} -> {name}（{reason}）'


def _consume_known_gaps(offenders: list) -> list:
    return [
        item for item in offenders
        if (item[0], item[1], item[2], item[3]) not in _KNOWN_GAPS
    ]


def test_the_claim_guard_sees_through_deferred_work():
    """Handing the operation to a worker does not put it under the claim.

    ``with claim: executor.submit(_publish_workshop_item, ...)`` satisfies
    lexical containment while the upload runs after the claim is released --
    the very race the guard exists to catch. Pinned on synthetic source so the
    rule holds even when no production code currently has this shape.
    """
    tree = ast.parse(
        'def deferred():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        executor.submit(_publish_workshop_item, folder)\n'
        '\n'
        'def deferred_generic(content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        '        executor.submit(shutil.rmtree, content_folder)\n'
        '\n'
        'def deferred_lambda():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        executor.submit(lambda: _publish_workshop_item(a, b, c, folder))\n'
        '\n'
        'def stored_lambda():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        callback = lambda: _publish_workshop_item(a, b, c, folder)\n'
        '    callback()\n'
        '\n'
        'def stored_reference():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        callback = _publish_workshop_item\n'
        '    callback(folder)\n'
        '\n'
        'def thread_target():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        threading.Thread(target=_publish_workshop_item, args=(folder,)).start()\n'
        '\n'
        'def mapped():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        executor.map(_publish_workshop_item, folders)\n'
        '\n'
        'def generated():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        return (_publish_workshop_item(a, b, c, folder) for _ in items)\n'
        '\n'
        'def returned_reference():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        return _publish_workshop_item\n'
        '\n'
        'def appended_reference():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        callbacks.append(_publish_workshop_item)\n'
        '\n'
        'def direct():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
        '\n'
        'def immediate_lambda():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        (lambda: _publish_workshop_item(a, b, c, folder))()\n'
    )
    functions = {node.name: node for node in tree.body}

    assert _unclaimed_folder_operations(functions['deferred'], 'publish'), (
        '推迟执行的上传必须被报出来——占用早就放开了'
    )
    assert _unclaimed_folder_operations(
        functions['deferred_generic'], 'new_router'
    ), 'generic folder operation 作为 deferred callback 时也必须保留 folder 参数'
    assert _unclaimed_folder_operations(functions['deferred_lambda'], 'publish'), (
        '藏在 deferred lambda 里的上传也必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['stored_lambda'], 'publish'), (
        '存在 claim 里、离开后才调用的 lambda body 也必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['stored_reference'], 'publish'), (
        '存在 claim 里的普通 callable 引用也可能逃逸，必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['thread_target'], 'publish'), (
        'Thread target 会在别的线程延后运行，必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['mapped'], 'publish'), (
        'executor.map 的 callable 会延后运行，必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['generated'], 'publish'), (
        'generator expression 的 body 会在迭代时才运行，必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['returned_reference'], 'publish'), (
        '返回受保护 callable 会让它逃逸 claim，必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['appended_reference'], 'publish'), (
        '把受保护 callable 传给未知调用会让它逃逸 claim，必须被报出来'
    )
    assert _unclaimed_folder_operations(functions['direct'], 'publish') == [], (
        '直接在占用里同步跑完是合法的，守卫不该报它'
    )
    assert _unclaimed_folder_operations(
        functions['immediate_lambda'], 'publish'
    ) == [], 'immediately invoked lambda 在 claim 退出前同步完成，不是 deferred work'


def test_the_claim_guard_resolves_operation_aliases():
    tree = ast.parse(
        'def imported_alias():\n'
        '    from shutil import rmtree as delete_tree\n'
        '    delete_tree(folder)\n'
        '\n'
        'def assigned_alias():\n'
        '    delete_tree = shutil.rmtree\n'
        '    delete_tree(folder)\n'
    )
    functions = {node.name: node for node in tree.body}

    for name in ('imported_alias', 'assigned_alias'):
        offenders = _unclaimed_folder_operations(functions[name], 'publish')
        assert any(item[3] == 'rmtree' for item in offenders), (
            f'{name} 必须把 alias 还原成受保护的 rmtree'
        )

    partial_tree = ast.parse(
        'publish_later = functools.partial(\n'
        '    _publish_workshop_item, a, b, c, folder\n'
        ')\n'
        'def partial_alias():\n'
        '    publish_later()\n'
        'def returned_partial_alias():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        return publish_later\n'
        'def yielded_partial_alias():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        yield publish_later\n'
    )
    partial_aliases = _operation_aliases(partial_tree.body)
    partial_functions = {
        node.name: node
        for node in partial_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    assert _unclaimed_folder_operations(
        partial_functions['partial_alias'], 'publish', partial_aliases
    ), 'module-level partial 也必须继承 protected operation 身份'
    for name in ('returned_partial_alias', 'yielded_partial_alias'):
        assert _unclaimed_folder_operations(
            partial_functions[name], 'publish', partial_aliases
        ), f'{name} 中逃逸 claim 的 module-level partial alias 必须被报出来'
    reassigned = ast.parse(
        'def publish():\n'
        '    callback = _publish_workshop_item\n'
        '    callback = harmless\n'
        '    callback()\n'
    ).body[0]
    assert _unclaimed_folder_operations(reassigned, 'publish') == [], (
        'protected operation alias 被普通 callable 重赋值后必须失效'
    )
    loop_reassigned = ast.parse(
        'def publish(items):\n'
        '    callback = _publish_workshop_item\n'
        '    for item in items:\n'
        '        callback = harmless\n'
        '    callback(a, b, c, folder)\n'
    ).body[0]
    assert _unclaimed_folder_operations(loop_reassigned, 'publish'), (
        'zero-iteration loop 必须保留进入 loop 前的 protected alias 路径'
    )
    exceptional_prefix = ast.parse(
        'def publish():\n'
        '    callback = harmless\n'
        '    try:\n'
        '        callback = _publish_workshop_item\n'
        '        may_raise()\n'
        '    except Exception:\n'
        '        pass\n'
        '    callback(a, b, c, folder)\n'
    ).body[0]
    assert _unclaimed_folder_operations(exceptional_prefix, 'publish'), (
        'try body 的任一异常前缀都必须保留可能的 protected operation alias'
    )
    exceptional_prefix_restored = ast.parse(
        'def publish():\n'
        '    callback = harmless\n'
        '    try:\n'
        '        callback = _publish_workshop_item\n'
        '        may_raise()\n'
        '        callback = harmless\n'
        '    except Exception:\n'
        '        pass\n'
        '    callback(a, b, c, folder)\n'
    ).body[0]
    assert _unclaimed_folder_operations(exceptional_prefix_restored, 'publish'), (
        'try body 成功路径恢复 alias 后，异常前缀仍必须保留 protected identity'
    )
    walrus_alias = ast.parse(
        'def delete(content_folder):\n'
        '    (op := shutil.rmtree)(content_folder)\n'
    ).body[0]
    assert _unclaimed_folder_operations(walrus_alias, 'publish'), (
        'walrus 绑定并立即调用的 protected operation alias 必须被发现'
    )
    attribute_alias = ast.parse(
        'def delete(self, content_folder):\n'
        '    self.op = shutil.rmtree\n'
        '    self.op(content_folder)\n'
    ).body[0]
    assert _unclaimed_folder_operations(attribute_alias, 'publish'), (
        'attribute storage 上的 protected operation alias 必须被发现'
    )
    bound_mutation = ast.parse(
        'def write(content_folder):\n'
        "    writer = Path(content_folder, 'preview.png').write_bytes\n"
        '    writer(data)\n'
    ).body[0]
    assert _unclaimed_folder_operations(bound_mutation, 'publish'), (
        'bound pathlib mutator alias 必须保留 receiver 的 content-folder origin'
    )


def test_the_claim_guard_discovers_content_path_mutations():
    tree = ast.parse(
        'def direct(content_folder):\n'
        "    Path(content_folder, 'preview.png').write_bytes(data)\n"
        '\n'
        'def assigned(content_folder):\n'
        "    preview = Path(content_folder) / 'preview.png'\n"
        '    preview.write_text(text)\n'
        '\n'
        'def claimed(content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        "        Path(content_folder, 'preview.png').write_bytes(data)\n"
        '\n'
        'def unrelated(metadata_path):\n'
        '    metadata_path.write_bytes(data)\n'
        '\n'
        'def move_between_folders(content_folder, other_content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        "        Path(content_folder, 'x').replace(\n"
        "            Path(other_content_folder, 'x')\n"
        '        )\n'
        '\n'
        'def parent_traversal(content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        "        shutil.rmtree(Path(content_folder, '..', 'sibling'))\n"
        '\n'
        'def keyword_rmtree(content_folder):\n'
        '    shutil.rmtree(path=content_folder)\n'
        '\n'
        'def unpacked_keyword_rmtree(content_folder):\n'
        "    shutil.rmtree(**{'path': content_folder})\n"
        '\n'
        'def stored_unpacked_keyword_rmtree(content_folder):\n'
        "    options = {'path': content_folder}\n"
        '    shutil.rmtree(**options)\n'
        '\n'
        'def keyword_copy2(content_folder):\n'
        '    shutil.copy2(src=source, dst=content_folder)\n'
        '\n'
        'def keyword_copy(content_folder):\n'
        '    shutil.copy(src=source, dst=content_folder)\n'
        '\n'
        'def keyword_copyfile(content_folder):\n'
        '    shutil.copyfile(src=source, dst=content_folder)\n'
        '\n'
        'def keyword_copytree(content_folder):\n'
        '    shutil.copytree(src=source, dst=content_folder)\n'
        '\n'
        'def crossed_conditional_targets(folder, other_folder, flag):\n'
        '    claimed = folder if flag else other_folder\n'
        '    target = other_folder if flag else folder\n'
        '    with claim_content_folder(claimed, purpose=p):\n'
        '        shutil.rmtree(target)\n'
        '\n'
        'def destructured_target(content_folder):\n'
        '    target, = (content_folder,)\n'
        '    shutil.rmtree(target)\n'
        '\n'
        'def match_target(content_folder, value):\n'
        '    match value:\n'
        '        case _:\n'
        '            target = content_folder\n'
        '    shutil.rmtree(target)\n'
        '\n'
        'def while_target(content_folder, other, flag):\n'
        '    target = other\n'
        '    while flag:\n'
        '        target = content_folder\n'
        '        break\n'
        '    shutil.rmtree(target)\n'
        '\n'
        'def walrus_target(content_folder):\n'
        '    if target := content_folder:\n'
        '        shutil.rmtree(target)\n'
        '\n'
        'def while_walrus_target(content_folder):\n'
        '    while target := content_folder:\n'
        '        break\n'
        '    shutil.rmtree(target)\n'
        '\n'
        'def exceptional_prefix_target(content_folder, other):\n'
        '    target = other\n'
        '    try:\n'
        '        target = content_folder\n'
        '        may_raise()\n'
        '        target = other\n'
        '    except Exception:\n'
        '        pass\n'
        '    shutil.rmtree(target)\n'
        '\n'
        'def os_mutations(content_folder, other_content_folder):\n'
        "    os.mkdir(os.path.join(content_folder, 'generated'))\n"
        "    os.makedirs(os.path.join(content_folder, 'nested'))\n"
        "    os.remove(os.path.join(content_folder, 'manifest.json'))\n"
        "    os.unlink(os.path.join(content_folder, 'voice.wav'))\n"
        '    os.rmdir(content_folder)\n'
        '    os.rename(content_folder, other_content_folder)\n'
        '    os.replace(content_folder, other_content_folder)\n'
        "    os.truncate(os.path.join(content_folder, 'preview.png'), 0)\n"
        '\n'
        'def aliased_os_mutations(content_folder):\n'
        '    remove_dir = os.rmdir\n'
        '    remove_file = os.unlink\n'
        '    remove_dir(content_folder)\n'
        "    remove_file(os.path.join(content_folder, 'voice.wav'))\n"
        '\n'
        'def aliased_os_module(content_folder):\n'
        '    import os as filesystem\n'
        '    filesystem.rmdir(content_folder)\n'
        '\n'
        'def conditional_operation_alias(content_folder, flag):\n'
        '    op = harmless\n'
        '    if flag:\n'
        '        op = os.rmdir\n'
        '    op(content_folder)\n'
        '\n'
        'def copy_source(content_folder, dst):\n'
        '    shutil.copytree(content_folder, dst)\n'
        '\n'
        'def claimed_copy_source(content_folder, dst):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        '        shutil.copytree(content_folder, dst)\n'
        '\n'
        'def move_source(content_folder, dst):\n'
        '    shutil.move(content_folder, dst)\n'
        '\n'
        'def claimed_move_source(content_folder, dst):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        '        shutil.move(content_folder, dst)\n'
        '\n'
        'def open_writes(content_folder):\n'
        "    open(os.path.join(content_folder, 'a'), 'w')\n"
        "    Path(content_folder, 'b').open('a')\n"
        "    os.open(os.path.join(content_folder, 'c'), os.O_CREAT | os.O_WRONLY)\n"
        '\n'
        'def stored_open_mode(content_folder):\n'
        "    mode = 'wb'\n"
        "    open(os.path.join(content_folder, 'preview.png'), mode)\n"
        '\n'
        'def dynamic_open_mode(content_folder, mode):\n'
        "    open(Path(content_folder, 'preview.png'), mode).write(data)\n"
        '\n'
        'def aliased_builtin_open(content_folder):\n'
        '    writer = open\n'
        "    writer(os.path.join(content_folder, 'preview.png'), 'wb')\n"
        '\n'
        'def stored_os_open_flags(content_folder):\n'
        '    flags = os.O_TRUNC | os.O_WRONLY\n'
        '    os.open(content_folder, flags)\n'
        '\n'
        'def aliased_os_open(content_folder):\n'
        '    import os as filesystem\n'
        '    filesystem.open(content_folder, filesystem.O_WRONLY)\n'
        '\n'
        'def atomic_byte_write(content_folder):\n'
        "    atomic_write_bytes(Path(content_folder, 'preview.png'), data)\n"
        '\n'
        'def explicit_content_local(path):\n'
        '    content_folder = path\n'
        '    shutil.rmtree(content_folder)\n'
        '\n'
        'def standalone_walrus(content_folder):\n'
        '    observe(target := content_folder)\n'
        '    shutil.rmtree(target)\n'
        '\n'
        'def escaped_write_handle(content_folder):\n'
        "    path = Path(content_folder, 'preview.png')\n"
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        "        writer = open(path, 'wb')\n"
        '    writer.write(data)\n'
        '    writer.close()\n'
        '\n'
        'def read_only_opens(content_folder):\n'
        "    open(os.path.join(content_folder, 'a'), 'r')\n"
        "    Path(content_folder, 'b').open()\n"
        "    os.open(os.path.join(content_folder, 'c'), os.O_RDONLY)\n"
        '\n'
        'def claimed_open_writes(content_folder):\n'
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        "        open(os.path.join(content_folder, 'a'), 'w')\n"
        "        Path(content_folder, 'b').open('a')\n"
        '\n'
        'def unrelated_open_writes(config_path):\n'
        "    open(config_path, 'w')\n"
        "    config_path.open('a')\n"
        '    os.open(config_path, os.O_CREAT | os.O_WRONLY)\n'
        '\n'
        'def harmless_string_replace(content_folder):\n'
        "    return content_folder.replace('\\\\', '/')\n"
        '\n'
        'def partial_root_delete(content_folder):\n'
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        '        os.rmdir(content_folder)\n'
        '\n'
        'def dynamic_partial_root_delete(content_folder):\n'
        '    with claim_partial_writer(os.path.abspath(content_folder), purpose=p):\n'
        '        os.rmdir(content_folder)\n'
        '\n'
        'def partial_child_delete(content_folder):\n'
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        "        os.rmdir(os.path.join(content_folder, 'child'))\n"
        '\n'
        'def absolute_escape(content_folder):\n'
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        "        Path(content_folder, '/tmp/out').write_text(data)\n"
        '\n'
        'def assigned_absolute_escape(content_folder):\n'
        "    segment = '/tmp/out'\n"
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        '        Path(content_folder, segment).write_text(data)\n'
        '\n'
        'def exceptional_absolute_escape(content_folder):\n'
        "    segment = 'safe'\n"
        '    try:\n'
        "        segment = '/tmp/out'\n"
        '        may_raise()\n'
        "        segment = 'safe'\n"
        '    except Exception:\n'
        '        pass\n'
        '    with claim_partial_writer(content_folder, purpose=p):\n'
        '        Path(content_folder, segment).write_text(data)\n'
        '\n'
        'def descendant_claim(content_folder):\n'
        "    with claim_content_folder(Path(content_folder) / 'child', purpose=p):\n"
        '        shutil.rmtree(content_folder)\n'
        '\n'
        'def sibling_claim(content_folder):\n'
        "    with claim_content_folder(Path(content_folder) / 'child', purpose=p):\n"
        "        shutil.rmtree(Path(content_folder) / 'sibling')\n"
        '\n'
        'def ancestor_claim(content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        "        shutil.rmtree(Path(content_folder) / 'child')\n"
    )
    functions = {node.name: node for node in tree.body}

    assert _unclaimed_folder_operations(functions['direct'], 'new_router')
    assert _unclaimed_folder_operations(functions['assigned'], 'new_router')
    assert _unclaimed_folder_operations(functions['claimed'], 'new_router') == []
    assert _unclaimed_folder_operations(functions['unrelated'], 'new_router') == []
    assert _unclaimed_folder_operations(
        functions['move_between_folders'], 'new_router'
    ), 'Path rename/replace 的 source 和 destination 都必须被 claim 覆盖'
    assert _unclaimed_folder_operations(
        functions['parent_traversal'], 'new_router'
    ), 'literal .. 逃出 claimed tree 时不能被当成受保护路径'
    for name in (
        'keyword_rmtree', 'keyword_copy', 'keyword_copy2', 'keyword_copyfile',
        'keyword_copytree', 'unpacked_keyword_rmtree',
        'stored_unpacked_keyword_rmtree',
    ):
        assert _unclaimed_folder_operations(functions[name], 'new_router'), (
            f'{name} 的 keyword target 也必须被 repository-wide guard 发现'
        )
    assert _unclaimed_folder_operations(
        functions['crossed_conditional_targets'], 'new_router'
    ), 'claim 和 mutation target 在每条 conditional path 上都相反时必须报出'
    assert _unclaimed_folder_operations(
        functions['destructured_target'], 'new_router'
    ), 'destructuring 绑定的 target 也必须继承 content-folder origin'
    assert _unclaimed_folder_operations(
        functions['match_target'], 'new_router'
    ), 'match case 中绑定的 target 也必须继承 content-folder origin'
    assert _unclaimed_folder_operations(
        functions['while_target'], 'new_router'
    ), 'while body 中绑定的 target 也必须保留 content-folder origin'
    assert _unclaimed_folder_operations(
        functions['walrus_target'], 'new_router'
    ), 'If.test 中 walrus 绑定的 target 必须传播 content-folder origin'
    assert _unclaimed_folder_operations(
        functions['while_walrus_target'], 'new_router'
    ), 'While.test 中 walrus 绑定的 target 必须传播 content-folder origin'
    assert _unclaimed_folder_operations(
        functions['exceptional_prefix_target'], 'new_router'
    ), 'try body 的异常前缀必须保留 content-folder path origin'
    os_offenders = _unclaimed_folder_operations(
        functions['os_mutations'], 'new_router'
    )
    assert {
        item[3] for item in os_offenders
    } >= {
        'mkdir', 'makedirs', 'remove', 'unlink', 'rmdir', 'rename', 'replace',
        'truncate',
    }, (
        'os-level content-path mutations 必须全部进入 repository-wide guard'
    )
    aliased_os_offenders = _unclaimed_folder_operations(
        functions['aliased_os_mutations'], 'new_router'
    )
    assert {item[3] for item in aliased_os_offenders} >= {'rmdir', 'unlink'}, (
        'generic os mutation aliases 也必须保留 operation identity'
    )
    assert _unclaimed_folder_operations(
        functions['aliased_os_module'], 'new_router'
    ), 'import os as ... 的 module alias 也必须识别 mutation'
    assert _unclaimed_folder_operations(
        functions['conditional_operation_alias'], 'new_router'
    ), '任一 conditional path 指向 protected operation 时必须保留该可能身份'
    assert _unclaimed_folder_operations(
        functions['copy_source'], 'new_router'
    ), 'copy source 读取 content folder 时也必须要求 matching claim'
    assert _unclaimed_folder_operations(
        functions['claimed_copy_source'], 'new_router'
    ) == [], 'external destination 不应让已覆盖的 content-folder source 误报'
    assert _unclaimed_folder_operations(
        functions['move_source'], 'new_router'
    ), 'shutil.move source 移走 content folder 时必须要求 exclusive claim'
    assert _unclaimed_folder_operations(
        functions['claimed_move_source'], 'new_router'
    ) == [], 'matching exclusive claim 应覆盖 content-folder move source'
    open_offenders = _unclaimed_folder_operations(
        functions['open_writes'], 'new_router'
    )
    assert sum(item[3] == _OPEN_WRITE_OPERATION for item in open_offenders) == 3
    assert _unclaimed_folder_operations(
        functions['stored_open_mode'], 'new_router'
    ), '局部变量保存的 write mode 也必须识别为 content-folder writer'
    assert _unclaimed_folder_operations(
        functions['dynamic_open_mode'], 'new_router'
    ), '显式但无法静态解析的 open mode 必须保守视为潜在 writer'
    assert _unclaimed_folder_operations(
        functions['aliased_builtin_open'], 'new_router'
    ), 'builtin open 的 callable alias 也必须识别为 content-folder writer'
    assert _unclaimed_folder_operations(
        functions['stored_os_open_flags'], 'new_router'
    ), '局部变量保存的 os.open write flags 也必须识别为 writer'
    assert _unclaimed_folder_operations(
        functions['aliased_os_open'], 'new_router'
    ), 'import os as ... 的 alias 也必须识别 os.open writer'
    assert _unclaimed_folder_operations(
        functions['atomic_byte_write'], 'new_router'
    ), 'atomic_write_bytes 必须进入 package-wide mutation vocabulary'
    assert _unclaimed_folder_operations(
        functions['explicit_content_local'], 'new_router'
    ), '显式 content_folder local 即使源参数泛化也必须保留语义 origin'
    assert _unclaimed_folder_operations(
        functions['standalone_walrus'], 'new_router'
    ), '普通 eager expression 中的 walrus 也必须传播 content-folder origin'
    escaped_handle_offenders = _unclaimed_folder_operations(
        functions['escaped_write_handle'], 'new_router'
    )
    assert sum(
        item[3] == _OPEN_WRITE_OPERATION for item in escaped_handle_offenders
    ) >= 2, 'writable handle 的 write/close 逃出 claim 后必须继续被 guard 跟踪'
    assert _unclaimed_folder_operations(
        functions['read_only_opens'], 'new_router'
    ) == [], 'read-only open 不应被当成 content-folder writer'
    assert _unclaimed_folder_operations(
        functions['claimed_open_writes'], 'new_router'
    ) == [], 'matching partial claim 应覆盖 mode-aware open writers'
    assert _unclaimed_folder_operations(
        functions['unrelated_open_writes'], 'new_router'
    ) == [], '与 content folder 无关的 open writers 不应被 package-wide guard 误报'
    assert _unclaimed_folder_operations(
        functions['harmless_string_replace'], 'new_router'
    ) == [], 'str.replace 不应被当成 Path.replace 文件系统 mutation'
    assert _unclaimed_folder_operations(
        functions['partial_root_delete'], 'new_router'
    ), 'shared claim 不能保护删除 content-folder root 的操作'
    assert _unclaimed_folder_operations(
        functions['dynamic_partial_root_delete'], 'new_router'
    ), '未知静态形状的 shared claim 不能证明 destructive target 位于根目录下方'
    assert _unclaimed_folder_operations(
        functions['partial_child_delete'], 'new_router'
    ) == [], 'shared claim 仍应覆盖 content-folder 内部的 child mutation'
    assert _unclaimed_folder_operations(
        functions['absolute_escape'], 'new_router'
    ), 'absolute path segment 覆盖 claimed prefix 时必须视为逃逸'
    assert _unclaimed_folder_operations(
        functions['assigned_absolute_escape'], 'new_router'
    ), '赋给局部变量的 absolute segment 也不能逃出 claimed tree'
    assert _unclaimed_folder_operations(
        functions['exceptional_absolute_escape'], 'new_router'
    ), 'try body 异常前缀中的 absolute segment 也不能逃出 claimed tree'
    for name in ('descendant_claim', 'sibling_claim'):
        assert _unclaimed_folder_operations(functions[name], 'new_router'), (
            f'{name} 的 claim 不覆盖 parent 或 sibling mutation'
        )
    assert _unclaimed_folder_operations(
        functions['ancestor_claim'], 'new_router'
    ) == [], 'ancestor folder claim 应覆盖其 descendant mutation'


def test_the_claim_guard_resolves_claim_context_aliases():
    fully_guarded = ast.parse(
        'def _write_claimed_preview_image(folder):\n'
        '    claim = (\n'
        "        claim_partial_writer(folder, purpose='preview')\n"
        "        if flag else claim_partial_writer(folder, purpose='fallback')\n"
        '    )\n'
        '    with claim:\n'
        '        atomic_write_bytes(path, data)\n'
    ).body[0]
    conditionally_guarded = ast.parse(
        'def _write_claimed_preview_image(folder):\n'
        '    claim = (\n'
        "        claim_partial_writer(folder, purpose='preview')\n"
        '        if flag else nullcontext()\n'
        '    )\n'
        '    with claim:\n'
        '        atomic_write_bytes(path, data)\n'
    ).body[0]
    reassigned = ast.parse(
        'def publish():\n'
        '    guard = claim_content_folder(folder, purpose=p)\n'
        '    guard = nullcontext()\n'
        '    with guard:\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    statement_conditional = ast.parse(
        'def publish():\n'
        '    guard = nullcontext()\n'
        '    if flag:\n'
        '        guard = claim_content_folder(folder, purpose=p)\n'
        '    with guard:\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    reassigned_factory = ast.parse(
        'def publish():\n'
        '    factory = claim_content_folder\n'
        '    factory = nullcontext\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    unpacked_reassigned_factory = ast.parse(
        'def publish():\n'
        '    factory = claim_content_folder\n'
        '    (factory,) = (nullcontext,)\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    conditional_factory = ast.parse(
        'def publish():\n'
        '    if flag:\n'
        '        factory = nullcontext\n'
        '    else:\n'
        '        factory = claim_content_folder\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    parameter_shadow = ast.parse(
        'def publish(claim_content_folder):\n'
        '    with claim_content_folder(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    local_definition_shadow = ast.parse(
        'def publish():\n'
        '    def claim_content_folder(folder):\n'
        '        return nullcontext()\n'
        '    with claim_content_folder(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    local_class_shadow = ast.parse(
        'def publish():\n'
        '    class claim_content_folder:\n'
        '        pass\n'
        '    with claim_content_folder(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    imported_shadow = ast.parse(
        'def publish():\n'
        '    from contextlib import nullcontext as claim_content_folder\n'
        '    with claim_content_folder(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    qualified_module_shadow = ast.parse(
        'def publish():\n'
        '    content_gate = fake\n'
        '    with content_gate.claim_content_folder(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    qualified_module_shadow._claim_module_aliases = {'content_gate'}
    match_reassigned_factory = ast.parse(
        'def publish(value):\n'
        '    factory = claim_content_folder\n'
        '    match value:\n'
        '        case _:\n'
        '            factory = nullcontext\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    match_captured_factory = ast.parse(
        'def publish(provider):\n'
        '    factory = claim_content_folder\n'
        '    match provider:\n'
        '        case factory:\n'
        '            with factory(folder):\n'
        '                _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    try_reassigned_factory = ast.parse(
        'def publish():\n'
        '    factory = claim_content_folder\n'
        '    try:\n'
        '        factory = nullcontext\n'
        '    except Exception:\n'
        '        pass\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    try_reassigned_context = ast.parse(
        'def publish():\n'
        "    claim = claim_content_folder(folder, purpose='publish')\n"
        '    try:\n'
        '        claim = nullcontext()\n'
        '    except Exception:\n'
        '        pass\n'
        '    with claim:\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    exceptional_prefix_restored_context = ast.parse(
        'def publish():\n'
        "    claim = claim_content_folder(folder, purpose='publish')\n"
        '    try:\n'
        '        claim = nullcontext()\n'
        '        may_raise()\n'
        "        claim = claim_content_folder(folder, purpose='publish')\n"
        '    except Exception:\n'
        '        pass\n'
        '    with claim:\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    match_reassigned_context = ast.parse(
        'def publish(value):\n'
        "    guard = claim_content_folder(folder, purpose='publish')\n"
        '    match value:\n'
        '        case _:\n'
        '            guard = nullcontext()\n'
        '    with guard:\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    loop_reassigned_factory = ast.parse(
        'def publish(items):\n'
        '    factory = claim_content_folder\n'
        '    for item in items:\n'
        '        factory = nullcontext\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    loop_target_factory = ast.parse(
        'def publish(factories):\n'
        '    factory = claim_content_folder\n'
        '    for factory in factories:\n'
        '        with factory(folder):\n'
        '            _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    with_target_factory = ast.parse(
        'def publish(provider):\n'
        '    factory = claim_content_folder\n'
        '    with provider as factory:\n'
        '        with factory(folder):\n'
        '            _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    exceptional_prefix_restored_factory = ast.parse(
        'def publish():\n'
        '    factory = claim_content_folder\n'
        '    try:\n'
        '        factory = nullcontext\n'
        '        may_raise()\n'
        '        factory = claim_content_folder\n'
        '    except Exception:\n'
        '        pass\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    while_reassigned_factory = ast.parse(
        'def publish(flag):\n'
        '    factory = claim_content_folder\n'
        '    while flag:\n'
        '        factory = nullcontext\n'
        '        break\n'
        '    with factory(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    loop_reassigned_contexts = [
        ast.parse(source).body[0]
        for source in (
            'def publish(items):\n'
            '    guard = claim_content_folder(folder, purpose=p)\n'
            '    for item in items:\n'
            '        guard = nullcontext()\n'
            '    with guard:\n'
            '        _publish_workshop_item(a, b, c, folder)\n',
            'async def publish(items):\n'
            '    guard = claim_content_folder(folder, purpose=p)\n'
            '    async for item in items:\n'
            '        guard = nullcontext()\n'
            '    with guard:\n'
            '        _publish_workshop_item(a, b, c, folder)\n',
            'def publish(flag):\n'
            '    guard = claim_content_folder(folder, purpose=p)\n'
            '    while flag:\n'
            '        guard = nullcontext()\n'
            '        break\n'
            '    with guard:\n'
            '        _publish_workshop_item(a, b, c, folder)\n',
        )
    ]

    assert _unclaimed_folder_operations(fully_guarded, 'preview_cards') == []
    assert _unclaimed_folder_operations(conditionally_guarded, 'preview_cards'), (
        '任一可达分支是 nullcontext 时，条件 context alias 不能算完整占用'
    )
    assert _unclaimed_folder_operations(reassigned, 'publish'), (
        'claim context alias 被普通 context 重赋值后必须立即失效'
    )
    assert _unclaimed_folder_operations(statement_conditional, 'publish'), (
        'statement-level if 只有一个分支拿 claim 时不能算完整占用'
    )
    assert _unclaimed_folder_operations(reassigned_factory, 'publish'), (
        'claim factory alias 被普通 callable 重赋值后必须失效'
    )
    assert _unclaimed_folder_operations(unpacked_reassigned_factory, 'publish'), (
        'destructuring target 重绑定后也必须清除 claim factory identity'
    )
    assert _unclaimed_folder_operations(conditional_factory, 'publish'), (
        'claim factory alias 必须在所有 statement-level 分支上一致'
    )
    assert _unclaimed_folder_operations(parameter_shadow, 'publish'), (
        '同名参数会遮蔽仓库 claim factory，不能被当成真实占用'
    )
    assert _unclaimed_folder_operations(local_definition_shadow, 'publish'), (
        '同名 local def 会遮蔽仓库 claim factory，不能被当成真实占用'
    )
    assert _unclaimed_folder_operations(local_class_shadow, 'publish'), (
        '同名 local class 会遮蔽仓库 claim factory，不能被当成真实占用'
    )
    assert _unclaimed_folder_operations(imported_shadow, 'publish'), (
        '无关 import 绑定同名 local 时必须清除仓库 claim factory 身份'
    )
    assert _unclaimed_folder_operations(qualified_module_shadow, 'publish'), (
        'qualified claim module base 被重绑定后，其 factory identities 必须全部失效'
    )
    assert _unclaimed_folder_operations(match_reassigned_factory, 'publish'), (
        'claim factory alias 必须合并 match 的全部可达 case'
    )
    assert _unclaimed_folder_operations(match_captured_factory, 'publish'), (
        'match capture 绑定必须先清除同名 claim factory alias'
    )
    assert _unclaimed_folder_operations(try_reassigned_factory, 'publish'), (
        'claim factory alias 必须在 try 的全部可达路径上一致'
    )
    assert _unclaimed_folder_operations(try_reassigned_context, 'publish'), (
        'stored claim context 必须在 try 的全部可达路径上一致'
    )
    assert _unclaimed_folder_operations(
        exceptional_prefix_restored_context, 'publish'
    ), 'stored claim context 必须合并 try body 的全部异常前缀'
    assert _unclaimed_folder_operations(match_reassigned_context, 'publish'), (
        'stored claim context 必须合并 match 的全部可达 case'
    )
    assert _unclaimed_folder_operations(loop_reassigned_factory, 'publish'), (
        'claim factory alias 必须合并 zero/nonzero loop 路径'
    )
    assert _unclaimed_folder_operations(loop_target_factory, 'publish'), (
        'for target 绑定必须先清除同名 claim factory alias'
    )
    assert _unclaimed_folder_operations(with_target_factory, 'publish'), (
        'with optional target 绑定必须先清除同名 claim factory alias'
    )
    assert _unclaimed_folder_operations(
        exceptional_prefix_restored_factory, 'publish'
    ), 'try body 的任一异常前缀都必须保留 claim alias 失效状态'
    assert _unclaimed_folder_operations(while_reassigned_factory, 'publish'), (
        'claim factory alias 必须合并 while 的 zero/nonzero 路径'
    )
    for loop_reassigned_context in loop_reassigned_contexts:
        assert _unclaimed_folder_operations(loop_reassigned_context, 'publish'), (
            'stored claim context 必须合并 for/async-for/while 的全部循环路径'
        )


def test_the_claim_guard_requires_one_continuous_claim():
    """Two individually protected halves still leave an acquisition window."""
    split = ast.parse(
        'def _preflight_and_publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    continuous = ast.parse(
        'def _preflight_and_publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    wrong_kind = ast.parse(
        'def _delete_content_folder():\n'
        '    with claim_reference_pair(folder):\n'
        '        shutil.rmtree(folder)\n'
    ).body[0]
    eager_header = ast.parse(
        'def _preflight_and_publish():\n'
        '    with claim_content_folder(\n'
        '        resolve_voice_reference_serialized(folder), purpose=p\n'
        '    ):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    wrapped_claim = ast.parse(
        'def _preflight_and_publish():\n'
        '    with nullcontext(claim_content_folder(folder, purpose=p)):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    wrong_continuous_target = ast.parse(
        'def _preflight_and_publish():\n'
        '    with claim_content_folder(other, purpose=p):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '        with claim_content_folder(folder, purpose=p):\n'
        '            _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]

    assert any(
        item[3] == 'continuous-claim'
        for item in _unclaimed_folder_operations(split, 'publish')
    ), 'preflight 和 upload 分成两把占用时，中间的窗口必须被报出来'
    assert _unclaimed_folder_operations(continuous, 'publish') == []
    assert _unclaimed_folder_operations(eager_header, 'publish'), (
        'with 头部在进入 claim 前求值，里面的目录操作必须报出来'
    )
    assert _unclaimed_folder_operations(wrapped_claim, 'publish'), (
        'claim 只作为参数传给别的 context manager 时并没有被进入，必须报出来'
    )
    assert any(
        item[3] == 'continuous-claim'
        for item in _unclaimed_folder_operations(wrong_kind, 'publish')
    ), '删整个目录必须拿独占 claim_content_folder，共享 pair claim 不够'
    assert any(
        item[3] == 'continuous-claim'
        for item in _unclaimed_folder_operations(wrong_continuous_target, 'publish')
    ), 'continuous claim 必须覆盖 unit 实际操作的同一目录'


def test_package_operations_require_the_matching_claim_kind_and_folder():
    matching = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    wrong_kind = ast.parse(
        'def publish():\n'
        '    with claim_partial_writer(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    wrong_folder = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(first_folder, purpose=p):\n'
        '        _publish_workshop_item(a, b, c, second_folder)\n'
    ).body[0]
    wrong_keyword_folder = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(first_folder, purpose=p):\n'
        '        _publish_workshop_item(a, b, c, content_folder=second_folder)\n'
    ).body[0]
    fake_claim = ast.parse(
        'def publish():\n'
        '    with fake.claim_content_folder(folder):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    keyword_claim = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(content_folder=folder, purpose=p):\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    rebound_folder = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        folder = other_folder\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    crossed_claims = ast.parse(
        'def publish():\n'
        '    with (\n'
        '        claim_content_folder(first, purpose=p),\n'
        '        claim_partial_writer(second),\n'
        '    ):\n'
        '        _publish_workshop_item(a, b, c, second)\n'
    ).body[0]
    dynamic_folder = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(next(folders), purpose=p):\n'
        '        _publish_workshop_item(a, b, c, next(folders))\n'
    ).body[0]
    loop_rebound_folder = ast.parse(
        'def publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        for folder in other_folders:\n'
        '            _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    generic_wrong_kind_and_folder = ast.parse(
        'def delete(content_folder, other):\n'
        '    with claim_partial_writer(other):\n'
        '        shutil.rmtree(content_folder)\n'
    ).body[0]
    generic_matching = ast.parse(
        'def delete(content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        '        shutil.rmtree(content_folder)\n'
    ).body[0]
    mutually_exclusive_rebinding = ast.parse(
        'def delete(content_folder, other, flag):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        '        if flag:\n'
        '            content_folder = other\n'
        '        else:\n'
        '            shutil.rmtree(content_folder)\n'
    ).body[0]
    parent_escape = ast.parse(
        'def delete(content_folder):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        "        shutil.rmtree(Path(content_folder).parent / 'sibling')\n"
    ).body[0]
    comprehension_rebound = ast.parse(
        'def publish(folder, other_folders):\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        [_publish_workshop_item(a, b, c, folder) '
        'for folder in other_folders]\n'
    ).body[0]
    nested_scope_rebinding = ast.parse(
        'def publish(folder):\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        def helper():\n'
        '            folder = other\n'
        '        _publish_workshop_item(a, b, c, folder)\n'
    ).body[0]
    with_rebound_folder = ast.parse(
        'def delete(content_folder, provider):\n'
        '    with claim_content_folder(content_folder, purpose=p):\n'
        '        with provider as content_folder:\n'
        '            shutil.rmtree(content_folder)\n'
    ).body[0]

    assert _unclaimed_folder_operations(matching, 'publish') == []
    assert _unclaimed_folder_operations(wrong_kind, 'publish'), (
        'package-wide publish 必须由独占 claim_content_folder 保护'
    )
    assert _unclaimed_folder_operations(wrong_folder, 'publish'), (
        'claim 必须保护 package-wide operation 实际接收的同一目录'
    )
    assert _unclaimed_folder_operations(wrong_keyword_folder, 'publish'), (
        'keyword content_folder 也必须与 claim target 对应'
    )
    assert _unclaimed_folder_operations(fake_claim, 'publish'), (
        '同名 attribute context 不能冒充仓库 claim factory'
    )
    assert _unclaimed_folder_operations(keyword_claim, 'publish') == []
    assert _unclaimed_folder_operations(rebound_folder, 'publish'), (
        'claim 后重绑定 folder 时，名字相同也不再代表同一目录'
    )
    assert _unclaimed_folder_operations(crossed_claims, 'publish'), (
        'claim kind 必须与它自己的 folder target 成对匹配，不能交叉组合'
    )
    assert _unclaimed_folder_operations(dynamic_folder, 'publish'), (
        '重复求值的动态 folder expression 不能证明目录身份相同'
    )
    assert _unclaimed_folder_operations(loop_rebound_folder, 'publish'), (
        'for target 重绑定 claimed folder 后必须让目录身份失效'
    )
    assert _unclaimed_folder_operations(
        generic_wrong_kind_and_folder, 'publish'
    ), 'generic folder operation 也必须同时匹配 claim kind 和 target'
    assert _unclaimed_folder_operations(generic_matching, 'publish') == []
    assert _unclaimed_folder_operations(
        mutually_exclusive_rebinding, 'publish'
    ) == [], '互斥 if 分支里的重绑定不能污染受 claim 保护的 else operation'
    assert _unclaimed_folder_operations(parent_escape, 'publish'), (
        '同源路径经 parent 逃出 claimed tree 后不能继续算被保护'
    )
    assert _unclaimed_folder_operations(comprehension_rebound, 'publish'), (
        'comprehension target 重绑定 claimed folder 后必须失效'
    )
    assert _unclaimed_folder_operations(nested_scope_rebinding, 'publish') == [], (
        'nested helper 的 local binding 不能污染 enclosing claim scope'
    )
    assert _unclaimed_folder_operations(with_rebound_folder, 'publish'), (
        'with optional target 重绑定 claimed folder 后必须让目录身份失效'
    )


def test_every_folder_consuming_call_sits_inside_a_claim():
    """Auto-discovered, so a new consumer cannot be added without noticing.

    Listing the functions that take a claim today would pass forever. This
    walks every Workshop router module and asks the opposite question: who
    touches the folder without one.

    ``_KNOWN_GAPS`` is not an exemption list -- it is the one place where the
    preview-image gap is written down as machine-checked debt rather than a
    sentence in a PR description that nobody will read again.
    """
    offenders = []
    for short, tree in _workshop_router_trees():
        module_aliases = _operation_aliases(tree.body)
        lambda_functions = _module_lambda_functions(tree)
        module_function = _module_executable_function(tree)
        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in [*functions, *lambda_functions, module_function]:
            if _is_allowed_unclaimed(
                short,
                node.name,
                top_level=(
                    node in tree.body
                    or node in lambda_functions
                    or node is module_function
                ),
            ):
                continue
            offenders.extend(_unclaimed_folder_operations(node, short, module_aliases))

    offenders = _consume_known_gaps(offenders)
    assert not offenders, (
        '这些地方在没拿到目录占用的情况下消费/改动内容目录：'
        f'{[_format_offender(item) for item in offenders]}'
    )


def test_unclaimed_exemptions_are_module_scoped():
    assert _is_allowed_unclaimed(
        'publish', 'prepare_workshop_upload', top_level=True
    )
    assert not _is_allowed_unclaimed(
        'workers.publish', 'prepare_workshop_upload', top_level=True
    )
    assert not _is_allowed_unclaimed(
        'publish', 'prepare_workshop_upload', top_level=False
    )


def test_the_known_gaps_are_still_gaps():
    """A known gap that quietly got fixed must not stay on the list.

    Otherwise the list rots into a permanent blindfold: the day someone moves
    the preview copy inside the claim, this entry would keep excusing whatever
    lands in that function next.
    """
    from main_routers.workshop_router import preview_cards, voice_refs

    modules = {
        'publish': publish,
        'voice_refs': voice_refs,
        'preview_cards': preview_cards,
    }
    for short, name, line, operation in sorted(_KNOWN_GAPS):
        tree = ast.parse(inspect.getsource(modules[short]))
        target = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        )
        matching = [
            item for item in _unclaimed_folder_operations(target, short)
            if item[2] == line and item[3] == operation
        ]
        assert len(matching) == 1, (
            f'{short}.{name}:{line} 的已知 {operation} 欠账已漂移；'
            '重新审视具体调用点并更新 _KNOWN_GAPS'
        )

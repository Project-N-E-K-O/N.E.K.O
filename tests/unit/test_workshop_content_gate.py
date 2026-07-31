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
    assert content_gate._EXCLUSIVE == {}, f"独占占用泄漏：{content_gate._EXCLUSIVE}"
    assert content_gate._PARTIAL_WRITERS == {}, (
        f"共享占用泄漏：{content_gate._PARTIAL_WRITERS}"
    )


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
_WORKER_OFFLOAD_CALLS = {'to_thread', 'run_in_executor', 'submit'}
_EAGER_ITERATOR_CONSUMERS = {
    'all', 'any', 'dict', 'frozenset', 'list', 'max', 'min', 'set', 'sorted', 'sum', 'tuple',
}


def _tail_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


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
    found = []
    for node in _walk_own_scope(func):
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Call):
            name = _tail_name(node)
            if name in _CLAIM_CALLS:
                found.append(node)
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


def _is_deferred_reference(node, parents: dict[int, ast.AST], stop) -> bool:
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, ast.Call) and _tail_name(current) in _WORKER_OFFLOAD_CALLS:
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


def _generator_expression_escapes(node, parents: dict[int, ast.AST]) -> bool:
    """A generator is safe only when a known eager consumer drains it here."""
    current = node
    while id(current) in parents:
        parent = parents[id(current)]
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
) -> bool:
    """Whether calling ``func`` only constructs deferred claim-owning work."""
    parents = _parent_map(func)
    if func.name in claiming and _function_is_generator(func):
        return True
    for child in _walk_own_scope(func):
        if not isinstance(child, ast.Call):
            continue
        if not _resolved_claim_name(
            child, claiming, attribute_claiming, class_scope
        ):
            continue
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
            node, claiming, attribute_claiming, class_scope
        )
    )
    while True:
        wrappers = {
            func.name
            for func in functions
            if func.name not in claiming
            and any(
                isinstance(node, ast.Call)
                and _resolved_claim_name(
                    node, claiming, attribute_claiming, class_scope
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
                    func, claiming, attribute_claiming, class_scope
                )
                or any(
                    isinstance(node, ast.Call)
                    and _resolved_claim_name(
                        node,
                        generator_owners,
                        attribute_generators,
                        class_scope,
                    )
                    for node in _walk_own_scope(func)
                )
            )
        )
        claiming.update(wrappers)


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
            if isinstance(node, ast.FunctionDef)
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


def _claiming_helpers_called_on_loop(func) -> list:
    """Nested claim helpers are legal only when every use is offloaded."""
    helpers = [
        node
        for node in _walk_own_scope(func)
        if isinstance(node, ast.FunctionDef)
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
    return _claiming_names_called_on_loop(func, claiming, generators)


def _reference_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _claiming_names_called_on_loop(
    func,
    claiming_names: set[str],
    generator_names: set[str] | None = None,
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
        if name in generator_names or not _is_deferred_reference(node, parents, func):
            offenders.append((name, node.lineno))
    return offenders


def _module_level_claiming_workers(trees):
    """Find owners per module/class scope, including direct import aliases."""
    scope_functions = {}
    module_trees = dict(trees)
    for module, tree in trees:
        scope_functions[(module, None)] = [
            node for node in tree.body if isinstance(node, ast.FunctionDef)
        ]
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                scope_functions[(module, node.name)] = [
                    child for child in node.body if isinstance(child, ast.FunctionDef)
                ]

    claiming = {}
    generators = {}
    for scope, functions in scope_functions.items():
        claiming[scope], generators[scope] = _claiming_worker_inventory(
            functions, class_scope=scope[1] is not None
        )

    module_names = set(module_trees)
    module_aliases = {module: {} for module in module_names}
    for module, tree in trees:
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module is None:
                for alias in node.names:
                    if alias.name in module_names:
                        module_aliases[module][alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    source_module = alias.name.rsplit('.', 1)[-1]
                    if source_module in module_names and alias.asname:
                        module_aliases[module][alias.asname] = source_module

    while True:
        changed = False
        for module, tree in trees:
            scope = (module, None)
            imported_claiming = set(claiming[scope])
            imported_generators = set(generators[scope])
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                source_module = node.module.rsplit('.', 1)[-1]
                if source_module not in module_names:
                    continue
                source_scope = (source_module, None)
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if alias.name in claiming.get(source_scope, set()):
                        imported_claiming.add(local_name)
                    if alias.name in generators.get(source_scope, set()):
                        imported_generators.add(local_name)
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
            next_claiming, next_generators = _claiming_worker_inventory(
                scope_functions[scope],
                imported_claiming,
                imported_generators,
                attribute_claiming,
                attribute_generators,
            )
            if next_claiming != claiming[scope] or next_generators != generators[scope]:
                claiming[scope] = next_claiming
                generators[scope] = next_generators
                changed = True
        if not changed:
            return claiming, generators, module_aliases


def _scope_claiming_names_called_on_loop(
    func,
    module: str,
    class_name: str | None,
    claiming_by_scope,
    generators_by_scope,
    module_aliases,
) -> list:
    """Resolve module names and ``self`` methods without cross-scope collisions."""
    parents = _parent_map(func)
    local_names = {}
    local_module_aliases = dict(module_aliases.get(module, {}))
    module_names = {scope[0] for scope in claiming_by_scope}
    for child in _walk_own_scope(func):
        if isinstance(child, ast.ImportFrom):
            if child.module:
                source_module = child.module.rsplit('.', 1)[-1]
                source_scope = (source_module, None)
                if source_module in module_names:
                    for alias in child.names:
                        if alias.name in claiming_by_scope.get(source_scope, set()):
                            local_names[alias.asname or alias.name] = (
                                source_scope, alias.name
                            )
            else:
                for alias in child.names:
                    source_module = alias.name.rsplit('.', 1)[-1]
                    if source_module in module_names:
                        local_module_aliases[alias.asname or alias.name] = source_module
        elif isinstance(child, ast.Import):
            for alias in child.names:
                source_module = alias.name.rsplit('.', 1)[-1]
                if source_module in module_names and alias.asname:
                    local_module_aliases[alias.asname] = source_module
    offenders = []
    for node in _walk_own_scope(func):
        if isinstance(node, ast.Name):
            name = node.id
            scope, resolved_name = local_names.get(
                name, ((module, None), name)
            )
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            name = node.attr
            if node.value.id in {'self', 'cls'} and class_name:
                scope = (module, class_name)
            elif (module, node.value.id) in claiming_by_scope:
                scope = (module, node.value.id)
            elif node.value.id in local_module_aliases:
                scope = (local_module_aliases[node.value.id], None)
            else:
                continue
        else:
            continue
        if not isinstance(node, ast.Name):
            resolved_name = name
        if resolved_name not in claiming_by_scope.get(scope, set()):
            continue
        if (
            resolved_name in generators_by_scope.get(scope, set())
            or not _is_deferred_reference(node, parents, func)
        ):
            offenders.append((
                f'{scope[0]}.{scope[1] or "<module>"}.{resolved_name}', node.lineno
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


def test_the_event_loop_guard_checks_module_level_claim_owners():
    tree = ast.parse(
        'def _claiming_worker():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
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
    )
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    claiming, generators, aliases = _module_level_claiming_workers([('synthetic', tree)])

    def module_offenders(name):
        return _scope_claiming_names_called_on_loop(
            functions[name], 'synthetic', None, claiming, generators, aliases
        )

    assert module_offenders('offloaded') == []
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
        functions['handler'], 'synthetic', 'Service', claiming, generators, aliases
    ), 'class method claim owner 的同步 wrapper 被 async method 直调时必须报出来'
    assert _scope_claiming_names_called_on_loop(
        functions['safe_handler'], 'synthetic', 'Other', claiming, generators, aliases
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
    assert module_offenders('safe_attribute_handler') == [], (
        'Safe().owner() 不能因 tail name 与模块 owner 相同而污染 wrapper'
    )

    source = ast.parse(
        'def owner():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
    )
    consumer = ast.parse(
        'from .source import owner as imported_owner\n'
        'from . import source as workers\n'
        'async def imported_direct():\n'
        '    imported_owner()\n'
        'async def imported_offloaded():\n'
        '    await asyncio.to_thread(imported_owner)\n'
        'async def qualified_direct():\n'
        '    workers.owner()\n'
        'async def qualified_offloaded():\n'
        '    await asyncio.to_thread(workers.owner)\n'
        'async def local_import_direct():\n'
        '    from .source import owner as local_owner\n'
        '    local_owner()\n'
        'async def local_import_offloaded():\n'
        '    from .source import owner as local_owner\n'
        '    await asyncio.to_thread(local_owner)\n'
        'async def local_module_direct():\n'
        '    from . import source as local_workers\n'
        '    local_workers.owner()\n'
    )
    unrelated = ast.parse(
        'def owner():\n'
        '    return 1\n'
        'async def safe_same_name():\n'
        '    owner()\n'
    )
    imported_claiming, imported_generators, imported_aliases = (
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
    ), '新模块里的 owner 经直接 import 后仍必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['imported_offloaded'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['qualified_direct'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
    ), 'module alias 上的 owner 直调必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['qualified_offloaded'],
        'consumer',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['local_import_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
    ), 'handler 内 import 的 claim owner 直调也必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['local_import_offloaded'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
    ) == []
    assert _scope_claiming_names_called_on_loop(
        imported_functions['local_module_direct'],
        'consumer', None, imported_claiming, imported_generators, imported_aliases,
    ), 'handler 内 module alias 的 owner 直调也必须被发现'
    assert _scope_claiming_names_called_on_loop(
        imported_functions['safe_same_name'],
        'unrelated',
        None,
        imported_claiming,
        imported_generators,
        imported_aliases,
    ) == [], '另一个模块的同名安全函数不能被误报'


def _workshop_router_trees():
    package_dir = Path(content_gate.__file__).resolve().parent
    return [
        (path.stem, ast.parse(path.read_text(encoding='utf-8')))
        for path in sorted(package_dir.glob('*.py'))
    ]


def test_no_claim_is_ever_taken_on_the_event_loop():
    """Rule 1, pinned: a claim taken in an ``async def`` is released by cancellation.

    Nothing else would fail if someone hoisted the claim up into the handler.
    It would read more tidily, every other test would stay green, and the
    folder would quietly go free the moment a client disconnected -- with the
    worker still writing into it.
    """
    trees = _workshop_router_trees()
    claiming_workers, generator_workers, module_aliases = (
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
            for helper, line in _claiming_helpers_called_on_loop(node):
                offenders.append(f'{short}.{node.name}:{line} -> {helper}（在事件循环直调）')
            for worker, line in _scope_claiming_names_called_on_loop(
                node,
                short,
                class_name,
                claiming_workers,
                generator_workers,
                module_aliases,
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
    'copy2', 'copyfile', 'copytree',
}

# These names are domain-specific enough to scan in every router module. The
# generic file APIs above stay module/unit scoped to avoid treating unrelated
# metadata writes as content-folder mutations.
_PACKAGE_WIDE_OPERATIONS = {
    '_publish_workshop_item',
    '_cleanup_workshop_voice_reference',
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
}

_UNIT_CLAIMS = {
    ('publish', '_preflight_and_publish'): 'claim_content_folder',
    ('voice_refs', '_replace_voice_reference'): 'claim_reference_pair',
    ('voice_refs', '_remove_voice_reference'): 'claim_reference_pair',
    ('publish', '_delete_content_folder'): 'claim_content_folder',
}

# 通用写 API 只在具体 worker 里入账，避免把无关 metadata 写入当成目录竞态。
_SCOPED_OPERATIONS = {
    ('preview_cards', '_write_preview_image'): {'atomic_write_bytes'},
}

# 把工作推迟到别处去跑的原语。哨兵名出现在它们的**实参**里时，「写在 with 里面」
# 什么都不证明 —— `with claim: executor.submit(_publish_workshop_item, ...)` 的
# 上传会在占用放开之后才真正发生，正是这条守卫要防的那个竞态，而按词法包含判定
# 它是绿的。所以这种形状一律算越界，不看它嵌在哪儿。
_DEFERRAL_CALLS = {
    'to_thread', 'run_in_executor', 'submit', 'map', 'Thread',
    'create_task', 'ensure_future', 'partial',
}

# 结构性豁免：那个目录是它自己刚 mkdir 出来的，路径还没返回给任何人，不可能有第二
# 个持有者。
_ALLOWED_UNCLAIMED = {('publish', 'prepare_workshop_upload')}

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
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return node.attr
    return None


def _known_operation_names() -> set[str]:
    names = set(_PACKAGE_WIDE_OPERATIONS) | set(_MUST_BE_CLAIMED)
    for inventory in _UNIT_OPERATIONS.values():
        names.update(inventory)
    for inventory in _SCOPED_OPERATIONS.values():
        names.update(inventory)
    return names


def _operation_aliases(nodes, seed=None) -> dict[str, str]:
    """Resolve imported and assigned aliases of protected operations."""
    known = _known_operation_names()
    aliases = dict(seed or {})
    candidates = list(nodes)
    changed = True
    while changed:
        changed = False
        for node in candidates:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    canonical = aliases.get(alias.name, alias.name)
                    local = alias.asname or alias.name
                    if canonical in known and aliases.get(local) != canonical:
                        aliases[local] = canonical
                        changed = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                canonical = aliases.get(_operation_name(value) or '', _operation_name(value))
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if canonical in known:
                    for target in targets:
                        if isinstance(target, ast.Name) and aliases.get(target.id) != canonical:
                            aliases[target.id] = canonical
                            changed = True
    return aliases


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
    if short in {'publish', 'voice_refs'}:
        required |= _MUST_BE_CLAIMED
    _, unit_inventory = _inventory_for_function(
        _UNIT_OPERATIONS, short, func.name
    )
    _, scoped_operations = _inventory_for_function(
        _SCOPED_OPERATIONS, short, func.name
    )
    required |= set(unit_inventory or {})
    required |= set(scoped_operations or ())
    aliases = _operation_aliases(_walk_own_scope(func), aliases)
    found = []
    for node in _walk_own_scope(func):
        name = _operation_name(node)
        canonical = aliases.get(name, name)
        if canonical in required:
            found.append((node, canonical))
    return found


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
    claim_aliases = {}
    for node in _walk_own_scope(func):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        expressions = [value]
        if isinstance(value, ast.IfExp):
            expressions = [value.body, value.orelse]
        kinds = {
            _tail_name(expression)
            for expression in expressions
            if isinstance(expression, ast.Call)
            and _tail_name(expression) in _CLAIM_CALLS
        }
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if kinds:
            for target in targets:
                if isinstance(target, ast.Name):
                    claim_aliases[target.id] = kinds

    claimed_scopes = []
    for block in _walk_own_scope(func):
        if not isinstance(block, ast.With):
            continue
        claim_kinds = set()
        for item in block.items:
            context = item.context_expr
            if isinstance(context, ast.Call) and _tail_name(context) in _CLAIM_CALLS:
                claim_kinds.add(_tail_name(context))
            elif isinstance(context, ast.Name):
                claim_kinds.update(claim_aliases.get(context.id, set()))
        if not claim_kinds:
            continue
        claimed_scopes.append((
            claim_kinds,
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

    parents = _parent_map(func)
    for operation, _ in _operation_nodes(func, short):
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

    offenders = []
    operations = _operation_nodes(func, short, aliases)
    for child, name in operations:
        if id(child) in deferred_nodes:
            offenders.append((short, func.name, child.lineno, name, '交给别处延后跑'))
        elif not any(id(child) in scope for _, scope in claimed_scopes):
            offenders.append((short, func.name, child.lineno, name, '未占用'))

    unit_key, unit_inventory = _inventory_for_function(
        _UNIT_OPERATIONS, short, func.name
    )
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
        if unit_ids and not any(
            required_claim in kinds and unit_ids <= scope
            for kinds, scope in claimed_scopes
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
        'def deferred_lambda():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        executor.submit(lambda: _publish_workshop_item(folder))\n'
        '\n'
        'def stored_lambda():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        callback = lambda: _publish_workshop_item(folder)\n'
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
        '        return (_publish_workshop_item(folder) for _ in items)\n'
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
        '        _publish_workshop_item(folder)\n'
    )
    functions = {node.name: node for node in tree.body}

    assert _unclaimed_folder_operations(functions['deferred'], 'publish'), (
        '推迟执行的上传必须被报出来——占用早就放开了'
    )
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


def test_the_claim_guard_resolves_claim_context_aliases():
    guarded = ast.parse(
        'def _write_preview_image():\n'
        '    claim = (\n'
        "        claim_partial_writer(folder, purpose='preview')\n"
        '        if folder else nullcontext()\n'
        '    )\n'
        '    with claim:\n'
        '        atomic_write_bytes(path, data)\n'
    ).body[0]

    assert _unclaimed_folder_operations(guarded, 'preview_cards') == []


def test_the_claim_guard_requires_one_continuous_claim():
    """Two individually protected halves still leave an acquisition window."""
    split = ast.parse(
        'def _preflight_and_publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        _publish_workshop_item(folder)\n'
    ).body[0]
    continuous = ast.parse(
        'def _preflight_and_publish():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '        _publish_workshop_item(folder)\n'
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
        '        _publish_workshop_item(folder)\n'
    ).body[0]
    wrapped_claim = ast.parse(
        'def _preflight_and_publish():\n'
        '    with nullcontext(claim_content_folder(folder, purpose=p)):\n'
        '        resolve_voice_reference_serialized(folder)\n'
        '        _publish_workshop_item(folder)\n'
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
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (short, node.name) in _ALLOWED_UNCLAIMED or any(
                name == node.name for _, name in _ALLOWED_UNCLAIMED
            ):
                continue
            offenders.extend(_unclaimed_folder_operations(node, short, module_aliases))

    offenders = _consume_known_gaps(offenders)
    assert not offenders, (
        '这些地方在没拿到目录占用的情况下消费/改动内容目录：'
        f'{[_format_offender(item) for item in offenders]}'
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

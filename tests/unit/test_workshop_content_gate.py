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
    assert content_gate._PAIR_WRITERS == {}, f"共享占用泄漏：{content_gate._PAIR_WRITERS}"


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
        with pytest.raises(ContentFolderBusy, match='参考语音正在写入'):
            with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
                pass


def test_reference_writers_remain_shared(tmp_path):
    with claim_reference_pair(str(tmp_path)):
        with claim_reference_pair(str(tmp_path)):
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


async def _drain(task) -> None:
    """Wait the task out and retrieve its outcome, without changing the verdict."""
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


def _run_worker(done: threading.Event, func, *args):
    """Expose completion separately from the cancellable asyncio wrapper."""
    try:
        return func(*args)
    finally:
        done.set()


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
    try:
        assert await asyncio.to_thread(gate.wait, _SYNC_TIMEOUT), (
            f'{what} 没在 {_SYNC_TIMEOUT:.0f}s 内就位——交错没建立起来，'
            f'后面的断言证明不了任何东西'
        )
        yield
    finally:
        release.set()
        if worker_done is not None:
            assert await asyncio.to_thread(worker_done.wait, _SYNC_TIMEOUT), (
                f'{what} 的 asyncio 等待方已经结束，但 worker 没有真正收尾'
            )
        await _drain(task)


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


_CLAIM_CALLS = {'claim_content_folder', 'claim_reference_pair'}
_WORKER_OFFLOAD_CALLS = {'to_thread', 'run_in_executor', 'submit'}


def _tail_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _walk_own_scope(func):
    """Walk one function body without attributing nested defs to its parent."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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


def _is_deferred_reference(node, parents: dict[int, ast.AST], stop) -> bool:
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, ast.Call) and _tail_name(current) in _WORKER_OFFLOAD_CALLS:
            callable_arg = _worker_callable(current)
            if callable_arg is node:
                return True
            if isinstance(callable_arg, ast.Lambda) and _contains_node(callable_arg, node):
                return True
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


def _claiming_helpers_called_on_loop(func) -> list:
    """Nested claim helpers are legal only when every use is offloaded."""
    helpers = {
        node.name: node
        for node in _walk_own_scope(func)
        if isinstance(node, ast.FunctionDef) and _claim_calls_in_own_scope(node)
    }
    if not helpers:
        return []

    return _claiming_names_called_on_loop(func, set(helpers))


def _reference_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _claiming_names_called_on_loop(func, claiming_names: set[str]) -> list:
    """References to claim-owning sync workers must be worker callables."""
    if not claiming_names:
        return []

    parents = _parent_map(func)
    offenders = []
    for node in _walk_own_scope(func):
        name = _reference_name(node)
        if name not in claiming_names:
            continue
        if not _is_deferred_reference(node, parents, func):
            offenders.append((name, node.lineno))
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


def test_the_event_loop_guard_checks_module_level_claim_owners():
    tree = ast.parse(
        'def _claiming_worker():\n'
        '    with claim_content_folder(folder, purpose=p):\n'
        '        pass\n'
        '\n'
        'async def offloaded():\n'
        '    await asyncio.to_thread(_claiming_worker)\n'
        '\n'
        'async def direct():\n'
        '    _claiming_worker()\n'
    )
    functions = {node.name: node for node in tree.body}

    assert _claiming_names_called_on_loop(
        functions['offloaded'], {'_claiming_worker'}
    ) == []
    assert _claiming_names_called_on_loop(
        functions['direct'], {'_claiming_worker'}
    ), '模块级 claim owner 被协程直接调用时也必须报出来'


def test_no_claim_is_ever_taken_on_the_event_loop():
    """Rule 1, pinned: a claim taken in an ``async def`` is released by cancellation.

    Nothing else would fail if someone hoisted the claim up into the handler.
    It would read more tidily, every other test would stay green, and the
    folder would quietly go free the moment a client disconnected -- with the
    worker still writing into it.
    """
    from main_routers.workshop_router import preview_cards, voice_manifest, voice_refs

    modules = (publish, voice_refs, preview_cards, voice_manifest, content_gate)
    trees = [(module, ast.parse(inspect.getsource(module))) for module in modules]
    claiming_workers = {
        node.name
        for _, tree in trees
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and _claim_calls_in_own_scope(node)
    }

    offenders = []
    for module, tree in trees:
        short = module.__name__.rsplit('.', 1)[-1]
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            # 嵌套的 async def 会被这层 walk 单独取到，各查各的作用域。
            for call in _claim_calls_in_own_scope(node):
                offenders.append(f'{short}.{node.name}:{call.lineno}')
            for helper, line in _claiming_helpers_called_on_loop(node):
                offenders.append(f'{short}.{node.name}:{line} -> {helper}（在事件循环直调）')
            for worker, line in _claiming_names_called_on_loop(node, claiming_workers):
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

# 已知欠账也用函数专属名字入账。`open`/`write` 不能全模块扫描，但在这个路由里正是
# 那次未校验路径、未占用、还直接跑在事件循环上的 preview 写入。
_SCOPED_OPERATIONS = {
    ('preview_cards', 'upload_preview_image'): {'open', 'write'},
}

# 把工作推迟到别处去跑的原语。哨兵名出现在它们的**实参**里时，「写在 with 里面」
# 什么都不证明 —— `with claim: executor.submit(_publish_workshop_item, ...)` 的
# 上传会在占用放开之后才真正发生，正是这条守卫要防的那个竞态，而按词法包含判定
# 它是绿的。所以这种形状一律算越界，不看它嵌在哪儿。
_DEFERRAL_CALLS = {
    'to_thread', 'run_in_executor', 'submit', 'create_task', 'ensure_future', 'partial',
}

# 结构性豁免：那个目录是它自己刚 mkdir 出来的，路径还没返回给任何人，不可能有第二
# 个持有者。
_ALLOWED_UNCLAIMED = {('publish', 'prepare_workshop_upload')}

# ⚠️ 已知缺口，不是豁免。放在这里是为了让它**可见**、而且会随代码漂移被重新审视：
# publish_to_workshop 把预览图 copy2 进内容目录发生在 claim **之前**，同一目录被
# 重复发布时能撕裂预览图。修它要连 /upload-preview-image 一起动 —— 那条路由既没
# 占用，也没有 _assert_under_base 路径校验，而且是在事件循环上直接 open().write()，
# 要拿占用得先把这次写挪进 worker 单元。那是独立一个 PR 的事。删掉这一条之前先确认
# 那边真的修好了。
_KNOWN_GAPS = {
    # 绑定到具体源码位置，而不是同函数同名操作的数量；旧点被修、新点冒出来不能互换。
    ('publish', 'publish_to_workshop', 596, 'copy2'),
    ('publish', 'publish_to_workshop', 620, 'copy2'),
    ('preview_cards', 'upload_preview_image', 538, 'open'),
    ('preview_cards', 'upload_preview_image', 539, 'write'),
}


def _operation_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _operation_nodes(func, short: str) -> list[tuple[ast.AST, str]]:
    required = set(_MUST_BE_CLAIMED) if short in {'publish', 'voice_refs'} else set()
    required |= set(_UNIT_OPERATIONS.get((short, func.name), {}))
    required |= _SCOPED_OPERATIONS.get((short, func.name), set())
    found = []
    for node in _walk_own_scope(func):
        name = _operation_name(node)
        if name in required:
            found.append((node, name))
    return found


def _unclaimed_folder_operations(func, short: str) -> list:
    """Sentinel operations in ``func`` that no claim covers.

    Two ways to be uncovered, and the second one is why lexical containment
    alone is not enough: the reference sits outside every claiming ``with``,
    or it is handed to something that runs it later (see ``_DEFERRAL_CALLS``),
    in which case being inside the ``with`` says nothing about when the work
    actually touches the folder.
    """
    claimed_scopes = []
    for block in _walk_own_scope(func):
        if not isinstance(block, ast.With):
            continue
        claim_kinds = set()
        for item in block.items:
            for node in ast.walk(item.context_expr):
                if isinstance(node, ast.Call) and _tail_name(node) in _CLAIM_CALLS:
                    claim_kinds.add(_tail_name(node))
        if not claim_kinds:
            continue
        claimed_scopes.append((claim_kinds, {id(inner) for inner in ast.walk(block)}))

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

    offenders = []
    operations = _operation_nodes(func, short)
    for child, name in operations:
        if id(child) in deferred_nodes:
            offenders.append((short, func.name, child.lineno, name, '交给别处延后跑'))
        elif not any(id(child) in scope for _, scope in claimed_scopes):
            offenders.append((short, func.name, child.lineno, name, '未占用'))

    unit_inventory = _UNIT_OPERATIONS.get((short, func.name))
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
        required_claim = _UNIT_CLAIMS[(short, func.name)]
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
    assert _unclaimed_folder_operations(functions['direct'], 'publish') == [], (
        '直接在占用里同步跑完是合法的，守卫不该报它'
    )


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

    assert any(
        item[3] == 'continuous-claim'
        for item in _unclaimed_folder_operations(split, 'publish')
    ), 'preflight 和 upload 分成两把占用时，中间的窗口必须被报出来'
    assert _unclaimed_folder_operations(continuous, 'publish') == []
    assert any(
        item[3] == 'continuous-claim'
        for item in _unclaimed_folder_operations(wrong_kind, 'publish')
    ), '删整个目录必须拿独占 claim_content_folder，共享 pair claim 不够'


def test_every_folder_consuming_call_sits_inside_a_claim():
    """Auto-discovered, so a new consumer cannot be added without noticing.

    Listing the functions that take a claim today would pass forever. This
    walks the two modules that own a content folder's lifecycle and asks the
    opposite question: who touches the folder without one.

    ``_KNOWN_GAPS`` is not an exemption list -- it is the one place where the
    preview-image gap is written down as machine-checked debt rather than a
    sentence in a PR description that nobody will read again.
    """
    from main_routers.workshop_router import preview_cards, voice_refs

    offenders = []
    for module in (publish, voice_refs, preview_cards):
        short = module.__name__.rsplit('.', 1)[-1]
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (short, node.name) in _ALLOWED_UNCLAIMED:
                continue
            offenders.extend(_unclaimed_folder_operations(node, short))

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

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


async def _parked(gate: threading.Event, what: str) -> None:
    """Wait for the worker to reach its gate, and say so plainly if it never does.

    Dropping the flag ``Event.wait`` returns would let a synchronisation
    timeout fall straight through into the assertions below, which then fail
    for the wrong reason: "DID NOT RAISE" reads like the exclusion is broken
    when in fact the interleaving these tests exist to force was never
    established.
    """
    assert await asyncio.to_thread(gate.wait, 5), (
        f'{what} 没在 5s 内就位——交错没建立起来，后面的断言证明不了任何东西'
    )


def _wait_until_nobody_holds(content_folder: str, *, timeout: float = 5.0) -> bool:
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
        finish.wait(timeout=5)
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
    await _parked(uploading, '假 SetItemContent')

    # finally 而不是顺着往下写：断言失败时假上传还卡在门上，它会攥着占用直到 5s
    # 超时，清账 fixture 于是在真正的失败上面再叠一条「占用泄漏」，把人往错误的
    # 方向带。放行永远要发生。
    try:
        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(
                voice_refs._replace_voice_reference,
                *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'sneaked-in', 'sneaked'),
            )
    finally:
        finish.set()

    assert await publishing == 4242

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
        finish.wait(timeout=5)
        return 7

    monkeypatch.setattr(publish, '_publish_workshop_item', _fake_steam_upload)

    publishing = asyncio.create_task(
        asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))
    )
    await _parked(uploading, '假 SetItemContent')

    try:
        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(voice_refs._remove_voice_reference, str(tmp_path))
    finally:
        finish.set()

    assert await publishing == 7, '发布本身必须照常跑完——被挡的是删，不是它'

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
        finish.wait(timeout=5)
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
    await _parked(mid_swap, 'swap 的提交点')

    try:
        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))
    finally:
        finish.set()

    await swapping
    assert _snapshot_pair(str(tmp_path))['prefix'] == 'new', (
        '被挡下的发布不该影响 swap 自己——它必须照常提交完'
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

    def _slow_upload(steamworks, title, description, content_folder, *rest):
        uploading.set()
        finish.wait(timeout=5)
        return 99

    monkeypatch.setattr(publish, '_publish_workshop_item', _slow_upload)

    task = asyncio.create_task(
        asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))
    )
    await _parked(uploading, '假 SetItemContent')

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(
                voice_refs._replace_voice_reference,
                *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'sneaked-in', 'sneaked'),
            )
    finally:
        finish.set()

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
    real_write = voice_refs.atomic_write_json

    def _park_before_commit(*args, **kwargs):
        swapping.set()
        finish.wait(timeout=5)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(voice_refs, 'atomic_write_json', _park_before_commit)

    task = asyncio.create_task(
        asyncio.to_thread(
            voice_refs._replace_voice_reference,
            *_swap_args(tmp_path, 'voice_sample_bbbbbbbbbbbb.wav', b'new-audio', 'new'),
        )
    )
    await _parked(swapping, 'swap 的提交点')

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(ContentFolderBusy):
            await asyncio.to_thread(publish._preflight_and_publish, *_publish_args(str(tmp_path)))
    finally:
        finish.set()

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


# ── structural guards ───────────────────────────────────────────────────


_CLAIM_CALLS = {'claim_content_folder', 'claim_reference_pair'}


def _referenced_names(node) -> set:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


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
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # 嵌套函数自己就是一个 worker 单元，不属于这个协程的作用域
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else None
            )
            if name in _CLAIM_CALLS:
                found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


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
        'async def hoisted():\n'
        "    with claim_content_folder(folder, purpose='x'):\n"
        '        pass\n'
    )
    functions = {node.name: node for node in tree.body}

    assert _claim_calls_in_own_scope(functions['handler']) == [], (
        '嵌套的同步 worker 里拿占用是合法的，守卫不该报它'
    )
    assert _claim_calls_in_own_scope(functions['hoisted']), (
        '直接写在协程体里的占用必须被报出来，否则守卫什么都没守'
    )


def test_no_claim_is_ever_taken_on_the_event_loop():
    """Rule 1, pinned: a claim taken in an ``async def`` is released by cancellation.

    Nothing else would fail if someone hoisted the claim up into the handler.
    It would read more tidily, every other test would stay green, and the
    folder would quietly go free the moment a client disconnected -- with the
    worker still writing into it.
    """
    from main_routers.workshop_router import voice_manifest, voice_refs

    offenders = []
    for module in (publish, voice_refs, voice_manifest, content_gate):
        tree = ast.parse(inspect.getsource(module))
        short = module.__name__.rsplit('.', 1)[-1]
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            # 嵌套的 async def 会被这层 walk 单独取到，各查各的作用域。
            for call in _claim_calls_in_own_scope(node):
                offenders.append(f'{short}.{node.name}:{call.lineno}')

    assert not offenders, f'这些占用是在协程里拿的，取消会把它们提前放开：{offenders}'


# 会消费或摧毁整个内容目录、或者改动那对参考语音文件的操作。引用到它们的函数必须把
# 这次引用放在 claim 的 with **里面** —— 不是「同一个函数里也有个 claim」，否则把工作
# 挪到 with 外面一行就能绕过去。
_MUST_BE_CLAIMED = {
    '_publish_workshop_item',             # Steam 把整个目录读走
    '_cleanup_workshop_voice_reference',  # 删掉这对文件
    'atomic_write_json',                  # 提交新 manifest，swap 的唯一提交点
    'rmtree',                             # 删掉整个目录
}

# 唯一的例外，理由是结构性的：那个目录是它自己刚 mkdir 出来的，路径还没返回给任何人，
# 不可能有第二个持有者。
_ALLOWED_UNCLAIMED = {('publish', 'prepare_workshop_upload')}


def test_every_folder_consuming_call_sits_inside_a_claim():
    """Auto-discovered, so a new consumer cannot be added without noticing.

    Listing the functions that take a claim today would pass forever. This
    walks the two modules that own a content folder's lifecycle and asks the
    opposite question: who touches the folder without one.
    """
    from main_routers.workshop_router import voice_refs

    offenders = []
    for module in (publish, voice_refs):
        short = module.__name__.rsplit('.', 1)[-1]
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (short, node.name) in _ALLOWED_UNCLAIMED:
                continue

            claimed_nodes = set()
            for block in ast.walk(node):
                if not isinstance(block, ast.With):
                    continue
                if not any(
                    _referenced_names(item.context_expr) & _CLAIM_CALLS
                    for item in block.items
                ):
                    continue
                for inner in ast.walk(block):
                    claimed_nodes.add(id(inner))

            for child in ast.walk(node):
                if not isinstance(child, (ast.Name, ast.Attribute)):
                    continue
                name = child.id if isinstance(child, ast.Name) else child.attr
                if name not in _MUST_BE_CLAIMED:
                    continue
                if id(child) in claimed_nodes:
                    continue
                offenders.append(f'{short}.{node.name}:{child.lineno} -> {name}')

    assert not offenders, f'这些地方在没拿到目录占用的情况下消费/改动内容目录：{offenders}'

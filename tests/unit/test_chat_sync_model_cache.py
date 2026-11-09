"""Model cache collection preserves all live references and authority fences."""

import asyncio
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from services.chat_sync.model_package import install_model, package_model
from services.chat_sync.model_protocol import canonical
from services.chat_sync.writer import store_connection
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_chat_sync_runtime import DOMAIN, UID, setup_runtime
from utils.config_manager import set_reserved


@pytest.fixture
async def cache_runtime(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    config = SimpleNamespace(live2d_dir=tmp_path / 'models', _characters_reload_lock=threading.RLock(),
                             characters={'猫娘': {}})
    config.load_characters = lambda **kwargs: deepcopy(config.characters)
    runtime.models.adapter = SimpleNamespace(config=config)
    await runtime.configure('https://sync.test')
    await runtime.enable(UID, 'Synthetic')
    try:
        yield runtime, config
    finally:
        await runtime.close()


def bundle(runtime, config, name='one'):
    source = config.live2d_dir / 'originals' / name
    source.mkdir(parents=True)
    model = source / 'model.model3.json'
    model.write_text('{"Version":3,"FileReferences":{"Moc":"model.moc3","Textures":[]}}')
    (source / 'model.moc3').write_bytes(b'MOC3' + name.encode())
    packages = runtime.models.store.path.parent / 'model-packages'
    record, upload = package_model(model, packages)
    downloaded = packages / (upload['sha256'] + '.zip')
    downloaded.write_bytes(Path(upload['path']).read_bytes())
    installed = install_model(downloaded, record, config.live2d_dir / 'cloud-sync')
    return record, upload, downloaded, installed


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['none', 'role', 'relative_role', 'selection', 'relative_selection', 'candidate', 'conflict', 'pending', 'failed', 'error'])
async def test_collection_preserves_references_from_every_role_and_task(cache_runtime, reference):
    runtime, config = cache_runtime
    record, upload, downloaded, installed = bundle(runtime, config)
    root = installed.parent
    path = '/user_live2d/' + installed.relative_to(config.live2d_dir).as_posix()
    # The model manager saves a catalog path relative to the model root.
    if reference.startswith('relative_'):
        path = installed.relative_to(config.live2d_dir).as_posix()
    state = {'revision': 1, 'selection': ''}
    if reference in ('role', 'relative_role'):
        raw = {}
        set_reserved(raw, 'avatar', 'live2d', 'model_path', path)
        config.characters['猫娘']['Other'] = raw
    elif reference in ('selection', 'relative_selection'):
        state['selection'] = canonical(['live2d', path]).decode()
    elif reference in ('candidate', 'conflict', 'pending'):
        state[reference] = {'record': record, 'path': path}
        if reference == 'pending':
            state[reference]['file'] = upload
    elif reference in ('failed', 'error'):
        state[reference] = 2
    _, _, commit = await runtime._bound_transport(UID)
    # A different domain must protect shared caches too.
    await commit(runtime.models.store.put, 'other-domain', state)
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert root.exists() is (reference in ('role', 'relative_role', 'selection', 'relative_selection', 'candidate', 'conflict', 'pending'))
    assert downloaded.exists() is (reference in ('candidate', 'conflict', 'pending'))
    assert Path(upload['path']).exists() is (reference == 'pending')
    assert (config.live2d_dir / 'originals' / 'one' / 'model.moc3').read_bytes() == b'MOC3one'


@pytest.mark.asyncio
async def test_collection_never_follows_links_or_removes_foreign_files(cache_runtime, tmp_path):
    runtime, config = cache_runtime
    _, upload, _, installed = bundle(runtime, config)
    foreign = tmp_path / 'user-file'
    foreign.write_bytes(b'user data')
    linked = installed.parent / 'linked'
    packages = Path(upload['path']).parent
    package_link = packages / ('a' * 32 + '.zip')
    try:
        linked.symlink_to(foreign)
        package_link.symlink_to(foreign)
    except (OSError, NotImplementedError):
        pytest.skip('filesystem symlinks are unavailable')
    ordinary = packages / 'original.zip'
    ordinary.write_bytes(b'keep')
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert installed.exists() and linked.is_symlink() and package_link.is_symlink()
    assert foreign.read_bytes() == b'user data' and ordinary.read_bytes() == b'keep'


@pytest.mark.asyncio
async def test_model_file_lease_defers_collection_until_the_pending_commit(cache_runtime):
    runtime, config = cache_runtime
    _, upload, _, _ = bundle(runtime, config)
    async with runtime.models.cache_lock:
        await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
        assert Path(upload['path']).exists()
        _, _, commit = await runtime._bound_transport(UID)
        await commit(runtime.models.store.put, DOMAIN.key(), {'revision': 0, 'selection': '', 'pending': {'file': upload}})
    await asyncio.to_thread(runtime.models.cleanup_cache)
    assert Path(upload['path']).exists()
    _, _, commit = await runtime._bound_transport(UID)
    await commit(runtime.models.store.put, DOMAIN.key(), {'revision': 0, 'selection': ''})
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert not Path(upload['path']).exists()


@pytest.mark.asyncio
async def test_recovery_collects_revoked_uploads_only_after_success(cache_runtime, monkeypatch):
    runtime, config = cache_runtime
    _, upload, _, _ = bundle(runtime, config)
    _, _, commit = await runtime._bound_transport(UID)
    await commit(runtime.models.store.put, DOMAIN.key(), {'revision': 0, 'selection': '', 'pending': {'file': upload}})
    original = runtime.writer._write

    def fail_completion(state):
        if not state['quarantined']:
            raise OSError('synthetic failed recovery')
        return original(state)

    monkeypatch.setattr(runtime.writer, '_write', fail_completion)
    with pytest.raises(ValueError, match='chat_sync_writer_recovery_required'):
        await asyncio.to_thread(runtime.prepare_writer_recovery)
    assert Path(upload['path']).exists()
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert Path(upload['path']).exists()
    monkeypatch.setattr(runtime.writer, '_write', original)
    await asyncio.to_thread(runtime.prepare_writer_recovery)
    assert not Path(upload['path']).exists()


@pytest.mark.asyncio
async def test_role_retirement_collects_only_the_committed_unreferenced_upload(cache_runtime):
    runtime, config = cache_runtime
    _, upload, _, _ = bundle(runtime, config)
    _, _, commit = await runtime._bound_transport(UID)
    await commit(runtime.models.store.put, DOMAIN.key(), {'revision': 0, 'selection': '', 'pending': {'file': upload}})
    binding = runtime.state.binding(UID)
    presence = await runtime.session_presence_provider()
    await asyncio.to_thread(runtime._purge_retired_character, binding, presence, runtime.writer.epoch())
    assert not Path(upload['path']).exists()
    with closing(store_connection(runtime.models.store.path)) as conn:
        assert not conn.execute('SELECT 1 FROM model_state WHERE domain=?', (DOMAIN.key(),)).fetchone()


@pytest.mark.asyncio
async def test_readable_model_root_does_not_pin_an_unrelated_writable_cache(cache_runtime, tmp_path):
    runtime, config = cache_runtime
    _, _, _, installed = bundle(runtime, config)
    config.readable_live2d_dir = tmp_path / 'readable-models'
    relative = installed.relative_to(config.live2d_dir)
    original = config.readable_live2d_dir / relative
    original.parent.mkdir(parents=True)
    original.write_bytes(b'readable original')
    raw = {}
    set_reserved(raw, 'avatar', 'live2d', 'model_path', '/user_live2d/' + relative.as_posix())
    config.characters['猫娘']['Other'] = raw
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert original.read_bytes() == b'readable original'
    assert not installed.exists()


@pytest.mark.asyncio
async def test_failed_reference_read_keeps_all_cache_files(cache_runtime):
    runtime, config = cache_runtime
    _, upload, downloaded, installed = bundle(runtime, config)
    def unavailable(**kwargs):
        raise OSError('synthetic unreadable characters')
    config.load_characters = unavailable
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert Path(upload['path']).exists() and downloaded.exists() and installed.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('line_ending', [b'\n', b'\r\n'])
async def test_another_process_lease_also_blocks_collection(cache_runtime, line_ending):
    runtime, config = cache_runtime
    _, upload, _, _ = bundle(runtime, config)
    script = ("from filelock import FileLock; import sys\nwith FileLock(sys.argv[1]):\n"
              f" sys.stdout.buffer.write({b'ready' + line_ending!r}); sys.stdout.buffer.flush()\n sys.stdin.readline()\n")
    child = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(runtime.models.store.path.parent / 'model-cache.lock'),
                                                 stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        assert (await asyncio.wait_for(child.stdout.readline(), 5)).rstrip(b'\r\n') == b'ready'
        await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
        assert Path(upload['path']).exists()
    finally:
        await asyncio.wait_for(child.communicate(b'\n'), 5)
    await asyncio.to_thread(runtime.models.cleanup_cache)
    assert not Path(upload['path']).exists()


@pytest.mark.asyncio
async def test_cancelled_model_step_releases_the_file_lease_before_collection(cache_runtime, monkeypatch):
    runtime, config = cache_runtime
    _, upload, _, _ = bundle(runtime, config)
    entered = asyncio.Event()
    original = runtime._request
    async def stalled(method, path, *args, **kwargs):
        if path.endswith('/models/capabilities'):
            entered.set()
            await asyncio.Event().wait()
        return await original(method, path, *args, **kwargs)
    monkeypatch.setattr(runtime, '_request', stalled)
    task = asyncio.create_task(runtime.models.step(UID))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
        assert Path(upload['path']).exists()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not Path(upload['path']).exists()


@pytest.mark.asyncio
async def test_cache_root_links_are_never_followed(cache_runtime, tmp_path):
    runtime, config = cache_runtime
    _, upload, downloaded, installed = bundle(runtime, config)
    packages = Path(upload['path']).parent
    backup = tmp_path / 'linked-packages'
    packages.rename(backup)
    cloud = installed.parent.parent
    copied = tmp_path / 'linked-installations'
    cloud.rename(copied)
    try:
        packages.symlink_to(backup, target_is_directory=True)
        cloud.symlink_to(copied, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('filesystem symlinks are unavailable')
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert Path(upload['path']).exists() and downloaded.exists() and installed.exists()


@pytest.mark.asyncio
async def test_one_unremovable_package_does_not_block_other_collection(cache_runtime, monkeypatch):
    runtime, config = cache_runtime
    _, upload, _, _ = bundle(runtime, config)
    _, other, _, _ = bundle(runtime, config, 'two')
    original = Path.unlink
    def denied(path, *args, **kwargs):
        if str(path) == upload['path']:
            raise PermissionError('synthetic denied cache removal')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', denied)
    await asyncio.to_thread(runtime.models.cleanup_cache, force=True)
    assert Path(upload['path']).exists() and not Path(other['path']).exists()
    assert runtime.models._cleanup_needed
    runtime.writer.check()

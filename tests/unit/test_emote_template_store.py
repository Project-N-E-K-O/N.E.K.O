# -*- coding: utf-8 -*-
"""Test replacement semantics, actual storage size and safe texture paths.

The storage layer uses temporary directories without FastAPI. Route tests cover
multipart parsing and response errors. Importing an existing key replaces its
copy, reported sizes come from stat, and texture paths remain within the copy.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from main_logic import emote_template_store as store_module
from main_logic.emote_template_store import (
    EmoteTemplateArchiveStore,
    EmoteTemplateStoreError,
    dir_key_for,
)


def _zip(content: bytes | str = b"template") -> bytes:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(zipfile.ZipInfo("wrapper/model.json", date_time=(2020, 1, 1, 0, 0, 0)),
                         payload.decode("utf-8", "ignore"))
    return stream.getvalue()


@pytest.fixture()
def store(tmp_path: Path) -> EmoteTemplateArchiveStore:
    return EmoteTemplateArchiveStore(tmp_path / "emote_templates")


def test_save_list_delete_roundtrip_reports_real_bytes(store, tmp_path):
    key = "YUI 道具::ModelA::191::191"
    entry = store.save(key, _zip())
    assert entry["key"] == key

    listed = store.list()
    assert [item["key"] for item in listed] == [key]
    # bytes 是 stat 出来的真实占用：往贴图目录里再塞一张图，数字必须跟着涨
    texture = store._entry_dir(key) / "textures" / "Cache" / "a.png"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(b"12345")
    assert store.list()[0]["bytes"] == entry["bytes"] + 5

    assert store.archive_path(key).read_bytes() == _zip()
    assert store.texture_paths(key) == ["Cache/a.png"]
    assert store.texture_path(key, "Cache/a.png") == texture

    freed = store.delete(key)
    assert freed["freedBytes"] == entry["bytes"] + 5
    assert store.list() == []
    assert not store.has(key)
    # 再删一次不报错：删不存在的东西等于没删
    assert store.delete(key)["freedBytes"] == 0


def test_reopening_store_recovers_a_copy_retired_before_publication(store):
    key = 'recoverable'
    store.save(key, _zip('old'), [('StageImages/one.png', b'original-image')])
    store._entry_dir(key).rename(store.root / '.retired-interrupted')
    recovered = EmoteTemplateArchiveStore(store.root)
    assert recovered.has(key)
    assert recovered.archive_path(key).read_bytes() == _zip('old')
    assert recovered.texture_path(key, 'StageImages/one.png').read_bytes() == b'original-image'


def test_reimport_preserves_images_recovered_before_the_write_check(store, monkeypatch):
    import main_routers.emote_template_router as routes
    from main_logic.emote_template_adapter import inspect_spine_zip
    from tests.emote_fixtures import spine_zip

    archive = spine_zip()
    key = inspect_spine_zip(archive)['id']
    store.save(key, archive, [('StageImages/saved.png', b'original-image')])
    stale = EmoteTemplateArchiveStore(store.root)
    store._entry_dir(key).rename(store.root / '.retired-interrupted')
    original = stale._require_writable

    def recover_then_check(key):
        # Another request can recover the old copy after an earlier has() read
        # returned false, but before save checks whether publication is safe.
        EmoteTemplateArchiveStore(store.root)
        original(key)

    monkeypatch.setattr(stale, '_require_writable', recover_then_check)
    result = routes._save_template_copy(stale, key, archive, 'reimport.zip', '', '', False)
    assert result['textures'] == ['StageImages/saved.png']
    assert stale.texture_path(key, 'StageImages/saved.png').read_bytes() == b'original-image'


def test_first_builtin_texture_save_preserves_images_recovered_after_has(store, monkeypatch):
    import main_routers.emote_template_router as routes

    key = routes._BUILTIN_KEY
    archive = _zip('builtin')
    store.save(key, archive, [('StageImages/saved.png', b'original-image')])
    stale = EmoteTemplateArchiveStore(store.root)
    store._entry_dir(key).rename(store.root / '.retired-interrupted')
    original = stale._require_writable

    def recover_then_check(key):
        EmoteTemplateArchiveStore(store.root)
        original(key)

    monkeypatch.setattr(routes, '_archive_store', lambda: stale)
    monkeypatch.setattr(routes, '_builtin_template', lambda _: ({}, archive, {}))
    monkeypatch.setattr(stale, '_require_writable', recover_then_check)
    result = routes._merge_library_textures(key, [('StageImages/new.png', b'new-image')])
    assert result['textures'] == ['StageImages/new.png', 'StageImages/saved.png']
    assert stale.texture_path(key, 'StageImages/saved.png').read_bytes() == b'original-image'
    assert stale.texture_path(key, 'StageImages/new.png').read_bytes() == b'new-image'


def test_preserving_textures_merges_replacements_and_checks_the_combined_limit(store, monkeypatch):
    store.save('merge', _zip(), [('StageImages/keep.png', b'keep'), ('StageImages/edit.png', b'old')])
    store.save('merge', _zip('new'), [('StageImages/edit.png', b'new')], preserve_textures=True)
    assert store.texture_path('merge', 'StageImages/keep.png').read_bytes() == b'keep'
    assert store.texture_path('merge', 'StageImages/edit.png').read_bytes() == b'new'
    monkeypatch.setattr(store_module, 'MAX_TEXTURE_TOTAL_BYTES', 7)
    with pytest.raises(EmoteTemplateStoreError, match='总大小超过上限'):
        store.save('merge', _zip('oversized'), [('StageImages/new.png', b'x')], preserve_textures=True)
    assert store.archive_path('merge').read_bytes() == _zip('new')
    assert store.texture_paths('merge') == ['StageImages/edit.png', 'StageImages/keep.png']


@pytest.mark.parametrize('reopen_first', [False, True])
def test_published_retired_copy_cannot_resurrect_after_delete(store, reopen_first):
    key = 'published'
    store.save(key, _zip('old'), [('StageImages/old.png', b'old-image')], filename='old.zip')
    retired = store.root / '.retired-after-publish'
    store_module.shutil.copytree(store._entry_dir(key), retired)
    store.save(key, _zip('new'), filename='new.zip')
    assert retired.is_dir()
    if reopen_first:
        store = EmoteTemplateArchiveStore(store.root)
        assert not retired.exists()
        assert store.archive_path(key).read_bytes() == _zip('new')
    assert store.delete(key)['freedBytes'] > 0
    reopened = EmoteTemplateArchiveStore(store.root)
    assert not reopened.has(key)
    assert not retired.exists()


def test_recovery_chooses_the_newest_candidate_before_retiring_old_copies(store):
    key = 'recover-newest'
    store.save(key, _zip('old'))
    store_module.shutil.copytree(store._entry_dir(key), store.root / '.retired-1000-old')
    store.save(key, _zip('new'))
    store._entry_dir(key).rename(store.root / '.retired-2000-new')
    reopened = EmoteTemplateArchiveStore(store.root)
    assert reopened.archive_path(key).read_bytes() == _zip('new')
    assert not list(store.root.glob('.retired-*'))


@pytest.mark.parametrize('published', [False, True])
def test_inaccessible_recovery_candidate_does_not_block_the_library(store, monkeypatch, capsys, published):
    store.save('healthy', _zip('healthy'))
    store.save('recovering', _zip('old'))
    oldest = store.root / '.retired-1000-old'
    store_module.shutil.copytree(store._entry_dir('recovering'), oldest)
    store.save('recovering', _zip('new'))
    latest = store.root / '.retired-2000-new'
    store._entry_dir('recovering').rename(latest)
    if published:
        store_module.shutil.copytree(latest, store._entry_dir('recovering'))
        (store._entry_dir('recovering') / store_module.ARCHIVE_NAME).write_bytes(_zip('published'))
    original = store_module.shutil.rmtree if published else store_module.os.replace

    def unavailable(path, *args, **kwargs):
        if Path(path) == latest:
            raise PermissionError('temporarily locked')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if published:
            patch.setattr(store_module.shutil, 'rmtree', unavailable)
        else:
            patch.setattr(store_module.os, 'replace', unavailable)
        reopened = EmoteTemplateArchiveStore(store.root)
        expected = {'healthy', 'recovering'} if published else {'healthy'}
        assert {entry['key'] for entry in reopened.list()} == expected
        assert reopened.archive_path('healthy').read_bytes() == _zip('healthy')
        assert reopened.delete('healthy')['freedBytes'] > 0
        if published:
            with pytest.raises(EmoteTemplateStoreError, match='删除未完成'):
                reopened.delete('recovering')
            assert reopened.has('recovering')
        else:
            assert not reopened.has('recovering')
        assert latest.is_dir()
    assert '[emote-template]' in capsys.readouterr().out
    repaired = EmoteTemplateArchiveStore(store.root)
    assert repaired.archive_path('recovering').read_bytes() == _zip('published' if published else 'new')
    assert not list(store.root.glob('.retired-*'))


def test_stale_store_rechecks_recovery_after_another_writer_fails(store, monkeypatch):
    key = 'stale'
    store.save(key, _zip(), [('StageImages/saved.png', b'original-image')])
    stale = EmoteTemplateArchiveStore(store.root)
    original = store_module.os.replace
    final = store._entry_dir(key)

    def fail_publish_and_rollback(source, target):
        if Path(target) == final:
            raise PermissionError('publication and rollback are locked')
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(store_module.os, 'replace', fail_publish_and_rollback)
        with pytest.raises(EmoteTemplateStoreError, match='副本写入失败'):
            store.save(key, _zip('replacement'))
        assert not stale.has(key)
        assert key not in stale._recovery_blocked_keys
        for write in (
            lambda: stale.save(key, _zip('reimport')),
            lambda: stale.save_textures(key, [('StageImages/new.png', b'new')]),
        ):
            with pytest.raises(EmoteTemplateStoreError, match='尚未恢复'):
                write()
        stale.save('healthy', _zip('unrelated'))
        assert stale.has('healthy')
    repaired = EmoteTemplateArchiveStore(store.root)
    assert repaired.archive_path(key).read_bytes() == _zip()
    assert repaired.texture_path(key, 'StageImages/saved.png').read_bytes() == b'original-image'


def test_locked_recovery_rejects_reimport_without_losing_saved_images(store, monkeypatch):
    key = 'recovering'
    store.save(key, _zip(), [('StageImages/saved.png', b'original-image')])
    retired = store.root / '.retired-locked'
    store._entry_dir(key).rename(retired)
    original = store_module.os.replace

    def locked(source, target):
        if Path(source) == retired:
            raise PermissionError('locked')
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(store_module.os, 'replace', locked)
        reopened = EmoteTemplateArchiveStore(store.root)
        assert not reopened.has(key)
        for write in (
            lambda: reopened.save(key, _zip('reimport')),
            lambda: reopened.save_textures(key, [('StageImages/new.png', b'new')]),
            lambda: reopened.remove_texture(key, 'StageImages/saved.png'),
        ):
            with pytest.raises(EmoteTemplateStoreError, match='尚未恢复'):
                write()
        reopened.save('unrelated', _zip())
        assert reopened.has('unrelated')
        assert (retired / 'textures/StageImages/saved.png').read_bytes() == b'original-image'
    repaired = EmoteTemplateArchiveStore(store.root)
    assert repaired.texture_path(key, 'StageImages/saved.png').read_bytes() == b'original-image'
    assert repaired.archive_path(key).read_bytes() == _zip()


@pytest.mark.parametrize('failure', ['write', 'publish', None])
def test_texture_batch_is_all_or_nothing_and_keeps_unmodified_images(store, monkeypatch, failure):
    originals = [('StageImages/a.png', b'old-a'), ('StageImages/b.png', b'old-b'),
                 ('StageImages/unchanged.png', b'untouched')]
    store.save('batch', _zip(), originals)
    original_write = Path.write_bytes
    original_replace = store_module.os.replace

    def write(path, data):
        if path.name == 'b.png' and any(p.startswith('.uploading-') for p in path.parts):
            raise OSError('disk full')
        return original_write(path, data)

    def replace(source, target):
        if Path(source).name.startswith('.uploading-'):
            raise OSError('publication failed')
        return original_replace(source, target)

    if failure == 'write':
        monkeypatch.setattr(Path, 'write_bytes', write)
    elif failure == 'publish':
        monkeypatch.setattr(store_module.os, 'replace', replace)
    changed = [('StageImages/a.png', b'new-a'), ('StageImages/b.png', b'new-b')]
    if failure:
        with pytest.raises(EmoteTemplateStoreError, match='贴图写入失败'):
            store.save_textures('batch', changed)
    else:
        store.save_textures('batch', changed)
    reopened = EmoteTemplateArchiveStore(store.root)
    expected = dict(originals) | (dict(changed) if failure is None else {})
    assert {path: reopened.texture_path('batch', path).read_bytes() for path in reopened.texture_paths('batch')} == expected
    assert reopened.archive_path('batch').read_bytes() == _zip()
    assert not list(store.root.glob('.uploading-*'))


@pytest.mark.parametrize('hardlinks', [False, True])
def test_texture_staging_shares_unchanged_files_and_detaches_replacements(store, monkeypatch, hardlinks):
    originals = [('StageImages/change.png', b'original'), ('StageImages/keep.png', b'unchanged')]
    store.save('linked', _zip(), originals)
    final = store._entry_dir('linked')
    publish = store._publish_copy
    if hardlinks:
        probe = store.root / '.hardlink-probe'
        try:
            store_module.os.link(final / store_module.ARCHIVE_NAME, probe)
        except OSError:
            pytest.skip('temporary filesystem does not support hardlinks')
        finally:
            probe.unlink(missing_ok=True)
    if not hardlinks:
        def unavailable(*args, **kwargs):
            raise OSError('hardlinks unsupported')
        monkeypatch.setattr(store_module.os, 'link', unavailable)

    def inspect_and_publish(key, staging):
        for relative in ('template.zip', 'textures/StageImages/keep.png'):
            assert store_module.os.path.samefile(final / relative, staging / relative) == hardlinks
        changed = 'textures/StageImages/change.png'
        assert not store_module.os.path.samefile(final / changed, staging / changed)
        assert (final / changed).read_bytes() == b'original'
        assert (staging / changed).read_bytes() == b'replacement'
        publish(key, staging)

    monkeypatch.setattr(store, '_publish_copy', inspect_and_publish)
    store.save_textures('linked', [('StageImages/change.png', b'replacement')])
    assert store.texture_path('linked', 'StageImages/keep.png').read_bytes() == b'unchanged'
    assert store.texture_path('linked', 'StageImages/change.png').read_bytes() == b'replacement'


def test_recovery_cleans_expired_staging_but_keeps_recent_and_active_copies(store, monkeypatch):
    now = store_module.time.time()
    old_time = now - store_module.MAX_STAGING_AGE_SECONDS - 60
    store.root.mkdir(parents=True, exist_ok=True)
    old = store.root / f'.uploading-{int(old_time * 1000)}-a1'
    recent = store.root / f'.uploading-{int(now * 1000)}-a2'
    for path in (old, recent):
        path.mkdir()
        (path / 'template.zip').write_bytes(b'unpublished')
        # copytree can preserve an old directory mtime even for a new staging copy.
        store_module.os.utime(path, (old_time, old_time))
    with monkeypatch.context() as patch:
        patch.setattr(store_module.time, 'time', lambda: old_time)
        active = store._new_staging()
    active.mkdir()
    (active / 'template.zip').write_bytes(b'still-writing')
    store_module.os.utime(active, (old_time, old_time))
    try:
        EmoteTemplateArchiveStore(store.root)
        assert not old.exists()
        assert recent.is_dir()
        assert (active / 'template.zip').read_bytes() == b'still-writing'
    finally:
        store._discard_staging(active)
    assert active.resolve() not in store_module._active_staging


def test_discarding_staging_does_not_block_recovery_of_other_templates(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    store.save('healthy', _zip())
    staging = store._new_staging()
    staging.mkdir()
    entered, release = Event(), Event()
    original = store_module.shutil.rmtree

    def slow_delete(path, *args, **kwargs):
        if Path(path) == staging:
            entered.set()
            assert release.wait(5), 'test did not release staging cleanup'
        return original(path, *args, **kwargs)

    monkeypatch.setattr(store_module.shutil, 'rmtree', slow_delete)
    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(store._discard_staging, staging)
        try:
            assert entered.wait(2)
            reopened = executor.submit(EmoteTemplateArchiveStore, store.root).result(timeout=2)
            assert reopened.has('healthy')
            assert staging.resolve() in store_module._active_staging
        finally:
            release.set()
        cleanup.result(timeout=2)
    assert not staging.exists()
    assert staging.resolve() not in store_module._active_staging


def test_reopening_during_an_unlocked_archive_write_keeps_its_staging(store, monkeypatch):
    original_write = Path.write_bytes
    now = store_module.time.time()
    observed = []

    def write(path, data):
        result = original_write(path, data)
        if path.name == store_module.ARCHIVE_NAME and path.parent.name.startswith('.uploading-'):
            with monkeypatch.context() as patch:
                patch.setattr(store_module.time, 'time', lambda: now + 2 * store_module.MAX_STAGING_AGE_SECONDS)
                EmoteTemplateArchiveStore(store.root)
                observed.append(path.exists())
        return result

    monkeypatch.setattr(Path, 'write_bytes', write)
    store.save('writing', _zip())
    assert observed == [True]
    assert store.archive_path('writing').read_bytes() == _zip()
    assert not any(path.parent == store.root.resolve() for path in store_module._active_staging)


def test_texture_merge_rejects_case_conflicts_without_losing_existing_files(store):
    store.save('case', _zip(), [('StageImages/A.png', b'first')])
    with pytest.raises(EmoteTemplateStoreError, match='大小写冲突'):
        store.save_textures('case', [('StageImages/a.png', b'second')])
    assert store.texture_path('case', 'StageImages/A.png').read_bytes() == b'first'
    store.save_textures('case', [('StageImages/A.png', b'replacement')])
    assert store.texture_path('case', 'StageImages/A.png').read_bytes() == b'replacement'
    with pytest.raises(EmoteTemplateStoreError, match='大小写冲突'):
        store.save('case', _zip('new'), [('StageImages/A.png', b'a'), ('StageImages/a.png', b'b')])
    assert store.archive_path('case').read_bytes() == _zip()


@pytest.mark.parametrize('stray_path', ['Thumbs.db', 'StageImages/tmpXXXX', 'WardrobeSprites/part.png.tmp'])
def test_unrelated_texture_directory_files_do_not_block_appending(store, stray_path):
    store.save('with-cache', _zip(), [('StageImages/one.png', b'first')])
    stray = store._entry_dir('with-cache') / 'textures' / stray_path
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b'cache')
    store.save_textures('with-cache', [('StageImages/two.png', b'second')])
    assert store.texture_paths('with-cache') == ['StageImages/one.png', 'StageImages/two.png']
    assert store.texture_path('with-cache', 'StageImages/two.png').read_bytes() == b'second'
    assert stray.read_bytes() == b'cache'


@pytest.mark.parametrize('limit', ['count', 'bytes'])
def test_texture_merge_checks_the_combined_saved_inventory(store, monkeypatch, limit):
    store.save('quota', _zip(), [('StageImages/one.png', b'1234')])
    monkeypatch.setattr(store_module, 'MAX_TEXTURE_FILES' if limit == 'count' else 'MAX_TEXTURE_TOTAL_BYTES',
                        1 if limit == 'count' else 5)
    with pytest.raises(EmoteTemplateStoreError, match='上限'):
        store.save_textures('quota', [('StageImages/two.png', b'5678')])
    assert store.texture_paths('quota') == ['StageImages/one.png']


def test_resaving_the_same_key_replaces_instead_of_piling_up(store):
    key = "pkg::model::3::3"
    store.save(key, _zip("old"), [("Texture Cache/a.png", b"old-tex")])
    store.save(key, _zip("new-content-here"), [])
    listed = store.list()
    assert len(listed) == 1, "同键再导必须原地替换，不能攒出第二份副本"
    assert store.archive_path(key).read_bytes() == _zip("new-content-here")
    # 替换就是替换：没随新包带来的贴图不保留（原件语义，没有合并）
    assert store.texture_paths(key) == []


def test_two_keys_never_share_a_directory_even_with_slashes(store, tmp_path):
    weird = "a/b::c d::1::2"
    other = "a/b::c d::1::3"
    store.save(weird, _zip("w"))
    store.save(other, _zip("o"))
    assert len(store.list()) == 2
    # 键里的 `/` 不许变成目录层级；磁盘名是 sha256
    assert dir_key_for(weird) == dir_key_for(weird)
    assert dir_key_for(weird) != dir_key_for(other)
    assert "/" not in dir_key_for(weird)
    assert store.archive_path(weird).read_bytes() == _zip("w")


def test_texture_paths_are_sandboxed(store):
    key = "k"
    store.save(key, _zip(), [("Texture Cache/Custom Textures/x.png", b"png")])
    assert store.texture_path(key, "Texture Cache/Custom Textures/x.png").read_bytes() == b"png"

    for evil in (
        "../escape.png",
        "a/../../escape.png",
        "/abs.png",
        "C:/abs.png",
        "ok.txt",
        "",
    ):
        with pytest.raises(EmoteTemplateStoreError):
            store.texture_path(key, evil)


def test_save_rejects_bad_input(store, monkeypatch):
    with pytest.raises(EmoteTemplateStoreError):
        store.save("", _zip())
    with pytest.raises(EmoteTemplateStoreError):
        store.save("k", b"")
    with pytest.raises(EmoteTemplateStoreError):
        store.save("k", _zip(), [("../evil.png", b"x")])
    monkeypatch.setattr(store_module, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(EmoteTemplateStoreError):
        store.save("k", _zip())
    # 超限的贴图同理
    monkeypatch.setattr(store_module, "MAX_ARCHIVE_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(store_module, "MAX_TEXTURE_BYTES", 2)
    with pytest.raises(EmoteTemplateStoreError):
        store.save("k", _zip(), [("a.png", b"xxxx")])


def test_staging_leftovers_are_invisible(store):
    store.save("k", _zip())
    junk = store.root / ".uploading-crashed"
    junk.mkdir(parents=True)
    (junk / "template.zip").write_bytes(_zip())
    assert len(store.list()) == 1


# ── 路由层：multipart 对齐 + 错误码 ─────────────────────────────────────────
@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main_routers.emote_template_router as router_module

    app = FastAPI()
    app.include_router(router_module.router)
    monkeypatch.setattr(
        router_module, "_template_library_directory",
        lambda: tmp_path / "emote_templates",
    )
    return TestClient(app, client=("127.0.0.1", 40000), headers={"x-neko-emote-library": "1"})


@pytest.mark.parametrize('over_count', [False, True])
def test_texture_upload_checks_count_and_running_total_before_retaining_bytes(monkeypatch, over_count):
    import asyncio
    from fastapi import Request, UploadFile
    import main_routers.emote_template_router as routes

    uploads = [UploadFile(io.BytesIO(b'abcd'), filename=f'{i}.png') for i in range(3)]
    reads = [0, 0, 0]
    for index, upload in enumerate(uploads):
        original = upload.read

        async def read(size, index=index, original=original):
            result = await original(size)
            reads[index] += len(result)
            return result

        monkeypatch.setattr(upload, 'read', read)
    monkeypatch.setattr(routes, 'MAX_TEXTURE_FILES', 2 if over_count else 10)
    monkeypatch.setattr(routes, 'MAX_TEXTURE_TOTAL_BYTES', 5)
    monkeypatch.setattr(routes, '_merge_library_textures', lambda *args: pytest.fail('oversized upload was persisted'))
    request = Request({'type': 'http', 'headers': [(b'x-neko-emote-library', b'1')]})
    response = asyncio.run(routes.merge_template_library_textures(
        request, key='test', textures=uploads,
        texture_paths=[f'StageImages/{i}.png' for i in range(3)],
    ))
    assert response.status_code == 400
    assert reads == ([0, 0, 0] if over_count else [4, 2, 0])
    assert all(upload.file.closed for upload in uploads)


def test_wardrobe_directory_does_not_wait_for_template_recovery(monkeypatch, tmp_path):
    import main_routers.emote_template_router as routes

    monkeypatch.setattr(routes, '_template_library_directory', lambda: tmp_path)
    monkeypatch.setattr(routes, '_archive_store', lambda: pytest.fail('wardrobe lookup attempted recovery'))
    assert routes._wardrobe_sheet_directory() == tmp_path / '.wardrobe-sheets'


def test_texture_read_waits_for_publication_and_owns_a_snapshot(store, monkeypatch):
    import asyncio
    import threading
    import main_routers.emote_template_router as routes

    store.save('read', _zip(), [('StageImages/a.png', b'original')])
    moved, release = threading.Event(), threading.Event()
    monkeypatch.setattr(routes, '_archive_store', lambda: store)

    async def scenario():
        lock = asyncio.Lock()
        monkeypatch.setattr(routes, '_library_storage_lock', lock)

        def publish():
            final, retired = store._entry_dir('read'), store.root / '.retired-test'
            final.rename(retired)
            moved.set()
            try:
                assert release.wait(5)
            finally:
                retired.rename(final)

        write = asyncio.create_task(routes._storage_transaction(lock, publish))
        try:
            assert await asyncio.to_thread(moved.wait, 5)
            read = asyncio.create_task(routes.open_template_library_texture('read', 'StageImages/a.png'))
            await asyncio.sleep(.02)
            assert not read.done(), 'read entered the directory publication gap'
        finally:
            release.set()
        await write
        response = await read
        store.save_textures('read', [('StageImages/a.png', b'replaced')])
        assert response.body == b'original'
        assert response.media_type == 'image/png'

    asyncio.run(scenario())


@pytest.mark.parametrize('known_length', [False, True])
def test_template_upload_body_is_capped_before_route_processing(monkeypatch, known_length):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from utils.asgi_body_limit import InboundBodySizeLimitMiddleware
    import main_routers.emote_template_router as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.add_middleware(InboundBodySizeLimitMiddleware,
                       multipart_path_prefix='/api/emote-maker', multipart_methods=('POST',),
                       max_multipart_body_bytes=128)
    monkeypatch.setattr(routes, '_archive_store', lambda: pytest.fail('oversized request reached the route'))
    body = b'--abc\r\nContent-Disposition: form-data; name="file"; filename="test.zip"\r\n\r\n' + b'x' * 256 + b'\r\n--abc--\r\n'
    headers = {'content-type': 'multipart/form-data; boundary=abc', 'x-neko-emote-library': '1'}
    if known_length:
        headers['content-length'] = str(len(body))
    with TestClient(app) as http:
        response = http.post('/api/emote-maker/library/archive?key=test',
                             headers=headers, content=iter([body[:100], body[100:]]))
    assert response.status_code == 413
    assert response.json()['error_code'] == 'payload_too_large'


def test_router_roundtrip_uses_our_names_and_self_contained_zip(client, tmp_path, monkeypatch):
    from tests.emote_fixtures import spine_zip
    from main_logic.emote_template_adapter import inspect_spine_zip
    monkeypatch.setenv("NEKO_EMOTE_TEMPLATE_DIR", str(tmp_path / "without-desktop-assets"))
    builtins = client.get("/api/emote-maker/builtins")
    assert builtins.status_code == 200
    assert builtins.json() == {"success": True, "packages": []}
    data = spine_zip()
    key = inspect_spine_zip(data)["id"]
    response = client.post("/api/emote-maker/library/archive", params={"key": key},
                           files={"file": ("external-name.zip", data, "application/zip")})
    assert response.status_code == 200, response.text
    assert response.json()["entry"]["key"] == key
    listed = client.get("/api/emote-maker/library").json()
    assert listed["totalBytes"] > 0
    archive = client.get("/api/emote-maker/library/archive", params={"key": key})
    assert archive.status_code == 200
    assert archive.headers["X-Archive-Filename"] == key + ".zip"
    assert inspect_spine_zip(archive.content)["id"] == key
    assert client.delete("/api/emote-maker/library", params={"key": key}).json()["freedBytes"] > 0


def test_builtin_assets_use_separate_immutable_source_and_keep_session_images(client, builtin_emote_template_dir, monkeypatch):
    monkeypatch.setenv("NEKO_EMOTE_TEMPLATE_DIR", str(builtin_emote_template_dir.parent))
    endpoint = "/api/emote-maker"
    catalog = client.get(endpoint + "/builtins").json()
    package = catalog["packages"][0]
    key = package["id"]
    assert key == "builtin:yui-0.2"
    assert package["model"]["displayName"] == "YUI 0.5"
    assert package["source"]["builtIn"] is True
    assert package["source"]["sceneAliases"] == {
        "animation:d_angry": "animation:d_angry2",
        "animation:d_surprise": "animation:d_surprise1",
    }
    slot_groups = package["catalogs"]["slotGroups"]
    assert package["counts"]["slotGroups"] == len(slot_groups)
    assert {part["id"] for part in slot_groups if part["kind"] == "toggle"} >= {"faka", "fashi_1"}
    assert any(part["id"] == "liuhai_1" and part["kind"] == "tint" for part in slot_groups)
    assert {part["id"] for part in slot_groups if part["kind"] == "tint"} >= {
        "yanjing_1", "yanjing_1_B", "yanjing_1_C",
    }
    assert package["counts"]["animations"] == 11
    archive = client.get(endpoint + "/library/archive", params={"key": key})
    assert archive.status_code == 200
    assert archive.headers["X-Archive-Filename"] == "yui-0.5.zip"
    assert client.get(endpoint + "/library").json()["entries"] == []
    ordinary = client.post(endpoint + "/import", files={"file": ("yui.zip", archive.content)})
    assert ordinary.json()["package"]["counts"]["slotGroups"] == 0
    assert client.delete(endpoint + "/library", params={"key": key}).status_code == 403

    written = client.post(endpoint + "/library/textures", params={"key": key},
                          files={"textures": ("note.png", b"session artwork")},
                          data={"texture_paths": "StageImages/note.png"})
    assert written.status_code == 200
    assert written.json()["textures"] == ["StageImages/note.png"]
    assert client.get(endpoint + "/library/archive", params={"key": key}).content == archive.content


def test_builtin_archive_keeps_existing_generated_texture_overrides(client, builtin_emote_template_dir):
    from PIL import Image
    from main_routers.emote_template_router import _archive_store, _builtin_texture_overrides

    wardrobe = _builtin_texture_overrides(builtin_emote_template_dir)
    assert len(wardrobe.parts) == 53
    assert wardrobe.parts["yanjing_1--yanjing_1_A"].attachment == "yanjing_1_A"
    part = wardrobe.parts["liuhai_1--liuhai_1"]
    recolored = Image.new("RGBA", part.original.size, "#1d79ca")
    recolored.putalpha(part.original.getchannel("A"))
    saved = io.BytesIO()
    recolored.save(saved, "PNG")
    legacy_eye_parts = {}
    for layer, old_size, new_size in (("B", (469, 47), (500, 55)), ("C", (500, 190), (447, 173))):
        name = f"yanjing_1_{layer}"
        eye = wardrobe.parts[f"{name}--{name}"]
        assert eye.original.size == new_size
        legacy = io.BytesIO()
        Image.new("RGBA", old_size, "#1d79ca").save(legacy, "PNG")
        legacy_eye_parts[eye.storage_path] = legacy.getvalue()
    key = "builtin:yui-0.2"
    store = _archive_store()
    store.save(key, wardrobe.archive, [(part.storage_path, saved.getvalue()), *legacy_eye_parts.items()], filename="yui-0.4.zip")
    response = client.get("/api/emote-maker/library/archive", params={"key": key})
    assert response.is_success
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        skeleton = json.loads(archive.read(wardrobe.skeleton_path))
        attachment = skeleton["skins"][0]["attachments"][part.slot][part.attachment]
        assert attachment["path"] == f"generated-{part.id}"
        assert archive.read(f"generated-{part.id}.png") == saved.getvalue()
        for layer in ("B", "C"):
            name = f"yanjing_1_{layer}"
            eye = skeleton["skins"][0]["attachments"][name][name]
            assert eye.get("path", name) == name
    for path, data in legacy_eye_parts.items():
        assert store.texture_path(key, path).read_bytes() == data


@pytest.mark.parametrize(("region_count", "model"), [
    (2, "qwen-image-2.1-pro"),
    (3, "qwen-image-2.1-pro"),
    (2, "qwen-image-edit"),
    (2, "wanx2.1-t2i-turbo"),
])
def test_wardrobe_spritesheet_sends_only_selected_regions_once(
    client, monkeypatch, region_count, model,
):
    import base64
    from types import SimpleNamespace

    from PIL import Image, ImageDraw, ImageFilter
    import main_routers.emote_template_router as routes
    from utils.image_generation import GeneratedImage, ImageResult

    class Config:
        async def aget_model_api_config(self, kind):
            if kind == "image":
                return {"enabled": True, "protocol": "dashscope", "model": model, "api_key": "mock"}
            return {"model": "test-vision", "api_key": "mock"}

    def encode(image):
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

    reference = Image.new("RGB", (100, 80), "red")
    reference.paste("blue", (10, 20, 40, 50))
    reference.paste("green", (60, 30, 80, 31))
    part = Image.new("RGB", (15, 15), "black")
    ImageDraw.Draw(part).ellipse((3, 3, 11, 11), fill="yellow")
    reference.paste(part.filter(ImageFilter.GaussianBlur(2.2)), (20, 55))
    sheet = Image.new("RGBA", (120, 80), (255, 0, 255, 1))
    draw = ImageDraw.Draw(sheet)
    if region_count == 3:
        draw.rectangle((12, 10, 36, 32), fill="blue")
        draw.rectangle((70, 10, 105, 32), fill="green")
        draw.rectangle((12, 47, 36, 72), fill="yellow")
    else:
        draw.rectangle((15, 15, 45, 60), fill="blue")
        draw.rectangle((70, 20, 105, 50), fill="green")
    raw_sheet = encode(sheet)
    calls = []
    vision_crops = []

    class Vision:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages):
            calls.append("vision")
            instruction = messages[-1].content[0]["text"]
            if model == "qwen-image-2.1-pro":
                assert "white padding" in instruction
            assert "Describe its shape, color, material" in instruction
            assert "discernible silhouette, main colors and large details" in instruction
            if region_count == 3:
                assert "For the crops numbered 3" in instruction
                assert "Omit lighting, surface texture, reflections" in instruction
            for item in messages[-1].content[1:]:
                prefix, data = item["image_url"]["url"].split(",", 1)
                assert prefix == "data:image/png;base64"
                vision_crops.append(base64.b64decode(data))
            inventory = "1. A blue ornament. 2. A green brooch."
            if region_count == 3:
                inventory += " 3. A yellow pin." + " Fine details." * 85
            return SimpleNamespace(content=inventory)

    async def create_vision(**options):
        return Vision()

    async def generate(request, *, config_manager):
        calls.append("image")
        assert request.size == "1536x1536"
        assert len(request.prompt) <= 2000
        assert len(vision_crops) == region_count
        if region_count == 3:
            assert request.reference_images[:2] == tuple(vision_crops[:2])
            assert request.reference_images[2] != vision_crops[2]
            with Image.open(io.BytesIO(request.reference_images[2])) as guide:
                assert guide.size == (15, 15)
                assert len(guide.convert("RGB").getcolors()) <= 3
        else:
            assert request.reference_images == tuple(vision_crops)
        if region_count == 3:
            assert "Cel animation redraw for these positions only: bottom-left." in request.prompt
            assert "one solid base color and one solid shadow color" in request.prompt
            assert "no gradients, metallic reflections, gloss" in request.prompt
            assert "Keep the source line weight and shading for the other objects." in request.prompt
        else:
            assert "Cel animation redraw" not in request.prompt
            assert "Preserve the reference design, proportions, orientation, colors, line weight and shading" in request.prompt
        assert "Never paste or enlarge input crops." in request.prompt
        assert "simplify unresolved details" in request.prompt
        with Image.open(io.BytesIO(vision_crops[0])) as unchanged:
            assert unchanged.size == (30, 30)
            assert unchanged.getcolors() == [(900, (0, 0, 255, 255))]
        with Image.open(io.BytesIO(vision_crops[1])) as crop:
            if model == "qwen-image-2.1-pro":
                assert "white padding" in request.prompt
                assert crop.size == (20, 2)
                assert set(crop.getcolors()) == {(20, (0, 128, 0, 255)), (20, (255, 255, 255, 255))}
            else:
                assert crop.size == (20, 1)
                assert crop.getcolors() == [(20, (0, 128, 0, 255))]
        assert "A blue ornament" in request.prompt
        if region_count == 2:
            assert "Two cells side by side" in request.prompt
            assert "Item 1: x 5–45%, y 8–92%." in request.prompt
            assert "Item 2: x 55–95%, y 8–92%." in request.prompt
        else:
            assert "Leave the bottom-right cell transparent." in request.prompt
            assert "Item 3: x 5–45%, y 55–95%." in request.prompt
        return ImageResult("qwen", model, GeneratedImage(data=raw_sheet))

    monkeypatch.setattr(routes, "get_config_manager", Config)
    monkeypatch.setattr(routes, "create_chat_llm_async", create_vision)
    monkeypatch.setattr(routes, "generate_image", generate)
    monkeypatch.setattr(routes, "atomic_write_bytes", lambda *_: pytest.fail("Unexpected write during generation"))
    wardrobe_url = "/api/emote-maker/builtin/wardrobe"
    info = client.get(wardrobe_url).json()
    assert info == {
        "success": True, "model": model,
        "spritesheetConfigurationCode": "image_edit_unsupported" if model == "wanx2.1-t2i-turbo" else "",
        "spritesheetRegionLimit": 3, "spritesheetCropAspectLimit": 10 if model == "qwen-image-2.1-pro" else None,
    }
    for method, path in (("POST", "generate"), ("GET", "guide"), ("PUT", "part"), ("PUT", "set"), ("DELETE", "part")):
        assert client.request(method, f"{wardrobe_url}/{path}").status_code == 404
    endpoint = wardrobe_url + "/generate-spritesheet"
    files = {"reference": ("reference.png", encode(reference), "image/png")}
    headers = {"x-neko-emote-wardrobe": "1"}
    boxes = [[.1, .25, .4, .625], [.6, .375, .8, .3875]]
    if region_count == 3:
        boxes.append([.2, .6875, .35, .875])
    assert client.post(endpoint, files=files).status_code == 403
    for selection in ([], boxes * 2):
        rejected = client.post(endpoint, headers=headers, files=files, data={"annotation": json.dumps(selection)})
        assert rejected.json()["code"] == "invalid_spritesheet_regions"
    assert calls == []

    data = {"annotation": json.dumps(boxes)}
    generated = client.post(endpoint, headers=headers, files=files, data=data)
    if model == "wanx2.1-t2i-turbo":
        assert generated.status_code == 400 and generated.json()["code"] == "image_edit_unsupported"
        assert calls == []
        return
    assert generated.is_success, generated.text if not generated.is_success else ""
    assert calls == ["vision", "image"]
    assert generated.headers["X-Wardrobe-Spritesheet-Layout"] == "separated"
    assert generated.headers["X-Wardrobe-Spritesheet-Padded"] == ("1" if model == "qwen-image-2.1-pro" else "0")
    with Image.open(io.BytesIO(generated.content)) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((30 if region_count == 3 else 20, 20)) == (0, 0, 255, 255)
        if region_count == 3:
            assert image.getpixel((90, 20)) == (0, 128, 0, 255)
            assert image.getpixel((30, 60)) == (255, 255, 0, 255)


def test_wardrobe_supplemental_prompt_is_only_for_one_crop(client, monkeypatch):
    from PIL import Image
    import main_routers.emote_template_router as routes
    from utils.image_generation import GeneratedImage, ImageResult, MAX_IMAGE_PROMPT_LENGTH

    async def configured(_manager):
        return "", "qwen-image-2.1-pro", "dashscope"

    async def describe(_manager, instruction, crops):
        assert len(crops) == 1
        assert "For the crops numbered 1" in instruction
        return "A narrow blue ribbon. " * 100

    result = io.BytesIO()
    sheet = Image.new("RGBA", (64, 64))
    sheet.paste("blue", (12, 12, 52, 52))
    sheet.save(result, "PNG")
    prompts = []

    async def generate(request, *, config_manager):
        prompts.append(request.prompt)
        assert len(request.reference_images) == 1
        return ImageResult("qwen", "qwen-image-2.1-pro", GeneratedImage(data=result.getvalue()))

    monkeypatch.setattr(routes, "get_config_manager", object)
    monkeypatch.setattr(routes, "_wardrobe_image_config", configured)
    monkeypatch.setattr(routes, "_read_wardrobe_appearance", describe)
    monkeypatch.setattr(routes, "generate_image", generate)
    monkeypatch.setattr("main_logic.emote_wardrobe._edge_sharpness", lambda _crop: 0.3)
    reference = io.BytesIO()
    Image.new("RGB", (100, 80), "blue").save(reference, "PNG")
    box = [.2, .2, .7, .213]
    endpoint = "/api/emote-maker/builtin/wardrobe/generate-spritesheet"
    details = "改成深蓝色，保留轮廓。".ljust(240, "深")

    def submit(boxes, user_prompt):
        return client.post(endpoint, headers={"x-neko-emote-wardrobe": "1"},
                           files={"reference": ("ref.png", reference.getvalue(), "image/png")},
                           data={"annotation": json.dumps(boxes), "supplemental_prompt": user_prompt})

    for boxes, user_prompt in (([box, box], details), ([box], details + "蓝")):
        response = submit(boxes, user_prompt)
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_wardrobe_prompt"
    assert not prompts

    response = submit([box], " " + details + " ")
    assert response.is_success
    prompt, = prompts
    assert details in prompt
    assert "One centered cell" in prompt and "Keep the layout and transparency" in prompt
    assert len(prompt) <= MAX_IMAGE_PROMPT_LENGTH


@pytest.mark.parametrize(("size", "expected", "padded"), [
    ((10, 101), (11, 101), 1),
    ((101, 10), (101, 11), 1),
    ((10, 100), (10, 100), 0),
    ((200, 2003), (129, 1280), 1),
])
def test_wardrobe_spritesheet_pads_narrow_crops_after_resizing(size, expected, padded):
    from PIL import Image
    from main_logic.emote_wardrobe import prepare_spritesheet_crops

    source = io.BytesIO()
    Image.new("RGB", size, "green").save(source, "PNG")
    (png,), count, _ = prepare_spritesheet_crops(source.getvalue(), ((0, 0, 1, 1),), aspect_limit=10)
    assert count == padded
    with Image.open(io.BytesIO(png)) as crop:
        assert crop.size == expected
        assert crop.getpixel((crop.width // 2, crop.height // 2)) == (0, 128, 0, 255)
        if padded:
            assert crop.getpixel((crop.width - 1, crop.height - 1)) == (255, 255, 255, 255)


@pytest.mark.parametrize("touching", [False, True])
def test_wardrobe_spritesheet_repacks_three_separated_sprites_and_keeps_touching_preview(touching):
    from PIL import Image, ImageDraw
    from main_logic.emote_wardrobe import prepare_spritesheet_result

    sheet = Image.new("RGBA", (240, 240))
    draw = ImageDraw.Draw(sheet)
    draw.rectangle((12, 44, 70, 193), fill="red")
    draw.rectangle((71 if touching else 78, 64, 145, 171), fill="green")
    draw.rectangle((151, 48, 229, 154), fill="blue")
    source = io.BytesIO()
    sheet.save(source, "PNG")

    png, separated = prepare_spritesheet_result(source.getvalue(), 3)
    with Image.open(io.BytesIO(png)) as result:
        assert separated is not touching
        if touching:
            assert result.tobytes() == sheet.tobytes()
        else:
            assert result.getpixel((60, 60))[:3] == (255, 0, 0)
            assert result.getpixel((180, 60))[:3] == (0, 128, 0)
            assert result.getpixel((60, 180))[:3] == (0, 0, 255)
            assert result.getpixel((180, 180))[3] == 0
            alpha = result.getchannel("A")
            assert alpha.crop((108, 0, 132, 240)).getbbox() is None
            assert alpha.crop((0, 108, 240, 132)).getbbox() is None


@pytest.mark.parametrize("corner_alpha", [5, 255])
def test_wardrobe_spritesheet_accepts_transparent_result_with_occupied_corner(corner_alpha):
    from PIL import Image
    from main_logic.emote_wardrobe import WardrobeError, prepare_spritesheet_result

    sheet = Image.new("RGBA", (80, 80), (163, 57, 207, 1))
    sheet.paste("red", (5, 10, 35, 35))
    sheet.paste("blue", (45, 10, 75, 35))
    sheet.putpixel((79, 0), (163, 57, 207, corner_alpha))
    source = io.BytesIO()
    sheet.save(source, "PNG")

    png, separated = prepare_spritesheet_result(source.getvalue(), 2)
    assert not separated
    with Image.open(io.BytesIO(png)) as result:
        assert result.getpixel((79, 0))[3] == corner_alpha
        assert result.getpixel((0, 40))[3] == 0
        assert result.getpixel((10, 20)) == (255, 0, 0, 255)

    opaque = io.BytesIO()
    Image.new("RGB", sheet.size, "#cc4433").save(opaque, "PNG")
    with pytest.raises(WardrobeError, match="invalid_spritesheet_result"):
        prepare_spritesheet_result(opaque.getvalue(), 2)


def test_wardrobe_reference_with_unconfigured_vision_stops_before_image_request(client, monkeypatch):
    from PIL import Image
    import main_routers.emote_template_router as routes

    class Config:
        async def aget_model_api_config(self, kind):
            if kind == "image":
                return {"enabled": True, "protocol": "dashscope", "model": "qwen-image-2.1-pro", "api_key": "mock"}
            assert kind == "vision"
            return {}

    reference = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(reference, "PNG")
    monkeypatch.setattr(routes, "get_config_manager", Config)
    monkeypatch.setattr(routes, "generate_image", lambda *args, **kwargs: pytest.fail("unexpected image request"))
    response = client.post(
        "/api/emote-maker/builtin/wardrobe/generate-spritesheet",
        headers={"x-neko-emote-wardrobe": "1"},
        data={"annotation": "[[0.1,0.1,0.9,0.9]]"},
        files={"reference": ("reference.png", reference.getvalue(), "image/png")},
    )
    assert response.json()["code"] == "wardrobe_vision_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,host", [
    ("qwen", "dashscope.aliyuncs.com"),
    ("qwen_intl", "dashscope-intl.aliyuncs.com"),
    ("custom", "example.com"),
])
async def test_wardrobe_vision_uses_same_region_for_configured_dashscope(provider, host):
    import main_routers.emote_template_router as routes

    class Config:
        async def aget_model_api_config(self, kind):
            if kind == "vision":
                return {"model": "free-vision-model", "api_key": "free-key"}
            assert kind == "image"
            return {"enabled": True, "provider": provider, "base_url": f"https://{host}", "api_key": "image-key"}

    selected = await routes._wardrobe_vision_config(Config())
    if provider == "custom":
        assert selected == {"model": "free-vision-model", "api_key": "free-key"}
        return
    assert selected["model"] == "qwen3.8-flash"
    assert selected["base_url"] == f"https://{host}/compatible-mode/v1"
    assert selected["api_key"] == "image-key"


def test_router_rejects_texture_escape_and_missing_archive(client):
    bad = client.post(
        "/api/emote-maker/library/archive",
        params={"key": "k"},
        files={
            "file": ("template.zip", _zip(), "application/zip"),
            "textures": ("x.png", b"png", "image/png"),
        },
        data={"texture_paths": "../evil.png"},
    )
    assert bad.status_code == 400

    missing = client.get("/api/emote-maker/library/archive", params={"key": "nope"})
    assert missing.status_code == 404


def test_content_ids_name_directories_and_keep_original_name_as_metadata(store):
    key = "spine-" + "a" * 64
    store.save(key, _zip(), filename="external-file.zip")
    assert store.archive_path(key).parent.name == key
    assert store.archive_path(key).name == "template.zip"
    assert store.archive_name(key) == key + ".zip"
    meta = __import__("json").loads((store.archive_path(key).parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["sourceName"] == "external-file.zip"


def test_reimport_keeps_session_artwork_and_rejects_external_model_textures(client, store):
    from tests.emote_fixtures import spine_zip
    from main_logic.emote_template_adapter import inspect_spine_zip
    data = spine_zip()
    key = inspect_spine_zip(data)["id"]
    endpoint = "/api/emote-maker/library/archive"
    assert client.post(endpoint, params={"key": key}, files={"file": ("one.zip", data)}).status_code == 200
    assert client.post("/api/emote-maker/library/textures", params={"key": key},
                       files={"textures": ("one.png", b"artwork")}, data={"texture_paths": "StageImages/one.png"}).status_code == 200
    assert client.post(endpoint, params={"key": key}, files={"file": ("renamed.zip", data)}).status_code == 200
    assert client.get("/api/emote-maker/library/textures", params={"key": key}).json()["paths"] == ["StageImages/one.png"]
    assert client.post("/api/emote-maker/library/textures", params={"key": key},
                       files={"textures": ("page.png", b"texture")}, data={"texture_paths": "outside/page.png"}).status_code == 400
    assert client.post("/api/emote-maker/library/textures", params={"key": key},
                       files={"textures": ("page.png", b"texture")}).status_code == 400
    saved_key = "previously-saved-template"
    store.save(saved_key, data)
    assert client.post("/api/emote-maker/library/textures", params={"key": saved_key},
                       files={"textures": ("page.png", b"texture")}, data={"texture_paths": "outside/page.png"}).status_code == 400
    assert client.post(endpoint, params={"key": key}, files={"file": ("one.rar", data)}).status_code == 400


def test_late_textures_merge_into_the_existing_copy(store):
    """Merge late textures by path, retain other files and reject uploads without a saved copy."""
    key = "k"
    store.save(key, _zip())
    with pytest.raises(EmoteTemplateStoreError):
        store.save_textures("no-copy-yet", [("a.png", b"x")])

    store.save_textures(key, [("Tex/a.png", b"one")])
    store.save_textures(key, [("Tex/a.png", b"two"), ("Tex/b.png", b"b")])
    assert store.texture_path(key, "Tex/a.png").read_bytes() == b"two"
    assert store.texture_path(key, "Tex/b.png").read_bytes() == b"b"

    with pytest.raises(EmoteTemplateStoreError):
        store.save_textures(key, [("../escape.png", b"x")])


def test_remove_texture_deletes_the_file(store):
    key = "k"
    store.save(key, _zip())
    store.save_textures(key, [("Tex/a.png", b"one"), ("Tex/b.png", b"two")])
    store.remove_texture(key, "Tex/a.png")
    assert store.texture_path(key, "Tex/b.png").read_bytes() == b"two"
    with pytest.raises(EmoteTemplateStoreError):
        store.texture_path(key, "Tex/a.png")
    # 不存在的文件删了不报错
    store.remove_texture(key, "Tex/ghost.png")


_EXPORT_HEADER = {"X-Neko-Emote-Export": "1"}


@pytest.mark.parametrize("peer,identity", [("198.51.100.10", None), ("127.0.0.1", "remote-session")])
@pytest.mark.parametrize("operation", ["folder", "file", "zip"])
def test_export_rejects_remote_peers_and_authenticated_remote_sessions(client, tmp_path, peer, identity, operation):
    from fastapi.testclient import TestClient

    folder = tmp_path / "existing"
    folder.mkdir()
    target = folder / "existing.png"
    target.write_bytes(b"original")

    async def remote_app(scope, receive, send):
        if identity:
            scope["neko.instance_identity"] = identity
        await client.app(scope, receive, send)

    with TestClient(remote_app, client=(peer, 40001)) as remote:
        payload = {"directory": str(tmp_path), "folder": "existing"}
        if operation == "zip":
            payload.update(names=["existing.png"], zip_name="existing.zip")
        if operation == "file":
            response = remote.post(
                "/api/emote-maker/export/file", headers=_EXPORT_HEADER,
                data={**payload, "name": "existing.png"},
                files={"file": ("existing.png", b"replacement", "image/png")},
            )
        else:
            response = remote.post(f"/api/emote-maker/export/{operation}", headers=_EXPORT_HEADER, json=payload)
    assert response.status_code == 403
    assert target.read_bytes() == b"original"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing"]


@pytest.mark.parametrize("operation", ["import", "library/archive"])
def test_archive_processing_runs_outside_the_event_loop(client, monkeypatch, operation):
    import asyncio
    import main_routers.emote_template_router as routes
    from tests.emote_fixtures import spine_zip

    archive = spine_zip()
    key = routes.inspect_spine_zip(archive)["id"]
    calls = []

    def track(name, function):
        def checked(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                on_event_loop = True
            except RuntimeError:
                on_event_loop = False
            calls.append((name, on_event_loop))
            return function(*args, **kwargs)
        return checked

    monkeypatch.setattr(routes, "inspect_spine_zip", track("inspect", routes.inspect_spine_zip))
    monkeypatch.setattr(routes, "spine_resource_archive", track("repack", routes.spine_resource_archive))
    monkeypatch.setattr(EmoteTemplateArchiveStore, "save", track("save", EmoteTemplateArchiveStore.save))
    response = client.post(
        f"/api/emote-maker/{operation}", params={"key": key},
        files={"file": ("template.zip", archive, "application/zip")},
    )
    assert response.status_code == 200, response.text
    expected = ["inspect"] if operation == "import" else ["inspect", "repack", "save"]
    assert [name for name, _ in calls] == expected
    assert not any(on_event_loop for _, on_event_loop in calls)


def test_export_folder_file_and_zip_land_beside_each_other(client, tmp_path: Path):
    parent = tmp_path / "picked"
    parent.mkdir()
    created = client.post(
        "/api/emote-maker/export/folder",
        headers=_EXPORT_HEADER,
        json={"directory": str(parent), "folder": "表情组"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["folder"] == "表情组"
    assert (parent / "表情组").is_dir()

    written = client.post(
        "/api/emote-maker/export/file",
        headers=_EXPORT_HEADER,
        data={"directory": body["directory"], "folder": body["folder"], "name": "01_笑.gif"},
        files={"file": ("01_笑.gif", b"GIF89a", "image/gif")},
    )
    assert written.status_code == 200
    assert (parent / "表情组" / "01_笑.gif").read_bytes() == b"GIF89a"

    again = client.post(
        "/api/emote-maker/export/folder",
        headers=_EXPORT_HEADER,
        json={"directory": str(parent), "folder": "表情组"},
    )
    assert again.json()["folder"] == "表情组 2"

    zipped = client.post(
        "/api/emote-maker/export/zip",
        headers=_EXPORT_HEADER,
        json={
            "directory": body["directory"],
            "folder": body["folder"],
            "names": ["01_笑.gif"],
            "zip_name": "表情组.zip",
        },
    )
    assert zipped.status_code == 200
    archive = parent / zipped.json()["zip"]
    assert archive.is_file()
    assert archive.parent == parent
    with zipfile.ZipFile(archive) as package:
        assert package.read("01_笑.gif") == b"GIF89a"


def test_export_write_rejects_a_missing_header_and_a_path_escape(client, tmp_path: Path):
    parent = tmp_path / "picked"
    parent.mkdir()
    naked = client.post(
        "/api/emote-maker/export/folder",
        json={"directory": str(parent), "folder": "组"},
    )
    assert naked.status_code == 403

    created = client.post(
        "/api/emote-maker/export/folder",
        headers=_EXPORT_HEADER,
        json={"directory": str(parent), "folder": "组"},
    ).json()
    escaped = client.post(
        "/api/emote-maker/export/file",
        headers=_EXPORT_HEADER,
        data={
            "directory": created["directory"],
            "folder": created["folder"],
            "name": "../outside.gif",
        },
        files={"file": ("../outside.gif", b"nope", "image/gif")},
    )
    assert escaped.status_code == 400
    assert not (parent / "outside.gif").exists()
    missing = client.post(
        "/api/emote-maker/export/zip",
        headers=_EXPORT_HEADER,
        json={
            "directory": created["directory"],
            "folder": created["folder"],
            "names": ["../outside.gif"],
            "zip_name": "组.zip",
        },
    )
    assert missing.status_code == 400
    assert list(parent.glob("*.zip")) == []


@pytest.mark.parametrize("resource", ["import", "library/archive", "library/textures", "library/textures/remove"])
def test_library_form_posts_require_the_editor_header(client, tmp_path, resource):
    from tests.emote_fixtures import spine_zip
    from main_logic.emote_template_adapter import inspect_spine_zip

    archive = spine_zip()
    key = inspect_spine_zip(archive)["id"]
    store = EmoteTemplateArchiveStore(tmp_path / "emote_templates")
    store.save(key, archive)
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    options = {"params": {"key": key}, "headers": {"x-neko-emote-library": "0"}}
    if resource in {"import", "library/archive"}:
        options["files"] = {"file": ("test.zip", archive, "application/zip")}
    else:
        options["data"] = {"texture_paths": "StageImages/one.png"}
    response = client.post("/api/emote-maker/" + resource, **options)
    assert response.status_code == 403
    after = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("operation", ["builtin-new", "builtin-existing", "builtin-remove", "custom", "custom-remove"])
def test_texture_storage_operations_run_outside_the_event_loop(client, tmp_path, monkeypatch, operation):
    import asyncio
    from PIL import Image
    import main_routers.emote_template_router as routes
    from tests.emote_fixtures import spine_zip

    archive = spine_zip()
    key = routes._BUILTIN_KEY if operation.startswith("builtin") else routes.inspect_spine_zip(archive)["id"]
    image = io.BytesIO()
    Image.new("RGBA", (2, 2), "white").save(image, "PNG")
    png = image.getvalue()
    store = EmoteTemplateArchiveStore(tmp_path / "emote_templates")
    if operation != "builtin-new":
        store.save(key, archive, [("StageImages/one.png", png)])
    monkeypatch.setattr(routes, "_builtin_template", lambda _: ({}, archive, {}))
    calls = []

    def tracked(name, function):
        def run(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            calls.append((name, on_loop))
            return function(*args, **kwargs)
        return run

    for name in ("has", "save", "save_textures", "texture_paths", "remove_texture"):
        monkeypatch.setattr(EmoteTemplateArchiveStore, name, tracked(name, getattr(EmoteTemplateArchiveStore, name)))
    options = {"params": {"key": key}, "data": {"texture_paths": "StageImages/one.png"}}
    resource = "library/textures"
    if operation.endswith("remove"):
        resource += "/remove"
    else:
        options["files"] = {"textures": ("one.png", png, "image/png")}
    response = client.post("/api/emote-maker/" + resource, **options)
    assert response.status_code == 200, response.text
    assert calls
    assert not any(on_loop for _, on_loop in calls), calls


@pytest.mark.asyncio
async def test_cancelled_storage_transaction_keeps_ownership_until_worker_finishes():
    import asyncio
    import threading
    from main_routers.emote_template_router import _storage_transaction

    entered, release = threading.Event(), threading.Event()
    lock = asyncio.Lock()
    order = []

    def first_write():
        order.append("first-start")
        entered.set()
        assert release.wait(5)
        order.append("first-finish")

    first = asyncio.create_task(_storage_transaction(lock, first_write))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        second = asyncio.create_task(_storage_transaction(lock, lambda: order.append("second")))
        await asyncio.sleep(.02)
        first.cancel()
        await asyncio.sleep(.02)
        assert lock.locked()
        assert order == ["first-start"]
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert order == ["first-start", "first-finish", "second"]


def test_long_export_folder_suffix_survives_followup_writes(client, tmp_path):
    name = 'g' * 80
    folders = []
    for content in (b'first', b'second'):
        response = client.post('/api/emote-maker/export/folder', headers=_EXPORT_HEADER,
            json={'directory': str(tmp_path), 'folder': name})
        assert response.status_code == 200
        folder = response.json()['folder']
        assert len(folder) <= 80
        folders.append(folder)
        written = client.post('/api/emote-maker/export/file', headers=_EXPORT_HEADER,
            data={'directory': str(tmp_path), 'folder': folder, 'name': 'one.gif'},
            files={'file': ('one.gif', content, 'image/gif')})
        assert written.status_code == 200
    assert folders[0] != folders[1]
    assert (tmp_path / folders[0] / 'one.gif').read_bytes() == b'first'
    assert (tmp_path / folders[1] / 'one.gif').read_bytes() == b'second'


@pytest.mark.asyncio
async def test_parallel_directory_zips_reserve_distinct_names(tmp_path, monkeypatch):
    import asyncio
    import threading
    import main_routers.emote_template_router as routes

    folder = tmp_path / 'group'
    folder.mkdir()
    (folder / 'one.png').write_bytes(b'original')
    barrier = threading.Barrier(2)
    local = threading.local()
    original = routes._unique_export_file

    def collide(*args):
        path = original(*args)
        if not getattr(local, 'waited', False):
            local.waited = True
            barrier.wait(5)
        return path

    monkeypatch.setattr(routes, '_unique_export_file', collide)
    body = routes.ExportZipBody(directory=str(tmp_path), folder='group', names=['one.png'], zip_name='group.zip')
    names = await asyncio.gather(*[asyncio.to_thread(routes._zip_export_folder, body) for _ in range(2)])
    assert len(set(names)) == 2
    for name in names:
        with zipfile.ZipFile(tmp_path / name) as archive:
            assert archive.read('one.png') == b'original'

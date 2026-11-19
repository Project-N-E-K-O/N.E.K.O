# -*- coding: utf-8 -*-
"""``/upload-reference-audio`` and ``/remove-reference-audio`` path guard.

Both routes may only touch a direct child of WorkshopExport (the ``item_xxx``
folder created by prepare-upload). A containment check alone
(``commonpath == root``) also accepts the root itself, which let these routes
write or delete the manifest and reference audio in the root; and on Windows
``commonpath`` raises ``ValueError`` for a path on another drive, which used
to escape as a 500 instead of a 403 (#3796).
"""

import json
import os

import pytest

from main_routers.workshop_router import voice_refs
from main_routers.workshop_router.voice_manifest import WORKSHOP_VOICE_MANIFEST_NAME


class _FakeUpload:
    filename = 'sample.wav'
    content_type = 'audio/wav'

    async def read(self) -> bytes:
        return b'RIFF-fake-wav'


class _FormRequest:
    def __init__(self, content_folder: str) -> None:
        self._form = {'file': _FakeUpload(), 'content_folder': content_folder}

    async def form(self) -> dict:
        return self._form


class _JsonRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


_ROOT_MANIFEST = {'version': 1, 'reference_audio': 'voice_sample.wav'}


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    """A WorkshopExport root with one item folder and a pair sitting in the root."""
    root = tmp_path / 'WorkshopExport'
    (root / 'item_aaa').mkdir(parents=True)
    # A pre-existing pair in the root: a guard that accepts the root would
    # overwrite or delete these.
    (root / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps(_ROOT_MANIFEST), encoding='utf-8'
    )
    (root / 'voice_sample.wav').write_bytes(b'root-audio')

    async def _workshop_path():
        return str(tmp_path)

    monkeypatch.setattr(voice_refs, 'get_workshop_path_async', _workshop_path)
    return root


def _assert_root_untouched(root):
    assert sorted(p.name for p in root.iterdir()) == sorted(
        ['item_aaa', WORKSHOP_VOICE_MANIFEST_NAME, 'voice_sample.wav']
    )
    assert json.loads(
        (root / WORKSHOP_VOICE_MANIFEST_NAME).read_text(encoding='utf-8')
    ) == _ROOT_MANIFEST
    assert (root / 'voice_sample.wav').read_bytes() == b'root-audio'


async def _upload(content_folder: str):
    response = await voice_refs.upload_reference_audio(_FormRequest(content_folder))
    return response.status_code, json.loads(response.body)


async def _remove(content_folder: str):
    response = await voice_refs.remove_reference_audio(
        _JsonRequest({'content_folder': content_folder})
    )
    return response.status_code, json.loads(response.body)


_ROUTES = pytest.mark.parametrize('call', [_upload, _remove], ids=['upload', 'remove'])


@_ROUTES
@pytest.mark.parametrize('suffix', ['', os.sep, os.sep + '.', os.sep + 'item_aaa' + os.sep + '..'])
async def test_root_and_its_aliases_are_rejected(export_root, call, suffix):
    status, body = await call(str(export_root) + suffix)

    assert status == 403, body
    assert body['success'] is False
    _assert_root_untouched(export_root)


@_ROUTES
async def test_rejection_does_not_echo_server_paths(export_root, tmp_path, call):
    status, body = await call(str(export_root))

    assert status == 403, body
    assert str(tmp_path) not in body['error']


@_ROUTES
async def test_outside_path_is_rejected(export_root, tmp_path, call):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()

    status, body = await call(str(outside))

    assert status == 403, body
    assert list(outside.iterdir()) == []
    _assert_root_untouched(export_root)


@_ROUTES
async def test_nested_path_is_rejected(export_root, call):
    nested = export_root / 'item_aaa' / 'sub'
    nested.mkdir()

    status, body = await call(str(nested))

    assert status == 403, body
    assert list(nested.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason='needs symlinks')
@_ROUTES
async def test_symlink_back_to_root_is_rejected(export_root, tmp_path, call):
    link = export_root / 'item_link'
    try:
        os.symlink(str(export_root), str(link), target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation not permitted')

    try:
        status, body = await call(str(link))

        assert status == 403, body
    finally:
        # Never leave a link back to the root behind, even on failure.
        link.unlink()
    _assert_root_untouched(export_root)


@_ROUTES
async def test_other_drive_is_403_not_500(export_root, tmp_path, monkeypatch, call):
    """On Windows ``commonpath`` raises ValueError across drives; that must stay a 403.

    This is a simulation (CI runs this on Linux): it proves the routes no
    longer depend on ``_assert_under_base`` / ``commonpath``, not real
    Windows drive handling. Only this module's reference is patched, so the
    shared ``os.path`` module stays untouched.
    """
    def _cross_drive(_path, _base):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(voice_refs, '_assert_under_base', _cross_drive)
    outside = tmp_path / 'other_drive'
    outside.mkdir()

    status, body = await call(str(outside))

    assert status == 403, body
    assert body['success'] is False
    assert list(outside.iterdir()) == []


async def test_upload_into_item_folder_still_works(export_root):
    item = export_root / 'item_aaa'

    status, body = await _upload(str(item))

    assert status == 200, body
    assert body['success'] is True
    manifest = json.loads((item / WORKSHOP_VOICE_MANIFEST_NAME).read_text(encoding='utf-8'))
    audio = item / manifest['reference_audio']
    assert audio.read_bytes() == b'RIFF-fake-wav'
    _assert_root_untouched(export_root)


async def test_remove_from_item_folder_still_works(export_root):
    item = export_root / 'item_aaa'
    status, body = await _upload(str(item))
    assert status == 200, body
    audio_name = body['manifest']['reference_audio']

    status, body = await _remove(str(item))

    assert status == 200, body
    assert body['success'] is True
    assert not (item / WORKSHOP_VOICE_MANIFEST_NAME).exists()
    assert not (item / audio_name).exists()
    _assert_root_untouched(export_root)


@pytest.mark.parametrize(
    'call, worker',
    [(_upload, '_replace_voice_reference'), (_remove, '_remove_voice_reference')],
    ids=['upload', 'remove'],
)
async def test_unexpected_error_does_not_echo_server_paths(
    export_root, tmp_path, monkeypatch, call, worker
):
    """An OSError message carries server paths; the 500 body must not."""
    item = export_root / 'item_aaa'

    def _boom(*_args, **_kwargs):
        raise OSError(f'[Errno 13] Permission denied: {item}')

    monkeypatch.setattr(voice_refs, worker, _boom)

    status, body = await call(str(item))

    assert status == 500, body
    assert body['success'] is False
    assert str(tmp_path) not in body['error']

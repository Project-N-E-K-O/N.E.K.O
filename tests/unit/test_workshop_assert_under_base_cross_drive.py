# -*- coding: utf-8 -*-
"""``_assert_under_base`` on a path that lives on another Windows drive.

On Windows ``os.path.commonpath`` raises ``ValueError("Paths don't have the
same drive")`` when the path and the base are on different drives (e.g. a
symlink resolving to another drive). Callers expect ``PermissionError`` for
"outside the base", so the cross-drive case must be reported exactly like an
ordinary escape. Linux cannot produce a real cross-drive pair, so these tests
make ``commonpath`` raise for paths under a marker directory.
"""

import json
import os
from pathlib import PurePath

import pytest

from main_routers.workshop_router import config_files, publish
from main_routers.workshop_router.voice_manifest import WORKSHOP_VOICE_MANIFEST_NAME

_OTHER_DRIVE = 'other_drive'
_real_commonpath = os.path.commonpath


def _fake_commonpath(paths):
    paths = list(paths)
    # Match a whole path component, so pytest's tmp dir names (derived from
    # test names) can never trigger it by accident.
    if any(_OTHER_DRIVE in PurePath(os.fspath(p)).parts for p in paths):
        raise ValueError("Paths don't have the same drive")
    return _real_commonpath(paths)


@pytest.fixture
def cross_drive(monkeypatch):
    monkeypatch.setattr(os.path, 'commonpath', _fake_commonpath)


@pytest.fixture
def workshop(tmp_path, monkeypatch):
    """A Workshop base with one item, plus a sibling folder standing in for another drive."""
    base = tmp_path / 'workshop'
    (base / 'item_aaa').mkdir(parents=True)
    (base / 'item_aaa' / 'card.chara.json').write_text('{}', encoding='utf-8')
    other = tmp_path / _OTHER_DRIVE
    other.mkdir()
    (other / 'card.chara.json').write_text('{}', encoding='utf-8')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'card.chara.json').write_text('{}', encoding='utf-8')

    async def _workshop_path():
        return str(base)

    monkeypatch.setattr(config_files, 'get_workshop_path_async', _workshop_path)
    monkeypatch.setattr(publish, 'get_workshop_path_async', _workshop_path)
    return tmp_path


# ── the helper itself ──────────────────────────────────────────────────


def test_cross_drive_is_reported_as_path_not_allowed(tmp_path, cross_drive):
    base = tmp_path / 'base'
    base.mkdir()
    with pytest.raises(PermissionError, match='path not allowed') as excinfo:
        config_files._assert_under_base(str(tmp_path / _OTHER_DRIVE / 'x.wav'), str(base))
    # The drive message carries no information callers need; do not chain it.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_cross_drive_matches_an_ordinary_escape(tmp_path, cross_drive):
    base = tmp_path / 'base'
    base.mkdir()
    with pytest.raises(PermissionError) as escape:
        config_files._assert_under_base(str(tmp_path / 'elsewhere' / 'x.wav'), str(base))
    with pytest.raises(PermissionError) as other_drive:
        config_files._assert_under_base(str(tmp_path / _OTHER_DRIVE / 'x.wav'), str(base))
    assert type(escape.value) is type(other_drive.value)
    assert str(escape.value) == str(other_drive.value)


def test_path_under_base_still_passes(tmp_path, cross_drive):
    base = tmp_path / 'base'
    (base / 'sub').mkdir(parents=True)
    target = base / 'sub' / 'x.wav'
    assert config_files._assert_under_base(str(target), str(base)) == os.path.realpath(target)


# ── /read-file, /list-chara-files, /list-audio-files ───────────────────


async def _call(route, arg):
    response = await route(arg)
    return response.status_code, json.loads(response.body)


@pytest.mark.parametrize(
    'route, leaf',
    [
        (config_files.read_workshop_file, 'card.chara.json'),
        (config_files.list_chara_files, ''),
        (config_files.list_audio_files, ''),
    ],
    ids=['read-file', 'list-chara-files', 'list-audio-files'],
)
@pytest.mark.asyncio
async def test_config_file_routes_answer_cross_drive_like_an_escape(
    workshop, cross_drive, route, leaf
):
    escape_path = str(workshop / 'outside' / leaf) if leaf else str(workshop / 'outside')
    other_path = str(workshop / _OTHER_DRIVE / leaf) if leaf else str(workshop / _OTHER_DRIVE)

    escape = await _call(route, escape_path)
    other = await _call(route, other_path)

    assert escape == other
    assert other[1]['success'] is False
    assert 'path not allowed' in other[1]['error']
    body = json.dumps(other[1], ensure_ascii=False)
    assert str(workshop) not in body
    assert "same drive" not in body

    # Sanity: the same route still serves a path inside the base.
    inside = str(workshop / 'workshop' / 'item_aaa' / leaf) if leaf else str(workshop / 'workshop' / 'item_aaa')
    status, payload = await _call(route, inside)
    assert status == 200 and payload['success'] is True


# ── /publish content_folder ────────────────────────────────────────────


class _JsonRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


async def _publish(content_folder: str):
    response = await publish.publish_to_workshop(_JsonRequest({
        'title': 't',
        'content_folder': content_folder,
        'visibility': 0,
    }))
    return response.status_code, json.loads(response.body)


@pytest.mark.asyncio
async def test_publish_rejects_cross_drive_content_folder_with_403(workshop, cross_drive, monkeypatch):
    monkeypatch.setattr(publish, 'get_steamworks', lambda: object())

    escape = await _publish(str(workshop / 'outside'))
    other = await _publish(str(workshop / _OTHER_DRIVE))

    assert escape[0] == 403
    assert other == escape
    body = json.dumps(other[1], ensure_ascii=False)
    assert str(workshop) not in body
    assert "same drive" not in body


# ── publish preflight: reference audio outside the content folder ──────


def _write_manifest(folder, reference: str) -> None:
    (folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({'version': 1, 'reference_audio': reference}), encoding='utf-8'
    )


def _never_publish(*_args, **_kwargs):
    raise AssertionError('publishing must not start')


@pytest.mark.parametrize('kind', ['escape', 'cross-drive'])
def test_publish_preflight_rejects_reference_outside_the_folder(tmp_path, cross_drive, monkeypatch, kind):
    folder = tmp_path / 'item'
    folder.mkdir()
    target_dir = tmp_path / ('outside' if kind == 'escape' else _OTHER_DRIVE)
    target_dir.mkdir()
    (target_dir / 'voice.wav').write_bytes(b'RIFF')
    # The manifest names a file inside the folder; realpath follows the
    # symlink out of it, like a reference pointing at another drive.
    try:
        os.symlink(target_dir / 'voice.wav', folder / 'voice.wav')
    except (OSError, NotImplementedError) as e:  # e.g. Windows without symlink privilege
        pytest.skip(f'symlinks unavailable: {e}')
    _write_manifest(folder, 'voice.wav')
    monkeypatch.setattr(publish, '_publish_workshop_item', _never_publish)

    with pytest.raises(publish._VoicePreflightError, match='path not allowed'):
        publish._preflight_and_publish(
            object(), 'title', 'description', str(folder), '', 0, [], 'note'
        )

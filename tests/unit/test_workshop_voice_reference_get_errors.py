# -*- coding: utf-8 -*-
"""Error responses of ``GET /voice-reference/{item_id}`` and ``.../audio``.

Reading the manifest wraps any failure, including an ``OSError`` whose
message holds the server's absolute path, into a ``ValueError``. Both routes
used to put ``str(e)`` into the response, so that path reached the client
(#3843). They now answer with fixed messages and keep the details in the log;
the status codes and the plain "not found" answers are unchanged.

The 503 text comes from ``GET /subscribed-items``, whose 500 branch used to
embed ``str(e)`` too. That route now only returns fixed strings, so the 503
can keep showing them (e.g. "Steamworks未初始化").
"""

import ast
import inspect
import json
import os
import textwrap
from types import SimpleNamespace

import pytest

from main_routers.workshop_router import ugc as wr_ugc
from main_routers.workshop_router import voice_refs
from main_routers.workshop_router.voice_manifest import WORKSHOP_VOICE_MANIFEST_NAME

_ITEM_ID = '123456'

_ROUTES = {
    'manifest': voice_refs.get_workshop_voice_reference,
    'audio': voice_refs.get_workshop_voice_reference_audio,
}


@pytest.fixture
def install_folder(tmp_path, monkeypatch):
    """A subscribed, installed item whose folder the test fills in."""
    folder = tmp_path / 'steamapps' / 'workshop' / 'content' / _ITEM_ID
    folder.mkdir(parents=True)

    async def _find(item_id):
        if str(item_id) != _ITEM_ID:
            return None
        return {'publishedFileId': _ITEM_ID, 'title': 't', 'installedFolder': str(folder)}

    monkeypatch.setattr(voice_refs, '_find_subscribed_item_by_id', _find)
    return folder


async def _call(route: str, item_id: str = _ITEM_ID):
    response = await _ROUTES[route](item_id)
    return response.status_code, response.body.decode('utf-8')


def _assert_no_path(body: str, path) -> None:
    # Check the raw body and the decoded JSON: json.dumps escapes non-ASCII
    # and backslashes, so one form alone could hide a leak.
    assert str(path) not in body
    assert str(path) not in json.dumps(json.loads(body), ensure_ascii=False)


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_unreadable_manifest_does_not_echo_server_path(install_folder, route):
    # A directory where the manifest should be: open() raises an OSError
    # (IsADirectoryError on POSIX, PermissionError on Windows) whose message
    # holds the absolute manifest path.
    manifest_path = install_folder / WORKSHOP_VOICE_MANIFEST_NAME
    manifest_path.mkdir()

    status, body = await _call(route)

    assert status == 400
    _assert_no_path(body, manifest_path)
    _assert_no_path(body, install_folder)
    assert json.loads(body)['error'] == '读取参考语音清单失败'


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_invalid_manifest_still_answers_400(install_folder, route):
    (install_folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text('not json', encoding='utf-8')

    status, body = await _call(route)

    assert status == 400
    assert json.loads(body)['success'] is False


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_missing_audio_is_still_404_without_path(install_folder, route):
    (install_folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({'version': 1, 'reference_audio': 'voice_sample.wav'}), encoding='utf-8'
    )

    status, body = await _call(route)

    assert status == 404
    _assert_no_path(body, install_folder)
    assert json.loads(body)['error'] == '参考语音文件不存在'


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_unknown_item_is_still_404(install_folder, route):
    status, body = await _call(route, item_id='999')

    assert status == 404
    assert json.loads(body)['error'] == '未找到对应的订阅工坊物品'


class _BrokenWorkshop:
    def __init__(self, secret) -> None:
        self._secret = secret

    def GetNumSubscribedItems(self):
        raise PermissionError(13, 'Permission denied', str(self._secret))


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_steam_not_running_is_still_explained(monkeypatch, route):
    monkeypatch.setattr(wr_ugc, 'get_steamworks', lambda: None)

    status, body = await _call(route)

    assert status == 503
    assert json.loads(body)['error'] == 'Steamworks未初始化'


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_subscription_lookup_failure_does_not_echo_server_path(
    tmp_path, monkeypatch, route
):
    secret = tmp_path / 'private' / 'steam_api.log'
    monkeypatch.setattr(
        wr_ugc, 'get_steamworks', lambda: SimpleNamespace(Workshop=_BrokenWorkshop(secret))
    )

    status, body = await _call(route)

    assert status == 503
    _assert_no_path(body, secret)
    assert json.loads(body)['error'] == '获取订阅物品失败'


async def test_subscribed_items_route_does_not_echo_server_path(tmp_path, monkeypatch):
    secret = tmp_path / 'private' / 'steam_api.log'
    monkeypatch.setattr(
        wr_ugc, 'get_steamworks', lambda: SimpleNamespace(Workshop=_BrokenWorkshop(secret))
    )

    response = await wr_ugc.get_subscribed_workshop_items()
    body = response.body.decode('utf-8')

    assert response.status_code == 500
    _assert_no_path(body, secret)
    assert json.loads(body)['error'] == '获取订阅物品失败'


def test_subscribed_items_route_only_returns_fixed_error_text():
    # The voice-reference routes pass SubscribedItemsUnavailable's message on
    # to the client; that is only safe while every "error" (and per-item
    # "error_message") this route builds is a literal. Scope: dict literals
    # only, which is how every response in this route is written today.
    tree = ast.parse(textwrap.dedent(inspect.getsource(wr_ugc.get_subscribed_workshop_items)))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in ('error', 'error_message'):
                checked += 1
                assert isinstance(value, ast.Constant), ast.unparse(value)
    assert checked >= 2


@pytest.mark.parametrize('route', sorted(_ROUTES))
async def test_audio_escaping_the_item_folder_is_400_without_path(install_folder, tmp_path, route):
    outside = tmp_path / 'outside.wav'
    outside.write_bytes(b'RIFF-fake-wav')
    try:
        os.symlink(outside, install_folder / 'voice_sample.wav')
    except (OSError, NotImplementedError):
        pytest.skip('symlinks are not available here')
    (install_folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({'version': 1, 'reference_audio': 'voice_sample.wav'}), encoding='utf-8'
    )

    status, body = await _call(route)

    assert status == 400
    _assert_no_path(body, outside)
    _assert_no_path(body, install_folder)
    assert json.loads(body)['error'] == '读取参考语音文件失败'


async def test_item_without_manifest_keeps_its_answers(install_folder):
    status, body = await _call('manifest')
    assert status == 200
    assert json.loads(body)['available'] is False

    status, body = await _call('audio')
    assert status == 404
    assert json.loads(body)['error'] == '该工坊物品没有参考语音'


async def test_valid_reference_is_still_served(install_folder):
    (install_folder / 'voice_sample.wav').write_bytes(b'RIFF-fake-wav')
    (install_folder / WORKSHOP_VOICE_MANIFEST_NAME).write_text(
        json.dumps({'version': 1, 'reference_audio': 'voice_sample.wav'}), encoding='utf-8'
    )

    status, body = await _call('manifest')
    assert status == 200
    data = json.loads(body)
    assert data['available'] is True
    assert data['manifest']['reference_audio'] == 'voice_sample.wav'

    response = await voice_refs.get_workshop_voice_reference_audio(_ITEM_ID)
    assert response.status_code == 200
    assert response.path.endswith('voice_sample.wav')

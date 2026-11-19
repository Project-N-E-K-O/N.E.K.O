# -*- coding: utf-8 -*-
"""``/cleanup-temp-folder`` may only delete a direct child of WorkshopExport.

A containment check alone (``commonpath == root``) also accepts the root
itself, and ``rmtree`` on the root wipes every exported item (#3796). These
tests pin the root, its aliases (trailing separator, Windows letter case,
symlinks, ``..``), paths outside, and nested paths as rejected, and the
ordinary ``item_xxx`` folder as still deleted.
"""

import json
import os

import pytest

from main_routers.workshop_router import publish


class _JsonRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    """A WorkshopExport root holding two exported items."""
    root = tmp_path / 'WorkshopExport'
    for name in ('item_aaa', 'item_bbb'):
        (root / name).mkdir(parents=True)
        (root / name / 'payload.txt').write_text(name, encoding='utf-8')

    async def _workshop_path():
        return str(tmp_path)

    monkeypatch.setattr(publish, 'get_workshop_path_async', _workshop_path)
    return root


async def _cleanup(temp_folder: str):
    response = await publish.cleanup_temp_folder(
        _JsonRequest({'temp_folder': temp_folder})
    )
    return response.status_code, json.loads(response.body)


def _assert_untouched(root):
    assert (root / 'item_aaa' / 'payload.txt').read_text(encoding='utf-8') == 'item_aaa'
    assert (root / 'item_bbb' / 'payload.txt').read_text(encoding='utf-8') == 'item_bbb'


async def test_root_itself_is_rejected(export_root):
    status, body = await _cleanup(str(export_root))

    assert status == 403, body
    assert body['success'] is False
    _assert_untouched(export_root)


async def test_rejection_does_not_echo_server_paths(export_root, tmp_path):
    status, body = await _cleanup(str(export_root))

    assert status == 403, body
    assert str(tmp_path) not in body['error'], '403 不许把服务端路径回给客户端'


async def test_plain_file_is_not_reported_as_deleted(export_root):
    """rmtree(ignore_errors=True) silently skips a file; that is not a 200."""
    stray = export_root / 'stray.txt'
    stray.write_text('x', encoding='utf-8')

    status, body = await _cleanup(str(stray))

    assert status == 500, body
    assert body['success'] is False
    assert stray.exists()
    _assert_untouched(export_root)


async def test_folder_that_survives_rmtree_is_reported_as_failed(
    export_root, monkeypatch
):
    """Simulate a locked file on Windows: rmtree gives up quietly."""
    monkeypatch.setattr(publish, '_delete_content_folder', lambda folder: None)

    status, body = await _cleanup(str(export_root / 'item_aaa'))

    assert status == 500, body
    assert body['success'] is False
    assert (export_root / 'item_aaa').exists()


@pytest.mark.parametrize('suffix', [os.sep, os.sep * 2, os.sep + '.'])
async def test_root_with_trailing_separator_is_rejected(export_root, suffix):
    status, body = await _cleanup(str(export_root) + suffix)

    assert status == 403, body
    _assert_untouched(export_root)


async def test_root_via_parent_traversal_is_rejected(export_root):
    status, body = await _cleanup(str(export_root / 'item_aaa' / '..'))

    assert status == 403, body
    _assert_untouched(export_root)


async def test_symlink_to_root_is_rejected(export_root):
    link = export_root / 'item_link'
    try:
        link.symlink_to(export_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not available on this platform')

    status, body = await _cleanup(str(link))

    assert status == 403, body
    _assert_untouched(export_root)


async def test_root_with_different_case_is_rejected_on_windows(export_root, monkeypatch):
    """Simulate Windows' case-insensitive comparison on any platform."""
    monkeypatch.setattr(publish, '_normcase', lambda p: p.lower())
    upper_root = os.path.join(str(export_root.parent), 'WORKSHOPEXPORT') + os.sep

    status, body = await _cleanup(upper_root)

    assert status == 403, body
    _assert_untouched(export_root)


def test_child_with_different_case_is_still_allowed_on_windows(export_root, monkeypatch):
    """Case-insensitive matching must not reject a legitimate child either."""
    monkeypatch.setattr(publish, '_normcase', lambda p: p.lower())
    child = os.path.join(str(export_root.parent), 'workshopexport', 'item_aaa')

    _, _, allowed = publish._resolve_workshop_export_child(
        child, str(export_root)
    )

    assert allowed


async def test_direct_child_is_deleted(export_root):
    status, body = await _cleanup(str(export_root / 'item_aaa'))

    assert status == 200, body
    assert body['success'] is True
    assert not (export_root / 'item_aaa').exists()
    assert (export_root / 'item_bbb' / 'payload.txt').exists(), '只能删掉指定的那一个'


async def test_direct_child_with_trailing_separator_is_deleted(export_root):
    status, body = await _cleanup(str(export_root / 'item_aaa') + os.sep)

    assert status == 200, body
    assert not (export_root / 'item_aaa').exists()
    assert (export_root / 'item_bbb').exists()


async def test_missing_direct_child_answers_404(export_root):
    status, body = await _cleanup(str(export_root / 'item_gone'))

    assert status == 404, body
    _assert_untouched(export_root)


async def test_path_outside_workshop_export_is_rejected(export_root, tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (outside / 'keep.txt').write_text('x', encoding='utf-8')

    status, body = await _cleanup(str(outside))

    assert status == 403, body
    assert (outside / 'keep.txt').exists()


async def test_parent_of_workshop_export_is_rejected(export_root, tmp_path):
    status, body = await _cleanup(str(tmp_path))

    assert status == 403, body
    _assert_untouched(export_root)


async def test_sibling_with_shared_prefix_is_rejected(export_root, tmp_path):
    sibling = tmp_path / 'WorkshopExportEvil' / 'item_x'
    sibling.mkdir(parents=True)

    status, body = await _cleanup(str(sibling))

    assert status == 403, body
    assert sibling.exists()


async def test_nested_path_below_an_item_is_rejected(export_root):
    nested = export_root / 'item_aaa' / 'sub'
    nested.mkdir()

    status, body = await _cleanup(str(nested))

    assert status == 403, body
    assert nested.exists()
    _assert_untouched(export_root)

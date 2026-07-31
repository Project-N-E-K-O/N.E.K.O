# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from main_routers.workshop_router import preview_cards
from main_routers.workshop_router.content_gate import (
    PUBLISH_PURPOSE,
    ContentFolderBusy,
    claim_content_folder,
    claim_reference_pair,
)

pytestmark = pytest.mark.unit


class _Upload:
    content_type = 'image/png'

    async def read(self) -> bytes:
        return b'new-preview-bytes'


class _Request:
    def __init__(self, content_folder: Path) -> None:
        self._content_folder = content_folder

    async def form(self) -> dict[str, object]:
        return {
            'file': _Upload(),
            'content_folder': str(self._content_folder),
        }


def test_upload_preview_image_replaces_atomically_from_a_worker(
    tmp_path, monkeypatch
):
    target = tmp_path / 'preview.png'
    target.write_bytes(b'old-complete-preview')
    event_loop_thread = threading.get_ident()
    observed: dict[str, object] = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        observed['thread'] = threading.get_ident()
        observed['target_before_replace'] = Path(dst).read_bytes()
        observed['staged_bytes'] = Path(src).read_bytes()
        return real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', spy_replace)

    response = asyncio.run(preview_cards.upload_preview_image(_Request(tmp_path)))

    assert response.status_code == 200
    assert json.loads(response.body)['success'] is True
    assert observed['target_before_replace'] == b'old-complete-preview'
    assert observed['staged_bytes'] == b'new-preview-bytes'
    assert observed['thread'] != event_loop_thread
    assert target.read_bytes() == b'new-preview-bytes'


def test_upload_preview_claim_survives_a_cancelled_waiter(
    tmp_path, monkeypatch
):
    writer_entered = threading.Event()
    release_writer = threading.Event()
    writer_finished = threading.Event()

    def blocking_write(_path, _content):
        writer_entered.set()
        try:
            assert release_writer.wait(timeout=2)
        finally:
            writer_finished.set()

    monkeypatch.setattr(preview_cards, 'atomic_write_bytes', blocking_write)

    async def scenario():
        task = asyncio.create_task(
            preview_cards.upload_preview_image(_Request(tmp_path))
        )
        try:
            for _ in range(200):
                if writer_entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert writer_entered.is_set()

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            with pytest.raises(ContentFolderBusy):
                with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
                    pass
        finally:
            task.cancel()
            release_writer.set()

        for _ in range(200):
            if writer_finished.is_set():
                break
            await asyncio.sleep(0.005)
        assert writer_finished.is_set()

        with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
            pass

    try:
        asyncio.run(scenario())
    finally:
        release_writer.set()


def test_upload_preview_shares_the_folder_with_reference_audio(tmp_path):
    with claim_reference_pair(str(tmp_path)):
        response = asyncio.run(
            preview_cards.upload_preview_image(_Request(tmp_path))
        )

    assert response.status_code == 200
    assert (tmp_path / 'preview.png').read_bytes() == b'new-preview-bytes'


def test_upload_preview_returns_409_while_the_folder_is_publishing(tmp_path):
    with claim_content_folder(str(tmp_path), purpose=PUBLISH_PURPOSE):
        response = asyncio.run(
            preview_cards.upload_preview_image(_Request(tmp_path))
        )

    payload = json.loads(response.body)
    assert response.status_code == 409
    assert payload['success'] is False
    assert '正在发布' in payload['error']
    assert payload['message'] == payload['error']
    assert not (tmp_path / 'preview.png').exists()


def test_upload_preview_does_not_recreate_a_folder_removed_before_claim(
    tmp_path, monkeypatch
):
    @contextmanager
    def cleanup_before_claim(content_folder, *, purpose):
        assert purpose == '上传预览图'
        shutil.rmtree(content_folder)
        yield

    monkeypatch.setattr(
        preview_cards,
        'claim_partial_writer',
        cleanup_before_claim,
    )

    response = asyncio.run(
        preview_cards.upload_preview_image(_Request(tmp_path))
    )

    payload = json.loads(response.body)
    assert response.status_code == 409
    assert payload['message'] == '内容目录已被清理，请重新开始上传'
    assert not tmp_path.exists()

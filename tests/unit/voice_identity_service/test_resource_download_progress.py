"""Byte progress is observed from real reads and stays owned by one resource task."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
from pathlib import Path
import time
from urllib.error import URLError

import psutil
import pytest

from main_logic.voice_identity_service import resource_manager as rm
from scripts import prepare_speaker_model as speaker
from scripts import prepare_voice_turn_assets as endpointing
from tests.fake_clock import patch_module_clock


class Response(io.BytesIO):
    def __init__(self, data, total=None):
        super().__init__(data)
        self.headers = {} if total is None else {"Content-Length": str(total)}

    def geturl(self):
        return "https://assets.invalid/fixed-model"


def test_speaker_progress_counts_written_chunks_and_separates_verification(tmp_path, monkeypatch):
    body = b"model" * 500_000
    monkeypatch.setattr(speaker.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(body))
    events = []
    target = tmp_path / "model.onnx"
    speaker._download_verified("https://assets.invalid/fixed-model", target, expected_size=len(body),
                              expected_sha256=hashlib.sha256(body).hexdigest(), on_progress=events.append)
    assert target.read_bytes() == body
    downloads = [event for event in events if event["phase"] == "downloading"]
    assert [event["downloaded_bytes"] for event in downloads] == [0, 1_048_576, 2_097_152, len(body)]
    assert all(event["total_bytes"] == len(body) and event["attempt"] == 1 for event in downloads)
    assert events[-1]["phase"] == "verifying"


@pytest.mark.parametrize("total", [None, "invalid", 0, -1, 10 ** 30, 2_500_000])
def test_silero_progress_only_has_percentage_when_the_size_is_known(tmp_path, monkeypatch, total):
    body = b"model" * 500_000
    monkeypatch.setattr(endpointing.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(body, total))
    events = []
    endpointing._download_verified("https://assets.invalid/fixed-model", tmp_path / "vad.onnx",
        hashlib.sha256(body).hexdigest(), maximum_bytes=16 * 1024 * 1024, on_progress=events.append)
    downloads = [event for event in events if event["phase"] == "downloading"]
    assert downloads[-1]["downloaded_bytes"] == len(body)
    assert downloads[-1]["total_bytes"] == (len(body) if total == len(body) else None)
    assert events[-1]["phase"] == "verifying"


def test_retry_resets_the_current_attempt_byte_count(tmp_path, monkeypatch):
    body = b"model" * 300_000
    class Interrupted(Response):
        def read(self, size):
            if self.tell():
                raise URLError("controlled disconnect")
            return super().read(size)
    streams = iter([Interrupted(body), Response(body)])
    monkeypatch.setattr(speaker.urllib.request, "urlopen", lambda *_args, **_kwargs: next(streams))
    patch_module_clock(monkeypatch, speaker, sleep=lambda _delay: None)
    events = []
    speaker._download_verified("https://assets.invalid/fixed-model", tmp_path / "model.onnx",
        expected_size=len(body), expected_sha256=hashlib.sha256(body).hexdigest(), on_progress=events.append)
    second = [event for event in events if event["attempt"] == 2 and event["phase"] == "downloading"]
    assert second[0]["downloaded_bytes"] == 0 and second[-1]["downloaded_bytes"] == len(body)
    assert any(event["attempt"] == 1 and event["downloaded_bytes"] > 0 for event in events)


def test_complete_transfer_is_not_a_success_when_its_checksum_fails(tmp_path, monkeypatch):
    body = b"corrupt model"
    monkeypatch.setattr(speaker.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(body))
    events = []
    target = tmp_path / "model.onnx"
    with pytest.raises(speaker.CampPlusAssetError, match="sha256"):
        speaker._download_verified("https://assets.invalid/fixed-model", target, expected_size=len(body),
            expected_sha256="0" * 64, on_progress=events.append)
    assert events[-1]["phase"] == "verifying"
    assert events[-2]["downloaded_bytes"] == events[-2]["total_bytes"]
    assert not target.exists()


@pytest.mark.parametrize("known_size", [True, False])
def test_wake_bundle_reports_real_archive_bytes_and_still_validates_before_publish(tmp_path, monkeypatch, known_size):
    from main_logic.voice_identity_service import wake_word_bundle as bundle
    from tests.unit.voice_identity_service.test_resource_operations import _archive
    archive = _archive(tmp_path)
    body = archive.read_bytes()
    monkeypatch.setattr(bundle, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(bundle.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(body, len(body) if known_size else None))
    events, validated = [], []
    target = bundle.install_bundle(tmp_path / "cache", publish=False, on_progress=events.append, validate=lambda path: validated.append(path))
    downloads = [event for event in events if event["phase"] == "downloading"]
    assert downloads[-1]["downloaded_bytes"] == len(body)
    assert downloads[-1]["total_bytes"] == (len(body) if known_size else None)
    assert [event["phase"] for event in events[-2:]] == ["verifying", "loading"]
    assert validated and target.is_dir()
    assert not (tmp_path / "cache/current.json").exists()


def _progress_result_worker(connection, kind, nr_enabled, wake_path, pcm16):
    connection.send({"progress": {"phase": "downloading", "resource": "campp", "downloaded_bytes": 25, "total_bytes": 100, "attempt": 1}})
    connection.send({"progress": {"phase": "verifying", "resource": "campp"}})
    connection.send({"ok": True, "result": {"checked": True}})
    connection.close()


def _progress_forever_worker(connection, kind, nr_enabled, wake_path, pcm16):
    Path(wake_path).write_text(str(os.getpid()), encoding="ascii")
    while True:
        connection.send({"progress": {"phase": "downloading", "resource": "campp", "downloaded_bytes": 1, "total_bytes": 10}})
        time.sleep(.01)


@pytest.mark.asyncio
async def test_progress_frames_do_not_replace_the_final_worker_result(monkeypatch):
    monkeypatch.setattr(rm, "_resource_worker", _progress_result_worker)
    events = []
    result = await rm._run_worker("repair", False, on_progress=events.append)
    assert result == {"checked": True}
    assert [event["phase"] for event in events] == ["downloading", "verifying"]
    assert events[0]["downloaded_bytes"] == 25


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_progress_cannot_extend_timeout_or_prevent_physical_retirement(tmp_path, monkeypatch, cancel):
    monkeypatch.setattr(rm, "_resource_worker", _progress_forever_worker)
    entered = asyncio.Event()
    marker = tmp_path / "worker-pid"
    task = asyncio.create_task(rm._run_worker("repair", False, str(marker), timeout=4,
                                            on_progress=lambda _event: entered.set()))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        pid = int(marker.read_text(encoding="ascii"))
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(rm.VoiceResourceError, match="resource_prepare_timeout"):
                await task
        assert not psutil.pid_exists(pid)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_progress_cannot_modify_a_cancelled_operation_or_its_successor(tmp_path, monkeypatch):
    entered = asyncio.Event()
    callbacks = []
    async def worker(*_args, on_progress, **_kwargs):
        callbacks.append(on_progress)
        on_progress({"phase": "downloading", "resource": "campp", "downloaded_bytes": 25, "total_bytes": 100})
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(rm, "_run_worker", worker)
    manager = rm.VoiceResourceManager(lambda: False, cache_root=tmp_path)
    try:
        first = manager.start("repair")["operation_id"]
        await asyncio.wait_for(entered.wait(), 2)
        assert manager.operation(first)["progress"] == .25
        await manager.cancel(first)
        before = manager.operation(first)
        entered.clear()
        second = manager.start("repair")["operation_id"]
        await asyncio.wait_for(entered.wait(), 2)
        callbacks[0]({"phase": "downloading", "resource": "campp", "downloaded_bytes": 90, "total_bytes": 100})
        assert manager.operation(first) == before
        assert manager.operation(second)["progress"] == .25
    finally:
        await manager.close()


@pytest.mark.parametrize("invalid", [
    {"phase": []}, {"phase": "downloading", "resource": []},
    {"phase": "downloading", "downloaded_bytes": True},
    {"phase": "downloading", "downloaded_bytes": -1},
    {"phase": "downloading", "downloaded_bytes": float("nan")},
    {"phase": "downloading", "downloaded_bytes": 2 ** 53},
    {"phase": "downloading", "downloaded_bytes": 11, "total_bytes": 10},
    {"phase": "downloading", "downloaded_bytes": 1, "total_bytes": 0},
])
def test_invalid_progress_cannot_become_a_snapshot(invalid):
    with pytest.raises(rm.VoiceResourceError, match="resource_worker_failed"):
        rm._validated_progress(invalid)

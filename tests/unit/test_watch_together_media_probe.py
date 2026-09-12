import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from main_logic.watch_together import engine, library


@pytest.mark.parametrize('streams,role,expected', [
    ([{'codec_type': 'audio', 'codec_name': 'pcm_s16le'}], 'audio', True),
    ([{'codec_type': 'video', 'codec_name': 'h264'}], 'video', True),
    ([{'codec_type': 'audio', 'codec_name': 'mp3'}], 'video', False),
    ([], 'audio', False),
])
def test_probe_checks_stream_role_and_caches(tmp_path, monkeypatch, streams, role, expected):
    library._probe_media_cached.cache_clear()
    monkeypatch.setattr(engine, 'media_binary', lambda _: 'ffprobe')
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({'streams': streams})))
    monkeypatch.setattr(subprocess, 'run', run)
    args = (tmp_path / 'object', 4, 123, role)
    assert library._probe_media(*args) is expected
    assert library._probe_media(*args) is expected
    assert run.call_count == 1
    assert run.call_args.kwargs['timeout'] == 10


def test_probe_timeout_is_unavailable(tmp_path, monkeypatch):
    library._probe_media_cached.cache_clear()
    monkeypatch.setattr(engine, 'media_binary', lambda _: 'ffprobe')
    monkeypatch.setattr(subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('ffprobe', 10)))
    assert not library._probe_media(tmp_path / 'object', 4, 123, 'audio')


def test_missing_probe_is_unknown_and_does_not_cache_failure(tmp_path, monkeypatch):
    library._probe_media_cached.cache_clear()
    monkeypatch.setattr(engine, 'media_binary', Mock(side_effect=FileNotFoundError()))
    assert library._probe_media(tmp_path / 'object', 4, 123, 'audio') is None
    assert library._probe_media_cached.cache_info().currsize == 0

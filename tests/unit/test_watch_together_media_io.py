import asyncio
import shutil

import httpx
import pytest

from main_logic.watch_together import engine


@pytest.mark.asyncio
async def test_dash_tracks_share_download_budget(tmp_path):
    budget = {'remaining': 10}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'123456'))) as client:
        await engine.download_stream(client, {'url':'https://cdn.test/video'}, tmp_path / 'video', budget=budget)
        assert budget['remaining'] == 4
        with pytest.raises(ValueError, match='limit'):
            await engine.download_stream(client, {'url':'https://cdn.test/audio'}, tmp_path / 'audio', budget=budget)
    assert (tmp_path / 'video').read_bytes() == b'123456'
    assert (tmp_path / 'audio').read_bytes() == b''


def test_browser_codecs_copy_known_formats_and_convert_fallbacks():
    assert engine.browser_codec_args({'codecid': 7}, {'codecs': 'mp4a.40.2'}) == ['-c:v', 'copy', '-c:a', 'copy']
    fallback = engine.browser_codec_args({'codecid': 12}, {'codecs': 'ec-3'})
    assert 'libx264' in fallback and 'yuv420p' in fallback and 'aac' in fallback
    assert engine.browser_codec_args({'codecid': 7}, None) == ['-c:v', 'copy']


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['backupUrl', 'backup_url'])
async def test_cdn_failure_retries_backup(tmp_path, key):
    requested = []
    def handle(request):
        requested.append(request.url.host)
        if request.url.host == 'primary.test':
            raise httpx.ConnectError('CDN unavailable', request=request)
        return httpx.Response(200, content=b'complete-video')
    target = tmp_path / 'video.m4s'
    target.write_bytes(b'old-partial-file')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await engine.download_stream(client, {'baseUrl': 'https://primary.test/v', key: ['https://backup.test/v']}, target)
    assert requested == ['primary.test', 'backup.test']
    assert target.read_bytes() == b'complete-video'


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg integration prerequisite')
@pytest.mark.parametrize('repeat_during_spawn', [False, True])
async def test_cancel_reaps_media_process_before_return(monkeypatch, repeat_during_spawn):
    created = asyncio.Event()
    release_spawn = asyncio.Event()
    processes = []
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        created.set()
        if repeat_during_spawn:
            await release_spawn.wait()
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(engine.run_media_async('ffmpeg', '-re', '-f', 'lavfi', '-i', 'sine=frequency=440', '-f', 'null', '-'))
    await asyncio.wait_for(created.wait(), 10)
    task.cancel()
    if repeat_during_spawn:
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), 'caller must wait for process creation and reaping'
        release_spawn.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert processes[0].returncode is not None

@pytest.mark.asyncio
async def test_download_file_io_runs_off_loop(tmp_path):
    import threading
    event_thread = threading.get_ident()
    class Target:
        def open(self, mode):
            assert threading.get_ident() != event_thread
            return (tmp_path / 'stream').open(mode)
    await engine.write_download_chunk(Target(), b'video', 'wb')
    assert (tmp_path / 'stream').read_bytes() == b'video'


@pytest.mark.asyncio
async def test_missing_vision_config_fails_before_video_work(tmp_path, monkeypatch):
    from types import SimpleNamespace
    instance = engine.Engine(tmp_path, None, 'cat')
    instance._cm = SimpleNamespace(get_model_api_config=lambda _: {})
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    with pytest.raises(RuntimeError, match='API'):
        await instance.prepare({'id':'missing'}, 'unused', 'cat')
    assert not (tmp_path / 'missing').exists()

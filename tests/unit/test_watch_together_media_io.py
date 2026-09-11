import asyncio
import shutil

import httpx
import pytest

from main_logic.watch_together import engine


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

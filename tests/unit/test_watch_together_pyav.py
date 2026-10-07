import asyncio
import multiprocessing
import threading
import pytest
from main_logic.watch_together import media
from main_logic.watch_together.media_smoke import exercise, make_sources

def test_media_pipeline_without_external_tools(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', '')
    exercise(tmp_path)

def test_worker_failure_and_timeout_leave_no_children(tmp_path):
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(RuntimeError, match='FileNotFoundError'):
        media.run('duration', tmp_path / 'missing')
    with pytest.raises(TimeoutError):
        media.run('duration', tmp_path / 'missing', timeout=0)
    assert {p.pid for p in multiprocessing.active_children()} == before

@pytest.mark.asyncio
async def test_cancel_during_spawn_reaps_child_even_when_cancelled_twice(tmp_path, monkeypatch):
    context = multiprocessing.get_context('spawn')
    original_start = context.Process.start
    started, release = threading.Event(), threading.Event()
    children = []
    def delayed_start(process):
        original_start(process)
        children.append(process)
        started.set()
        assert release.wait(10)
    monkeypatch.setattr(context.Process, 'start', delayed_start)
    task = asyncio.create_task(media.run_async('duration', tmp_path / 'missing'))
    assert await asyncio.to_thread(started.wait, 10)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    with pytest.raises(ValueError, match='closed'):
        _ = children[0].pid

@pytest.mark.asyncio
async def test_async_media_does_not_block_event_loop(tmp_path):
    video, _ = make_sources(tmp_path)
    ticks = 0
    task = asyncio.create_task(media.run_async('duration', video))
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.001)
    assert await task > 3
    assert ticks > 1

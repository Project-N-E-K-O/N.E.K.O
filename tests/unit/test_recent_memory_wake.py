"""Recent writes wake sync without polling chat bodies or sharing their content."""

import asyncio
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils import recent_file
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401


def test_only_successful_recent_writes_notify_and_listener_faults_do_not_fail_saves(tmp_path, monkeypatch):
    seen = []
    path = tmp_path / 'recent.json'
    unsubscribe = recent_file.subscribe_recent_changes(seen.append)
    def broken_listener(path):
        raise RuntimeError('optional listener failure')
    remove_broken = recent_file.subscribe_recent_changes(broken_listener)
    try:
        recent_file.write_recent_payload(path, [])
        assert [Path(value) for value in seen] == [path]
        recent_file.read_recent_text(path)
        assert len(seen) == 1
        def fail_write(*args, **kwargs):
            raise OSError('synthetic disk failure')
        monkeypatch.setattr(recent_file, 'atomic_write_json', fail_write)
        with pytest.raises(OSError):
            recent_file.write_recent_payload(path, [])
        assert len(seen) == 1
    finally:
        unsubscribe()
        remove_broken()


@pytest.mark.asyncio
async def test_memory_process_coalesces_writes_into_one_content_free_local_hint(tmp_path, monkeypatch):
    from services.chat_sync.recent_wake import forward_recent_memory_changes
    from config import MAIN_SERVER_PORT
    posted = asyncio.Event()
    post = AsyncMock(side_effect=lambda *args, **kwargs: posted.set())
    monkeypatch.setattr('services.chat_sync.recent_wake.get_internal_http_client', lambda: type('Client', (), {'post': post})())
    listeners = len(recent_file._CHANGE_LISTENERS)
    task = asyncio.create_task(forward_recent_memory_changes())
    try:
        await asyncio.sleep(0)
        for index in range(5):
            await asyncio.to_thread(recent_file.write_recent_payload, tmp_path / 'recent.json', [{'secret': index}])
        await asyncio.wait_for(posted.wait(), 2)
        await asyncio.sleep(0.25)
        post.assert_awaited_once_with(f'http://127.0.0.1:{MAIN_SERVER_PORT}/api/chat-sync/recent-changed', timeout=2)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert len(recent_file._CHANGE_LISTENERS) == listeners


@pytest.mark.asyncio
async def test_recent_runtime_has_no_duplicate_body_scan_and_idles_until_write(tmp_path, monkeypatch):
    from tests.unit.test_chat_sync_runtime import setup_runtime
    from memory.recent_sync import RecentMemoryAdapter
    monkeypatch.setattr('memory.recent_sync.assert_cloudsave_writable', lambda *args, **kwargs: None)
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path / 'memory', project_memory_dir=tmp_path / 'memory'))
    runtime, _, _ = setup_runtime(tmp_path, recent_memory_adapter=adapter)
    ticked = asyncio.Event()
    ticks = []
    async def tick():
        ticks.append(True)
        ticked.set()
    monkeypatch.setattr(runtime, 'scheduler_tick', tick)
    def forbidden_read(name):
        raise AssertionError('body lane must not reread the projection')
    monkeypatch.setattr(adapter, 'read', forbidden_read)
    try:
        assert await runtime._channel_pass('a' * 32, 'body') is None
        runtime.start_scheduler()
        await asyncio.wait_for(ticked.wait(), 2)
        ticked.clear()
        await asyncio.sleep(1.25)
        assert len(ticks) == 1
        for index in range(5):
            await asyncio.to_thread(recent_file.write_recent_payload, tmp_path / 'recent.json', [index])
        await asyncio.wait_for(ticked.wait(), 2)
        await asyncio.sleep(0.3)
        assert len(ticks) == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('source,delay', [('idle', None), ('retry', 3), ('notification', 3), ('new-role', 3)])
async def test_idle_wait_is_thirty_seconds_but_preserves_earlier_retry(tmp_path, monkeypatch, source, delay):
    from tests.unit.test_chat_sync_runtime import setup_runtime
    runtime, _, _ = setup_runtime(tmp_path, recent_memory_adapter=object())
    waits = []
    async def wait_for_hint(awaitable, *, timeout):
        awaitable.close()
        waits.append(timeout)
        runtime._closing = True
    monkeypatch.setattr(runtime, 'scheduler_tick', AsyncMock())
    monkeypatch.setattr(runtime.scheduler, 'next_wake_at', lambda: runtime.scheduler.clock() + delay * 1000 if source == 'retry' else None)
    monkeypatch.setattr(runtime.state, 'next_wake_at', lambda now: now + delay * 1000 if source == 'new-role' else None)
    if source == 'notification':
        runtime._notification_retry_at = runtime.scheduler.clock() + delay * 1000
    monkeypatch.setattr('services.chat_sync.runtime.asyncio.wait_for', wait_for_hint)
    try:
        await runtime._run_scheduler()
        assert waits == [delay or 30]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pending_profile_does_not_keep_waking_a_declined_retry(tmp_path, monkeypatch):
    from tests.unit.test_chat_sync_runtime import setup_runtime, UID
    runtime, _, _ = setup_runtime(tmp_path, recent_memory_adapter=object())
    monkeypatch.setattr(runtime.profiles.store, 'next_operation', lambda domain: {'base_revision': 1})
    monkeypatch.setattr(runtime.profiles, 'step', AsyncMock())
    try:
        await runtime.configure('https://sync.test')
        binding = await runtime.enable(UID, 'Neko')
        runtime._wake.clear()
        await runtime._automatic_profiles([binding], await runtime.session_presence_provider())
        assert not runtime._wake.is_set()
    finally:
        await runtime.close()


def test_write_from_separate_memory_process_reaches_local_host_without_content(tmp_path):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, self.rfile.read(int(self.headers.get('Content-Length', 0)))))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = '''
import asyncio, sys
from types import SimpleNamespace
sys.modules['config'] = SimpleNamespace(MAIN_SERVER_PORT=int(sys.argv[2]))
from services.chat_sync.recent_wake import forward_recent_memory_changes
from utils.recent_file import write_recent_payload
from utils.internal_http_client import aclose_internal_http_client_current_loop
async def main():
    task = asyncio.create_task(forward_recent_memory_changes())
    await asyncio.sleep(0)
    await asyncio.to_thread(write_recent_payload, sys.argv[1], [{'content': 'private memo'}])
    await asyncio.sleep(0.6)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await aclose_internal_http_client_current_loop()
asyncio.run(main())
'''
    try:
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'recent.json'), str(server.server_port)],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert calls == [('/api/chat-sync/recent-changed', b'')]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_saved_chat_and_compaction_sync_once_without_idle_reads_or_upload_echo(tmp_path, monkeypatch):
    import httpx
    from memory.recent_sync import RecentMemoryAdapter
    from tests.unit.test_chat_sync_runtime import SyntheticService, setup_runtime
    from tests.unit.test_recent_memory_sync import Server, OWNER, UID, row

    monkeypatch.setattr('memory.recent_sync.assert_cloudsave_writable', lambda *args, **kwargs: None)
    path = tmp_path / 'memory' / 'Neko' / 'recent.json'
    recent_file.write_recent_payload(path, [row('system', 'memo'), row('human', 'current chat')])
    remote = Server()
    class Service(SyntheticService):
        async def request(self, request):
            if request.url.path.endswith('/recent-memory'):
                return httpx.Response(200, json=await remote.request(request.method, request.url.path, request.content or None))
            return await super().request(request)
    service = Service()
    service.owner = OWNER
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path / 'memory', project_memory_dir=tmp_path / 'memory'))
    reads = []
    original_read = adapter.read
    def counted_read(name):
        reads.append(name)
        return original_read(name)
    monkeypatch.setattr(adapter, 'read', counted_read)
    runtime, _, _ = setup_runtime(tmp_path, service, recent_memory_adapter=adapter)
    async def wait_for_receipts(count):
        async with asyncio.timeout(4):
            while len(remote.receipts) < count:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.5)
    try:
        await runtime.configure('https://sync.test')
        await runtime.enable(UID, 'Neko')
        runtime.start_scheduler()
        await wait_for_receipts(1)
        idle_reads = len(reads)
        await asyncio.sleep(1.2)
        assert len(reads) == idle_reads
        recent_file.write_recent_payload(path, [row('system', 'compacted memo')])
        await wait_for_receipts(2)
        assert [entry['text'] for entry in remote.rows] == ['compacted memo']
        assert len(remote.requests) == 2
    finally:
        await runtime.close()

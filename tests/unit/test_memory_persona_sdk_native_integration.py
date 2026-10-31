"""Real memory ASGI route through control RPC and the public SDK client.

The transport seams are in-process; no services or user storage are opened.
"""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import httpx
import pytest
from plugin.logging_config import get_logger

from app.memory_server import runtime
from memory.persona.manager import PersonaManager
from plugin.core.context import PluginContext
from plugin.core.state import state
from plugin.sdk.plugin import MemoryClient, MemoryTarget
from plugin.sdk.shared.core.context import SdkContext
from plugin.sdk.shared.models.result import Ok, Err
from plugin.server.application import memory as adapter
from plugin.server.messaging.request_router import PluginRouter


@pytest.mark.asyncio
async def test_private_persona_native_http_control_rpc_and_sdk(tmp_path, monkeypatch):
    path = tmp_path / 'persona.json'
    path.write_text(json.dumps({
        'master': {'facts': [
            {'id': 'p1', 'text': 'tea', 'source': 'manual', 'protected': True, 'suppress': True},
            'legacy', {'text': 'not private', 'subject_kind': 'group_chat'},
        ]},
        '@subject/broken': {'facts': ['hidden']},
    }), encoding='utf-8')
    manager = PersonaManager.__new__(PersonaManager)
    manager._personas = {}
    manager._alocks = {}
    manager._alocks_guard = threading.Lock()
    manager._persona_path = lambda name: str(path)
    manager._async_sync_character_card = AsyncMock(return_value=False)
    manager._async_migrate_from_settings = AsyncMock()
    monkeypatch.setattr(runtime, 'persona_manager', manager)
    monkeypatch.setattr(runtime, '_memory_runtime_init_completed', True)
    monkeypatch.setattr(runtime, '_memory_storage_blocked_after_init', False)

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app))
    monkeypatch.setattr(adapter, 'get_internal_http_client', lambda: http)
    queue = asyncio.Queue()
    monkeypatch.setattr(state, '_plugin_comm_queue', queue)
    host = PluginContext(plugin_id='persona-test', config_path=tmp_path / 'plugin.toml', logger=get_logger("persona-test"), status_queue=None)

    # Replace only the queue transport: actual PluginContext/RpcPeer, router,
    # dispatcher, handler, encoding, SDK and native route remain in the path.
    async def send(request, timeout):
        await queue.put(dict(request, from_plugin=host.plugin_id))
    monkeypatch.setattr(host, '_send_rpc_request', send)
    monkeypatch.setattr(state, 'get_downlink_sender', lambda plugin_id: host._dispatch_rpc_response)
    router = PluginRouter()
    await router.start()
    client = MemoryClient(SdkContext(host))
    target = MemoryTarget.private('YUI')
    try:
        bad_name = await client.get_persona(target=MemoryTarget.private('bad/name'))
        assert isinstance(bad_name, Err) and bad_name.error.code == 'INVALID_ARGUMENT'
        result = await client.get_persona(target=target)
        assert isinstance(result, Ok), result
        assert result.value.target == target
        assert len(result.value.sections) == 1
        section = result.value.sections[0]
        assert section.entity == 'master'
        assert [(e.id, e.text, e.source, e.protected, e.suppressed) for e in section.entries] == [
            ('p1', 'tea', 'manual', True, True), (None, 'legacy', 'unknown', False, False),
        ]
        manager._personas['YUI'] = {}
        empty = await client.get_persona(target=target)
        assert isinstance(empty, Ok) and empty.value.sections == []
        manager._personas['YUI'] = {'master': {'facts': [{'text': 7}]}}
        malformed = await client.get_persona(target=target)
        assert isinstance(malformed, Err) and malformed.error.code == 'MEMORY_PROTOCOL_ERROR'
        manager._personas['YUI'] = {'master': {'facts': ['x'] * 10001}}
        oversized = await client.get_persona(target=target)
        assert isinstance(oversized, Err) and oversized.error.code == 'MEMORY_RESPONSE_TOO_LARGE'
        monkeypatch.setattr(runtime, 'persona_manager', None)
        unready = await client.get_persona(target=target)
        assert isinstance(unready, Err) and unready.error.code == 'MEMORY_NOT_READY'
        # The real search endpoint remains usable when persona is absent. Use
        # a retrieval double so this integration test needs no indexes or LLM.
        from app.memory_server import routes
        import memory.hybrid_recall as native_search
        monkeypatch.setattr(runtime, 'fact_store', object())
        monkeypatch.setattr(runtime, 'reflection_engine', object())
        monkeypatch.setattr(routes, '_resolve_scoped_memory_language', AsyncMock(return_value='en'))
        recall = AsyncMock(return_value={
            'query': 'tea', 'results': [], 'candidates_total': 0, 'elapsed_ms': 0.0,
        })
        monkeypatch.setattr(native_search, 'hybrid_recall', recall)
        search = await client.search(target=target, query='tea')
        assert isinstance(search, Ok) and search.value.items == []
        assert recall.await_args.kwargs['persona_manager'] is None
        monkeypatch.setattr(runtime, '_memory_storage_blocked_after_init', True)
        blocked = await client.get_persona(target=target)
        assert isinstance(blocked, Err) and blocked.error.code == 'MEMORY_STORAGE_RESTRICTED'
    finally:
        host.close()
        await router.stop()
        await http.aclose()

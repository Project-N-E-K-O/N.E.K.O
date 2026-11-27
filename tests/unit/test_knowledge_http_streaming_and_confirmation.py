"""Regress production upload guards and file ownership with synthetic inputs."""

import hashlib
import asyncio
from pathlib import Path
import struct
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes, runtime
from utils.asgi_body_limit import InboundBodySizeLimitMiddleware
from utils.http.knowledge_proxy import BodyTooLarge, PACK_BODY_MAX_BYTES
from utils.knowledge_package import canonical_json, validate_package
from starlette.requests import ClientDisconnect
from tests.knowledge_archive_fixtures import specification


def _large_archive(path):
    """A data-only stored ZIP exceeds 20 MiB while every member stays valid."""
    entries, chunks = [], []
    content = ''.join(f'{index:04d}' + 'x' * 996 for index in range(80))
    for index in range(100):
        title = f'Entry {index}'
        entries.append({'title': title, 'content': content})
        for offset in range(80):
            text = title + '\n' + content[offset * 1000:(offset + 1) * 1000]
            chunks.append({'entry_title': title, 'chunk_index': offset,
                           'embedding_text': text, 'text_hash': hashlib.sha256(text.encode()).hexdigest()})
    pack = {'schema_version': 1, 'pack_id': 'large.http-review', 'material_type': 'knowledge',
            'source': {'name': 'Synthetic', 'homepage': '', 'license': 'CC0'}, 'entries': entries}
    members = {'pack.json': canonical_json(pack),
               'chunks.jsonl': b''.join(canonical_json(row) + b'\n' for row in chunks),
               'vectors.f32': struct.pack('<256f', 1.0, *([0.0] * 255)) * len(chunks)}
    manifest = {'format': 'neko.knowledge-package', 'format_version': 2, 'package_id': pack['pack_id'],
                'name': 'Synthetic', 'version': '1', 'source': {'author': 'Synthetic', 'license': 'CC0', 'url': ''},
                'chunking_version': 'neko-pack-chunks-v1', 'embedding_spec': specification(256),
                'counts': {'documents': len(entries), 'chunks': len(chunks)},
                'files': {name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                          for name, raw in members.items()}}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in {'manifest.json': canonical_json(manifest), **members}.items():
            archive.writestr(name, raw)
    return path.read_bytes()


async def test_production_guard_allows_a_valid_large_zip_but_keeps_the_global_cap(tmp_path, monkeypatch):
    from knowledge.service import KnowledgeService

    service = KnowledgeService(tmp_path / 'knowledge')
    await service.start()
    try:
        accepted = []
        async def import_pack(path, **_kwargs):
            accepted.append(validate_package(path).manifest['package_id'])
            return {'ok': True, 'state': 'queued'}
        monkeypatch.setattr(service, 'import_pack', import_pack)
        monkeypatch.setattr(knowledge_routes, '_service', service)
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        production = next(item for item in runtime.app.user_middleware if item.cls is InboundBodySizeLimitMiddleware)
        app.add_middleware(production.cls, **production.kwargs)
        raw = _large_archive(tmp_path / 'large.zip')
        assert 20 * 1024 * 1024 < len(raw) < PACK_BODY_MAX_BYTES
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            result = await client.post('/internal/knowledge/packs/import', content=raw, headers={'Content-Type': 'application/zip'})
            assert result.status_code == 200 and result.json()['ok'], result.text
            oversized = await client.post('/internal/knowledge/packs/import', content=b'',
                                          headers={'Content-Type': 'application/zip', 'Content-Length': str(PACK_BODY_MAX_BYTES + 1)})
            assert oversized.status_code == 413
            ordinary = await client.post('/other', content=b'', headers={'Content-Length': str(len(raw))})
            assert ordinary.status_code == 413
        assert accepted == ['large.http-review']
    finally:
        await service.stop()


@pytest.mark.parametrize('failure,status', [(BodyTooLarge, 413), (ClientDisconnect, 400)])
async def test_partial_multipart_files_close_when_the_bounded_stream_aborts(tmp_path, monkeypatch, failure, status):
    from starlette.requests import Request
    from knowledge.service import KnowledgeService

    service = KnowledgeService(tmp_path / 'knowledge')
    await service.start()
    opened = []
    original = knowledge_routes.MultiPartParser
    class RecordingParser(original):
        def on_headers_finished(self):
            super().on_headers_finished()
            opened.extend(self._files_to_close_on_error)
    monkeypatch.setattr(knowledge_routes, 'MultiPartParser', RecordingParser)
    async def body(_request, _maximum):
        yield b'--demo\r\nContent-Disposition: form-data; name="pack"; filename="pack.zip"\r\n\r\nPK1234'
        raise failure()
    monkeypatch.setattr(knowledge_routes, 'capped_body', body)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'content-type', b'multipart/form-data; boundary=demo')]})
    try:
        result = await knowledge_routes._streamed_pack_import(request, service, subscribed=False)
        assert result.status_code == status
        assert opened and all(handle.closed for handle in opened)
    finally:
        await service.stop()


async def test_cancelled_preview_keeps_its_spool_until_the_parser_releases_it(tmp_path, monkeypatch):
    from knowledge.service import KnowledgeService

    service = KnowledgeService(tmp_path / 'knowledge')
    await service.start()
    started, release = asyncio.Event(), asyncio.Event()
    paths = []
    async def preview(_filename, raw, **_kwargs):
        paths.append(raw)
        started.set()
        await release.wait()
        assert raw.is_file()
        return {'ok': True}
    monkeypatch.setattr(service, 'preview_source', preview)
    monkeypatch.setattr(knowledge_routes, '_service', service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            task = asyncio.create_task(client.post('/internal/knowledge/sources/preview', content=b'synthetic',
                                       headers={'Content-Type': 'application/octet-stream', 'X-Knowledge-Filename': 'example.txt'}))
            try:
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and paths[0].exists()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not paths[0].exists()
    finally:
        await service.stop()


async def test_source_commit_confirmation_is_typed_and_forwarded(tmp_path, monkeypatch):
    from knowledge.service import KnowledgeService

    service = KnowledgeService(tmp_path / 'knowledge')
    await service.start()
    seen = []
    async def commit(_preview, _digest, **kwargs):
        seen.append(kwargs)
        return {'ok': True}
    monkeypatch.setattr(service, 'commit_source', commit)
    monkeypatch.setattr(knowledge_routes, '_service', service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            payload = {'preview_id': 'synthetic', 'sha256': 'a' * 64, 'enabled': True}
            result = await client.post('/internal/knowledge/sources/commit', json={**payload, 'enabled_confirmed': 'yes'})
            assert result.status_code == 400 and not seen
            result = await client.post('/internal/knowledge/sources/commit', json={**payload, 'enabled_confirmed': True})
            assert result.json()['ok'] and seen == [{'enabled': True, 'enabled_confirmed': True}]
    finally:
        await service.stop()


async def test_preview_passes_a_temporary_path_and_reclaims_it(tmp_path, monkeypatch):
    from knowledge.service import KnowledgeService

    service = KnowledgeService(tmp_path / 'knowledge')
    await service.start()
    paths = []
    async def preview(filename, raw, **_kwargs):
        assert isinstance(raw, Path) and raw.read_bytes() == b'synthetic private text'
        paths.append(raw)
        return {'ok': True, 'preview_id': 'synthetic'}
    monkeypatch.setattr(service, 'preview_source', preview)
    monkeypatch.setattr(knowledge_routes, '_service', service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            result = await client.post('/internal/knowledge/sources/preview', content=b'synthetic private text',
                                       headers={'Content-Type': 'application/octet-stream', 'X-Knowledge-Filename': 'example.txt'})
        assert result.status_code == 200 and result.json()['ok']
        assert paths and all(not path.exists() for path in paths)
    finally:
        await service.stop()

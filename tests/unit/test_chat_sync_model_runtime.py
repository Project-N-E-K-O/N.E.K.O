"""Live2D binding, renderer admission and verified package transfer."""
import hashlib
import json
import httpx
import pytest
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_chat_sync_runtime import BINDING, DOMAIN, OWNER, UID, SyntheticService, setup_runtime
from services.chat_sync.model_protocol import canonical, encode

class ModelService(SyntheticService):
    def __init__(self):
        super().__init__()
        self.model = {"schemaVersion": 1, "model": None}
        self.model_revision = 1

    async def request(self, request):
        if request.url.path == "/v1/chat-sync/models/capabilities":
            return httpx.Response(200, json={"schemaVersion": 1, "modelBinding": 1, "resources": False})
        if request.url.path.endswith("/model-binding"):
            assert request.headers["X-Neko-Chat-Binding"] == BINDING
            return httpx.Response(200, json={"ownerId": OWNER, "characterSyncId": UID, "revision": self.model_revision,
                "record": self.model, "recordHash": hashlib.sha256(encode(self.model)).hexdigest()})
        return await super().request(request)

@pytest.mark.asyncio
async def test_model_binding_waits_for_renderer_and_rejects_a_newer_unbind_race(tmp_path):
    runtime, service, _ = setup_runtime(tmp_path, ModelService())
    class Adapter:
        path = "/user_live2d/original/model.model3.json"
        applied = 0
        async def read(self, _uid):
            return {"name": "Synthetic", "kind": "live2d", "path": self.path, "selection": canonical(["live2d", self.path]).decode()}
        async def apply(self, _uid, expected, path, commit):
            assert expected == (await self.read(_uid))["selection"]
            await commit(setattr, self, "path", path)
            self.applied += 1
            return True
    adapter = Adapter()
    runtime.models.adapter = adapter
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Synthetic")
        await runtime.models.step(UID, manual=True)
        status = await runtime.models.status(UID)
        assert adapter.applied == 0 and status["candidate"]["path"] != adapter.path
        service.model_revision = 2
        assert await runtime.models.finish(UID, status["candidate"]["token"], True) is False
        assert adapter.applied == 0
        await runtime.models.step(UID, manual=True)
        latest = await runtime.models.status(UID)
        assert latest["candidate"]["token"] != status["candidate"]["token"]
        assert await runtime.models.finish(UID, latest["candidate"]["token"], True) is True
        assert adapter.applied == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_default_model_candidate_loads_from_the_actual_static_route(tmp_path):
    from urllib.parse import urljoin
    from app.main_server.web_app import app

    runtime, _, _ = setup_runtime(tmp_path, ModelService())

    class Adapter:
        path = '/user_live2d/current/model.model3.json'

        async def read(self, _uid):
            return {'name': 'Synthetic', 'kind': 'live2d', 'path': self.path,
                    'selection': canonical(['live2d', self.path]).decode()}

        async def apply(self, _uid, expected, path, commit):
            assert expected == (await self.read(_uid))['selection']
            await commit(setattr, self, 'path', path)
            return True

    adapter = Adapter()
    runtime.models.adapter = adapter
    try:
        await runtime.configure('https://sync.test')
        await runtime.enable(UID, 'Synthetic')
        await runtime.models.step(UID, manual=True)
        candidate = (await runtime.models.status(UID))['candidate']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:48911/') as client:
            response = await client.get(candidate['path'])
            assert response.status_code == 200
            moc = await client.get(urljoin(str(response.url), response.json()['FileReferences']['Moc']))
            assert moc.status_code == 200 and moc.content.startswith(b'MOC3')
        assert await runtime.models.finish(UID, candidate['token'], True) is True
        assert adapter.path == candidate['path']
        await runtime.models.step(UID, manual=True)
        assert not (await runtime.models.status(UID)).get('candidate')
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_model_download_failure_exposes_a_durable_manual_retry(tmp_path, monkeypatch):
    from pathlib import Path
    service = ModelService()
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/cloud_model_v1.json").read_text(encoding='utf-8'))
    service.model = next(item["record"] for item in fixture["valid"] if item["name"] == "complete_model")
    runtime, _, _ = setup_runtime(tmp_path, service)
    class Adapter:
        async def read(self, _uid):
            return {"name": "Synthetic", "kind": "live2d", "path": "/original", "selection": "original"}
    async def unavailable(*_args):
        raise OSError("cloud_model_download_unavailable")
    runtime.models.adapter = Adapter()
    monkeypatch.setattr(runtime.models, "download", unavailable)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Synthetic")
        with pytest.raises(OSError, match="cloud_model_download_unavailable"):
            await runtime.models.step(UID, manual=True)
        assert (await runtime.models.status(UID))["failed"] is True
        await runtime.models.resolve(UID, "retry")
        assert (await runtime.models.status(UID))["failed"] is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['network', 'hash', 'oversize', 'short', 'guard', 'cancel', 'commit-guard', 'http'])
async def test_model_download_failures_remove_only_their_own_partial_file(tmp_path, monkeypatch, failure):
    import asyncio
    from types import SimpleNamespace
    from services.chat_sync.model_sync import DesktopModelSync

    raw = b'accepted model package'
    package = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
               'resource': {'url': 'https://cdn.example/model.zip'}}
    directory = tmp_path / 'model-packages'
    directory.mkdir()
    other = directory / 'other-flight.part'
    other.write_bytes(b'owned by a different download')
    target = directory / (package['sha256'] + '.zip')
    target.write_bytes(b'previous cached bytes')

    class Response:
        status_code = 503 if failure == 'http' else 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        async def aiter_raw(self):
            yield raw[:1]
            if failure == 'network':
                raise httpx.ReadError('synthetic disconnect')
            yield {'hash': b'x' * (len(raw) - 1), 'oversize': raw,
                   'short': b''}.get(failure, raw[1:])

    class Client:
        def __init__(self, **_kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        def stream(self, *_args):
            return Response()

    guard_calls = 0
    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        if failure == 'cancel' and guard_calls == 2:
            raise asyncio.CancelledError()
        if failure == 'guard':
            raise ValueError('chat_sync_binding_changed')
        if failure == 'commit-guard' and guard_calls == 3:
            raise ValueError('chat_sync_binding_changed')

    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    sync = SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'profiles.sqlite3'))
    error = asyncio.CancelledError if failure == 'cancel' else (httpx.ReadError if failure == 'network'
            else OSError if failure == 'http' else ValueError)
    for _ in range(2):
        guard_calls = 0
        with pytest.raises(error):
            await DesktopModelSync.download(sync, DOMAIN, None, package, guard)
        assert list(directory.glob('*.part')) == [other]
        assert other.read_bytes() == b'owned by a different download'
        assert target.read_bytes() == b'previous cached bytes'


@pytest.mark.asyncio
async def test_model_download_publishes_verified_bytes_and_reuses_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.chat_sync.model_sync import DesktopModelSync

    raw = b'accepted model package'
    package = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
               'resource': {'url': 'https://cdn.example/model.zip'}}
    requests = []
    async def respond(request):
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(raw))
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **_kwargs: client)
    async def guard():
        pass
    sync = SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'profiles.sqlite3'))
    path = await DesktopModelSync.download(sync, DOMAIN, None, package, guard)
    assert path.read_bytes() == raw
    assert list(path.parent.glob('*.part')) == []
    assert await DesktopModelSync.download(sync, DOMAIN, None, package, guard) == path
    assert len(requests) == 1 and requests[0].headers.get('Authorization') is None


class PublishingModelService(ModelService):
    """Keep real request envelopes, CAS and immutable-operation receipts."""

    def __init__(self):
        super().__init__()
        self.operations = {}
        self.puts = []
        self.resources = []
        self.hook = None
        self.ready = True
        self.lose_ack = False

    async def request(self, request):
        path = request.url.path
        if path.endswith('/model-binding') or '/resources' in path:
            assert request.headers['X-Neko-Chat-Binding'] == BINDING
            if self.hook:
                await self.hook(request)
        if path == '/v1/chat-sync/models/capabilities':
            return httpx.Response(200, json={'schemaVersion': 1, 'modelBinding': 1, 'resources': True})
        if path.endswith('/model-binding') and request.method == 'PUT':
            body = json.loads(request.content)
            self.puts.append(body)
            receipt = self.operations.get(body['operationId'])
            if receipt is None:
                if body['baseRevision'] != self.model_revision:
                    return httpx.Response(409, json={'detail': 'conflict'})
                self.model_revision += 1
                self.model = body['record']
                receipt = {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': body['operationId'],
                           'revision': self.model_revision, 'recordHash': hashlib.sha256(encode(self.model)).hexdigest()}
                self.operations[body['operationId']] = receipt
            if self.lose_ack:
                self.lose_ack = False
                raise httpx.ReadError('synthetic lost receipt')
            return httpx.Response(200, json=receipt)
        if path.endswith('/resources'):
            body = json.loads(request.content)
            self.resources.append(body)
            return httpx.Response(200, json={'resourceId': body['resourceId'], 'ready': self.ready,
                'upload': {'url': 'https://oss.example/model.zip', 'method': 'PUT', 'headers': {}}})
        if path.endswith('/complete'):
            return httpx.Response(200, json={'resourceId': path.split('/')[-2], 'ready': True})
        return await super().request(request)


async def pending_model_runtime(tmp_path, *, uploaded=False, large=False):
    from pathlib import Path
    from services.chat_sync.model_package import package_model

    class Adapter:
        path = '/B.model3.json'
        kind = 'live2d'

        async def read(self, _uid):
            return {'name': 'Synthetic', 'kind': self.kind, 'path': self.path,
                    'selection': canonical([self.kind, self.path]).decode()}

        def source(self, path):
            return tmp_path / path[1:] / Path(path).name

    adapter = Adapter()
    for name in ('A', 'B', 'C', 'D'):
        source = adapter.source('/' + name + '.model3.json')
        source.parent.mkdir()
        source.write_text('{"Version":3,"FileReferences":{"Moc":"model.moc3","Textures":[]}}')
        raw = b'MOC3' + name.encode()
        if large and name == 'B':
            import random
            raw += random.Random(0).randbytes(150_000)
        (source.parent / 'model.moc3').write_bytes(raw)
    runtime, service, _ = setup_runtime(tmp_path, PublishingModelService())
    runtime.models.adapter = adapter
    await runtime.configure('https://sync.test')
    await runtime.enable(UID, 'Synthetic')
    record, file = package_model(adapter.source(adapter.path), tmp_path / 'pending-packages')
    pending = {'operationId': 'e' * 32, 'baseRevision': 1, 'record': record, 'file': file,
               'selection': '["live2d","/B.model3.json"]', 'uploaded': uploaded}
    _, _, commit = await runtime._bound_transport(UID)
    await commit(runtime.models.store.put, DOMAIN.key(), {'revision': 1, 'selection': '["live2d","/A.model3.json"]',
                 'remote': service.model, 'pending': pending})
    return runtime, service, adapter, pending


@pytest.mark.asyncio
@pytest.mark.parametrize('uploaded', [False, True])
async def test_changed_model_retires_pending_before_upload_or_publish(tmp_path, uploaded):
    from pathlib import Path
    runtime, service, adapter, pending = await pending_model_runtime(tmp_path, uploaded=uploaded)
    adapter.path = '/C.model3.json'
    original = Path(pending['file']['path']).read_bytes()
    try:
        await runtime.models.step(UID)
        assert service.puts == [] and service.resources == []
        assert not (await runtime.models.status(UID))['failed']
        await runtime.models.step(UID)
        assert service.model['model']['modelFile'] == 'C.model3.json'
        assert all(item['operationId'] != pending['operationId'] for item in service.puts)
        assert Path(pending['file']['path']).read_bytes() == original
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,path', [('live2d', '/A.model3.json'), ('vrm', '/other.vrm'), ('img', '/other.png')])
async def test_pending_model_is_not_published_after_reverting_or_selecting_an_unsupported_avatar(tmp_path, kind, path):
    runtime, service, adapter, _ = await pending_model_runtime(tmp_path)
    adapter.kind, adapter.path = kind, path
    try:
        await runtime.models.step(UID)
        await runtime.models.step(UID)
        assert service.puts == [] and service.resources == []
        assert service.model_revision == 1
        assert not (await runtime.models.status(UID))['failed']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('uploaded', [False, True])
async def test_unchanged_pending_model_resumes_the_same_operation(tmp_path, uploaded):
    runtime, service, _, pending = await pending_model_runtime(tmp_path, uploaded=uploaded)
    try:
        await runtime.models.step(UID)
        assert service.model == pending['record']
        assert [body['operationId'] for body in service.puts] == [pending['operationId']]
        assert len(service.resources) == (0 if uploaded else 1)
        assert 'pending' not in runtime.models.store.get(DOMAIN.key())
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_changed_model_checks_a_lost_ack_before_publishing_the_current_selection(tmp_path):
    runtime, service, adapter, pending = await pending_model_runtime(tmp_path)
    service.lose_ack = True
    try:
        with pytest.raises(ValueError, match='chat_sync_unavailable'):
            await runtime.models.step(UID)
        assert service.model == pending['record'] and service.model_revision == 2
        adapter.path = '/C.model3.json'
        await runtime.models.step(UID)
        assert len(service.puts) == 1
        state = runtime.models.store.get(DOMAIN.key())
        assert state['revision'] == 2 and 'pending' not in state and not state.get('conflict')
        await runtime.models.step(UID)
        assert service.model['model']['modelFile'] == 'C.model3.json'
        assert service.puts[-1]['baseRevision'] == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unchanged_model_retries_a_lost_ack_with_the_identical_envelope(tmp_path):
    runtime, service, _, pending = await pending_model_runtime(tmp_path)
    service.lose_ack = True
    try:
        with pytest.raises(ValueError, match='chat_sync_unavailable'):
            await runtime.models.step(UID)
        await runtime.models.step(UID)
        assert service.puts == [{field: pending[field] for field in ('operationId', 'baseRevision', 'record')}] * 2
        assert service.model_revision == 2 and len(service.resources) == 1
        assert not (await runtime.models.status(UID))['failed']
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_changed_pending_model_keeps_unknown_operation_when_cloud_cannot_be_checked(tmp_path):
    runtime, service, adapter, pending = await pending_model_runtime(tmp_path)
    adapter.path = '/C.model3.json'
    reads = 0
    async def unavailable(request):
        nonlocal reads
        if request.method == 'GET' and request.url.path.endswith('/model-binding'):
            reads += 1
            if reads == 2:
                raise httpx.ReadError('synthetic state query unavailable')
    service.hook = unavailable
    try:
        with pytest.raises(ValueError, match='chat_sync_unavailable'):
            await runtime.models.step(UID)
        assert runtime.models.store.get(DOMAIN.key())['pending'] == pending
        assert service.puts == [] and service.resources == []
        service.hook = None
        await runtime.models.step(UID)
        await runtime.models.step(UID)
        assert service.model['model']['modelFile'] == 'C.model3.json'
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('later_same_record', [False, True])
async def test_changed_pending_model_preserves_unrelated_cloud_changes_as_conflicts(tmp_path, later_same_record):
    from services.chat_sync.model_package import package_model
    runtime, service, adapter, pending = await pending_model_runtime(tmp_path)
    service.model, _ = package_model(adapter.source('/D.model3.json'), tmp_path / 'other-device')
    service.model_revision = 2
    if later_same_record:
        service.model, service.model_revision = pending['record'], 3
    adapter.path = '/C.model3.json'
    try:
        await runtime.models.step(UID)
        assert (await runtime.models.status(UID))['conflict'] is True
        await runtime.models.step(UID)
        assert service.puts == [] and service.resources == []
        assert service.model_revision == (3 if later_same_record else 2)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_late_old_commit_after_cloud_check_does_not_allow_a_blind_overwrite(tmp_path):
    runtime, service, adapter, pending = await pending_model_runtime(tmp_path)
    adapter.path = '/C.model3.json'
    try:
        await runtime.models.step(UID)
        async def late_commit(request):
            if request.method == 'PUT':
                service.model, service.model_revision = pending['record'], 2
        service.hook = late_commit
        await runtime.models.step(UID)
        assert service.model == pending['record'] and service.model_revision == 2
        assert (await runtime.models.status(UID))['conflict'] is True
        assert service.puts[0]['record']['model']['modelFile'] == 'C.model3.json'
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['resource-ready', 'head', 'upload-commit', 'put-issued'])
async def test_selection_changes_during_pending_requests_use_the_current_model_next(tmp_path, monkeypatch, boundary):
    runtime, service, adapter, _ = await pending_model_runtime(tmp_path)
    changed = False
    async def switch(request):
        nonlocal changed
        if not changed and ((boundary == 'resource-ready' and request.url.path.endswith('/resources'))
                or (boundary == 'head' and request.method == 'GET' and request.url.path.endswith('/model-binding'))
                or (boundary == 'put-issued' and request.method == 'PUT')):
            adapter.path = '/C.model3.json'
            changed = True
    service.hook = switch
    original_put = runtime.models.store.put
    def switch_after_upload(key, state):
        nonlocal changed
        original_put(key, state)
        if boundary == 'upload-commit' and not changed and state.get('pending', {}).get('uploaded'):
            adapter.path = '/C.model3.json'
            changed = True
    monkeypatch.setattr(runtime.models.store, 'put', switch_after_upload)
    try:
        await runtime.models.step(UID)
        assert len(service.puts) == (1 if boundary == 'put-issued' else 0)
        await runtime.models.step(UID)
        assert service.model['model']['modelFile'] == 'C.model3.json'
        assert adapter.path == '/C.model3.json'
        assert not (await runtime.models.status(UID))['failed']
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_model_changed_during_upload_stops_chunks_without_blocking_the_scheduler(tmp_path, monkeypatch):
    from pathlib import Path
    runtime, service, adapter, pending = await pending_model_runtime(tmp_path, large=True)
    service.ready = False
    chunks_sent = []

    class UploadTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            assert str(request.url) == 'https://oss.example/model.zip'
            assert request.headers.get('Authorization') is None
            async for chunk in request.stream:
                chunks_sent.append(chunk)
                adapter.path = '/C.model3.json'
            return httpx.Response(204)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(transport=UploadTransport(), **kwargs))
    original = Path(pending['file']['path']).read_bytes()
    try:
        binding = runtime.state.binding(UID)
        presence = await runtime.session_presence_provider()
        await runtime._automatic_models([binding], presence)
        assert service.puts == []
        assert len(chunks_sent) == 1 and len(chunks_sent[0]) == 65536
        assert not (await runtime.models.status(UID))['failed']
        assert runtime.scheduler.ready(DOMAIN.key(), 'model', binding['generation'], presence)
        await runtime.models.step(UID)
        assert service.model['model']['modelFile'] == 'C.model3.json'
        assert Path(pending['file']['path']).read_bytes() == original
    finally:
        await runtime.close()

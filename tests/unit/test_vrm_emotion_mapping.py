"""VRM emotion mappings must preserve uploaded filenames and path boundaries."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
import httpx
import pytest
import pytest_asyncio

from main_routers import vrm_router
from main_routers.characters_router import live2d_models
from main_routers.config_router.page_config import _resolve_vrm_path
from utils.config_manager import get_reserved


pytestmark = pytest.mark.unit
API = "/api/model/vrm"


@pytest.fixture
def vrm_api(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    (project_root / "static" / "vrm").mkdir(parents=True)
    user_dir = tmp_path / "user_vrm"
    user_dir.mkdir()
    config = SimpleNamespace(
        project_root=project_root,
        vrm_dir=user_dir,
        ensure_vrm_directory=lambda: True,
        aload_characters=AsyncMock(return_value={"猫娘": {"Test": {}}}),
        asave_characters=AsyncMock(),
    )
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: config)
    monkeypatch.setattr(live2d_models, "get_config_manager", lambda: config)
    monkeypatch.setattr(live2d_models, "get_init_one_catgirl", lambda: AsyncMock())
    monkeypatch.setattr(
        vrm_router,
        "get_subscribed_workshop_items",
        AsyncMock(return_value={"success": True, "items": []}),
    )
    app = FastAPI()
    app.include_router(vrm_router.router)
    app.include_router(live2d_models.router)
    app.mount("/user_vrm", StaticFiles(directory=user_dir))
    app.mount("/static/vrm", StaticFiles(directory=project_root / "static" / "vrm"))
    with TestClient(app) as client:
        yield client, config


@pytest_asyncio.fixture
async def vrm_async_api(vrm_api):
    client, config = vrm_api
    # Starlette 1.3.1 TestClient unquotes HTTPX's already-decoded URL path.
    # ASGITransport preserves the server's single-decode semantics for literal percent names.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://testserver",
    ) as async_client:
        yield async_client, config, client.app


@pytest.mark.parametrize(
    "model_name",
    ["Avatar", "My Avatar", "Avatar(1)", "Avatar[1]", "猫娘", "猫娘 🐱",
     "Cafe\u0301", "Avatar.v1_2-test", "Avatar#100%", "a%20b", "a%2Fb"],
)
@pytest.mark.parametrize("extension", [".vrm", ".VRM", ".VrM"])
@pytest.mark.asyncio
async def test_uploaded_model_emotion_mapping_roundtrip(vrm_async_api, model_name, extension):
    client, config, _ = vrm_async_api
    filename = f"{model_name}{extension}"
    # Upload routes store opaque bytes; VRM rendering is outside this test.
    content = b"glTF"
    uploaded = await client.post(f"{API}/upload", files={"file": (filename, content)})
    assert uploaded.status_code == 200
    assert uploaded.json()["model_name"] == model_name
    model_url = uploaded.json()["model_url"]
    assert model_url == f"/user_vrm/{quote(filename, safe='')}"
    fetched = await client.get(model_url)
    assert fetched.status_code == 200
    assert fetched.content == content
    models = (await client.get(f"{API}/models")).json()["models"]
    assert any(model["name"] == model_name and model["filename"] == filename
               and model["url"] == model_url and model["path"] == f"/user_vrm/{filename}"
               for model in models)
    model = next(model for model in models if model["filename"] == filename)
    saved_character = await client.put('/api/characters/catgirl/l2d/Test', json={
        'model_type': 'live3d', 'vrm': model['path'], 'apply_runtime': False,
    })
    assert saved_character.status_code == 200
    characters = config.asave_characters.call_args.args[0]
    persisted = get_reserved(characters['猫娘']['Test'], 'avatar', 'vrm', 'model_path')
    assert persisted == model['path']
    resolved_url = _resolve_vrm_path(persisted, config, 'Test')
    assert resolved_url == model_url
    assert (await client.get(resolved_url)).content == content

    url = f"{API}/emotion_mapping/{quote(model_name, safe='')}"
    loaded = await client.get(url)
    assert loaded.status_code == 200
    assert loaded.json()["config"] == vrm_router.DEFAULT_MOOD_MAP
    mapping = {"happy": ["custom smile"], "sad": ["custom sadness"]}
    saved = await client.post(url, json=mapping)
    assert saved.status_code == 200
    assert saved.json()["success"] is True
    assert (await client.get(url)).json()["config"] == mapping
    config_path = config.project_root / "static" / "vrm" / "configs" / f"{model_name}_emotion.json"
    assert json.loads(config_path.read_text(encoding="utf-8")) == mapping
    assert (config.vrm_dir / filename).read_bytes() == content


def test_bundled_model_with_punctuation_can_save_mapping(vrm_api):
    client, config = vrm_api
    model_name = "Built-in Avatar(1)"
    (config.project_root / "static" / "vrm" / f"{model_name}.vrm").write_bytes(b"glTF")
    url = f"{API}/emotion_mapping/{quote(model_name, safe='')}"
    mapping = {"happy": ["custom smile"]}
    assert client.post(url, json=mapping).status_code == 200
    assert client.get(url).json()["config"] == mapping


@pytest.mark.parametrize("location", ["builtin", "user"])
@pytest.mark.parametrize("extension", [".VRM", ".VrM"])
def test_existing_models_with_uppercase_extensions_can_save_mapping(vrm_api, location, extension):
    client, config = vrm_api
    model_name = "Existing Avatar(1)"
    directory = (config.project_root / "static" / "vrm"
                 if location == "builtin" else config.vrm_dir)
    filename = f"{model_name}{extension}"
    model_path = directory / filename
    model_path.write_bytes(b"existing model sentinel")
    models = client.get(f"{API}/models").json()["models"]
    model = next(model for model in models if model["filename"] == filename)
    assert model["name"] == model_name
    fetched = client.get(model["url"])
    assert fetched.status_code == 200
    assert fetched.content == b"existing model sentinel"
    url = f"{API}/emotion_mapping/{quote(model_name, safe='')}"
    mapping = {"happy": ["existing model smile"]}
    assert client.post(url, json=mapping).status_code == 200
    assert client.get(url).json()["config"] == mapping
    assert model_path.read_bytes() == b"existing model sentinel"
    assert {path.name for path in directory.iterdir() if path.is_file()} == {filename}


@pytest.mark.parametrize("filename", [
    "What?.vrm", "model:stream.vrm", "model*.vrm", "model|stream.vrm", "<model>.vrm", ".vrm",
    "..vrm", "...vrm",
])
def test_invalid_upload_names_are_rejected_without_writes(vrm_api, filename):
    client, config = vrm_api
    response = client.post(f"{API}/upload", files={"file": (filename, b"glTF")})
    assert response.status_code == 400
    assert response.json()["success"] is False
    assert not list(config.vrm_dir.iterdir())
    assert not (config.project_root / "static" / "vrm" / "configs").exists()


def test_model_lookup_requires_matching_stem_and_extension(vrm_api):
    client, config = vrm_api
    for filename in ["Other.VRM", "Avatar.vrma", "Avatar.vrm.bak"]:
        (config.vrm_dir / filename).write_bytes(b"unrelated sentinel")
    (config.vrm_dir / "Avatar.VRM").mkdir()
    response = client.post(f"{API}/emotion_mapping/Avatar", json={"happy": ["smile"]})
    assert response.status_code == 404
    assert not (config.project_root / "static" / "vrm" / "configs").exists()
    models = client.get(f"{API}/models").json()["models"]
    assert [model["filename"] for model in models] == ["Other.VRM"]


@pytest.mark.parametrize("names", [("Avatar(1)", "Avatar1"), ("My Avatar", "MyAvatar")])
def test_distinct_model_names_keep_separate_mappings(vrm_api, names):
    client, config = vrm_api
    for index, name in enumerate(names):
        (config.vrm_dir / f"{name}.vrm").write_bytes(b"glTF")
        url = f"{API}/emotion_mapping/{quote(name, safe='')}"
        assert client.post(url, json={"happy": [f"expression-{index}"]}).status_code == 200
    for index, name in enumerate(names):
        url = f"{API}/emotion_mapping/{quote(name, safe='')}"
        assert client.get(url).json()["config"] == {"happy": [f"expression-{index}"]}
    assert len(list((config.project_root / "static" / "vrm" / "configs").glob("*.json"))) == 2


@pytest.mark.parametrize(
    "model_name",
    ["", ".", "..", "../outside", "folder/model", r"folder\model", "/outside",
     r"C:\outside", "C:outside", "model:stream", "model\x00", "model\n",
     "model?", "model*", "<model>", '"model"', "model|stream"],
)
def test_invalid_model_names_are_rejected_without_writes(vrm_api, model_name):
    _, config = vrm_api
    assert vrm_router._get_emotion_config_path(model_name) is None
    assert vrm_router._get_model_path(model_name) == (None, "")
    assert not (config.project_root / "static" / "vrm" / "configs").exists()
    assert not list(config.vrm_dir.iterdir())


def test_missing_model_does_not_create_emotion_mapping(vrm_api):
    client, config = vrm_api
    response = client.post(f"{API}/emotion_mapping/Missing%20Avatar", json={"happy": ["smile"]})
    assert response.status_code == 404
    assert not (config.project_root / "static" / "vrm" / "configs").exists()


@pytest.mark.parametrize('extension', ['.VRM', '.VrM'])
@pytest.mark.parametrize('location', ['user', 'builtin'])
@pytest.mark.parametrize('existing_name', ['Avatar', 'avatar'])
def test_upload_rejects_same_stem_without_changing_existing_file(vrm_api, extension, location, existing_name):
    client, config = vrm_api
    directory = config.vrm_dir if location == 'user' else config.project_root / 'static' / 'vrm'
    original = directory / f'{existing_name}.vrm'
    original.write_bytes(b'original model')
    result = client.post(f'{API}/upload', files={'file': (f'Avatar{extension}', b'new model')})
    assert result.status_code == 400
    assert original.read_bytes() == b'original model'
    assert {p.name for p in directory.iterdir()} == {f'{existing_name}.vrm'}
    if location == 'builtin':
        assert not list(config.vrm_dir.iterdir())


@pytest.mark.parametrize('extension', ['.vrm', '.VRM', '.VrM'])
@pytest.mark.parametrize('builtin_name', ['Avatar', 'avatar'])
@pytest.mark.parametrize('by_url', [False, True])
def test_deleting_user_model_preserves_builtin_shared_mapping(vrm_api, extension, builtin_name, by_url):
    client, config = vrm_api
    builtin = config.project_root / 'static' / 'vrm' / f'{builtin_name}{extension}'
    builtin.write_bytes(b'builtin model')
    user = config.vrm_dir / 'Avatar.vrm'
    user.write_bytes(b'user model')
    mapping = {'happy': ['shared smile']}
    assert client.post(f'{API}/emotion_mapping/Avatar', json=mapping).status_code == 200
    config_dir = config.project_root / 'static' / 'vrm' / 'configs'
    shares_mapping = (config_dir / f'{builtin_name}_emotion.json').exists()
    deleted = (client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/Avatar.vrm'})
               if by_url else client.delete(f'{API}/model/Avatar'))
    assert deleted.status_code == 200
    assert not user.exists()
    assert builtin.read_bytes() == b'builtin model'
    if shares_mapping:
        assert client.get(f'{API}/emotion_mapping/Avatar').json()['config'] == mapping
    else:
        assert not (config_dir / 'Avatar_emotion.json').exists()


@pytest.mark.parametrize('by_url', [False, True])
def test_deleting_last_uppercase_model_removes_mapping(vrm_api, by_url):
    client, config = vrm_api
    model = config.vrm_dir / 'Avatar.VRM'
    model.write_bytes(b'model')
    assert client.post(f'{API}/emotion_mapping/Avatar', json={'happy': ['smile']}).status_code == 200
    deleted = (client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/Avatar.VRM'})
               if by_url else client.delete(f'{API}/model/Avatar'))
    assert deleted.status_code == 200
    assert not model.exists()
    assert not (config.project_root / 'static' / 'vrm' / 'configs' / 'Avatar_emotion.json').exists()


@pytest.mark.parametrize('by_url', [False, True])
@pytest.mark.parametrize('subdir', ['', 'nested'])
def test_deleting_user_model_preserves_workshop_mapping(vrm_api, tmp_path, monkeypatch, by_url, subdir):
    client, config = vrm_api
    item = tmp_path / 'workshop' / '123'
    directory = item / subdir
    directory.mkdir(parents=True)
    (directory / 'Avatar.VRM').write_bytes(b'workshop sentinel')
    monkeypatch.setattr(vrm_router, 'get_subscribed_workshop_items', AsyncMock(return_value={
        'success': True, 'items': [{'installedFolder': str(item), 'publishedFileId': '123'}],
    }))
    (config.vrm_dir / 'Avatar.vrm').write_bytes(b'user model')
    mapping = {'happy': ['workshop smile']}
    assert client.post(f'{API}/emotion_mapping/Avatar', json=mapping).status_code == 200
    response = (client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/Avatar.vrm'})
                if by_url else client.delete(f'{API}/model/Avatar'))
    assert response.status_code == 200
    assert not (config.vrm_dir / 'Avatar.vrm').exists()
    assert (directory / 'Avatar.VRM').read_bytes() == b'workshop sentinel'
    assert client.get(f'{API}/emotion_mapping/Avatar').json()['config'] == mapping


@pytest.mark.parametrize('by_url', [False, True])
@pytest.mark.parametrize('failure', ['response', 'exception'])
def test_delete_preserves_mapping_if_workshop_check_fails(vrm_api, monkeypatch, by_url, failure):
    client, config = vrm_api
    (config.vrm_dir / 'Avatar.vrm').write_bytes(b'user model')
    mapping = {'happy': ['smile']}
    assert client.post(f'{API}/emotion_mapping/Avatar', json=mapping).status_code == 200
    query = AsyncMock(return_value={'success': False})
    if failure == 'exception':
        query.side_effect = RuntimeError('workshop unavailable')
    monkeypatch.setattr(vrm_router, 'get_subscribed_workshop_items', query)
    response = (client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/Avatar.vrm'})
                if by_url else client.delete(f'{API}/model/Avatar'))
    assert response.status_code == 200
    assert client.get(f'{API}/emotion_mapping/Avatar').json()['config'] == mapping


def test_existing_same_stem_files_remain_listed_in_mapping_precedence(vrm_api):
    client, config = vrm_api
    upper = config.vrm_dir / 'Avatar.VRM'
    lower = config.vrm_dir / 'Avatar.vrm'
    upper.write_bytes(b'upper sentinel')
    lower.write_bytes(b'lower sentinel')
    if upper.read_bytes() != b'upper sentinel':
        pytest.skip('Requires a case-sensitive filesystem')
    models = client.get(f'{API}/models').json()['models']
    assert [m['filename'] for m in models] == ['Avatar.vrm', 'Avatar.VRM']
    assert vrm_router._get_model_path('Avatar')[0] == lower.resolve()
    mapping = {'happy': ['shared smile']}
    assert client.post(f'{API}/emotion_mapping/Avatar', json=mapping).status_code == 200
    assert client.delete(f'{API}/model/Avatar').status_code == 200
    assert upper.read_bytes() == b'upper sentinel'
    assert client.get(f'{API}/emotion_mapping/Avatar').json()['config'] == mapping


@pytest.mark.parametrize('location', ['builtin', 'user'])
@pytest.mark.parametrize('filename', ['My Avatar.vrm', '猫娘.VRM', 'a b.vrm', 'a%20b.vrm', 'Avatar#100%.vrm'])
def test_existing_raw_config_paths_produce_encoded_fetch_urls(vrm_api, location, filename):
    client, config = vrm_api
    directory = config.vrm_dir if location == 'user' else config.project_root / 'static' / 'vrm'
    prefix = '/user_vrm' if location == 'user' else '/static/vrm'
    (directory / filename).write_bytes(b'existing model')
    for reference in [f'{prefix}/{filename}', filename]:
        url = _resolve_vrm_path(reference, config, 'Test')
        assert url == f'{prefix}/{quote(filename, safe="")}'
    assert _resolve_vrm_path('https://example.com/a%20b.vrm', config, 'Test') == 'https://example.com/a%20b.vrm'
    custom_url = '/api/models/current.vrm?token=abc#part'
    assert _resolve_vrm_path(custom_url, config, 'Test') == custom_url
    assert _resolve_vrm_path('/workshop/123/猫娘#100%.VRM', config, 'Test') == '/workshop/123/%E7%8C%AB%E5%A8%98%23100%25.VRM'


def test_delete_preserves_mapping_when_remaining_models_cannot_be_checked(vrm_api, monkeypatch):
    client, config = vrm_api
    (config.vrm_dir / 'Avatar.vrm').write_bytes(b'model')
    assert client.post(f'{API}/emotion_mapping/Avatar', json={'happy': ['smile']}).status_code == 200

    def inaccessible(directory):
        raise PermissionError('directory unavailable')

    monkeypatch.setattr(vrm_router, '_iter_vrm_model_files', inaccessible)
    # URL deletion resolves an exact file independently of the mapping lookup.
    assert client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/Avatar.vrm'}).status_code == 200
    assert client.get(f'{API}/emotion_mapping/Avatar').json()['config'] == {'happy': ['smile']}


@pytest.mark.asyncio
async def test_encoded_model_urls_do_not_alias_other_filenames(vrm_async_api):
    client, config, _ = vrm_async_api
    filenames = {"a b.vrm": b"space sentinel", "a%20b.vrm": b"literal percent sentinel"}
    for filename, content in filenames.items():
        assert (await client.post(f"{API}/upload", files={"file": (filename, content)})).status_code == 200
    models = (await client.get(f"{API}/models")).json()["models"]
    for model in models:
        fetched = await client.get(model["url"])
        assert fetched.status_code == 200
        assert fetched.content == filenames[model["filename"]]
    literal_model = next(model for model in models if model["filename"] == "a%20b.vrm")
    deleted = await client.request("DELETE", f"{API}/model", json={"url": literal_model["url"]})
    assert deleted.status_code == 200
    assert not (config.vrm_dir / "a%20b.vrm").exists()
    assert (config.vrm_dir / "a b.vrm").read_bytes() == b"space sentinel"


@pytest.mark.parametrize("filename", ["Avatar.vrm", "v1..2.vrm", "My Avatar.vrm", "猫娘 🐱.VRM", "Avatar#100%.vrm", "a%20b.vrm", "a%2Fb.vrm"])
def test_delete_decodes_model_url_once(vrm_api, filename):
    client, config = vrm_api
    target = config.vrm_dir / filename
    target.write_bytes(b"delete target")
    other = config.vrm_dir / "Other.vrm"
    other.write_bytes(b"other sentinel")
    segment = quote(filename, safe='')
    response = client.request("DELETE", f"{API}/model", json={"url": f"/user_vrm/{segment}"})
    assert response.status_code == 200
    assert not target.exists()
    assert other.read_bytes() == b"other sentinel"


@pytest.mark.parametrize("filename", ["What?.vrm", 'Old:Avatar.VRM', 'Old*Avatar.vrm', 'Old|Avatar.vrm', 'Old"Avatar.vrm', 'Old<Avatar>.vrm'])
@pytest.mark.parametrize("by_url", [False, True])
def test_legacy_files_can_be_deleted_without_relaxing_upload_policy(vrm_api, filename, by_url):
    client, config = vrm_api
    assert not vrm_router._is_valid_vrm_model_name(filename[:-4])
    assert vrm_router._is_vrm_basename(filename)
    target = config.vrm_dir / filename
    try:
        target.write_bytes(b"legacy model")
    except OSError:
        pytest.skip("This filesystem cannot create the legacy filename")
    other = config.vrm_dir / "Other.vrm"
    other.write_bytes(b"sentinel")
    # The validator assertion above checks raw legacy names. Multipart clients
    # escape quotes as %22, which is a different, valid literal filename.
    response = (client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/' + quote(filename, safe='')})
                if by_url else client.delete(f'{API}/model/' + quote(filename[:-4], safe='')))
    assert response.status_code == 200
    assert not target.exists()
    assert other.read_bytes() == b"sentinel"


@pytest.mark.parametrize("by_url", [False, True])
def test_delete_never_interprets_a_legacy_name_as_a_drive_prefix(vrm_api, by_url):
    client, config = vrm_api
    filename = "C:Other.vrm"
    other = config.vrm_dir / "Other.vrm"
    other.write_bytes(b"other sentinel")
    target = config.vrm_dir / filename
    is_alias = target.name != filename
    if not is_alias:
        target.write_bytes(b"legacy colon filename")
    response = (client.request('DELETE', f'{API}/model', json={'url': '/user_vrm/' + quote(filename, safe='')})
                if by_url else client.delete(f'{API}/model/' + quote(filename[:-4], safe='')))
    assert response.status_code == (400 if is_alias else 200)
    assert other.read_bytes() == b"other sentinel"
    if not is_alias:
        assert not target.exists()


@pytest.mark.parametrize("space_model_exists", [False, True])
def test_delete_never_falls_back_to_encoded_filename(vrm_api, space_model_exists):
    client, config = vrm_api
    literal = config.vrm_dir / "a%20b.vrm"
    literal.write_bytes(b"literal percent sentinel")
    space = config.vrm_dir / "a b.vrm"
    if space_model_exists:
        space.write_bytes(b"space sentinel")
    response = client.request("DELETE", f"{API}/model", json={"url": "/user_vrm/a%20b.vrm"})
    assert response.status_code == (200 if space_model_exists else 404)
    assert not space.exists()
    assert literal.read_bytes() == b"literal percent sentinel"


@pytest.mark.parametrize("url", [
    "/user_vrm/../outside.vrm", "/user_vrm/%2e%2e%2foutside.vrm",
    "/user_vrm/%2e%2e%5coutside.vrm", "/user_vrm/%2foutside.vrm",
    "/user_vrm/%00.vrm", "/static/vrm/outside.vrm",
])
def test_delete_rejects_encoded_path_syntax_without_removing_files(vrm_api, tmp_path, url):
    client, config = vrm_api
    outside = tmp_path / "outside.vrm"
    outside.write_bytes(b"outside sentinel")
    inside = config.vrm_dir / "Inside.vrm"
    inside.write_bytes(b"inside sentinel")
    response = client.request("DELETE", f"{API}/model", json={"url": url})
    assert response.status_code == 400
    assert outside.read_bytes() == b"outside sentinel"
    assert inside.read_bytes() == b"inside sentinel"


@pytest.mark.asyncio
async def test_workshop_model_urls_encode_each_filename_segment(vrm_async_api, tmp_path, monkeypatch):
    client, _, app = vrm_async_api
    workshop_root = tmp_path / "workshop"
    item = workshop_root / "123"
    subdirectory = item / "Folder#20%"
    subdirectory.mkdir(parents=True)
    for directory, filename in [(item, "Avatar#100%.vrm"), (subdirectory, "a%20b.VRM")]:
        (directory / filename).write_bytes(filename.encode())
    app.mount("/workshop", StaticFiles(directory=workshop_root))
    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", AsyncMock(return_value={
        "success": True, "items": [{"installedFolder": str(item), "publishedFileId": "123"}],
    }))
    models = (await client.get(f"{API}/models")).json()["models"]
    assert len(models) == 2
    for model in models:
        fetched = await client.get(model["url"])
        assert fetched.status_code == 200
        assert fetched.content == model["filename"].encode()


@pytest.mark.parametrize("location,extension", [
    ("config", ".vrm"), ("builtin", ".vrm"), ("user", ".vrm"),
    ("builtin", ".VRM"), ("user", ".VRM"),
])
def test_outside_resolved_paths_are_rejected(vrm_api, tmp_path, monkeypatch, location, extension):
    client, config = vrm_api
    name = "My Avatar(1)"
    static_dir = config.project_root / "static" / "vrm"
    if location == "config":
        directory = static_dir / "configs"
        directory.mkdir()
        candidate = directory / f"{name}_emotion.json"
    else:
        directory = static_dir if location == "builtin" else config.vrm_dir
        candidate = directory / f"{name}{extension}"
    candidate.write_bytes(b"inside sentinel")
    outside = tmp_path / "outside.vrm"
    outside.write_bytes(b"outside sentinel")
    original_resolve = type(candidate).resolve

    def resolve(path, *args, **kwargs):
        if path == candidate:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(type(candidate), "resolve", resolve)
    if location == "config":
        assert vrm_router._get_emotion_config_path(name) is None
    else:
        assert vrm_router._get_model_path(name) == (None, "")
        if location == "user":
            response = client.request("DELETE", f"{API}/model", json={
                "url": f"/user_vrm/{quote(candidate.name, safe='')}",
            })
            assert response.status_code == 400
    assert candidate.read_bytes() == b"inside sentinel"
    assert outside.read_bytes() == b"outside sentinel"


@pytest.mark.parametrize("location,extension", [
    ("config", ".vrm"), ("builtin", ".vrm"), ("user", ".vrm"),
    ("builtin", ".VRM"), ("user", ".VRM"),
])
def test_resolved_paths_cannot_escape_their_directory(vrm_api, tmp_path, location, extension):
    _, config = vrm_api
    name = "My Avatar(1)"
    outside = tmp_path / "outside.vrm"
    outside.write_bytes(b"outside sentinel")
    static_dir = config.project_root / "static" / "vrm"
    if location == "config":
        config_dir = static_dir / "configs"
        config_dir.mkdir()
        link = config_dir / f"{name}_emotion.json"
    else:
        model_dir = static_dir if location == "builtin" else config.vrm_dir
        link = model_dir / f"{name}{extension}"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")

    if location == "config":
        assert vrm_router._get_emotion_config_path(name) is None
    else:
        assert vrm_router._get_model_path(name) == (None, "")
    assert outside.read_bytes() == b"outside sentinel"

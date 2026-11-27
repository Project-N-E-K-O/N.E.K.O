"""Model transfer copies preserve sources, dependencies, and immutable received artifacts."""

import hashlib
import io
import json
import zipfile

from PIL import Image
import pytest

from services.chat_sync.model_package import dependencies, install_model, package_model


def test_sync_resolves_the_relative_binding_written_by_the_model_selector(tmp_path, monkeypatch):
    from main_routers import live2d_router
    from services.chat_sync.model_sync import DesktopModelAdapter
    source = tmp_path / 'Local model'
    source.mkdir()
    entry = source / 'custom export.model3.json'
    entry.write_text('{"Version":3}')
    monkeypatch.setattr(live2d_router, 'find_model_directory', lambda name: (str(source), '/user_live2d')
                        if name == 'Local model' else (None, None))
    adapter = DesktopModelAdapter(object())
    assert adapter.source('Local model/custom export.model3.json') == entry
    assert adapter.source('Local%20model/custom%20export.model3.json') == entry
    assert adapter.source('Local model/../outside.model3.json') is None
    assert adapter.source('https://external.example/Local model/custom export.model3.json') is None


def test_sync_resolves_a_builtin_model_name_without_rebinding_it(tmp_path, monkeypatch):
    from main_routers import live2d_router
    from services.chat_sync.model_sync import DesktopModelAdapter
    source = tmp_path / 'builtin'
    source.mkdir()
    entry = source / 'builtin.model3.json'
    entry.write_text('{"Version":3}')
    monkeypatch.setattr(live2d_router, 'find_model_directory', lambda name: (str(source), '/static')
                        if name == 'builtin' else (None, None))
    assert DesktopModelAdapter(object()).source('builtin') == entry


def model_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    texture = source / "透明 贴图.png"
    Image.new("RGBA", (2048, 2048), (220, 100, 80, 100)).save(texture)
    (source / "model.moc3").write_bytes(b"MOC3" + b"x" * 300)
    (source / "动作.motion3.json").write_text('{"Version":3,"Curves":[]}')
    (source / "extra.exp3.json").write_text('{"Type":"Live2D Expression","Parameters":[]}')
    (source / "user-settings.json").write_text('{"private":"device preference"}')
    model = source / "角色.model3.json"
    model.write_text(json.dumps({"Version": 3, "FileReferences": {"Moc": "model.moc3", "Textures": [texture.name],
        "Motions": {"Idle": [{"File": "动作.motion3.json"}]}}}, ensure_ascii=False), encoding="utf-8")
    return source, model


@pytest.mark.parametrize("model", [None, [], "model", 1,
    {"FileReferences": {"Moc": "model.moc3", "Textures": [None]}},
    {"FileReferences": {"Moc": "model.moc3", "Textures": [{}]}},
    {"FileReferences": {"Moc": "model.moc3", "Textures": [[]]}},
    {"FileReferences": {"Moc": "model.moc3", "Textures": [1]}}])
def test_malformed_model_dependencies_raise_a_validation_error(model):
    with pytest.raises(ValueError, match="invalid_cloud_model"):
        dependencies(json.dumps(model).encode("utf-8"))


@pytest.mark.parametrize("failure", ["missing-dependency", "filename", "size"])
def test_failed_package_removes_only_its_new_partial_archive(tmp_path, monkeypatch, failure):
    from services.chat_sync import model_package
    source, model = model_fixture(tmp_path)
    original = model.read_bytes()
    if failure == "missing-dependency":
        (source / "model.moc3").unlink()
    elif failure == "filename":
        (source / "invalid%name.exp3.json").write_text("{}", encoding="utf-8")
    else:
        monkeypatch.setattr(model_package, "MAX_MODEL_BYTES", 200)
    destination = tmp_path / "packages"
    destination.mkdir()
    existing = destination / "existing.zip"
    existing.write_bytes(b"previous package")
    with pytest.raises((ValueError, OSError)):
        package_model(model, destination)
    assert list(destination.iterdir()) == [existing]
    assert existing.read_bytes() == b"previous package"
    assert model.read_bytes() == original


def test_compressed_roundtrip_preserves_source_alpha_and_exact_received_package(tmp_path):
    source, model = model_fixture(tmp_path)
    original = {item.name: item.read_bytes() for item in source.iterdir()}
    binding, artifact = package_model(model, tmp_path / "packages")
    archive = tmp_path / "packages" / (artifact["resourceId"] + ".zip")
    assert binding["model"]["compression"]["textureMaxEdge"] == 2048
    assert "extra.exp3.json" in {entry["path"] for entry in binding["model"]["files"]}
    assert "user-settings.json" not in {entry["path"] for entry in binding["model"]["files"]}
    assert {item.name: item.read_bytes() for item in source.iterdir()} == original
    installed = install_model(archive, binding, tmp_path / "installed")
    assert installed.is_file()
    with Image.open(installed.parent / "透明 贴图.png") as image:
        assert image.size == (2048, 2048)
        assert image.getpixel((10, 10))[3] == 100
    returned, copy = package_model(installed, tmp_path / "returned")
    assert returned == binding
    assert copy["sha256"] == artifact["sha256"]
    assert __import__("pathlib").Path(copy["path"]).read_bytes() == archive.read_bytes()
    assert not (tmp_path / "returned").exists()


def test_eight_k_texture_is_compressed_without_modifying_the_source(tmp_path):
    source, model = model_fixture(tmp_path)
    texture = source / '透明 贴图.png'
    with Image.new('RGBA', (8192, 8192), (220, 100, 80, 100)) as image:
        image.save(texture)
    original_hash = hashlib.sha256(texture.read_bytes()).hexdigest()
    binding, artifact = package_model(model, tmp_path / 'packages')
    installed = install_model(artifact['path'], binding, tmp_path / 'installed')
    with Image.open(installed.parent / texture.name) as image:
        assert image.size == (2048, 2048)
        assert image.getpixel((10, 10))[3] == 100
    assert hashlib.sha256(texture.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("size,expected", [((640, 320), (640, 320)), ((2048, 1024), (2048, 1024)), ((4096, 2048), (2048, 1024))])
def test_texture_limit_preserves_aspect_ratio_and_keeps_smaller_sources_exact(tmp_path, size, expected):
    source, model = model_fixture(tmp_path)
    texture = source / "透明 贴图.png"
    with Image.new("RGBA", size, (220, 100, 80, 100)) as image:
        image.save(texture)
    original = texture.read_bytes()
    _, artifact = package_model(model, tmp_path / "packages")
    with zipfile.ZipFile(artifact["path"]) as archive:
        received = archive.read(texture.name)
    with Image.open(io.BytesIO(received)) as image:
        assert image.size == expected
        assert image.getpixel((10, 10))[3] == 100
    if size == expected:
        assert received == original
    assert texture.read_bytes() == original


def test_legacy_1024_received_package_is_reused_without_upscaling_or_recompression(tmp_path, monkeypatch):
    from services.chat_sync import model_package
    _, model = model_fixture(tmp_path)
    monkeypatch.setattr(model_package, "TEXTURE_MAX_EDGE", 1024)
    binding, artifact = package_model(model, tmp_path / "legacy")
    installed = install_model(artifact["path"], binding, tmp_path / "installed")
    monkeypatch.setattr(model_package, "TEXTURE_MAX_EDGE", 2048)
    returned, copy = package_model(installed, tmp_path / "returned")
    assert returned == binding
    assert returned["model"]["compression"]["textureMaxEdge"] == 1024
    assert copy["sha256"] == artifact["sha256"]
    assert __import__("pathlib").Path(copy["path"]).read_bytes() == __import__("pathlib").Path(artifact["path"]).read_bytes()
    assert not (tmp_path / "returned").exists()


@pytest.mark.parametrize("mutation", ["hash", "missing", "traversal", "duplicate", "symlink", "entry-size"])
def test_invalid_packages_never_install_or_overwrite_a_working_model(tmp_path, mutation):
    _, model = model_fixture(tmp_path)
    binding, artifact = package_model(model, tmp_path / "packages")
    original_path = __import__("pathlib").Path(artifact["path"])
    installed = install_model(original_path, binding, tmp_path / "installed")
    before = installed.read_bytes()
    with zipfile.ZipFile(original_path) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (entry, raw) in enumerate(entries):
            if index == 0:
                if mutation == "missing":
                    continue
                if mutation == "hash":
                    raw += b"corrupt"
                if mutation == "entry-size":
                    raw += b" " * (1024 * 1024)
                    binding["model"]["files"][0].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                if mutation == "traversal":
                    entry.filename = "../outside.model3.json"
                if mutation == "symlink":
                    entry.external_attr = 0o120777 << 16
                if mutation == "duplicate":
                    archive.writestr(entry, raw)
            archive.writestr(entry, raw)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(output.getvalue())
    binding["model"]["package"].update(size=bad.stat().st_size, sha256=hashlib.sha256(bad.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        install_model(bad, binding, tmp_path / "installed")
    assert installed.read_bytes() == before
    assert not (tmp_path / "outside.model3.json").exists()


def test_sync_model_resolver_rejects_absolute_drives_and_escaped_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from main_routers import live2d_router
    root = tmp_path / 'models'; root.mkdir()
    outside = tmp_path / 'outside.model3.json'; outside.write_text('{}')
    (root / 'escape.model3.json').symlink_to(outside)
    config = SimpleNamespace(live2d_dir=root)
    for path in ['user_live2d/C:/outside.model3.json', '/user_live2d/model.model3.json',
                 'user_live2d/%43%3A/outside.model3.json', 'user_live2d/escape.model3.json']:
        assert live2d_router._resolve_sync_model_file(path, config) is None
    (root / 'safe.model3.json').write_text('{}')
    assert live2d_router._resolve_sync_model_file('user_live2d/safe.model3.json', config) == root / 'safe.model3.json'


@pytest.mark.parametrize('prefix', ['user_live2d', 'user_live2d_local', 'static', 'workshop'])
def test_sync_resolves_root_relative_model_urls_inside_their_allowed_root(tmp_path, monkeypatch, prefix):
    from types import SimpleNamespace
    from main_routers import live2d_router
    from services.chat_sync.model_sync import DesktopModelAdapter

    root = tmp_path / prefix
    source = root / 'cloud-sync' / 'model folder' / 'model.model3.json'
    source.parent.mkdir(parents=True)
    source.write_text('{"Version":3}')
    if prefix == 'static':
        monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(live2d_router, 'get_workshop_path', lambda: str(root))
    config = SimpleNamespace(live2d_dir=root, readable_live2d_dir=root)
    adapter = DesktopModelAdapter(config)
    path = f'/{prefix}/cloud-sync/model%20folder/model.model3.json'
    assert adapter.source(path) == source
    assert adapter.source(path[1:]) == source
    assert adapter.source('/' + path) is None
    outside = tmp_path / 'outside.model3.json'
    outside.write_text('{}')
    (root / 'escape.model3.json').symlink_to(outside)
    assert adapter.source(f'/{prefix}/escape.model3.json') is None
    assert adapter.source(f'/{prefix}/../outside.model3.json') is None
    assert adapter.source(str(outside)) is None


def test_concurrent_model_install_reuses_verified_winner_and_removes_own_stage(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    _, model = model_fixture(tmp_path)
    binding, artifact = package_model(model, tmp_path / 'packages')
    original = Path.rename
    def raced(source, target):
        if source.name.endswith('.part'):
            shutil.copytree(source, target)
            raise FileExistsError('another installer completed first')
        return original(source, target)
    monkeypatch.setattr(Path, 'rename', raced)
    installed = install_model(artifact['path'], binding, tmp_path / 'installed')
    assert installed.is_file()
    assert list((tmp_path / 'installed').glob('*.part')) == []

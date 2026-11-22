import json
from types import SimpleNamespace

import pytest

from main_routers import mmd_router


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _setup_mmd_env(monkeypatch, tmp_path):
    app_docs = tmp_path / "app_docs"
    mmd_dir = app_docs / "mmd"
    mmd_animation_dir = mmd_dir / "animation"
    mmd_dir.mkdir(parents=True, exist_ok=True)
    cm = SimpleNamespace(
        app_docs_dir=app_docs,
        project_root=tmp_path,
        get_steam_workshop_path=lambda: None,
        get_workshop_path=lambda: None,
        mmd_dir=mmd_dir,
        mmd_animation_dir=mmd_animation_dir,
    )
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: cm)
    return mmd_dir


@pytest.mark.asyncio
async def test_f0210_delete_model_preserves_siblings_in_shared_top_subdir(monkeypatch, tmp_path):
    mmd_dir = _setup_mmd_env(monkeypatch, tmp_path)
    pack_dir = mmd_dir / "SharedPack"
    tex_dir = pack_dir / "tex"
    tex_dir.mkdir(parents=True)
    model_a = pack_dir / "ModelA.pmx"
    model_b = pack_dir / "ModelB.pmd"
    skin = tex_dir / "skin.png"
    model_a.write_bytes(b"pmx-a")
    model_b.write_bytes(b"pmd-b")
    skin.write_bytes(b"png")

    resp1 = await mmd_router.delete_mmd_model(
        _DummyRequest({"url": "/user_mmd/SharedPack/ModelA.pmx"})
    )
    body1 = json.loads(resp1.body)
    assert resp1.status_code == 200
    assert body1["success"] is True
    assert body1["deleted_files"] == 1
    assert not model_a.exists()
    assert model_b.exists()
    assert skin.exists()
    assert pack_dir.exists()

    # Deleting the final model in SharedPack removes the directory and textures
    resp2 = await mmd_router.delete_mmd_model(
        _DummyRequest({"url": "/user_mmd/SharedPack/ModelB.pmd"})
    )
    body2 = json.loads(resp2.body)
    assert resp2.status_code == 200
    assert body2["success"] is True
    assert body2["deleted_files"] == 2
    assert not pack_dir.exists()


@pytest.mark.asyncio
async def test_f0210_delete_nested_model_prunes_empty_subdir_without_affecting_sibling(monkeypatch, tmp_path):
    mmd_dir = _setup_mmd_env(monkeypatch, tmp_path)
    pack_dir = mmd_dir / "NestedPack"
    sub_a = pack_dir / "SubA"
    sub_b = pack_dir / "SubB"
    sub_a.mkdir(parents=True)
    sub_b.mkdir(parents=True)
    model_a = sub_a / "Shared.pmx"
    model_b = sub_b / "Shared.pmd"
    model_a.write_bytes(b"a")
    model_b.write_bytes(b"b")

    mapping = mmd_dir / "emotion_config" / "Shared.json"
    mapping.parent.mkdir()
    mapping.write_text('{"happy":1}')

    resp = await mmd_router.delete_mmd_model(
        _DummyRequest({"url": "/user_mmd/NestedPack/SubA/Shared.pmx"})
    )
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["deleted_files"] == 1
    assert not model_a.exists()
    assert not sub_a.exists()
    assert model_b.exists()
    assert mapping.read_text() == '{"happy":1}'
    unrelated_model = mmd_dir / "Unrelated.pmx"
    unrelated_model.write_bytes(b"u")
    unrelated_mapping = mmd_dir / "emotion_config" / "Unrelated.json"
    unrelated_mapping.write_text('{"sad":1}')
    resp = await mmd_router.delete_mmd_model(_DummyRequest({"url": "/user_mmd/NestedPack/SubB/Shared.pmd"}))
    assert resp.status_code == 200
    assert not mapping.exists()
    assert unrelated_model.exists()
    assert unrelated_mapping.read_text() == '{"sad":1}'


@pytest.mark.asyncio
async def test_f0210_delete_solo_subdir_and_top_level_model(monkeypatch, tmp_path):
    mmd_dir = _setup_mmd_env(monkeypatch, tmp_path)
    solo_dir = mmd_dir / "Solo"
    (solo_dir / "tex").mkdir(parents=True)
    (solo_dir / "Solo.pmx").write_bytes(b"solo")
    (solo_dir / "tex" / "a.png").write_bytes(b"tex")

    root_a = mmd_dir / "RootA.pmx"
    root_b = mmd_dir / "RootB.pmx"
    root_a.write_bytes(b"ra")
    root_b.write_bytes(b"rb")

    resp_solo = await mmd_router.delete_mmd_model(
        _DummyRequest({"url": "/user_mmd/Solo/Solo.pmx"})
    )
    assert resp_solo.status_code == 200
    assert json.loads(resp_solo.body)["deleted_files"] == 2
    assert not solo_dir.exists()

    resp_root = await mmd_router.delete_mmd_model(
        _DummyRequest({"url": "/user_mmd/RootA.pmx"})
    )
    assert resp_root.status_code == 200
    assert json.loads(resp_root.body)["deleted_files"] == 1
    assert not root_a.exists()
    assert root_b.exists()


@pytest.mark.asyncio
async def test_f0210_delete_model_keeps_mapping_when_a_directory_cannot_be_listed(monkeypatch, tmp_path):
    import os

    mmd_dir = _setup_mmd_env(monkeypatch, tmp_path)
    workshop_dir = tmp_path / "workshop"
    locked = workshop_dir / "item" / "locked"
    locked.mkdir(parents=True)
    # Same-named model hidden inside a directory that cannot be listed.
    (locked / "Solo.pmx").write_bytes(b"workshop")
    cm = mmd_router.get_config_manager()
    cm.get_steam_workshop_path = lambda: str(workshop_dir)

    model = mmd_dir / "Solo.pmx"
    model.write_bytes(b"solo")
    mapping = mmd_dir / "emotion_config" / "Solo.json"
    mapping.parent.mkdir()
    mapping.write_text('{"happy":1}')

    real_scandir = os.scandir

    def guarded_scandir(path="."):
        if os.path.abspath(os.fspath(path)) == os.path.abspath(locked):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    # Inject at the listing layer: Path.rglob would swallow this silently on Python 3.11.
    monkeypatch.setattr(os, "scandir", guarded_scandir)

    resp = await mmd_router.delete_mmd_model(_DummyRequest({"url": "/user_mmd/Solo.pmx"}))
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["success"] is True
    assert not model.exists()
    assert mapping.read_text() == '{"happy":1}'

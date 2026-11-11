import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_routers.workshop_router import publish
from main_routers.workshop_router.meta import (
    _build_subscriber_workshop_model_ref,
    _resolve_workshop_model_ref,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_model_ref", "expected"),
    [
        # Preserve existing semantics on the normal path.
        ("example/example.model3.json", "/workshop/999/example/example.model3.json"),
        ("/workshop/123/model/foo.model3.json", "/workshop/999/model/foo.model3.json"),
        ("/workshop/123", "/workshop/999"),
        ("", ""),
        # Workshop cards are third-party content; strip '..' segments at ingestion,
        # so the resulting model_ref cannot resolve outside /workshop/{item_id}/.
        ("../../secrets/config.json", "/workshop/999/secrets/config.json"),
        ("/workshop/123/../../secrets/config.json", "/workshop/999/secrets/config.json"),
        ("foo/../../../bar.model3.json", "/workshop/999/foo/bar.model3.json"),
        ("..\\..\\evil.model3.json", "/workshop/999/evil.model3.json"),
        ("..", "/workshop/999"),
        # Strip '.' and drive-letter segments too.
        ("./model/foo.model3.json", "/workshop/999/model/foo.model3.json"),
        ("C:/evil/foo.model3.json", "/workshop/999/evil/foo.model3.json"),
    ],
)
def test_build_subscriber_workshop_model_ref_strips_traversal_segments(raw_model_ref: str, expected: str):
    assert _build_subscriber_workshop_model_ref("999", raw_model_ref) == expected


@pytest.mark.unit
def test_build_subscriber_workshop_model_ref_without_item_id_returns_ref_unchanged():
    # Without item_id, add no prefix; sync_cards does not store avatar bindings when item_id is empty.
    assert _build_subscriber_workshop_model_ref("", "model/foo.model3.json") == "model/foo.model3.json"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model_type", "card_path", "packaged_path"),
    [
        ("vrm", "/user_vrm/Avatar.vrm", "Avatar.vrm"),
        ("vrm", "/user_vrm/Nested/Avatar.vrm", "Avatar.vrm"),
        ("vrm", "/static/vrm/Avatar.vrm", "Avatar.vrm"),
        ("vrm", "/static/vrm/Nested/Avatar.vrm", "Avatar.vrm"),
        ("mmd", "/user_mmd/Avatar/Models/Avatar.pmx", "Avatar/Models/Avatar.pmx"),
        ("mmd", "/static/mmd/Models/Avatar.pmd", "Models/Avatar.pmd"),
        ("mmd", "/workshop/123/Avatar.pmx", "123/Avatar.pmx"),
    ],
)
def test_workshop_model_ref_uses_packaged_file_only_when_original_is_missing(
    tmp_path, model_type, card_path, packaged_path,
):
    model_ref = _build_subscriber_workshop_model_ref("999", card_path)
    def resolve():
        return _resolve_workshop_model_ref(str(tmp_path), model_ref, model_type, card_model_ref=card_path)

    assert resolve() == model_ref

    packaged_file = tmp_path / packaged_path
    packaged_file.parent.mkdir(parents=True, exist_ok=True)
    packaged_file.touch()
    assert resolve() == f"/workshop/999/{packaged_path}"

    original_file = tmp_path / model_ref.removeprefix("/workshop/999/")
    original_file.parent.mkdir(parents=True, exist_ok=True)
    original_file.touch()
    assert resolve() == model_ref


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_type", "source_path", "packaged_path"),
    [
        ("vrm", "/user_vrm/Avatar.vrm", "Avatar.vrm"),
        ("vrm", "/static/vrm/Avatar.vrm", "Avatar.vrm"),
        ("vrm", "/workshop/999/models/Avatar.vrm", "Avatar.vrm"),
        ("mmd", "/user_mmd/Avatar.pmx", "Avatar.pmx"),
        ("mmd", "/user_mmd/Avatar/Models/Avatar.pmx", "Avatar/Models/Avatar.pmx"),
        ("mmd", "/static/mmd/collection/Avatar/Avatar.pmd", "Avatar/Avatar.pmd"),
        ("mmd", "/workshop/999/collection/Avatar/Avatar.pmx", "Avatar/Avatar.pmx"),
        ("mmd", "/workshop/999/Avatar.pmx", "999/Avatar.pmx"),
    ],
)
async def test_prepare_workshop_upload_writes_the_packaged_model_path(
    tmp_path, monkeypatch, model_type, source_path, packaged_path,
):
    source_file = tmp_path / source_path.lstrip("/")
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"model")
    if model_type == "vrm" and source_file.parent.name == "Nested":
        (source_file.parent.parent / source_file.name).write_bytes(b"different model")
    texture = source_file.with_name("texture.png")
    texture.write_bytes(b"texture")
    config = SimpleNamespace(
        project_root=tmp_path,
        vrm_dir=tmp_path / "user_vrm",
        mmd_dir=tmp_path / "user_mmd",
        card_faces_dir=tmp_path / "card_faces",
    )
    monkeypatch.setattr(publish, "get_config_manager", lambda: config)
    monkeypatch.setattr(publish, "get_workshop_path_async", AsyncMock(return_value=str(tmp_path)))
    monkeypatch.setattr(publish, "read_workshop_meta", lambda _: None)
    monkeypatch.setattr(publish, "get_subscribed_workshop_items", AsyncMock(return_value={
        "success": True,
        "items": [{"publishedFileId": "999", "installedFolder": str(tmp_path / "workshop/999")}],
    }))
    request = SimpleNamespace(json=AsyncMock(return_value={
        "charaData": {"档案名": "Avatar", model_type: source_path},
        "modelType": model_type,
        "modelName": source_path,
        "fileName": "Avatar.chara.json",
    }))

    response = await publish.prepare_workshop_upload(request)

    assert response.status_code == 200, response.body
    package = Path(json.loads(response.body)["temp_folder"])
    card = json.loads((package / "Avatar.chara.json").read_text(encoding="utf-8"))
    assert card[model_type] == packaged_path
    assert (package / card[model_type]).read_bytes() == b"model"
    if model_type == "mmd" and "/" in packaged_path:
        assert (package / packaged_path).with_name(texture.name).read_bytes() == b"texture"

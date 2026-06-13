import zipfile
from types import SimpleNamespace

import main_routers.characters_router as characters_router


def _pngtuber_character_config() -> dict:
    return {
        "_reserved": {
            "avatar": {
                "model_type": "pngtuber",
                "pngtuber": {
                    "idle_image": "/user_pngtuber/avatar/idle.png?v=1#preview",
                    "layered_metadata": "/user_pngtuber/avatar/metadata.json",
                    "talking_image": "/static/pngtuber/default/talk.png",
                },
            }
        }
    }


def test_add_pngtuber_assets_packages_whole_referenced_model_directory(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    avatar_dir = pngtuber_dir / "avatar"
    (avatar_dir / "layers").mkdir(parents=True)
    (avatar_dir / "idle.png").write_bytes(b"idle")
    (avatar_dir / "metadata.json").write_text('{"layers":["layers/mouth.png"]}', encoding="utf-8")
    (avatar_dir / "layers" / "mouth.png").write_bytes(b"mouth")
    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    zip_path = tmp_path / "character.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        added = characters_router._add_pngtuber_assets_to_character_zip(
            zf,
            _pngtuber_character_config(),
            config_manager,
        )

    assert added is True
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert set(zf.namelist()) == {
            "model/pngtuber/avatar/idle.png",
            "model/pngtuber/avatar/metadata.json",
            "model/pngtuber/avatar/layers/mouth.png",
        }


def test_copy_imported_pngtuber_assets_renames_conflicts_and_rewrites_refs(tmp_path):
    model_dir = tmp_path / "model"
    imported_avatar = model_dir / "pngtuber" / "avatar"
    (imported_avatar / "layers").mkdir(parents=True)
    (imported_avatar / "idle.png").write_bytes(b"idle")
    (imported_avatar / "metadata.json").write_text("{}", encoding="utf-8")
    (imported_avatar / "layers" / "mouth.png").write_bytes(b"mouth")

    pngtuber_dir = tmp_path / "user_pngtuber"
    (pngtuber_dir / "avatar").mkdir(parents=True)
    (pngtuber_dir / "avatar" / "old.png").write_bytes(b"old")
    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    rel_map = characters_router._copy_imported_pngtuber_assets(model_dir, config_manager)

    assert rel_map["avatar/idle.png"] == "avatar(1)/idle.png"
    assert rel_map["avatar/metadata.json"] == "avatar(1)/metadata.json"
    assert rel_map["avatar/layers/mouth.png"] == "avatar(1)/layers/mouth.png"
    assert (pngtuber_dir / "avatar(1)" / "layers" / "mouth.png").is_file()

    character_data = _pngtuber_character_config()
    rewritten = characters_router._rewrite_imported_pngtuber_refs(character_data, rel_map)
    pngtuber = rewritten["_reserved"]["avatar"]["pngtuber"]

    assert pngtuber["idle_image"] == "/user_pngtuber/avatar(1)/idle.png?v=1#preview"
    assert pngtuber["layered_metadata"] == "/user_pngtuber/avatar(1)/metadata.json"
    assert pngtuber["talking_image"] == "/static/pngtuber/default/talk.png"

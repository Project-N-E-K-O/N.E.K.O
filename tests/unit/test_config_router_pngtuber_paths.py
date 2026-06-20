from types import SimpleNamespace

from main_routers.config_router import (
    PNGTUBER_USER_PATH,
    _infer_pngtuber_metadata_from_idle,
    _resolve_pngtuber_image_path,
    _resolve_pngtuber_metadata_path,
)


def test_resolve_pngtuber_user_image_keeps_cache_buster_for_existing_file(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    image_dir = pngtuber_dir / "avatar"
    image_dir.mkdir(parents=True)
    (image_dir / "idle.png").write_bytes(b"png")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)
    image_url = f"{PNGTUBER_USER_PATH}/avatar/idle.png?v=1#preview"

    assert _resolve_pngtuber_image_path(image_url, config_manager, "Neko") == image_url


def test_resolve_pngtuber_relative_image_checks_path_without_cache_buster(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    image_dir = pngtuber_dir / "avatar"
    image_dir.mkdir(parents=True)
    (image_dir / "talk.webp").write_bytes(b"webp")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    assert (
        _resolve_pngtuber_image_path("avatar/talk.webp?t=2", config_manager, "Neko")
        == f"{PNGTUBER_USER_PATH}/avatar/talk.webp"
    )


def test_resolve_pngtuber_rejects_protocol_relative_url(tmp_path):
    config_manager = SimpleNamespace(pngtuber_dir=tmp_path / "pngtuber")

    assert _resolve_pngtuber_image_path("//evil.example/avatar.png", config_manager, "Neko") == ""


def test_resolve_pngtuber_metadata_keeps_auto_layer_json_for_existing_file(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    model_dir = pngtuber_dir / "avatar"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.live2d-auto-layer.json").write_text("{}", encoding="utf-8")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    assert (
        _resolve_pngtuber_metadata_path(
            "avatar/metadata.live2d-auto-layer.json?t=2",
            config_manager,
            "Neko",
        )
        == f"{PNGTUBER_USER_PATH}/avatar/metadata.live2d-auto-layer.json"
    )


def test_infer_pngtuber_metadata_from_idle_prefers_auto_layer_metadata(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    model_dir = pngtuber_dir / "avatar"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.live2d-auto-layer.json").write_text("{}", encoding="utf-8")
    (model_dir / "metadata.json").write_text("{}", encoding="utf-8")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    assert (
        _infer_pngtuber_metadata_from_idle(
            f"{PNGTUBER_USER_PATH}/avatar/idle.png?v=1#preview",
            config_manager,
        )
        == f"{PNGTUBER_USER_PATH}/avatar/metadata.live2d-auto-layer.json"
    )

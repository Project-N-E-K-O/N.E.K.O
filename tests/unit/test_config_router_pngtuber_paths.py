from types import SimpleNamespace

from main_routers.config_router import (
    PNGTUBER_USER_PATH,
    _infer_pngtuber_metadata_from_idle,
    _resolve_pngtuber_image_path,
    _resolve_pngtuber_metadata_path,
)
from main_routers.pngtuber_protocol import (
    NEKO_PNGTUBER_ADAPTER,
    adapter_for_metadata,
    normalize_pngtuber_runtime_config,
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


def test_resolve_pngtuber_metadata_keeps_v2_json_for_existing_file(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    model_dir = pngtuber_dir / "avatar"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.neko-pngtuber.v2.json").write_text("{}", encoding="utf-8")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    assert (
        _resolve_pngtuber_metadata_path(
            "avatar/metadata.neko-pngtuber.v2.json?t=2",
            config_manager,
            "Neko",
        )
        == f"{PNGTUBER_USER_PATH}/avatar/metadata.neko-pngtuber.v2.json"
    )


def test_infer_pngtuber_metadata_from_idle_ignores_legacy_metadata(tmp_path):
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
        == ""
    )


def test_infer_pngtuber_metadata_prefers_neko_v2_metadata(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    model_dir = pngtuber_dir / "avatar"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.neko-pngtuber.v2.json").write_text("{}", encoding="utf-8")
    (model_dir / "metadata.live2d-auto-layer.json").write_text("{}", encoding="utf-8")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    assert (
        _infer_pngtuber_metadata_from_idle(
            f"{PNGTUBER_USER_PATH}/avatar/idle.png",
            config_manager,
        )
        == f"{PNGTUBER_USER_PATH}/avatar/metadata.neko-pngtuber.v2.json"
    )


def test_pngtuber_protocol_normalizes_neko_v2_runtime_config(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    model_dir = pngtuber_dir / "avatar"
    model_dir.mkdir(parents=True)
    (model_dir / "idle.png").write_bytes(b"png")
    (model_dir / "metadata.neko-pngtuber.v2.json").write_text("{}", encoding="utf-8")

    config_manager = SimpleNamespace(pngtuber_dir=pngtuber_dir)

    config = normalize_pngtuber_runtime_config(
        {"idle_image": "avatar/idle.png"},
        config_manager,
        "Neko",
    )

    assert config["idle_image"] == f"{PNGTUBER_USER_PATH}/avatar/idle.png"
    assert config["metadata"] == f"{PNGTUBER_USER_PATH}/avatar/metadata.neko-pngtuber.v2.json"
    assert config["layered_metadata"] == config["metadata"]
    assert config["adapter"] == NEKO_PNGTUBER_ADAPTER


def test_pngtuber_protocol_rejects_legacy_layered_adapter_for_legacy_metadata():
    assert adapter_for_metadata("/user_pngtuber/avatar/metadata.live2d-auto-layer.json") == ""

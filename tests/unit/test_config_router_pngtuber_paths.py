from types import SimpleNamespace

from main_routers.config_router import PNGTUBER_USER_PATH, _resolve_pngtuber_image_path


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

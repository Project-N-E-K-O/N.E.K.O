import json
from pathlib import Path

import pytest
from PIL import Image

from main_routers import workshop_router


def _write_image(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (80, 160, 220, 255)).save(path)


def _write_meta(path: Path, origin: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"origin": origin}), encoding="utf-8")


class _FakeConfigManager:
    def __init__(self, root: Path):
        self.card_faces_dir = root / "card_faces"

    def ensure_card_faces_directory(self) -> bool:
        self.card_faces_dir.mkdir(parents=True, exist_ok=True)
        return True

    def card_face_meta_path(self, chara_name: str) -> Path:
        return self.card_faces_dir / f"{chara_name}.meta.json"


@pytest.mark.unit
def test_should_refresh_workshop_card_face_allows_missing_face_file(tmp_path: Path):
    face_path = tmp_path / "card_faces" / "demo.png"
    meta_path = tmp_path / "card_faces" / "demo.meta.json"

    assert workshop_router._should_refresh_workshop_card_face(face_path, meta_path) is True


@pytest.mark.unit
def test_should_refresh_workshop_card_face_protects_existing_face_without_sidecar(tmp_path: Path):
    face_path = tmp_path / "card_faces" / "demo.png"
    meta_path = tmp_path / "card_faces" / "demo.meta.json"
    _write_image(face_path, (900, 900))

    assert workshop_router._should_refresh_workshop_card_face(face_path, meta_path) is False


@pytest.mark.unit
@pytest.mark.parametrize("origin", ("self", "imported"))
def test_should_refresh_workshop_card_face_protects_user_owned_origins(tmp_path: Path, origin: str):
    face_path = tmp_path / "card_faces" / "demo.png"
    meta_path = tmp_path / "card_faces" / "demo.meta.json"
    _write_image(face_path, (900, 900))
    _write_meta(meta_path, origin)

    assert workshop_router._should_refresh_workshop_card_face(face_path, meta_path) is False


@pytest.mark.unit
def test_should_refresh_workshop_card_face_only_refreshes_non_normalized_steam_face(tmp_path: Path):
    face_path = tmp_path / "card_faces" / "demo.png"
    meta_path = tmp_path / "card_faces" / "demo.meta.json"
    _write_meta(meta_path, "steam")

    _write_image(face_path, workshop_router.WORKSHOP_CARD_FACE_SIZE)
    assert workshop_router._should_refresh_workshop_card_face(face_path, meta_path) is False

    _write_image(face_path, (900, 900))
    assert workshop_router._should_refresh_workshop_card_face(face_path, meta_path) is True


@pytest.mark.unit
def test_should_refresh_workshop_card_face_allows_orphaned_generated_face(tmp_path: Path):
    config_mgr = _FakeConfigManager(tmp_path)
    preview_path = tmp_path / "preview.png"
    _write_image(preview_path, (1024, 1024))

    created = workshop_router._ensure_workshop_card_face_from_preview(
        config_mgr,
        "demo",
        str(preview_path),
        None,
    )

    face_path = config_mgr.card_faces_dir / "demo.png"
    meta_path = config_mgr.card_face_meta_path("demo")

    assert created is True
    assert face_path.is_file()
    assert meta_path.exists() is False
    assert workshop_router._should_refresh_workshop_card_face(face_path, meta_path) is True


@pytest.mark.unit
def test_render_workshop_card_face_image_outputs_normalized_canvas():
    source = Image.new("RGB", (1280, 720), (240, 220, 180))

    rendered = workshop_router._render_workshop_card_face_image(source)

    assert rendered.size == workshop_router.WORKSHOP_CARD_FACE_SIZE
    assert rendered.mode == "RGBA"


@pytest.mark.unit
def test_ensure_workshop_card_face_from_preview_persists_sidecar_when_rendering(tmp_path: Path):
    config_mgr = _FakeConfigManager(tmp_path)
    preview_path = tmp_path / "preview.png"
    _write_image(preview_path, (1024, 1024))

    created = workshop_router._ensure_workshop_card_face_from_preview(
        config_mgr,
        "demo",
        str(preview_path),
        {"authorName": "Tester"},
    )

    face_path = config_mgr.card_faces_dir / "demo.png"
    meta_path = config_mgr.card_face_meta_path("demo")

    assert created is True
    assert face_path.is_file()
    assert meta_path.is_file()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["origin"] == "steam"
    assert meta["author"] == "Tester"


@pytest.mark.unit
def test_ensure_workshop_card_face_from_preview_does_not_write_meta_before_render_succeeds(tmp_path: Path):
    config_mgr = _FakeConfigManager(tmp_path)
    preview_path = tmp_path / "broken-preview.png"
    preview_path.write_text("not a png", encoding="utf-8")

    with pytest.raises(Exception):
        workshop_router._ensure_workshop_card_face_from_preview(
            config_mgr,
            "demo",
            str(preview_path),
            {"authorName": "Tester"},
        )

    face_path = config_mgr.card_faces_dir / "demo.png"
    meta_path = config_mgr.card_face_meta_path("demo")
    assert face_path.exists() is False
    assert meta_path.exists() is False


@pytest.mark.unit
def test_ensure_workshop_card_face_meta_skips_user_owned_png_without_marker(tmp_path: Path):
    config_mgr = _FakeConfigManager(tmp_path)
    face_path = config_mgr.card_faces_dir / "demo.png"
    _write_image(face_path, workshop_router.WORKSHOP_CARD_FACE_SIZE)

    created = workshop_router._ensure_workshop_card_face_meta(
        config_mgr,
        "demo",
        {"authorName": "Tester"},
    )

    assert created is False
    assert config_mgr.card_face_meta_path("demo").exists() is False

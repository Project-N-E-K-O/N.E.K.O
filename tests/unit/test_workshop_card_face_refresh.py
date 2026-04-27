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
def test_render_workshop_card_face_image_outputs_normalized_canvas():
    source = Image.new("RGB", (1280, 720), (240, 220, 180))

    rendered = workshop_router._render_workshop_card_face_image(source)

    assert rendered.size == workshop_router.WORKSHOP_CARD_FACE_SIZE
    assert rendered.mode == "RGBA"

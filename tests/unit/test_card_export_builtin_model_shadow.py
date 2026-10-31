"""Card export decides built-in vs user model by the saved binding, not by name alone.

A user-imported model may share a built-in's name (e.g. ``yui-sister``). The character's
saved ``asset_source`` says which one it displays: a local binding must pack the user's
model, while a builtin binding must not pack a same-named user model.
"""

import io
import struct
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeUpload:
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data
        self._offset = 0

    async def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


def _archive_names(png: bytes) -> list[str]:
    offset = 8
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset:offset + 4])
        chunk_type = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + length]
        if chunk_type == b"neKo":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return zf.namelist()
        offset += 12 + length
    raise AssertionError("card has no neKo chunk")


def _config(tmp_path, asset_source, model_path):
    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value={"猫娘": {
        "Exported": {
            "昵称": "Exported",
            "_reserved": {"avatar": {
                "model_type": "live2d",
                "asset_source": asset_source,
                "live2d": {"model_path": model_path},
            }},
        },
    }})
    config_manager.card_faces_dir = tmp_path
    config_manager.card_face_meta_path = MagicMock(return_value=tmp_path / "card_meta.json")
    return config_manager


def _model_dir(tmp_path):
    model_dir = tmp_path / "models" / "yui-sister"
    model_dir.mkdir(parents=True)
    (model_dir / "yui-sister.model3.json").write_text("{}", encoding="utf-8")
    return model_dir


def _portrait():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color="#ffffff").save(buffer, format="PNG")
    return _FakeUpload("portrait.png", buffer.getvalue())


async def _export_both(cards, tmp_path):
    plain = await cards.export_catgirl_card("Exported")
    portrait = await cards.export_catgirl_with_portrait(
        "Exported", portrait=_portrait(), include_model=True,
    )
    return _archive_names(bytes(plain.body)), _archive_names(bytes(portrait.body))


_RELATIVE_PATH = "yui-sister/yui-sister.model3.json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset_source", "model_path", "found_user_model", "packed"),
    [
        ("local_imported", _RELATIVE_PATH, True, True),
        ("local", _RELATIVE_PATH, True, True),
        ("local", _RELATIVE_PATH, False, False),
        ("builtin", _RELATIVE_PATH, True, False),
        ("", _RELATIVE_PATH, True, False),
    ],
)
async def test_builtin_name_export_follows_saved_binding(
    tmp_path, asset_source, model_path, found_user_model, packed,
):
    from main_routers.characters_router import cards

    assert "yui-sister" in cards.BUILTIN_LIVE2D_MODEL_NAMES
    model_dir = _model_dir(tmp_path)
    config = _config(tmp_path, asset_source, model_path)
    with patch.object(cards, "get_config_manager", return_value=config), \
            patch.object(cards, "find_model_directory", return_value=(str(model_dir), "/user_live2d")), \
            patch.object(cards, "is_user_imported_model", return_value=found_user_model):
        plain_names, portrait_names = await _export_both(cards, tmp_path)

    model_file = "model/yui-sister/yui-sister.model3.json"
    assert (model_file in plain_names) is packed
    assert (model_file in portrait_names) is packed


def test_legacy_user_live2d_prefix_counts_as_user_binding():
    from main_routers.characters_router import cards

    data = {"_reserved": {"avatar": {}}}
    assert not cards._is_bound_to_builtin_live2d(
        data, "yui-sister", "/user_live2d/yui-sister/yui-sister.model3.json",
    )
    assert cards._is_bound_to_builtin_live2d(data, "yui-sister", _RELATIVE_PATH)
    assert not cards._is_bound_to_builtin_live2d(data, "someone-else", _RELATIVE_PATH)

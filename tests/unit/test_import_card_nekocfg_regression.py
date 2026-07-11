# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression test: importing a `.nekocfg` card must not crash.

The `.nekocfg` branch of ``import_character_card`` never assigned
``imported_card_character_data``, while the shared tail of the handler passes
it unconditionally to ``_restore_imported_pngtuber_avatar_config`` — so every
`.nekocfg` import died with ``UnboundLocalError`` (the ZIP/PNG branches were
fine because they assign it before use).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main_routers import characters_router


XOR_KEY = b"NEKOCHARA2024"


def _xor(data: bytes) -> bytes:
    return bytes(data[i] ^ XOR_KEY[i % len(XOR_KEY)] for i in range(len(data)))


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self._buf = payload
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._buf) - self._pos
        chunk = self._buf[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nekocfg_import_does_not_raise_unbound_local():
    character = {
        "档案名": "测试猫娘",
        "昵称": "测试猫娘",
        "性格": "温柔",
    }
    payload = _xor(json.dumps(character, ensure_ascii=False).encode("utf-8"))

    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value={"猫娘": {}})
    config_manager.asave_characters = AsyncMock()
    config_manager.ensure_card_faces_directory = MagicMock()
    config_manager.card_face_meta_path = MagicMock(
        side_effect=AssertionError("meta write should be mocked out")
    )

    with patch.object(characters_router, "get_config_manager", return_value=config_manager), \
         patch.object(characters_router, "get_initialize_character_data", return_value=None), \
         patch.object(
             characters_router,
             "_mark_new_character_greeting_pending_safe",
             new=AsyncMock(return_value=(True, None)),
         ), \
         patch.object(characters_router, "_write_card_meta", new=MagicMock()):
        config_manager.card_face_meta_path = MagicMock(return_value="unused-meta-path")
        response = await characters_router.import_character_card(
            zip_file=_FakeUpload("card.nekocfg", payload),
            card_image=None,
        )

    body = json.loads(bytes(response.body).decode("utf-8"))
    # Before the fix this raised UnboundLocalError('imported_card_character_data')
    # instead of returning any response at all.
    assert body.get("success") is True, body
    config_manager.asave_characters.assert_awaited()
    saved_characters = config_manager.asave_characters.await_args.args[0]
    assert "测试猫娘" in saved_characters["猫娘"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nekocfg_import_does_not_restore_pngtuber_avatar_config():
    """.nekocfg ships no model assets: a hand-crafted file carrying
    _reserved.avatar.pngtuber must not have that config restored (the image
    paths it references were never extracted locally)."""
    character = {
        "档案名": "手工猫娘",
        "昵称": "手工猫娘",
        "_reserved": {
            "avatar": {
                "model_type": "pngtuber",
                "pngtuber": {"idle": "pngtuber/nonexistent/idle.png"},
            }
        },
    }
    payload = _xor(json.dumps(character, ensure_ascii=False).encode("utf-8"))

    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value={"猫娘": {}})
    config_manager.asave_characters = AsyncMock()
    config_manager.ensure_card_faces_directory = MagicMock()
    config_manager.card_face_meta_path = MagicMock(return_value="unused-meta-path")

    with patch.object(characters_router, "get_config_manager", return_value=config_manager), \
         patch.object(characters_router, "get_initialize_character_data", return_value=None), \
         patch.object(
             characters_router,
             "_mark_new_character_greeting_pending_safe",
             new=AsyncMock(return_value=(True, None)),
         ), \
         patch.object(characters_router, "_write_card_meta", new=MagicMock()):
        response = await characters_router.import_character_card(
            zip_file=_FakeUpload("card.nekocfg", payload),
            card_image=None,
        )

    body = json.loads(bytes(response.body).decode("utf-8"))
    assert body.get("success") is True, body
    saved_characters = config_manager.asave_characters.await_args.args[0]
    saved = saved_characters["猫娘"]["手工猫娘"]
    avatar = (saved.get("_reserved") or {}).get("avatar") or {}
    assert avatar.get("model_type") != "pngtuber", saved
    assert "pngtuber" not in avatar, saved

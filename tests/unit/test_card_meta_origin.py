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
"""Card-face metadata ``origin`` mark: the Molili import wizard flips a locally
created card's origin to ``molili``; everything else stays read-only."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from main_routers.characters_router import cards


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _config(tmp_path, origin='self'):
    sidecar = tmp_path / 'meta.json'
    sidecar.write_text(json.dumps({
        'author': '', 'origin': origin,
        'created_at': '2026-10-17T16:43:31', 'updated_at': '2026-10-17T16:43:31',
    }, ensure_ascii=False), encoding='utf-8')
    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value={'猫娘': {'测试': {}}})
    config_manager.ensure_card_faces_directory = MagicMock()
    config_manager.card_face_meta_path = MagicMock(return_value=sidecar)
    return config_manager, sidecar


def _patched(config_manager):
    from unittest.mock import patch
    return patch.object(cards, 'get_config_manager', return_value=config_manager)


def _meta_on_disk(sidecar):
    return json.loads(sidecar.read_text(encoding='utf-8'))


def test_card_meta_origin_upgrade_self_to_molili(tmp_path):
    config_manager, sidecar = _config(tmp_path, origin='self')
    with _patched(config_manager):
        response = asyncio.run(cards.put_card_meta('测试', _FakeRequest({'origin': 'molili'})))

    assert response.status_code == 200
    assert _meta_on_disk(sidecar)['origin'] == 'molili'


def test_card_meta_origin_upgrade_is_idempotent(tmp_path):
    config_manager, sidecar = _config(tmp_path, origin='molili')
    with _patched(config_manager):
        response = asyncio.run(cards.put_card_meta('测试', _FakeRequest({'origin': 'molili'})))

    assert response.status_code == 200
    assert _meta_on_disk(sidecar)['origin'] == 'molili'


def test_card_meta_origin_only_accepts_molili(tmp_path):
    config_manager, _sidecar = _config(tmp_path, origin='self')
    with _patched(config_manager):
        response = asyncio.run(cards.put_card_meta('测试', _FakeRequest({'origin': 'self'})))

    assert response.status_code == 400


def test_card_meta_author_stays_editable_on_molili_card(tmp_path):
    config_manager, sidecar = _config(tmp_path, origin='molili')
    with _patched(config_manager):
        response = asyncio.run(cards.put_card_meta('测试', _FakeRequest({'author': '璃琉期'})))

    assert response.status_code == 200
    assert _meta_on_disk(sidecar)['author'] == '璃琉期'


def test_card_meta_origin_refused_for_steam_card(tmp_path):
    config_manager, sidecar = _config(tmp_path, origin='steam')
    with _patched(config_manager):
        response = asyncio.run(cards.put_card_meta('测试', _FakeRequest({'origin': 'molili'})))

    assert response.status_code == 403
    assert _meta_on_disk(sidecar)['origin'] == 'steam'

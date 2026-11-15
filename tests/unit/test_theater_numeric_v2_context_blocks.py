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

"""Fenced prompt blocks stay readable back by label."""

import pytest

from services.theater.numeric_v2_context import (
    prompt_data_block,
    prompt_data_block_payload,
    render_prompt_context,
)


@pytest.mark.parametrize("value", ["a=b", "<tag>", "==", "a>b<c=d", "普通文本"])
def test_block_payload_round_trips_values_that_could_forge_delimiters(value):
    data = {"character_name": "测试", "nested": {"key": value}, "items": [value]}
    text = prompt_data_block("角色设定", data)
    assert prompt_data_block_payload(text, "角色设定") == data


def test_block_payload_reads_the_requested_label_out_of_several_blocks():
    text = render_prompt_context({
        "character_name": "小葵",
        "acting_context": {"core_persona": "人格原文", "current_scene_state": "开场状态"},
        "visible_player_history": [],
        "player_input": {},
    })
    role = prompt_data_block_payload(text, "角色设定")
    # The preflight persona check reads core_persona out of this block, so the
    # renderer must keep it there rather than moving it with the staging fields.
    assert role["acting_context"]["core_persona"] == "人格原文"
    assert "current_scene_state" not in role["acting_context"]
    assert prompt_data_block_payload(text, "剧本资料")["acting_context"]["current_scene_state"] == "开场状态"
    assert prompt_data_block_payload(text, "历史对话") == {"visible_player_history": []}


def test_block_payload_rejects_a_label_that_is_not_present():
    text = prompt_data_block("角色设定", {"character_name": "测试"})
    with pytest.raises(KeyError):
        prompt_data_block_payload(text, "剧本资料")

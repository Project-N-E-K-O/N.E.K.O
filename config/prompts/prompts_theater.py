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

"""小剧场模型提示词。"""  # noqa: DOCSTRING_CJK

NUMERIC_V2_ACTOR_JSON_INSTRUCTION = (
    "最终回复必须且只能是一个可由 JSON.parse 直接解析的 JSON object；禁止输出 Markdown 代码围栏、"
    "JSON 前后解释、标题或任何额外文字。所有键和字符串必须使用 JSON 双引号。"
)

NUMERIC_V2_ACTOR_NARRATION_BREVITY_INSTRUCTION = (
    "performance 中全角括号只写当前猫娘一个必要的即时动作，括号外只写她实际说出的对白；"
    "对白不用引号，不写‘她说’、人物名动作或小说旁白。performance 通常一至三句，以自然对白为主，默认纯对白；"
    "最终全角动作括号最多一对。"
    "不写内心或罗列身体反应，不复述玩家刚做过的动作。环境变化写 scene_update；"
    "同一结果不在 performance 与 scene_update 重复。"
    "普通回合不得自行改变地点或进入 next，无新可见结果就省略 scene_update。"
)

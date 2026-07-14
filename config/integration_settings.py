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

"""Plugin, translation, vision, connectivity, and MCP limits."""

# ---- Plugin platform ----
PLUGIN_USER_CONTEXT_MAX_ITEMS = 200
"""每用户上下文 deque maxlen（plugin core state）。
- 用途：plugin 跨调用维护的 per-user 上下文条数上限。
- 上游：用户与 plugin 的交互事件序列。"""

# ---- Utils: translation / vision / connectivity test / MCP ----
TRANSLATION_OUTPUT_MAX_TOKENS = 1000
"""翻译 LLM 的 max_completion_tokens。
- 用途：单 chunk 翻译输出上限。
- 上游：LLM 输出。"""

TRANSLATION_CHUNK_MAX_TOKENS_SHORT = 2000
"""翻译短文本路径的分块 token 上限。
- 用途：单次翻译调用的输入 token 数；长文本被切成多块串行翻译。
- 上游：用户/系统传入的待翻译原文。"""

TRANSLATION_CHUNK_MAX_TOKENS_LONG = 5000
"""翻译长文本路径的分块 token 上限。
- 用途：长文本翻译路径下的更大 chunk size。
- 上游：用户/系统传入的待翻译原文。"""

VISION_ANALYSIS_MAX_TOKENS = 500
"""截图 / 图像分析 LLM 的 max_completion_tokens。
- 用途：返回画面描述。
- 上游：LLM 输出。"""

CONNECTIVITY_TEST_MAX_TOKENS = 1
"""provider 连通性测试请求的 max_completion_tokens。
- 用途：仅测试 API 可达，最小请求。
- 上游：LLM 输出。"""

MCP_TOOL_RESULT_MAX_TOKENS = 1000
"""MCP 工具结果回流给 LLM 前的 token 上限。
- 用途：mcp_adapter._truncate_llm_text 默认 limit；超过则截断 + "..."。
- 上游：MCP server 返回的工具执行结果。"""

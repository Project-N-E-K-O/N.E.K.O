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
"""Per-user context deque maxlen (plugin core state).
- Purpose: cap per-user context entries retained by the plugin across calls.
- Upstream: sequence of user-plugin interaction events."""

# ---- Utils: translation / vision / connectivity test / MCP ----
TRANSLATION_OUTPUT_MAX_TOKENS = 1000
"""Translation LLM max_completion_tokens.
- Purpose: output limit for translating one chunk.
- Upstream: LLM output."""

TRANSLATION_CHUNK_MAX_TOKENS_SHORT = 2000
"""Chunk token limit for the short-text translation path.
- Purpose: input tokens per translation call; long texts are split into chunks translated sequentially.
- Upstream: source text supplied by the user/system for translation."""

TRANSLATION_CHUNK_MAX_TOKENS_LONG = 5000
"""Chunk token limit for the long-text translation path.
- Purpose: larger chunk size for the long-text translation path.
- Upstream: source text supplied by the user/system for translation."""

VISION_ANALYSIS_MAX_TOKENS = 500
"""Screenshot / image-analysis LLM max_completion_tokens.
- Purpose: return a description of the image.
- Upstream: LLM output."""

CONNECTIVITY_TEST_MAX_TOKENS = 1
"""Provider connectivity-test request max_completion_tokens.
- Purpose: minimal request testing only API reachability.
- Upstream: LLM output."""

MCP_TOOL_RESULT_MAX_TOKENS = 1000
"""MCP tool-result token limit before returning results to the LLM.
- Purpose: default mcp_adapter._truncate_llm_text limit; truncate and append \"...\" when exceeded.
- Upstream: tool execution results returned by the MCP server."""

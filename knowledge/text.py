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

"""Compatibility facade for shared deterministic text helpers.

The canonical helpers live below both built-in knowledge domains. Keeping
these names as aliases preserves the public-knowledge import contract.
"""

# Explicit re-exports also preserve private helpers used by older consumers.
from utils.knowledge_text import (  # noqa: F401
    _CONTROL_CHARS_RE,
    _HORIZONTAL_SPACE_RE,
    _BLANK_LINES_RE,
    _TITLE_SPACE_RE,
    _CJK_RANGES,
    _TOKEN_RE,
    _CJK_RUN_RE,
    MAX_QUERY_TOKENS,
    sanitize_external_text,
    single_line,
    title_key,
    strict_surface,
    is_cjk_token,
    word_runs,
    _TRAILING_PUNCT,
    _EDGE_QUOTES,
    _quote_pair,
    loose_surface,
    _strip_marks,
    _NAME_SYMBOLS,
    is_name_symbol,
    _is_name_symbol,
    _unglued_runs,
    unglued_tokens,
    ordered_runs,
    unglued_word_runs,
    unglued_cjk_text,
    search_view,
    search_tokens,
    query_tokens,
    fts_match_expression,
    neutralize_fence,
    strip_chat_markup,
)

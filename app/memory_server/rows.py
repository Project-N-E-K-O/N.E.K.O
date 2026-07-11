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

"""Pure, stateless extraction helpers shared across the memory_server package.

Two input shapes are covered:
  - time_indexed SQL rows ``[(timestamp, session_id, message_json), ...]``
    (``_coerce_db_ts`` / ``_extract_*_from_rows`` / ``_trim_to_user_msg_bracket``)
  - in-memory BaseMessage-like lists (``_has_human_messages`` /
    ``_extract_ai_response`` / ``_extract_user_messages``)

No module state, no imports from sibling submodules. Precedent:
``memory/evidence_handlers.py`` was extracted from the former monolithic
``app/memory_server.py`` for the same testability reason.
"""

import json
from datetime import datetime


def _coerce_db_ts(ts) -> datetime | None:
    """Normalize the timestamp field of a SQL row into a **naive** datetime.

    SQLAlchemy + SQLite return strings instead of datetimes under some driver
    configurations; same normalization as
    memory/timeindex.py:get_last_conversation_time. Returns None when unparseable
    (the caller should skip the row rather than write None into the cursor).

    If a TZ-aware datetime is parsed (import / migration paths write things like
    "...+00:00"), force `replace(tzinfo=None)` to naive — every cursor / comparison
    in this package works with naive semantics (last_b_check_ts / last_a_msg_ts /
    facts.json `created_at` are all naive `datetime.now().isoformat()`); comparing
    aware with naive raises TypeError, permanently muting the caller (Codex P1+P2
    round-7/8 on PR #1408, both cases).
    """
    if isinstance(ts, datetime):
        result = ts
    elif isinstance(ts, str):
        try:
            result = datetime.fromisoformat(ts)
        except ValueError:
            try:
                result = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                return None
    else:
        return None
    if result.tzinfo is not None:
        result = result.replace(tzinfo=None)
    return result


def _extract_user_messages_with_ts_from_rows(rows: list) -> list[tuple[str, datetime]]:
    """Extract (user message text, timestamp) tuples from time_indexed SQL query results.

    rows: [(timestamp, session_id, message_json), ...] (ASC ordered by ts)
    message_json is the JSON string stored by langchain SQLChatMessageHistory.
    content may be a str or list[{type, text}].

    The returned list is sorted by ts ASC; the caller can advance the cursor based
    on the last item's ts. The timestamp is normalized into a datetime object via
    _coerce_db_ts (the SQL driver may return str); rows that fail parsing are
    skipped.
    """
    out: list[tuple[str, datetime]] = []
    for ts_raw, _, msg_json in rows:
        ts = _coerce_db_ts(ts_raw)
        if ts is None:
            continue
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if isinstance(msg, dict) and msg.get('type') == 'human':
                content = msg.get('data', {}).get('content', '')
                if isinstance(content, str):
                    if content.strip():
                        out.append((content, ts))
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            text_val = part.get('text', '')
                            if text_val.strip():
                                out.append((text_val, ts))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _extract_user_messages_from_rows(rows: list) -> list[str]:
    """Extract user message text from time_indexed SQL query results (legacy text-only view).

    rows: [(timestamp, session_id, message_json), ...]
    """
    user_msgs = []
    for _, _, msg_json in rows:
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if isinstance(msg, dict) and msg.get('type') == 'human':
                content = msg.get('data', {}).get('content', '')
                if isinstance(content, str):
                    if content.strip():
                        user_msgs.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            text = part.get('text', '')
                            if text.strip():
                                user_msgs.append(text)
        except (json.JSONDecodeError, TypeError):
            continue
    return user_msgs


def _extract_role_tagged_messages_from_rows(rows: list) -> list[dict]:
    """Full-message extraction for Path B — keeps both user + ai types and outputs a
    message_dict list fed directly into ``convert_to_messages``.

    Differences from ``_extract_user_messages_from_rows``:
    - accepts type ∈ {'human', 'ai'} (no longer human-only)
    - returns [{'type': 'human'|'ai', 'data': {'content': str}}, ...] instead of a
      plain str list, so downstream ``convert_to_messages`` can restore
      HumanMessage/AIMessage, letting ``FactStore._format_conversation`` render by
      type → name_mapping into the "{MASTER_NAME} | xxx" / "{LANLAN_NAME} | xxx"
      form, from which the path B prompt judges each fact's source attribution
      (user_observation / ai_disclosure)

    Lesson from PR #1399: return list[dict] here and let the caller assemble
    message_dicts and convert with ``convert_to_messages(message_dicts)``
    directly — do **not** wrap with ``json.dumps`` (convert_to_messages only
    accepts a list; a str gets silently swallowed into []).
    """
    out: list[dict] = []
    for _, _, msg_json in rows:
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if not isinstance(msg, dict):
                continue
            msg_type = msg.get('type')
            if msg_type not in ('human', 'ai'):
                continue
            content = msg.get('data', {}).get('content', '')
            # content 归一化：内部可能是 str 或 [{type:'text', text:'...'}, ...]
            # 后者拼回单个 str（path B prompt 不需要细粒度 part 结构，
            # FactStore._format_conversation 把 list content 拼成 ''.join 也是
            # 同样语义）。
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [
                    p.get('text', '')
                    for p in content
                    if isinstance(p, dict) and p.get('type') == 'text'
                ]
                text = ''.join(parts)
            else:
                continue
            if not text.strip():
                continue
            out.append({'type': msg_type, 'data': {'content': text}})
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _trim_to_user_msg_bracket(message_dicts: list[dict]) -> list[dict]:
    """Keep only the messages between the first and last human msg (inclusive).

    Product thesis: guard against cheap-layer pollution. AI content **before** the
    first user msg is a proactive probe the user never validated, and AI content
    **after** the last user msg is a monologue the user never responded to — both
    are cheap layers and shouldn't settle as facts. Only AI content sandwiched
    between two user msgs implies "the user saw / acknowledged this conversation
    context" and qualifies for path B to pick back up as an ai_disclosure fact.

    No human msg at all → return [] (caller treats it as an AI-only window and
    skips). Exactly one human msg → return that one (the bracket degenerates to a
    single point, still legal: that msg is itself the user speaking, and path B can
    use known_pool to see the adjacent AI context).
    """
    human_indices = [
        i for i, m in enumerate(message_dicts) if m.get('type') == 'human'
    ]
    if not human_indices:
        return []
    return message_dicts[human_indices[0]:human_indices[-1] + 1]


def _has_human_messages(messages) -> bool:
    """Check whether the message list contains user (human) messages."""
    for m in messages:
        if getattr(m, 'type', '') == 'human':
            return True
    return False


def _extract_ai_response(messages: list) -> str:
    """Extract the text of the last AI reply from the message list."""
    for m in reversed(messages):
        if getattr(m, 'type', '') == 'ai':
            content = getattr(m, 'content', '')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
                return ''.join(parts)
    return ''


def _extract_user_messages(messages: list) -> list[str]:
    """Extract user message texts from the message list (skipping blanks)."""
    user_msgs = []
    for m in messages:
        if getattr(m, 'type', '') == 'human':
            content = getattr(m, 'content', '')
            if isinstance(content, str):
                if content.strip():
                    user_msgs.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        text = part.get('text', '').strip()
                        if text:
                            user_msgs.append(text)
    return user_msgs

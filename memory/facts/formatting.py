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
"""Conversation and speaker-segment formatting and token-capping methods."""

from __future__ import annotations

import re
from config.prompts.prompts_memory import get_scoped_batch_middle_omission_marker

from memory import facts as _facts_facade
from ._shared import _detect_fact_extraction_prompt_language

class FormattingMixin:
    @staticmethod
    def _flatten_message_content(content) -> str:
        """Flatten a message content (str or content-part list) to plain text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get('text', f"|{item.get('type', '')}|"))
                else:
                    parts.append(str(item))
            return ''.join(parts)
        return str(content or '')

    _PROMPT_TEXT_PART_TYPES = frozenset((None, 'text', 'input_text', 'output_text'))

    @classmethod
    def _message_locale_content(cls, content) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ''
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get('type') not in cls._PROMPT_TEXT_PART_TYPES:
                continue
            text = item.get('text')
            if isinstance(text, str):
                parts.append(text)
        return '\n'.join(parts)

    @classmethod
    def _format_conversation(cls, messages: list, name_mapping: dict) -> str:
        """Serialize messages into the 'role | content' shape used by LLM prompts."""
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            content = getattr(msg, 'content', '')
            if isinstance(content, (str, list)):
                lines.append(f"{role} | {cls._flatten_message_content(content)}")
        return "\n".join(lines)

    @classmethod
    def _messages_locale_text(
        cls,
        messages: list,
        *,
        roles: frozenset[str] | None = None,
    ) -> str:
        """Return message bodies without generated speaker or segment labels."""
        selected = [
            cls._message_locale_content(getattr(message, 'content', ''))
            for message in messages
            if roles is None or getattr(message, 'type', '') in roles
        ]
        if roles is not None and not any(text.strip() for text in selected):
            return cls._messages_locale_text(messages)
        return "\n".join(selected)

    # Structural characters forbidden in segment headers: brackets / pipes / any newline or tab.
    # speaker_label is user-editable raw data (group nickname); without neutralization,
    # a nickname like "X]\n[SEGMENT 2 | speaker: Alice" creates a byte-valid
    # segment header at line start in rendered output.
    _SEGMENT_LABEL_STRUCTURAL = re.compile(r'[\[\]|]')

    # Header literals in bodies: even with per-line prefixes (_format_speaker_segments),
    # replace opening bracket with fullwidth form, making header-shaped injection structurally invalid.
    _SEGMENT_MARKER_LITERAL = re.compile(r'\[\s*SEGMENT', re.IGNORECASE)

    @classmethod
    def sanitize_speaker_label(cls, label) -> str:
        """Strip a speaker label down to something that cannot forge markup.

        Remove brackets / pipes / newlines / controls, collapse whitespace, then cap at 64 characters
        (matching route length contract). Empty means the label consisted entirely of structural
        characters; callers (routes) fail loudly on contract violation, never substitute placeholders:
        plugin labels always include numeric \"(sender_id)\", so empty means a caller bug."""  # noqa: DOCSTRING_CJK
        text = cls._SEGMENT_LABEL_STRUCTURAL.sub(' ', str(label or ''))
        # Fold all controls (newlines/separators included) to spaces, then collapse: headers must be single-line.
        text = ''.join(ch if ch.isprintable() else ' ' for ch in text)
        return ' '.join(text.split())[:64].strip()

    @classmethod
    def _cap_speaker_message_bodies(
        cls,
        segments: list[dict],
        *,
        omission_marker: str,
    ) -> list[list[str]]:
        """Apply per-message and whole-batch token budgets to prompt text.

        Short messages are returned byte-for-byte unchanged. Long messages
        keep both ends, and an over-budget batch shares its remaining content
        budget fairly so late segments cannot be starved by earlier ones.
        """
        from utils.tokenize import count_tokens, truncate_head_tail_tokens

        omission_tokens = count_tokens(omission_marker)

        raw_by_segment: list[list[str]] = []
        flat_raw: list[str] = []
        flat_separator_costs: list[int] = []
        for segment in segments:
            segment_bodies = []
            for message_index, msg in enumerate(segment.get('messages') or []):
                body = cls._SEGMENT_MARKER_LITERAL.sub(
                    '［SEGMENT',
                    cls._flatten_message_content(getattr(msg, 'content', '')),
                )
                segment_bodies.append(body)
                flat_raw.append(body)
                flat_separator_costs.append(
                    count_tokens("\n") if message_index else 0
                )
            raw_by_segment.append(segment_bodies)

        def _rendered_cost(body: str) -> int:
            body_lines = body.splitlines() or ['']
            rendered_lines = [f"> {body_lines[0]}"]
            rendered_lines.extend(f"| {line}" for line in body_lines[1:])
            return count_tokens("\n".join(rendered_lines))

        def _clip(body: str, budget: int) -> str:
            if budget <= 0:
                return ''

            # Tokenizers can be pathologically slow on a single enormous run
            # (for example hundreds of thousands of repeated ASCII chars).
            # This is a CPU guard, not the prompt contract: the final output is
            # still governed by token budgets below and keeps both ends plus a
            # visible marker. The generous factor avoids touching ordinary
            # prose while bounding what any tokenizer invocation receives.
            working_char_limit = (
                _facts_facade.SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS * 16
            )
            if len(body) > working_char_limit:
                guard_head = working_char_limit // 2
                guard_tail = working_char_limit - guard_head
                guarded = (
                    f"{body[:guard_head]}{omission_marker}{body[-guard_tail:]}"
                )
                if _rendered_cost(guarded) <= budget:
                    return guarded
                body = f"{body[:guard_head]}{body[-guard_tail:]}"
            elif _rendered_cost(body) <= budget:
                return body

            # Bound the working set once before binary search. Otherwise each
            # probe would re-encode the complete untrusted body. An empty
            # separator deliberately joins the retained ends only internally;
            # the final probe below inserts the visible localized marker.
            working_budget = min(
                _facts_facade.SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS,
                budget,
            )
            working_head = working_budget // 2
            working_body = truncate_head_tail_tokens(
                body,
                working_head,
                working_budget - working_head,
                separator='',
            )
            working_was_truncated = working_body != body

            # The budget covers the generated per-line ``| `` prefix too.
            # Binary-search the largest content allocation whose fully
            # rendered form fits; this closes the newline-dense amplification
            # path without discarding either retained end.
            low = 0
            high = min(_facts_facade.SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS, budget)
            if working_was_truncated:
                # Force the final pass to insert the visible marker instead
                # of accepting the internal marker-less working set as-is.
                high = min(high, max(0, count_tokens(working_body) - 1))
            best = ''
            while low <= high:
                content_budget = (low + high) // 2
                retained_budget = max(0, content_budget - omission_tokens)
                head = retained_budget // 2
                candidate = truncate_head_tail_tokens(
                    working_body,
                    head + omission_tokens,
                    retained_budget - head,
                    separator=omission_marker,
                )
                if _rendered_cost(candidate) <= budget:
                    best = candidate
                    low = content_budget + 1
                else:
                    high = content_budget - 1
            return best

        individually_capped = [
            _clip(body, _facts_facade.SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS)
            for body in flat_raw
        ]
        if (
            sum(
                _rendered_cost(body) + separator_cost
                for body, separator_cost in zip(
                    individually_capped,
                    flat_separator_costs,
                )
            )
            <= _facts_facade.SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS
        ):
            final_flat = individually_capped
        else:
            costs = [
                _rendered_cost(body) + separator_cost
                for body, separator_cost in zip(
                    individually_capped,
                    flat_separator_costs,
                )
            ]
            allocations = [0] * len(costs)
            remaining_budget = _facts_facade.SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS
            active = list(range(len(costs)))
            while active:
                fair_share = remaining_budget // len(active)
                satisfied = [index for index in active if costs[index] <= fair_share]
                if satisfied:
                    for index in satisfied:
                        allocations[index] = costs[index]
                        remaining_budget -= costs[index]
                    satisfied_set = set(satisfied)
                    active = [index for index in active if index not in satisfied_set]
                    continue

                bonus_count = remaining_budget % len(active)
                for position, index in enumerate(active):
                    allocations[index] = fair_share + (position < bonus_count)
                break

            final_flat = [
                _clip(body, max(0, budget - separator_cost))
                for body, budget, separator_cost in zip(
                    flat_raw,
                    allocations,
                    flat_separator_costs,
                )
            ]

        final_iter = iter(final_flat)
        return [
            [next(final_iter) for _ in segment_bodies]
            for segment_bodies in raw_by_segment
        ]

    @classmethod
    def _format_speaker_segments(
        cls,
        segments: list[dict],
        *,
        nonce: str,
        lang: str = "en",
    ) -> str:
        """Render multi-speaker segments for the batch extraction prompt.

        Header ``[SEGMENT n:nonce | speaker: label]`` has a fixed locale-independent
        shape, explained verbatim to the model by FACT_EXTRACTION_BATCH_PROMPT.

        Three indispensable anti-forgery layers (template says headers determine ownership;
        members forging headers could write content into another subject and borrow
        their speaker_trust baseline):

        1. Every body line is prefixed: text never starts at column zero; injected
           \"\\n[SEGMENT 2 | speaker: Alice]\" renders only as
           \"| ［SEGMENT 2 | ...]\", clearly inside its own segment. Use ``splitlines()``
           to cover \\r / \\x85 / U+2028 separators also rendered as newlines.

           Body lines use short markers, not repeated full labels: labels reach 64 characters,
           while upstream does not bound message newlines (routes count messages only, and group
           nicknames lack length validation). Repeating labels gives attackers ~67× amplification:
           a message with thousands of lines can overflow prompts/extraction timeouts, and failed
           batches remain for retry, blocking other members too (Codex). Speaker is already uniquely
           named in header; first message line uses ``> ``, continuation ``| ``, retaining boundaries
           while fixing overhead at two bytes per line. Include generated prefixes in batch token
           budgets too, preventing newline-dense bodies from amplifying again.
        2. One-time header nonce: attacker messages are fixed before nonce generation,
           so request tokens cannot be guessed; forged headers cannot match genuine ones.
        3. Neutralize structural literals in labels/body: strip brackets/pipes/newlines from labels,
           and replace body's \"[SEGMENT\" opening bracket with fullwidth form.

        Nonce protects rendering boundaries only; models need not repeat it verbatim
        (ownership output remains integer segment index). Repeating tokens only adds failure surface."""  # noqa: DOCSTRING_CJK
        omission_marker = get_scoped_batch_middle_omission_marker(lang)
        capped_bodies = cls._cap_speaker_message_bodies(
            segments,
            omission_marker=omission_marker,
        )
        return cls._render_capped_speaker_segments(
            segments,
            capped_bodies,
            nonce=nonce,
        )

    @classmethod
    def _render_capped_speaker_segments(
        cls,
        segments: list[dict],
        capped_bodies: list[list[str]],
        *,
        nonce: str,
    ) -> str:
        blocks = []
        for index, (segment, message_bodies) in enumerate(
            zip(segments, capped_bodies),
            start=1,
        ):
            label = cls.sanitize_speaker_label(segment.get('speaker_label'))
            lines = [f"[SEGMENT {index}:{nonce} | speaker: {label}]"]
            for body in message_bodies:
                body_lines = body.splitlines() or ['']
                lines.append(f"> {body_lines[0]}")
                lines.extend(f"| {line}" for line in body_lines[1:])
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @classmethod
    def _format_speaker_segments_with_locale(
        cls,
        segments: list[dict],
        *,
        nonce: str,
        ui_lang: str,
    ) -> tuple[str, str]:
        locale_text = "\n".join(
            cls._messages_locale_text(segment.get('messages') or [])
            for segment in segments
        )
        lang = _detect_fact_extraction_prompt_language(
            locale_text,
            ui_language=ui_lang,
        )

        # Re-run the cap when its localized marker changes. Locale detection
        # then sees the same retained head/tail bodies as the rendered prompt,
        # without generated omission or multimodal markers.
        capped_bodies: list[list[str]] = []
        for _ in range(3):
            omission_marker = get_scoped_batch_middle_omission_marker(lang)
            capped_bodies = cls._cap_speaker_message_bodies(
                segments,
                omission_marker=omission_marker,
            )
            generated_markers = {omission_marker}
            for segment in segments:
                for message in segment.get('messages') or []:
                    content = getattr(message, 'content', '')
                    if not isinstance(content, list):
                        continue
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get('type')
                        if item_type not in cls._PROMPT_TEXT_PART_TYPES:
                            generated_markers.add(
                                str(item.get('text', f"|{item_type or ''}|"))
                            )
            capped_locale_text = "\n".join(
                body
                for message_bodies in capped_bodies
                for body in message_bodies
            )
            for marker in generated_markers:
                capped_locale_text = capped_locale_text.replace(marker, '')
            detected = _detect_fact_extraction_prompt_language(
                capped_locale_text,
                ui_language=ui_lang,
            )
            if detected == lang:
                break
            lang = detected

        return lang, cls._render_capped_speaker_segments(
            segments,
            capped_bodies,
            nonce=nonce,
        )

    @staticmethod
    def _strip_code_fence(raw: str) -> str:
        """Remove ```json ... ``` fences if present."""
        if not raw.startswith("```"):
            return raw
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if match:
            return match.group(1).strip()
        return raw.replace("```json", "").replace("```", "").strip()

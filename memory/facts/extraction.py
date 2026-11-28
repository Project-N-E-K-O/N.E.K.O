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
"""LLM fact extraction retries, response parsing, and batch extraction methods."""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Awaitable, Callable
from config import EVIDENCE_EXTRACT_FACTS_MODEL_TIER, SCOPED_BATCH_SEGMENT_NONCE_BYTES
from memory.scopes import SUBJECT_GROUP_CHAT, coerce_subject
from utils.file_utils import robust_json_loads
from utils.token_tracker import set_call_type

from memory import facts as _facts_facade
from ._shared import (
    FactExtractionFailed,
    _detect_fact_extraction_prompt_language,
)

class ExtractionMixin:
    async def _allm_call_with_retries(
        self, prompt: str, lanlan_name: str, tier: str, call_type: str,
        max_retries: int = 3,
        timeout: float = 60,
        thinking: bool = False,
        parse_max_retries: int | None = None,
        failure_info: dict | None = None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
    ):
        """Shared LLM helper: retry on network errors + JSON errors, same
        policy as the old `extract_facts`. Returns parsed JSON or None on
        terminal failure (caller decides whether to abort / swallow).

        ``parse_max_retries`` caps JSON-parse failures separately (``None`` =
        same as ``max_retries``). A caller with its own fallback for malformed
        or truncated output (the external daily import splits the batch) sets
        it to 1 while network errors / 429 / 5xx keep the full backoff budget.

        ``failure_info``: on terminal failure, ``failure_info["reason"]`` is set
        to ``"parse"`` (the last attempt returned unparseable JSON) or
        ``"transport"`` (network / provider / other error), so a caller can
        tell output it might repair from an outage it cannot.

        ``before_attempt`` is awaited before every provider attempt, retries
        included (the external daily import paces all its calls through it).

        Note: no longer accepts temperature. The project-wide convention is to
        never send that parameter (gatekeeper: scripts/check_no_temperature.py).
        The model comes straight from the api_config of ``tier``; the
        SETTING_PROPOSER_MODEL fallback is gone.

        The 60s default timeout suits background LLM calls (Stage-1 fact extract
        / Stage-2 signal detect / negative keyword check); callers may raise it
        as needed (e.g. pass 90s for Stage-2 with thinking on). SDK
        max_retries=0 avoids double-layer retries (the business layer already
        controls retries via its max_retries parameter).

        thinking: False lets create_chat_llm resolve extra_body per model (for
        most providers this disables thinking) under the shared 4096 guard.
        True keeps thinking on through ``memory.thinking_llm`` — the larger
        ``MEMORY_THINKING_OUTPUT_MAX_TOKENS`` cap with a one-shot fallback, and
        a log line when the output is exhausted (#3319: the 4096 guard was hit
        with an empty answer and only surfaced as a JSON parse error).
        Phase D: Stage-2 signal detection passes True."""
        from openai import APIConnectionError, InternalServerError, RateLimitError
        from utils.llm_client import create_chat_llm_async
        from memory.thinking_llm import ainvoke_thinking, describe_output

        retries = 0
        parse_failures = 0
        last_failure = "transport"
        while retries < max_retries:
            resp = None
            try:
                set_call_type(call_type)
                api_config = await self._config_manager.aget_model_api_config(tier)
                if thinking:
                    # Keep the hook right before the actual request: config reads / client construction can also suspend, and putting it before them
                    # would scramble the real call-start order of concurrent requests
                    if before_attempt is not None:
                        await before_attempt()
                    resp, _ = await ainvoke_thinking(
                        api_config, prompt, timeout=timeout,
                        call_label=f"{lanlan_name} {call_type}",
                    )
                else:
                    from config import LLM_OUTPUT_GUARD_MAX_TOKENS
                    llm = await create_chat_llm_async(
                        api_config['model'],
                        api_config['base_url'], api_config['api_key'],
                        timeout=timeout,
                        max_retries=0,
                        max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous for variable-length JSON.
                        provider_type=api_config.get('provider_type'),
                    )
                    try:
                        if before_attempt is not None:
                            await before_attempt()
                        resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # extract-facts prompt assembled from token-capped recent history components.
                    finally:
                        await llm.aclose()
                raw = resp.content.strip()
                raw = self._strip_code_fence(raw)
                return robust_json_loads(raw)
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                retries += 1
                last_failure = "transport"
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} 网络错误 {type(e).__name__}, "
                    f"重试 {retries}/{max_retries}"
                )
                if retries < max_retries:
                    await _facts_facade.asyncio.sleep(2 ** (retries - 1))
                continue
            except json.JSONDecodeError as e:
                retries += 1
                parse_failures += 1
                last_failure = "parse"
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} JSON 解析失败 "
                    f"(重试 {retries}/{max_retries}): {e} ({describe_output(resp)})"
                )
                if parse_max_retries is not None and parse_failures >= parse_max_retries:
                    break
                if retries < max_retries:
                    await _facts_facade.asyncio.sleep(2 ** (retries - 1))
                continue
            except Exception as e:
                retries += 1
                last_failure = "transport"
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} 失败 "
                    f"(重试 {retries}/{max_retries}): {type(e).__name__}: {e}"
                )
                if retries < max_retries:
                    await _facts_facade.asyncio.sleep(2 ** (retries - 1))
                continue

        _facts_facade.logger.warning(
            f"[FactStore] {lanlan_name}: {call_type} 达到最大重试 {max_retries}，放弃"
        )
        if failure_info is not None:
            failure_info["reason"] = last_failure
        return None

    async def _allm_extract_facts(
        self, lanlan_name: str, messages: list,
        *, treat_malformed_as_failure: bool = False,
        speaker_label: str | None = None,
        parse_max_retries: int | None = None,
        failure_info: dict | None = None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
        label_own_words: bool = False,
    ) -> list[dict] | None:
        """Stage-1: pure extraction. Prompt carries no existing observations
        to avoid self-cycling (the LLM quoting an existing reflection back as a
        new fact). Returns the raw LLM-extracted list, or None on terminal failure.

        ``treat_malformed_as_failure``: a non-array payload (e.g. ``{"facts":
        [...]}``) is a model-shape failure, not a confirmed empty extraction.
        The conversation paths tolerate it as ``[]`` (advance the cursor; the
        window is lossy but recoverable from live chat). Daily import passes
        ``True`` so a malformed result becomes a failed day (retryable) rather
        than being checkpointed in the sidecar as a fact-less day — a sidecar
        checkpoint would skip the LLM on every later import and silently lose
        that day's facts (Codex P2).

        ``speaker_label``: render 'user' turns and the {MASTER_NAME}
        placeholder as this label instead of the configured master name. The
        legacy prompt assumes the human speaker IS the master — true for
        private admin chats, wrong for group-member batches, whose speaker
        would otherwise have their statements extracted as facts about the
        master (Codex P2).

        ``label_own_words``: a group-chat history that contains the
        character's own messages; the model also marks each fact's
        ``source`` (``ai_disclosure`` for the character's words), which
        persistence honours. Without it every fact counts as the others'
        words."""
        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        if speaker_label:
            name_mapping['human'] = speaker_label
        conversation_text = self._format_conversation(messages, name_mapping)
        from memory.long_term_sync import retirement_source_prompt
        conversation_text, source_instruction = retirement_source_prompt(
            os.path.join(self._config_manager.memory_dir, lanlan_name) if getattr(self._config_manager, 'memory_dir', None) is not None else None, messages, conversation_text, name_mapping)
        prompt_lang = _detect_fact_extraction_prompt_language(
            self._messages_locale_text(
                messages,
                roles=frozenset({'human', 'user'}),
            ),
            ui_language=_facts_facade.get_global_language_full(),
        )

        template = _facts_facade.get_fact_extraction_prompt(prompt_lang)
        if label_own_words:
            from config.prompts.prompts_memory import (
                get_fact_extraction_group_source_addendum,
            )
            template += get_fact_extraction_group_source_addendum(prompt_lang)
        prompt = (
            template
            .replace('{CONVERSATION}', conversation_text)
            .replace('{LANLAN_NAME}', lanlan_name)
            .replace('{MASTER_NAME}', name_mapping.get('human', '主人'))
        )

        prompt += source_instruction

        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction",
            parse_max_retries=parse_max_retries,
            failure_info=failure_info,
            before_attempt=before_attempt,
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            if treat_malformed_as_failure:
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: Stage-1 返回非数组 "
                    f"{type(extracted).__name__}，当作抽取失败（可重试，不 checkpoint）"
                )
                if failure_info is not None:
                    failure_info["reason"] = "schema"
                return None
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: Stage-1 返回非数组 "
                f"{type(extracted).__name__}，当作空列表处理"
            )
            return []
        return extracted

    async def _allm_extract_facts_batch(
        self, lanlan_name: str, segments: list[dict],
    ) -> list | None:
        """Stage-1 batch: one LLM call over multiple single-speaker segments.

        Output contract: one object per segment (``{\"segment\": n, \"facts\": [...]}``, parsed
        fail-closed by ``extract_facts_batch``). Structured ownership prevents content-bearing
        facts from lacking attribution and makes segment coverage explicit. Non-arrays always mean terminal
        failure, returning None; sole caller is fail-closed scoped_history routing,
        with no permissive empty-result mode.

        Deliberate placeholder order: {LANLAN_NAME} → {SEGMENT_NONCE} → {SEGMENTS}.
        Literal \"{LANLAN_NAME}\" / \"{SEGMENT_NONCE}\" in message bodies must not undergo
        another replacement (especially nonce, which would let attackers print it in their bodies)."""  # noqa: DOCSTRING_CJK
        # One-time segment-boundary token: attacker messages are fixed before generation, making it unpredictable.
        nonce = secrets.token_hex(SCOPED_BATCH_SEGMENT_NONCE_BYTES)
        ui_lang = _facts_facade.get_global_language_full()
        lang, rendered_segments = await _facts_facade.asyncio.to_thread(
            self._format_speaker_segments_with_locale,
            segments,
            nonce=nonce,
            ui_lang=ui_lang,
        )
        prompt = (
            _facts_facade.get_fact_extraction_batch_prompt(lang)
            .replace('{LANLAN_NAME}', lanlan_name)
            .replace('{SEGMENT_NONCE}', nonce)
            .replace('{SEGMENTS}', rendered_segments)
        )
        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction_batch",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: 批抽取返回非数组 "
                f"{type(extracted).__name__}，当作抽取失败（可重试）"
            )
            return None
        return extracted

    @staticmethod
    def _coerce_segment_index(raw, segment_count: int) -> int | None:
        """The 0-based segment index for a model-emitted segment number, or None.

        Accept integers and numeric strings (common model output \"1\"); explicitly exclude bool
        (True subclasses int). Out-of-range always None; never clamp to boundary segments:
        assigning A's content to B is much worse than retrying the whole batch."""  # noqa: DOCSTRING_CJK
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            seg = raw
        elif isinstance(raw, str):
            # Use try/except, not isdigit(): superscript digits ("²") and other characters
            # unsupported by int() still satisfy isdigit(); accepting them then raising ValueError
            # would crash the whole batch.
            try:
                seg = int(raw.strip())
            except ValueError:
                return None
        elif isinstance(raw, float) and raw.is_integer():
            seg = int(raw)
        else:
            return None
        if not (1 <= seg <= segment_count):
            return None
        return seg - 1

    # Keys actually consumed from an LLM fact by :meth:`_apersist_new_facts_locked`.
    # Used only here to determine unconsumed content when a whole segment object becomes a fact.
    #
    # ⚠️ Handwritten lists stale as schemas evolve, misclassifying every fact containing new
    # fields as failed and endlessly re-extracting buckets. Therefore
    # test_persisted_fact_fields_matches_what_persist_actually_reads uses AST
    # to inspect _apersist_new_facts_locked; forgetting to update this list after new fields
    # fails that test instead of looping in production.
    _PERSISTED_FACT_FIELDS = frozenset({
        'text', 'importance', 'entity', 'source', 'event_when',
        '_external_import',
    })

    @staticmethod
    def _as_fact_entry(entry) -> dict | None:
        """Normalize one ``facts[]`` element into a persistable fact, or None.

        Promote bare strings to ``{'text': ...}``: models sometimes output sentences rather than
        objects; content is clear, ownership supplied by enclosing segment, so acceptance loses nothing,
        preferable to discarding content or paying for whole-segment re-extraction. Limit to
        ``str``: rendering numeric/boolean false-like values as text has no meaning."""  # noqa: DOCSTRING_CJK
        if isinstance(entry, dict):
            text = entry.get('text')
            if isinstance(text, str) and text.strip():
                return entry
            return None
        if isinstance(entry, str) and entry.strip():
            return {'text': entry}
        return None

    @classmethod
    def _carries_unused_text(cls, entry) -> bool:
        """True when a value still holds non-blank text somewhere.

        Tests only whether the value contains text, without interpreting field names;
        :meth:`_has_unconsumed_text` handles the field-name layer."""  # noqa: DOCSTRING_CJK
        if isinstance(entry, str):
            return bool(entry.strip())
        if isinstance(entry, dict):
            # Recurse through keys and values equally: malformed fact maps
            # ({"Alice likes cats": 7}) can occur at any field depth
            # ({"fact": {"Alice likes cats": 7}}), missed by top-level-only checks.
            # Field-shaped keys (confidence / start / unit, etc.) are not content.
            return any(
                (
                    isinstance(key, str)
                    and key.strip()
                    and not cls._FIELD_NAME_RE.match(key)
                )
                or cls._carries_unused_text(value)
                for key, value in entry.items()
            )
        if isinstance(entry, (list, tuple, set)):
            return any(cls._carries_unused_text(v) for v in entry)
        return False

    # Field-like JSON keys are ASCII identifiers. New schema fields tend to use
    # confidence / reason / evidence; malformed facts embedding prose into keys
    # ({"Alice likes cats": 7}) rarely look like identifiers: natural language contains spaces
    # or non-ASCII. Distinguish field-name keys from content keys this way.
    _FIELD_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

    @classmethod
    def _holds_unextracted_text(cls, entry, *, always_consumed: tuple = ()) -> bool:
        """True when a **rejected** entry still holds text nobody extracted.

        Ask only for entries yielding nothing, based on whether retries can rescue content:
        - Nothing extracted → another extraction may produce canonical shape,
          recovering the content. Retaining the bucket for retry is useful;
        - Fact extracted, with extra side fields → re-extraction repeats the shape,
          leaving those fields unread. Failing recovers nothing, merely prevents this member's
          memory from ever settling: buckets grow to hard cap then lose original messages,
          far worse than losing a note. Log WARNING only (see caller); segment remains ok.

        ⚠️ Keys themselves may be content: malformed maps like ``{\"Alice likes cats\": 7}``
        hold prose in keys and numeric values; checking values alone discards them as shells
        (Codex). Non-field-shaped keys (see :attr:`_FIELD_NAME_RE`) count as content whenever
        nonblank.

        ``always_consumed``: keys callers already parsed individually (segment object's ``segment``
        / ``facts``), treated as consumed."""  # noqa: DOCSTRING_CJK
        if not isinstance(entry, dict):
            return cls._carries_unused_text(entry)
        for key, value in entry.items():
            if isinstance(key, str) and key in always_consumed:
                continue
            if (
                isinstance(key, str)
                and key.strip()
                and not cls._FIELD_NAME_RE.match(key)
            ):
                return True
            if cls._carries_unused_text(value):
                return True
        return False

    @classmethod
    def _unread_fields_of_accepted_fact(cls, fact, *, always_consumed=()) -> list:
        """Field names on an accepted fact that nobody downstream reads.

        Logging only (rationale in :meth:`_holds_unextracted_text`: failure cannot recover
        content and only prevents this member from settling forever). Logging makes models
        attaching extra prose to facts visible, so prompts can be fixed when it happens,
        rather than detected through an endlessly retrying gate."""  # noqa: DOCSTRING_CJK
        if not isinstance(fact, dict):
            return []
        return sorted(
            str(key)[:64] for key, value in fact.items()
            if key not in cls._PERSISTED_FACT_FIELDS
            and key not in always_consumed
            # Treat keys/values equally (as _carries_unused_text):
            # {"text": "A", "Bob 的生日是 3 月 5 日": 7} stores prose in the key;
            # value-only checking would not even log this content.
            and (
                (
                    isinstance(key, str)
                    and key.strip()
                    and not cls._FIELD_NAME_RE.match(key)
                )
                or cls._carries_unused_text(value)
            )
        )

    @classmethod
    def _parse_batch_segment_entry(
        cls, item, segment_count: int,
    ) -> tuple[int | None, list[dict], int, int]:
        """Parse one top-level element of the batch payload.

        Returns ``(0-based index | None, facts, dropped, suspect)``.

        ``index is None`` means unassignable: it might carry a segment's content,
        but ownership is unknown. Callers raise for the whole batch (same invariant as
        :meth:`extract_facts` retryable whole-batch failure on malformed entries:
        persistence silently skips them; advancing caller cursors permanently loses content).

        Discard has two levels, based on whether content would be lost:
        - ``dropped``: empty shells (``{}`` / ``{\"text\": \"\"}`` / ``{\"importance\": 5}``
          / empty strings), losing no content; segment remains ``ok``;
        - ``suspect``: unused entries still carrying text; mark segment
          ``failed`` so callers retain buckets for retry. Nesting eliminates ambiguous
          ownership, not content in unrecognized shapes; those must fail closed.

        Tolerated shapes:
        - Canonical: ``{\"segment\": n, \"facts\": [...]}``;
        - No segment facts: ``{\"segment\": n}`` (explicitly names segment without content;
          valid no-facts conclusion, not missing coverage);
        - Legacy flat facts: ``{\"segment\": n, \"text\": ...}``, including simultaneous
          facts arrays. Mixing conventions leaves attribution unambiguous (same segment
          object); collect its own text too, never lose it through the list branch.
          Pass entire dictionary unchanged to persistence (it consumes event_when / entity / source
          itself), so none of this content is discarded;
        - Bare strings inside ``facts`` (see :meth:`_as_fact_entry`).
        Present ``facts`` neither array nor ``null`` means malformed, possibly content-bearing:
        unassignable. ``null`` is exempt: carries no content and, like missing fields
        or empty arrays, means no facts; treat as absent (rejecting would re-extract all eight
        segments merely for one empty value)."""  # noqa: DOCSTRING_CJK
        if not isinstance(item, dict):
            return None, [], 0, 0
        index = cls._coerce_segment_index(item.get('segment'), segment_count)
        if index is None:
            return None, [], 0, 0
        raw_facts = item.get('facts')
        if raw_facts is not None and not isinstance(raw_facts, list):
            return None, [], 0, 0

        kept: list[dict] = []
        dropped = 0
        suspect = 0
        unread_fields: list[str] = []
        for entry in (raw_facts or []):
            fact = cls._as_fact_entry(entry)
            if fact is None:
                # Unreadable as a fact: unused text makes it suspect (re-extraction may rescue
                # it in canonical form); discard empty shells only.
                if cls._holds_unextracted_text(entry):
                    suspect += 1
                else:
                    dropped += 1
                continue
            kept.append(fact)
            unread_fields.extend(cls._unread_fields_of_accepted_fact(fact))
        own_fact = cls._as_fact_entry(item)
        # What remains unconsumed on the segment object? Text in remaining keys means this shape
        # was not understood; do not count it as the segment's conclusion, which makes callers pop
        # the bucket and permanently lose that text.
        #
        # Two exclusion cases, based on what was actually consumed:
        # - Whole item accepted as fact → persistence reads _PERSISTED_FACT_FIELDS
        #   (event_when's "day", entity's "master" are consumed;
        #   treating them as side text falsely fails every flat fact with temporal clues). But
        #   unread keys remain unconsumed, e.g. note in {"text": "...", "note": "..."};
        #   that content really disappears on pop, so still classify suspect.
        # - Item unreadable as fact → even text was not consumed ({"text": ["Alice 养猫"]}
        #   wraps content in a non-string); exclude nothing.
        if own_fact is not None:
            kept.append(own_fact)
        # Segment object answered when it supplied its own fact or facts array (even empty:
        # a valid conclusion of no facts). Answered objects get the same handling
        # as accepted facts: side fields only log. Failing {"segment": 1, "facts": [],
        # "reason": "..."} recovers nothing; a model habitually including that field
        # would make this member impossible to settle (Codex).
        if own_fact is not None or isinstance(raw_facts, list):
            unread_fields.extend(cls._unread_fields_of_accepted_fact(
                item, always_consumed=('segment', 'facts'),
            ))
        elif cls._holds_unextracted_text(
            item, always_consumed=('segment', 'facts'),
        ):
            suspect += 1
        if unread_fields:
            _facts_facade.logger.warning(
                f"[FactStore] 批抽取：模型往事实上挂了没人读的字段 "
                f"{sorted(set(unread_fields))}——那部分内容不会入库。判 failed "
                f"换不回它（重抽会复现同一个形状），所以只记一条日志；真频繁"
                f"出现就去改 prompt。"
            )
        return index, kept, dropped, suspect

    @staticmethod
    def _speaker_provenance_of(segment: dict) -> dict | None:
        """The provenance fields to stamp onto this segment's persisted facts.

        speaker_label identifies who spoke; speaker_trust is caller code's trust snapshot.
        Always read from the request segment,
        never LLM output: forged same-name model keys are ignored."""  # noqa: DOCSTRING_CJK
        prov: dict = {}
        label = str(segment.get('speaker_label') or '').strip()
        if label:
            prov['speaker_label'] = label[:64]
        trust = segment.get('speaker_trust')
        if (
            isinstance(trust, (int, float))
            and not isinstance(trust, bool)
            and 0.0 <= float(trust) <= 1.0
        ):
            prov['speaker_trust'] = float(trust)
        from memory.speaker_trust import stable_speaker_id
        speaker_id = stable_speaker_id(segment.get('speaker_id'))
        if speaker_id is not None:
            prov['speaker_id'] = speaker_id
            # Persisted alongside speaker_id, never in place of it — and taken
            # from the segment, so it comes from the SAME request-start pool
            # snapshot that decided routing and trust (see
            # ``_reconcile_existing_provenance`` for why a fresh lookup here
            # would be wrong).
            entity_id = str(
                segment.get('speaker_entity_id') or ''
            ).strip()
            if entity_id:
                prov['speaker_entity_id'] = entity_id
        return prov or None

    @staticmethod
    def group_history_has_own_words(messages, subject) -> bool:
        """Whether a scoped history is a group chat that includes the
        character's own messages, the case ``label_own_words`` is for."""
        memory_subject = coerce_subject(subject)
        return (
            memory_subject is not None
            and memory_subject.kind == SUBJECT_GROUP_CHAT
            and any(getattr(m, 'type', '') == 'ai' for m in messages or ())
        )

    async def extract_facts_batch(
        self, segments: list[dict], lanlan_name: str,
    ) -> list[dict]:
        """Multi-segment scoped extraction: one LLM call, per-segment dispatch.

        ``segments``: ``[{'messages': [...], 'subject': MemorySubject,
        'speaker_label': str, 'speaker_trust': float|None}, ...]`` — one speaker
        per segment. Reduces O(speakers) LLM calls to one per batch.

        Returns one result dict per segment, in request order:
        ``{'status': 'ok'|'failed', 'created': list[dict], 'dropped': int}``.

        Fail-closed semantics operate per segment (versus :meth:`extract_facts` whole-batch
        decisions):
        - Entire LLM call terminal failure / non-array return → raise
          :class:`FactExtractionFailed` (route 502, callers retain all for retry);
        - Unassignable top-level element (missing/out-of-range segment, non-array facts) → whole-batch
          raise: may carry content whose segment is unknown; silently discarding
          makes callers pop buckets whose content disappeared;
        - Segment absent from output → that segment ``failed`` (retain/retry), never
          no-facts: if model merges eight segments into segment 1, the other seven people's
          buckets (sole per-member copies) would otherwise all be popped;
        - Empty shells inside segment objects (``{}`` / ``{\"text\": \"\"}`` / empty strings) → discard,
          count in ``dropped`` for caller; segment stays ``ok``: they carry no
          content, so nothing is lost;
        - Unrecognized shapes still carrying text inside segment → segment ``failed``;
          persist recognized facts normally (SHA-256 deduplication covers retry repetitions,
          preserving at least those facts if retries never succeed);
        - Segment persistence failure → only that segment ``failed``, without restarting others.

        Entire empty array is valid (no facts worth remembering in the whole batch); every segment
        is ``ok`` with zero facts. This is the most common group batch; treating it as zero covered segments
        would make every quiet group-message batch retry endlessly.
        """  # noqa: DOCSTRING_CJK
        if not segments:
            return []

        segment_generations = [
            (
                self._subject_forget_generation(lanlan_name, memory_subject)
                if (memory_subject := coerce_subject(segment.get('subject')))
                is not None
                else None
            )
            for segment in segments
        ]
        extracted = await self._allm_extract_facts_batch(lanlan_name, segments)
        if extracted is None:
            raise FactExtractionFailed(
                f"batch Stage-1 LLM call failed for {lanlan_name!r} "
                f"({len(segments)} segments, fail_closed caller)"
            )
        # None = segment absent from output (distinct from no facts, represented by empty list).
        per_segment: list[list[dict] | None] = [None] * len(segments)
        dropped_per_segment = [0] * len(segments)
        suspect_per_segment = [0] * len(segments)
        unplaceable = 0
        for item in extracted:
            index, facts, dropped, suspect = self._parse_batch_segment_entry(
                item, len(segments),
            )
            if index is None:
                unplaceable += 1
                continue
            if per_segment[index] is None:
                per_segment[index] = []
            per_segment[index].extend(facts)
            dropped_per_segment[index] += dropped
            suspect_per_segment[index] += suspect
        if unplaceable:
            raise FactExtractionFailed(
                f"batch Stage-1 returned {unplaceable}/{len(extracted)} "
                f"unplaceable entries for {lanlan_name!r} (fail_closed caller)"
            )
        if not extracted:
            # Empty array = model concludes no facts worth remembering for the entire batch; every segment answered.
            per_segment = [[] for _ in segments]
        if any(facts is None for facts in per_segment):
            missing = [
                i + 1 for i, facts in enumerate(per_segment) if facts is None
            ]
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: 批抽取输出缺段 {missing}（共 "
                f"{len(segments)} 段）——按失败保留重试，绝不当成「本段无事实」"
            )

        results: list[dict] = []
        chronological_predecessor_failed = False
        for position, (segment, segment_facts) in enumerate(
            zip(segments, per_segment), start=1,
        ):
            dropped = dropped_per_segment[position - 1]
            suspect = suspect_per_segment[position - 1]
            if chronological_predecessor_failed:
                # Segments are authored-order input.  Persisting a later
                # segment after an earlier one failed would give the retry of
                # that predecessor a newer created_at and invert chronology.
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: 批抽取第 {position} 段因前序段"
                    "失败而保留重试（未持久化）"
                )
                results.append(
                    {'status': 'failed', 'created': [], 'dropped': dropped}
                )
                continue
            if segment_facts is None:
                results.append(
                    {'status': 'failed', 'created': [], 'dropped': dropped}
                )
                chronological_predecessor_failed = True
                continue
            if dropped:
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: 批抽取第 {position} 段丢弃 "
                    f"{dropped} 条无内容的垃圾条目（归属由段对象给定，"
                    f"丢弃不损失内容）"
                )
            status = 'ok'
            if suspect:
                # Unrecognized items carrying text: mark segment failed so caller retains bucket.
                # Persist recognized entries normally; retries re-extract them, with SHA-256
                # deduplication covering repeats, while preserving them even if retries never succeed.
                _facts_facade.logger.warning(
                    f"[FactStore] {lanlan_name}: 批抽取第 {position} 段有 "
                    f"{suspect} 条形状看不懂但带文字的条目——按失败保留重试"
                )
                status = 'failed'
            created: list[dict] = []
            reconciled: list[dict] = []
            if segment_facts:
                try:
                    created = await self._apersist_new_facts(
                        lanlan_name,
                        segment_facts,
                        subject=segment.get('subject'),
                        speaker_provenance=self._speaker_provenance_of(segment),
                        expected_subject_generation=(
                            segment_generations[position - 1]
                        ),
                        reconciled_facts=reconciled,
                    )
                except Exception as exc:
                    _facts_facade.logger.error(
                        f"[FactStore] {lanlan_name}: 批抽取第 "
                        f"{position} 段持久化失败（后序段保留重试）: {exc}"
                    )
                    results.append(
                        {'status': 'failed', 'created': [], 'dropped': dropped}
                    )
                    chronological_predecessor_failed = True
                    continue
            results.append(
                {
                    'status': status,
                    'created': created,
                    'reconciled': reconciled,
                    'dropped': dropped,
                }
            )
            if status != 'ok':
                chronological_predecessor_failed = True
        return results

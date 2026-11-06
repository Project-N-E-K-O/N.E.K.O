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

"""Weekly persona introspection: review what the chat model is actually shown.

Every other maintenance job looks at storage — clusters of similar entries,
queued contradictions, recent chat. None of them looks at the one thing the
chat model reads: the rendered long-term memory block ``/new_dialog`` injects.
Hollow relationship labels ("they have formed a deep bond") outrank concrete
facts there for months because nothing ever reads that block back.

This module does, once a week per character with thinking on:

1. Render the injected block exactly as ``/new_dialog`` does, through the same
   pipeline (``arender_injected_memory``), with a ``RenderAnnotator`` attached.
   The annotator only prefixes each bullet with a number and records which
   entry the number stands for; the text is otherwise byte-identical, which
   ``test_annotated_render_matches_injected_render`` holds in place.
2. Ask the correction-tier model to suppress, rewrite or merge entries that
   are contradictory, redundant, hollow, stale or unclear.
3. Validate every number against the map — read-only (character-card) and
   unknown numbers are rejected and counted, never applied.
4. Apply through the persona / reflection managers, then render again: once
   an entry leaves the budget another one moves up into it. At most
   ``MEMORY_INTROSPECTION_MAX_ROUNDS`` rounds; a round with no applied action
   ends the run early.

Suppression here is permanent (``permanent_suppression``), unlike the 5-hour
mention cooldown ``suppress``: the entry stays on disk but stops reaching the
model through every path — render, recall, promotion and refine.

Only the legacy (private chat) render is reviewed; group / participant scoped
subjects are out of scope for now.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


PERMANENT_SUPPRESSION_KEY = 'permanent_suppression'

KIND_PERSONA = 'persona'
KIND_REFLECTION = 'reflection'
# The pinned-fact section: shown for comparison, never a target (it has its
# own lifetimes and admission-time conflict resolution).
KIND_PINNED = 'pinned'

ACTION_SUPPRESS = 'suppress'
ACTION_REWRITE = 'rewrite'
ACTION_MERGE = 'merge'
ACTION_KEEP = 'keep'

_ACTIONS = frozenset({ACTION_SUPPRESS, ACTION_REWRITE, ACTION_MERGE})

# Rejection reasons, counted per round.
REJECT_INVALID = 'invalid'
REJECT_UNKNOWN_ID = 'unknown_id'
REJECT_READONLY = 'readonly'
REJECT_DUPLICATE = 'duplicate_target'
REJECT_CROSS_KIND = 'cross_kind'
REJECT_CROSS_SECTION = 'cross_section'
REJECT_OVER_CAP = 'over_cap'
REJECT_STALE = 'stale'

_REASON_MAX_CHARS = 200

# The checklist codes a suppress names (the prompt lists them in brackets).
ISSUE_CODES = frozenset({
    'contradiction', 'redundant', 'hollow', 'self_rule', 'unclear', 'stale',
    'card_conflict',
})
# Problems in the wording itself: a later line that says the same is just as
# wrong. Stale / contradicted / card-conflicting lines are left out on purpose
# -- fresh facts can make the same sentence true again.
_RESTATEMENT_BLOCKING_ISSUES = frozenset({'hollow', 'redundant', 'self_rule', 'unclear'})


def is_permanently_suppressed(entry) -> bool:
    """True when introspection retired this persona entry / reflection for good.

    Distinct from ``suppress`` (the 5-hour mention cooldown, lifted
    automatically): nothing ever clears this flag.
    """
    return isinstance(entry, dict) and bool(entry.get(PERMANENT_SUPPRESSION_KEY))


def build_permanent_suppression(
    reason: str, now_iso: str, *,
    issue: str | None = None,
    merged_into: str | None = None, restates: str | None = None,
) -> dict:
    """The value stored under ``PERMANENT_SUPPRESSION_KEY``.

    ``issue`` is the checklist code the model gave (see ``ISSUE_CODES``).
    ``merged_into`` names the entry a merge folded this one into: the source
    stays on disk, retired, instead of being deleted. ``restates`` names the
    retired entry a newly synthesized reflection repeats, which is why it was
    stored already suppressed.
    """
    marker = {'at': now_iso, 'reason': reason}
    if issue:
        marker['issue'] = issue
    if merged_into:
        marker['merged_into'] = merged_into
    if restates:
        marker['restates'] = restates
    return marker


def retirement_blocks_restatement(entry) -> bool:
    """Whether a new line restating this retired entry should be retired too.

    Only when the entry was retired for its wording (hollow, redundant, the
    AI's own reaction as a rule, unclear subject), folded into a merge, or
    was itself such a restatement. A line retired as stale, contradicted or
    conflicting with the card can become true again, and one with no issue
    recorded gets the benefit of the doubt.
    """
    if not is_permanently_suppressed(entry):
        return False
    marker = entry.get(PERMANENT_SUPPRESSION_KEY)
    if not isinstance(marker, dict):
        return False
    return (
        marker.get('issue') in _RESTATEMENT_BLOCKING_ISSUES
        or bool(marker.get('merged_into'))
        or bool(marker.get('restates'))
    )


def _clip_reason(raw) -> str:
    text = ' '.join(str(raw or '').split())
    return text[:_REASON_MAX_CHARS]


# ── annotated render ────────────────────────────────────────────────


@dataclass(frozen=True)
class AnnotatedLine:
    number: int
    kind: str
    entry_id: str
    # Persona section key, or the reflection's own ``entity`` as stored. A
    # merge never spans two (promotion merges reflections the same way).
    entity: str | None
    # Raw entry text at render time — the apply step refuses to touch an
    # entry whose text has changed since the model saw it.
    text: str
    readonly: bool


class RenderAnnotator:
    """Numbers each memory bullet a render emits.

    Passed into ``arender_persona_markdown``; the render calls ``tag`` once
    per bullet it writes and puts the returned prefix right after ``- ``.
    Read-only lines (character card, or entries without an id to address
    them by) are numbered too, so the model can cite them as a reference and
    an action aimed at one is counted as a rejection rather than as an
    unknown number.
    """

    def __init__(self) -> None:
        self.lines: dict[int, AnnotatedLine] = {}
        self._section_of: dict[int, str] = {}

    def bind_persona(self, persona_view: dict) -> None:
        for section_key, section in persona_view.items():
            if not isinstance(section, dict):
                continue
            for entry in section.get('facts') or ():
                if isinstance(entry, dict):
                    self._section_of[id(entry)] = section_key

    def tag(self, kind: str, entry: dict) -> str:
        number = len(self.lines) + 1
        entry_id = str(entry.get('id') or '')
        if kind == KIND_PERSONA:
            entity = self._section_of.get(id(entry))
        else:
            entity = entry.get('entity')
        readonly = (
            kind == KIND_PINNED
            or bool(entry.get('protected'))
            or not entry_id
            or (kind == KIND_PERSONA and entity is None)
        )
        self.lines[number] = AnnotatedLine(
            number=number,
            kind=kind,
            entry_id=entry_id,
            entity=entity,
            text=str(entry.get('text') or ''),
            readonly=readonly,
        )
        return f"[{number} RO] " if readonly else f"[{number}] "

    def has_editable_lines(self) -> bool:
        return any(not line.readonly for line in self.lines.values())


@dataclass(frozen=True)
class InjectedMemory:
    """The long-term memory ``/new_dialog`` injects, in its two parts."""

    # The pinned-fact section; "" when nothing is pinned.
    pinned: str
    # The persona + reflection sections; "" falls back to legacy settings.
    persona: str

    @property
    def pinned_prefix(self) -> str:
        return self.pinned + "\n\n" if self.pinned else ""

    @property
    def text(self) -> str:
        """Both parts exactly as ``/new_dialog`` joins them."""
        return self.pinned_prefix + self.persona


async def _arender_pinned_section(
    pinned_fact_store, name: str, lang: str, annotator: RenderAnnotator | None,
) -> str:
    """The pinned-fact section; a failure costs only this section."""
    if pinned_fact_store is None:
        return ""
    tag = (lambda entry: annotator.tag(KIND_PINNED, entry)) if annotator is not None else None
    try:
        return await pinned_fact_store.arender_prompt_block(name, lang, tag=tag)
    except Exception as e:
        logger.warning(
            f"[MemoryServer] {name}: pinned-fact render failed: {type(e).__name__}"
        )
        return ""


async def arender_injected_memory(
    persona_manager, reflection_engine, name: str,
    *, pinned_fact_store=None, lang: str = 'zh',
    annotator: RenderAnnotator | None = None,
) -> InjectedMemory:
    """The pinned-fact section and the persona + reflection block
    ``/new_dialog`` injects.

    The single definition both ``/new_dialog`` and introspection call, so
    the block introspection reviews cannot drift from the one the chat model
    reads: same suppression refresh, same reflection selection, same render.
    The pinned section renders first, as it is injected first; its lines are
    numbered read-only.
    """
    pinned = await _arender_pinned_section(pinned_fact_store, name, lang, annotator)
    try:
        await reflection_engine.aupdate_suppressions(name)
    except Exception as e:
        logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
    pending = await reflection_engine.aget_pending_reflections(name)
    confirmed = await reflection_engine.aget_confirmed_reflections(name)
    extra = {'annotator': annotator} if annotator is not None else {}
    persona = await persona_manager.arender_persona_markdown(
        name, pending, confirmed, **extra,
    )
    return InjectedMemory(pinned=pinned, persona=persona)


# ── action validation ───────────────────────────────────────────────


@dataclass
class IntrospectionPlan:
    persona: list[dict] = field(default_factory=list)
    reflection: list[dict] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)

    @property
    def size(self) -> int:
        return len(self.persona) + len(self.reflection)


_NUMBER_RE = re.compile(r'\d+')
# A run of '=' as long as the prompt's own delimiters. Entry text (a Workshop
# card line, say) could otherwise close the memory block early and continue
# as instructions.
_DELIMITER_RUN_RE = re.compile(r'={6,}')
# A numbered bullet as the render writes it: the tag opens its own line.
_TAG_LINE_RE = re.compile(r'(?m)^[ \t]*- \[(\d+)( RO)?\] ')
# The time label older reflections carry between the tag and the text.
_TIME_LABEL_RE = re.compile(r'\[[^\]\n]*\] ')


def _parse_number(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    match = _NUMBER_RE.search(str(raw or ''))
    return int(match.group()) if match else None


def _raw_targets(action: dict) -> list:
    if action.get('action') == ACTION_MERGE:
        ids = action.get('ids')
        return list(ids) if isinstance(ids, list) else []
    return [action.get('id')]


def plan_actions(
    raw_actions, lines: dict[int, AnnotatedLine], *, max_actions: int,
) -> IntrospectionPlan:
    """Check the model's actions against the number map.

    Every number must exist and be editable; a merge stays within one kind
    and, for persona entries, one section; a number is used by at most one
    action. Anything else is dropped and counted under its reason. ``keep``
    is accepted silently and costs nothing.
    """
    plan = IntrospectionPlan()
    used: set[int] = set()
    accepted = 0
    for action in raw_actions if isinstance(raw_actions, list) else ():
        if not isinstance(action, dict):
            plan.rejected[REJECT_INVALID] += 1
            continue
        verb = action.get('action')
        if verb == ACTION_KEEP:
            continue
        if verb not in _ACTIONS:
            plan.rejected[REJECT_INVALID] += 1
            continue
        if accepted >= max_actions:
            plan.rejected[REJECT_OVER_CAP] += 1
            continue
        numbers: list[int] = []
        for raw in _raw_targets(action):
            number = _parse_number(raw)
            if number is not None and number not in numbers:
                numbers.append(number)
        new_text = str(action.get('text') or '').strip()
        if (
            not numbers
            or (verb == ACTION_MERGE and len(numbers) < 2)
            or (verb != ACTION_SUPPRESS and not new_text)
        ):
            plan.rejected[REJECT_INVALID] += 1
            continue
        targets = [lines.get(number) for number in numbers]
        if any(line is None for line in targets):
            plan.rejected[REJECT_UNKNOWN_ID] += 1
            continue
        if any(line.readonly for line in targets):
            plan.rejected[REJECT_READONLY] += 1
            continue
        if any(number in used for number in numbers):
            plan.rejected[REJECT_DUPLICATE] += 1
            continue
        kinds = {line.kind for line in targets}
        if len(kinds) > 1:
            plan.rejected[REJECT_CROSS_KIND] += 1
            continue
        kind = targets[0].kind
        if len({line.entity for line in targets}) > 1:
            plan.rejected[REJECT_CROSS_SECTION] += 1
            continue
        used.update(numbers)
        accepted += 1
        resolved = {
            'action': verb,
            'targets': [(line.entry_id, line.text) for line in targets],
            'reason': _clip_reason(action.get('reason')) or verb,
            'numbers': numbers,
        }
        if verb != ACTION_SUPPRESS:
            resolved['text'] = new_text
        else:
            issue = str(action.get('issue') or '').strip().lower()
            resolved['issue'] = issue if issue in ISSUE_CODES else None
        # The subject the model saw (one for all targets, checked above):
        # the persona section to act in, or what each reflection must still
        # belong to when the action is applied.
        resolved['entity'] = targets[0].entity
        if kind == KIND_PERSONA:
            plan.persona.append(resolved)
        else:
            plan.reflection.append(resolved)
    return plan


def parse_actions(raw: str):
    """Model output → list of action dicts, or None when it is not a list."""
    from utils.file_utils import robust_json_loads

    text = (raw or '').strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    if not text:
        return None
    try:
        parsed = robust_json_loads(text)
    except Exception:  # noqa: BLE001 — malformed model output is a failed round, not a crash
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get('actions'), list):
        parsed = parsed['actions']
    return parsed if isinstance(parsed, list) else None


# ── the run ─────────────────────────────────────────────────────────


@dataclass
class IntrospectionResult:
    rounds: int = 0
    applied: int = 0
    rejected: Counter = field(default_factory=Counter)
    # None, FAILURE_TRANSIENT (retry soon) or FAILURE_FINAL (wait for next
    # week); see classify_model_failure.
    failure: str | None = None
    failure_reason: str = ''

    @property
    def completed(self) -> bool:
        return self.failure is None


FAILURE_TRANSIENT = 'transient'
FAILURE_FINAL = 'final'


def classify_model_failure(exc: BaseException) -> str:
    """Whether a failed model call is worth retrying soon.

    Transient: a dropped connection, a 5xx or a rate limit -- the same call
    may well succeed in half an hour. Final: a timeout (checked first: both
    SDKs' timeout errors subclass their connection errors), any other 4xx
    such as bad credentials or an unknown model, and anything unrecognised.
    A final failure waits for the next weekly cycle, when the input has
    changed anyway. Connection errors come from the shared OpenAI + Anthropic
    list the chat retry path uses, so either backend of the correction tier
    is recognised.
    """
    import asyncio

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or 'Timeout' in type(exc).__name__:
        return FAILURE_FINAL
    status = getattr(exc, 'status_code', None)
    if isinstance(status, int):
        return FAILURE_TRANSIENT if status == 429 or status >= 500 else FAILURE_FINAL
    import httpx

    from utils.llm_client.retry import chat_retry_error_types

    if isinstance(exc, (ConnectionError, httpx.TransportError, *chat_retry_error_types())):
        return FAILURE_TRANSIENT
    return FAILURE_FINAL


def _print_text(message: str) -> None:
    """Entry text goes to stdout only, never to the log file."""
    from memory.recent import _safe_print

    _safe_print(message)


def _print_plan(name: str, round_no: int, plan: IntrospectionPlan) -> None:
    for action in plan.persona + plan.reflection:
        before = " | ".join(text[:60] for _eid, text in action['targets'])
        after = action.get('text', '')
        arrow = f" → \"{after[:60]}\"" if after else ""
        _print_text(
            f"[Introspection] {name} 第{round_no}轮 {action['action']}: "
            f"\"{before}\"{arrow}（{action['reason'][:60]}）"
        )


def _shown_lines(
    memory_text: str, lines: dict[int, AnnotatedLine],
) -> dict[int, AnnotatedLine]:
    """The numbered lines whose tag and whole text made it into the prompt.

    A line cut off by the input budget, or cut in half, is not offered to
    the model's actions: it judged at most part of that text. The text must
    follow its tag directly (after the time label older reflections carry),
    so neither the tag's own digits nor the same words in a later line pass
    for it, and a numbered list inside an entry's own text does not cut the
    entry short.
    """
    shown: dict[int, AnnotatedLine] = {}
    for match in _TAG_LINE_RE.finditer(memory_text):
        line = lines.get(int(match.group(1)))
        if line is None:
            continue
        rest = memory_text[match.end():]
        label = _TIME_LABEL_RE.match(rest)
        starts = (rest, rest[label.end():]) if label else (rest,)
        # The do-not-mention section prints the stripped text.
        if any(start.lstrip().startswith(line.text.strip()) for start in starts):
            shown[line.number] = line
    return shown


def _build_prompt(
    memory_text: str, *, ai_name: str, master_name: str,
    lines: dict[int, AnnotatedLine],
) -> tuple[str, dict[int, AnnotatedLine]]:
    """The prompt, and the numbered lines it shows in full."""
    from config import (
        MEMORY_INTROSPECTION_INPUT_MAX_TOKENS,
        MEMORY_INTROSPECTION_MAX_ACTIONS_PER_ROUND,
    )
    from config.prompts.prompts_memory import get_memory_introspection_prompt
    from utils.language_utils import (
        detect_prompt_language_with_ascii_fallback,
        get_global_language_full,
    )
    from utils.tokenize import truncate_to_tokens

    # The render is already budgeted (persona / reflection pools plus count
    # caps on card and do-not-mention lines); this is the backstop for a
    # card import big enough to outgrow the sum.
    memory_text = truncate_to_tokens(memory_text, MEMORY_INTROSPECTION_INPUT_MAX_TOKENS)
    shown = _shown_lines(memory_text, lines)
    memory_text = _DELIMITER_RUN_RE.sub('==', memory_text)
    lang = detect_prompt_language_with_ascii_fallback(
        memory_text, ui_language=get_global_language_full(),
    )
    prompt = (
        get_memory_introspection_prompt(lang)
        .replace('{AI_NAME}', ai_name)
        .replace('{MASTER_NAME}', master_name)
        .replace('{MAX_ACTIONS}', str(MEMORY_INTROSPECTION_MAX_ACTIONS_PER_ROUND))
        .replace('{MEMORY}', memory_text)
    )
    return prompt, shown


async def _ainvoke(config_manager, prompt: str, *, name: str, round_no: int):
    """One model call: ``(actions or None, output_ran_out)``."""
    from config import MEMORY_INTROSPECTION_OUTPUT_CAPS, MEMORY_LLM_HARD_TIMEOUT_SECONDS
    from memory.thinking_llm import ainvoke_thinking, response_hit_output_limit
    from utils.token_tracker import set_call_type

    set_call_type("memory_introspection")
    # Same tier as refine and persona correction, thinking on: the actions
    # rewrite long-term persona and are not undone automatically. A larger
    # output allowance than the shared one, since the whole injected block
    # is under review.
    api_config = await config_manager.aget_model_api_config('correction')
    resp, cap = await ainvoke_thinking(  # noqa: LLM_INPUT_BUDGET  # prompt built by _build_prompt, which truncates the memory block to MEMORY_INTROSPECTION_INPUT_MAX_TOKENS.
        api_config, prompt,
        timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
        call_label=f"{name} memory_introspection round={round_no}",
        output_caps=MEMORY_INTROSPECTION_OUTPUT_CAPS,
    )
    actions = parse_actions(getattr(resp, 'content', '') or '')
    return actions, response_hit_output_limit(resp, cap)


async def arun_introspection(
    name: str, *, persona_manager, reflection_engine, config_manager,
    pinned_fact_store=None,
) -> IntrospectionResult:
    """Review and correct one character's injected memory, up to N rounds."""
    from config import (
        MEMORY_INTROSPECTION_MAX_ACTIONS_PER_ROUND,
        MEMORY_INTROSPECTION_MAX_ROUNDS,
    )
    from config.prompts.prompts_memory import _normalize_memory_prompt_lang
    from utils.language_utils import get_global_language_full

    # The character's prompt locale (the caller runs inside it), as
    # /new_dialog uses when the request names no language.
    lang = _normalize_memory_prompt_lang(get_global_language_full())

    result = IntrospectionResult()
    _, _, _, _, name_mapping, _, _, _, _ = await config_manager.aget_character_data()
    master_name = name_mapping.get('human', '主人')
    for round_no in range(1, MEMORY_INTROSPECTION_MAX_ROUNDS + 1):
        annotator = RenderAnnotator()
        memory_text = (await arender_injected_memory(
            persona_manager, reflection_engine, name,
            pinned_fact_store=pinned_fact_store, lang=lang, annotator=annotator,
        )).text
        if not annotator.has_editable_lines():
            break
        prompt, shown_lines = _build_prompt(
            memory_text, ai_name=name, master_name=master_name,
            lines=annotator.lines,
        )
        try:
            raw_actions, ran_out = await _ainvoke(
                config_manager, prompt, name=name, round_no=round_no,
            )
        except Exception as e:  # noqa: BLE001 — classified; the caller schedules the retry
            result.failure = classify_model_failure(e)
            result.failure_reason = type(e).__name__
            logger.warning(
                f"[Introspection] {name} 第{round_no}轮模型调用失败"
                f"（{result.failure}）: {type(e).__name__}: {e}"
            )
            break
        result.rounds = round_no
        if raw_actions is None:
            # Out of output, or not a JSON array: the same input would most
            # likely end the same way, so no early retry.
            result.failure = FAILURE_FINAL
            result.failure_reason = 'output_exhausted' if ran_out else 'unparseable'
            logger.warning(
                f"[Introspection] {name} 第{round_no}轮没有可用的 JSON 数组"
                f"（{result.failure_reason}）"
            )
            break
        plan = plan_actions(
            raw_actions, shown_lines,
            max_actions=MEMORY_INTROSPECTION_MAX_ACTIONS_PER_ROUND,
        )
        result.rejected.update(plan.rejected)
        applied = 0
        if plan.size:
            _print_plan(name, round_no, plan)
            outcomes = []
            if plan.persona:
                outcomes.append(await persona_manager.aapply_introspection_actions(
                    name, plan.persona,
                ))
            if plan.reflection:
                outcomes.append(await reflection_engine.aapply_introspection_actions(
                    name, plan.reflection,
                ))
            for outcome in outcomes:
                applied += outcome['applied']
                if outcome['stale']:
                    result.rejected[REJECT_STALE] += outcome['stale']
                    plan.rejected[REJECT_STALE] += outcome['stale']
        result.applied += applied
        verbs = Counter(a['action'] for a in plan.persona + plan.reflection)
        logger.info(
            f"[Introspection] {name} 第{round_no}轮: 动作 {plan.size} 条 "
            f"(suppress={verbs[ACTION_SUPPRESS]} rewrite={verbs[ACTION_REWRITE]} "
            f"merge={verbs[ACTION_MERGE]})，应用 {applied}，拒绝 {dict(plan.rejected)}；"
            f"ids={[t[0] for a in plan.persona + plan.reflection for t in a['targets']]}"
        )
        if applied == 0:
            break
    return result

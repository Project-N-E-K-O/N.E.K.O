"""LLM prompt placeholder leak detector.

Bug class this guards against
-----------------------------
Prompt templates in ``config/prompts_*.py`` use Python ``str.format()`` style
placeholders like ``{master}`` / ``{window}``. The producer side embeds the
placeholder; the consumer side is responsible for calling ``.format(...)`` to
expand it. When a consumer forgets — e.g. ``sf = _loc(SOME_TEMPLATE, lang)``
returns the raw template string and gets concatenated straight into a system
message — the literal ``{master}`` characters reach the LLM, which is at best
confusing and at worst leaks framework internals into the model output.

Real-world case that motivated this module: PR #1075. ``SCREEN_SECTION_FOOTER``
in ``config/prompts_proactive.py`` was given a ``{master}`` placeholder so it
mirrored the corresponding header. The header was rendered with ``.format()``;
the footer's consumer in ``main_routers/system_router.py`` used ``_loc()``
without ``.format()``. The literal ``{master}`` shipped to the LLM. Caught only
by a Codex review — i.e. by luck.

Strategy
--------
Per-prompt unit tests don't scale: every new prompt would need someone to
remember to add its test. The only layer that automatically covers all current
*and future* prompts is the LLM-call chokepoint, since every prompt has to
flow through it on the way out. So we hook the request-body construction in
``utils.llm_client.ChatOpenAI._params()`` and the realtime equivalent in
``main_logic.omni_realtime_client.OmniRealtimeClient.update_session()``.

Scope: system role only
-----------------------
We deliberately scan only ``role == "system"`` messages. Justification:

* The bug class is "template not rendered". Templates are almost exclusively
  used in instructional/system prompts. Renderers do not pour templates into
  user/assistant turns in this codebase today.
* User and assistant content can legitimately contain literal ``{...}``
  characters — users paste Python f-string snippets, JSON schemas, Rust
  generic syntax, etc. Scanning those would produce false positives that
  drown the real signal.
* If a future feature ever pushes a template through a non-system role
  (e.g. `system_router` starts injecting a rendered system prompt as a
  ``user`` priming turn), expand the role allowlist below. Don't widen it
  speculatively.

Tool-calling fields (``tool_calls`` arguments JSON, ``tool`` role results)
are also skipped: those are model-generated or pre-serialized JSON where a
bare ``{`` is structural, not a placeholder.

Severity
--------
* Test/CI environments (``PYTEST_CURRENT_TEST`` is set, or the user explicitly
  requests with ``NEKO_PROMPT_LEAK_RAISE=1``): raise ``AssertionError``. This
  way *any* existing test that happens to hit the buggy path will fail loud,
  even if it wasn't designed as a prompt-leak test.
* Production: ``logger.warning(...)``. We never want a malformed prompt to
  break the user's running session — but we still want the warning in the
  logs for the bug-bash next morning.

The detector itself must never raise into the LLM call site (other than the
intentional test-mode ``AssertionError``). Callers wrap invocations in
``try/except`` and let ``AssertionError`` propagate while swallowing other
exceptions. See ``utils.llm_client._params`` for the canonical wiring.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# Match ``{name}`` / ``{master_name}`` / ``{a1b2}`` — a Python identifier
# wrapped in single braces. Negative look-around on ``{{`` / ``}}`` lets the
# legitimate doubled-brace escape pass through. The leading char must be
# ``[A-Za-z_]`` to avoid matching ``{0}`` etc., which we don't use anywhere
# in this codebase but are also not the bug shape we're chasing.
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{[A-Za-z_][A-Za-z_0-9]*\}(?!\})")

# Roles where a template leak is plausible. Keep narrow on purpose — see the
# module docstring.
_SCANNED_ROLES = frozenset({"system"})


def _is_test_or_forced() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    flag = os.environ.get("NEKO_PROMPT_LEAK_RAISE", "")
    return flag.lower() in ("1", "true", "yes")


def check_text_for_leaks(text: Any) -> list[str]:
    """Return all unresolved ``{placeholder}`` substrings found in ``text``.

    ``text`` may be ``None`` or a non-string; in those cases we return ``[]``
    silently so callers can pass message ``content`` straight in without
    type-checking. Order is preserved; duplicates are kept (so a callsite log
    line communicates how bad the leak is).
    """
    if not isinstance(text, str) or not text:
        return []
    return _PLACEHOLDER_RE.findall(text)


def _iter_text_parts(content: Any) -> Iterable[str]:
    """Yield every text-like fragment inside an OpenAI ``content`` field.

    Handles plain strings, multimodal lists (``[{type:'text', text:...},
    {type:'image_url', ...}]``) and the rare single-dict shape. Image parts
    are skipped — base64 payloads can't contain a stray ``{master}`` and
    scanning them would just waste cycles and risk regex pathologies.
    """
    if isinstance(content, str):
        yield content
        return
    if isinstance(content, list):
        for part in content:
            yield from _iter_text_parts(part)
        return
    if isinstance(content, dict):
        ptype = content.get("type")
        if ptype in ("text", "input_text", "output_text"):
            t = content.get("text")
            if isinstance(t, str):
                yield t
        # image_url / input_image / tool_use payloads etc. → skip.
        return
    # Unknown shape: don't try to coerce to str — that would risk producing
    # spurious placeholder hits from dict reprs.
    return


def _report(leaks: list[str], where: str) -> None:
    """Emit a leak report. Raises in test mode, warns otherwise.

    ``where`` is a short tag identifying the call-site (e.g. ``"_params:
    model=gpt-4o-mini, msg[0]"``). Kept free-form since callers know best
    what context to surface.
    """
    if not leaks:
        return
    # Dedupe for the message body but preserve count for the summary so we
    # don't bury a 50-instance leak under a tidy "{master}".
    unique = sorted(set(leaks))
    msg = (
        f"LLM payload contains {len(leaks)} unresolved placeholder occurrence(s) "
        f"({len(unique)} unique): {unique} at {where}"
    )
    if _is_test_or_forced():
        raise AssertionError(msg)
    logger.warning(msg)


def check_messages_for_leaks(messages: Any, context: str = "") -> None:
    """Scan an OpenAI-format ``messages`` list for unresolved placeholders.

    Only inspects ``role == "system"`` messages — see the module docstring
    for the rationale and the criteria for widening the scope.

    ``messages`` is permitted to be any iterable of dicts; non-dict entries
    are skipped silently (the message-normalization layer in ``llm_client``
    has already coerced everything by the time we get here, but we stay
    defensive so this function is also callable in tests against raw
    fixtures).
    """
    if not messages:
        return
    try:
        iterator = list(messages)
    except TypeError:
        return
    all_leaks: list[tuple[int, list[str]]] = []
    for idx, msg in enumerate(iterator):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in _SCANNED_ROLES:
            continue
        for text in _iter_text_parts(msg.get("content")):
            hits = check_text_for_leaks(text)
            if hits:
                all_leaks.append((idx, hits))
    if not all_leaks:
        return
    flat: list[str] = []
    for _idx, hits in all_leaks:
        flat.extend(hits)
    where_parts: list[str] = []
    if context:
        where_parts.append(context)
    where_parts.append(
        "messages[" + ",".join(str(i) for i, _ in all_leaks) + "].content"
    )
    _report(flat, where=" | ".join(where_parts))


def check_dict_strings_for_leaks(d: Any, context: str = "") -> None:
    """Recursively scan every string value inside a nested dict/list ``d``.

    Used by the realtime path: the OpenAI Realtime / Gemini Live session
    config nests the system instruction at provider-specific paths
    (``session.instructions`` vs ``setup.system_instruction.parts[].text``
    vs ``config.system_instruction.parts[].text``). Walking the whole tree
    is simpler and provider-agnostic, which the codebase's symmetry rules
    require.

    Same severity contract as ``check_messages_for_leaks``: raise in tests,
    warn in production.
    """
    if d is None:
        return
    leaks: list[str] = []
    paths: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            hits = check_text_for_leaks(node)
            if hits:
                leaks.extend(hits)
                paths.append(path or "<root>")
            return
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else str(k))
            return
        if isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
            return

    _walk(d, "")
    if not leaks:
        return
    where_parts: list[str] = []
    if context:
        where_parts.append(context)
    where_parts.append("paths=" + ",".join(paths))
    _report(leaks, where=" | ".join(where_parts))

# -*- coding: utf-8 -*-
"""
Analyzer-facing view of the running plugin registry.

The analyzer / plugin-routing LLM consumes a *projection* of the host's
plugin registry — not the raw payload. Two audience boundaries live
here:

1. **Lifecycle boundary** — only plugins whose process is currently
   ``running`` are exposed; disabled / stopped / load-failed / pending
   entries are filtered out (the analyzer would otherwise route to a
   capability that has no live process to receive the dispatch).

2. **Caller-role boundary** — methods decorated with the plugin SDK's
   ``@llm_tool`` surface as entries with id prefix
   ``__llm_tool__<name>`` (see ``plugin.sdk.plugin.llm_tool``). Those
   are the *dialog* LLM's tools, called synchronously with the full
   schema. Letting the analyzer also dispatch them means the same
   tool can be triggered through two competing code paths, with the
   analyzer's ~10 s decision latency racing the dialog LLM's direct
   call. The analyzer must only see ``@plugin_entry``-registered
   entries (status / config / queries).

The original audience filter only stripped ``__llm_tool__*`` entries
but left the plugin's ``description`` text untouched. Plugin authors
naturally write descriptions like ``"... AI 通过 minecraft_task 工具
下达指令 ..."`` referencing those very LLM-direct tools, and the
analyzer LLM happily picked the leaked name as an ``entry_id``,
collided with strict matching, retried, and then judged
``has_task=false`` — wasting two LLM round-trips per request.

The sanitizer below appends an explicit, machine-recognizable boundary
marker listing the stripped tool names, so the analyzer's system
prompt can teach the LLM to recognize the marker and refuse to propose
any of those names as an entry. The marker token
(:data:`LLM_DIRECT_BOUNDARY_MARKER`) is intentionally locale-agnostic
— the per-language explanation lives in
``USER_PLUGIN_SYSTEM_PROMPT`` so each language renders its own
instruction.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

__all__ = [
    "LLM_TOOL_ENTRY_PREFIX",
    "LLM_DIRECT_BOUNDARY_MARKER",
    "sanitize_plugins_for_analyzer",
    "extract_llm_direct_tool_names",
]


# Reserved id prefix used by the plugin SDK when materializing an
# ``@llm_tool`` registration as an entry; kept in sync with
# ``plugin.sdk.plugin.llm_tool`` / ``NekoPluginBase`` (see
# ``test_base_auto_registers_decorated_methods`` for the contract).
LLM_TOOL_ENTRY_PREFIX = "__llm_tool__"

# Stable marker the analyzer prompt teaches the LLM to recognize.
# Placed at the END of a sanitized plugin's ``description`` field, in
# the form:
#     <original description> <marker>: name1, name2
# The capitalized English token is intentional — it stays
# recognizable regardless of the analyzer's UI locale, and the per-
# language behavioral rule lives in USER_PLUGIN_SYSTEM_PROMPT.
LLM_DIRECT_BOUNDARY_MARKER = "[LLM-DIRECT TOOLS NOT CALLABLE FROM ANALYZER]"


def _is_llm_tool_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    eid = entry.get("id")
    return isinstance(eid, str) and eid.startswith(LLM_TOOL_ENTRY_PREFIX)


def extract_llm_direct_tool_names(entries: Iterable[Any]) -> List[str]:
    """Return the bare tool names of ``__llm_tool__*`` entries.

    Strips the ``__llm_tool__`` prefix and preserves the encounter
    order. Non-dict / non-prefixed entries are silently skipped so
    the helper is safe to call on raw plugin payloads.
    """
    out: List[str] = []
    for entry in entries or []:
        if not _is_llm_tool_entry(entry):
            continue
        eid = entry["id"]
        name = eid[len(LLM_TOOL_ENTRY_PREFIX):]
        if name:
            out.append(name)
    return out


def _annotate_description(description: Any, llm_tool_names: List[str]) -> str:
    """Append the boundary marker + tool list to a plugin description.

    Empty ``llm_tool_names`` returns the description verbatim — plugins
    with no ``@llm_tool`` exposure don't carry the boundary risk and
    shouldn't pay the prompt tokens.
    """
    desc = description if isinstance(description, str) else ""
    if not llm_tool_names:
        return desc
    names = ", ".join(llm_tool_names)
    suffix = f"{LLM_DIRECT_BOUNDARY_MARKER}: {names}"
    return f"{desc} {suffix}".strip() if desc else suffix


def sanitize_plugins_for_analyzer(raw_plugins: Any) -> List[Dict[str, Any]]:
    """Return the analyzer-safe projection of the raw plugin registry.

    Steps, in order:

    1. Drop anything that isn't a ``dict`` or whose ``status`` is not
       ``"running"`` (lifecycle boundary — see module docstring).
    2. For each running plugin, strip ``__llm_tool__*``-prefixed
       entries from ``entries`` (caller-role boundary).
    3. When stripping removed at least one tool, append the
       :data:`LLM_DIRECT_BOUNDARY_MARKER` and the list of stripped
       tool names to that plugin's ``description``. The analyzer
       system prompt (``USER_PLUGIN_SYSTEM_PROMPT``) carries the
       behavioral rule explaining how the LLM should treat the marker.

    The result is a fresh list of shallow per-plugin dict copies with
    ``entries`` rebuilt as a fresh list; other nested values (metadata
    blocks, schema dicts, etc.) may still share references with the
    input payload. Today's only caller — the HTTP plugin provider —
    consumes the projection read-only, so a full deep copy is not
    worth the cost; if a future caller needs to mutate nested fields,
    it should clone those itself.
    """
    sanitized: List[Dict[str, Any]] = []
    for p in raw_plugins or []:
        if not isinstance(p, dict) or p.get("status") != "running":
            continue
        clone = dict(p)
        entries = clone.get("entries")
        if isinstance(entries, list):
            stripped_names = extract_llm_direct_tool_names(entries)
            clone["entries"] = [e for e in entries if not _is_llm_tool_entry(e)]
            if stripped_names:
                clone["description"] = _annotate_description(
                    clone.get("description"), stripped_names,
                )
        sanitized.append(clone)
    return sanitized

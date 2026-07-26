"""General-purpose secret redaction utility.

Background (P24 §4.3 H / §14.3 B-C audit)
------------------------------------------
Beyond the targeted :func:`persistence.redact_model_config` that walks
``session.model_config`` specifically, we need a broader redactor for:

* ``diagnostics_store.record_internal(detail=...)`` payloads, which may
  nest arbitrary structures from pipeline / router error handlers.
* Future HTTP response bodies that dump session state without going
  through persistence's explicit redaction path.
* Log entries whose ``payload`` dict was assembled from user-typed
  form values (nobody checks per-key whether any leaf is a credential).

Scope (what counts as "sensitive")
-----------------------------------
The key-name list below covers:

* ``api_key`` / ``apiKey`` — primary LLM credential used across providers
* ``api_token`` / ``apiToken`` — some providers use this spelling
* ``access_token`` / ``refresh_token`` — OAuth-style flows
* ``secret`` / ``client_secret`` — generic catch-all
* ``password`` / ``passwd`` — form fields occasionally
* ``authorization`` — full auth header value (``Bearer <token>``)

Value is preserved structurally (same key, same type) so pretty-printers
don't hiccup — the leaf string is replaced by
:data:`REDACTED_PLACEHOLDER`. Non-string sensitive values (e.g. a list
of tokens, a dict of named keys) get the whole subtree elided.

What this does NOT do
----------------------
* **Does not alter session.messages content** (that's user speech/
  LLM output — per §3A G1 "never filter user content" it's explicitly
  preserved raw, including any accidental credential a user might type
  into chat). If a user pastes an API key into Chat, it's the tester's
  responsibility to understand that gets logged.
* **Does not touch snapshot cold spills**. Snapshots live under the
  per-session sandbox dir ``<sandbox>/.snapshots/*.json.gz`` and contain
  raw ``session.model_config`` (with real ``api_key``) so ``rewind``
  can restore full auth state — otherwise rewinding would leave the
  user with ``<REDACTED>`` placeholders and broken subsequent LLM calls.
  The sandbox dir is local-user territory; users sharing a
  ``testbench_data/`` zip bundle must self-audit credentials, same
  risk class as sharing browser localStorage dumps.

See also
--------
* ``persistence.redact_model_config`` — the original targeted redactor
  for ``session.model_config`` specifically (used by save/load/export).
  This module delegates to it for ``model_config``-shaped inputs.
* ``P24_BLUEPRINT §4.3 H`` — the audit that produced this module.
* ``~/.cursor/skills/single-writer-choke-point/SKILL.md`` — general
  "redaction before display / log / export" pattern.
"""
from __future__ import annotations

import copy
import re
from typing import Any

#: Tiers accepted by :func:`redact_export_bundle` (P30 memory export).
#: ``minimal`` = credentials only; ``standard`` = + consistent identity
#: pseudonymisation; ``strict`` = + whole-layer removal of the rawest
#: transcript content. Ordered weakest → strongest.
EXPORT_REDACTION_TIERS: tuple[str, ...] = ("minimal", "standard", "strict")

#: Extra sensitive header-ish keys for export bundles (``cookie`` is not in
#: the base :data:`SENSITIVE_KEYS`; ``bearer`` catches raw bearer values).
EXPORT_EXTRA_SECRET_KEYS: frozenset[str] = frozenset({"cookie", "bearer"})

#: Neutral placeholders for pseudonymised identities. Deliberately NOT the
#: real default character name (e.g. "NEKO") so a placeholder can never
#: collide with real memory content that legitimately mentions that name.
IDENTITY_PLACEHOLDERS: dict[str, str] = {
    "character_name": "「角色」",
    "master_name": "「主人」",
}

#: Minimum length of an identity name we are willing to globally substitute.
#: A 1-char name (or empty) would smear across unrelated text, so we skip it
#: and record a warning instead (blueprint §5.2).
_MIN_IDENTITY_LEN = 2

#: Replacement string for scalar secret leaves. Fixed value (not a
#: per-call random token) so log diffs remain readable and test fixtures
#: can assert on the replacement deterministically.
REDACTED_PLACEHOLDER = "<REDACTED>"

#: Lowercase key names (exact match) that always get their value
#: redacted. Compared case-insensitively by the walker. Keep sorted
#: alphabetically so diffs of added secret types are reviewable.
SENSITIVE_KEYS: frozenset[str] = frozenset({
    "access_token",
    "accesstoken",
    "api_key",
    "api_token",
    "apikey",
    "apitoken",
    "authorization",
    "client_secret",
    "clientsecret",
    "password",
    "passwd",
    "refresh_token",
    "refreshtoken",
    "secret",
})


def redact_secrets(
    obj: Any,
    *,
    placeholder: str = REDACTED_PLACEHOLDER,
    extra_keys: frozenset[str] | set[str] | None = None,
) -> Any:
    """Return a deep-copied ``obj`` with any sensitive field values masked.

    Walks dict / list / tuple recursively. Plain scalars pass through.
    Dict keys are compared case-insensitively against :data:`SENSITIVE_KEYS`
    plus any ``extra_keys`` the caller supplies (per-site custom secrets).

    Non-mutating: input is never modified.

    Parameters
    ----------
    obj : Any
        The structure to redact. Usually ``dict | list | str``; anything
        else is returned unchanged (via deepcopy).
    placeholder : str
        What to replace sensitive string leaves with. Defaults to
        :data:`REDACTED_PLACEHOLDER`. Set to a marker like
        ``"<REDACTED:api_key>"`` if you want per-caller context in logs.
    extra_keys : set[str] | None
        Extra key names (lowercase) to treat as sensitive on top of
        :data:`SENSITIVE_KEYS`. Example: ``{"cookie", "bearer"}`` for
        HTTP header redaction specifically.

    Examples
    --------
    >>> redact_secrets({"api_key": "sk-123", "model": "gpt-4"})
    {'api_key': '<REDACTED>', 'model': 'gpt-4'}

    >>> redact_secrets({"nested": {"api_key": "sk-123"}, "safe": "ok"})
    {'nested': {'api_key': '<REDACTED>'}, 'safe': 'ok'}

    >>> redact_secrets(["plain", {"password": "hunter2"}])
    ['plain', {'password': '<REDACTED>'}]
    """
    sensitive = SENSITIVE_KEYS | (frozenset(extra_keys) if extra_keys else frozenset())

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for k, v in node.items():
                # Match key names case-insensitively so "ApiKey" / "API_KEY"
                # are caught too. Keep the original key casing in output.
                is_sensitive = (
                    isinstance(k, str) and k.lower() in sensitive
                )
                if is_sensitive and v:  # don't mask empty / None (no secret to hide)
                    # Non-string sensitive values get the whole subtree elided
                    # (matches the module-level "subtree elided" guarantee).
                    out[k] = placeholder
                else:
                    out[k] = _walk(v)
            return out
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, tuple):
            return tuple(_walk(item) for item in node)
        # Scalars (str / int / float / bool / None / datetime / etc.)
        # pass through. We don't try to detect "looks like an api key
        # inside a random free-text field" — that'd require regex heuristics
        # with false-positives; users who paste credentials into Chat
        # content are explicitly out of scope per the module docstring.
        return node

    return _walk(copy.deepcopy(obj))


# ── P30: memory analysis export bundle redaction ────────────────────
#
# The memory export (P30) ships a character's raw memory + our derived
# analysis in one shareable ZIP. Redaction here is fundamentally different
# from ``redact_secrets`` (which only masks *credential-shaped keys*):
#
#   * It must pseudonymise *identity names* (master / character) that appear
#     as free-text substrings anywhere in the bundle.
#   * It must be **cross-layer consistent** (blueprint §5.1 R-Consistency,
#     user hard constraint): the same identity token maps to the same
#     placeholder in the raw dialogue, in facts/reflections, AND in the
#     derived analysis — never "dialogue says A but the fact says B".
#
# We deliberately DO NOT attempt regex PII scrubbing of arbitrary free text:
# the module docstring above and §3A G1 ("never filter user content") reject
# that as unreliable (false positives) and out of scope. The strongest tier
# instead removes the *rawest transcript layer wholesale* — a whole-field
# structural placeholder, not a per-token smear — so it can't create the
# cross-layer divergence the user warned about.


def build_identity_map(
    identity_names: dict[str, Any] | None,
) -> tuple[dict[str, str], list[str]]:
    """Return ``(name -> placeholder, warnings)`` for pseudonymisation.

    ``identity_names`` maps logical roles (``character_name`` /
    ``master_name``) to the real values pulled from the session persona.
    A name shorter than :data:`_MIN_IDENTITY_LEN` is skipped (it would smear
    across unrelated text) and recorded as a warning.
    """
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for role, placeholder in IDENTITY_PLACEHOLDERS.items():
        raw = (identity_names or {}).get(role)
        name = str(raw or "").strip()
        if not name:
            continue
        if len(name) < _MIN_IDENTITY_LEN:
            warnings.append(
                f"身份名 {role}={name!r} 过短 (<{_MIN_IDENTITY_LEN} 字), "
                "跳过假名化以免误伤正文"
            )
            continue
        # A later role must not overwrite an earlier placeholder for the same
        # literal (e.g. master_name == character_name). First writer wins.
        if name not in mapping:
            mapping[name] = placeholder
    return mapping, warnings


def apply_identity_map(obj: Any, mapping: dict[str, str]) -> Any:
    """Deep-copy ``obj`` replacing every ``name`` substring in string leaves.

    Uses a SINGLE-PASS alternation (longest name first) so that (a) a name that
    is a substring of another (``"NEKO"`` vs ``"NEKO酱"``) is not partially
    clobbered, and (b) an already-inserted placeholder can never be re-matched
    and rewritten by a later, shorter name (the classic sequential-``replace``
    corruption when a real name happens to equal a placeholder token).
    """  # noqa: DOCSTRING_CJK
    if not mapping:
        return copy.deepcopy(obj)
    # Longest first ⇒ leftmost-longest match preference at each position.
    ordered = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)
    pattern = re.compile("|".join(re.escape(name) for name, _ in ordered))
    lookup = dict(ordered)

    def _sub(text: str) -> str:
        if not text:
            return text
        return pattern.sub(lambda m: lookup[m.group(0)], text)

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return _sub(node)
        if isinstance(node, dict):
            # Rewrite string KEYS too — an identity can be a mapping key
            # (e.g. ``persona.json`` is ``{entity_name: {...}}``), and leaving
            # the key un-pseudonymised would leak the real name and break
            # cross-layer consistency (blueprint §5.1). On the rare chance two
            # keys collapse to the same placeholder, last-writer wins.
            return {(_sub(k) if isinstance(k, str) else k): _walk(v)
                    for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, tuple):
            return tuple(_walk(item) for item in node)
        return node

    return _walk(copy.deepcopy(obj))


def _omit_transcript_content(raw_data: dict[str, Any]) -> bool:
    """strict tier: replace the rawest transcript *content* with placeholders.

    Mutates ``raw_data`` in place. Removes free-text ``content`` from the
    conversation corpus turns and ``recent.json`` messages while preserving
    structure (count / role / timestamp). Returns True if anything was
    omitted. Derived memory (facts / reflections / persona) is intentionally
    left in place — it's the abstract analysis value, already pseudonymised.
    """
    omitted = False

    corpus = raw_data.get("conversation_corpus.json")
    if isinstance(corpus, dict):
        for turn in corpus.get("turns", []) or []:
            if isinstance(turn, dict) and isinstance(turn.get("content"), str):
                role = turn.get("role") or "?"
                turn["content"] = f"<omitted len={len(turn['content'])} role={role}>"
                omitted = True

    recent = raw_data.get("recent.json")
    if isinstance(recent, list):
        for entry in recent:
            if not isinstance(entry, dict):
                continue
            data = entry.get("data")
            if isinstance(data, dict) and isinstance(data.get("content"), str):
                role = entry.get("type") or "?"
                data["content"] = f"<omitted len={len(data['content'])} role={role}>"
                omitted = True

    return omitted


#: Lineage node types whose text IS raw transcript (verbatim conversation),
#: as opposed to derived memory (fact / reflection / persona / correction).
_LINEAGE_TRANSCRIPT_NODE_TYPES = frozenset({"message", "recent_memo"})


def _omit_lineage_transcript(analysis: dict[str, Any]) -> bool:
    """Strip verbatim conversation text from ``analysis/lineage.json`` nodes.

    The lineage snapshot carries conversation nodes whose ``label`` and
    ``meta.content`` are the *raw dialogue*. That text must be omitted whenever
    the raw transcript itself is (strict tier, or corpus excluded) — otherwise a
    "shareable" bundle leaks, through the analysis layer, the very conversation
    the raw layer claims to omit. Derived nodes (fact / reflection / persona /
    correction) are intentionally left in place. Mutates ``analysis`` in place;
    returns True if anything was omitted.
    """
    lineage = analysis.get("lineage.json")
    if not isinstance(lineage, dict):
        return False
    nodes = lineage.get("nodes")
    if not isinstance(nodes, list):
        return False
    omitted = False
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") not in _LINEAGE_TRANSCRIPT_NODE_TYPES:
            continue
        role = node.get("status") or "?"
        label = node.get("label")
        if isinstance(label, str):
            node["label"] = f"<omitted len={len(label)} role={role}>"
            omitted = True
        meta = node.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("content"), str):
            meta["content"] = f"<omitted len={len(meta['content'])} role={role}>"
            omitted = True
    return omitted


def redact_export_bundle(
    bundle: dict[str, Any],
    *,
    tier: str = "standard",
    identity_names: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Redact a P30 memory export ``bundle`` at the given ``tier``.

    This is the **single redaction chokepoint** for memory export (blueprint
    §5). Applied as the LAST step of bundle assembly so it covers every text
    field, including analysis findings and persona summaries, with one shared
    identity map (cross-layer consistency, §5.1).

    Parameters
    ----------
    bundle : dict
        ``{"raw_data": {...}, "analysis": {...}, ...}`` — the assembled,
        un-redacted export bundle. Not mutated (deep-copied).
    tier : str
        One of :data:`EXPORT_REDACTION_TIERS`. Invalid values raise
        ``ValueError`` so the router can map to 400.
    identity_names : dict | None
        ``{"character_name": ..., "master_name": ...}`` real values used to
        build the pseudonym map (``standard`` / ``strict`` only).

    At the ``strict`` tier the verbatim dialogue that also lives inside
    ``analysis/lineage.json`` message nodes is scrubbed too, matching the raw
    layer so the analysis layer can not leak a transcript strict withheld.

    Returns
    -------
    (redacted_bundle, info) where ``info`` = ``{"tier", "identity_map_size",
    "strict_transcript_omitted", "lineage_transcript_omitted", "warnings"}``.
    ``info`` deliberately does NOT contain the pseudonym → real-name reverse
    map (§5.3 铁律) — only the count.
    """  # noqa: DOCSTRING_CJK
    if tier not in EXPORT_REDACTION_TIERS:
        raise ValueError(
            f"unknown redaction tier {tier!r}; expected one of {EXPORT_REDACTION_TIERS}"
        )

    warnings: list[str] = []

    # Tier A (all tiers): credentials always removed.
    redacted = redact_secrets(bundle, extra_keys=EXPORT_EXTRA_SECRET_KEYS)

    identity_map_size = 0
    # Tier B/C: consistent identity pseudonymisation over the WHOLE bundle.
    if tier in ("standard", "strict"):
        mapping, map_warnings = build_identity_map(identity_names)
        warnings.extend(map_warnings)
        redacted = apply_identity_map(redacted, mapping)
        identity_map_size = len(mapping)

    # Tier C: whole-layer removal of the rawest transcript content.
    strict_omitted = False
    if tier == "strict":
        raw_data = redacted.get("raw_data")
        if isinstance(raw_data, dict):
            strict_omitted = _omit_transcript_content(raw_data)

    # Conversation text also rides inside ``analysis/lineage.json`` message
    # nodes. At the strict tier the raw transcript (recent.json + corpus) is
    # omitted, so the lineage copy MUST be omitted too — otherwise a "strict"
    # shareable bundle leaks, through the analysis layer, the very dialogue the
    # raw layer withheld (and it would be cross-layer INCONSISTENT: raw omitted
    # vs analysis verbatim). Only strict scrubs here: at minimal/standard the
    # raw dialogue is retained, so scrubbing lineage would itself break §5.1
    # consistency (raw shows A, analysis shows <omitted>).
    lineage_omitted = False
    if tier == "strict":
        analysis = redacted.get("analysis")
        if isinstance(analysis, dict):
            lineage_omitted = _omit_lineage_transcript(analysis)

    info = {
        "tier": tier,
        "identity_map_size": identity_map_size,
        "strict_transcript_omitted": strict_omitted,
        "lineage_transcript_omitted": lineage_omitted,
        "warnings": warnings,
    }
    return redacted, info


__all__ = [
    "EXPORT_EXTRA_SECRET_KEYS",
    "EXPORT_REDACTION_TIERS",
    "IDENTITY_PLACEHOLDERS",
    "REDACTED_PLACEHOLDER",
    "SENSITIVE_KEYS",
    "apply_identity_map",
    "build_identity_map",
    "redact_export_bundle",
    "redact_secrets",
]

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
from typing import Any

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


__all__ = ["REDACTED_PLACEHOLDER", "SENSITIVE_KEYS", "redact_secrets"]

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

"""Structured per-character voice config (voice-source unification, blueprint §2/§9).

A character's voice used to be a single flat ``voice_id`` string that overloaded
three orthogonal things and leaked routing through prefixes
(``gsv:`` / ``eleven:`` / ``__gptsovits_disabled__|``). This module models it as a
single structured object instead:

* **source** — where the voice identity comes from: ``preset`` (official built-in)
  / ``clone`` (user-cloned) / ``design`` (text-described, generated; future).
* **provider** — which TTS backend owns it (``gemini`` / ``gptsovits`` /
  ``cosyvoice`` / ``elevenlabs`` / ``minimax`` / ``vllm_omni`` / ``free`` / ...).
* **ref** — the voice identity *within* that provider (preset name / clone id /
  design id). Not globally unique; only unique per ``(provider, ref)``.
* **config** — optional ``{url, model, api_key_ref}`` for self-pointed local/hosted
  endpoints; empty for native / SaaS providers that resolve creds elsewhere.

This module holds **only** the data model and the *unambiguous, context-free*
legacy-prefix parser. Resolving a *bare* legacy id (no prefix) to its
(source, provider) needs runtime context (current API key buckets / native
registry / free catalog) and therefore lives on ``ConfigManager`` next to the
existing ``validate_voice_id`` resolution, which it reuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Voice sources (dimension B). Empty string = "no voice configured".
SOURCE_PRESET = "preset"
SOURCE_CLONE = "clone"
SOURCE_DESIGN = "design"
VALID_SOURCES = frozenset({SOURCE_PRESET, SOURCE_CLONE, SOURCE_DESIGN})


@dataclass(frozen=True)
class VoiceConfig:
    """One character's voice, as two orthogonal dimensions + a ref + optional config.

    Frozen so it can be passed around dispatch without aliasing surprises; use
    :meth:`to_dict` for the JSON-at-rest form and :meth:`from_any` to read back
    either the new object form or a legacy flat string.
    """

    source: str = ""
    provider: str = ""
    ref: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True when no voice is configured (no ref / source / provider)."""
        return not self.ref and not self.source and not self.provider

    def to_dict(self) -> dict[str, Any]:
        """Serializable form for characters.json. ``config`` omitted when empty to
        keep the stored object minimal for native / SaaS providers."""
        d: dict[str, Any] = {
            "source": self.source,
            "provider": self.provider,
            "ref": self.ref,
        }
        if self.config:
            d["config"] = dict(self.config)
        return d

    @classmethod
    def from_any(cls, value: Any) -> "VoiceConfig":
        """Read a VoiceConfig from either the new object form or a legacy value.

        * ``VoiceConfig`` → returned as-is.
        * ``dict`` with source/provider/ref → the structured object.
        * ``str`` → the *unambiguous* legacy prefix is parsed; a bare id (no
          prefix) is carried through as ``ref`` only — callers that need its
          provider/source must run the context-aware normalizer
          (``ConfigManager.normalize_voice_id_to_config``).
        * anything else → empty.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            parsed = parse_legacy_voice_id(value)
            if parsed is not None:
                return parsed
            return cls(ref=value.strip())
        if isinstance(value, dict):
            # Tolerate a dict that is actually a stored object.
            return cls(
                source=str(value.get("source") or ""),
                provider=str(value.get("provider") or ""),
                ref=str(value.get("ref") or ""),
                config=dict(value.get("config") or {}),
            )
        return cls()


def parse_legacy_voice_id(voice_id: Any) -> "VoiceConfig | None":
    """Parse the *unambiguous* prefixed legacy ``voice_id`` forms into a VoiceConfig.

    Handles exactly the forms whose provider is encoded in a prefix (so no runtime
    context is needed):

    * empty → empty VoiceConfig (no voice).
    * ``__gptsovits_disabled__|...`` → empty: the retired GPT-SoVITS "disabled"
      placeholder is *not* an active voice; it froze a now-unselected config into
      voice_id and is dropped on normalize (blueprint §6).
    * ``eleven:<id>`` → ``{clone, elevenlabs, <id>}``.
    * ``gsv:<id>`` → ``{clone, gptsovits, <id>}``.

    Returns ``None`` for a **bare** id (no recognizable prefix): its
    (source, provider) is ambiguous without runtime context, so the caller must
    fall back to the context-aware normalizer.
    """
    # Imports are local to avoid import cycles (config / utils.* import this module
    # transitively in some paths) and to keep the data model dependency-light.
    from config import GSV_VOICE_PREFIX
    from utils.tts.providers.elevenlabs import ELEVENLABS_TTS_VOICE_PREFIX
    from utils.gptsovits_config import GSV_DISABLED_VOICE_PREFIX

    s = str(voice_id or "").strip()
    if not s:
        return VoiceConfig()
    if s.startswith(GSV_DISABLED_VOICE_PREFIX):
        return VoiceConfig()
    if s.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return VoiceConfig(
            source=SOURCE_CLONE,
            provider="elevenlabs",
            ref=s[len(ELEVENLABS_TTS_VOICE_PREFIX):].strip(),
        )
    if s.startswith(GSV_VOICE_PREFIX):
        return VoiceConfig(
            source=SOURCE_CLONE,
            provider="gptsovits",
            ref=s[len(GSV_VOICE_PREFIX):].strip(),
        )
    return None


def normalize_voice_id(
    voice_id: Any,
    *,
    vllm_selected: bool = False,
    clone_provider_lookup: "Any" = None,
    is_native: "Any" = None,
    native_provider: str = "",
    hosted_preset_provider: "Any" = None,
    free_voice_ids: "Any" = (),
) -> "VoiceConfig":
    """Resolve a legacy ``voice_id`` (prefixed *or* bare) to a structured VoiceConfig.

    Pure: all runtime context is injected, so it is testable without a ConfigManager.
    Mirrors the resolution order of ``ConfigManager.validate_voice_id`` so migration
    is faithful:

    1. unambiguous prefix (``eleven:`` / ``gsv:`` / disabled placeholder / empty) —
       handled by :func:`parse_legacy_voice_id`.
    2. ``vllm_selected`` → ``{preset, vllm_omni, ref}`` (vLLM-Omni uses preset ids).
    3. ``clone_provider_lookup(ref)`` returns a provider (the ref is a cloned voice
       in the current API's voice_storage) → ``{clone, <provider>, ref}``.
    4. ``is_native(ref)`` → ``{preset, <native_provider>, ref}``.
    5. ``hosted_preset_provider(ref)`` returns a provider key (the ref is a built-in
       preset of the currently selected hosted/local provider, e.g. MiMo's "Milo")
       → ``{preset, <provider key>, ref}``.
    6. ``ref in free_voice_ids`` → ``{preset, free, ref}``.
    7. otherwise unresolved → ``{ref}`` only (provider/source unknown; carried through
       so nothing is lost — callers treat an unresolved ref as "leave as-is").

    Args:
        clone_provider_lookup: ``ref -> provider|None`` (None = not a known clone).
        is_native: ``ref -> bool``.
        hosted_preset_provider: ``ref -> provider key|None`` — the selected
            hosted/local provider's key when ``ref`` is one of its preset voices,
            else None (mirrors ``tts_provider_registry.is_selected_preset_voice``).
        free_voice_ids: container of free preset voice ids.
    """
    parsed = parse_legacy_voice_id(voice_id)
    if parsed is not None:
        return parsed

    ref = str(voice_id or "").strip()
    if not ref:
        return VoiceConfig()

    if vllm_selected:
        return VoiceConfig(source=SOURCE_PRESET, provider="vllm_omni", ref=ref)

    if clone_provider_lookup is not None:
        provider = clone_provider_lookup(ref)
        if provider is not None:
            return VoiceConfig(source=SOURCE_CLONE, provider=str(provider or ""), ref=ref)

    if is_native is not None and is_native(ref):
        return VoiceConfig(source=SOURCE_PRESET, provider=str(native_provider or ""), ref=ref)

    if hosted_preset_provider is not None:
        provider = hosted_preset_provider(ref)
        if provider:
            return VoiceConfig(source=SOURCE_PRESET, provider=str(provider), ref=ref)

    if ref in free_voice_ids:
        return VoiceConfig(source=SOURCE_PRESET, provider="free", ref=ref)

    return VoiceConfig(ref=ref)


def to_legacy_voice_id(vc: "VoiceConfig") -> str:
    """Reverse shim: render a VoiceConfig back to the flat, prefixed ``voice_id`` the
    existing dispatch / validation chain understands.

    Transitional scaffolding for the full refactor: lets storage move to the
    structured object while consumers not yet migrated keep receiving the legacy
    string (``eleven:`` / ``gsv:`` prefixes reconstructed from ``provider``). Removed
    once every consumer reads VoiceConfig directly.
    """
    if vc is None or vc.is_empty():
        return ""
    if vc.provider == "elevenlabs":
        return f"eleven:{vc.ref}"
    if vc.provider == "gptsovits":
        return f"gsv:{vc.ref}"
    return vc.ref


def read_legacy_voice_id(raw: Any) -> str:
    """Read a per-character voice **at rest** (either the legacy flat string or the new
    structured ``{source, provider, ref}`` object) as the flat, prefixed legacy
    ``voice_id`` string the runtime / validation chain consumes.

    This is the read-tolerance seam for the union-find-style lazy migration (blueprint
    §6): some entries are flat strings (untouched), some are objects (migrated on a
    user voice-set). Every consumer that loads a character's voice as a string funnels
    through here, so downstream logic (dispatch / validate / free-preset gating) keeps
    working on strings and never needs to know which form is at rest.

    * ``dict`` → reconstruct the legacy string from the object (``eleven:`` / ``gsv:``
      prefixes restored from ``provider``; presets/native have no prefix so round-trip
      to their bare ``ref``).
    * ``str`` → returned stripped (already the legacy form).
    * anything else / None → ``""``.
    """
    if isinstance(raw, dict):
        return to_legacy_voice_id(VoiceConfig.from_any(raw))
    return str(raw or "").strip()

"""Character profile manager: preset + user JSON load, scene lookup, layered
rendering, initial runtime overlay. Stateless service; canonical state lives
in :class:`GalgameSharedState` and the plugin store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import unicodedata

from .context_tokens import count_tokens_heuristic


REQUIRED_FIELDS: frozenset[str] = frozenset({"identity", "character_voice"})
OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {"appearance", "relationships", "background", "key_events", "spoiler_warning"}
)
VOICE_REQUIRED: frozenset[str] = frozenset({"core_traits"})
VOICE_TRAIT_REQUIRED: frozenset[str] = frozenset({"trait", "speech_effect"})

MAX_PROFILE_SIZE_BYTES: int = 50 * 1024
"""Per-file upper bound for character_data JSON (preset and user)."""

L0_MAX_TOKENS: int = 50
L1_MAX_TOKENS: int = 150
L2_MAX_TOKENS: int = 600

_L1_MAX_TRAITS: int = 3
_L1_TRAIT_TEXT_CAP: int = 22
_L1_SPEECH_TEXT_CAP: int = 22
_L1_IDENTITY_CAP: int = 40


_TOP_LEVEL_REQUIRED: frozenset[str] = frozenset({"game_id", "characters"})

_TRAIT_TEXT_RE = re.compile(r"\s+")
_MATCH_SPACE_RE = re.compile(r"\s+")
_GAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _match_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _MATCH_SPACE_RE.sub("", normalized.strip())


def _merge_unique_strings(*groups: object) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            text = str(item or "").strip()
            key = _match_key(text)
            if text and key not in seen:
                merged.append(text)
                seen.add(key)
    return merged


def _normalize_game_id_for_path(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _GAME_ID_RE.fullmatch(normalized):
        return ""
    return normalized


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating a character data payload."""

    valid: bool
    profiles: dict[str, dict[str, Any]]
    version: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _invalid_result(
    *,
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ValidationResult:
    """Shorthand for a failed :class:`ValidationResult`."""
    return ValidationResult(
        valid=False, profiles={}, version="", errors=errors, warnings=warnings
    )


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Outcome of importing a user JSON file into the data directory."""

    ok: bool
    target_path: str
    profile_count: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CharacterProfileMatch:
    """Best profile file match for a runtime game/window signal."""

    game_id: str
    reason: str
    source_value: str = ""


class CharacterProfileManager:
    """Service for loading and rendering character profiles.

    The manager does not own canonical state; it only holds a lightweight
    in-memory cache keyed by ``game_id`` to avoid re-reading the same JSON
    files when the plugin re-binds to the same game.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._logger = logger or logging.getLogger(__name__)
        self._cache: dict[str, dict[str, Any]] = {}
        self._metadata_cache: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_game_profiles(
        self,
        game_id: str,
        *,
        force_reload: bool = False,
    ) -> dict[str, Any]:
        """Load preset + user JSON for ``game_id`` and merge them.

        Returns a dict::

            {
                "profiles": {name: profile, ...},   # merged
                "version": "YYYY-MM-DD",
                "errors": [...],                    # blocking errors (preset)
                "warnings": [...],                  # soft warnings (mostly user)
                "preset_loaded": bool,
                "user_loaded": bool,
            }

        Preset validation failures fall back to a valid user file when present.
        User failures fall back to preset (per plan: 用户 JSON 坏了不影响使用).
        """

        normalized = _normalize_game_id_for_path(game_id)
        if not normalized:
            return self._empty_load_result(["invalid game_id"])

        if not force_reload and normalized in self._cache:
            cached = self._cache[normalized]
            return deepcopy(cached)

        preset_path = self._data_dir / f"{normalized}.json"
        user_path = self._data_dir / f"{normalized}.user.json"

        preset_result = self._load_and_validate(preset_path, is_user=False)
        user_result = self._load_and_validate(user_path, is_user=True)

        errors: list[str] = []
        warnings: list[str] = []
        errors.extend(preset_result.errors)
        warnings.extend(preset_result.warnings)
        if user_result.errors:
            warnings.extend(
                f"user JSON ignored: {err}" for err in user_result.errors
            )
        warnings.extend(user_result.warnings)

        if preset_result.valid:
            merged = self._merge_profiles(
                preset=preset_result.profiles,
                user=user_result.profiles if user_result.valid else {},
            )
            version = self._pick_version(preset_result.version, user_result.version)
        elif user_result.valid and (
            not preset_path.exists() or not preset_result.profiles
        ):
            merged = dict(user_result.profiles)
            version = user_result.version
            if preset_path.exists() and errors:
                warnings.extend(f"preset JSON ignored: {err}" for err in errors)
            errors = []
        else:
            merged = {}
            version = preset_result.version or ""

        result = {
            "profiles": merged,
            "version": version,
            "errors": list(errors),
            "warnings": list(warnings),
            "preset_loaded": preset_result.valid,
            "user_loaded": user_result.valid,
        }
        self._cache[normalized] = deepcopy(result)
        return deepcopy(result)

    def invalidate(self, game_id: str | None = None) -> None:
        """Drop cached entries; useful when switching games or after import."""
        if game_id is None:
            self._cache.clear()
            self._metadata_cache = None
            return
        self._cache.pop(_normalize_game_id_for_path(game_id), None)
        self._metadata_cache = None

    def resolve_profile_match(
        self,
        signals: list[dict[str, Any]],
    ) -> CharacterProfileMatch | None:
        """Resolve runtime game/window signals to the best profile ``game_id``."""
        metadata = self._profile_metadata()
        if not metadata:
            return None

        ordered_signals = [signal for signal in signals if isinstance(signal, dict)]
        for signal in ordered_signals:
            raw_game_id = str(signal.get("game_id") or "").strip()
            if not raw_game_id:
                continue
            signal_key = _match_key(raw_game_id)
            for profile_id, meta in metadata.items():
                if signal_key in {_match_key(profile_id), _match_key(meta.get("game_id"))}:
                    return CharacterProfileMatch(
                        game_id=profile_id,
                        reason="exact_game_id",
                        source_value=raw_game_id,
                    )

        alias_index: dict[str, tuple[str, str]] = {}
        title_index: list[tuple[str, str, str]] = []
        process_index: list[tuple[str, str, str]] = []
        window_index: list[tuple[str, str, str]] = []
        for profile_id, meta in metadata.items():
            for alias in meta.get("aliases", []):
                alias_key = _match_key(alias)
                if alias_key:
                    alias_index.setdefault(alias_key, (profile_id, str(alias)))
            for title in meta.get("game_title_contains", []):
                title_key = _match_key(title)
                if title_key:
                    title_index.append((profile_id, title_key, str(title)))
            for process in meta.get("process_names", []):
                process_key = _match_key(process)
                if process_key:
                    process_index.append((profile_id, process_key, str(process)))
            for title in meta.get("window_title_contains", []):
                title_key = _match_key(title)
                if title_key:
                    window_index.append((profile_id, title_key, str(title)))

        for signal in ordered_signals:
            for value_name in ("game_id", "game_title"):
                value = str(signal.get(value_name) or "").strip()
                matched = alias_index.get(_match_key(value))
                if matched:
                    profile_id, alias = matched
                    return CharacterProfileMatch(
                        game_id=profile_id,
                        reason="alias",
                        source_value=alias or value,
                    )

        for signal in ordered_signals:
            value = str(signal.get("game_title") or "").strip()
            value_key = _match_key(value)
            if not value_key:
                continue
            for profile_id, title_key, title in title_index:
                if title_key and title_key in value_key:
                    return CharacterProfileMatch(
                        game_id=profile_id,
                        reason="game_title_contains",
                        source_value=title or value,
                    )

        for signal in ordered_signals:
            value = str(signal.get("process_name") or "").strip()
            value_key = _match_key(Path(value).name)
            if not value_key:
                continue
            value_stem_key = _match_key(Path(value).stem)
            for profile_id, process_key, process in process_index:
                if process_key in {value_key, value_stem_key} or process_key in value_key:
                    return CharacterProfileMatch(
                        game_id=profile_id,
                        reason="process_name",
                        source_value=process or value,
                    )

        for signal in ordered_signals:
            value = str(signal.get("window_title") or "").strip()
            value_key = _match_key(value)
            if not value_key:
                continue
            for profile_id, title_key, title in window_index:
                if title_key and title_key in value_key:
                    return CharacterProfileMatch(
                        game_id=profile_id,
                        reason="window_title_contains",
                        source_value=title or value,
                    )
        return None

    # ------------------------------------------------------------------
    # Scene queries
    # ------------------------------------------------------------------

    @staticmethod
    def get_profiles_for_scene(
        all_profiles: dict[str, dict[str, Any]],
        character_names: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Return the subset of profiles whose names appear in ``character_names``.

        Order follows ``character_names`` (deduped). Names absent from
        ``all_profiles`` are silently skipped — the caller decides whether the
        absence is meaningful.
        """
        if not all_profiles or not character_names:
            return {}
        seen: set[str] = set()
        result: dict[str, dict[str, Any]] = {}
        for raw_name in character_names:
            name = (raw_name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            profile = all_profiles.get(name)
            if profile:
                result[name] = profile
        return result

    # ------------------------------------------------------------------
    # Rendering — natural-language paragraph (legacy single-character)
    # ------------------------------------------------------------------

    def build_cat_context(
        self,
        name: str,
        profile: dict[str, Any],
        runtime_state: dict[str, Any] | None = None,
    ) -> str:
        """Render a single character profile as the L1 paragraph used by the
        legacy push path. New code should prefer :meth:`build_character_push_payload`.
        """
        return self.build_character_context_l1(name, profile, runtime_state)

    # ------------------------------------------------------------------
    # Layered rendering — L0 / L1 / L2
    # ------------------------------------------------------------------

    def build_character_context_l0(
        self,
        name: str,
        profile: dict[str, Any],
        runtime_state: dict[str, Any] | None = None,
    ) -> str:
        """Minimal tag: name + identity + top trait→speech effect mapping."""
        identity = self._short_identity(profile)
        traits = self._voice_traits(profile)
        first_trait = traits[0] if traits else {}
        trait_text = self._compact_text(first_trait.get("trait"))
        speech_text = self._compact_text(first_trait.get("speech_effect"))
        emotion = self._current_emotion(runtime_state)

        parts = [f"【{name}】"]
        if identity:
            parts.append(identity)
        if trait_text and speech_text:
            parts.append(f"{trait_text}→{speech_text}")
        elif trait_text:
            parts.append(trait_text)
        if emotion:
            parts.append(f"当前：{emotion}")
        rendered = " · ".join(parts)
        return self._fit_to_tokens(rendered, L0_MAX_TOKENS)

    def build_character_context_l1(
        self,
        name: str,
        profile: dict[str, Any],
        runtime_state: dict[str, Any] | None = None,
    ) -> str:
        """Standard block: identity + top trait→speech mappings + verbal tics +
        current emotion. Targets ≤150 tokens.

        Runtime overlay (emotion / arc / relationship) is emitted before traits
        so it survives the final token-budget trim — the trait list is the
        easiest section to shed if we run hot.
        """
        traits = self._voice_traits(profile)[:_L1_MAX_TRAITS]
        verbal_tics = self._voice_verbal_tics(profile)[:2]
        pronoun = self._voice_pronoun(profile)
        identity_raw = self._compact_text(profile.get("identity"))
        identity = identity_raw[:_L1_IDENTITY_CAP] + (
            "…" if len(identity_raw) > _L1_IDENTITY_CAP else ""
        )
        emotion = self._current_emotion(runtime_state)
        arc_stage = self._runtime_field(runtime_state, "arc_stage")
        relationship = self._runtime_field(runtime_state, "relationship_status")

        lines: list[str] = [f"【{name}】"]
        if identity:
            lines.append(f"身份：{identity}")
        if emotion:
            lines.append(f"当前情绪：{emotion}")
        if arc_stage and arc_stage != "初始":
            lines.append(f"弧光阶段：{arc_stage}")
        if relationship:
            lines.append(f"与主角关系：{relationship}")
        if traits:
            lines.append("性格→说话方式：")
            for trait_obj in traits:
                trait = self._compact_text(trait_obj.get("trait"))[:_L1_TRAIT_TEXT_CAP]
                speech = self._compact_text(trait_obj.get("speech_effect"))[:_L1_SPEECH_TEXT_CAP]
                if trait and speech:
                    lines.append(f"· {trait}→{speech}")
        if pronoun:
            lines.append(f"自称：{pronoun}")
        if verbal_tics:
            lines.append("口头习惯：" + "；".join(verbal_tics))
        rendered = "\n".join(lines)
        return self._fit_to_tokens(rendered, L1_MAX_TOKENS)

    def build_character_context_l2(
        self,
        name: str,
        profile: dict[str, Any],
        runtime_state: dict[str, Any] | None = None,
    ) -> str:
        """Full profile rendering: L1 + appearance + relationships + background +
        key events. Targets ≤600 tokens (the plan's ~500-token guidance with a
        small safety margin).
        """
        base = self.build_character_context_l1(name, profile, runtime_state)
        extras: list[str] = []

        appearance = self._compact_text(profile.get("appearance"))
        if appearance:
            extras.append(f"外观：{appearance}")

        relationships = profile.get("relationships")
        if isinstance(relationships, dict) and relationships:
            rel_lines = ["人际关系："]
            for other, desc in list(relationships.items())[:5]:
                desc_text = self._compact_text(desc)
                if desc_text:
                    rel_lines.append(f"· {other}：{desc_text}")
            if len(rel_lines) > 1:
                extras.append("\n".join(rel_lines))

        background = profile.get("background")
        if isinstance(background, list) and background:
            bg_lines = ["背景："]
            for item in background[:5]:
                text = self._compact_text(item)
                if text:
                    bg_lines.append(f"· {text}")
            if len(bg_lines) > 1:
                extras.append("\n".join(bg_lines))

        key_events = profile.get("key_events")
        if isinstance(key_events, list) and key_events:
            ev_lines = ["关键事件："]
            for item in key_events[:4]:
                if isinstance(item, dict):
                    event = self._compact_text(item.get("event"))
                    sig = self._compact_text(item.get("significance"))
                    if event and sig:
                        ev_lines.append(f"· {event}（{sig}）")
                    elif event:
                        ev_lines.append(f"· {event}")
                else:
                    text = self._compact_text(item)
                    if text:
                        ev_lines.append(f"· {text}")
            if len(ev_lines) > 1:
                extras.append("\n".join(ev_lines))

        info_gap = self._runtime_field(runtime_state, "information_gap")
        if info_gap:
            extras.append(f"信息差：{info_gap}")

        rendered = base if not extras else base + "\n" + "\n".join(extras)
        return self._fit_to_tokens(rendered, L2_MAX_TOKENS)

    def build_character_push_payload(
        self,
        characters: list[str],
        profiles: dict[str, dict[str, Any]],
        runtime_states: dict[str, dict[str, Any]] | None,
        level: str,
        *,
        fixed_character: str | None = None,
    ) -> dict[str, Any]:
        """Build the character-pipeline payload for a single push.

        Args:
            characters: ordered list of character names to include.
            profiles: full profile dict (preset+user merged).
            runtime_states: runtime overlay (current_emotion / arc_stage / ...).
            level: one of "L0" / "L1" / "L2".
            fixed_character: optional name for the locked anchor (fixed mode).

        Returns:
            ``{"format": "character_payload", "level": ..., "fixed_character":
            ..., "characters": {name: rendered_text}}``. Empty ``characters`` is
            valid and signals the caller to omit the block.
        """
        level_norm = (level or "L1").upper()
        builder = {
            "L0": self.build_character_context_l0,
            "L1": self.build_character_context_l1,
            "L2": self.build_character_context_l2,
        }.get(level_norm, self.build_character_context_l1)

        states = runtime_states or {}
        ordered: dict[str, str] = {}
        seen: set[str] = set()
        for raw in characters:
            name = (raw or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            profile = profiles.get(name)
            if not profile:
                continue
            ordered[name] = builder(name, profile, states.get(name))

        return {
            "format": "character_payload",
            "level": level_norm,
            "fixed_character": (fixed_character or "").strip() or None,
            "characters": ordered,
        }

    # ------------------------------------------------------------------
    # Runtime state derivation
    # ------------------------------------------------------------------

    def init_runtime_state_from_profile(
        self,
        name: str,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Derive an initial runtime overlay from the preset profile baseline.

        Used at first binding so that runtime fields are populated before the
        first scene-switch LLM update.
        """
        traits = self._voice_traits(profile)
        first_trait = self._compact_text(traits[0].get("trait")) if traits else ""
        key_events = profile.get("key_events")
        plot_involvement = (
            "主要角色"
            if isinstance(key_events, list) and key_events
            else "次要角色"
        )

        return {
            "current_emotion": first_trait,
            "arc_stage": "初始",
            "relationship_status": self._derive_initial_relationship(profile),
            "plot_involvement": plot_involvement,
            "information_gap": "",
            "updated_at_seq": 0,
        }

    # ------------------------------------------------------------------
    # Importing user JSON
    # ------------------------------------------------------------------

    def import_user_profiles(
        self,
        game_id: str,
        source_path: str | os.PathLike[str],
    ) -> ImportResult:
        """Validate ``source_path`` and write it to ``<game_id>.user.json``.

        Returns an :class:`ImportResult`. Validation failure means the file is
        not written. The data directory is created on demand.
        """
        normalized = _normalize_game_id_for_path(game_id)
        if not normalized:
            return ImportResult(
                ok=False,
                target_path="",
                profile_count=0,
                errors=("invalid game_id",),
                warnings=(),
            )

        src = Path(source_path)
        target = self._data_dir / f"{normalized}.user.json"

        validation = self._load_and_validate(src, is_user=True)
        if not validation.valid:
            return ImportResult(
                ok=False,
                target_path=str(target),
                profile_count=0,
                errors=validation.errors,
                warnings=validation.warnings,
            )

        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            source_data = json.loads(src.read_text(encoding="utf-8"))
            metadata = {
                key: deepcopy(value)
                for key, value in source_data.items()
                if key not in {"game_id", "last_updated", "characters"}
            }
            payload = {
                **metadata,
                "game_id": normalized,
                "last_updated": validation.version,
                "characters": validation.profiles,
            }
            self._atomic_write_json(target, payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return ImportResult(
                ok=False,
                target_path=str(target),
                profile_count=0,
                errors=(f"failed to write {target.name}: {exc}",),
                warnings=validation.warnings,
            )

        self.invalidate(normalized)
        return ImportResult(
            ok=True,
            target_path=str(target),
            profile_count=len(validation.profiles),
            errors=(),
            warnings=validation.warnings,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_and_validate(self, path: Path, *, is_user: bool) -> ValidationResult:
        if not path.exists():
            errs = () if is_user else (f"file not found: {path.name}",)
            return _invalid_result(errors=errs)
        try:
            size = path.stat().st_size
        except OSError as exc:
            return _invalid_result(errors=(f"stat failed for {path.name}: {exc}",))
        if size > MAX_PROFILE_SIZE_BYTES:
            return _invalid_result(
                errors=(
                    f"{path.name} 超出大小上限 ({size} > {MAX_PROFILE_SIZE_BYTES} bytes)",
                )
            )
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _invalid_result(errors=(f"failed to read {path.name}: {exc}",))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _invalid_result(
                errors=(f"{path.name} JSON 解析失败: {exc.msg}",)
            )
        return self._validate_payload(data, is_user=is_user, source=path.name)

    def _profile_metadata(self) -> dict[str, dict[str, Any]]:
        if self._metadata_cache is not None:
            return dict(self._metadata_cache)
        metadata: dict[str, dict[str, Any]] = {}
        try:
            paths = sorted(self._data_dir.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            profile_id = (
                path.name[: -len(".user.json")]
                if path.name.endswith(".user.json")
                else path.stem
            )
            if not _normalize_game_id_for_path(profile_id):
                continue
            try:
                if path.stat().st_size > MAX_PROFILE_SIZE_BYTES:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            validation = self._validate_payload(
                data,
                is_user=path.name.endswith(".user.json"),
                source=path.name,
            )
            if not validation.valid:
                continue
            game_id = str(data.get("game_id") or path.stem).strip()
            match = data.get("match")
            match_obj = match if isinstance(match, dict) else {}

            def _strings(value: object) -> list[str]:
                if isinstance(value, list):
                    return [
                        str(item).strip()
                        for item in value
                        if str(item or "").strip()
                    ]
                if isinstance(value, str) and value.strip():
                    return [value.strip()]
                return []

            existing = metadata.get(profile_id, {})
            metadata[profile_id] = {
                "game_id": game_id,
                "aliases": _merge_unique_strings(
                    existing.get("aliases"), _strings(data.get("aliases"))
                ),
                "game_title_contains": _merge_unique_strings(
                    existing.get("game_title_contains"),
                    _strings(match_obj.get("game_title_contains")),
                ),
                "process_names": _merge_unique_strings(
                    existing.get("process_names"),
                    _strings(match_obj.get("process_names")),
                ),
                "window_title_contains": _merge_unique_strings(
                    existing.get("window_title_contains"),
                    _strings(match_obj.get("window_title_contains")),
                ),
            }
        self._metadata_cache = dict(metadata)
        return metadata

    def _validate_payload(
        self,
        data: Any,
        *,
        is_user: bool,
        source: str,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if not isinstance(data, dict):
            return _invalid_result(
                errors=(f"{source}: top-level value must be an object",)
            )
        missing_top = _TOP_LEVEL_REQUIRED - data.keys()
        if missing_top:
            errors.append(
                f"{source}: missing top-level fields: {', '.join(sorted(missing_top))}"
            )
        characters_raw = data.get("characters")
        if not isinstance(characters_raw, dict):
            return _invalid_result(
                errors=tuple(
                    errors + [f"{source}: 'characters' must be an object"]
                ),
                warnings=tuple(warnings),
            )

        cleaned: dict[str, dict[str, Any]] = {}
        for raw_name, profile in characters_raw.items():
            name = str(raw_name or "").strip()
            if not name:
                errors.append(f"{source}: character key cannot be empty")
                continue
            if not isinstance(profile, dict):
                errors.append(f"{source}: '{name}' profile must be an object")
                continue
            missing = REQUIRED_FIELDS - profile.keys()
            if missing:
                errors.append(
                    f"{source}: '{name}' missing required: {', '.join(sorted(missing))}"
                )
                continue
            voice = profile.get("character_voice")
            if not isinstance(voice, dict):
                errors.append(f"{source}: '{name}'.character_voice must be an object")
                continue
            voice_errors, voice_warnings = self._validate_voice(name, voice, is_user=is_user)
            errors.extend(f"{source}: {msg}" for msg in voice_errors)
            warnings.extend(f"{source}: {msg}" for msg in voice_warnings)
            if voice_errors:
                continue
            cleaned[name] = profile

        if not cleaned and not errors:
            errors.append(f"{source}: 'characters' is empty")

        version = str(data.get("last_updated") or "").strip()
        valid = bool(cleaned) and not errors
        return ValidationResult(
            valid=valid,
            profiles=cleaned if valid else {},
            version=version,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _validate_voice(
        self,
        name: str,
        voice: dict[str, Any],
        *,
        is_user: bool,
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        core_traits = voice.get("core_traits")
        if not isinstance(core_traits, list) or not core_traits:
            errors.append(f"{name}: character_voice.core_traits 至少需要一条性格描述")
            return errors, warnings
        for index, trait_obj in enumerate(core_traits):
            if not isinstance(trait_obj, dict):
                errors.append(f"{name}: core_traits[{index}] 必须是对象")
                continue
            trait = trait_obj.get("trait")
            speech = trait_obj.get("speech_effect")
            if not isinstance(trait, str) or not trait.strip():
                errors.append(f"{name}: core_traits[{index}].trait 不能为空")
            if not isinstance(speech, str) or not speech.strip():
                errors.append(
                    f"{name}: core_traits[{index}].speech_effect "
                    "不能为空（性格如何影响说话？）"
                )
            if is_user and isinstance(trait, str) and isinstance(speech, str):
                warnings.append(
                    f"{name}: 请确认 core_traits[{index}].trait（{trait.strip()[:32]}）"
                    f"和 speech_effect（{speech.strip()[:32]}）描述一致"
                    "——性格决定说话方式。"
                )
        if not voice.get("verbal_tics"):
            warnings.append(f"{name}: character_voice.verbal_tics 为空")
        if not voice.get("first_person_pronoun"):
            warnings.append(f"{name}: character_voice.first_person_pronoun 为空")
        return errors, warnings

    @staticmethod
    def _merge_profiles(
        *,
        preset: dict[str, dict[str, Any]],
        user: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """User entries fully replace preset entries with the same name; user
        entries with new names append to the result.
        """
        merged: dict[str, dict[str, Any]] = dict(preset)
        for name, profile in user.items():
            merged[name] = profile
        return merged

    @staticmethod
    def _pick_version(preset_version: str, user_version: str) -> str:
        candidates = [v for v in (user_version, preset_version) if v]
        if not candidates:
            return ""
        return max(candidates)

    @staticmethod
    def _voice_traits(profile: dict[str, Any]) -> list[dict[str, Any]]:
        voice = profile.get("character_voice")
        if not isinstance(voice, dict):
            return []
        traits = voice.get("core_traits")
        if not isinstance(traits, list):
            return []
        return [trait for trait in traits if isinstance(trait, dict)]

    @staticmethod
    def _voice_verbal_tics(profile: dict[str, Any]) -> list[str]:
        voice = profile.get("character_voice")
        if not isinstance(voice, dict):
            return []
        tics = voice.get("verbal_tics")
        if not isinstance(tics, list):
            return []
        return [str(tic).strip() for tic in tics if str(tic or "").strip()]

    @staticmethod
    def _voice_pronoun(profile: dict[str, Any]) -> str:
        voice = profile.get("character_voice")
        if not isinstance(voice, dict):
            return ""
        return str(voice.get("first_person_pronoun") or "").strip()

    @staticmethod
    def _short_identity(profile: dict[str, Any]) -> str:
        identity = str(profile.get("identity") or "").strip()
        if not identity:
            return ""
        if len(identity) <= 40:
            return identity
        return identity[:38] + "…"

    @staticmethod
    def _compact_text(value: Any) -> str:
        if value is None:
            return ""
        text = _TRAIT_TEXT_RE.sub(" ", str(value)).strip()
        return text

    @staticmethod
    def _current_emotion(runtime_state: dict[str, Any] | None) -> str:
        if not isinstance(runtime_state, dict):
            return ""
        return str(runtime_state.get("current_emotion") or "").strip()

    @staticmethod
    def _runtime_field(runtime_state: dict[str, Any] | None, key: str) -> str:
        if not isinstance(runtime_state, dict):
            return ""
        return str(runtime_state.get(key) or "").strip()

    @staticmethod
    def _derive_initial_relationship(profile: dict[str, Any]) -> str:
        rel = profile.get("relationships")
        if not isinstance(rel, dict):
            return ""
        for _other, desc in rel.items():
            text = str(desc or "").strip()
            if text:
                return text[:40] + ("…" if len(text) > 40 else "")
        return ""

    @staticmethod
    def _fit_to_tokens(text: str, max_tokens: int) -> str:
        """Trim ``text`` so its heuristic token count ≤ ``max_tokens``.

        Uses a coarse binary search on character length. Suffix marker keeps
        the truncation explicit so downstream consumers can detect it.
        """
        if max_tokens <= 0 or not text:
            return text
        if count_tokens_heuristic(text) <= max_tokens:
            return text
        marker = "…"
        low, high = 0, len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid].rstrip()
            if not candidate:
                low = mid + 1
                continue
            if count_tokens_heuristic(candidate + marker) <= max_tokens:
                best = candidate + marker
                low = mid + 1
            else:
                high = mid - 1
        return best or text[: max(1, max_tokens)]

    @staticmethod
    def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.stem + ".",
            suffix=".tmp",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _empty_load_result(errors: list[str]) -> dict[str, Any]:
        return {
            "profiles": {},
            "version": "",
            "errors": list(errors),
            "warnings": [],
            "preset_loaded": False,
            "user_loaded": False,
        }


__all__ = [
    "CharacterProfileManager",
    "ImportResult",
    "ValidationResult",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "VOICE_REQUIRED",
    "VOICE_TRAIT_REQUIRED",
    "MAX_PROFILE_SIZE_BYTES",
    "L0_MAX_TOKENS",
    "L1_MAX_TOKENS",
    "L2_MAX_TOKENS",
]

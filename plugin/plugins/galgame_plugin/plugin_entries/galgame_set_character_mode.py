from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetCharacterModeMixin:
    @plugin_entry(
        id="galgame_set_character_mode",
        name=tr(
            "entries.galgame_set_character_mode.name",
            default="设置角色档案模式",
        ),
        description=tr(
            "entries.galgame_set_character_mode.description",
            default="切换角色档案模式：off 或 fixed（锁定某角色作为猫娘视角）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["off", "fixed"]},
                "character_name": {"type": "string", "default": ""},
            },
            "required": ["mode"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_character_mode(
        self,
        mode: str,
        character_name: str = "",
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        mode_normalized = (mode or "").strip().lower()
        if mode_normalized not in {"off", "fixed"}:
            return Err(SdkError(f"invalid character mode: {mode!r}"))

        if mode_normalized == "fixed":
            name = (character_name or "").strip()
            if not name:
                return Err(SdkError("fixed mode requires character_name"))
            load = self._load_character_profiles_for_current_context(force=True)
            loaded_profiles = (
                {}
                if bool(load.get("pending_match"))
                else dict(load.get("profiles") or {})
            )
            if not loaded_profiles:
                errors = load.get("errors") or []
                resolved_game_id = str(load.get("resolved_game_id") or "")
                return Err(
                    SdkError(
                        "no character profiles available"
                        + (f" for {resolved_game_id}" if resolved_game_id else ""),
                        details={"errors": list(errors)},
                    )
                )
            if name not in loaded_profiles:
                return Err(SdkError(f"character {name!r} not found in profiles"))

        with self._state_lock:
            self._state.character_mode = mode_normalized
            self._state.character_fixed_name = (
                (character_name or "").strip()
                if mode_normalized == "fixed"
                else ""
            )
            self._state.character_mode_stale = False
            self._state_dirty = True
            self._cached_snapshot = None
            fixed_name = str(self._state.character_fixed_name or "")

        try:
            self._persist.persist_config_override(
                STORE_CHARACTER_MODE, mode_normalized
            )
            self._persist.persist_config_override(
                STORE_CHARACTER_FIXED_NAME, fixed_name
            )
        except Exception:  # noqa: BLE001
            self.logger.warning(
                "failed to persist character mode switch", exc_info=True
            )

        return Ok(
            {
                "mode": mode_normalized,
                "character_name": fixed_name,
                "summary": (
                    f"character_mode={mode_normalized} character={fixed_name or '-'}"
                ),
            }
        )

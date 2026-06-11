from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetCharacterProfileMixin:
    @plugin_entry(
        id="galgame_get_character_profile",
        name=tr(
            "entries.galgame_get_character_profile.name",
            default="查询角色完整档案",
        ),
        description=tr(
            "entries.galgame_get_character_profile.description",
            default="返回指定角色的预置档案 + 运行时状态。off 模式下拒绝。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        timeout=10.0,
        llm_result_fields=["summary"],
    )
    async def galgame_get_character_profile(self, name: str, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        with self._state_lock:
            mode = str(self._state.character_mode or "off")
            profiles = dict(self._state.character_profiles or {})
            runtime = dict(self._state.character_runtime_state or {})
        if mode == "off":
            return Ok(
                {
                    "disabled": True,
                    "reason": (
                        "角色档案功能未开启，请先调用 galgame_set_character_mode"
                        " 切换为 fixed"
                    ),
                    "summary": "character_mode=off",
                }
            )
        target = (name or "").strip()
        if not target:
            return Err(SdkError("character name required"))
        if not profiles:
            load = self._load_character_profiles_for_current_context()
            profiles = dict(load.get("profiles") or {})
            with self._state_lock:
                runtime = dict(self._state.character_runtime_state or {})
        profile = profiles.get(target)
        if profile is None:
            return Err(SdkError(f"character {target!r} not found"))
        return Ok(
            {
                "name": target,
                "profile": profile,
                "runtime_state": runtime.get(target, {}),
                "summary": f"profile for {target}",
            }
        )

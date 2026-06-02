from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetCharacterListMixin:
    @plugin_entry(
        id="galgame_get_character_list",
        name=tr(
            "entries.galgame_get_character_list.name",
            default="查询当前游戏角色列表",
        ),
        description=tr(
            "entries.galgame_get_character_list.description",
            default="返回当前绑定游戏的可用角色名称和一句话身份描述。",
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=10.0,
        llm_result_fields=["summary"],
    )
    async def galgame_get_character_list(self, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        with self._state_lock:
            bound_game_id = str(self._state.bound_game_id or "")
            profile_game_id = str(self._state.character_profile_game_id or "")
            match_reason = str(self._state.character_profile_match_reason or "")
            profiles = dict(self._state.character_profiles or {})
        load = self._load_character_profiles_for_current_context(force=True)
        loaded_profiles = dict(load.get("profiles") or {})
        if loaded_profiles or not profiles:
            profiles = loaded_profiles
            profile_game_id = str(load.get("resolved_game_id") or "")
            match_reason = str(load.get("match_reason") or "")
        items = [
            {
                "name": name,
                "identity": str((profile or {}).get("identity") or ""),
            }
            for name, profile in profiles.items()
        ]
        return Ok(
            {
                "game_id": profile_game_id or bound_game_id,
                "profile_game_id": profile_game_id,
                "match_reason": match_reason,
                "characters": items,
                "summary": f"{len(items)} character(s) available",
            }
        )

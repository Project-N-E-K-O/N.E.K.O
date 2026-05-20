from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameBindGameMixin:
    @plugin_entry(
        id="galgame_bind_game",
        name=tr("entries.galgame_bind_game.name", default='绑定 galgame 游戏'),
        description=tr("entries.galgame_bind_game.description", default='绑定指定 game_id；传空字符串清除手动绑定并恢复自动选择。'),
        input_schema={
            "type": "object",
            "properties": {"game_id": {"type": "string", "default": ""}},
            "required": ["game_id"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_bind_game(self, game_id: str, **_):
        normalized = game_id.strip()
        with self._state_lock:
            available_game_ids = list(self._state.available_game_ids)
        if normalized and normalized not in available_game_ids:
            return Err(SdkError(f"unknown game_id: {normalized!r}"))

        with self._state_lock:
            mode = self._state.mode
            push_notifications = self._state.push_notifications
            advance_speed = self._state.advance_speed

        try:
            self._config_service.persist_preferences(
                bound_game_id=normalized,
                mode=mode,
                push_notifications=push_notifications,
                advance_speed=advance_speed,
            )
        except Exception as exc:
            return Err(SdkError(f"persist binding failed: {exc}"))

        with self._state_lock:
            self._state.bound_game_id = normalized
            self._state_dirty = True
            self._cached_snapshot = None
        self._clear_character_profiles()
        self._load_character_profiles_for_current_context(force=True)
        await self._poll_bridge(force=True)
        with self._state_lock:
            payload = {
                "bound_game_id": self._state.bound_game_id,
                "active_session_id": self._state.active_session_id,
                "summary": f"bound_game_id={self._state.bound_game_id or '(auto)'} active_session_id={self._state.active_session_id}",
            }
        return Ok(payload)

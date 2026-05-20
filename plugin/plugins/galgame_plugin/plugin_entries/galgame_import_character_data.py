from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameImportCharacterDataMixin:
    @plugin_entry(
        id="galgame_import_character_data",
        name=tr(
            "entries.galgame_import_character_data.name",
            default="导入角色档案 JSON",
        ),
        description=tr(
            "entries.galgame_import_character_data.description",
            default="从 JSON 文件导入用户自定义角色档案。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "game_id": {"type": "string", "default": ""},
            },
            "required": ["file_path"],
        },
        timeout=15.0,
        llm_result_fields=["summary"],
    )
    async def galgame_import_character_data(
        self,
        file_path: str,
        game_id: str = "",
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        target_game = (game_id or "").strip()
        if not target_game:
            with self._state_lock:
                target_game = str(self._state.bound_game_id or "")
        if not target_game:
            return Err(
                SdkError("game_id required (no game currently bound)")
            )
        source = (file_path or "").strip()
        if not source:
            return Err(SdkError("file_path required"))
        manager = self._get_character_profile_manager()
        try:
            result = await asyncio.to_thread(
                manager.import_user_profiles,
                target_game,
                source,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "galgame character data import failed",
                exc_info=True,
            )
            return Err(
                SdkError(
                    f"import failed: {exc}",
                    details={
                        "target_path": "",
                        "errors": [str(exc)],
                        "warnings": [],
                    },
                )
            )
        if not result.ok:
            return Err(
                SdkError(
                    f"import failed: {'; '.join(result.errors)}",
                    details={
                        "target_path": result.target_path,
                        "errors": list(result.errors),
                        "warnings": list(result.warnings),
                    },
                )
            )
        # Reload merged profiles only when the import targets the active binding.
        with self._state_lock:
            active_game = str(self._state.bound_game_id or self._state.active_game_id or "")
        if target_game == active_game:
            self._activate_character_profiles(target_game)
        return Ok(
            {
                "ok": True,
                "game_id": target_game,
                "target_path": result.target_path,
                "profile_count": result.profile_count,
                "warnings": list(result.warnings),
                "summary": (
                    f"imported {result.profile_count} profile(s) for {target_game}"
                ),
            }
        )

"""N.E.K.O native data backup plugin."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)
from plugin.sdk.shared.core.base_runtime import resolve_runtime_data_root

if __package__:
    from .backup import BACKUP_GROUPS, BackupEngine, BackupError
    from .schedule import ScheduleState
else:  # Standalone repository tests import this file as top-level ``__init__``.
    from backup import BACKUP_GROUPS, BackupEngine, BackupError
    from schedule import ScheduleState


@neko_plugin
class DataBackupPlugin(NekoPluginBase):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._engine: BackupEngine | None = None
        self._operation_lock = asyncio.Lock()
        self._schedule = ScheduleState()
        self._schedule_lock = threading.RLock()
        self._schedule_running = False
        self._schedule_revision = 0

    @lifecycle(id="startup")
    async def startup(self, **_):
        plugin_data = self.data_path().resolve(strict=False)
        data_root = resolve_runtime_data_root()
        default_backup_root = plugin_data / "snapshots"
        try:
            configured = await self.config.get_str(
                "backup.directory", default="", timeout=5.0
            )
            try:
                backup_root = self._resolve_backup_root(configured, default_backup_root)
                self._engine = BackupEngine(data_root, backup_root)
            except (BackupError, OSError, ValueError):
                self._engine = BackupEngine(data_root, default_backup_root)
            raw_schedule = await self.config.get(
                "backup.schedule", default={}, timeout=5.0
            )
            schedule = ScheduleState.from_config(raw_schedule)
            with self._schedule_lock:
                self._schedule = schedule
            if schedule.enabled and schedule.to_dict() != raw_schedule:
                await self.config.set(
                    "backup.schedule", schedule.to_dict(), timeout=5.0
                )
            self.register_static_ui("static", cache_control="no-store")
            self.set_list_actions(
                [
                    {
                        "id": "open_ui",
                        "label": "打开备份管理",
                        "kind": "ui",
                        "target": f"/plugin/{self.plugin_id}/ui/",
                        "open_in": "new_tab",
                    }
                ]
            )
            return Ok(self._status())
        except (BackupError, OSError, ValueError) as exc:
            self._engine = None
            return Err(SdkError(str(exc)))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        async with self._operation_lock:
            self._engine = None
        return Ok({"status": "stopped"})

    def _backup(self) -> BackupEngine:
        if self._engine is None:
            raise BackupError("backup plugin is not started")
        return self._engine

    def _status(self) -> dict:
        status = self._backup().status()
        status["default_backup_root"] = str(
            self.data_path("snapshots").resolve(strict=False)
        )
        with self._schedule_lock:
            status["schedule"] = self._schedule.to_dict(running=self._schedule_running)
        return status

    @staticmethod
    def _resolve_backup_root(value: str | None, default: Path) -> Path:
        raw = str(value or "").strip()
        if not raw:
            return default
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise BackupError("backup directory must be an absolute path")
        return path

    @plugin_entry(
        id="backup_status",
        name="查看备份状态",
        description="返回固定备份组与已有快照。",
        timeout=30.0,
    )
    async def backup_status(self, **_):
        try:
            async with self._operation_lock:
                return Ok(await asyncio.to_thread(self._status))
        except (BackupError, OSError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="backup_set_directory",
        name="更改备份目录",
        description="设置绝对备份目录；传入空字符串恢复默认目录。",
        input_schema={
            "type": "object",
            "properties": {"directory": {"type": "string"}},
            "required": ["directory"],
            "additionalProperties": False,
        },
        timeout=30.0,
    )
    async def backup_set_directory(self, directory: str, **_):
        default = self.data_path("snapshots").resolve(strict=False)
        try:
            async with self._operation_lock:
                backup_root = self._resolve_backup_root(directory, default)
                engine = await asyncio.to_thread(
                    BackupEngine, self._backup().data_root, backup_root
                )
                await self.config.set(
                    "backup.directory",
                    "" if not directory.strip() else str(engine.backup_root),
                    timeout=5.0,
                )
                self._engine = engine
                return Ok(self._status())
        except (BackupError, OSError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="backup_create",
        name="创建数据快照",
        description="为 core 或 assets 固定数据组创建快照。",
        input_schema={
            "type": "object",
            "properties": {"group": {"type": "string", "enum": list(BACKUP_GROUPS)}},
            "required": ["group"],
            "additionalProperties": False,
        },
        timeout=600.0,
    )
    async def backup_create(self, group: str, **_):
        try:
            async with self._operation_lock:
                return Ok(
                    await asyncio.to_thread(self._backup().create_snapshot, group)
                )
        except (BackupError, OSError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="backup_set_schedule",
        name="设置定时快照",
        description="启用或关闭定时快照，并设置执行周期与备份组。",
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "interval_days": {"type": "integer", "minimum": 1, "maximum": 365},
                "groups": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(BACKUP_GROUPS)},
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
            "required": ["enabled", "interval_days", "groups"],
            "additionalProperties": False,
        },
        timeout=30.0,
    )
    async def backup_set_schedule(
        self, enabled: bool, interval_days: int, groups: list[str], **_
    ):
        try:
            with self._schedule_lock:
                schedule = self._schedule.reconfigured(
                    enabled=enabled,
                    interval_days=interval_days,
                    groups=groups,
                )
            await self.config.set("backup.schedule", schedule.to_dict(), timeout=5.0)
            with self._schedule_lock:
                self._schedule = schedule
                self._schedule_revision += 1
            return Ok(self._status())
        except (BackupError, OSError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @timer_interval(
        id="scheduled_backup",
        seconds=3600,
        name="检查定时快照",
        description="每小时检查用户配置的定时快照计划。",
        auto_start=True,
    )
    async def scheduled_backup(self, **_):
        with self._schedule_lock:
            schedule = self._schedule
            if self._schedule_running or not schedule.is_due():
                return Ok({"created": {}, "schedule": schedule.to_dict()})
            self._schedule_running = True
            revision = self._schedule_revision

        created: dict[str, str] = {}
        warnings: list[str] = []
        error: str | None = None
        try:
            async with self._operation_lock:
                engine = self._backup()
                for group in schedule.groups:
                    snapshot = await asyncio.to_thread(engine.create_snapshot, group)
                    created[group] = snapshot["id"]
                    warnings.extend(snapshot.get("warnings", ()))
        except Exception as exc:
            error = str(exc)
            updated = schedule.failed(error)
        else:
            updated = schedule.succeeded(warning="; ".join(warnings) or None)

        with self._schedule_lock:
            self._schedule_running = False
            if revision != self._schedule_revision:
                return Ok(
                    {
                        "created": created,
                        "warnings": warnings,
                        "settings_changed": True,
                    }
                )
            self._schedule = updated
        try:
            await self.config.set("backup.schedule", updated.to_dict(), timeout=5.0)
        except Exception as exc:
            return Err(SdkError(f"failed to save schedule state: {exc}"))
        if error:
            return Err(SdkError(error))
        return Ok(
            {"created": created, "warnings": warnings, "schedule": updated.to_dict()}
        )

    @plugin_entry(
        id="backup_restore",
        name="恢复数据快照",
        description="仅在确认值与快照 ID 完全一致时恢复，并先创建安全快照。",
        input_schema={
            "type": "object",
            "properties": {
                "group": {"type": "string", "enum": list(BACKUP_GROUPS)},
                "snapshot_id": {"type": "string"},
                "confirmation": {"type": "string"},
            },
            "required": ["group", "snapshot_id", "confirmation"],
            "additionalProperties": False,
        },
        timeout=900.0,
    )
    async def backup_restore(
        self, group: str, snapshot_id: str, confirmation: str, **_
    ):
        if confirmation != snapshot_id:
            return Err(SdkError("confirmation must match the snapshot id"))
        try:
            async with self._operation_lock:
                result = await asyncio.to_thread(
                    self._backup().restore_snapshot, group, snapshot_id
                )
                return Ok(result)
        except (BackupError, OSError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="backup_delete",
        name="删除数据快照",
        description="仅在确认值与快照 ID 完全一致时删除快照。",
        input_schema={
            "type": "object",
            "properties": {
                "group": {"type": "string", "enum": list(BACKUP_GROUPS)},
                "snapshot_id": {"type": "string"},
                "confirmation": {"type": "string"},
            },
            "required": ["group", "snapshot_id", "confirmation"],
            "additionalProperties": False,
        },
        timeout=300.0,
    )
    async def backup_delete(self, group: str, snapshot_id: str, confirmation: str, **_):
        if confirmation != snapshot_id:
            return Err(SdkError("confirmation must match the snapshot id"))
        try:
            async with self._operation_lock:
                return Ok(
                    await asyncio.to_thread(
                        self._backup().delete_snapshot, group, snapshot_id
                    )
                )
        except (BackupError, OSError) as exc:
            return Err(SdkError(str(exc)))


__all__ = ["DataBackupPlugin"]

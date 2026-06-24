"""Neko Roast runtime assembly."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any

from ..adapters.bili_auth_service import BiliAuthService
from ..adapters.neko_dispatcher import NekoDispatcher
from ..modules._base import ReservedModule
from ..modules.avatar_roast import AvatarRoastModule
from ..modules.bili_identity import BiliIdentityModule
from ..modules.bili_live_ingest import BiliLiveIngestModule
from ..modules.developer_sandbox import DeveloperSandboxModule
from ..modules.live_events import LiveEventsModule
from ..modules.viewer_profile import ViewerProfileModule
from ..stores.audit_store import AuditStore
from ..stores.avatar_cache import AvatarCache
from ..stores.credential_store import CredentialStore
from ..stores.viewer_store import ViewerStore
from .contracts import InteractionResult, RoastConfig, ViewerEvent, ViewerProfile, parse_room_id
from .event_bus import EventBus
from .instructions import (
    NEKO_ROAST_CONTEXT_INSTRUCTIONS,
    NEKO_ROAST_DEVELOPER_ANNOUNCEMENT,
    NEKO_ROAST_DEVELOPER_INSTRUCTIONS,
    NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS,
    NEKO_ROAST_RESTORE_INSTRUCTIONS,
)
from .module_registry import ModuleRegistry
from .permission_gate import PermissionGate
from .pipeline import RoastPipeline
from .safety_guard import SafetyGuard


class RoastRuntime:
    # host 配置持久化预算（秒）：超过即放弃等待（配置已内存生效），避免被 host 的写竞争
    # （update_own_config 偶发卡满 10s）拖垮 update_config / connect 等 action。
    _CONFIG_PERSIST_BUDGET_SECONDS = 4.0

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.config = RoastConfig()
        self.audit = AuditStore(limit=100)
        self.avatar_cache = AvatarCache()
        self.viewer_store = ViewerStore(plugin, self.audit, lambda: self.config.viewer_store_dir)
        self.permission_gate = PermissionGate(self.config)
        self.safety_guard = SafetyGuard(self.config, self.audit)
        self.dispatcher = NekoDispatcher(plugin)
        self.event_bus = EventBus(self.audit)  # 传 audit：handler 失败按 owner 归属记账
        # P5 登录态：加密凭据 store + 扫码登录服务（注入 store 的加载/保存/重载回调）。
        self.credential_store = CredentialStore(plugin, self.audit)
        self.bili_credential: Any = None  # 缓存的 bilibili_api.Credential（已登录则非 None）
        self.bili_auth = BiliAuthService(
            logger=getattr(plugin, "logger", None),
            credential_provider=self.credential_store.build_credential,
            credential_saver=self.credential_store.save,
            credential_reloader=self.reload_credential,
        )
        self.registry = ModuleRegistry()
        self.recent_results: deque[dict[str, Any]] = deque(maxlen=self.config.recent_limit)
        self.recent_sandbox_results: deque[dict[str, Any]] = deque(maxlen=self.config.recent_limit)
        self.live_connection_state = "disconnected"
        self.instructions_injected = False
        self.developer_instructions_injected = False
        # 串行化插件自身的配置写，避免并发 update_config 内存 apply 互踩 / 叠加持久化。
        # 懒初始化，避免构造时无运行 loop。
        self._config_lock: asyncio.Lock | None = None

        self.bili_live_ingest = BiliLiveIngestModule()
        self.bili_identity = BiliIdentityModule()
        self.viewer_profile = ViewerProfileModule()
        self.avatar_roast = AvatarRoastModule()
        self.developer_sandbox = DeveloperSandboxModule()
        self.live_events = LiveEventsModule()
        self.pipeline = RoastPipeline(self)
        self.plugin_dir = Path(__file__).resolve().parents[1]

        for module in (
            self.bili_live_ingest,
            self.bili_identity,
            self.viewer_profile,
            self.avatar_roast,
            self.developer_sandbox,
            self.live_events,
            ReservedModule("bili_dm_ingest", "B站私信输入"),
            ReservedModule("contribution_rank", "贡献值"),
            ReservedModule("watch_time", "观看时长"),
            ReservedModule("bili_read_tools", "B站读取工具"),
            ReservedModule("bili_write_tools", "B站写入工具"),
            ReservedModule("automation_ops", "自动化操作"),
        ):
            self.registry.register(module)

    async def start(self) -> None:
        await self.reload_config()
        await self.reload_credential()  # 载入此前已加密保存的 B站 登录凭据（若有）
        await self.registry.setup_all(self)
        self.audit.record("runtime_start", "neko_roast runtime ready")

    async def reload_credential(self) -> None:
        """从 store 重建缓存的 B站 Credential；无凭据 / 失败则置 None（不抛）。"""
        try:
            self.bili_credential = await self.credential_store.build_credential()
        except Exception:
            self.bili_credential = None

    async def bili_login(self) -> dict[str, Any]:
        """生成扫码登录二维码（或回报已登录）。"""
        return await self.bili_auth.login()

    async def bili_login_check(self) -> dict[str, Any]:
        """轮询扫码状态；DONE 时加密保存凭据并热重载。"""
        return await self.bili_auth.login_check()

    async def bili_login_status(self) -> dict[str, Any]:
        """检查当前登录态（无凭据时不调 SDK，直接 logged_in=False）。"""
        return await self.bili_auth.check_credential()

    async def bili_logout(self) -> dict[str, Any]:
        """本地注销：删除加密凭据 + 密钥文件，清空缓存（不吊销服务端 token）。"""
        removed = await self.credential_store.delete()
        self.bili_credential = None
        self.audit.record("bili_logout", "logged out (local credential removed)", detail={"files": removed})
        return {"logged_out": True, "removed": removed, "logged_in": False}

    async def stop(self) -> None:
        await self.restore_developer_instructions()
        await self.restore_instructions()
        await self.registry.teardown_all()
        self.audit.record("runtime_stop", "neko_roast runtime stopped")

    async def inject_instructions(self, *, force: bool = False) -> str:
        if self.instructions_injected and not force:
            return "already_injected"
        try:
            output = await self.dispatcher.push_context_instructions(NEKO_ROAST_CONTEXT_INSTRUCTIONS)
        except Exception as exc:
            self.instructions_injected = False
            message = str(exc).strip() or f"instruction_inject_failed: {type(exc).__name__}"
            self.audit.record("instructions_inject_failed", message, level="warning")
            return message
        self.instructions_injected = True
        self.audit.record("instructions_injected", output, detail={"source": "neko_roast"})
        return output

    async def sync_developer_mode(self, *, announce: bool = False) -> str:
        if self.config.developer_tools_enabled:
            result = await self.inject_developer_instructions()
            if announce:
                announcement = await self.announce_developer_mode()
                return f"{result}; {announcement}"
            return result
        return await self.restore_developer_instructions()

    async def inject_developer_instructions(self, *, force: bool = False) -> str:
        if self.developer_instructions_injected and not force:
            return "developer_already_injected"
        try:
            output = await self.dispatcher.push_developer_instructions(NEKO_ROAST_DEVELOPER_INSTRUCTIONS)
        except Exception as exc:
            self.developer_instructions_injected = False
            message = str(exc).strip() or f"developer_instruction_inject_failed: {type(exc).__name__}"
            self.audit.record("developer_instructions_inject_failed", message, level="warning")
            return message
        self.developer_instructions_injected = True
        self.audit.record("developer_instructions_injected", output, detail={"source": "neko_roast"})
        return output

    async def restore_developer_instructions(self) -> str:
        if not self.developer_instructions_injected:
            return "developer_not_injected"
        try:
            output = await self.dispatcher.push_developer_restore(NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS)
        except Exception as exc:
            message = str(exc).strip() or f"developer_instruction_restore_failed: {type(exc).__name__}"
            self.audit.record("developer_instructions_restore_failed", message, level="warning")
            return message
        self.developer_instructions_injected = False
        self.audit.record("developer_instructions_restored", output, detail={"source": "neko_roast"})
        return output

    async def announce_developer_mode(self) -> str:
        try:
            output = await self.dispatcher.push_developer_announcement(NEKO_ROAST_DEVELOPER_ANNOUNCEMENT)
        except Exception as exc:
            message = str(exc).strip() or f"developer_mode_announce_failed: {type(exc).__name__}"
            self.audit.record("developer_mode_announce_failed", message, level="warning")
            return message
        self.audit.record("developer_mode_announced", output, detail={"source": "neko_roast"})
        return output

    async def restore_instructions(self) -> str:
        if not self.instructions_injected:
            return "not_injected"
        try:
            output = await self.dispatcher.push_context_restore(NEKO_ROAST_RESTORE_INSTRUCTIONS)
        except Exception as exc:
            message = str(exc).strip() or f"instruction_restore_failed: {type(exc).__name__}"
            self.audit.record("instructions_restore_failed", message, level="warning")
            return message
        self.instructions_injected = False
        self.audit.record("instructions_restored", output, detail={"source": "neko_roast"})
        return output

    async def reload_config(self) -> RoastConfig:
        data: dict[str, Any] = {}
        try:
            dumped = await self.plugin.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                data = dumped.get("neko_roast", {}) if isinstance(dumped.get("neko_roast"), dict) else {}
        except Exception as exc:
            self.audit.record("config_load_failed", f"config load failed: {type(exc).__name__}", level="warning")
        return self._activate_config(RoastConfig.from_mapping(data))

    def _activate_config(self, config: RoastConfig) -> RoastConfig:
        self.config = config
        self.audit.set_limit(max(50, self.config.recent_limit * 4))
        self.recent_results = deque(self.recent_results, maxlen=self.config.recent_limit)
        self.recent_sandbox_results = deque(self.recent_sandbox_results, maxlen=self.config.recent_limit)
        self.permission_gate.update(self.config)
        self.safety_guard.update(self.config)
        if self.config.live_room_id <= 0:
            self.live_connection_state = "disconnected"
        self.safety_guard.set_connected(self.live_connection_state == "connected")
        return self.config

    def _get_config_lock(self) -> asyncio.Lock:
        if self._config_lock is None:
            self._config_lock = asyncio.Lock()
        return self._config_lock

    async def update_config(self, updates: dict[str, Any]) -> RoastConfig:
        allowed = set(RoastConfig.__dataclass_fields__.keys())
        clean = {key: value for key, value in updates.items() if key in allowed}
        if not clean:
            return self.config
        # 房号支持直接粘直播间链接：进 config / 持久化前归一成数字（链接→房号），保证落盘是 int。
        if "live_room_id" in clean:
            clean["live_room_id"] = parse_room_id(clean["live_room_id"])
        # 配置写竞争根治（插件侧）：先内存生效，再尽力持久化。
        # host 的 update_own_config 在「只重后端不重前端」等场景会卡满写竞争；旧实现先 await
        # 持久化再 apply，被 host 的 10s entry 超时连内存兜底都来不及跑，导致 update_config /
        # connect 直接 500。现在反过来：runtime 行为以内存为准、即时生效；持久化降级为带预算的
        # 尽力而为，超时/失败都不回滚、不阻塞。lock 串行化插件自身并发写，避免内存 apply 互踩。
        old_room_id = int(self.config.live_room_id or 0)
        was_listening = bool(self.bili_live_ingest.is_listening())
        async with self._get_config_lock():
            data = self.config.to_dict()
            data.update(clean)
            self._activate_config(RoastConfig.from_mapping(data))
            if "developer_tools_enabled" in clean:
                await self.sync_developer_mode(announce=False)
            await self._persist_config_best_effort(clean)
        await self._reconcile_live_listener_after_config(clean, old_room_id=old_room_id, was_listening=was_listening)
        return self.config

    async def _reconcile_live_listener_after_config(
        self,
        clean: dict[str, Any],
        *,
        old_room_id: int,
        was_listening: bool,
    ) -> None:
        if not was_listening:
            return
        room_changed = "live_room_id" in clean and int(self.config.live_room_id or 0) != old_room_id
        disabled = "live_enabled" in clean and not bool(self.config.live_enabled)
        if not room_changed and not disabled:
            return
        if not self.config.live_enabled:
            self.live_connection_state = "disconnected"
            self.safety_guard.set_connected(False)
            return
        if disabled or self.config.live_room_id <= 0:
            await self._stop_live_listener(mark_disabled=True)
            return
        started = await self._start_live_listener(int(self.config.live_room_id))
        self.audit.record(
            "live_reconnected" if started else "live_reconnect_failed",
            "danmaku listener restarted for room change" if started else "failed to restart danmaku listener for room change",
            level="info" if started else "warning",
            detail={"room_id": self.config.live_room_id, "previous_room_id": old_room_id},
        )

    async def _start_live_listener(self, room_id: int) -> bool:
        started = await self.bili_live_ingest.start_listening(room_id)
        self.live_connection_state = "connected" if started else "disconnected"
        self.config.live_enabled = bool(started)
        self.safety_guard.set_connected(started)
        return started

    async def _stop_live_listener(self, *, mark_disabled: bool) -> None:
        await self.bili_live_ingest.stop_listening()
        self.live_events.reset()
        if mark_disabled:
            self.config.live_enabled = False
        self.live_connection_state = "disconnected"
        self.safety_guard.set_connected(False)

    async def _persist_config_best_effort(self, clean: dict[str, Any]) -> None:
        """尽力持久化：带预算超时；超时/失败只记 audit，绝不回滚已生效的内存配置。"""
        try:
            await asyncio.wait_for(
                self._persist_config_update(clean),
                timeout=self._CONFIG_PERSIST_BUDGET_SECONDS,
            )
        except asyncio.TimeoutError:
            self.audit.record(
                "config_persist_timeout",
                f"config persistence exceeded {self._CONFIG_PERSIST_BUDGET_SECONDS}s budget; "
                "runtime config already applied in memory",
                level="warning",
            )
        except Exception as exc:
            self.audit.record(
                "config_persist_failed",
                f"config persistence failed, using runtime config: {type(exc).__name__}",
                level="warning",
            )

    async def _persist_config_update(self, clean: dict[str, Any]) -> None:
        update_own_config = getattr(getattr(self.plugin, "ctx", None), "update_own_config", None)
        if callable(update_own_config):
            await update_own_config({"neko_roast": clean}, timeout=10.0)
            return

        config_api = getattr(self.plugin, "config", None)
        ensure_active = getattr(config_api, "profile_ensure_active", None)
        if callable(ensure_active):
            await ensure_active("default", {"neko_roast": clean}, timeout=10.0)
        update = getattr(config_api, "update", None)
        if not callable(update):
            raise RuntimeError("plugin config update API is unavailable")
        try:
            await update({"neko_roast": clean})
        except ValueError as exc:
            if "no active profile" not in str(exc):
                raise
            raise RuntimeError("plugin config update requires an active profile") from exc

    def record_result(self, result: InteractionResult) -> None:
        if result.event.source == "developer_sandbox":
            payload = result.to_sandbox_dict()
            self.recent_sandbox_results.append(payload)
            self.event_bus.emit("sandbox_result", payload)
            return
        payload = result.to_public_dict()
        self.recent_results.append(payload)
        self.event_bus.emit("result", payload)

    async def handle_live_payload(self, payload: dict[str, Any]) -> InteractionResult:
        event = self.bili_live_ingest.normalize(payload)
        return await self.pipeline.handle_event(event)

    async def lookup_live_room(self, room_id: Any) -> dict[str, Any]:
        status = await self.bili_live_ingest.lookup_room_status(parse_room_id(room_id))
        level = "info" if status.ok else "warning"
        self.audit.record(
            "live_room_lookup",
            status.message or "live room looked up",
            level=level,
            detail={"room_id": status.room_id, "ok": status.ok, "live_status": status.live_status},
        )
        return status.to_dict()

    async def handle_sandbox_target(self, **kwargs: Any) -> InteractionResult:
        self._require_developer_mode()
        event = self.developer_sandbox.parse_target(**kwargs)
        return await self.pipeline.handle_event(event)

    async def lookup_bili_user(self, **kwargs: Any) -> dict[str, Any]:
        self._require_developer_mode()
        event = self.developer_sandbox.parse_target(**kwargs, use_presets=False)
        if not event.uid:
            raise ValueError("uid or Bilibili space URL is required")
        identity = await self.bili_identity.resolve(event)
        profile = ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)
        identity_payload = identity.to_public_dict()
        identity_payload["avatar_preview_url"] = ""
        identity_payload["avatar_preview_data_url"] = ""
        self.audit.record("developer_lookup", "bili user looked up", detail={"uid": identity.uid, "fetched": identity.fetched})
        return {
            "event": event.to_dict(),
            "identity": identity_payload,
            "profile": profile.to_dict(),
        }

    def clear_sandbox_data(self) -> dict[str, Any]:
        cleared_records = len(self.recent_sandbox_results)
        self.recent_sandbox_results.clear()
        cleared_preview_files = 0
        preview_dir = self.plugin_dir / "static" / "avatar-preview"
        if preview_dir.is_dir():
            for path in preview_dir.iterdir():
                if not path.is_file():
                    continue
                try:
                    path.unlink()
                    cleared_preview_files += 1
                except OSError:
                    self.audit.record("sandbox_preview_clear_failed", path.name, level="warning")
        self.audit.record(
            "sandbox_clear",
            "sandbox runtime data cleared",
            detail={"records": cleared_records, "preview_files": cleared_preview_files},
        )
        return {"records": cleared_records, "preview_files": cleared_preview_files}

    def _require_developer_mode(self) -> None:
        if not self.config.developer_tools_enabled:
            raise PermissionError("developer mode is disabled")

    async def handle_manual_event(self, **kwargs: Any) -> InteractionResult:
        event = ViewerEvent(
            uid=str(kwargs.get("uid") or "").strip(),
            nickname=str(kwargs.get("nickname") or "").strip(),
            avatar_url=str(kwargs.get("avatar_url") or "").strip(),
            danmaku_text=str(kwargs.get("danmaku_text") or "").strip(),
            target_lanlan=str(kwargs.get("target_lanlan") or kwargs.get("lanlan_name") or "").strip(),
            source="manual_live_simulation",
            live_mode=self.config.live_mode,
            raw=dict(kwargs),
        )
        return await self.pipeline.handle_event(event)

    async def dashboard_state(self) -> dict[str, Any]:
        profiles = await self.viewer_store.recent_profiles(self.config.recent_limit)
        storage = self.viewer_store.storage_status()
        return {
            "config": self.config.to_dict(),
            "live_connection": self.live_connection_snapshot(),
            # 观众档案改走本地 JSON（不依赖宿主 PluginStore，见 docs/devlog.md）。
            # store_enabled 保留旧字段名兼容面板，现指"档案目录是否可写=能否持久化"。
            "store_enabled": bool(storage.get("writable")),
            "viewer_store": storage,
            "modules": self.registry.snapshot(),
            "safety": self.safety_guard.snapshot(),
            "recent_profiles": profiles,
            "recent_results": list(reversed(self.recent_results)),
            "recent_sandbox_results": list(reversed(self.recent_sandbox_results)),
            "recent_audit": self.audit.recent(self.config.recent_limit),
            "avatar_cache": self.avatar_cache.status(),
            "actions": self.dashboard_actions(),
        }

    def dashboard_actions(self) -> list[dict[str, str]]:
        action_ids = [
            "update_config",
            "pick_folder",  # 面板「档案存储」卡的「浏览…」调它弹原生选目录框；必须在此暴露给 surface 才不被 403
            "set_live_room",
            "lookup_live_room",
            "connect_live_room",
            "disconnect_live_room",
            "pause_roast",
            "resume_roast",
            "clear_queue",
            "submit_viewer_event",
            "clear_sandbox_data",
            "bili_login",
            "bili_login_check",
            "bili_login_status",
            "bili_logout",
        ]
        return [{"id": action_id, "entry_id": action_id} for action_id in action_ids]

    def pause(self) -> None:
        self.safety_guard.pause("manual pause from control panel")

    def resume(self) -> None:
        self.safety_guard.resume()

    def clear_queue(self) -> None:
        self.safety_guard.clear_queue()
        self.audit.record("queue_clear", "queue cleared")

    def live_connection_snapshot(self) -> dict[str, Any]:
        if self.bili_live_ingest.is_listening():
            ls = self.bili_live_ingest.listener_state()
            state = str(ls.get("state") or "disconnected")
            viewer = int(ls.get("viewer_count") or 0)
        else:
            state = self.live_connection_state
            viewer = 0
        connected = state in ("receiving", "connected")
        return {
            "room_id": self.config.live_room_id,
            "state": state,
            "connected": connected,
            "listening": connected and self.config.live_enabled,
            "viewer_count": viewer,
        }

    async def set_live_room(self, room_id: Any) -> RoastConfig:
        room_id = parse_room_id(room_id)
        if room_id <= 0:
            raise ValueError("room_id must be positive")
        old_room_id = self.config.live_room_id
        config = await self.update_config({"live_room_id": room_id})
        if old_room_id != room_id and not self.bili_live_ingest.is_listening():
            self.live_connection_state = "disconnected"
            self.safety_guard.set_connected(False)
        self.audit.record("live_room_set", "live room updated", detail={"room_id": room_id})
        return config

    async def connect_live_room(self, room_id: Any = 0) -> dict[str, Any]:
        target_room_id = parse_room_id(room_id) or int(self.config.live_room_id or 0)
        if target_room_id <= 0:
            raise ValueError("room_id must be configured before connecting")
        if target_room_id != self.config.live_room_id:
            await self.set_live_room(target_room_id)
            if self.bili_live_ingest.is_listening() and int(self.config.live_room_id or 0) == target_room_id:
                return self.live_connection_snapshot()
        self.config.live_enabled = True  # 内存即时生效（gate/safety 共享同一 config 对象），避免配置写竞争拖垮连接
        started = await self._start_live_listener(target_room_id)
        self.audit.record(
            "live_connected" if started else "live_connect_failed",
            "danmaku listener started" if started else "failed to start danmaku listener",
            level="info" if started else "warning",
            detail={"room_id": target_room_id},
        )
        return self.live_connection_snapshot()

    async def disconnect_live_room(self) -> dict[str, Any]:
        await self._stop_live_listener(mark_disabled=True)
        self.audit.record("live_disconnected", "live ingest marked disconnected", detail={"room_id": self.config.live_room_id})
        return self.live_connection_snapshot()

from __future__ import annotations

from typing import Any, Dict, Optional
from pathlib import Path
import math

from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, Ok, Err, SdkError

from .service import STS2AutoplayService

_CONFIG_FILE = Path(__file__).with_name("plugin.toml")


@neko_plugin
class STS2AutoplayPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        self._service = STS2AutoplayService(self.logger, self.report_status, self._push_frontend_notification)

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("sts2", {}) if isinstance(cfg.get("sts2"), dict) else {}
        await self._service.startup(self._cfg)
        return Ok({"status": "ready", "result": await self._service.get_status()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self._service.shutdown()
        return Ok({"status": "shutdown"})

    def _push_frontend_notification(self, *, content: str, description: str, metadata: Dict[str, Any], priority: int = 5) -> None:
        self.push_message(
            source="sts2_autoplay",
            message_type="proactive_notification",
            description=description,
            priority=priority,
            content=content,
            metadata=metadata,
        )

    def _save_speed_overrides(self, *, post_action_delay_seconds: Optional[float] = None, poll_interval_active_seconds: Optional[float] = None, action_interval_seconds: Optional[float] = None) -> None:
        updates: list[tuple[str, float]] = []
        if post_action_delay_seconds is not None:
            updates.append(("post_action_delay_seconds", float(post_action_delay_seconds)))
        if poll_interval_active_seconds is not None:
            updates.append(("poll_interval_active_seconds", float(poll_interval_active_seconds)))
        if action_interval_seconds is not None:
            updates.append(("action_interval_seconds", float(action_interval_seconds)))
        if not updates:
            return
        text = _CONFIG_FILE.read_text(encoding="utf-8")
        for key, value in updates:
            self._cfg[key] = value
            text = self._replace_toml_number(text, key, value)
        _CONFIG_FILE.write_text(text, encoding="utf-8")

    def _replace_toml_number(self, text: str, key: str, value: float) -> str:
        if not math.isfinite(value):
            raise SdkError(f"非法配置值: {key}={value}")
        needle = f"{key} ="
        replacement = f"{key} = {value:g}"
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line.strip().startswith(needle):
                lines[index] = replacement
                return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        raise SdkError(f"plugin.toml 中未找到配置项: {key}")

    @plugin_entry(id="sts2_health_check", name="检查尖塔服务", description="检查本地尖塔 Agent 服务健康状态。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_health_check(self, **_):
        try:
            return Ok(await self._service.health_check())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_refresh_state", name="刷新尖塔状态", description="强制刷新一次当前尖塔游戏状态。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_refresh_state(self, **_):
        try:
            return Ok(await self._service.refresh_state())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_get_status", name="获取尖塔状态", description="获取尖塔连接状态、自动游玩状态和最近错误。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_get_status(self, **_):
        try:
            payload = await self._service.get_status()
            server_state = str((payload.get("server") or {}).get("state") or "unknown")
            autoplay_state = str((payload.get("autoplay") or {}).get("state") or "unknown")
            payload["message"] = f"{server_state} | autoplay={autoplay_state}"
            return Ok(payload)
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_get_snapshot", name="获取尖塔快照", description="获取最近缓存的尖塔游戏快照和合法动作。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_get_snapshot(self, **_):
        try:
            payload = await self._service.get_snapshot()
            return Ok(payload)
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_step_once", name="执行一步", description="根据当前策略执行一步尖塔合法动作。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_step_once(self, **_):
        try:
            return Ok(await self._service.step_once())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_start_autoplay", name="开启尖塔游玩", description="启动后台尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_start_autoplay(self, **_):
        try:
            return Ok(await self._service.start_autoplay())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_pause_autoplay", name="暂停尖塔游玩", description="暂停后台尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_pause_autoplay(self, **_):
        try:
            return Ok(await self._service.pause_autoplay())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_resume_autoplay", name="恢复尖塔游玩", description="恢复已暂停的尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_resume_autoplay(self, **_):
        try:
            return Ok(await self._service.resume_autoplay())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_stop_autoplay", name="停止尖塔游玩", description="停止后台尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_stop_autoplay(self, **_):
        try:
            return Ok(await self._service.stop_autoplay())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_get_history", name="获取尖塔历史", description="获取最近尖塔动作和状态历史。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}})
    async def sts2_get_history(self, limit: int = 20, **_):
        try:
            return Ok(await self._service.get_history(limit=limit))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_set_mode", name="设置尖塔模式", description="设置尖塔自动游玩模式。支持 full-program / half-program / full-model。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"mode": {"type": "string", "default": "half-program"}}, "required": ["mode"]})
    async def sts2_set_mode(self, mode: str, **_):
        try:
            return Ok(await self._service.set_mode(mode))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_set_character_strategy", name="设置角色策略", description="设置角色策略名称。会按 strategies/<name>.md 在策略目录中匹配对应文档。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"character_strategy": {"type": "string", "default": "defect"}}, "required": ["character_strategy"]})
    async def sts2_set_character_strategy(self, character_strategy: str, **_):
        try:
            return Ok(await self._service.set_character_strategy(character_strategy))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_set_speed", name="设置尖塔速度", description="设置动作间隔、动作后等待时间和尖塔活跃轮询间隔。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"action_interval_seconds": {"type": "number"}, "post_action_delay_seconds": {"type": "number"}, "poll_interval_active_seconds": {"type": "number"}}})
    async def sts2_set_speed(self, action_interval_seconds: Optional[float] = None, post_action_delay_seconds: Optional[float] = None, poll_interval_active_seconds: Optional[float] = None, **_):
        try:
            payload = await self._service.set_speed(action_interval_seconds=action_interval_seconds, post_action_delay_seconds=post_action_delay_seconds, poll_interval_active_seconds=poll_interval_active_seconds)
            self._save_speed_overrides(action_interval_seconds=payload.get("action_interval_seconds"), post_action_delay_seconds=payload.get("post_action_delay_seconds"), poll_interval_active_seconds=payload.get("poll_interval_active_seconds"))
            return Ok(payload)
        except Exception as e:
            return Err(str(e))

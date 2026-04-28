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

    def _push_frontend_notification(self, *, content: str, description: str, metadata: Dict[str, Any], priority: int = 5, message_type: str = "neko_observation") -> None:
        self.push_message(
            source="sts2_autoplay",
            message_type=message_type,
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

    @plugin_entry(id="sts2_health_check", name="检查尖塔服务", description="检查本地尖塔 Agent 服务健康状态。仅在用户明确要求检查尖塔服务健康时调用。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}}, metadata={"agent_auto": False})
    async def sts2_health_check(self, **_):
        try:
            return Ok(await self._service.health_check())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_refresh_state", name="刷新尖塔状态", description="强制刷新一次当前尖塔游戏状态。仅在用户明确要求刷新尖塔状态时调用。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}}, metadata={"agent_auto": False})
    async def sts2_refresh_state(self, **_):
        try:
            return Ok(await self._service.refresh_state())
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_get_status", name="获取尖塔状态", description="获取尖塔连接状态、自动游玩状态和最近错误。仅在用户明确要求查看尖塔状态时调用。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}}, metadata={"agent_auto": False})
    async def sts2_get_status(self, **_):
        try:
            payload = await self._service.get_status()
            server_state = str((payload.get("server") or {}).get("state") or "unknown")
            autoplay_state = str((payload.get("autoplay") or {}).get("state") or "unknown")
            payload["message"] = f"{server_state} | autoplay={autoplay_state}"
            return Ok(payload)
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_get_snapshot", name="获取尖塔快照", description="获取最近缓存的尖塔游戏快照和合法动作。仅在用户明确要求查看尖塔快照或合法动作时调用。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}}, metadata={"agent_auto": False})
    async def sts2_get_snapshot(self, **_):
        try:
            payload = await self._service.get_snapshot()
            return Ok(payload)
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_step_once", name="执行一步", description="根据当前策略执行一步尖塔合法动作。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_step_once(self, **_):
        try:
            payload = await self._service.step_once()
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_neko_command", name="尖塔猫娘指令", description="杀戮尖塔普通用户自然语言总入口。用户没有明确指定底层工具时优先调用本入口；它会根据用户原话自动判断查看状态、给建议、打一张牌、执行一步、开启自动游玩、暂停、恢复、停止或发送软指导。默认咨询不操作，只有用户明确授权时才执行游戏动作。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"command": {"type": "string", "description": "用户原话，例如：这回合怎么打、帮我打一张牌、先防一下、暂停一下"}, "scope": {"type": "string", "default": "auto", "description": "可选意图提示：auto/status/advice/one_card/one_action/autoplay/control/guidance"}, "confirm": {"type": "boolean", "default": False, "description": "是否已确认允许持续托管等高风险操作"}}, "required": ["command"]})
    async def sts2_neko_command(self, command: str, scope: str = "auto", confirm: bool = False, **_):
        try:
            payload = await self._service.neko_command(command=command, scope=scope, confirm=confirm)
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_recommend_one_card_by_neko", name="猫娘推荐一张牌", description="当用户询问杀戮尖塔当前打哪张牌好、帮忙看看出牌建议时调用：插件只会读取玩家/手牌/敌人状态并推荐一张 play_card，说明理由，不会自动打出卡牌。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"objective": {"type": "string", "description": "用户咨询目标，例如：帮我看看当前打哪张牌好"}}})
    async def sts2_recommend_one_card_by_neko(self, objective: Optional[str] = None, **_):
        try:
            payload = await self._service.recommend_one_card_by_neko(objective=objective)
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_play_one_card_by_neko", name="猫娘选择并打出一张牌", description="仅当用户明确授权插件实际操作、自动打出、代打或说帮我选一张牌打出去时调用：插件会读取玩家/手牌/敌人状态，选择一张 play_card，先通知将要打出的卡牌和理由，然后执行出牌。用户只是问打哪张牌好、想要建议时不要调用本入口，应调用 sts2_recommend_one_card_by_neko。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"objective": {"type": "string", "description": "用户授权目标，例如：帮我选一张牌打出去"}}})
    async def sts2_play_one_card_by_neko(self, objective: Optional[str] = None, **_):
        try:
            payload = await self._service.play_one_card_by_neko(objective=objective)
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_start_autoplay", name="开启尖塔游玩", description="由猫娘根据用户请求启动半自动尖塔游玩循环；例如用户说'帮我打这一关'时调用。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"objective": {"type": "string", "description": "用户授权目标，例如：帮我打这一关"}, "stop_condition": {"type": "string", "default": "current_floor", "description": "停止条件：current_floor/current_combat/manual"}}})
    async def sts2_start_autoplay(self, objective: Optional[str] = None, stop_condition: str = "current_floor", **_):
        try:
            payload = await self._service.start_autoplay(objective=objective, stop_condition=stop_condition)
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_pause_autoplay", name="暂停尖塔游玩", description="暂停后台尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_pause_autoplay(self, **_):
        try:
            payload = await self._service.pause_autoplay()
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_resume_autoplay", name="恢复尖塔游玩", description="恢复已暂停的尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_resume_autoplay(self, **_):
        try:
            payload = await self._service.resume_autoplay()
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_stop_autoplay", name="停止尖塔游玩", description="停止后台尖塔自动游玩循环。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {}})
    async def sts2_stop_autoplay(self, **_):
        try:
            payload = await self._service.stop_autoplay()
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_get_history", name="获取尖塔历史", description="获取最近尖塔动作和状态历史。仅在用户明确要求查看尖塔历史时调用。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}, metadata={"agent_auto": False})
    async def sts2_get_history(self, limit: int = 20, **_):
        try:
            return Ok(await self._service.get_history(limit=limit))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_send_neko_guidance", name="发送Neko指导", description="向后台 autoplay 发送猫娘的软指导，会在下一轮决策时被 LLM 参考。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"content": {"type": "string", "description": "猫娘的指导内容，自然语言"}, "step": {"type": "integer", "description": "对应的步数（可选）"}, "type": {"type": "string", "default": "soft_guidance"}}})
    async def sts2_send_neko_guidance(self, content: str, step: Optional[int] = None, type: str = "soft_guidance", **_):
        try:
            payload = await self._service.send_neko_guidance({"content": content, "step": step, "type": type})
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_set_mode", name="设置尖塔模式", description="设置尖塔自动游玩模式。支持 full-program / half-program / full-model。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"mode": {"type": "string", "default": "half-program"}}, "required": ["mode"]})
    async def sts2_set_mode(self, mode: str, **_):
        try:
            payload = await self._service.set_mode(mode)
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_set_character_strategy", name="设置角色策略", description="设置角色策略名称。会按 strategies/<name>.md 在策略目录中匹配对应文档。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"character_strategy": {"type": "string", "default": "defect"}}, "required": ["character_strategy"]})
    async def sts2_set_character_strategy(self, character_strategy: str, **_):
        try:
            payload = await self._service.set_character_strategy(character_strategy)
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

    @plugin_entry(id="sts2_set_speed", name="设置尖塔速度", description="设置动作间隔、动作后等待时间和尖塔活跃轮询间隔。", llm_result_fields=["summary"], input_schema={"type": "object", "properties": {"action_interval_seconds": {"type": "number"}, "post_action_delay_seconds": {"type": "number"}, "poll_interval_active_seconds": {"type": "number"}}})
    async def sts2_set_speed(self, action_interval_seconds: Optional[float] = None, post_action_delay_seconds: Optional[float] = None, poll_interval_active_seconds: Optional[float] = None, **_):
        try:
            payload = await self._service.set_speed(action_interval_seconds=action_interval_seconds, post_action_delay_seconds=post_action_delay_seconds, poll_interval_active_seconds=poll_interval_active_seconds)
            self._save_speed_overrides(action_interval_seconds=payload.get("action_interval_seconds"), post_action_delay_seconds=payload.get("post_action_delay_seconds"), poll_interval_active_seconds=payload.get("poll_interval_active_seconds"))
            return await self.finish(data=payload, reply=False, message=str(payload.get("summary") or ""))
        except Exception as e:
            return Err(str(e))

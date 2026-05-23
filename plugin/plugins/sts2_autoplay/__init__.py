from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, llm_tool, neko_plugin, plugin_entry, tr

from .service import STS2AutoplayService

JsonObject = dict[str, Any]
AsyncPayloadFactory = Callable[[], Awaitable[JsonObject]]


def _as_mapping(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _summary_from(payload: Mapping[str, Any]) -> str:
    return str(payload.get("summary") or payload.get("message") or payload.get("content") or "")


@neko_plugin
class STS2AutoplayPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: JsonObject = {}
        self._service = STS2AutoplayService(
            self.logger,
            self.report_status,
            self._push_frontend_notification,
            sdk_bus=self.bus,
            sdk_ctx=self.ctx,
            i18n=self.i18n,
        )

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        cfg = _as_mapping(await self.config.dump(timeout=5.0))
        self._cfg = _as_mapping(cfg.get("sts2"))
        startup_result = _as_mapping(await self._service.startup(self._cfg))
        return Ok({"status": "ready", "result": await self._service.get_status(), "startup": startup_result})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        await self._service.shutdown()
        return Ok({"status": "shutdown"})

    @llm_tool(
        name="sts2_get_status",
        description=tr("tools.sts2_get_status.description", default="只读获取杀戮尖塔连接状态、当前界面和基础分类信息。"),
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    async def llm_get_status(self, **_: Any) -> JsonObject:
        return await self._service.get_status()

    async def _run_entry(self, action: AsyncPayloadFactory, *, finish: bool = False):
        try:
            payload = await action()
            if finish:
                payload = dict(payload) if isinstance(payload, Mapping) else {"value": payload}
                payload.setdefault("summary", _summary_from(payload))
                return await self.finish(data=payload, delivery="passive", message=_summary_from(payload))
            return Ok(payload)
        except SdkError as error:
            self.logger.warning(f"STS2 plugin entry failed: {error}")
            return Err(str(error))
        except Exception as error:
            self.logger.exception("Unexpected STS2 plugin entry failure")
            return Err(self.i18n.t("errors.internal", default="尖塔插件内部错误: {error}", error=error))

    def _host_reply_text(self, content: str, *, limit: int = 30) -> str:
        text = str(content or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _push_frontend_notification(
        self,
        *,
        content: str,
        description: str,
        metadata: JsonObject,
        priority: int = 5,
        message_type: str = "sts2_status",
        visibility: list[str] | None = None,
        ai_behavior: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "source": "sts2_autoplay",
            "priority": priority,
            "metadata": dict(metadata),
        }
        host_content = self._host_reply_text(content)
        if visibility is not None or ai_behavior is not None:
            kwargs.update(
                {
                    "visibility": visibility if visibility is not None else [],
                    "ai_behavior": ai_behavior or "respond",
                    "parts": [{"type": "text", "text": host_content}],
                }
            )
            kwargs["metadata"]["description"] = description
            kwargs["metadata"]["message_type"] = message_type
            kwargs["metadata"]["delivery_semantics"] = "passive"
        else:
            kwargs.update(
                {
                    "message_type": message_type,
                    "description": description,
                    "content": host_content,
                }
            )

        self.push_message(**kwargs)

    @plugin_entry(
        id="sts2_health_check",
        name=tr("entries.sts2_health_check.name", default="看看尖塔连上没"),
        description=tr("entries.sts2_health_check.description", default="看看本地尖塔 Agent 服务现在能不能正常连上。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_health_check(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.health_check()
            self.push_message(
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.health_check.done", default="尖塔服务检查完成。"))}],
                source="sts2_autoplay",
                metadata={"entry_id": "sts2_health_check", "kind": "status_feedback", "delivery_semantics": "passive"},
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_get_status",
        name=tr("entries.sts2_get_status.name", default="看看现在是什么情况"),
        description=tr("entries.sts2_get_status.description", default="看看尖塔连接状态、当前界面和基础局面信息。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_get_status(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.get_status()
            self.push_message(
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.get_status.done", default="已获取尖塔状态。"))}],
                source="sts2_autoplay",
                metadata={"entry_id": "sts2_get_status", "kind": "status_feedback", "delivery_semantics": "passive"},
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_read_state",
        name=tr("entries.sts2_read_state.name", default="看看当前局面"),
        description=tr("entries.sts2_read_state.description", default="顺手刷新一下，并把当前快照、局势摘要和猫娘同步包一起读出来。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_read_state(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.neko.get_readout()
            self.push_message(
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.read_state.done", default="已读取尖塔局面。"))}],
                source="sts2_autoplay",
                metadata={"entry_id": "sts2_read_state", "kind": "status_feedback", "delivery_semantics": "passive"},
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_set_standby",
        name=tr("entries.sts2_set_standby.name", default="设置尖塔待机"),
        description=tr("entries.sts2_set_standby.description", default="切换尖塔待机模式。待机模式下停止动作执行，但保留状态整理与猫娘同步准备。"),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "standby": {"type": "boolean"},
            },
            "required": ["standby"],
        },
        metadata={"agent_auto": False},
    )
    async def sts2_set_standby(self, standby: bool, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_standby(standby))

    @plugin_entry(
        id="sts2_start_autoplay",
        name=tr("entries.sts2_start_autoplay.name", default="让它自己玩起来"),
        description=tr("entries.sts2_start_autoplay.description", default="启动后台自动运行，让尖塔自己继续往下打。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_start_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.start_autoplay)

    @plugin_entry(
        id="sts2_pause_autoplay",
        name=tr("entries.sts2_pause_autoplay.name", default="先停一下自动玩"),
        description=tr("entries.sts2_pause_autoplay.description", default="先暂停后台自动运行，等你决定下一步。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_pause_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.pause_autoplay)

    @plugin_entry(
        id="sts2_resume_autoplay",
        name=tr("entries.sts2_resume_autoplay.name", default="继续让它自己玩"),
        description=tr("entries.sts2_resume_autoplay.description", default="从暂停处接着自动运行。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_resume_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.resume_autoplay)

    @plugin_entry(
        id="sts2_stop_autoplay",
        name=tr("entries.sts2_stop_autoplay.name", default="别让它自己玩了"),
        description=tr("entries.sts2_stop_autoplay.description", default="停止后台自动运行，把控制权收回来。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_stop_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.stop_autoplay, finish=True)

    @plugin_entry(
        id="sts2_enable_companion_mode",
        name=tr("entries.sts2_enable_companion_mode.name", default="打开陪玩模式"),
        description=tr("entries.sts2_enable_companion_mode.description", default="让它开始陪你看局面，并适时给点评和提醒。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_enable_companion_mode(self, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_companion_mode(True))

    @plugin_entry(
        id="sts2_disable_companion_mode",
        name=tr("entries.sts2_disable_companion_mode.name", default="关掉陪玩模式"),
        description=tr("entries.sts2_disable_companion_mode.description", default="先别继续陪玩点评，只保留基础运行。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_disable_companion_mode(self, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_companion_mode(False))

    @plugin_entry(
        id="sts2_apply_user_override",
        name=tr("entries.sts2_apply_user_override.name", default="按我这句来调整策略"),
        description=tr("entries.sts2_apply_user_override.description", default="按当前场景理解你的这句话，并更新对应的事件或敌人级偏好。"),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["instruction"],
        },
        metadata={"agent_auto": False},
    )
    async def sts2_apply_user_override(self, instruction: str, source: str = "user", **_: Any):
        return await self._run_entry(lambda: self._service.apply_user_override_safely(instruction, source=source))

    @plugin_entry(
        id="sts2_get_planned_operation",
        name=tr("entries.sts2_get_planned_operation.name", default="看看它准备怎么走"),
        description=tr("entries.sts2_get_planned_operation.description", default="看看当前局面下，它下一步打算怎么操作。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_get_planned_operation(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.neko.get_planned_operation()
            self.push_message(
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.get_planned_operation.done", default="已获取尖塔规划动作。"))}],
                source="sts2_autoplay",
                metadata={"entry_id": "sts2_get_planned_operation", "kind": "status_feedback", "delivery_semantics": "passive"},
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_execute_planned_operation",
        name=tr("entries.sts2_execute_planned_operation.name", default="按建议走一步"),
        description=tr("entries.sts2_execute_planned_operation.description", default="直接执行它当前建议的下一步动作。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_execute_planned_operation(self, **_: Any):
        return await self._run_entry(self._service.neko.execute_planned_operation, finish=True)

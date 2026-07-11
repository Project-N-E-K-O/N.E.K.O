"""Neko Roast plugin entry."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr, ui


@neko_plugin
class NekoRoastPlugin(NekoPluginBase):
    name = "neko_roast"
    passive = True

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = getattr(ctx, "logger", None)
        self.runtime: Any | None = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            from .core.runtime import RoastRuntime
        except ModuleNotFoundError as exc:
            if exc.name != "plugin.plugins.neko_roast.core.runtime":
                raise
            return Ok({"status": "ready", "runtime": "pending"})
        self.runtime = RoastRuntime(self)
        await self.runtime.start()
        self.register_dynamic_entry(
            "developer_lookup_bili_user",
            self.developer_lookup_bili_user,
            name="查询 B站用户基础资料",
            description="开发者模式开启时使用。当用户要求查询 B站 UID、B站空间链接或运行弹幕锐评内置测试案例时使用。内置测试案例传 target='__demo__'。只查询 UID、昵称和头像，不输出锐评。",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "uid": {"type": "string"},
                    "nickname": {"type": "string"},
                    "avatar_url": {"type": "string"},
                },
            },
            timeout=20.0,
        )
        self.register_dynamic_entry(
            "developer_roast_by_bili_url",
            self.developer_roast_by_bili_url,
            name="查询 B站用户并弹幕锐评",
            description="开发者模式开启时使用。当用户要求查询并锐评 B站 UID、B站空间链接或运行弹幕锐评内置测试案例时使用。内置测试案例传 target='__demo__'。会查询 UID、昵称和头像，并让猫猫按当前人设输出锐评。",
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "uid": {"type": "string"},
                    "nickname": {"type": "string"},
                    "avatar_url": {"type": "string"},
                    "danmaku_text": {"type": "string"},
                },
            },
            timeout=30.0,
        )
        self._sync_developer_entries()
        await self.runtime.sync_live_instructions()
        await self.runtime.sync_developer_mode(announce=False)
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self.runtime:
            await self.runtime.stop()
        return Ok({"status": "stopped"})

    async def _on_command_loop_start(self) -> None:
        if self.runtime is not None:
            self.runtime._start_idle_hosting_loop()

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        if self.runtime is None:
            return Ok({"status": "ready", "runtime": "pending"})
        runtime = self.runtime
        await runtime.reload_config()
        self._sync_developer_entries()
        await runtime.sync_live_instructions()
        await runtime.sync_developer_mode(announce=False)
        return Ok({"status": "reloaded"})

    def _runtime(self) -> RoastRuntime:
        if self.runtime is None:
            raise RuntimeError("NekoRoast runtime is not started")
        return self.runtime

    def _sync_developer_entries(self) -> None:
        runtime = self._runtime()
        entry_ids = ("developer_lookup_bili_user", "developer_roast_by_bili_url")
        for entry_id in entry_ids:
            if runtime.config.developer_tools_enabled:
                self.enable_entry(entry_id)
            else:
                self.disable_entry(entry_id)


    @ui.context(id="dashboard", title=tr("panel.title", default="NEKO Live"))
    async def get_dashboard_ui_context(self) -> dict[str, Any]:
        return await self._runtime().dashboard_state()

    @ui.action(id="update_config", label=tr("actions.update_config.label", default="保存设置"), group="control", order=10, refresh_context=True)
    @plugin_entry(
        id="update_config",
        name=tr("entries.update_config.name", default="更新 NEKO Live 设置"),
        description=tr("entries.update_config.description", default="更新 NEKO Live 的直播模式、开关和安全门设置。"),
        input_schema={
            "type": "object",
            "properties": {
                "live_platform": {"type": "string"},
                "live_room_ref": {"type": "string"},
                "live_room_id": {"type": "integer"},
                "live_enabled": {"type": "boolean"},
                "live_mode": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "developer_tools_enabled": {"type": "boolean"},
                "rate_limit_seconds": {"type": "number"},
                "queue_limit": {"type": "integer"},
                "roast_strength": {"type": "string"},
                "activity_level": {"type": "string"},
                "roast_once_per_uid": {"type": "boolean"},
                "stream_theme": {"type": "string"},
                "stream_goal": {"type": "string"},
                "stream_columns": {"type": "string"},
                "stream_avoid_topics": {"type": "string"},
            },
        },
    )
    async def update_config_entry(self, **kwargs):
        runtime = self._runtime()
        previous_developer_mode = runtime.config.developer_tools_enabled
        config = await runtime.update_config(kwargs)
        self._sync_developer_entries()
        if "developer_tools_enabled" in kwargs:
            await runtime.sync_developer_mode(
                announce=bool(config.developer_tools_enabled and not previous_developer_mode),
            )
        return Ok({"config": config.to_dict()})

    @ui.action(id="pick_folder", label=tr("actions.pick_folder.label", default="选择文件夹"), group="control", order=15)
    @plugin_entry(
        id="pick_folder",
        name=tr("entries.pick_folder.name", default="选择文件夹"),
        description=tr("entries.pick_folder.description", default="弹出本机原生「选择文件夹」对话框，返回所选目录路径（面板用）。"),
        input_schema={
            "type": "object",
            "properties": {
                "initial": {"type": "string", "description": "初始目录"},
                "title": {"type": "string", "description": "对话框标题"},
            },
        },
    )
    async def pick_folder(self, **kwargs):
        """本机弹原生「选择文件夹」对话框（装机软件那种），返回所选目录。

        面板（hosted-ui 沙箱）拿不到 Electron dialog，故由插件后端（本机进程）用 tkinter 选目录框；
        放进 worker 线程跑（一次性、各自独立 Tk root），避开 async 事件循环与 Tk 主线程冲突，
        且不依赖 sys.executable（打包后仍可用）。-topmost 尽力压过常驻置顶窗口。
        """
        import asyncio as _asyncio

        initial = str(kwargs.get("initial") or "")
        title = str(kwargs.get("title") or "Select folder")

        def _ask() -> str:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            try:
                root.withdraw()
                try:
                    root.attributes("-topmost", True)
                    root.update()
                except Exception:  # noqa: BLE001 — 置顶失败不影响弹框
                    pass
                chosen = filedialog.askdirectory(initialdir=(initial or None), title=title)
                return chosen or ""
            finally:
                try:
                    root.destroy()
                except Exception:  # noqa: BLE001
                    pass

        try:
            path = await _asyncio.to_thread(_ask)
            return Ok({"path": path})
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"pick_folder failed: {type(exc).__name__}: {exc}"))

    @ui.action(id="set_live_room", label=tr("actions.set_live_room.label", default="切换房间"), group="room", order=10, refresh_context=True)
    @plugin_entry(
        id="set_live_room",
        name=tr("entries.set_live_room.name", default="设置直播间"),
        description=tr("entries.set_live_room.description", default="设置当前平台下 NEKO Live 要监听的直播间目标。"),
        input_schema={
            "type": "object",
            "properties": {"room_id": {"type": "string", "description": "直播间目标或链接"}},
            "required": ["room_id"],
        },
    )
    async def set_live_room(self, room_id="", **_):
        try:
            config = await self._runtime().set_live_room(room_id)
            connection = self._runtime().live_connection_snapshot()
            return Ok(
                {
                    "platform": config.live_platform,
                    "room_ref": config.live_room_ref,
                    "room_id": config.live_room_id,
                    "connection": connection,
                }
            )
        except (TypeError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="lookup_live_room", label=tr("actions.lookup_live_room.label", default="查询直播间"), group="room", order=15, refresh_context=True)
    @plugin_entry(
        id="lookup_live_room",
        name=tr("entries.lookup_live_room.name", default="查询直播间状态"),
        description=tr("entries.lookup_live_room.description", default="按当前平台的直播间目标查询标题、主播和开播状态。"),
        input_schema={
            "type": "object",
            "properties": {"room_id": {"type": "string", "description": "直播间目标或链接"}},
            "required": ["room_id"],
        },
    )
    async def lookup_live_room(self, room_id="", **_):
        try:
            payload = await self._runtime().lookup_live_room(room_id)
            return Ok(payload)
        except (TypeError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="connect_live_room", label=tr("actions.connect_live_room.label", default="开始监听"), group="room", order=20, refresh_context=True)
    @plugin_entry(
        id="connect_live_room",
        name=tr("entries.connect_live_room.name", default="开始监听直播间"),
        description=tr("entries.connect_live_room.description", default="开启 NEKO Live 直播接收状态。v0.1 不复制旧弹幕插件的 WebSocket 实现。"),
        input_schema={"type": "object", "properties": {"room_id": {"type": "string", "description": "直播间目标或链接（留空用已配置房间）"}}},
    )
    async def connect_live_room(self, room_id="", **_):
        try:
            connection = await self._runtime().connect_live_room(room_id)
            return Ok({"connection": connection})
        except (TypeError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="disconnect_live_room", label=tr("actions.disconnect_live_room.label", default="停止监听"), group="room", order=30, refresh_context=True)
    @plugin_entry(
        id="disconnect_live_room",
        name=tr("entries.disconnect_live_room.name", default="停止监听直播间"),
        description=tr("entries.disconnect_live_room.description", default="停止 NEKO Live 直播接收状态。"),
    )
    async def disconnect_live_room(self, **_):
        connection = await self._runtime().disconnect_live_room()
        return Ok({"connection": connection})

    @ui.action(id="bili_login", label=tr("actions.bili_login.label", default="扫码登录"), group="auth", order=10, refresh_context=True)
    @plugin_entry(id="bili_login", name=tr("entries.bili_login.name", default="B站扫码登录"), description=tr("entries.bili_login.description", default="生成B站扫码登录二维码（已登录则直接回报）。登录态用于绕过 -352 风控、抓取头像与连接弹幕。"))
    async def bili_login(self, **_):
        try:
            return Ok(await self._runtime().bili_login())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="bili_login_check", label=tr("actions.bili_login_check.label", default="检查登录"), group="auth", order=20, refresh_context=True)
    @plugin_entry(id="bili_login_check", name=tr("entries.bili_login_check.name", default="检查扫码登录状态"), description=tr("entries.bili_login_check.description", default="轮询扫码状态；用户在手机确认后加密保存凭据。"))
    async def bili_login_check(self, **_):
        try:
            return Ok(await self._runtime().bili_login_check())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="bili_login_status", label=tr("actions.bili_login_status.label", default="登录状态"), group="auth", order=30, refresh_context=True)
    @plugin_entry(id="bili_login_status", name=tr("entries.bili_login_status.name", default="查询B站登录状态"), description=tr("entries.bili_login_status.description", default="查询当前是否已登录B站及用户名。"))
    async def bili_login_status(self, **_):
        try:
            return Ok(await self._runtime().bili_login_status())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="bili_logout", label=tr("actions.bili_logout.label", default="退出登录"), group="auth", order=40, refresh_context=True)
    @plugin_entry(id="bili_logout", name=tr("entries.bili_logout.name", default="退出B站登录"), description=tr("entries.bili_logout.description", default="本地注销：删除本机加密保存的B站凭据与密钥（不吊销服务端 token）。"))
    async def bili_logout(self, **_):
        try:
            return Ok(await self._runtime().bili_logout())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="douyin_cookie_import", label=tr("actions.douyin_cookie_import.label", default="导入抖音 Cookie"), group="auth", order=50, refresh_context=True)
    @plugin_entry(
        id="douyin_cookie_import",
        name=tr("entries.douyin_cookie_import.name", default="导入抖音 Cookie"),
        description=tr("entries.douyin_cookie_import.description", default="手动导入浏览器 Cookie 并加密保存，用于抖音直播只读接入。"),
        input_schema={
            "type": "object",
            "properties": {
                "cookie": {"type": "string", "description": "浏览器复制的 Cookie header"},
                "uid": {"type": "string", "description": "可选账号标识，只用于本地状态展示"},
                "nickname": {"type": "string", "description": "可选昵称，只用于本地状态展示"},
            },
            "required": ["cookie"],
        },
    )
    async def douyin_cookie_import(self, cookie="", uid="", nickname="", **_):
        try:
            return Ok(await self._runtime().douyin_cookie_import(cookie, uid=uid, nickname=nickname))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="douyin_cookie_status", label=tr("actions.douyin_cookie_status.label", default="检查抖音 Cookie"), group="auth", order=60, refresh_context=True)
    @plugin_entry(
        id="douyin_cookie_status",
        name=tr("entries.douyin_cookie_status.name", default="检查抖音 Cookie 状态"),
        description=tr("entries.douyin_cookie_status.description", default="读取本地加密保存的抖音 Cookie 状态，不回传原始 Cookie。"),
    )
    async def douyin_cookie_status(self, **_):
        try:
            return Ok(await self._runtime().douyin_cookie_status())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="douyin_cookie_validate", label=tr("actions.douyin_cookie_validate.label", default="校验抖音 Cookie"), group="auth", order=70, refresh_context=True)
    @plugin_entry(
        id="douyin_cookie_validate",
        name=tr("entries.douyin_cookie_validate.name", default="校验抖音 Cookie"),
        description=tr("entries.douyin_cookie_validate.description", default="使用当前抖音直播间目标手动校验本地 Cookie 是否仍可读取房间元数据，不回传原始 Cookie。"),
        input_schema={
            "type": "object",
            "properties": {"room_ref": {"type": "string", "description": "抖音直播间 URL 或标识；留空使用当前配置。"}},
        },
    )
    async def douyin_cookie_validate(self, room_ref="", **_):
        try:
            return Ok(await self._runtime().douyin_cookie_validate(room_ref=room_ref))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="douyin_cookie_delete", label=tr("actions.douyin_cookie_delete.label", default="删除抖音 Cookie"), group="auth", order=80, refresh_context=True)
    @plugin_entry(
        id="douyin_cookie_delete",
        name=tr("entries.douyin_cookie_delete.name", default="删除抖音 Cookie"),
        description=tr("entries.douyin_cookie_delete.description", default="删除本地加密保存的抖音 Cookie 和密钥。"),
    )
    async def douyin_cookie_delete(self, **_):
        try:
            return Ok(await self._runtime().douyin_cookie_delete())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="pause_roast", label=tr("actions.pause.label", default="一键暂停"), group="safety", order=20, refresh_context=True)
    @plugin_entry(id="pause_roast", name=tr("entries.pause_roast.name", default="暂停锐评"), description=tr("entries.pause_roast.description", default="立即暂停弹幕锐评输出。"))
    async def pause_roast(self, **_):
        self._runtime().pause()
        return Ok({"status": "paused"})

    @ui.action(id="resume_roast", label=tr("actions.resume.label", default="恢复运行"), group="safety", order=30, refresh_context=True)
    @plugin_entry(id="resume_roast", name=tr("entries.resume_roast.name", default="恢复锐评"), description=tr("entries.resume_roast.description", default="清空队列并重置安全门。"))
    async def resume_roast(self, **_):
        self._runtime().resume()
        return Ok({"status": "running"})

    @ui.action(id="clear_queue", label=tr("actions.clear_queue.label", default="清空队列"), group="safety", order=40, refresh_context=True)
    @plugin_entry(id="clear_queue", name=tr("entries.clear_queue.name", default="清空队列"), description=tr("entries.clear_queue.description", default="清空当前锐评队列。"))
    async def clear_queue(self, **_):
        self._runtime().clear_queue()
        return Ok({"status": "cleared"})

    @ui.action(id="trigger_idle_hosting", label=tr("entries.trigger_idle_hosting.name", default="触发空闲营业"), group="hosting", order=10, refresh_context=True)
    @plugin_entry(
        id="trigger_idle_hosting",
        name=tr("entries.trigger_idle_hosting.name", default="触发空闲营业"),
        description=tr("entries.trigger_idle_hosting.description", default="猫猫独播空闲时手动触发一次营业。"),
    )
    async def trigger_idle_hosting(self, **_):
        try:
            result = await self._runtime().trigger_idle_hosting()
            return Ok(result.to_public_dict())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="trigger_warmup_hosting", label=tr("panel.actions.triggerWarmupHosting", default="触发开场营业"), group="hosting", order=20, refresh_context=True)
    @plugin_entry(
        id="trigger_warmup_hosting",
        name=tr("entries.trigger_warmup_hosting.name", default="触发开场营业"),
        description=tr("entries.trigger_warmup_hosting.description", default="猫猫独播刚开始时手动触发一次开场话题。"),
    )
    async def trigger_warmup_hosting(self, **_):
        try:
            result = await self._runtime().trigger_warmup_hosting()
            return Ok(result.to_public_dict())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="trigger_active_engagement", label=tr("panel.actions.triggerActiveEngagement", default="触发主动营业"), group="hosting", order=30, refresh_context=True)
    @plugin_entry(
        id="trigger_active_engagement",
        name=tr("entries.trigger_active_engagement.name", default="触发主动营业"),
        description=tr("entries.trigger_active_engagement.description", default="猫猫独播冷场时手动触发一次主动互动。"),
    )
    async def trigger_active_engagement(self, **_):
        try:
            result = await self._runtime().trigger_active_engagement()
            return Ok(result.to_public_dict())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="submit_viewer_event", label=tr("panel.actions.submitSandbox", default="发射模拟弹幕"), group="developer", order=20, refresh_context=True)
    @plugin_entry(
        id="submit_viewer_event",
        name=tr("entries.submit_viewer_event.name", default="提交观众事件"),
        description=tr("entries.submit_viewer_event.description", default="从开发者沙盒提交 UID/URL 测试事件。"),
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "uid": {"type": "string"},
                "nickname": {"type": "string"},
                "avatar_url": {"type": "string"},
                "danmaku_text": {"type": "string"},
                "lookup_only": {"type": "boolean"},
            },
        },
    )
    async def submit_viewer_event(self, **kwargs):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        lookup_only = bool(kwargs.pop("lookup_only", False))
        if lookup_only:
            try:
                payload = await runtime.lookup_bili_user(**kwargs)
            except (TypeError, ValueError, PermissionError) as exc:
                return Err(SdkError(str(exc)))
            return Ok(payload)
        try:
            result = await runtime.handle_sandbox_target(**kwargs)
        except PermissionError as exc:
            return Err(SdkError(str(exc)))
        return Ok(result.to_sandbox_dict())

    @plugin_entry(
        id="submit_live_event",
        name=tr("entries.submit_live_event.name", default="提交直播事件"),
        description=tr("entries.submit_live_event.description", default="提交归一化后的直播弹幕事件。"),
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "nickname": {"type": "string"},
                "avatar_url": {"type": "string"},
                "danmaku_text": {"type": "string"},
            },
            "required": ["uid"],
        },
    )
    async def submit_live_event(self, **kwargs):
        result = await self._runtime().handle_live_payload(kwargs)
        return Ok(result.to_public_dict())

    @plugin_entry(
        id="submit_manual_live_event",
        name=tr("entries.submit_manual_live_event.name", default="Submit manual live event"),
        description=tr(
            "entries.submit_manual_live_event.description",
            default="Submit a developer-only manual live simulation event through the live pipeline.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "nickname": {"type": "string"},
                "avatar_url": {"type": "string"},
                "danmaku_text": {"type": "string"},
                "target_lanlan": {"type": "string"},
            },
            "required": ["uid"],
        },
    )
    async def submit_manual_live_event(self, **kwargs):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        result = await runtime.handle_manual_event(**kwargs)
        return Ok(result.to_public_dict())

    @ui.action(id="clear_sandbox_data", label=tr("actions.clear_sandbox_data.label", default="清空沙盒记录"), group="developer", order=30, refresh_context=True)
    @plugin_entry(id="clear_sandbox_data", name=tr("entries.clear_sandbox_data.name", default="清空沙盒记录"), description=tr("entries.clear_sandbox_data.description", default="清空开发者沙盒的临时记录和头像预览缓存，不影响观众档案。"))
    async def clear_sandbox_data(self, **_):
        return Ok({"cleared": self._runtime().clear_sandbox_data()})

    @plugin_entry(id="clear_viewer_profiles", name=tr("entries.clear_viewer_profiles.name", default="清空观众档案"), description=tr("entries.clear_viewer_profiles.description", default="清空观众档案，用于下一场受控直播测试前重置首评状态。"))
    async def clear_viewer_profiles(self, **_):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        try:
            return Ok({"cleared": await runtime.clear_viewer_profiles()})
        except PermissionError as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="delete_viewer_profile",
        name=tr("entries.delete_viewer_profile.name", default="Delete viewer profile"),
        description=tr("entries.delete_viewer_profile.description", default="Delete one viewer profile by UID, including first-appearance state for controlled testing."),
        input_schema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    )
    async def delete_viewer_profile(self, uid="", **_):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        try:
            return Ok({"result": await runtime.delete_viewer_profile(uid)})
        except (PermissionError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="reset_viewer_impression",
        name=tr("entries.reset_viewer_impression.name", default="Reset viewer impression"),
        description=tr("entries.reset_viewer_impression.description", default="Clear one viewer's impression memory by UID while preserving first-appearance roast state."),
        input_schema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    )
    async def reset_viewer_impression(self, uid="", **_):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        try:
            return Ok({"result": await runtime.reset_viewer_impression(uid)})
        except (PermissionError, ValueError) as exc:
            return Err(SdkError(str(exc)))

    async def developer_lookup_bili_user(self, **kwargs):
        return await self._developer_lookup_bili_user_impl(**kwargs)

    async def _developer_lookup_bili_user_impl(self, **kwargs):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        try:
            payload = await runtime.lookup_bili_user(**kwargs)
        except (TypeError, ValueError, PermissionError) as exc:
            return Err(SdkError(str(exc)))
        return Ok(payload)

    async def developer_roast_by_bili_url(self, **kwargs):
        return await self._developer_roast_by_bili_url_impl(**kwargs)

    async def _developer_roast_by_bili_url_impl(self, **kwargs):
        runtime = self._runtime()
        if not runtime.config.developer_tools_enabled:
            return Err(SdkError("developer mode is disabled"))
        try:
            result = await runtime.handle_sandbox_target(**kwargs)
        except PermissionError as exc:
            return Err(SdkError(str(exc)))
        return Ok(result.to_sandbox_dict())

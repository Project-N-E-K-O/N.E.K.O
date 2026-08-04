"""RVC cover plugin — search online, convert with local RVC, push to player."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from .config_store import RvcCoverConfigStore
from .rvc_web_process import RvcWebConfig, RvcWebProcessManager
from .service import RvcCoverService, _OUTPUT_SUBDIR, settings_from_mapping, settings_to_mapping


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@neko_plugin
class RvcCoverPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.config_store = RvcCoverConfigStore(self.data_path("."), logger=self.logger)
        self._service = RvcCoverService(
            logger=self.logger,
            work_dir=self.data_path("work"),
            static_ui_dir=self.data_path("static_ui"),
            plugin_id=self.plugin_id,
            push_music=self._push_music_link,
            notify=self._notify_user,
        )
        self._web_manager = RvcWebProcessManager(
            RvcWebConfig(
                rvc_root=self._service.settings.rvc_root,
                python_path=self._service.settings.python_path,
                log_dir=self.data_path("logs"),
            )
        )

    def _web_config_from_settings(self) -> RvcWebConfig:
        s = self._service.settings
        return RvcWebConfig(
            rvc_root=s.rvc_root,
            python_path=s.python_path,
            port=int(s.web_port),
            server_name=str(s.web_server_name),
            auto_start=bool(s.auto_start_web),
            startup_timeout_seconds=float(s.web_startup_timeout_seconds),
            shutdown_timeout_seconds=float(s.web_shutdown_timeout_seconds),
            log_dir=self.data_path("logs"),
        )

    def _sync_web_manager(self) -> None:
        self._web_manager.configure(self._web_config_from_settings())

    @property
    def _source_static_dir(self) -> Path:
        return self.config_dir / "static"

    @property
    def _static_ui_dir(self) -> Path:
        return self.data_path("static_ui")

    def _register_writable_static_ui(self) -> bool:
        """Serve UI from data/static_ui so generated outputs can live under /ui/outputs/."""
        source = self._source_static_dir
        target = self._static_ui_dir
        target.mkdir(parents=True, exist_ok=True)
        (target / _OUTPUT_SUBDIR).mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            for path in source.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(source)
                if rel.parts and rel.parts[0] == _OUTPUT_SUBDIR:
                    continue
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        index_path = target / "index.html"
        if not index_path.is_file():
            return self.register_static_ui("static")
        self._static_ui_config = {
            "enabled": True,
            "directory": str(target),
            "index_file": "index.html",
            "cache_control": "no-cache",
            "plugin_id": self.plugin_id,
        }
        self._notify_static_ui_registered(self._static_ui_config)
        return True

    def _notify_user(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        try:
            self.push_message(
                source=self.plugin_id,
                visibility=["chat"],
                ai_behavior="blind",
                parts=[{"type": "text", "text": text}],
                metadata={"kind": "rvc_cover_status"},
                priority=6,
            )
        except Exception:
            self.logger.debug("push status failed", exc_info=True)

    def _push_music_link(
        self,
        *,
        url: str,
        title: str,
        artist: str,
        target_lanlan: str | None = None,
    ) -> None:
        host = (urlparse(url).hostname or "").lower()
        domains = (
            ["127.0.0.1", "localhost", "::1"]
            if host in {"127.0.0.1", "localhost", "::1"}
            else ([host] if host else [])
        )
        if domains:
            self.ctx.push_message(
                source=self.plugin_id,
                message_type="music_allowlist_add",
                description=f"Allow music host: {domains[0]}",
                priority=7,
                metadata={"domains": domains},
                target_lanlan=target_lanlan,
            )
        self.ctx.push_message(
            source=self.plugin_id,
            message_type="music_play_url",
            description=f"🎤 RVC 翻唱 [{title}]",
            priority=9,
            metadata={
                "url": url,
                "name": title or "RVC Cover",
                "artist": artist or "RVC",
            },
            target_lanlan=target_lanlan,
        )

    async def _load_toml_rvc_section(self) -> dict[str, Any]:
        cfg = _as_mapping(await self.config.dump(timeout=5.0))
        return _as_mapping(cfg.get("rvc"))

    async def _load_effective_rvc_settings(self) -> dict[str, Any]:
        base = await self._load_toml_rvc_section()
        overlay: dict[str, Any] = {}
        if await self.config_store.exists():
            overlay = await self.config_store.load()
        return self.config_store.merge_with_base(base, overlay)

    async def _apply_effective_settings(self) -> list[str]:
        section = await self._load_effective_rvc_settings()
        problems = self._service.apply_settings(section)
        self._sync_web_manager()
        return problems

    def _dashboard(self) -> dict[str, Any]:
        state = self._service.dashboard_state()
        state["config_path"] = str(self.config_store.path)
        state["rvc_web"] = self._web_manager.snapshot()
        return state

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        self._register_writable_static_ui()
        self.set_list_actions(
            [
                {
                    "id": "open_ui",
                    "label": self.i18n.t("ui.actions.open", default="打开面板"),
                    "kind": "ui",
                    "target": f"/plugin/{self.plugin_id}/ui/",
                    "open_in": "new_tab",
                }
            ]
        )
        problems = await self._apply_effective_settings()
        if problems:
            self.logger.warning("RVC cover startup issues: %s", problems)
        else:
            self.logger.info(
                "RVC cover ready model=%s root=%s store=%s",
                self._service.settings.model_name,
                self._service.settings.rvc_root,
                self.config_store.path,
            )
        web_status = await asyncio.to_thread(self._web_manager.start_if_needed)
        self.logger.info(
            "RVC web lifecycle start mode=%s health=%s url=%s error=%s",
            web_status.get("mode"),
            web_status.get("health"),
            web_status.get("url"),
            web_status.get("last_error"),
        )
        state = self._dashboard()
        state["rvc_web"] = web_status
        return Ok(state)

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        web_status = await asyncio.to_thread(self._web_manager.stop)
        self.logger.info(
            "RVC web lifecycle stop mode=%s started_by_plugin=%s",
            web_status.get("mode"),
            web_status.get("started_by_plugin"),
        )
        return Ok({"status": "stopped", "rvc_web": web_status})

    @lifecycle(id="config_change")
    async def config_change(self, **_: Any):
        old = self._web_manager.snapshot()
        old_cfg = self._web_manager.config
        problems = await self._apply_effective_settings()
        new_cfg = self._web_config_from_settings()
        need_restart = bool(old.get("started_by_plugin")) and (
            int(old.get("port") or 0) != int(new_cfg.port)
            or Path(old_cfg.rvc_root) != Path(new_cfg.rvc_root)
            or Path(old_cfg.python_path) != Path(new_cfg.python_path)
        )
        web_status = self._web_manager.snapshot()
        if need_restart:
            await asyncio.to_thread(self._web_manager.stop)
            web_status = await asyncio.to_thread(self._web_manager.start_if_needed)
        state = self._dashboard()
        state["status"] = "reloaded"
        state["problems"] = problems
        state["rvc_web"] = web_status
        return Ok(state)

    @ui.context(id="rvc_cover")
    async def get_dashboard_context(self):
        return {
            **self._dashboard(),
            "actions": [
                {"id": "get_dashboard_state", "entry_id": "get_dashboard_state"},
                {"id": "save_settings", "entry_id": "save_settings"},
                {"id": "sing_cover", "entry_id": "sing_cover"},
                {"id": "list_models", "entry_id": "list_models"},
                {"id": "cover_status", "entry_id": "cover_status"},
            ],
        }

    def _enqueue_cover(
        self,
        *,
        query: str = "",
        song: str = "",
        artist: str = "",
        model_name: str = "",
        target_lanlan: str = "",
    ) -> dict[str, Any]:
        return self._service.enqueue(
            query=query,
            song=song,
            artist=artist,
            model_name=model_name,
            target_lanlan=target_lanlan,
        )

    @llm_tool(
        name="sing_cover",
        description=(
            "当用户想听你用角色音色唱歌/翻唱时调用。"
            "会联网搜索歌曲，用本机 RVC 转换后推到播放器。"
            "适用于「给我唱一首…」「翻唱…」「用XX声音唱…」。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "歌名或搜索关键词，例如「晴天」或「周杰伦 晴天」。",
                },
                "song": {"type": "string", "description": "可选。精确歌名。"},
                "artist": {"type": "string", "description": "可选。原唱歌手。"},
                "model_name": {
                    "type": "string",
                    "description": "可选。RVC 模型文件名，如 Ai糯糯雫.pth。",
                },
            },
            "required": [],
        },
        timeout=30.0,
    )
    async def llm_sing_cover(
        self,
        *,
        query: str = "",
        song: str = "",
        artist: str = "",
        model_name: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        return self._enqueue_cover(
            query=query,
            song=song,
            artist=artist,
            model_name=model_name,
        )

    @plugin_entry(
        id="get_dashboard_state",
        name="面板状态",
        description="读取 RVC 翻唱面板状态、模型列表与当前任务。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def entry_get_dashboard_state(self, **_: Any):
        return Ok(self._dashboard())

    @plugin_entry(
        id="save_settings",
        name="保存 RVC 设置",
        description="保存音色与推理参数到本机插件数据目录。",
        input_schema={
            "type": "object",
            "properties": {
                "model_name": {"type": "string"},
                "device": {"type": "string"},
                "f0_method": {"type": "string"},
                "f0_up_key": {"type": "number"},
                "index_rate": {"type": "number"},
                "filter_radius": {"type": "number"},
                "resample_sr": {"type": "number"},
                "rms_mix_rate": {"type": "number"},
                "protect": {"type": "number"},
                "infer_timeout_seconds": {"type": "number"},
                "index_path": {"type": "string"},
                "rvc_root": {"type": "string"},
                "python_path": {"type": "string"},
                "use_uvr": {"type": "boolean"},
                "auto_start_web": {"type": "boolean"},
                "web_port": {"type": "number"},
                "web_server_name": {"type": "string"},
            },
            "required": [],
        },
        metadata={"agent_auto": False},
    )
    async def entry_save_settings(self, **kwargs: Any):
        current = await self._load_effective_rvc_settings()
        allowed = {
            "model_name",
            "device",
            "f0_method",
            "f0_up_key",
            "index_rate",
            "filter_radius",
            "resample_sr",
            "rms_mix_rate",
            "protect",
            "infer_timeout_seconds",
            "index_path",
            "rvc_root",
            "python_path",
            "use_uvr",
            "auto_start_web",
            "web_port",
            "web_server_name",
        }
        patch = {
            key: kwargs[key]
            for key in allowed
            if key in kwargs and kwargs[key] is not None
        }
        if not patch:
            return Err("没有可保存的设置字段")
        merged = {**current, **patch}
        try:
            saved = await self.config_store.save(merged)
        except Exception as exc:
            self.logger.exception("rvc save_settings failed")
            return Err(f"保存失败: {exc}")
        problems = self._service.apply_settings(saved)
        state = self._dashboard()
        state["problems"] = problems
        state["saved"] = True
        state["settings"] = settings_to_mapping(settings_from_mapping(saved))
        return Ok(state)

    @plugin_entry(
        id="sing_cover",
        name="RVC 翻唱",
        description="联网搜歌并用本机 RVC 翻唱后推送到播放器。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "song": {"type": "string"},
                "artist": {"type": "string"},
                "model_name": {"type": "string"},
                "target_lanlan": {"type": "string"},
            },
            "required": [],
        },
        metadata={"agent_auto": False},
    )
    async def entry_sing_cover(
        self,
        query: str = "",
        song: str = "",
        artist: str = "",
        model_name: str = "",
        target_lanlan: str = "",
        **_: Any,
    ):
        result = self._enqueue_cover(
            query=query,
            song=song,
            artist=artist,
            model_name=model_name,
            target_lanlan=target_lanlan,
        )
        if result.get("ok"):
            return Ok(result)
        return Err(str(result.get("message") or result.get("error") or "sing_cover failed"))

    @plugin_entry(
        id="list_models",
        name="列出 RVC 模型",
        description="列出 vendor/rvc/assets/weights 下可用的 .pth 音色。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def entry_list_models(self, **_: Any):
        models = self._service.list_models()
        return Ok(
            {
                "models": models,
                "default": self._service.settings.model_name,
                "count": len(models),
            }
        )

    @plugin_entry(
        id="cover_status",
        name="翻唱状态",
        description="查看当前 RVC 翻唱任务状态。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def entry_cover_status(self, **_: Any):
        return Ok(self._service.status())

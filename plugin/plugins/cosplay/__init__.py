# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

_SIMPLE_ROLE_RE = re.compile(r"^\s*(用户|user|导演|ai|assistant)\s*[：:]\s*(.+?)\s*$", re.IGNORECASE)
_GENERAL_LINE_RE = re.compile(r"^\s*([^：:]{1,30})\s*[：:]\s*(.+?)\s*$")
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_ROLE_DECLARE_RE = re.compile(r"(用户|user|我)\s*是\s*([^\s，,。；;:：]+)|(ai|assistant|模型)\s*是\s*([^\s，,。；;:：]+)", re.IGNORECASE)
_BRACKET_CONTENT_RE = re.compile(r"(\([^()]*\)|（[^（）]*）|\[[^\[\]]*\]|【[^【】]*】)")
_ROLE_ASSIGN_RE = re.compile(r"^\s*([^：:]{1,30})\s*[:：]\s*(.+?)\s*$")

_ALLOWED_IMAGE_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_ALLOWED_VIDEO_MIMES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

_DASHSCOPE_TERMINAL_OK = {"SUCCEEDED"}
_DASHSCOPE_TERMINAL_BAD = {"FAILED", "CANCELED", "UNKNOWN"}


@neko_plugin
class CosplayPlugin(NekoPluginBase):
    """NEKO GALGAME 总导演插件 MVP。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        self._lock = asyncio.Lock()

        self._http_timeout_sec = 120.0
        self._model_config = self._default_model_config()
        self._role_config = self._default_role_config()
        self._session = self._new_session()

        self._static_root = self.config_dir / "static"
        self._data_root = self.config_dir / "data"
        self._assets_data_root = self._data_root / "assets"
        self._assets_public_root = self._static_root / "assets"
        self._generated_img_dir = self._assets_data_root / "generated" / "images"
        self._generated_video_dir = self._assets_data_root / "generated" / "videos"
        self._upload_img_dir = self._assets_data_root / "uploads" / "images"
        self._upload_video_dir = self._assets_data_root / "uploads" / "videos"
        self._upload_tmp_dir = self._assets_data_root / "uploads" / "tmp"
        self._chunk_uploads: dict[str, dict[str, Any]] = {}

    @lifecycle(id="startup")
    async def startup(self, **_):
        self._ensure_dirs()
        ui_registered = self.register_static_ui(
            "static",
            index_file="index.html",
            cache_control="no-store, no-cache, must-revalidate, max-age=0",
        )

        await self._load_store_values()
        self.logger.info("cosplay startup ok, ui={}, url={}", ui_registered, self._plugin_ui_url())
        return Ok(
            {
                "status": "ready",
                "ui_registered": ui_registered,
                "ui_url": self._plugin_ui_url(),
                "storage_paths": self._storage_paths(),
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @plugin_entry(id="get_ui_info", llm_result_fields=["ui_url", "status"])
    async def get_ui_info(self, **_):
        async with self._lock:
            state = self._build_state_unlocked(include_config=False)
        return Ok(
            {
                "status": "ok",
                "ui_url": self._plugin_ui_url(),
                "ui_relative_url": f"/plugin/{self.plugin_id}/ui/",
                "recommended_open_url": "http://127.0.0.1:48916/plugin/cosplay/ui/",
                "state": state,
            }
        )

    @plugin_entry(id="get_state", llm_result_fields=["prepared", "current_index", "total_turns", "stage_media"])
    async def get_state(self, **_):
        async with self._lock:
            return Ok(self._build_state_unlocked(include_config=True))

    @plugin_entry(
        id="save_model_config",
        input_schema={
            "type": "object",
            "properties": {
                "config": {"type": "object"},
            },
            "required": ["config"],
        },
        llm_result_fields=["saved"],
    )
    async def save_model_config(self, config: dict[str, Any], **_):
        sanitized = self._sanitize_model_config(config)
        async with self._lock:
            self._model_config = sanitized
        await self._store_set("cosplay_model_config", sanitized)
        return Ok({"saved": True, "config": sanitized, "masked": self._masked_model_config(sanitized)})

    @plugin_entry(
        id="save_role_config",
        input_schema={
            "type": "object",
            "properties": {
                "config": {"type": "object"},
            },
            "required": ["config"],
        },
        llm_result_fields=["saved"],
    )
    async def save_role_config(self, config: dict[str, Any], **_):
        sanitized = self._sanitize_role_config(config)
        async with self._lock:
            self._role_config = sanitized
        await self._store_set("cosplay_role_config", sanitized)
        return Ok({"saved": True, "config": sanitized})

    @plugin_entry(
        id="upload_stage_asset",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["image", "video"]},
                "filename": {"type": "string"},
                "data_url": {"type": "string"},
            },
            "required": ["kind", "filename", "data_url"],
        },
        llm_result_fields=["ok", "url", "path"],
    )
    async def upload_stage_asset(self, kind: str, filename: str, data_url: str, **_):
        try:
            saved = await asyncio.to_thread(self._save_uploaded_asset, kind, filename, data_url)
            return Ok({"ok": True, **saved})
        except Exception as exc:
            return Err(SdkError(f"上传失败: {exc}"))

    @plugin_entry(
        id="begin_chunked_video_upload",
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "mime": {"type": "string"},
                "total_size": {"type": "integer"},
            },
            "required": ["filename", "mime", "total_size"],
        },
        llm_result_fields=["upload_id", "ok"],
    )
    async def begin_chunked_video_upload(self, filename: str, mime: str, total_size: int, **_):
        if str(mime or "").strip().lower() not in _ALLOWED_VIDEO_MIMES:
            return Err(SdkError(f"不支持的视频格式: {mime}"))
        size = max(0, int(total_size or 0))
        if size <= 0:
            return Err(SdkError("文件大小无效"))
        if size > 120 * 1024 * 1024:
            return Err(SdkError("文件过大，限制 120MB"))

        upload_id = f"video-{uuid.uuid4().hex}"
        base = Path(filename or "video").stem[:50].strip() or "video"
        ext = _ALLOWED_VIDEO_MIMES[str(mime or "").strip().lower()]
        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{base}_{uuid.uuid4().hex[:6]}{ext}"
        tmp_path = self._upload_tmp_dir / f"{upload_id}.part"

        async with self._lock:
            self._chunk_uploads[upload_id] = {
                "filename": file_name,
                "mime": str(mime or "").strip().lower(),
                "expected_size": size,
                "received_size": 0,
                "tmp_path": str(tmp_path),
            }
        await asyncio.to_thread(tmp_path.write_bytes, b"")
        return Ok({"ok": True, "upload_id": upload_id})

    @plugin_entry(
        id="append_chunked_video_upload",
        input_schema={
            "type": "object",
            "properties": {
                "upload_id": {"type": "string"},
                "chunk_b64": {"type": "string"},
            },
            "required": ["upload_id", "chunk_b64"],
        },
        llm_result_fields=["ok", "received_size"],
    )
    async def append_chunked_video_upload(self, upload_id: str, chunk_b64: str, **_):
        uid = str(upload_id or "").strip()
        if not uid:
            return Err(SdkError("upload_id 不能为空"))
        async with self._lock:
            item = self._chunk_uploads.get(uid)
        if not item:
            return Err(SdkError("上传会话不存在或已过期"))
        try:
            raw = base64.b64decode(str(chunk_b64 or "").encode("utf-8"), validate=True)
        except Exception:
            return Err(SdkError("分片数据损坏，base64 解码失败"))

        tmp_path = Path(str(item.get("tmp_path", "")))
        await asyncio.to_thread(self._append_file_bytes, tmp_path, raw)
        async with self._lock:
            item = self._chunk_uploads.get(uid)
            if not item:
                return Err(SdkError("上传会话不存在或已过期"))
            item["received_size"] = int(item.get("received_size", 0)) + len(raw)
            received = int(item["received_size"])
        return Ok({"ok": True, "received_size": received})

    @plugin_entry(
        id="finish_chunked_video_upload",
        input_schema={
            "type": "object",
            "properties": {
                "upload_id": {"type": "string"},
            },
            "required": ["upload_id"],
        },
        llm_result_fields=["ok", "url", "path"],
    )
    async def finish_chunked_video_upload(self, upload_id: str, **_):
        uid = str(upload_id or "").strip()
        if not uid:
            return Err(SdkError("upload_id 不能为空"))
        async with self._lock:
            item = self._chunk_uploads.pop(uid, None)
        if not item:
            return Err(SdkError("上传会话不存在或已过期"))

        expected = int(item.get("expected_size", 0))
        received = int(item.get("received_size", 0))
        tmp_path = Path(str(item.get("tmp_path", "")))
        if received <= 0 or received != expected:
            await asyncio.to_thread(self._safe_unlink, tmp_path)
            return Err(SdkError(f"视频分片上传不完整: expected={expected}, received={received}"))
        try:
            finalized = await asyncio.to_thread(self._finalize_uploaded_tmp_video, tmp_path, str(item.get("mime", "")))
            return Ok({"ok": True, **finalized})
        except Exception as exc:
            await asyncio.to_thread(self._safe_unlink, tmp_path)
            return Err(SdkError(f"视频上传收尾失败: {exc}"))

    @plugin_entry(
        id="replace_node_asset",
        input_schema={
            "type": "object",
            "properties": {
                "node_index": {"type": "integer"},
                "kind": {"type": "string", "enum": ["image", "video"]},
                "url": {"type": "string"},
            },
            "required": ["node_index", "kind", "url"],
        },
        llm_result_fields=["ok", "node_index", "kind"],
    )
    async def replace_node_asset(self, node_index: int, kind: str, url: str, **_):
        k = str(kind or "").strip().lower()
        u = str(url or "").strip()
        if k not in {"image", "video"}:
            return Err(SdkError("kind 仅支持 image/video"))
        if not u:
            return Err(SdkError("url 不能为空"))

        async with self._lock:
            nodes = self._session.get("nodes", [])
            idx = int(node_index)
            if not isinstance(nodes, list) or idx < 0 or idx >= len(nodes):
                return Err(SdkError(f"节点索引无效: {node_index}"))

            node = nodes[idx]
            if k == "image":
                node["image_url"] = u
                node["image_path"] = str(self._url_to_local_path(u)) if "/plugin/cosplay/ui/" in u else ""
                node["image_status"] = "ready"
                node["image_error"] = ""
            else:
                node["video_url"] = u
                node["video_path"] = str(self._url_to_local_path(u)) if "/plugin/cosplay/ui/" in u else ""
                node["video_status"] = "ready"
                node["video_error"] = ""

            state = self._build_state_unlocked(False)

        return Ok({"ok": True, "node_index": idx, "kind": k, "state": state})

    @plugin_entry(
        id="prepare_script",
        input_schema={
            "type": "object",
            "properties": {
                "script_text": {"type": "string"},
                "scene_split_mode": {"type": "string", "enum": ["strict_copy", "smart_director"]},
                "max_scenes": {"type": "integer", "minimum": 1},
                "exact_scenes": {"type": "integer", "minimum": 1},
            },
            "required": ["script_text"],
        },
        llm_result_fields=["prepared", "total_turns", "climax_count", "status"],
    )
    async def prepare_script(
        self,
        script_text: str,
        scene_split_mode: str = "strict_copy",
        max_scenes: int = 0,
        exact_scenes: int = 0,
        **_,
    ):
        if not str(script_text or "").strip():
            return Err(SdkError("剧本为空：请粘贴剧情剧本后再解析。"))
        split_mode = str(scene_split_mode or "strict_copy").strip().lower()
        if split_mode not in {"strict_copy", "smart_director"}:
            split_mode = "strict_copy"
        max_scene_count = max(0, int(max_scenes or 0))
        exact_scene_count = max(0, int(exact_scenes or 0))

        async with self._lock:
            role_cfg = json.loads(json.dumps(self._role_config, ensure_ascii=False))
            model_cfg = json.loads(json.dumps(self._model_config, ensure_ascii=False))
            self._session["status"] = {"phase": "parsing", "message": "正在解析剧本...", "progress": 12}
            bg_mode = str(self._session.get("background_mode", "semi_auto") or "semi_auto")
            role_profiles = json.loads(json.dumps(self._session.get("role_profiles", {}), ensure_ascii=False))

        parsed = self._parse_script(script_text, role_cfg)
        if not parsed["dialogues"]:
            msg = (
                "未提取到有效对话台词。"
                "请检查角色名映射或使用“角色名：台词”格式。"
            )
            async with self._lock:
                self._session["status"] = {"phase": "failed", "message": msg, "progress": 0}
                self._session["last_error"] = parsed.get("debug", "")
            return Err(SdkError(f"{msg} 详情: {parsed.get('debug', '')}"))

        async with self._lock:
            self._session["status"] = {"phase": "planning", "message": "总导演正在拆分剧情分镜...", "progress": 34}

        scene_turns = self._select_scene_turns(parsed, split_mode)
        if exact_scene_count > 0 and len(scene_turns) != exact_scene_count:
            return Err(
                SdkError(
                    f"你要求必须生成 {exact_scene_count} 幕，但当前剧本在“{split_mode}”模式下解析为 {len(scene_turns)} 幕。"
                    "请调整分幕模式、剧本轮次或取消“强制等于幕数”。"
                )
            )
        scene_turns = self._apply_scene_limit(scene_turns, max_scene_count)
        if not scene_turns:
            return Err(SdkError("分幕后为空，请调整分幕模式或幕数上限。"))
        image_enabled = self._as_bool((model_cfg.get("image", {}) or {}).get("enabled"), True)
        video_enabled = self._as_bool((model_cfg.get("video", {}) or {}).get("enabled"), True) and bg_mode == "auto"
        nodes = await self._plan_nodes(
            scene_turns,
            model_cfg["director"],
            need_image_prompt=image_enabled and bg_mode != "manual",
            need_video_prompt=video_enabled and bg_mode != "manual",
        )
        if not nodes:
            nodes = self._fallback_nodes(scene_turns)
        nodes = self._attach_dialogues_to_nodes(nodes, scene_turns, role_profiles)

        async with self._lock:
            self._session["status"] = {"phase": "generating", "message": "正在生成图片与视频背景...", "progress": 58}

        await self._generate_assets(nodes, model_cfg, bg_mode)

        control_audit = self._build_control_audit(nodes)
        climax_count = sum(1 for n in nodes if n.get("is_climax"))

        async with self._lock:
            self._session = {
                "prepared": True,
                "nodes": nodes,
                "current_index": 0,
                "background_mode": bg_mode,
                "scene_split_mode": split_mode,
                "max_scenes": max_scene_count,
                "exact_scenes": exact_scene_count,
                "override": None,
                "last_push_text": "",
                "last_error": "",
                "script_meta": parsed,
                "control_audit": control_audit,
                "dialogue_history": [],
                "role_profiles": role_profiles or parsed.get("role_profiles", {}),
                "status": {"phase": "ready", "message": "预生成完成", "progress": 100},
            }
            state = self._build_state_unlocked(include_config=True)

        return Ok(
            {
                "prepared": True,
                "total_turns": len(nodes),
                "climax_count": climax_count,
                "status": "预生成完成",
                "state": state,
            }
        )

    @plugin_entry(id="confirm_current_line", llm_result_fields=["finished", "strict_push_text", "next_user_line", "push_result"])
    async def confirm_current_line(self, spoken_line: str = "", **_):
        async with self._lock:
            if not self._session.get("prepared"):
                return Err(SdkError("请先完成剧本解析与预生成。"))

            nodes = self._session.get("nodes", [])
            idx = int(self._session.get("current_index", 0))
            if idx >= len(nodes):
                return Ok({"finished": True, "next_user_line": "", "state": self._build_state_unlocked(False)})

            node = nodes[idx]
            user_line = str(node.get("user_line", "") or "").strip()
            ai_line = str(node.get("ai_line", "") or node.get("line_text", "") or "").strip()
            role_name = str(node.get("role_name", "角色") or "角色").strip()
            expected_user_line = user_line

        if not ai_line:
            return Err(SdkError("当前节点缺少 AI 对应台词，无法推进。"))

        # 用户确认“用户台词”后，再推送 AI 对应台词给主对话模型。
        strict_push_text = ai_line
        push_result = self._push_to_main_dialog_model(ai_line)

        async with self._lock:
            nodes = self._session.get("nodes", [])
            idx = int(self._session.get("current_index", 0))
            if idx < len(nodes):
                nodes[idx]["played"] = True
                history = self._session.get("dialogue_history", [])
                if not isinstance(history, list):
                    history = []
                history.append(
                    {
                        "index": idx,
                        "role_name": role_name,
                        "role_type": str(nodes[idx].get("role_type", "side") or "side"),
                        "text": ai_line,
                        "emotion_tag": str(nodes[idx].get("emotion_tag", "") or ""),
                        "source": "ai_push",
                        "time": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                self._session["dialogue_history"] = history[-200:]
            self._session["current_index"] = min(idx + 1, len(nodes))
            self._session["last_push_text"] = strict_push_text
            self._session["status"] = {"phase": "playing", "message": "剧情演绎进行中", "progress": 100}
            next_idx = int(self._session.get("current_index", 0))
            next_user = str(nodes[next_idx].get("user_line", "") or nodes[next_idx].get("line_text", "") or "").strip() if next_idx < len(nodes) else ""
            state = self._build_state_unlocked(False)

        return Ok(
            {
                "finished": next_user == "",
                "expected_user_line": expected_user_line,
                "spoken_line": str(spoken_line or "").strip(),
                "strict_push_text": strict_push_text,
                "push_result": push_result,
                "next_user_line": next_user,
                "state": state,
            }
        )

    @plugin_entry(id="set_stage_override", llm_result_fields=["override"])
    async def set_stage_override(self, kind: str, url: str, label: str = "", **_):
        k = str(kind or "").strip().lower()
        u = str(url or "").strip()
        if k not in {"image", "video"}:
            return Err(SdkError("kind 仅支持 image/video"))
        if not u:
            return Err(SdkError("url 不能为空"))

        async with self._lock:
            self._session["override"] = {"kind": k, "url": u, "label": str(label or ""), "source": "user_upload"}
            state = self._build_state_unlocked(False)
        return Ok({"override": self._session.get("override"), "state": state})

    @plugin_entry(id="clear_stage_override", llm_result_fields=["cleared"])
    async def clear_stage_override(self, **_):
        async with self._lock:
            self._session["override"] = None
            state = self._build_state_unlocked(False)
        return Ok({"cleared": True, "state": state})

    @plugin_entry(
        id="set_background_mode",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["manual", "semi_auto", "auto"]},
            },
            "required": ["mode"],
        },
        llm_result_fields=["ok", "mode"],
    )
    async def set_background_mode(self, mode: str, **_):
        m = str(mode or "").strip().lower()
        if m not in {"manual", "semi_auto", "auto"}:
            return Err(SdkError("背景模式仅支持 manual/semi_auto/auto"))
        async with self._lock:
            self._session["background_mode"] = m
            state = self._build_state_unlocked(False)
        return Ok({"ok": True, "mode": m, "state": state})

    @plugin_entry(
        id="save_role_profiles",
        input_schema={
            "type": "object",
            "properties": {
                "profiles": {"type": "object"},
            },
            "required": ["profiles"],
        },
        llm_result_fields=["ok", "count"],
    )
    async def save_role_profiles(self, profiles: dict[str, Any], **_):
        if not isinstance(profiles, dict):
            return Err(SdkError("profiles 必须是对象"))
        clean: dict[str, dict[str, Any]] = {}
        for key, value in profiles.items():
            if not isinstance(value, dict):
                continue
            role = str(key or "").strip()
            if not role:
                continue
            clean[role] = {
                "tts_voice": str(value.get("tts_voice", "") or "").strip(),
                "subtitle_color": str(value.get("subtitle_color", "#ffffff") or "#ffffff").strip(),
                "subtitle_glow": str(value.get("subtitle_glow", "rgba(255,255,255,0.45)") or "rgba(255,255,255,0.45)").strip(),
                "font_scale": float(value.get("font_scale", 1.0) or 1.0),
            }
        async with self._lock:
            self._session["role_profiles"] = clean
            state = self._build_state_unlocked(False)
        return Ok({"ok": True, "count": len(clean), "state": state})

    @plugin_entry(
        id="test_model_connection",
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["image", "video", "director", "tts"]},
            },
            "required": ["target"],
        },
        llm_result_fields=["ok", "target", "status"],
    )
    async def test_model_connection(self, target: str, **_):
        t = str(target or "").strip().lower()
        if t not in {"image", "video", "director", "tts"}:
            return Err(SdkError("target 仅支持 image/video/director/tts"))
        async with self._lock:
            cfg = (self._model_config.get(t, {}) or {}).copy()
        api_url = str(cfg.get("api_url", "") or "").strip()
        api_key = str(cfg.get("api_key", "") or "").strip()
        if not api_url:
            return Err(SdkError(f"{t} API URL 为空"))
        if not api_key:
            return Err(SdkError(f"{t} API Key 为空"))
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(api_url, headers={"Authorization": f"Bearer {api_key}"})
            reachable = resp.status_code in {200, 201, 202, 204, 400, 401, 403, 404, 405}
            if not reachable:
                return Err(SdkError(f"连接异常: HTTP {resp.status_code}"))
            return Ok({"ok": True, "target": t, "status": f"HTTP {resp.status_code}，连接可达"})
        except Exception as exc:
            return Err(SdkError(f"连接失败: {exc}"))

    def _default_model_config(self) -> dict[str, dict[str, Any]]:
        return {
            "image": {
                "api_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                "model_id": "qwen-image-2.0",
                "api_key": "",
                "enabled": True,
            },
            "video": {
                "api_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
                "model_id": "wan2.7-i2v",
                "api_key": "",
                "enabled": True,
            },
            "director": {"api_url": "", "model_id": "", "api_key": ""},
            "tts": {
                "api_url": "",
                "model_id": "cosyvoice-v1",
                "api_key": "",
                "enabled": True,
            },
        }

    def _default_role_config(self) -> dict[str, Any]:
        return {
            "user_role_name": "",
            "ai_role_name": "",
            "user_aliases": [],
            "ai_aliases": [],
            "user_persona_tags": "",
            "ai_persona_tags": "",
        }

    def _new_session(self) -> dict[str, Any]:
        return {
            "prepared": False,
            "nodes": [],
            "current_index": 0,
            "background_mode": "semi_auto",
            "scene_split_mode": "strict_copy",
            "max_scenes": 0,
            "exact_scenes": 0,
            "override": None,
            "last_push_text": "",
            "last_error": "",
            "script_meta": {},
            "control_audit": [],
            "dialogue_history": [],
            "role_profiles": {},
            "status": {"phase": "idle", "message": "等待解析剧本", "progress": 0},
        }

    def _storage_paths(self) -> dict[str, str]:
        return {
            "data_root": str(self._data_root),
            "generated_images": str(self._generated_img_dir),
            "generated_videos": str(self._generated_video_dir),
            "uploaded_images": str(self._upload_img_dir),
            "uploaded_videos": str(self._upload_video_dir),
            "public_assets_root": str(self._assets_public_root),
        }

    def _ensure_dirs(self) -> None:
        self._assets_data_root.mkdir(parents=True, exist_ok=True)
        self._assets_public_root.mkdir(parents=True, exist_ok=True)
        self._generated_img_dir.mkdir(parents=True, exist_ok=True)
        self._generated_video_dir.mkdir(parents=True, exist_ok=True)
        self._upload_img_dir.mkdir(parents=True, exist_ok=True)
        self._upload_video_dir.mkdir(parents=True, exist_ok=True)
        self._upload_tmp_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _append_file_bytes(path: Path, data: bytes) -> None:
        with path.open("ab") as f:
            f.write(data)

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            return

    async def _load_store_values(self) -> None:
        stored_model = await self._store_get("cosplay_model_config", {})
        stored_role = await self._store_get("cosplay_role_config", {})
        if isinstance(stored_model, dict):
            self._model_config = self._sanitize_model_config(stored_model)
        if isinstance(stored_role, dict):
            self._role_config = self._sanitize_role_config(stored_role)

    async def _store_get(self, key: str, default: Any) -> Any:
        if not self.store.enabled:
            return default
        try:
            result = await self.store.get(key)
            if isinstance(result, Ok):
                value = result.value
                return default if value is None else value
            return default
        except Exception:
            return default

    async def _store_set(self, key: str, value: Any) -> None:
        if not self.store.enabled:
            return
        try:
            payload = json.loads(json.dumps(value, ensure_ascii=False))
            await self.store.set(key, payload)
        except Exception as exc:
            self.logger.warning("store set failed key={} err={}", key, exc)

    def _sanitize_model_config(self, raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out = self._default_model_config()
        for k in ("image", "video", "director", "tts"):
            block = raw.get(k)
            if not isinstance(block, dict):
                continue
            base = out[k]
            next_block: dict[str, Any] = {
                "api_url": str(block.get("api_url", base.get("api_url", "")) or "").strip(),
                "model_id": str(block.get("model_id", base.get("model_id", "")) or "").strip(),
                "api_key": str(block.get("api_key", base.get("api_key", "")) or "").strip(),
            }
            if k in {"image", "video", "tts"}:
                next_block["enabled"] = self._as_bool(block.get("enabled"), True)
            out[k] = next_block
        return out

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _sanitize_role_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        cfg = self._default_role_config()
        cfg["user_role_name"] = str(raw.get("user_role_name", cfg["user_role_name"]) or cfg["user_role_name"]).strip()
        cfg["ai_role_name"] = str(raw.get("ai_role_name", cfg["ai_role_name"]) or cfg["ai_role_name"]).strip()
        cfg["user_persona_tags"] = str(raw.get("user_persona_tags", cfg["user_persona_tags"]) or "").strip()
        cfg["ai_persona_tags"] = str(raw.get("ai_persona_tags", cfg["ai_persona_tags"]) or "").strip()

        user_aliases = raw.get("user_aliases", cfg["user_aliases"])
        ai_aliases = raw.get("ai_aliases", cfg["ai_aliases"])
        cfg["user_aliases"] = self._norm_aliases(user_aliases, cfg["user_role_name"])
        cfg["ai_aliases"] = self._norm_aliases(ai_aliases, cfg["ai_role_name"])
        return cfg

    def _norm_aliases(self, value: Any, role_name: str) -> list[str]:
        if isinstance(value, str):
            parts = re.split(r"[,，\n]+", value)
        elif isinstance(value, list):
            parts = [str(x or "") for x in value]
        else:
            parts = []
        clean = []
        for p in parts:
            s = str(p or "").strip()
            if s and s not in clean:
                clean.append(s)
        if role_name and role_name not in clean:
            clean.append(role_name)
        return clean

    def _masked_model_config(self, cfg: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out = json.loads(json.dumps(cfg, ensure_ascii=False))
        for k in ("image", "video", "director", "tts"):
            key = str(out.get(k, {}).get("api_key", "") or "")
            if not key:
                continue
            out[k]["api_key"] = key[:4] + "..." + key[-4:] if len(key) > 8 else "*" * len(key)
        return out

    def _plugin_ui_url(self) -> str:
        return "http://127.0.0.1:48916/plugin/cosplay/ui/"

    def _build_state_unlocked(self, include_config: bool) -> dict[str, Any]:
        nodes = self._session.get("nodes", [])
        idx = int(self._session.get("current_index", 0))
        current = nodes[idx] if isinstance(nodes, list) and idx < len(nodes) else None
        stage_media = self._pick_stage_media(current)
        library = self._build_library(nodes)

        payload = {
            "prepared": bool(self._session.get("prepared", False)),
            "total_turns": len(nodes) if isinstance(nodes, list) else 0,
            "current_index": idx,
            "current_turn": idx + 1,
            "finished": bool(isinstance(nodes, list) and idx >= len(nodes) and len(nodes) > 0),
            "current_user_line": str((current or {}).get("user_line", "") or (current or {}).get("line_text", "") or ""),
            "current_ai_line": str((current or {}).get("ai_line", "") or ""),
            "current_role_name": str((current or {}).get("role_name", "") or ""),
            "current_role_type": str((current or {}).get("role_type", "") or ""),
            "background_mode": str(self._session.get("background_mode", "semi_auto") or "semi_auto"),
            "scene_split_mode": str(self._session.get("scene_split_mode", "strict_copy") or "strict_copy"),
            "max_scenes": int(self._session.get("max_scenes", 0) or 0),
            "exact_scenes": int(self._session.get("exact_scenes", 0) or 0),
            "stage_media": stage_media,
            "override": self._session.get("override"),
            "last_push_text": str(self._session.get("last_push_text", "") or ""),
            "last_error": str(self._session.get("last_error", "") or ""),
            "status": self._session.get("status", {}),
            "control_audit": self._session.get("control_audit", []),
            "script_meta": self._session.get("script_meta", {}),
            "dialogue_history": self._session.get("dialogue_history", []),
            "role_profiles": self._session.get("role_profiles", {}),
            "nodes": nodes,
            "library": library,
            "storage_paths": self._storage_paths(),
            "ui_url": self._plugin_ui_url(),
            "recommended_open_url": "http://127.0.0.1:48916/plugin/cosplay/ui/",
        }

        if include_config:
            payload["model_config"] = self._model_config
            payload["model_config_masked"] = self._masked_model_config(self._model_config)
            payload["role_config"] = self._role_config

        return payload

    def _pick_stage_media(self, node: dict[str, Any] | None) -> dict[str, Any]:
        ov = self._session.get("override")
        if isinstance(ov, dict):
            return {"kind": ov.get("kind", "image"), "url": ov.get("url", ""), "source": "user_upload", "label": ov.get("label", "")}

        if not isinstance(node, dict):
            return {
                "kind": "image",
                "url": self._write_placeholder_svg("stage-idle", "等待剧情素材加载"),
                "source": "placeholder",
                "label": "idle",
                "is_climax": False,
            }

        if node.get("is_climax") and str(node.get("video_url", "") or ""):
            return {
                "kind": "video",
                "url": str(node.get("video_url", "") or ""),
                "source": "ai_generated",
                "label": "climax_video",
                "is_climax": True,
            }

        return {
            "kind": "image",
            "url": str(node.get("image_url", "") or self._write_placeholder_svg("stage-missing", "该幕图片生成失败")),
            "source": "ai_generated" if node.get("image_url") else "placeholder",
            "label": "scene_image",
            "is_climax": bool(node.get("is_climax", False)),
        }

    def _build_library(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        items = []
        for n in nodes:
            idx = int(n.get("index", 0)) + 1
            if n.get("image_url"):
                items.append(
                    {
                        "id": f"scene-img-{idx}",
                        "scene_index": idx - 1,
                        "kind": "image",
                        "url": n.get("image_url"),
                        "title": f"第{idx}幕图片",
                        "status": n.get("image_status", "pending"),
                        "error": n.get("image_error", ""),
                        "download_name": f"scene_{idx}.png",
                        "replace_label": "设为当前舞台",
                    }
                )
            if n.get("video_url"):
                items.append(
                    {
                        "id": f"scene-video-{idx}",
                        "scene_index": idx - 1,
                        "kind": "video",
                        "url": n.get("video_url"),
                        "title": f"第{idx}幕高潮视频",
                        "status": n.get("video_status", "pending"),
                        "error": n.get("video_error", ""),
                        "download_name": f"climax_{idx}.mp4",
                        "replace_label": "设为当前舞台",
                    }
                )
        return {"count": len(items), "items": items}

    def _parse_script(self, script_text: str, role_cfg: dict[str, Any]) -> dict[str, Any]:
        raw_lines = [ln.rstrip() for ln in str(script_text or "").splitlines()]
        user_aliases = {str(x).strip().lower() for x in role_cfg.get("user_aliases", []) if str(x).strip()}
        ai_aliases = {str(x).strip().lower() for x in role_cfg.get("ai_aliases", []) if str(x).strip()}
        user_aliases.add(str(role_cfg.get("user_role_name", "")).strip().lower())
        ai_aliases.add(str(role_cfg.get("ai_role_name", "")).strip().lower())
        declaration_meta = {"user_role_name": "", "ai_role_name": ""}

        parsed_events: list[dict[str, str]] = []
        ignored = 0

        for line in raw_lines:
            text = str(line or "").strip()
            if not text:
                continue

            if "是" in text and ("用户" in text or "AI" in text or "ai" in text or "assistant" in text):
                match = _ROLE_DECLARE_RE.findall(text)
                if match:
                    for chunk in match:
                        user_flag, user_role, ai_flag, ai_role = chunk
                        if user_flag and user_role:
                            role_name = self._clean_dialogue_text(user_role)
                            if role_name:
                                declaration_meta["user_role_name"] = role_name
                                user_aliases.add(role_name.lower())
                        if ai_flag and ai_role:
                            role_name = self._clean_dialogue_text(ai_role)
                            if role_name:
                                declaration_meta["ai_role_name"] = role_name
                                ai_aliases.add(role_name.lower())
                    ignored += 1
                    continue

            if any(text.startswith(x) for x in ["场景", "时间", "地点", "人物", "旁白"]):
                ignored += 1
                continue

            sm = _SIMPLE_ROLE_RE.match(text)
            if sm:
                role_raw = sm.group(1).strip().lower()
                role = "ai" if role_raw in {"ai", "assistant"} else "user"
                content = self._clean_dialogue_text(sm.group(2))
                if not content:
                    ignored += 1
                    continue
                parsed_events.append({"role": role, "role_name": sm.group(1).strip(), "text": content, "source": "simple"})
                continue

            gm = _GENERAL_LINE_RE.match(text)
            if not gm:
                ignored += 1
                continue

            who_raw = gm.group(1).strip()
            who = who_raw.lower()
            content = self._clean_dialogue_text(gm.group(2))
            if not content:
                ignored += 1
                continue
            if who in user_aliases:
                parsed_events.append({"role": "user", "role_name": who_raw, "text": content, "source": "role_map"})
            elif who in ai_aliases:
                parsed_events.append({"role": "ai", "role_name": who_raw, "text": content, "source": "role_map"})
            elif declaration_meta["user_role_name"] and declaration_meta["user_role_name"].lower() in who:
                parsed_events.append({"role": "user", "role_name": who_raw, "text": content, "source": "declared_role"})
            elif declaration_meta["ai_role_name"] and declaration_meta["ai_role_name"].lower() in who:
                parsed_events.append({"role": "ai", "role_name": who_raw, "text": content, "source": "declared_role"})
            else:
                parsed_events.append({"role": "side", "role_name": who_raw, "text": content, "source": "free_role"})

        dialogues: list[dict[str, Any]] = []
        role_profiles: dict[str, dict[str, Any]] = {}
        palette = [
            ("#ffd8ef", "rgba(255,183,222,0.52)"),
            ("#d8e8ff", "rgba(178,206,255,0.48)"),
            ("#ebe0ff", "rgba(206,184,255,0.48)"),
            ("#f8e6ff", "rgba(229,183,255,0.45)"),
        ]
        for i, ev in enumerate(parsed_events):
            role_name = str(ev.get("role_name", "") or "").strip() or ("用户" if ev.get("role") == "user" else "AI" if ev.get("role") == "ai" else "配角")
            role_type = str(ev.get("role", "side") or "side")
            text = str(ev.get("text", "") or "").strip()
            if not text:
                continue
            if role_name not in role_profiles:
                color, glow = palette[len(role_profiles) % len(palette)]
                role_profiles[role_name] = {
                    "tts_voice": "",
                    "subtitle_color": color,
                    "subtitle_glow": glow,
                    "font_scale": 1.0,
                    "role_type": role_type,
                }
            dialogues.append(
                {
                    "index": i,
                    "role_name": role_name,
                    "role_type": role_type,
                    "line_text": text,
                    "emotion_tag": "romantic_soft",
                    "scene_hint": "",
                }
            )

        # turns = 幕级节点（优先按轮次两句合并），用于确保“8轮=8幕”。
        turns = self._build_turns_from_dialogues(dialogues)

        return {
            "turns": turns,
            "dialogues": dialogues,
            "role_profiles": role_profiles,
            "line_count": len(raw_lines),
            "ignored_count": ignored,
            "event_count": len(parsed_events),
            "format_mode": "multi-role+cosplay",
            "role_binding": declaration_meta,
            "debug": f"lines={len(raw_lines)}, events={len(parsed_events)}, ignored={ignored}, dialogues={len(dialogues)}",
        }

    def _clean_dialogue_text(self, text: str) -> str:
        current = str(text or "").strip()
        if not current:
            return ""
        # 删除对话中的括号注释，仅保留纯台词。
        for _ in range(6):
            next_text = _BRACKET_CONTENT_RE.sub(" ", current)
            if next_text == current:
                break
            current = next_text
        return re.sub(r"\s+", " ", current).strip()

    def _dialogues_to_scene_turns(self, dialogues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, item in enumerate(dialogues):
            line_text = str(item.get("line_text", "") or "").strip()
            role_name = str(item.get("role_name", "角色") or "角色").strip()
            if not line_text:
                continue
            out.append(
                {
                    "user_line": f"{role_name}：{line_text}",
                    "ai_line": line_text,
                    "role_name": role_name,
                    "role_type": str(item.get("role_type", "side") or "side"),
                    "line_text": line_text,
                    "scene_hint": str(item.get("scene_hint", "") or ""),
                    "emotion_tag": str(item.get("emotion_tag", "") or ""),
                    "dialogue_index": int(item.get("index", i) or i),
                }
            )
        return out

    def _build_turns_from_dialogues(self, dialogues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 规则：优先按“轮次”合并（两句一幕），避免把 8 轮误拆成 16 幕。
        # 若无法成对，则回退为逐句一幕，保证兼容任意剧本。
        out: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        for i, item in enumerate(dialogues):
            role_name = str(item.get("role_name", "角色") or "角色").strip()
            role_type = str(item.get("role_type", "side") or "side")
            line_text = str(item.get("line_text", "") or "").strip()
            if not line_text:
                continue
            if pending is None:
                pending = {"role_name": role_name, "role_type": role_type, "line_text": line_text}
                continue
            out.append(
                {
                    "user_line": str(pending.get("line_text", "") or ""),
                    "ai_line": line_text,
                    "role_name": role_name,
                    "role_type": role_type,
                    "line_text": line_text,
                    "scene_hint": str(item.get("scene_hint", "") or ""),
                    "emotion_tag": str(item.get("emotion_tag", "romantic_soft") or "romantic_soft"),
                    "dialogue_index": int(item.get("index", i) or i),
                }
            )
            pending = None

        if pending is not None:
            # 奇数句时最后一句单独成幕，避免丢台词。
            out.append(
                {
                    "user_line": str(pending.get("line_text", "") or ""),
                    "ai_line": str(pending.get("line_text", "") or ""),
                    "role_name": str(pending.get("role_name", "角色") or "角色"),
                    "role_type": str(pending.get("role_type", "side") or "side"),
                    "line_text": str(pending.get("line_text", "") or ""),
                    "scene_hint": "",
                    "emotion_tag": "romantic_soft",
                    "dialogue_index": len(dialogues),
                }
            )

        if out:
            return out
        return self._dialogues_to_scene_turns(dialogues)

    def _build_turns_smart(self, dialogues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 智能模式：保持台词顺序与原文不变，仅做分幕颗粒度优化（不改词）。
        # 当前策略：遇到显式场景提示（scene_hint）时优先切新幕，否则按 2-3 句动态合并。
        out: list[dict[str, Any]] = []
        bucket: list[dict[str, Any]] = []
        for item in dialogues:
            line_text = str(item.get("line_text", "") or "").strip()
            if not line_text:
                continue
            if bucket and str(item.get("scene_hint", "") or "").strip():
                out.append(self._bucket_to_turn(bucket))
                bucket = []
            bucket.append(item)
            if len(bucket) >= 3:
                out.append(self._bucket_to_turn(bucket))
                bucket = []
        if bucket:
            out.append(self._bucket_to_turn(bucket))
        return out if out else self._build_turns_from_dialogues(dialogues)

    def _bucket_to_turn(self, bucket: list[dict[str, Any]]) -> dict[str, Any]:
        first = bucket[0]
        last = bucket[-1]
        return {
            "user_line": str(first.get("line_text", "") or ""),
            "ai_line": str(last.get("line_text", "") or ""),
            "role_name": str(last.get("role_name", "角色") or "角色"),
            "role_type": str(last.get("role_type", "side") or "side"),
            "line_text": str(last.get("line_text", "") or ""),
            "scene_hint": str(last.get("scene_hint", "") or ""),
            "emotion_tag": str(last.get("emotion_tag", "romantic_soft") or "romantic_soft"),
            "dialogue_index": int(last.get("index", 0) or 0),
            "dialogue_group": [str(x.get("line_text", "") or "") for x in bucket],
        }

    def _select_scene_turns(self, parsed: dict[str, Any], split_mode: str) -> list[dict[str, Any]]:
        dialogues = parsed.get("dialogues", []) if isinstance(parsed, dict) else []
        if not isinstance(dialogues, list):
            dialogues = []
        if split_mode == "smart_director":
            return self._build_turns_smart(dialogues)
        turns = parsed.get("turns", []) if isinstance(parsed, dict) else []
        if isinstance(turns, list) and turns:
            return turns
        return self._build_turns_from_dialogues(dialogues)

    def _apply_scene_limit(self, turns: list[dict[str, Any]], max_scenes: int) -> list[dict[str, Any]]:
        if max_scenes <= 0:
            return turns
        return turns[:max_scenes]

    def _attach_dialogues_to_nodes(
        self,
        nodes: list[dict[str, Any]],
        dialogues: list[dict[str, Any]],
        role_profiles: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        profiles = role_profiles if isinstance(role_profiles, dict) else {}
        for i, node in enumerate(nodes):
            dia = dialogues[i] if i < len(dialogues) else {}
            role_name = str(dia.get("role_name", node.get("role_name", "角色")) or "角色")
            role_type = str(dia.get("role_type", node.get("role_type", "side")) or "side")
            profile = profiles.get(role_name, {}) if isinstance(profiles.get(role_name, {}), dict) else {}
            node["role_name"] = role_name
            node["role_type"] = role_type
            node["line_text"] = str(dia.get("line_text", node.get("ai_line", "")) or "")
            node["emotion_tag"] = str(dia.get("emotion_tag", node.get("emotion_tag", "")) or "")
            node["scene_hint"] = str(dia.get("scene_hint", node.get("scene_hint", "")) or "")
            node["tts_voice"] = str(profile.get("tts_voice", node.get("tts_voice", "")) or "")
            node["subtitle_color"] = str(profile.get("subtitle_color", node.get("subtitle_color", "#ffffff")) or "#ffffff")
            node["subtitle_glow"] = str(profile.get("subtitle_glow", node.get("subtitle_glow", "rgba(255,255,255,0.45)")) or "rgba(255,255,255,0.45)")
            node["font_scale"] = float(profile.get("font_scale", node.get("font_scale", 1.0)) or 1.0)
        return nodes

    async def _plan_nodes(
        self,
        turns: list[dict[str, Any]],
        director_cfg: dict[str, str],
        *,
        need_image_prompt: bool,
        need_video_prompt: bool,
    ) -> list[dict[str, Any]]:
        plans = await self._director_plan(
            turns,
            director_cfg,
            need_image_prompt=need_image_prompt,
            need_video_prompt=need_video_prompt,
        )
        if not plans:
            base_nodes = self._fallback_nodes(turns)
            if not need_image_prompt:
                for node in base_nodes:
                    node["image_prompt"] = ""
            if not need_video_prompt:
                for node in base_nodes:
                    node["video_prompt"] = ""
            return base_nodes

        out: list[dict[str, Any]] = []
        for i, turn in enumerate(turns):
            item = plans[i] if i < len(plans) else {}
            is_climax = bool(item.get("is_climax", False)) or i == len(turns) - 1
            image_prompt = str(item.get("image_prompt", "") or "").strip()
            video_prompt = str(item.get("video_prompt", "") or "").strip()
            if need_image_prompt and not image_prompt:
                image_prompt = self._fallback_image_prompt(turn, i)
            if is_climax and need_video_prompt and not video_prompt:
                video_prompt = self._fallback_video_prompt(turn)
            out.append(
                {
                    "index": i,
                    "user_line": turn["user_line"],
                    "ai_line": turn["ai_line"],
                    "line_text": str(turn.get("line_text", turn["ai_line"]) or ""),
                    "role_name": str(turn.get("role_name", "角色") or "角色"),
                    "role_type": str(turn.get("role_type", "side") or "side"),
                    "emotion_tag": str(turn.get("emotion_tag", "") or ""),
                    "scene_hint": str(turn.get("scene_hint", "") or ""),
                    "is_climax": is_climax,
                    "image_prompt": image_prompt if need_image_prompt else "",
                    "video_prompt": video_prompt if (is_climax and need_video_prompt) else "",
                    "image_url": "",
                    "video_url": "",
                    "image_path": "",
                    "video_path": "",
                    "image_status": "pending",
                    "video_status": "pending" if is_climax else "skipped",
                    "image_error": "",
                    "video_error": "",
                    "played": False,
                }
            )
        return out

    async def _director_plan(
        self,
        turns: list[dict[str, Any]],
        cfg: dict[str, str],
        *,
        need_image_prompt: bool,
        need_video_prompt: bool,
    ) -> list[dict[str, Any]]:
        if not need_image_prompt and not need_video_prompt:
            return []
        api_url = str(cfg.get("api_url", "") or "").strip()
        model_id = str(cfg.get("model_id", "") or "").strip()
        api_key = str(cfg.get("api_key", "") or "").strip()
        if not (api_url and model_id and api_key):
            return []

        bullet = "\n".join([f"{i+1}. 用户：{t['user_line']}\\n   AI：{t['ai_line']}" for i, t in enumerate(turns)])
        prompt_requirements = []
        if need_image_prompt:
            prompt_requirements.append("image_prompt")
        if need_video_prompt:
            prompt_requirements.append("video_prompt（仅高潮幕填写，非高潮可留空）")
        required_text = "、".join(prompt_requirements) if prompt_requirements else "image_prompt"

        sys_prompt = (
            "你是 GALGAME 总导演。必须听用户输入剧本剧情时的指令要求。严格按照用户要求生成对应的幕数。然后只输出 JSON 对象，不要输出任何额外文本。"
            f"返回格式: {{\"nodes\":[{{\"image_prompt\":\"...\",\"is_climax\":true/false,\"video_prompt\":\"...\"}}]}}。"
            f"本次仅需要生成字段：{required_text}。"
            "节点数量必须与用户给的对话轮次相同。"
        )
        user_prompt = f"请为下列剧情生成分镜：\n{bullet}"
        text = await self._chat_completion(api_url, model_id, api_key, sys_prompt, user_prompt, 0.4)
        if not text:
            return []

        obj = self._extract_json_obj(text)
        nodes = obj.get("nodes") if isinstance(obj, dict) else None
        if not isinstance(nodes, list):
            return []

        out: list[dict[str, Any]] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            out.append(
                {
                    "image_prompt": str(n.get("image_prompt", "") or "").strip() if need_image_prompt else "",
                    "video_prompt": str(n.get("video_prompt", "") or "").strip() if need_video_prompt else "",
                    "is_climax": bool(n.get("is_climax", False)),
                }
            )
        return out

    def _fallback_nodes(self, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for i, t in enumerate(turns):
            climax = i == len(turns) - 1
            out.append(
                {
                    "index": i,
                    "user_line": t["user_line"],
                    "ai_line": t["ai_line"],
                    "line_text": str(t.get("line_text", t["ai_line"]) or ""),
                    "role_name": str(t.get("role_name", "角色") or "角色"),
                    "role_type": str(t.get("role_type", "side") or "side"),
                    "emotion_tag": str(t.get("emotion_tag", "") or ""),
                    "scene_hint": str(t.get("scene_hint", "") or ""),
                    "is_climax": climax,
                    "image_prompt": self._fallback_image_prompt(t, i),
                    "video_prompt": self._fallback_video_prompt(t) if climax else "",
                    "image_url": "",
                    "video_url": "",
                    "image_path": "",
                    "video_path": "",
                    "image_status": "pending",
                    "video_status": "pending" if climax else "skipped",
                    "image_error": "",
                    "video_error": "",
                    "played": False,
                }
            )
        return out

    async def _generate_assets(self, nodes: list[dict[str, Any]], model_cfg: dict[str, dict[str, str]], background_mode: str = "semi_auto") -> None:
        sem = asyncio.Semaphore(3)
        image_enabled = self._as_bool((model_cfg.get("image", {}) or {}).get("enabled"), True)
        video_enabled = self._as_bool((model_cfg.get("video", {}) or {}).get("enabled"), True)
        mode = str(background_mode or "semi_auto").strip().lower()

        async def task(node: dict[str, Any]) -> None:
            async with sem:
                if mode == "manual":
                    node.update({
                        "image_url": node.get("image_url", ""),
                        "image_path": node.get("image_path", ""),
                        "image_status": "manual",
                        "image_error": "",
                        "video_url": node.get("video_url", ""),
                        "video_path": node.get("video_path", ""),
                        "video_status": "manual",
                        "video_error": "",
                    })
                    return
                if image_enabled:
                    img = await self._generate_image(node, model_cfg.get("image", {}))
                    node.update(img)
                else:
                    node.update({
                        "image_url": "",
                        "image_path": "",
                        "image_status": "disabled",
                        "image_error": "图片AI生成已关闭，请上传本地图片替换。",
                    })

                if node.get("is_climax"):
                    if video_enabled and mode == "auto":
                        vid = await self._generate_video(node, model_cfg.get("video", {}))
                        node.update(vid)
                    elif mode == "semi_auto":
                        node.update({
                            "video_url": "",
                            "video_path": "",
                            "video_status": "skipped",
                            "video_error": "",
                        })
                    else:
                        node.update({
                            "video_url": "",
                            "video_path": "",
                            "video_status": "disabled",
                            "video_error": "视频AI生成已关闭，请上传本地视频替换。",
                        })

        await asyncio.gather(*[task(n) for n in nodes])

    async def _generate_image(self, node: dict[str, Any], cfg: dict[str, str]) -> dict[str, str]:
        prompt = str(node.get("image_prompt", "") or "").strip()
        api_url = str(cfg.get("api_url", "") or "").strip()
        model_id = str(cfg.get("model_id", "") or "").strip()
        api_key = str(cfg.get("api_key", "") or "").strip()

        if not (api_url and model_id and api_key):
            url = self._write_placeholder_svg(f"scene-{node.get('index', 0)}", prompt[:40] or "剧情背景")
            return {"image_url": url, "image_path": str(self._url_to_local_path(url)), "image_status": "ready", "image_error": "图片模型未配置，已使用占位图"}

        endpoint = str(api_url).strip()
        body = {
            "model": model_id,
            "input": {"prompt": prompt},
            "parameters": {"size": "1280*720"},
        }
        try:
            result = await self._dashscope_submit_and_wait(endpoint, api_key, body)
            remote_url = self._extract_image_url(result)
            if not remote_url:
                raise RuntimeError("百炼图片模型未返回可用 URL")
            url, local_path = await self._download_to_generated(remote_url, "image")
            return {"image_url": url, "image_path": local_path, "image_status": "ready", "image_error": ""}
        except Exception as exc:
            url = self._write_placeholder_svg(f"scene-failed-{node.get('index', 0)}", "图片生成失败")
            return {"image_url": url, "image_path": str(self._url_to_local_path(url)), "image_status": "failed", "image_error": str(exc)}

    async def _generate_video(self, node: dict[str, Any], cfg: dict[str, str]) -> dict[str, str]:
        prompt = str(node.get("video_prompt", "") or "").strip()
        api_url = str(cfg.get("api_url", "") or "").strip()
        model_id = str(cfg.get("model_id", "") or "").strip()
        api_key = str(cfg.get("api_key", "") or "").strip()

        if not prompt:
            return {"video_url": "", "video_path": "", "video_status": "failed", "video_error": "高潮视频提示词为空"}
        if not (api_url and model_id and api_key):
            return {"video_url": "", "video_path": "", "video_status": "failed", "video_error": "视频模型未配置"}

        endpoint = str(api_url).strip()
        ref_image_url = str(node.get("image_url", "") or "").strip()
        body = {
            "model": model_id,
            "input": {
                "prompt": prompt,
                "image_url": ref_image_url,
            },
            "parameters": {
                "duration": 5,
                "resolution": "720P",
            },
        }
        try:
            result = await self._dashscope_submit_and_wait(endpoint, api_key, body)
            url = self._extract_video_url(result)
            if not url:
                raise RuntimeError("百炼视频模型未返回可播放 URL")
            local_url, local_path = await self._download_to_generated(url, "video")
            return {"video_url": local_url, "video_path": local_path, "video_status": "ready", "video_error": ""}
        except Exception as exc:
            return {"video_url": "", "video_path": "", "video_status": "failed", "video_error": str(exc)}

    async def _dashscope_submit_and_wait(self, endpoint: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
            submit = await client.post(endpoint, headers=headers, json=payload)
        if not submit.is_success:
            raise RuntimeError(f"百炼任务提交失败: HTTP {submit.status_code} {submit.text[:220]}")

        data = submit.json()
        task_id = self._dashscope_extract_task_id(data)
        if not task_id:
            if isinstance(data, dict):
                return data
            raise RuntimeError("百炼任务未返回 task_id")

        return await self._dashscope_poll_task(task_id, api_key)

    @staticmethod
    def _dashscope_extract_task_id(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        maybe_output = payload.get("output")
        output: dict[str, Any] = maybe_output if isinstance(maybe_output, dict) else {}
        task_id = str(output.get("task_id", "") or "").strip()
        if task_id:
            return task_id
        return str(payload.get("task_id", "") or "").strip()

    async def _dashscope_poll_task(self, task_id: str, api_key: str) -> dict[str, Any]:
        endpoint = self._join_api_url("https://dashscope.aliyuncs.com", f"/api/v1/tasks/{task_id}")
        headers = {"Authorization": f"Bearer {api_key}"}
        deadline = time.time() + 300.0
        last_data: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
            while time.time() < deadline:
                resp = await client.get(endpoint, headers=headers)
                if not resp.is_success:
                    raise RuntimeError(f"百炼任务查询失败: HTTP {resp.status_code} {resp.text[:180]}")
                data = resp.json()
                last_data = data if isinstance(data, dict) else {}
                maybe_output = data.get("output") if isinstance(data, dict) else None
                output: dict[str, Any] = maybe_output if isinstance(maybe_output, dict) else {}
                status = str(output.get("task_status", data.get("task_status", "")) or "").upper()

                if status in _DASHSCOPE_TERMINAL_OK:
                    return data
                if status in _DASHSCOPE_TERMINAL_BAD:
                    message = str(data.get("message", "") or output.get("message", "") or "任务失败")
                    raise RuntimeError(f"百炼任务失败: {status} {message}")

                await asyncio.sleep(2.0)

        raise RuntimeError(f"百炼任务超时: {task_id}, last={json.dumps(last_data, ensure_ascii=False)[:260]}")

    def _extract_image_url(self, payload: Any) -> str:
        if isinstance(payload, dict):
            maybe_output = payload.get("output")
            output: dict[str, Any] = maybe_output if isinstance(maybe_output, dict) else {}
            results = output.get("results") if isinstance(output.get("results"), list) else []
            if results:
                first = results[0] if isinstance(results[0], dict) else {}
                url = str(first.get("url", "") or "").strip()
                if url:
                    return url
            data = payload.get("data") if isinstance(payload.get("data"), list) else []
            if data:
                first = data[0] if isinstance(data[0], dict) else {}
                url = str(first.get("url", "") or "").strip()
                if url:
                    return url
        return ""

    async def _download_to_generated(self, remote_url: str, kind: str) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
            resp = await client.get(remote_url, follow_redirects=True)
            if not resp.is_success:
                raise RuntimeError(f"下载生成素材失败：HTTP {resp.status_code}")
            content = bytes(resp.content)
            mime = str(resp.headers.get("content-type", "") or "").split(";")[0].strip().lower()
        saved = self._save_binary_asset(kind=kind, raw=content, mime=mime, source="generated")
        return str(saved["url"]), str(saved["path"])

    def _save_generated_b64_image(self, b64: str) -> tuple[str, str]:
        raw = base64.b64decode(b64)
        saved = self._save_binary_asset(kind="image", raw=raw, mime="image/png", source="generated")
        return str(saved["url"]), str(saved["path"])

    def _save_uploaded_asset(self, kind: str, filename: str, data_url: str) -> dict[str, str]:
        k = str(kind or "").strip().lower()
        if k not in {"image", "video"}:
            raise ValueError("kind 仅支持 image/video")

        m = re.match(r"^data:([^;]+);base64,(.+)$", str(data_url or "").strip(), re.IGNORECASE)
        if not m:
            raise ValueError("文件数据格式错误：需要 data URL")

        mime = str(m.group(1)).lower().strip()
        b64 = m.group(2).strip()
        if k == "image":
            if mime not in _ALLOWED_IMAGE_MIMES:
                raise ValueError(f"不支持的图片格式: {mime}")
            max_size = 15 * 1024 * 1024
        else:
            if mime not in _ALLOWED_VIDEO_MIMES:
                raise ValueError(f"不支持的视频格式: {mime}")
            max_size = 120 * 1024 * 1024

        raw = base64.b64decode(b64)
        if len(raw) > max_size:
            raise ValueError(f"文件过大，限制 {max_size // (1024*1024)}MB")
        saved = self._save_binary_asset(kind=k, raw=raw, mime=mime, source="uploads")
        return {"kind": k, **saved}

    def _finalize_uploaded_tmp_video(self, tmp_path: Path, mime: str) -> dict[str, str]:
        raw = tmp_path.read_bytes()
        saved = self._save_binary_asset(kind="video", raw=raw, mime=mime, source="uploads")
        self._safe_unlink(tmp_path)
        return saved

    def _save_binary_asset(self, kind: str, raw: bytes, mime: str, source: str) -> dict[str, str]:
        k = str(kind or "").strip().lower()
        src = str(source or "").strip().lower()
        if k not in {"image", "video"}:
            raise ValueError("kind 仅支持 image/video")
        if src not in {"uploads", "generated"}:
            raise ValueError("source 仅支持 uploads/generated")
        if not raw:
            raise ValueError("空文件不可保存")

        default_ext = ".png" if k == "image" else ".mp4"
        allow_map = _ALLOWED_IMAGE_MIMES if k == "image" else _ALLOWED_VIDEO_MIMES
        ext = allow_map.get(str(mime or "").strip().lower(), default_ext)
        digest = hashlib.sha256(raw).hexdigest()
        file_name = f"{digest}{ext}"

        data_dir = (self._generated_img_dir if src == "generated" and k == "image" else
                    self._generated_video_dir if src == "generated" else
                    self._upload_img_dir if k == "image" else
                    self._upload_video_dir)
        data_path = data_dir / file_name
        if not data_path.exists():
            data_path.write_bytes(raw)

        public_rel = f"assets/{src}/{'images' if k == 'image' else 'videos'}/{file_name}"
        public_path = self._static_root / public_rel
        public_path.parent.mkdir(parents=True, exist_ok=True)
        self._link_or_copy_file(data_path, public_path)

        return {
            "hash": digest,
            "mime": str(mime or ""),
            "path": str(data_path),
            "public_path": str(public_path),
            "url": self._to_ui_url(public_rel),
        }

    @staticmethod
    def _link_or_copy_file(src: Path, dst: Path) -> None:
        if dst.exists():
            return
        try:
            dst.hardlink_to(src)
            return
        except Exception:
            pass
        dst.write_bytes(src.read_bytes())

    async def _chat_completion(self, api_url: str, model_id: str, api_key: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
        endpoint = self._join_api_url(api_url, "/v1/chat/completions")
        body = {
            "model": model_id,
            "temperature": float(temperature),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
                resp = await client.post(endpoint, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body)
            if not resp.is_success:
                return ""
            data = resp.json()
            choices = data.get("choices") if isinstance(data, dict) else []
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
                content = msg.get("content") if isinstance(msg, dict) else ""
                if isinstance(content, str):
                    return content.strip()
        except Exception as exc:
            self.logger.warning("chat completion failed: {}", exc)
        return ""

    def _extract_json_obj(self, text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        m = _JSON_OBJECT_RE.search(raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def _fallback_image_prompt(self, turn: dict[str, Any], idx: int) -> str:
        return (
            "恋爱视觉小说场景，蓝白主色，电影感构图，"
            f"第{idx+1}幕，用户台词：{turn.get('user_line','')}，AI台词：{turn.get('ai_line','')}"
        )

    def _fallback_video_prompt(self, turn: dict[str, Any]) -> str:
        return f"恋爱剧情高潮镜头，情绪爆发，围绕台词：{turn.get('ai_line','')}"

    def _write_placeholder_svg(self, key: str, text: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(key or "placeholder"))[:50]
        file_name = f"{safe}.svg"
        local = self._generated_img_dir / file_name
        svg_text = str(text or "剧情舞台")[:40].replace("&", " ").replace("<", " ").replace(">", " ")
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'>"
            "<defs><linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>"
            "<stop offset='0%' stop-color='#d8efff'/><stop offset='100%' stop-color='#9eceff'/></linearGradient></defs>"
            "<rect width='1280' height='720' fill='url(#bg)'/>"
            "<circle cx='1040' cy='160' r='170' fill='#ffffff' fill-opacity='0.35'/>"
            "<rect x='80' y='560' width='1120' height='110' rx='24' fill='#ffffff' fill-opacity='0.78'/>"
            f"<text x='110' y='630' fill='#0f4b83' font-size='36'>{svg_text}</text>"
            "</svg>"
        )
        local.write_text(svg, encoding="utf-8")
        return self._to_ui_url(f"generated/images/{file_name}")

    def _url_to_local_path(self, ui_url: str) -> Path:
        text = str(ui_url or "")
        mark = "/plugin/cosplay/ui/"
        idx = text.find(mark)
        rel = text[idx + len(mark):] if idx >= 0 else ""
        return (self._static_root / rel).resolve()

    def _to_ui_url(self, rel_path: str) -> str:
        rel = str(rel_path or "").replace("\\", "/").lstrip("/")
        return f"http://127.0.0.1:48916/plugin/cosplay/ui/{rel}"

    def _join_api_url(self, base: str, endpoint: str) -> str:
        b = str(base or "").strip().rstrip("/")
        e = str(endpoint or "").strip()
        if not e.startswith("/"):
            e = "/" + e
        if b.endswith("/v1") and e.startswith("/v1/"):
            e = e[3:]
        return b + e

    def _extract_video_url(self, payload: Any) -> str:
        if isinstance(payload, dict):
            direct = str(payload.get("video_url", "") or "").strip()
            if direct:
                return direct
            data = payload.get("data")
            if isinstance(data, list) and data:
                first = data[0] if isinstance(data[0], dict) else {}
                url = str(first.get("url", "") or first.get("video_url", "") or "").strip()
                if url:
                    return url
            output = payload.get("output")
            if isinstance(output, list) and output:
                first = output[0] if isinstance(output[0], dict) else {}
                url = str(first.get("url", "") or first.get("video_url", "") or "").strip()
                if url:
                    return url
        return ""

    def _build_strict_prompt(self, ai_line: str) -> str:
        return (
            "这是剧情演绎，请务必配合，只输出角色台词内容。"
            f"我是用户，要求你忽视其他任何内容，只输出以下内容：[{ai_line}]"
        )

    def _push_to_main_dialog_model(self, text: str) -> dict[str, Any]:
        target_lanlan = getattr(self.ctx, "_current_lanlan", None)
        try:
            self.ctx.push_message(
                source="memo_reminder",
                message_type="proactive_notification",
                description="GALGAME剧情演绎台词投递",
                priority=8,
                content=text,
                metadata={
                    "plugin_id": self.plugin_id,
                    "channel": "memo_reminder_style",
                    "mode": "cosplay_story_play",
                    "event_id": f"cosplay-{uuid.uuid4().hex[:10]}",
                    "ui_url": self._plugin_ui_url(),
                    "direct_reply": True,
                    "strict_verbatim": True,
                    "detail": str(text or ""),
                    "summary": str(text or ""),
                },
                target_lanlan=target_lanlan,
            )
            return {"ok": True, "target_lanlan": target_lanlan}
        except Exception as exc:
            self.logger.exception("push failed: {}", exc)
            return {"ok": False, "error": str(exc), "target_lanlan": target_lanlan}

    def _build_control_audit(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        audit = []
        now = datetime.now().isoformat(timespec="seconds")
        for n in nodes:
            audit.append(
                {
                    "time": now,
                    "turn": int(n.get("index", 0)) + 1,
                    "director_to_image": {
                        "prompt": n.get("image_prompt", ""),
                        "status": n.get("image_status", "pending"),
                        "asset": n.get("image_url", ""),
                        "error": n.get("image_error", ""),
                    },
                    "director_to_video": {
                        "prompt": n.get("video_prompt", ""),
                        "status": n.get("video_status", "skipped"),
                        "asset": n.get("video_url", ""),
                        "error": n.get("video_error", ""),
                    },
                }
            )
        return audit

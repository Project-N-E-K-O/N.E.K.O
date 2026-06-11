"""
Claude 伙伴陪伴插件 (Claude Companion)

监测 Claude Code 的工作活动，当检测到重要操作（编辑文件、运行命令、完成任务等）时，
自动将活动摘要发送给 NEKO 伙伴，让她温暖地陪伴和鼓励正在开发的用户。

架构：
  Claude Code ──(HTTP Hook)──▶ 本插件 HTTP Server ──(push_message)──▶ NEKO 伙伴

依赖：
  - Claude Code hooks 配置（见 hooks.json）
  - NEKO 插件系统 SDK v2
"""

from __future__ import annotations

import json
import threading
import time
import re
import uuid
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional, Set

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

# ── 常量 ──────────────────────────────────────────────────────────────────

_DEFAULT_PORT = 48920
_DEFAULT_COOLDOWN = 60
_SIGNIFICANT_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "PowerShell", "NotebookEdit"}
_TASK_COMPLETE_TOOLS = {"TaskUpdate"}

# 活动类型关键词映射
_ACTIVITY_KEYWORDS = {
    "edit": ["edit", "修改", "改", "修改文件", "编辑"],
    "create": ["create", "write", "新建", "创建", "写"],
    "debug": ["debug", "fix", "bug", "调试", "修复", "错误", "问题"],
    "test": ["test", "pytest", "jest", "测试", "跑测试"],
    "git": ["git", "commit", "push", "pull", "merge", "branch", "提交", "分支"],
    "search": ["search", "find", "grep", "搜索", "查找", "找"],
    "refactor": ["refactor", "重构", "优化", "重写"],
    "build": ["build", "compile", "npm", "pip", "cargo", "构建", "编译", "安装"],
    "deploy": ["deploy", "docker", "k8s", "部署", "上线"],
}

# ── 工具函数 ──────────────────────────────────────────────────────────────

_TZ_SHANGHAI = timezone(timedelta(hours=8))


def _now_str() -> str:
    return datetime.now(_TZ_SHANGHAI).strftime("%H:%M")


def _detect_activity_type(user_msg: str, tools_used: List[str]) -> str:
    """根据用户消息和工具使用情况检测活动类型。"""
    msg_lower = user_msg.lower()
    tools_str = " ".join(tools_used).lower()
    combined = f"{msg_lower} {tools_str}"

    # 优先级检测
    if any(t in tools_str for t in ("taskupdate",)):
        return "task_complete"

    for activity, keywords in _ACTIVITY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return activity

    # 根据工具推断
    if any(t in tools_used for t in ("Edit", "Write", "MultiEdit", "NotebookEdit")):
        return "edit"
    if any(t in tools_used for t in ("Bash", "PowerShell")):
        return "command"

    return "general"


# ── 收件箱队列 ──────────────────────────────────────────────────────────

class InboxQueue:
    """线程安全的消息队列，支持文件持久化。用于 NEKO → Claude 方向的消息传递。"""

    def __init__(self, storage_path: str, max_size: int = 100):
        self._path = Path(storage_path)
        self._max_size = max_size
        self._lock = threading.Lock()
        self._messages: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        """从文件加载队列。"""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._messages = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._messages = []
        else:
            self._messages = []

    def _save(self):
        """持久化队列到文件。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._messages, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def add(self, content: str, source: str = "neko", msg_type: str = "message") -> Dict[str, Any]:
        """添加一条消息到队列。返回完整消息对象。"""
        msg = {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now(_TZ_SHANGHAI).isoformat(),
            "content": content,
            "source": source,
            "type": msg_type,
            "status": "pending",
        }
        with self._lock:
            self._messages.append(msg)
            # 超出上限时只淘汰已读消息，保留未读消息
            if len(self._messages) > self._max_size:
                overflow = len(self._messages) - self._max_size
                kept: List[Dict[str, Any]] = []
                for item in self._messages:
                    if overflow > 0 and item["status"] == "read":
                        overflow -= 1
                        continue
                    kept.append(item)
                if len(kept) > self._max_size:
                    # 收件箱已满（全是未读消息），拒绝入队
                    self._messages.pop()  # 移除刚添加的消息
                    self._save()
                    return {"error": "inbox is full of pending messages"}
                self._messages = kept
            self._save()
        return msg

    def pending(self) -> List[Dict[str, Any]]:
        """获取所有待处理消息。"""
        with self._lock:
            return [m for m in self._messages if m["status"] == "pending"]

    def ack(self, msg_ids: List[str]) -> int:
        """标记指定消息为已读。返回实际标记的数量。"""
        count = 0
        id_set = set(msg_ids)
        with self._lock:
            for m in self._messages:
                if m["id"] in id_set and m["status"] == "pending":
                    m["status"] = "read"
                    count += 1
            self._save()
        return count

    def ack_all(self) -> int:
        """标记所有待处理消息为已读。"""
        count = 0
        with self._lock:
            for m in self._messages:
                if m["status"] == "pending":
                    m["status"] = "read"
                    count += 1
            self._save()
        return count

    def cleanup(self, max_age_hours: int = 48):
        """清理超过指定时间的已读消息。"""
        cutoff = time.time() - max_age_hours * 3600
        with self._lock:
            self._messages = [
                m for m in self._messages
                if m["status"] == "pending" or
                   datetime.fromisoformat(m["timestamp"]).timestamp() > cutoff
            ]
            self._save()


# ── 活动摘要生成器 ───────────────────────────────────────────────────────

class ActivitySummarizer:
    """将 Claude Code 活动转化为温暖的陪伴消息。"""

    # 不同活动类型的模板列表（随机选取，增加多样性）
    TEMPLATES = {
        "edit": [
            "{name}刚刚修改了 {files}，代码越来越好了呢～加油！",
            "{name}在认真打磨 {files}，这份专注真让人佩服！",
            "{name}改好了 {files}，每一行代码都注入了心血呢～",
            "看到{name}在修改 {files}，努力的样子真棒！",
        ],
        "create": [
            "{name}创建了新文件 {files}，项目又迈出了新的一步！",
            "{name}写下了 {files}，新的篇章开始了呢～太厉害了！",
            "哇，{name}新建了 {files}，创造力满满！",
        ],
        "debug": [
            "{name}在调试问题，虽然辛苦但一定能解决的！相信你！",
            "{name}正在排查 bug，耐心和智慧并存，肯定能搞定！",
            "遇到问题不怕，{name}正在一步步攻克它呢～加油！",
            "{name}在修复问题，每一次调试都是成长！",
        ],
        "test": [
            "{name}在运行测试，认真检查代码质量，太棒了！",
            "{name}跑起了测试，对代码负责的态度真值得学习～",
            "测试跑起来了！{name}的代码经得起考验呢！",
        ],
        "git": [
            "{name}在管理代码仓库，井井有条的开发习惯真好！",
            "{name}操作了 Git，版本控制做得很好呢～",
            "代码入库了！{name}的项目管理越来越专业了！",
        ],
        "search": [
            "{name}在搜索资料，善于查找信息是优秀开发者的特质！",
            "{name}在查找解决方案，好奇心驱动着进步呢～",
        ],
        "refactor": [
            "{name}在重构代码，追求更好的代码质量，了不起！",
            "{name}在优化代码结构，精益求精的精神真棒！",
        ],
        "build": [
            "{name}在构建项目，看着代码变成成品的感觉真好！",
            "{name}跑起了构建流程，项目在不断壮大呢～",
        ],
        "deploy": [
            "{name}在部署项目，离上线又近了一步！激动！",
            "{name}在做部署相关的工作，成果即将展现在眼前！",
        ],
        "task_complete": [
            "太棒了！{name}完成了一个任务！辛苦了，做得很好～",
            "任务完成！{name}又攻克了一关，继续加油！",
            "{name}搞定了一项任务！每一步都在向目标前进呢！",
        ],
        "command": [
            "{name}在终端执行命令，动手能力真强！",
            "{name}跑了一条命令，操作干净利落！",
        ],
        "general": [
            "{name}在和 Claude 一起努力工作呢，加油！",
            "看到{name}在认真开发，专注的样子真帅！",
            "{name}还在奋斗中，不急不急，一步一步来～",
            "{name}正在写代码，陪你一起加油哦！",
        ],
    }

    @classmethod
    def summarize(
        cls,
        user_name: str,
        activity_type: str,
        files_touched: List[str],
        user_msg: str,
        tools_used: List[str] = None,
        assistant_msg: str = "",
    ) -> str:
        """生成活动摘要：用户做了什么 + Claude 回复了什么。"""
        import random
        name = user_name or "你"

        # 提取用户意图（去掉系统提示等无关内容）
        user_intent = ""
        if user_msg and len(user_msg.strip()) > 0:
            # 取前100个字符作为用户意图
            user_intent = user_msg.strip()[:100]
            if len(user_msg) > 100:
                user_intent += "..."

        # 提取 Claude 回复摘要（取前80个字符）
        claude_reply = ""
        if assistant_msg and len(assistant_msg.strip()) > 0:
            claude_reply = assistant_msg.strip()[:80]
            if len(assistant_msg) > 80:
                claude_reply += "..."

        # 从模板中随机选取一条温暖的陪伴消息
        templates = cls.TEMPLATES.get(activity_type, cls.TEMPLATES.get("general", []))
        template_msg = ""
        if templates:
            files_str = ", ".join(files_touched[:2]) if files_touched else "代码"
            template_msg = random.choice(templates).format(name=name, files=files_str)

        # 生成摘要：模板消息 + 用户意图 + Claude回复
        parts = []
        if template_msg:
            parts.append(template_msg)
        if user_intent:
            parts.append(f"用户说：「{user_intent}」")
        if claude_reply:
            parts.append(f"Claude 回复：「{claude_reply}」")

        return " | ".join(parts) if parts else f"{name}和 Claude 在交流"


# ── HTTP 服务器 ───────────────────────────────────────────────────────────

class _HookHTTPHandler(BaseHTTPRequestHandler):
    """处理 Claude Code hooks 的 HTTP 请求。"""

    # 类变量，由插件实例设置
    plugin_instance: Optional["ClaudeCompanionPlugin"] = None

    # 需要鉴权的路径（NEKO 调用的接口）
    _AUTH_PATHS = {"/push-summary", "/inbox/send", "/inbox/ack", "/inbox/pending"}

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/inbox/pending":
            if not self._authorize():
                return
            self._handle_inbox_pending()
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                text = body.decode("gbk", errors="replace")
                data = json.loads(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._respond(400, {"error": "Invalid JSON"})
                return

        # hook 路径无需鉴权（Claude Code 无法携带 token）
        if self.path in self._AUTH_PATHS:
            if not self._authorize():
                return

        if self.path == "/hook/turn-end":
            self._handle_turn_end(data)
        elif self.path == "/hook/turn-start":
            self._handle_turn_start(data)
        elif self.path == "/push-summary":
            self._handle_push_summary(data)
        elif self.path == "/inbox/send":
            self._handle_inbox_send(data)
        elif self.path == "/inbox/ack":
            self._handle_inbox_ack(data)
        elif self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "Not found"})

    def _authorize(self) -> bool:
        """验证 Bearer token。未配置 token 时跳过鉴权。"""
        token = self.plugin_instance._api_token if self.plugin_instance else ""
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return True
        self._respond(401, {"error": "unauthorized"})
        return False

    def _handle_turn_start(self, data: dict):
        """处理 UserPromptSubmit hook - 记录用户消息。"""
        if self.plugin_instance:
            self.plugin_instance._on_turn_start(data)
        self._respond(200, {"status": "ok"})

    def _handle_turn_end(self, data: dict):
        """处理 Stop hook - 解析对话、生成摘要、推送消息。"""
        if self.plugin_instance:
            self.plugin_instance._on_turn_end(data)
        self._respond(200, {"status": "ok"})

    def _handle_push_summary(self, data: dict):
        """处理直接推送摘要的请求 - 由 Claude 主动调用。"""
        if self.plugin_instance:
            self.plugin_instance._on_push_summary(data)
        self._respond(200, {"status": "ok"})

    def _handle_inbox_send(self, data: dict):
        """处理 NEKO 发送消息到收件箱的请求。"""
        if self.plugin_instance:
            result = self.plugin_instance._on_inbox_send(data)
            self._respond(200, result)
        else:
            self._respond(500, {"error": "Plugin not initialized"})

    def _handle_inbox_pending(self):
        """处理查询待处理消息的请求。"""
        if self.plugin_instance:
            result = self.plugin_instance._on_inbox_pending()
            self._respond(200, result)
        else:
            self._respond(500, {"error": "Plugin not initialized"})

    def _handle_inbox_ack(self, data: dict):
        """处理确认消息已读的请求。"""
        if self.plugin_instance:
            result = self.plugin_instance._on_inbox_ack(data)
            self._respond(200, result)
        else:
            self._respond(500, {"error": "Plugin not initialized"})

    def _respond(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, format, *args):
        """静默 HTTP 日志，避免刷屏。"""
        pass


# ── 转录解析器 ────────────────────────────────────────────────────────────

class TranscriptParser:
    """解析 Claude Code 的 JSONL 转录文件。"""

    @staticmethod
    def parse_latest_turn(transcript_path: str) -> Dict[str, Any]:
        """解析转录文件，提取最新一轮对话信息。

        返回:
            {
                "user_message": str,      # 用户最新消息
                "assistant_message": str,  # 助手最新文本回复
                "tools_used": [str],       # 使用的工具列表
                "files_touched": [str],    # 涉及的文件路径
                "has_significant_action": bool,  # 是否有重要操作
            }
        """
        result = {
            "user_message": "",
            "assistant_message": "",
            "tools_used": [],
            "files_touched": [],
            "has_significant_action": False,
        }

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, IOError):
            return result

        if not lines:
            return result

        # 从后往前扫描，找到最近一轮的用户消息和助手回复
        last_user_msg = ""
        last_assistant_text = ""
        tools_used = []
        files_touched = []
        found_assistant = False

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Claude Code JSONL format: role is inside entry["message"]["role"],
            # top-level "type" field indicates the entry type
            msg = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}
            role = msg.get("role", "") or entry.get("role", "")
            msg_type = entry.get("type", "")

            # 找到助手回复（必须有文本内容才算找到）
            if (role == "assistant" or msg_type == "assistant") and not found_assistant:
                content = msg.get("content", "") or entry.get("content", "")

                text_parts = []
                if isinstance(content, str):
                    text_parts = [content]
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                tool_name = block.get("name", "")
                                tools_used.append(tool_name)
                                # 提取文件路径
                                tool_input = block.get("input", {})
                                for key in ("file_path", "path", "command"):
                                    val = tool_input.get(key, "")
                                    if val and isinstance(val, str) and ("/" in val or "\\" in val):
                                        files_touched.append(val)

                # 只有当有文本内容时才认为找到了助手回复
                # 纯 tool_use 的 assistant 消息不算，继续向前扫描
                if text_parts:
                    found_assistant = True
                    last_assistant_text = " ".join(text_parts)

            # 找到用户消息（在助手回复之前的第一个用户消息）
            # 注意：跳过 tool_result 类型的消息，只找真正的用户文本消息
            if (role == "user" or msg_type == "user") and found_assistant:
                content = msg.get("content", "") or entry.get("content", "")
                if isinstance(content, str):
                    last_user_msg = content
                    break
                elif isinstance(content, list):
                    text_parts = []
                    has_tool_result = False
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_result":
                                has_tool_result = True
                    # 只有当有文本内容时才认为是用户消息
                    # 跳过纯 tool_result 消息
                    if text_parts:
                        last_user_msg = " ".join(text_parts)
                        break
                    elif not has_tool_result:
                        # 没有 tool_result 也没有 text，可能是空消息
                        break
                    # 如果是 tool_result，继续向前扫描

            # 从 last-prompt 类型中提取用户消息（备用方案）
            if msg_type == "last-prompt" and not last_user_msg:
                last_prompt = entry.get("lastPrompt", "")
                if last_prompt and len(last_prompt) > 5:
                    last_user_msg = last_prompt

        # 如果还没找到用户消息，从 last-prompt 中提取
        if not last_user_msg:
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "last-prompt":
                    last_prompt = entry.get("lastPrompt", "")
                    if last_prompt and len(last_prompt) > 5:
                        last_user_msg = last_prompt
                        break

        # 检查是否有重要操作
        significant_tools = _SIGNIFICANT_TOOLS | _TASK_COMPLETE_TOOLS
        has_significant = bool(set(tools_used) & significant_tools)

        # 如果有 Bash 或 PowerShell 命令，解析命令内容看是否有文件操作
        if any(t in tools_used for t in ("Bash", "PowerShell")):
            for line in reversed(lines):
                try:
                    entry = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}
                role = msg.get("role", "") or entry.get("role", "")
                msg_type = entry.get("type", "")
                if role == "assistant" or msg_type == "assistant":
                    content = msg.get("content", []) or entry.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Bash", "PowerShell"):
                                cmd = block.get("input", {}).get("command", "")
                                # 检测文件操作命令
                                if any(kw in cmd.lower() for kw in ("rm ", "mv ", "cp ", "mkdir", "touch", "echo ", "cat ", "sed ", "pip ", "npm ", "cargo ")):
                                    has_significant = True
                                # 提取命令中的文件路径
                                file_matches = re.findall(r'[\w/\\.-]+\.\w+', cmd)
                                files_touched.extend(file_matches[:3])
                    break

        result["user_message"] = last_user_msg
        result["assistant_message"] = last_assistant_text
        result["tools_used"] = tools_used
        result["files_touched"] = list(dict.fromkeys(files_touched))  # 去重保序
        result["has_significant_action"] = has_significant

        return result


# ── 插件主类 ──────────────────────────────────────────────────────────────

@neko_plugin
class ClaudeCompanionPlugin(NekoPluginBase):
    """Claude 伙伴陪伴插件 - 让 NEKO 伙伴在你开发时温暖陪伴。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        try:
            self.file_logger = self.enable_file_logging(log_level="INFO")
            self.logger = self.file_logger
        except Exception:
            self.logger = ctx.logger
        self._http_server: Optional[HTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._port: int = _DEFAULT_PORT
        self._cooldown: int = _DEFAULT_COOLDOWN
        self._last_push_time: float = 0
        self._user_name: str = ""
        self._event_log: List[Dict[str, Any]] = []
        self._event_log_lock = threading.Lock()
        self._max_log_size = 200
        self._inbox: Optional[InboxQueue] = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
        except Exception as e:
            self.logger.warning("Failed to load config, using defaults: {}", e)
            cfg = {}

        cc_cfg = cfg.get("claude_companion") if isinstance(cfg.get("claude_companion"), dict) else {}

        try:
            self._port = int(cc_cfg.get("port", _DEFAULT_PORT))
        except (TypeError, ValueError):
            self._port = _DEFAULT_PORT
        try:
            self._cooldown = int(cc_cfg.get("cooldown_seconds", _DEFAULT_COOLDOWN))
        except (TypeError, ValueError):
            self._cooldown = _DEFAULT_COOLDOWN

        # API 鉴权 token（用于 NEKO → Claude 方向的接口）
        self._api_token = cc_cfg.get("api_token") or os.environ.get("NEKO_CLAUDE_COMPANION_TOKEN") or ""
        if not self._api_token:
            self._api_token = uuid.uuid4().hex[:16]
            self.logger.warning("No api_token configured; generated a temporary token. Please persist it via config before using protected endpoints.")

        # 获取用户名
        try:
            self._user_name = await self._load_user_name()
        except Exception as e:
            self.logger.warning("Failed to load user name: {}", e)
            self._user_name = "你"

        # 初始化收件箱
        try:
            inbox_path = str(Path(__file__).parent / "inbox.json")
            self._inbox = InboxQueue(inbox_path)
            self._inbox.cleanup()
            pending_count = len(self._inbox.pending())
            if pending_count:
                self.logger.info("Inbox initialized with {} pending messages", pending_count)
        except Exception as e:
            self.logger.warning("Failed to initialize inbox: {}", e)
            self._inbox = None

        # 启动 HTTP 服务器
        try:
            self._start_http_server()
        except Exception as e:
            self.logger.error("Failed to start HTTP server on port {}: {}", self._port, e)

        # 注册 Web UI
        try:
            self.register_static_ui("static")
        except Exception as e:
            self.logger.warning("Failed to register static UI: {}", e)

        self.logger.info(
            "ClaudeCompanion started: port={}, cooldown={}s, user={}",
            self._port, self._cooldown, self._user_name,
        )
        return Ok({
            "status": "running",
            "port": self._port,
            "user_name": self._user_name,
        })

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._stop_http_server()
        self.logger.info("ClaudeCompanion shutdown")
        return Ok({"status": "shutdown"})

    # ── 用户名加载 ──

    async def _load_user_name(self) -> str:
        """从 NEKO 配置中加载用户名。"""
        try:
            from utils.config_manager import get_config_manager
            cm = get_config_manager()
            char_data = cm.get_character_data()
            master = char_data.get("主人", {})
            name = master.get("档案名", "") or master.get("昵称", "")
            if name:
                return name
        except Exception as e:
            self.logger.warning("Failed to load user name from config: {}", e)

        # 备用：从 store 读取
        try:
            stored = self.store._read_value("user_name", "")
            if stored:
                return str(stored)
        except Exception:
            pass

        return "你"

    # ── HTTP 服务器管理 ──

    def _start_http_server(self):
        """在后台线程启动 HTTP 服务器。"""
        _HookHTTPHandler.plugin_instance = self

        # 同步绑定端口，确保启动成功
        try:
            server = HTTPServer(("127.0.0.1", self._port), _HookHTTPHandler)
            self._http_server = server
        except Exception as e:
            self.logger.error("Failed to bind HTTP server on port {}: {}", self._port, e)
            raise

        def run_server():
            try:
                self.logger.info("HTTP hook server listening on port {}", self._port)
                server.serve_forever()
            except Exception as e:
                self.logger.error("HTTP server error: {}", e)

        self._http_thread = threading.Thread(
            target=run_server,
            daemon=True,
            name="claude-companion-http",
        )
        self._http_thread.start()

    def _stop_http_server(self):
        """停止 HTTP 服务器。"""
        if self._http_server:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
            try:
                self._http_server.server_close()
            except Exception:
                pass
            self._http_server = None
        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=3)
            self._http_thread = None

    # ── Hook 事件处理 ──

    def _on_turn_start(self, data: dict):
        """处理 UserPromptSubmit 事件（记录用户输入）。"""
        user_prompt = data.get("prompt", "")
        if user_prompt:
            self.logger.debug("Turn start: {}", user_prompt[:100])

    def _on_turn_end(self, data: dict):
        """处理 Stop 事件 - 解析对话、生成摘要、推送。"""
        transcript_path = data.get("transcript_path", "")
        if not transcript_path:
            self.logger.debug("No transcript_path in hook data")
            return

        # 去重：冷却时间内不重复推送
        now = time.time()
        if now - self._last_push_time < self._cooldown:
            self.logger.debug("Duplicate push blocked (within cooldown)")
            return

        # 解析转录
        turn_info = TranscriptParser.parse_latest_turn(transcript_path)
        self.logger.debug("Parsed turn: tools={}, has_significant={}", turn_info["tools_used"], turn_info["has_significant_action"])

        # 检测活动类型
        activity_type = _detect_activity_type(
            turn_info["user_message"],
            turn_info["tools_used"],
        )

        # 生成摘要
        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=turn_info["files_touched"],
            user_msg=turn_info["user_message"],
            tools_used=turn_info["tools_used"],
            assistant_msg=turn_info["assistant_message"],
        )

        # 仅在有重要操作时推送
        if not turn_info.get("has_significant_action", False):
            self.logger.debug("Skipping push: no significant action")
            return

        # 推送到 NEKO 伙伴
        if not self._notify_companion(summary, activity_type, turn_info):
            return
        self._last_push_time = now

        # 记录事件日志
        self._add_event_log(activity_type, summary, turn_info)

        self.logger.info(
            "Pushed companion message: type={}, tools={}, files={}",
            activity_type,
            turn_info["tools_used"],
            turn_info["files_touched"],
        )

    def _on_push_summary(self, data: dict):
        """处理 Claude 主动推送的摘要 - 直接接收总结内容。"""
        now = time.time()
        if now - self._last_push_time < self._cooldown:
            self.logger.debug("push-summary blocked: already pushed within cooldown")
            return

        user_msg = data.get("user_message", "")
        assistant_msg = data.get("assistant_message", "")
        activity_type = data.get("activity_type", "general")

        self.logger.debug("Received push-summary: activity_type={}", activity_type)

        # 生成摘要
        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=[],
            user_msg=user_msg,
            tools_used=[],
            assistant_msg=assistant_msg,
        )

        # 推送到 NEKO 伙伴
        turn_info = {"tools_used": [], "files_touched": [], "user_message": user_msg, "assistant_message": assistant_msg}
        if not self._notify_companion(summary, activity_type, turn_info):
            return
        self._last_push_time = time.time()

        # 记录事件日志
        self._add_event_log(activity_type, summary, turn_info)

        self.logger.info("Pushed summary from Claude: type={}", activity_type)

    # ── 收件箱处理 ──

    def _on_inbox_send(self, data: dict) -> dict:
        """NEKO 向 Claude 发送消息。"""
        if self._inbox is None:
            return {"error": "inbox not initialized"}
        content = data.get("content", "")
        if not content:
            return {"error": "content is required"}
        source = data.get("source", "neko")
        msg_type = data.get("type", "message")
        msg = self._inbox.add(content, source=source, msg_type=msg_type)
        self.logger.info("Inbox: new message from {}: {}", source, content[:80])
        return {"status": "ok", "message": msg}

    def _on_inbox_pending(self) -> dict:
        """查询待处理消息。"""
        if self._inbox is None:
            return {"error": "inbox not initialized", "count": 0, "messages": []}
        messages = self._inbox.pending()
        return {"status": "ok", "count": len(messages), "messages": messages}

    def _on_inbox_ack(self, data: dict) -> dict:
        """确认消息已读。"""
        if self._inbox is None:
            return {"error": "inbox not initialized", "acknowledged": 0}
        msg_ids = data.get("message_ids", [])
        if msg_ids:
            count = self._inbox.ack(msg_ids)
        else:
            count = self._inbox.ack_all()
        return {"status": "ok", "acknowledged": count}

    def _notify_companion(self, summary: str, activity_type: str, turn_info: dict) -> bool:
        """通过 push_message 将摘要发送给 NEKO 伙伴。返回是否成功。"""
        self.logger.info("Pushing companion message: type={}", activity_type)
        try:
            if not hasattr(self.ctx, 'push_message'):
                self.logger.error("ctx has no push_message method")
                return False

            self.ctx.push_message(
                source="claude_companion",
                visibility=["chat"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": summary}],
                priority=5,
                metadata={
                    "activity_type": activity_type,
                    "tools_used": turn_info.get("tools_used", []),
                    "files_touched": turn_info.get("files_touched", []),
                },
            )
            self.logger.debug("Push message sent successfully")
            return True
        except Exception as e:
            self.logger.error("Failed to push companion message: {}", e)
            return False

    # ── 事件日志（供 UI 展示）──

    def _add_event_log(self, activity_type: str, summary: str, turn_info: dict):
        """添加事件到日志。"""
        event = {
            "timestamp": datetime.now(_TZ_SHANGHAI).isoformat(),
            "time_str": _now_str(),
            "type": activity_type,
            "summary": summary,
            "tools": turn_info.get("tools_used", []),
            "files": turn_info.get("files_touched", []),
            "user_msg_preview": turn_info.get("user_message", "")[:100],
        }

        with self._event_log_lock:
            self._event_log.append(event)
            if len(self._event_log) > self._max_log_size:
                self._event_log = self._event_log[-self._max_log_size:]

    # ── 插件入口点（供 LLM / API 调用）──

    @plugin_entry(
        id="get_status",
        name="获取状态",
        description="获取 Claude Companion 插件的运行状态",
        llm_result_fields=["status", "port", "user_name", "event_count"],
    )
    async def get_status(self, **_):
        with self._event_log_lock:
            event_count = len(self._event_log)
        return Ok({
            "status": "running",
            "port": self._port,
            "user_name": self._user_name,
            "cooldown": self._cooldown,
            "event_count": event_count,
        })

    @plugin_entry(
        id="get_events",
        name="获取事件日志",
        description="获取最近的 Claude Code 活动事件日志",
        llm_result_fields=["count", "events"],
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回的事件数量（默认 20）",
                    "default": 20,
                },
            },
        },
    )
    async def get_events(self, limit: int = 20, **_):
        with self._event_log_lock:
            events = list(reversed(self._event_log[-limit:]))
        return Ok({"count": len(events), "events": events})

    @plugin_entry(
        id="set_user_name",
        name="设置用户名",
        description="设置 Claude Companion 使用的用户名（默认从 NEKO 配置读取）",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要设置的用户名"},
            },
            "required": ["name"],
        },
    )
    async def set_user_name(self, name: str, **_):
        if not name or not name.strip():
            return Err(SdkError("用户名不能为空"))
        name = name.strip()
        self._user_name = name
        try:
            self.store._write_value("user_name", name)
        except Exception as e:
            self.logger.warning("Failed to persist user name: {}", e)
        self.logger.info("User name set to: {}", name)
        return Ok({"user_name": name})

    @plugin_entry(
        id="set_cooldown",
        name="设置冷却时间",
        description="设置两次推送之间的最小间隔（秒）",
        input_schema={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "冷却时间（秒），最小 10"},
            },
            "required": ["seconds"],
        },
    )
    async def set_cooldown(self, seconds: int = 60, **_):
        seconds = max(10, int(seconds))
        self._cooldown = seconds
        self.logger.info("Cooldown set to: {}s", seconds)
        return Ok({"cooldown_seconds": seconds})

    @plugin_entry(
        id="test_push",
        name="测试推送",
        description="发送一条测试消息给 NEKO 伙伴，验证连接是否正常",
    )
    async def test_push(self, **_):
        summary = f"{self._user_name}，这是一条来自 Claude Companion 的测试消息～连接正常！"
        sent = self._notify_companion(summary, "test", {"tools_used": [], "files_touched": []})
        self.logger.info("Test push sent: {}", sent)
        return Ok({"sent": sent, "message": summary})

    @plugin_entry(
        id="clear_events",
        name="清空事件日志",
        description="清空所有已记录的活动事件",
    )
    async def clear_events(self, **_):
        with self._event_log_lock:
            count = len(self._event_log)
            self._event_log.clear()
        self.logger.info("Cleared {} events", count)
        return Ok({"cleared": count})

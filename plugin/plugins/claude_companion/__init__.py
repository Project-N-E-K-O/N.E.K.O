"""
Claude Companion plugin

Monitors Claude Code activity and detects important operations (editing files, running commands, completing tasks, etc.),
hen automatically sends activity summaries to the NEKO companion so she can warmly accompany and encourage the user while they develop.

Architecture:
  Claude Code ──(HTTP Hook)──▶ This plugin's HTTP Server ──(push_message)──▶ NEKO companion

Dependencies:
  - Claude Code hooks configuration (see hooks.json)
  - NEKO Plugin System SDK v2
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

# ── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_PORT = 48920
_DEFAULT_COOLDOWN = 60
_SIGNIFICANT_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "PowerShell", "NotebookEdit"}
_TASK_COMPLETE_TOOLS = {"TaskUpdate"}

# Activity-type keyword mapping
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

# ── Helper functions ───────────────────────────────────────────────────────

_TZ_SHANGHAI = timezone(timedelta(hours=8))


def _now_str() -> str:
    return datetime.now(_TZ_SHANGHAI).strftime("%H:%M")


def _detect_activity_type(user_msg: str, tools_used: List[str]) -> str:
    """Detect activity types based on user messages and tool usage."""
    msg_lower = user_msg.lower()
    tools_str = " ".join(tools_used).lower()
    combined = f"{msg_lower} {tools_str}"

    # Priority detection
    if any(t in tools_str for t in ("taskupdate",)):
        return "task_complete"

    for activity, keywords in _ACTIVITY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return activity

    # Infer from tool
    if any(t in tools_used for t in ("Edit", "Write", "MultiEdit", "NotebookEdit")):
        return "edit"
    if any(t in tools_used for t in ("Bash", "PowerShell")):
        return "command"

    return "general"


# ── Inbox queue ────────────────────────────────────────────────────────────

class InboxQueue:
    """Thread-safe message queue with file persistence. Used for NEKO → Claude messaging."""

    def __init__(self, storage_path: str, max_size: int = 100):
        self._path = Path(storage_path)
        self._max_size = max_size
        self._lock = threading.Lock()
        self._messages: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        """Load the queue from a file."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._messages = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._messages = []
        else:
            self._messages = []

    def _save(self):
        """Persist the queue to a file."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._messages, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def add(self, content: str, source: str = "neko", msg_type: str = "message") -> Dict[str, Any]:
        """Add a message to the queue. Return the complete message object."""
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
            # When over capacity, evict only read messages and preserve unread ones
            if len(self._messages) > self._max_size:
                overflow = len(self._messages) - self._max_size
                kept: List[Dict[str, Any]] = []
                for item in self._messages:
                    if overflow > 0 and item["status"] == "read":
                        overflow -= 1
                        continue
                    kept.append(item)
                if len(kept) > self._max_size:
                    # Inbox is full (all messages unread); reject the new message
                    self._messages.pop()  # Remove the message just added
                    self._save()
                    return {"error": "inbox is full of pending messages"}
                self._messages = kept
            self._save()
        return msg

    def pending(self) -> List[Dict[str, Any]]:
        """Get all pending messages."""
        with self._lock:
            return [m for m in self._messages if m["status"] == "pending"]

    def ack(self, msg_ids: List[str]) -> int:
        """Mark the specified messages as read. Return the number actually marked."""
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
        """Mark all pending messages as read."""
        count = 0
        with self._lock:
            for m in self._messages:
                if m["status"] == "pending":
                    m["status"] = "read"
                    count += 1
            self._save()
        return count

    def cleanup(self, max_age_hours: int = 48):
        """Delete read messages older than the specified age."""
        cutoff = time.time() - max_age_hours * 3600
        with self._lock:
            self._messages = [
                m for m in self._messages
                if m["status"] == "pending" or
                   datetime.fromisoformat(m["timestamp"]).timestamp() > cutoff
            ]
            self._save()


# ── Activity summary generator ─────────────────────────────────────────────

class ActivitySummarizer:
    """Turn Claude Code activity into warm companionship messages."""

    # Template lists by activity type (randomly choose one for variety)
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
        """Generate an activity summary: what the user did + how Claude responded."""
        import random
        name = user_name or "你"

        # Extract user intent (remove irrelevant content such as system prompts)
        user_intent = ""
        if user_msg and len(user_msg.strip()) > 0:
            # Use the first 100 characters as the user intent
            user_intent = user_msg.strip()[:100]
            if len(user_msg) > 100:
                user_intent += "..."

        # Extract a summary of Claude's response (first 80 characters)
        claude_reply = ""
        if assistant_msg and len(assistant_msg.strip()) > 0:
            claude_reply = assistant_msg.strip()[:80]
            if len(assistant_msg) > 80:
                claude_reply += "..."

        # Randomly select a warm companionship message from the templates
        templates = cls.TEMPLATES.get(activity_type, cls.TEMPLATES.get("general", []))
        template_msg = ""
        if templates:
            files_str = ", ".join(files_touched[:2]) if files_touched else "代码"
            template_msg = random.choice(templates).format(name=name, files=files_str)

        # Generate a summary: template message + user intent + Claude response
        parts = []
        if template_msg:
            parts.append(template_msg)
        if user_intent:
            parts.append(f"用户说：「{user_intent}」")
        if claude_reply:
            parts.append(f"Claude 回复：「{claude_reply}」")

        return " | ".join(parts) if parts else f"{name}和 Claude 在交流"


# ── HTTP server ────────────────────────────────────────────────────────────

class _HookHTTPHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests from Claude Code hooks."""

    # Class variable, set by the plugin instance
    plugin_instance: Optional["ClaudeCompanionPlugin"] = None

    # Paths that require authentication (APIs called by NEKO)
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

        # Hook paths do not require authentication (Claude Code cannot include a token)
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
        """Validate the Bearer token. Skip authentication if no token is configured."""
        token = self.plugin_instance._api_token if self.plugin_instance else ""
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return True
        self._respond(401, {"error": "unauthorized"})
        return False

    def _handle_turn_start(self, data: dict):
        """Handle the UserPromptSubmit hook — record the user's message."""
        if self.plugin_instance:
            self.plugin_instance._on_turn_start(data)
        self._respond(200, {"status": "ok"})

    def _handle_turn_end(self, data: dict):
        """Handle the Stop hook — parse the conversation, generate a summary, and push it."""
        if self.plugin_instance:
            self.plugin_instance._on_turn_end(data)
        self._respond(200, {"status": "ok"})

    def _handle_push_summary(self, data: dict):
        """Handle a request to push a summary directly — called proactively by Claude."""
        if self.plugin_instance:
            self.plugin_instance._on_push_summary(data)
        self._respond(200, {"status": "ok"})

    def _handle_inbox_send(self, data: dict):
        """Handle a request to send a message from NEKO to the inbox."""
        if self.plugin_instance:
            result = self.plugin_instance._on_inbox_send(data)
            self._respond(200, result)
        else:
            self._respond(500, {"error": "Plugin not initialized"})

    def _handle_inbox_pending(self):
        """Handle a request to query pending messages."""
        if self.plugin_instance:
            result = self.plugin_instance._on_inbox_pending()
            self._respond(200, result)
        else:
            self._respond(500, {"error": "Plugin not initialized"})

    def _handle_inbox_ack(self, data: dict):
        """Handle a request to confirm messages were read."""
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
        """Silence HTTP logs to avoid flooding."""
        pass


# ── Transcript parser ──────────────────────────────────────────────────────

class TranscriptParser:
    """Parse Claude Code JSONL transcript files."""

    @staticmethod
    def parse_latest_turn(transcript_path: str) -> Dict[str, Any]:
        """Parse the transcript file and extract information about the latest conversation turn.

        Returns:
            {
                \"user_message\": str,      # Latest user message
                \"assistant_message\": str,  # Latest assistant text response
                \"tools_used\": [str],       # Tools used
                \"files_touched\": [str],    # File paths involved
                \"has_significant_action\": bool,  # Whether an important operation occurred
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

        # Scan backward to find the most recent user message and assistant response
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

            # Find an assistant response (requires text content)
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
                                # Extract file paths
                                tool_input = block.get("input", {})
                                for key in ("file_path", "path", "command"):
                                    val = tool_input.get(key, "")
                                    if val and isinstance(val, str) and ("/" in val or "\\" in val):
                                        files_touched.append(val)

                # Count an assistant response only when it contains text
                # A tool_use-only assistant message does not count; keep scanning backward
                if text_parts:
                    found_assistant = True
                    last_assistant_text = " ".join(text_parts)

            # Find the user message (first user message before the assistant response)
            # Skip tool_result messages; look only for actual user text
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
                    # Count a user message only when it contains text
                    # Skip messages containing only tool_result
                    if text_parts:
                        last_user_msg = " ".join(text_parts)
                        break
                    elif not has_tool_result:
                        # No tool_result or text may indicate an empty message
                        break
                    # If this is tool_result, keep scanning backward

            # Extract user messages from the last-prompt type (fallback)
            if msg_type == "last-prompt" and not last_user_msg:
                last_prompt = entry.get("lastPrompt", "")
                if last_prompt and len(last_prompt) > 5:
                    last_user_msg = last_prompt

        # If the user message is still missing, extract it from last-prompt
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

        # Check whether an important operation occurred
        significant_tools = _SIGNIFICANT_TOOLS | _TASK_COMPLETE_TOOLS
        has_significant = bool(set(tools_used) & significant_tools)

        # If there is a Bash or PowerShell command, parse it for file operations
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
                                # Detect file-operation commands
                                if any(kw in cmd.lower() for kw in ("rm ", "mv ", "cp ", "mkdir", "touch", "echo ", "cat ", "sed ", "pip ", "npm ", "cargo ")):
                                    has_significant = True
                                # Extract file paths from the command
                                file_matches = re.findall(r'[\w/\\.-]+\.\w+', cmd)
                                files_touched.extend(file_matches[:3])
                    break

        result["user_message"] = last_user_msg
        result["assistant_message"] = last_assistant_text
        result["tools_used"] = tools_used
        result["files_touched"] = list(dict.fromkeys(files_touched))  # Deduplicate while preserving order
        result["has_significant_action"] = has_significant

        return result


# ── Main plugin class ──────────────────────────────────────────────────────

@neko_plugin
class ClaudeCompanionPlugin(NekoPluginBase):
    """Claude Companion plugin — gives you warm NEKO companionship while you develop."""

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

        # API authentication token (for NEKO → Claude endpoints)
        self._api_token = cc_cfg.get("api_token") or os.environ.get("NEKO_CLAUDE_COMPANION_TOKEN") or ""
        if not self._api_token:
            self._api_token = uuid.uuid4().hex[:16]
            self.logger.warning("No api_token configured; generated a temporary token. Please persist it via config before using protected endpoints.")

        # Get username
        try:
            self._user_name = await self._load_user_name()
        except Exception as e:
            self.logger.warning("Failed to load user name: {}", e)
            self._user_name = "你"

        # Initialize inbox
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

        # Start HTTP server
        try:
            self._start_http_server()
        except Exception as e:
            self.logger.error("Failed to start HTTP server on port {}: {}", self._port, e)

        # Register Web UI
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

    # ── Username loading ──

    async def _load_user_name(self) -> str:
        """Load the username from NEKO configuration."""
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

        # Fallback: read from store
        try:
            stored = self.store._read_value("user_name", "")
            if stored:
                return str(stored)
        except Exception:
            pass

        return "你"

    # ── HTTP server management ──

    def _start_http_server(self):
        """Start the HTTP server in a background thread."""
        _HookHTTPHandler.plugin_instance = self

        # Bind port synchronously to ensure startup succeeds
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
        """Stop the HTTP server."""
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

    # ── Hook event processing ──

    def _on_turn_start(self, data: dict):
        """Handle the UserPromptSubmit event (record user input)."""
        user_prompt = data.get("prompt", "")
        if user_prompt:
            self.logger.debug("Turn start: {}", user_prompt[:100])

    def _on_turn_end(self, data: dict):
        """Handle the Stop event — parse the conversation, generate a summary, and push it."""
        transcript_path = data.get("transcript_path", "")
        if not transcript_path:
            self.logger.debug("No transcript_path in hook data")
            return

        # Deduplicate: do not push the same event again during the cooldown
        now = time.time()
        if now - self._last_push_time < self._cooldown:
            self.logger.debug("Duplicate push blocked (within cooldown)")
            return

        # Parse transcript
        turn_info = TranscriptParser.parse_latest_turn(transcript_path)
        self.logger.debug("Parsed turn: tools={}, has_significant={}", turn_info["tools_used"], turn_info["has_significant_action"])

        # Detect activity type
        activity_type = _detect_activity_type(
            turn_info["user_message"],
            turn_info["tools_used"],
        )

        # Generate summary
        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=turn_info["files_touched"],
            user_msg=turn_info["user_message"],
            tools_used=turn_info["tools_used"],
            assistant_msg=turn_info["assistant_message"],
        )

        # Push only when an important operation occurred
        if not turn_info.get("has_significant_action", False):
            self.logger.debug("Skipping push: no significant action")
            return

        # Push to the NEKO companion
        if not self._notify_companion(summary, activity_type, turn_info):
            return
        self._last_push_time = now

        # Record event log
        self._add_event_log(activity_type, summary, turn_info)

        self.logger.info(
            "Pushed companion message: type={}, tools={}, files={}",
            activity_type,
            turn_info["tools_used"],
            turn_info["files_touched"],
        )

    def _on_push_summary(self, data: dict):
        """Receive a summary proactively pushed by Claude."""
        now = time.time()
        if now - self._last_push_time < self._cooldown:
            self.logger.debug("push-summary blocked: already pushed within cooldown")
            return

        user_msg = data.get("user_message", "")
        assistant_msg = data.get("assistant_message", "")
        activity_type = data.get("activity_type", "general")

        self.logger.debug("Received push-summary: activity_type={}", activity_type)

        # Generate summary
        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=[],
            user_msg=user_msg,
            tools_used=[],
            assistant_msg=assistant_msg,
        )

        # Push to the NEKO companion
        turn_info = {"tools_used": [], "files_touched": [], "user_message": user_msg, "assistant_message": assistant_msg}
        if not self._notify_companion(summary, activity_type, turn_info):
            return
        self._last_push_time = time.time()

        # Record event log
        self._add_event_log(activity_type, summary, turn_info)

        self.logger.info("Pushed summary from Claude: type={}", activity_type)

    # ── Inbox processing ──

    def _on_inbox_send(self, data: dict) -> dict:
        """Send a message from NEKO to Claude."""
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
        """Query pending messages."""
        if self._inbox is None:
            return {"error": "inbox not initialized", "count": 0, "messages": []}
        messages = self._inbox.pending()
        return {"status": "ok", "count": len(messages), "messages": messages}

    def _on_inbox_ack(self, data: dict) -> dict:
        """Confirm that messages were read."""
        if self._inbox is None:
            return {"error": "inbox not initialized", "acknowledged": 0}
        msg_ids = data.get("message_ids", [])
        if msg_ids:
            count = self._inbox.ack(msg_ids)
        else:
            count = self._inbox.ack_all()
        return {"status": "ok", "acknowledged": count}

    def _notify_companion(self, summary: str, activity_type: str, turn_info: dict) -> bool:
        """Send a summary to the NEKO companion using push_message. Return whether it succeeded."""
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

    # ── Event log (displayed in UI) ──

    def _add_event_log(self, activity_type: str, summary: str, turn_info: dict):
        """Add an event to the log."""
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

    # ── Plugin entry points (for LLM / API calls) ──

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

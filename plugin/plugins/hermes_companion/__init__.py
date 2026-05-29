"""
Hermes 伙伴陪伴插件 (Hermes Companion)

基于 OpenClaw Companion 架构（感谢 OpenClaw 的慷慨贡献 🙏），
适配 Hermes 平台，让 NEKO 猫娘感知你在 Hermes 中的工作活动。

架构：
  Hermes ──(HTTP POST)──▶ 本插件 HTTP Server ──(push_message)──▶ NEKO 伙伴
  端口 48922                                 解析/总结                  端口 48916

依赖：
  - Hermes 配置（HEARTBEAT.md / Skill / Hook）
  - NEKO 插件系统 SDK v2
"""

from __future__ import annotations

import json
import threading
import time
import os
import re
import sqlite3
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from pathlib import Path

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    message,
    Ok,
    Err,
    SdkError,
)

# ── 常量 ──────────────────────────────────────────────────────────────────

_DEFAULT_PORT = 48922
_DEFAULT_COOLDOWN = 30
_SOURCE_ID = "hermes_companion"

# 活动类型关键词映射
_ACTIVITY_KEYWORDS = {
    "edit": ["edit", "修改", "改", "修改文件", "编辑", "write"],
    "create": ["create", "新建", "创建"],
    "debug": ["debug", "fix", "bug", "调试", "修复", "错误", "问题"],
    "test": ["test", "pytest", "jest", "测试", "跑测试"],
    "git": ["git", "commit", "push", "pull", "merge", "branch", "提交", "分支"],
    "search": ["search", "find", "grep", "搜索", "查找", "找", "研究"],
    "refactor": ["refactor", "重构", "优化", "重写"],
    "build": ["build", "compile", "npm", "pip", "cargo", "构建", "编译", "安装", "install"],
    "deploy": ["deploy", "docker", "k8s", "部署", "上线"],
    "scrape": ["scrape", "crawl", "fetch", "爬虫", "抓取", "scrapling", "firecrawl"],
    "skill": ["skill", "技能", "clawhub"],
}

# ── 工具函数 ──────────────────────────────────────────────────────────────

_TZ_SHANGHAI = timezone(timedelta(hours=8))


def _now_str() -> str:
    return datetime.now(_TZ_SHANGHAI).strftime("%H:%M")


def _detect_activity_type(summary: str, tools_used: List[str], files_touched: List[str]) -> str:
    """根据摘要内容和工具使用情况检测活动类型。"""
    summary_lower = summary.lower()
    tools_str = " ".join(tools_used).lower()
    combined = f"{summary_lower} {tools_str}"

    for activity, keywords in _ACTIVITY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return activity

    if any(t in tools_used for t in ("Edit", "Write", "NotebookEdit")):
        return "edit"
    if "Bash" in tools_used:
        return "command"

    return "general"


def _get_file_short_name(filepath: str) -> str:
    """从完整路径提取简短文件名。"""
    if not filepath:
        return ""
    parts = filepath.replace("\\", "/").split("/")
    return parts[-1] if parts else filepath


# ── Hermes 会话解析器 ──────────────────────────────────────────────────────

# 重要工具列表（用于判断是否有重要操作）
_SIGNIFICANT_TOOLS = {
    "write_file", "patch", "terminal", "execute_code",
    "Edit", "Write", "Bash", "NotebookEdit",
}


def _get_hermes_db_path() -> str:
    """获取 Hermes state.db 的路径。"""
    home = os.path.expanduser("~")
    # 优先 HERMES_HOME 环境变量
    hermes_home = os.environ.get("HERMES_HOME", "")
    if hermes_home:
        db = os.path.join(hermes_home, "state.db")
        if os.path.exists(db):
            return db
    # 默认路径
    default = os.path.join(home, ".hermes", "state.db")
    if os.path.exists(default):
        return default
    return ""


class TranscriptParser:
    """解析 Hermes 会话数据库，提取最新一轮对话信息。

    优先从 transcript_path（JSONL）解析；
    若路径不存在或解析失败，回退到 Hermes SQLite 数据库。
    """

    @staticmethod
    def parse_latest_turn(transcript_path: str = "") -> Dict[str, Any]:
        """解析最新一轮对话。

        返回:
            {
                "user_message": str,
                "assistant_message": str,
                "tools_used": [str],
                "files_touched": [str],
                "has_significant_action": bool,
            }
        """
        result = {
            "user_message": "",
            "assistant_message": "",
            "tools_used": [],
            "files_touched": [],
            "has_significant_action": False,
        }

        # 尝试从 JSONL 转录文件解析（Claude Code 格式兼容）
        if transcript_path and os.path.exists(transcript_path):
            try:
                parsed = TranscriptParser._parse_jsonl(transcript_path)
                if parsed["user_message"] or parsed["assistant_message"]:
                    return parsed
            except Exception:
                pass

        # 回退：从 Hermes SQLite 数据库解析
        try:
            return TranscriptParser._parse_hermes_db()
        except Exception:
            return result

    @staticmethod
    def _parse_jsonl(transcript_path: str) -> Dict[str, Any]:
        """解析 JSONL 转录文件（Claude Code 格式兼容）。"""
        result = {
            "user_message": "",
            "assistant_message": "",
            "tools_used": [],
            "files_touched": [],
            "has_significant_action": False,
        }

        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            return result

        last_user_msg = ""
        last_assistant_text = ""
        tools_used: List[str] = []
        files_touched: List[str] = []
        found_assistant = False

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}
            role = msg.get("role", "") or entry.get("role", "")
            msg_type = entry.get("type", "")

            # 找助手回复
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
                                tools_used.append(block.get("name", ""))
                                tool_input = block.get("input", {})
                                for key in ("file_path", "path", "command"):
                                    val = tool_input.get(key, "")
                                    if val and isinstance(val, str) and ("/" in val or "\\" in val):
                                        files_touched.append(val)
                if text_parts:
                    found_assistant = True
                    last_assistant_text = " ".join(text_parts)

            # 找用户消息
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
                    if text_parts:
                        last_user_msg = " ".join(text_parts)
                        break
                    elif not has_tool_result:
                        break

            if msg_type == "last-prompt" and not last_user_msg:
                last_prompt = entry.get("lastPrompt", "")
                if last_prompt and len(last_prompt) > 5:
                    last_user_msg = last_prompt

        significant_tools = _SIGNIFICANT_TOOLS
        has_significant = bool(set(tools_used) & significant_tools)

        result["user_message"] = last_user_msg
        result["assistant_message"] = last_assistant_text
        result["tools_used"] = tools_used
        result["files_touched"] = list(dict.fromkeys(files_touched))
        result["has_significant_action"] = has_significant

        return result

    @staticmethod
    def _parse_hermes_db() -> Dict[str, Any]:
        """从 Hermes SQLite 数据库提取最新一轮对话。"""
        result = {
            "user_message": "",
            "assistant_message": "",
            "tools_used": [],
            "files_touched": [],
            "has_significant_action": False,
        }

        db_path = _get_hermes_db_path()
        if not db_path or not os.path.exists(db_path):
            return result

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            # 获取最新 session
            row = conn.execute(
                "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return result

            session_id = row["id"]

            # 获取最后 20 条消息（足够覆盖最新一轮）
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_name "
                "FROM messages WHERE session_id = ? "
                "ORDER BY timestamp DESC, id DESC LIMIT 20",
                (session_id,),
            ).fetchall()

            if not rows:
                return result

            last_user_msg = ""
            last_assistant_text = ""
            tools_used: List[str] = []
            files_touched: List[str] = []
            found_assistant = False

            for msg_row in rows:
                role = msg_row["role"]
                content = msg_row["content"] or ""
                tool_calls_raw = msg_row["tool_calls"]
                tool_name = msg_row["tool_name"] or ""

                # 找助手回复（第一个有文本内容的 assistant 消息）
                if role == "assistant" and not found_assistant:
                    # 解析 tool_calls
                    if tool_calls_raw:
                        try:
                            tc_list = json.loads(tool_calls_raw) if isinstance(tool_calls_raw, str) else tool_calls_raw
                            if isinstance(tc_list, list):
                                for tc in tc_list:
                                    fn = tc.get("function", {})
                                    name = fn.get("name", "")
                                    if name:
                                        tools_used.append(name)
                                    # 提取文件路径
                                    args_str = fn.get("arguments", "")
                                    if args_str:
                                        try:
                                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                                            if isinstance(args, dict):
                                                for key in ("path", "file_path", "command"):
                                                    val = args.get(key, "")
                                                    if val and isinstance(val, str) and ("/" in val or "\\" in val):
                                                        files_touched.append(val)
                                        except (json.JSONDecodeError, TypeError):
                                            pass
                        except (json.JSONDecodeError, TypeError):
                            pass

                    if tool_name and tool_name not in tools_used:
                        tools_used.append(tool_name)

                    # 只有有文本内容才算找到助手回复
                    if content and content.strip():
                        found_assistant = True
                        last_assistant_text = content.strip()

                # 找用户消息（助手回复之前的第一个 user 消息）
                if role == "user" and found_assistant and not last_user_msg:
                    if content and content.strip():
                        # 跳过 tool_result 类型
                        if not content.strip().startswith("{"):
                            last_user_msg = content.strip()
                        else:
                            try:
                                parsed = json.loads(content)
                                if isinstance(parsed, dict) and parsed.get("type") == "tool_result":
                                    continue
                                last_user_msg = content.strip()
                            except (json.JSONDecodeError, TypeError):
                                last_user_msg = content.strip()

            # 判断是否有重要操作
            has_significant = bool(set(tools_used) & _SIGNIFICANT_TOOLS)

            result["user_message"] = last_user_msg
            result["assistant_message"] = last_assistant_text
            result["tools_used"] = list(dict.fromkeys(tools_used))
            result["files_touched"] = list(dict.fromkeys(files_touched))
            result["has_significant_action"] = has_significant

        finally:
            conn.close()

        return result


# ── 双向通信注入器 ─────────────────────────────────────────────────────

class TmuxInjector:
    """tmux 注入器 — 向 WSL2 中 tmux 会话的 Hermes TUI 模拟键盘输入（主方案）"""

    def __init__(self, session_name: str = "neko-hermes", command: str = "hermes", capture_lines: int = 50):
        self.session = session_name
        self.command = command
        self.capture_lines = capture_lines

    def _tmux_cmd(self, args: list) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu", "tmux"] + args,
            capture_output=True, text=True, timeout=15
        )

    def is_session_alive(self) -> bool:
        try:
            result = self._tmux_cmd(["has-session", "-t", self.session])
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def create_session(self) -> dict:
        try:
            result = self._tmux_cmd(["new-session", "-d", "-s", self.session, self.command])
            if result.returncode == 0:
                return {"success": True, "message": f"tmux 会话 '{self.session}' 已在 WSL2 中创建"}
            else:
                return {"success": False, "error": result.stderr.strip()}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "创建会话超时"}
        except FileNotFoundError:
            return {"success": False, "error": "wsl 或 tmux 未找到"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_message(self, text: str, submit: bool = True) -> dict:
        try:
            if not self.is_session_alive():
                return {"success": False, "error": f"tmux 会话 '{self.session}' 不存在"}
            self._tmux_cmd(["send-keys", "-t", self.session, "-l", text])
            if submit:
                self._tmux_cmd(["send-keys", "-t", self.session, "Enter"])
            return {"success": True, "message": f"已注入: {text[:80]}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "发送消息超时"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def capture_output(self, lines: int = None) -> str:
        n = lines or self.capture_lines
        try:
            if not self.is_session_alive():
                return ""
            result = self._tmux_cmd(["capture-pane", "-t", self.session, "-p", "-S", f"-{n}"])
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def ensure_session(self) -> dict:
        if self.is_session_alive():
            return {"success": True, "message": "会话已存在", "existed": True}
        return self.create_session()

    def kill_session(self) -> dict:
        try:
            result = self._tmux_cmd(["kill-session", "-t", self.session])
            if result.returncode == 0:
                return {"success": True, "message": f"tmux 会话 '{self.session}' 已关闭"}
            else:
                return {"success": False, "error": result.stderr.strip()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_sessions(self) -> list:
        try:
            result = self._tmux_cmd(["list-sessions"])
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                return [line.strip() for line in lines if line.strip()]
            return []
        except Exception:
            return []


class HttpApiInjector:
    """HTTP API 注入器 — 通过 Hermes API Server 与 Hermes 通信（备用方案）"""

    def __init__(self, completions_url: str = "http://127.0.0.1:8642/v1/chat/completions",
                 responses_url: str = "http://127.0.0.1:8642/v1/responses",
                 api_key: str = "change-me-local-dev",
                 model: str = "hermes-agent"):
        self.completions_url = completions_url
        self.responses_url = responses_url
        self.api_key = api_key
        self.model = model
        self._previous_response_id = None

    def send_chat_completion(self, text: str, timeout_sec: int = 120) -> dict:
        """通过 /v1/chat/completions 发送消息"""
        try:
            payload = json.dumps({
                "model": self.model,
                "messages": [
                    {"role": "user", "content": text}
                ],
                "stream": False
            }).encode("utf-8")
            req = urllib.request.Request(
                self.completions_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                result = json.loads(resp.read().decode())
                reply = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"success": True, "reply": reply, "mode": "chat_completions"}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_response(self, text: str, timeout_sec: int = 120) -> dict:
        """通过 /v1/responses 发送消息（保持上下文）"""
        try:
            payload = json.dumps({
                "model": self.model,
                "input": text,
                "previous_response_id": self._previous_response_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.responses_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                result = json.loads(resp.read().decode())
                reply = result.get("output", [{}])[0].get("content", "") if isinstance(result.get("output"), list) else str(result.get("output", ""))
                self._previous_response_id = result.get("id")
                return {"success": True, "reply": reply, "mode": "responses", "response_id": self._previous_response_id}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_health(self) -> bool:
        """检查 API Server 是否可用"""
        try:
            req = urllib.request.Request(
                self.completions_url.replace("/v1/chat/completions", "/health"),
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False


# ── 活动摘要生成器 ───────────────────────────────────────────────────────

class ActivitySummarizer:
    """将 Hermes 活动转化为温暖的陪伴消息。"""

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
        "scrape": [
            "{name}在爬取数据，信息收集工作做得很好呢！",
            "{name}在抓取网页内容，数据采集小能手！",
        ],
        "skill": [
            "{name}在管理技能，能力越来越强了！",
            "{name}安装了新技能，武器库又扩充了！",
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
            "{name}在和 Hermes 一起努力工作呢，加油！",
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
        """生成活动摘要：用户做了什么 + Hermes 回复了什么。"""

        name = user_name or "你"

        # 提取用户意图
        user_intent = ""
        if user_msg and len(user_msg.strip()) > 0:
            user_intent = user_msg.strip()[:100]
            if len(user_msg) > 100:
                user_intent += "..."

        # 提取 Hermes 回复摘要
        hermes_reply = ""
        if assistant_msg and len(assistant_msg.strip()) > 0:
            hermes_reply = assistant_msg.strip()[:80]
            if len(assistant_msg) > 80:
                hermes_reply += "..."

        # 生成摘要
        if user_intent and hermes_reply:
            summary = f"{name}说：「{user_intent}」，Hermes 回复：「{hermes_reply}」"
        elif user_intent:
            summary = f"{name}说：「{user_intent}」"
        elif hermes_reply:
            summary = f"Hermes 回复了 {name}：「{hermes_reply}」"
        else:
            summary = f"{name}和 Hermes 在交流"

        return summary


# ── HTTP 服务器 ───────────────────────────────────────────────────────────

class _HookHTTPHandler(BaseHTTPRequestHandler):
    """处理 Hermes 推送的 HTTP 请求。"""

    plugin_instance: Optional["HermesCompanionPlugin"] = None

    def do_GET(self):
        """支持 GET 健康检查（curl -s http://localhost:48922/health）。"""
        if self.path == "/health":
            self._respond(200, {"status": "ok", "source": _SOURCE_ID})
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

        if self.path == "/hook/turn-end":
            self._handle_turn_end(data)
        elif self.path == "/hook/user-prompt-submit":
            self._handle_user_prompt_submit(data)
        elif self.path == "/push-summary":
            self._handle_push_summary(data)
        elif self.path == "/from-neko" or self.path == "/from-neko/tmux":
            self._handle_from_neko(data, mode="tmux")
        elif self.path == "/from-neko/api":
            self._handle_from_neko(data, mode="api")
        elif self.path == "/from-neko/responses":
            self._handle_from_neko(data, mode="responses")
        elif self.path == "/health":
            self._respond(200, {"status": "ok", "source": _SOURCE_ID})
        else:
            self._respond(404, {"error": "Not found"})

    def _handle_turn_end(self, data: dict):
        """处理 Hook 触发的事件。"""
        if self.plugin_instance:
            self.plugin_instance._on_turn_end(data)
        self._respond(200, {"status": "ok"})

    def _handle_push_summary(self, data: dict):
        """处理 Hermes 主动推送的摘要。"""
        if self.plugin_instance:
            self.plugin_instance._on_push_summary(data)
        self._respond(200, {"status": "ok"})

    def _handle_user_prompt_submit(self, data: dict):
        """处理用户提交 prompt 的事件。"""
        if self.plugin_instance:
            self.plugin_instance._on_user_prompt_submit(data)
        self._respond(200, {"status": "ok"})

    def _respond(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def _handle_from_neko(self, data: dict, mode: str = "tmux"):
        """处理 NEKO 猫娘向 Hermes 发送消息的请求。"""
        if self.plugin_instance:
            text = data.get("text", "") or data.get("content", "")
            sender = data.get("sender", "neko")
            if not text:
                self._respond(400, {"error": "text 或 content 字段必填"})
                return
            if mode == "tmux":
                result = self.plugin_instance._on_from_neko_tmux(text, sender)
            elif mode == "api":
                result = self.plugin_instance._on_from_neko_api(text, sender, use_responses=False)
            elif mode == "responses":
                result = self.plugin_instance._on_from_neko_api(text, sender, use_responses=True)
            else:
                result = {"error": f"不支持的模式: {mode}"}
            self._respond(200, result)
        else:
            self._respond(500, {"error": "Plugin not initialized"})

    def log_message(self, format, *args):
        """静默 HTTP 日志。"""
        pass


# ── 插件主类 ──────────────────────────────────────────────────────────────

@neko_plugin
class HermesCompanionPlugin(NekoPluginBase):
    """Hermes 伙伴陪伴插件 - 让 NEKO 伙伴在你开发时温暖陪伴。"""

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
        self._tmux_injector: Optional[TmuxInjector] = None
        self._http_api_injector: Optional[HttpApiInjector] = None
        self._tmux_config: Dict[str, Any] = {}
        self._http_api_config: Dict[str, Any] = {}

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
        except Exception as e:
            self.logger.warning("Failed to load config, using defaults: {}", e)
            cfg = {}

        hc_cfg = cfg.get("hermes_companion") if isinstance(cfg.get("hermes_companion"), dict) else {}

        try:
            self._port = int(hc_cfg.get("port", _DEFAULT_PORT))
        except (TypeError, ValueError):
            self._port = _DEFAULT_PORT
        try:
            self._cooldown = int(hc_cfg.get("cooldown_seconds", _DEFAULT_COOLDOWN))
        except (TypeError, ValueError):
            self._cooldown = _DEFAULT_COOLDOWN

        try:
            self._user_name = await self._load_user_name()
        except Exception as e:
            self.logger.warning("Failed to load user name: {}", e)
            self._user_name = "你"

        self._tmux_config = hc_cfg.get("tmux", {}) if isinstance(hc_cfg.get("tmux"), dict) else {}
        self._http_api_config = hc_cfg.get("http_api", {}) if isinstance(hc_cfg.get("http_api"), dict) else {}

        tmux_enabled = self._tmux_config.get("enabled", True)
        if tmux_enabled:
            session_name = self._tmux_config.get("session_name", "neko-hermes")
            command = self._tmux_config.get("command", "hermes")
            capture_lines = self._tmux_config.get("capture_lines", 50)
            self._tmux_injector = TmuxInjector(session_name, command, capture_lines)
            self.logger.info("TmuxInjector initialized: session={}, command={}", session_name, command)

        api_enabled = self._http_api_config.get("enabled", True)
        if api_enabled:
            completions_url = self._http_api_config.get("url", "http://127.0.0.1:8642/v1/chat/completions")
            responses_url = self._http_api_config.get("responses_url", "http://127.0.0.1:8642/v1/responses")
            api_key = self._http_api_config.get("api_key", "change-me-local-dev")
            model = self._http_api_config.get("model", "hermes-agent")
            self._http_api_injector = HttpApiInjector(completions_url, responses_url, api_key, model)
            self.logger.info("HttpApiInjector initialized: url={}", completions_url)

        try:
            self._start_http_server()
        except Exception as e:
            self.logger.error("Failed to start HTTP server on port {}: {}", self._port, e)

        try:
            self.register_static_ui("static")
        except Exception as e:
            self.logger.warning("Failed to register static UI: {}", e)

        self.logger.info(
            "HermesCompanion started: port={}, cooldown={}s, user={}",
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
        self.logger.info("HermesCompanion shutdown")
        return Ok({"status": "shutdown"})

    async def _load_user_name(self) -> str:
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
        try:
            stored = self.store._read_value("user_name", "")
            if stored:
                return str(stored)
        except Exception:
            pass
        return "你"

    def _start_http_server(self):
        _HookHTTPHandler.plugin_instance = self

        def run_server():
            try:
                server = HTTPServer(("127.0.0.1", self._port), _HookHTTPHandler)
                self._http_server = server
                self.logger.info("HTTP hook server listening on port {}", self._port)
                server.serve_forever()
            except Exception as e:
                self.logger.error("HTTP server error: {}", e)

        self._http_thread = threading.Thread(
            target=run_server,
            daemon=True,
            name="hermes-companion-http",
        )
        self._http_thread.start()

    def _stop_http_server(self):
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None

    def _on_turn_end(self, data: dict):
        """处理 Hook 触发的事件。"""
        transcript_path = data.get("transcript_path", "")
        if not transcript_path:
            return

        now = time.time()
        if now - self._last_push_time < self._cooldown:
            return

        turn_info = TranscriptParser.parse_latest_turn(transcript_path)
        if not turn_info.get("has_significant_action", False):
            return

        user_msg = turn_info.get("user_message", "")
        assistant_msg = turn_info.get("assistant_message", "")
        tools_used = turn_info.get("tools_used", [])
        files_touched = turn_info.get("files_touched", [])

        activity_type = _detect_activity_type(user_msg + " " + assistant_msg, tools_used, files_touched)
        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=files_touched,
            user_msg=user_msg,
            tools_used=tools_used,
            assistant_msg=assistant_msg,
        )

        self._notify_companion(summary, activity_type, turn_info)
        self._last_push_time = now
        self._add_event_log(activity_type, summary, turn_info)
        self.logger.info("Pushed from hook: type={}", activity_type)

    def _on_push_summary(self, data: dict):
        """处理 Hermes 主动推送的摘要。"""
        now = time.time()
        if now - self._last_push_time < self._cooldown:
            return

        user_msg = data.get("user_message", "")
        assistant_msg = data.get("assistant_message", "")
        activity_type = data.get("activity_type", "general")

        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=[],
            user_msg=user_msg,
            tools_used=[],
            assistant_msg=assistant_msg,
        )

        turn_info = {
            "tools_used": [],
            "files_touched": [],
            "user_message": user_msg,
            "assistant_message": assistant_msg,
        }
        self._notify_companion(summary, activity_type, turn_info)
        self._last_push_time = time.time()
        self._add_event_log(activity_type, summary, turn_info)
        self.logger.info("Pushed summary from Hermes: type={}", activity_type)

    def _on_user_prompt_submit(self, data: dict):
        """处理用户提交 prompt 的事件（Hook: UserPromptSubmit）。"""
        # 记录用户意图，尝试解析最新对话
        transcript_path = data.get("transcript_path", "")
        user_prompt = data.get("prompt", "") or data.get("user_message", "")

        now = time.time()
        if now - self._last_push_time < self._cooldown:
            return

        # 尝试从数据库解析最新对话
        turn_info = TranscriptParser.parse_latest_turn(transcript_path)
        user_msg = turn_info.get("user_message", "") or user_prompt
        assistant_msg = turn_info.get("assistant_message", "")
        tools_used = turn_info.get("tools_used", [])
        files_touched = turn_info.get("files_touched", [])

        if not user_msg and not assistant_msg:
            return

        activity_type = _detect_activity_type(
            user_msg + " " + assistant_msg, tools_used, files_touched
        )
        summary = ActivitySummarizer.summarize(
            user_name=self._user_name,
            activity_type=activity_type,
            files_touched=files_touched,
            user_msg=user_msg,
            tools_used=tools_used,
            assistant_msg=assistant_msg,
        )

        self._notify_companion(summary, activity_type, turn_info)
        self._last_push_time = now
        self._add_event_log(activity_type, summary, turn_info)
        self.logger.info("Pushed from user-prompt-submit: type={}", activity_type)

    def _notify_companion(self, summary: str, activity_type: str, turn_info: dict):
        """通过 push_message 将摘要发送给 NEKO 伙伴。"""
        try:
            if not hasattr(self.ctx, 'push_message'):
                self.logger.error("ctx has no push_message method")
                return

            self.ctx.push_message(
                source=_SOURCE_ID,
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
        except Exception as e:
            self.logger.error("Failed to push: {}", e)

    def _add_event_log(self, activity_type: str, summary: str, turn_info: dict):
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

    # ── 插件入口点 ──

    @plugin_entry(
        id="get_status",
        name="获取状态",
        description="获取 Hermes Companion 插件的运行状态",
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
        description="获取最近的 Hermes 活动事件日志",
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
        description="设置 Hermes Companion 使用的用户名",
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
    async def set_cooldown(self, seconds: int = 30, **_):
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
        summary = f"{self._user_name}，这是一条来自 Hermes Companion 的测试消息～连接正常！"
        self._notify_companion(summary, "test", {"tools_used": [], "files_touched": []})
        return Ok({"sent": True, "message": summary})

    @plugin_entry(
        id="clear_events",
        name="清空事件日志",
        description="清空所有已记录的活动事件",
    )
    async def clear_events(self, **_):
        with self._event_log_lock:
            count = len(self._event_log)
            self._event_log.clear()
        return Ok({"cleared": count})

    # ── 双向通信：从 NEKO 到 Hermes ───────────────────────────────────

    def _on_from_neko_tmux(self, text: str, sender: str = "neko") -> dict:
        if not self._tmux_injector:
            return {"success": False, "error": "tmux 注入器未初始化"}
        auto_create = self._tmux_config.get("auto_create", True)
        if auto_create:
            ensure = self._tmux_injector.ensure_session()
            if not ensure.get("success"):
                return ensure
        result = self._tmux_injector.send_message(text)
        if result.get("success") and self._tmux_config.get("capture_reply", True):
            time.sleep(2)
            reply = self._tmux_injector.capture_output()
            if reply:
                result["captured_reply"] = reply
        return result

    def _on_from_neko_api(self, text: str, sender: str = "neko", use_responses: bool = False) -> dict:
        """通过 HTTP API 与 Hermes 通信"""
        if not self._http_api_injector:
            return {"success": False, "error": "HTTP API 注入器未初始化"}
        if use_responses:
            return self._http_api_injector.send_response(text)
        return self._http_api_injector.send_chat_completion(text)

    @plugin_entry(
        id="inject_tmux",
        name="发送消息到 Hermes",
        description="将消息发送到 Hermes TUI 会话中，等同于用户在 Hermes 的对话框中输入并发送消息。Hermes 会收到消息并执行对应任务。如果 Hermes 未启动，会自动启动它。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要发送给 Hermes 的消息内容，等同于用户在 Hermes 中输入的指令"},
                "capture_reply": {"type": "boolean", "description": "是否捕获 Hermes 的回复", "default": True},
            },
            "required": ["text"],
        },
        llm_result_fields=["success", "message", "captured_reply"],
    )
    async def inject_tmux(self, text: str, capture_reply: bool = True, **_):
        if not self._tmux_injector:
            return Err(SdkError("tmux 注入器未初始化"))
        auto_create = self._tmux_config.get("auto_create", True)
        if auto_create:
            ensure = self._tmux_injector.ensure_session()
            if not ensure.get("success"):
                return Err(SdkError(ensure.get("error", "未知错误")))
        result = self._tmux_injector.send_message(text)
        if result.get("success") and capture_reply and self._tmux_config.get("capture_reply", True):
            time.sleep(2)
            reply = self._tmux_injector.capture_output()
            if reply:
                result["captured_reply"] = reply
                self._notify_companion(reply, "general", {"tools_used": [], "files_touched": []})
        return Ok(result)

    @plugin_entry(
        id="inject_api",
        name="注入消息到 Hermes API",
        description="通过 Hermes API Server 发送消息（/v1/chat/completions）",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要发送的消息内容"},
                "use_responses": {"type": "boolean", "description": "是否使用 /v1/responses（保持上下文）", "default": False},
            },
            "required": ["text"],
        },
        llm_result_fields=["success", "reply"],
    )
    async def inject_api(self, text: str, use_responses: bool = False, **_):
        if not self._http_api_injector:
            return Err(SdkError("HTTP API 注入器未初始化"))
        if use_responses:
            result = self._http_api_injector.send_response(text)
        else:
            result = self._http_api_injector.send_chat_completion(text)
        if result.get("success") and result.get("reply"):
            self._notify_companion(result["reply"], "general", {"tools_used": [], "files_touched": []})
        return Ok(result)

    @plugin_entry(
        id="tmux_status",
        name="tmux 会话状态",
        description="检查 WSL2 tmux 会话状态",
        llm_result_fields=["session_alive", "session_name", "sessions"],
    )
    async def tmux_status(self, **_):
        result = {"session_name": self._tmux_config.get("session_name", "neko-hermes")}
        if self._tmux_injector:
            result["session_alive"] = self._tmux_injector.is_session_alive()
            result["sessions"] = self._tmux_injector.list_sessions()
        else:
            result["session_alive"] = False
            result["sessions"] = []
        return Ok(result)

    @plugin_entry(
        id="capture_tmux",
        name="捕获 TUI 输出",
        description="捕获 Hermes TUI 的最新输出内容",
        input_schema={
            "type": "object",
            "properties": {
                "lines": {"type": "integer", "description": "要捕获的行数", "default": 50},
            },
        },
        llm_result_fields=["output"],
    )
    async def capture_tmux(self, lines: int = 50, **_):
        if not self._tmux_injector:
            return Err(SdkError("tmux 注入器未初始化"))
        output = self._tmux_injector.capture_output(lines)
        return Ok({"output": output, "lines": lines})

    @plugin_entry(
        id="create_tmux_session",
        name="启动 Hermes",
        description="在 WSL2 的 tmux 中启动 Hermes TUI 交互会话（等同于用户运行 hermes 命令）。启动后 Hermes 会在后台持续运行，用户可以随时通过 wsl tmux attach 查看。这是启动 Hermes 的首选方法。",
    )
    async def create_tmux_session(self, **_):
        if not self._tmux_injector:
            return Err(SdkError("tmux 注入器未初始化"))
        return Ok(self._tmux_injector.create_session())

    @plugin_entry(
        id="kill_tmux_session",
        name="关闭 tmux 会话",
        description="关闭 WSL2 tmux 会话",
    )
    async def kill_tmux_session(self, **_):
        if not self._tmux_injector:
            return Err(SdkError("tmux 注入器未初始化"))
        return Ok(self._tmux_injector.kill_session())

    @plugin_entry(
        id="api_health",
        name="检查 API 健康",
        description="检查 Hermes API Server 是否可用",
    )
    async def api_health(self, **_):
        if not self._http_api_injector:
            return Err(SdkError("HTTP API 注入器未初始化"))
        healthy = self._http_api_injector.check_health()
        return Ok({"healthy": healthy, "url": self._http_api_config.get("url", "")})

    @message(
        id="inject_to_hermes_tui",
        name="转发消息到 Hermes TUI",
        description="NEKO 聊天时自动将消息注入到 Hermes TUI",
    )
    async def inject_to_hermes_tui(self, text: str = "", content: str = "", sender: str = "", **_):
        msg = text or content or ""
        if not msg:
            return Ok({"injected": False, "reason": "空消息"})
        if not self._tmux_injector:
            return Ok({"injected": False, "reason": "tmux 注入器未初始化"})
        auto_create = self._tmux_config.get("auto_create", True)
        if auto_create:
            self._tmux_injector.ensure_session()
        result = self._tmux_injector.send_message(msg)
        if result.get("success"):
            self.logger.info("Auto-injected to Hermes TUI: {}", msg[:80])
            return Ok({"injected": True, "session": self._tmux_injector.session})
        return Ok({"injected": False, "error": result.get("error")})
"""
猫娘小老师 (Neko Teacher) v1.0.0

从 Obsidian 知识库导入 Markdown 文档，让猫娘定时为你讲解知识。
核心功能：
1. 扫描并导入外部 Obsidian 库中的 .md 文件；
2. 按固定字数或按 Markdown 标题将文档分片；
3. 支持单篇文档或整个文件夹的顺序推送；
4. 定时策略：固定间隔、随机间隔、每日定时、一次性计划；
5. 推送消息直达猫娘，可朗读或讲解。
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

# ── 常量 ──
_STORE_KEY_SETTINGS = "neko_teacher_settings_v1"
_STORE_KEY_TASKS = "neko_teacher_tasks_v1"
_STORE_KEY_HISTORY = "neko_teacher_history_v1"

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "library_path": "",
    "enabled": True,
    "default_chunk_mode": "by_heading",       # by_heading | fixed_length
    "default_chunk_size": 500,              # 每片字数（仅 fixed_length）
    "default_schedule": {
        "type": "interval",                 # interval | daily | once
        "interval_seconds": 300,            # 5 分钟
        "random_interval": False,
        "random_min": 60,
        "random_max": 600,
        "daily_time": "09:00",
        "once_datetime": "",
    },
    "target_lanlan": "",
    "auto_start_last_task": True,
    "push_prompt": (
        "这是主人今天要学习的知识片段哦~请用温柔可爱的语气为主人朗读或讲解这部分内容，"
        "可以适当补充例子帮助理解。讲完后可以鼓励一下主人喵~"
    ),
}

_TASK_STATUS_PENDING = "pending"
_TASK_STATUS_RUNNING = "running"
_TASK_STATUS_PAUSED = "paused"
_TASK_STATUS_COMPLETED = "completed"
_TASK_STATUS_STOPPED = "stopped"

_CHUNK_MODES = {"fixed_length", "by_heading"}
_SCHEDULE_TYPES = {"interval", "daily", "once"}

# ── 工具函数 ──

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).astimezone(timezone.utc).isoformat()


def _safe_text(raw: object, fallback: str = "") -> str:
    text = str(raw or "").strip()
    return text if text else fallback


def _coerce_bool(raw: object, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    return text in {"1", "true", "yes", "on", "y"}


def _coerce_int(raw: object, default: int = 0, min_val: int | None = None, max_val: int | None = None) -> int:
    try:
        val = int(float(str(raw or "").strip()))
    except Exception:
        val = default
    if min_val is not None:
        val = max(min_val, val)
    if max_val is not None:
        val = min(max_val, val)
    return val


def _parse_daily_time(text: str) -> Tuple[int, int]:
    """解析 HH:MM 为 (hour, minute)"""
    t = _safe_text(text, "09:00")
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        return (9, 0)
    h, minute = int(m.group(1)), int(m.group(2))
    h = max(0, min(23, h))
    minute = max(0, min(59, minute))
    return (h, minute)


def _today_at_local(hour: int, minute: int) -> datetime:
    """返回今天指定本地时间的 UTC datetime"""
    now = datetime.now()
    local_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local_dt.astimezone(timezone.utc)


def _build_chunks_fixed_length(text: str, chunk_size: int = 500) -> List[str]:
    """按字数分片，优先在句子边界截断。"""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining.strip())
            break

        # 在 chunk_size 附近找最佳断点
        candidate = remaining[:chunk_size]
        # 优先找段落边界
        best = candidate.rfind("\n\n")
        if best > chunk_size * 0.3:
            split_at = best + 2
        else:
            # 其次找句子结束标点后紧跟空格或换行
            best = -1
            for m in re.finditer(r'[。！？.!?~]\s', candidate):
                if m.end() > chunk_size * 0.3:
                    best = m.end()
            if best == -1:
                # 再试换行
                best = candidate.rfind("\n")
                if best > chunk_size * 0.3:
                    split_at = best + 1
                else:
                    split_at = chunk_size
            else:
                split_at = best

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()

    return [c for c in chunks if c]


def _build_chunks_by_heading(text: str) -> List[str]:
    """按 Markdown 标题分片，每个标题及其下方内容为一个片段。"""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    chunks: List[str] = []
    current: List[str] = []

    for line in lines:
        if re.match(r'^#{1,6}\s+', line):
            if current:
                chunk = "\n".join(current).strip()
                if chunk:
                    chunks.append(chunk)
                current = []
        current.append(line)

    if current:
        chunk = "\n".join(current).strip()
        if chunk:
            chunks.append(chunk)

    # 如果没有标题，整篇作为一个片段
    if not chunks and text.strip():
        chunks = [text.strip()]

    return chunks


def _strip_markdown_meta(text: str) -> str:
    """去除 YAML frontmatter"""
    text = text.replace("\r\n", "\n")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]
    return text.strip()


@neko_plugin
class NekoTeacherPlugin(NekoPluginBase):
    """猫娘小老师插件：Obsidian 知识库定时推送"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._settings: Dict[str, Any] = dict(_DEFAULT_SETTINGS)
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []
        self._next_push_time: float = 0.0
        self._active_task_id: Optional[str] = ""

        self._fallback_settings_path = self.config_dir / "settings.json"
        self._fallback_tasks_path = self.config_dir / "tasks.json"
        self._fallback_history_path = self.config_dir / "history.json"

        # ⭐ 启动时把 i18n.json 注入到 index.html 占位符
        self._inline_i18n_into_html()

        self._store_available = False
        self._load_all_fallback()

    # ── 持久化辅助 ──

    def _test_store(self) -> bool:
        if not self.store.enabled:
            self.store.enabled = True
        try:
            test_key = "_neko_teacher_store_test"
            self.store._write_value(test_key, {"test": True, "ts": time.time()})
            result = self.store._read_value(test_key, None)
            return isinstance(result, dict) and result.get("test")
        except Exception as e:
            self.logger.warning("Store test failed: {}", e)
            return False

    def _load_all_fallback(self):
        for path, key, default in [
            (self._fallback_settings_path, "settings", _DEFAULT_SETTINGS),
            (self._fallback_tasks_path, "tasks", {}),
            (self._fallback_history_path, "history", []),
        ]:
            try:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if key == "settings" and isinstance(data, dict):
                            merged = dict(_DEFAULT_SETTINGS)
                            merged.update(data)
                            self._settings = merged
                        elif key == "tasks" and isinstance(data, dict):
                            self._tasks = data
                        elif key == "history" and isinstance(data, list):
                            self._history = data[-500:]  # 保留最近 500 条
            except Exception as e:
                self.logger.warning("Load fallback {} failed: {}", key, e)

    def _save_all_fallback(self):
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self._fallback_settings_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
            with open(self._fallback_tasks_path, "w", encoding="utf-8") as f:
                json.dump(self._tasks, f, ensure_ascii=False, indent=2)
            with open(self._fallback_history_path, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning("Save fallback failed: {}", e)

    def _load_settings(self) -> Dict[str, Any]:
        if self._store_available:
            try:
                data = self.store._read_value(_STORE_KEY_SETTINGS, None)
                if isinstance(data, dict):
                    merged = dict(_DEFAULT_SETTINGS)
                    merged.update(data)
                    self._settings = merged
                    return merged
            except Exception as e:
                self.logger.warning("Load settings from store failed: {}", e)
        return dict(self._settings)

    def _inline_i18n_into_html(self):
        """把 static/i18n.json 注入到 static/index.html 的 __I18N_JSON__ 占位符。
        这样插件启动时不需要 fetch 就能拿到 i18n 数据 —— 解决 N.E.K.O webview fetch 路径 404 问题。"""
        try:
            import re
            static_dir = self.config_dir / "static"
            html_path = static_dir / "index.html"
            i18n_path = static_dir / "i18n.json"
            if not (html_path.exists() and i18n_path.exists()):
                return
            html = html_path.read_text(encoding="utf-8")
            i18n_text = i18n_path.read_text(encoding="utf-8")
            # JSON 内 </script> 需转义，避免破坏外层 script 标签
            i18n_safe = i18n_text.replace("</", "<\\/")
            if "__I18N_JSON__" in html:
                # 替换占位符
                new_html = re.sub(
                    r'<script id="i18n-inline" type="application/json">\s*__I18N_JSON__\s*</script>',
                    f'<script id="i18n-inline" type="application/json">{i18n_safe}</script>',
                    html,
                    count=1,
                )
            elif "i18n-inline" in html:
                # 占位符已被替换但 i18n 数据可能不是最新的 —— 重新写
                new_html = re.sub(
                    r'<script id="i18n-inline" type="application/json">.*?</script>',
                    f'<script id="i18n-inline" type="application/json">{i18n_safe}</script>',
                    html,
                    count=1,
                    flags=re.DOTALL,
                )
            else:
                # index.html 完全没有 i18n-inline 标签 —— 在 </body> 前注入
                new_html = html.replace(
                    "</body>",
                    f'<script id="i18n-inline" type="application/json">{i18n_safe}</script>\n</body>',
                    1,
                )
            html_path.write_text(new_html, encoding="utf-8")
            self.logger.info("Inlined i18n into index.html ({} bytes)", len(new_html))
        except Exception as e:
            self.logger.warning("inline i18n failed: {}", e)

    def _save_settings(self):
        if self._store_available:
            try:
                self.store._write_value(_STORE_KEY_SETTINGS, self._settings)
            except Exception as e:
                self.logger.warning("Save settings to store failed: {}", e)
        self._save_all_fallback()

    def _load_tasks(self) -> Dict[str, Any]:
        if self._store_available:
            try:
                data = self.store._read_value(_STORE_KEY_TASKS, None)
                if isinstance(data, dict):
                    self._tasks = data
                    return data
            except Exception as e:
                self.logger.warning("Load tasks from store failed: {}", e)
        return dict(self._tasks)

    def _save_tasks(self):
        if self._store_available:
            try:
                self.store._write_value(_STORE_KEY_TASKS, self._tasks)
            except Exception as e:
                self.logger.warning("Save tasks to store failed: {}", e)
        self._save_all_fallback()

    def _add_history(self, entry: Dict[str, Any]):
        self._history.append(entry)
        if len(self._history) > 500:
            self._history = self._history[-500:]
        if self._store_available:
            try:
                self.store._write_value(_STORE_KEY_HISTORY, self._history)
            except Exception:
                pass
        self._save_all_fallback()

    # ── 文件扫描 ──

    def _build_folder_tree(self, root_path: str) -> Dict[str, Any]:
        """扫描根目录，返回 {name, path, type, children, file_count} 树结构"""
        try:
            root = Path(root_path).expanduser().resolve()
        except Exception as e:
            return {"error": f"路径解析失败: {root_path} - {e}"}
        if not root.exists():
            return {"error": f"路径不存在: {root}"}
        if not root.is_dir():
            return {"error": f"路径不是文件夹: {root}"}

        def _scan_dir(dir_path: Path):
            """递归扫描目录，返回树节点列表"""
            nodes = []
            try:
                entries = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                return nodes

            for entry in entries:
                # 跳过 Obsidian 配置目录和隐藏文件
                if entry.name.startswith(".") or entry.name == ".obsidian":
                    if entry.name == ".obsidian" and entry.is_dir():
                        continue
                    continue

                if entry.is_dir():
                    children = _scan_dir(entry)
                    md_count = sum(1 for c in children if c["type"] == "file")
                    for c in children:
                        if c["type"] == "folder":
                            md_count += c.get("file_count", 0)
                    nodes.append({
                        "name": entry.name,
                        "path": str(entry),
                        "type": "folder",
                        "children": children,
                        "file_count": md_count,
                    })
                elif entry.suffix.lower() == ".md":
                    nodes.append({
                        "name": entry.name,
                        "path": str(entry),
                        "type": "file",
                        "title": entry.stem,
                        "size": entry.stat().st_size,
                    })
            return nodes

        children = _scan_dir(root)
        md_count = sum(1 for c in children if c["type"] == "file")
        for c in children:
            if c["type"] == "folder":
                md_count += c.get("file_count", 0)

        return {
            "name": root.name,
            "path": str(root),
            "type": "folder",
            "children": children,
            "file_count": md_count,
        }

    def _scan_markdown_files(self, root_path: str) -> List[Dict[str, Any]]:
        root = Path(root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return []
        results: List[Dict[str, Any]] = []
        for p in root.rglob("*.md"):
            if p.is_file() and not p.name.startswith("."):
                rel = p.relative_to(root).as_posix()
                results.append({
                    "path": str(p),
                    "relative": rel,
                    "name": p.name,
                    "title": p.stem,
                    "size": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                })
        results.sort(key=lambda x: x["relative"])
        return results

    def _read_and_chunk_document(self, file_path: str, chunk_mode: str, chunk_size: int) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {"error": "文件不存在"}
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as e:
            return {"error": f"读取失败: {e}"}

        text = _strip_markdown_meta(raw)
        if chunk_mode == "by_heading":
            chunks = _build_chunks_by_heading(text)
        else:
            chunks = _build_chunks_fixed_length(text, max(100, chunk_size))

        return {
            "path": str(path),
            "name": path.name,
            "title": path.stem,
            "total_chars": len(text),
            "chunk_mode": chunk_mode,
            "chunk_size": chunk_size,
            "total_chunks": len(chunks),
            "chunks": chunks,
        }

    # ── 调度与推送 ──

    def _calculate_next_interval(self, schedule: Dict[str, Any]) -> float:
        """根据 schedule 计算下一次等待秒数"""
        if schedule.get("random_interval"):
            lo = max(10, schedule.get("random_min", 60))
            hi = max(lo, schedule.get("random_max", 600))
            return random.uniform(lo, hi)
        return max(10.0, float(schedule.get("interval_seconds", 300)))

    def _should_start_daily_task(self, task: Dict[str, Any]) -> bool:
        schedule = task.get("schedule", {})
        if schedule.get("type") != "daily":
            return False
        h, m = _parse_daily_time(schedule.get("daily_time", "09:00"))
        target_dt = _today_at_local(h, m)
        now = _utc_now()
        last_start = task.get("last_daily_start")
        if last_start:
            try:
                last_dt = datetime.fromisoformat(last_start)
                if last_dt.date() == now.date():
                    return False
            except Exception:
                pass
        return now >= target_dt

    def _should_start_once_task(self, task: Dict[str, Any]) -> bool:
        schedule = task.get("schedule", {})
        if schedule.get("type") != "once":
            return False
        raw = _safe_text(schedule.get("once_datetime"), "")
        if not raw:
            return False
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                local_tz = datetime.now().astimezone().tzinfo or timezone.utc
                dt = dt.replace(tzinfo=local_tz)
            return _utc_now() >= dt.astimezone(timezone.utc) and not task.get("once_triggered")
        except Exception:
            return False

    def _resolve_target_lanlan(self, task: Dict[str, Any]) -> Optional[str]:
        explicit = _safe_text(task.get("target_lanlan"), "")
        if explicit:
            return explicit
        explicit = _safe_text(self._settings.get("target_lanlan"), "")
        if explicit:
            return explicit
        # 尝试从 ctx 或环境变量读取
        for attr in ("_current_lanlan",):
            val = getattr(self.ctx, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for env in ("NEKO_TARGET_LANLAN", "NEKO_LANLAN_NAME", "NEKO_HER_NAME"):
            val = os.getenv(env, "").strip()
            if val:
                return val
        return None

    def _push_chunk(self, task: Dict[str, Any], doc: Dict[str, Any], chunk: str, chunk_index: int):
        """向猫娘推送一个知识片段"""
        target_lanlan = self._resolve_target_lanlan(task)
        doc_title = doc.get("title", "未命名文档")
        total_chunks = doc.get("total_chunks", 1)
        progress = f"({chunk_index + 1}/{total_chunks})"

        prompt = _safe_text(self._settings.get("push_prompt"), _DEFAULT_SETTINGS["push_prompt"])

        content_text = (
            f"📚 今日知识讲解 {progress}\n"
            f"📖 文档：《{doc_title}》\n\n"
            f"{chunk}\n\n"
            f"{prompt}"
        )

        try:
            self.ctx.push_message(
                source="neko_teacher",
                parts=[{
                    "type": "text",
                    "text": content_text,
                }],
                visibility=[],
                ai_behavior="respond",
                priority=6,
                metadata={
                    "neko_teacher": True,
                    "task_id": task.get("task_id"),
                    "doc_title": doc_title,
                    "chunk_index": chunk_index,
                    "total_chunks": total_chunks,
                    "timestamp": _iso(),
                },
                target_lanlan=target_lanlan,
            )
            self.logger.info(
                "Pushed chunk {}/{} of [{}] to {}",
                chunk_index + 1, total_chunks, doc_title, target_lanlan or "default"
            )
        except Exception as e:
            self.logger.exception("Push message failed: {}", e)
            raise

    def _do_push_next(self, task_id: str) -> bool:
        """执行一次推送，返回是否还有后续"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.get("status") != _TASK_STATUS_RUNNING:
                return False

            # 使用 docs_full 获取完整 chunks 数据
            docs = task.get("docs_full", task.get("docs", []))
            doc_index = task.get("current_doc_index", 0)
            chunk_index = task.get("current_chunk_index", 0)

            if doc_index >= len(docs):
                task["status"] = _TASK_STATUS_COMPLETED
                task["finished_at"] = _iso()
                self._save_tasks()
                return False

            doc = docs[doc_index]
            chunks = doc.get("chunks", [])

            if chunk_index >= len(chunks):
                # 当前文档已完成，进入下一篇
                doc_index += 1
                chunk_index = 0
                task["current_doc_index"] = doc_index
                task["current_chunk_index"] = chunk_index
                if doc_index >= len(docs):
                    task["status"] = _TASK_STATUS_COMPLETED
                    task["finished_at"] = _iso()
                    self._save_tasks()
                    return False
                doc = docs[doc_index]
                chunks = doc.get("chunks", [])

            chunk = chunks[chunk_index]
            try:
                self._push_chunk(task, doc, chunk, chunk_index)
            except Exception as e:
                task["last_error"] = str(e)
                self._save_tasks()
                return True  # 下次重试

            self._add_history({
                "task_id": task_id,
                "task_name": task.get("name", ""),
                "doc_title": doc.get("title", ""),
                "doc_path": doc.get("path", ""),
                "chunk_index": chunk_index,
                "total_chunks": doc.get("total_chunks", 1),
                "timestamp": _iso(),
            })

            chunk_index += 1
            task["current_chunk_index"] = chunk_index
            task["updated_at"] = _iso()
            self._save_tasks()

            return True

    def _scheduler_loop(self):
        time.sleep(1.0)
        self.logger.info("NekoTeacher scheduler thread started (tid={})", threading.get_ident())

        while not self._stop_event.is_set():
            try:
                now = time.time()
                with self._lock:
                    settings = self._load_settings()
                    if not settings.get("enabled", True):
                        wait_sec = 60.0
                    else:
                        active_id = self._active_task_id
                        next_time = self._next_push_time
                        active_task_running = False
                        if active_id and active_id in self._tasks:
                            active_task_running = self._tasks[active_id].get("status") == _TASK_STATUS_RUNNING

                        if active_id and active_task_running:
                            if now >= next_time:
                                # 锁外执行推送，避免与 _do_push_next 内部锁嵌套死锁
                                wait_sec = 1.0
                            else:
                                wait_sec = max(1.0, next_time - now)
                        else:
                            # 检查 pending 任务中是否有 daily/once 该启动的
                            wait_sec = 5.0
                            auto_start_tid = None
                            for tid, task in self._tasks.items():
                                if task.get("status") != _TASK_STATUS_PENDING:
                                    continue
                                schedule = task.get("schedule", {})
                                should_start = False
                                if schedule.get("type") == "daily":
                                    should_start = self._should_start_daily_task(task)
                                elif schedule.get("type") == "once":
                                    should_start = self._should_start_once_task(task)
                                if should_start:
                                    auto_start_tid = tid
                                    break
                            if auto_start_tid:
                                task = self._tasks[auto_start_tid]
                                schedule = task.get("schedule", {})
                                task["status"] = _TASK_STATUS_RUNNING
                                task["started_at"] = _iso()
                                if schedule.get("type") == "daily":
                                    task["last_daily_start"] = _iso()
                                if schedule.get("type") == "once":
                                    task["once_triggered"] = True
                                self._active_task_id = auto_start_tid
                                interval = self._calculate_next_interval(schedule)
                                self._next_push_time = now + interval
                                self._save_tasks()
                                self.logger.info("Auto-started task {} (type={})", auto_start_tid, schedule.get("type"))
                                wait_sec = 1.0

                # 锁外执行推送（避免死锁）
                if active_id and active_task_running and now >= next_time:
                    has_more = self._do_push_next(active_id)
                    with self._lock:
                        if has_more:
                            schedule = self._tasks[active_id].get("schedule", {})
                            interval = self._calculate_next_interval(schedule)
                            self._next_push_time = now + interval
                            self.logger.info(
                                "Next push in {:.0f}s for task {}", interval, active_id
                            )
                        else:
                            self._active_task_id = None
                            self._next_push_time = 0.0

            except Exception:
                self.logger.exception("Scheduler loop error, retry in 10s")
                wait_sec = 10.0

            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            self._wake_event.wait(timeout=min(wait_sec, 10.0))
            if self._stop_event.is_set():
                break

        self.logger.info("NekoTeacher scheduler thread exiting")

    # ── 生命周期 ──

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._store_available = self._test_store()
        self._load_settings()
        self._load_tasks()

        # 重建运行状态
        with self._lock:
            for tid, task in self._tasks.items():
                if task.get("status") == _TASK_STATUS_RUNNING:
                    task["status"] = _TASK_STATUS_PAUSED
                    task["last_error"] = "插件重载，任务已暂停，请手动恢复"
                    task["updated_at"] = _iso()
            self._active_task_id = None
            self._next_push_time = 0.0
            self._save_tasks()

        self._stop_event.clear()
        self._wake_event.clear()

        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._stop_event.set()
            self._wake_event.set()
            self._scheduler_thread.join(timeout=5.0)
            self._stop_event.clear()
            self._wake_event.clear()

        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="neko-teacher-scheduler",
        )
        self._scheduler_thread.start()

        self.register_static_ui("static")

        return Ok({
            "status": "running",
            "store_available": self._store_available,
            "tasks_count": len(self._tasks),
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._stop_event.set()
        self._wake_event.set()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=5.0)
        with self._lock:
            for task in self._tasks.values():
                if task.get("status") == _TASK_STATUS_RUNNING:
                    task["status"] = _TASK_STATUS_PAUSED
                    task["last_error"] = "插件关闭，任务已暂停"
                    task["updated_at"] = _iso()
            self._save_tasks()
        return Ok({"status": "shutdown"})

    # ── API: 设置 ──

    @plugin_entry(id="get_settings", name="获取设置", description="获取猫娘小老师的全局设置")
    async def get_settings(self, **_):
        settings = self._load_settings()
        return Ok({"settings": settings})

    @plugin_entry(
        id="update_settings",
        name="更新设置",
        description="更新全局设置",
        input_schema={
            "type": "object",
            "properties": {
                "library_path": {"type": "string"},
                "enabled": {"type": "boolean"},
                "default_chunk_mode": {"type": "string", "enum": ["fixed_length", "by_heading"]},
                "default_chunk_size": {"type": "integer"},
                "target_lanlan": {"type": "string"},
                "auto_start_last_task": {"type": "boolean"},
                "push_prompt": {"type": "string"},
            },
        },
    )
    async def update_settings(self, **kwargs):
        with self._lock:
            settings = self._load_settings()
            for key in _DEFAULT_SETTINGS.keys():
                if key in kwargs:
                    settings[key] = kwargs[key]
            if settings.get("default_chunk_size", 500) < 50:
                settings["default_chunk_size"] = 50
            self._settings = settings
            self._save_settings()
        self._wake_event.set()
        return Ok({"settings": settings})

    # ── API: 库扫描 ──

    @plugin_entry(
        id="pick_folder",
        name="选择文件夹",
        description="保留 API 占位 —— 实际文件夹选择由前端的 webkitdirectory 完成",
        input_schema={
            "type": "object",
            "properties": {
                "initial_dir": {"type": "string", "description": "保留字段，前端不使用"},
            },
        },
    )
    async def pick_folder(self, initial_dir: str = "", **_):
        """保留 API 占位 —— N.E.K.O 客户端是 Electron Webview，
        浏览器原生的 <input webkitdirectory> 才能弹出系统文件夹选择对话框；
        后端 tkinter 不可用 (无 GUI 主循环)。前端直接走 webkitdirectory 选完后
        用 scan_library_tree 拿到树结构即可。"""
        return Ok({
            "hint": "use_webkitdirectory",
            "message": "请使用前端的 <input webkitdirectory> 触发文件夹选择",
        })

    @plugin_entry(
        id="scan_library_tree",
        name="扫描知识库树",
        description="扫描指定路径，返回文件夹树结构",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要扫描的文件夹路径"},
            },
        },
    )
    async def scan_library_tree(self, path: str = "", **_):
        target = _safe_text(path, self._settings.get("library_path", ""))
        if not target:
            return Err(SdkError("请提供路径或在设置中配置 library_path"))
        tree = self._build_folder_tree(target)
        if "error" in tree:
            return Err(SdkError(tree["error"]))
        return Ok({"path": target, "tree": tree})

    @plugin_entry(
        id="read_document",
        name="读取文档",
        description="读取指定 Markdown 文档内容并分片",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文档绝对路径"},
                "chunk_mode": {"type": "string", "enum": ["fixed_length", "by_heading"]},
                "chunk_size": {"type": "integer"},
            },
            "required": ["path"],
        },
    )
    async def read_document(self, path: str, chunk_mode: str = "", chunk_size: int = 0, **_):
        if not path or not Path(path).exists():
            return Err(SdkError("文件不存在"))
        mode = chunk_mode if chunk_mode in _CHUNK_MODES else self._settings.get("default_chunk_mode", "fixed_length")
        size = chunk_size if chunk_size > 0 else self._settings.get("default_chunk_size", 500)
        result = self._read_and_chunk_document(path, mode, size)
        if "error" in result:
            return Err(SdkError(result["error"]))
        # 不返回完整 chunks 内容到前端，避免过大；只返回摘要
        summary = {
            **{k: v for k, v in result.items() if k != "chunks"},
            "chunks_preview": [c[:80] + "..." if len(c) > 80 else c for c in result["chunks"][:3]],
            "has_more_chunks": len(result["chunks"]) > 3,
        }
        return Ok({"document": summary, "full": result})

    # ── API: 从 webkitdirectory 上传 vault ──

    @plugin_entry(
        id="import_vault_files",
        name="从 webkitdirectory 上传的 vault 文件列表导入知识库",
        description="接收前端用 <input webkitdirectory> 选中的 .md 文件列表（relpath + content），"
                    "保存到插件的 vaults 目录后扫描返回树结构。"
                    "解决 N.E.K.O Electron webview 下 file.path 为空、无法直接拿到绝对路径的问题。",
        input_schema={
            "type": "object",
            "properties": {
                "vault_name": {"type": "string", "description": "vault 根目录名（来自 webkitRelativePath 第一段）"},
                "files": {
                    "type": "array",
                    "description": "文件列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "relpath": {"type": "string", "description": "相对路径，如 vault_name/notes/hello.md"},
                            "size": {"type": "integer"},
                            "content": {"type": "string", "description": "UTF-8 文件内容"},
                        },
                    },
                },
            },
            "required": ["vault_name", "files"],
        },
    )
    async def import_vault_files(self, vault_name: str = "", files: list = None, **_):
        """把前端上传的 vault 文件写到本地临时目录，然后扫描返回树结构"""
        if not vault_name:
            return Err(SdkError("vault_name 不能为空"))
        if not files or not isinstance(files, list):
            return Err(SdkError("files 列表不能为空"))

        # 安全清理 vault_name（防止路径穿越）
        safe_name = "".join(c for c in vault_name if c.isalnum() or c in "_-").strip() or "vault"
        vault_dir = self.config_dir / "vaults" / safe_name
        try:
            vault_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return Err(SdkError(f"无法创建 vault 目录: {e}"))

        written = 0
        skipped = 0
        for f in files:
            try:
                relpath = f.get("relpath", "")
                content = f.get("content", "")
                if not relpath or content is None:
                    skipped += 1
                    continue
                # 去掉开头的 vault_name/ 前缀（webkitRelativePath 的第一段）
                parts = relpath.replace("\\", "/").split("/")
                if parts and parts[0] == safe_name:
                    parts = parts[1:]
                if not parts:
                    skipped += 1
                    continue
                # 过滤 ..
                parts = [p for p in parts if p and p not in ("..", ".")]
                target = vault_dir.joinpath(*parts)
                # 限制写入 vault_dir 之内
                if not str(target.resolve()).startswith(str(vault_dir.resolve())):
                    skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # 写入 UTF-8 内容（带 BOM 的话用 utf-8-sig）
                text = content
                if text.startswith("\ufeff"):
                    text = text[1:]
                target.write_text(text, encoding="utf-8")
                written += 1
            except Exception as e:
                self.logger.warning("write vault file failed: {} - {}", f.get("relpath", ""), e)
                skipped += 1

        if written == 0:
            return Err(SdkError("没有成功写入任何 .md 文件"))

        tree = self._build_folder_tree(str(vault_dir))
        if "error" in tree:
            return Err(SdkError(tree["error"]))
        return Ok({
            "path": str(vault_dir),
            "vault_name": safe_name,
            "written": written,
            "skipped": skipped,
            "tree": tree,
        })

    # ── API: 任务管理 ──

    def _validate_schedule(self, schedule: Dict[str, Any]) -> Tuple[bool, str]:
        stype = schedule.get("type", "interval")
        if stype not in _SCHEDULE_TYPES:
            return False, f"不支持的 schedule.type: {stype}"
        if stype == "once":
            dt = _safe_text(schedule.get("once_datetime"), "")
            if not dt:
                return False, "一次性任务需要提供 once_datetime"
            try:
                datetime.fromisoformat(dt)
            except Exception:
                return False, "once_datetime 格式无效"
        return True, ""

    @plugin_entry(
        id="create_task",
        name="创建推送任务",
        description="选择一篇文档或一个文件夹，创建定时推送任务",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target_path": {"type": "string", "description": "文档或文件夹的绝对路径"},
                "target_type": {"type": "string", "enum": ["file", "folder"]},
                "chunk_mode": {"type": "string", "enum": ["fixed_length", "by_heading"]},
                "chunk_size": {"type": "integer"},
                "schedule": {"type": "object"},
                "target_lanlan": {"type": "string"},
            },
            "required": ["name", "target_path", "target_type"],
        },
    )
    async def create_task(
        self,
        name: str,
        target_path: str,
        target_type: str,
        chunk_mode: str = "",
        chunk_size: int = 0,
        schedule: Dict[str, Any] | None = None,
        target_lanlan: str = "",
        **_,
    ):
        if target_type not in {"file", "folder"}:
            return Err(SdkError("target_type 必须是 file 或 folder"))

        path = Path(target_path)
        if not path.exists():
            return Err(SdkError("目标路径不存在"))

        mode = chunk_mode if chunk_mode in _CHUNK_MODES else self._settings.get("default_chunk_mode", "fixed_length")
        size = chunk_size if chunk_size > 0 else self._settings.get("default_chunk_size", 500)

        # 构建 docs 列表
        docs: List[Dict[str, Any]] = []
        if target_type == "file":
            if not path.suffix.lower() == ".md":
                return Err(SdkError("目标文件必须是 .md"))
            doc = self._read_and_chunk_document(str(path), mode, size)
            if "error" in doc:
                return Err(SdkError(doc["error"]))
            docs.append(doc)
        else:
            files = self._scan_markdown_files(str(path))
            for f in files:
                doc = self._read_and_chunk_document(f["path"], mode, size)
                if "error" not in doc:
                    docs.append(doc)
            if not docs:
                return Err(SdkError("文件夹中没有可用的 Markdown 文件"))

        # schedule
        raw_schedule = dict(schedule) if schedule else dict(self._settings.get("default_schedule", {}))
        ok, err = self._validate_schedule(raw_schedule)
        if not ok:
            return Err(SdkError(err))

        task_id = f"nt_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        task: Dict[str, Any] = {
            "task_id": task_id,
            "name": _safe_text(name, f"学习任务 {task_id[-4:]}"),
            "target_path": str(path),
            "target_type": target_type,
            "chunk_mode": mode,
            "chunk_size": size,
            "schedule": raw_schedule,
            "status": _TASK_STATUS_PENDING,
            "docs": [{k: v for k, v in d.items() if k != "chunks"} for d in docs],  # 摘要存储
            "docs_full": docs,   # 完整分片数据
            "current_doc_index": 0,
            "current_chunk_index": 0,
            "target_lanlan": _safe_text(target_lanlan, self._settings.get("target_lanlan", "")),
            "created_at": _iso(),
            "updated_at": _iso(),
            "started_at": None,
            "finished_at": None,
            "last_error": "",
            "last_daily_start": None,
            "once_triggered": False,
        }

        with self._lock:
            self._tasks[task_id] = task
            self._save_tasks()

        return Ok({"task_id": task_id, "task": self._serialize_task(task), "message": "任务创建成功"})

    def _serialize_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        docs = task.get("docs", [])
        total_docs = len(docs)
        doc_index = task.get("current_doc_index", 0)
        chunk_index = task.get("current_chunk_index", 0)
        total_chunks_in_current = docs[doc_index].get("total_chunks", 0) if doc_index < total_docs else 0

        return {
            "task_id": task.get("task_id"),
            "name": task.get("name"),
            "target_path": task.get("target_path"),
            "target_type": task.get("target_type"),
            "status": task.get("status"),
            "schedule": task.get("schedule"),
            "total_docs": total_docs,
            "current_doc_index": doc_index,
            "current_doc_title": docs[doc_index].get("title", "") if doc_index < total_docs else "",
            "current_chunk_index": chunk_index,
            "total_chunks_in_current": total_chunks_in_current,
            "progress_percent": self._calc_progress(task),
            "target_lanlan": task.get("target_lanlan"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "last_error": task.get("last_error", ""),
        }

    def _calc_progress(self, task: Dict[str, Any]) -> int:
        docs = task.get("docs_full", task.get("docs", []))
        if not docs:
            return 0
        total_chunks_all = sum(d.get("total_chunks", 0) for d in docs)
        if total_chunks_all == 0:
            return 0
        done = 0
        for i, d in enumerate(docs):
            if i < task.get("current_doc_index", 0):
                done += d.get("total_chunks", 0)
            elif i == task.get("current_doc_index", 0):
                done += min(task.get("current_chunk_index", 0), d.get("total_chunks", 0))
        return int(done * 100 / total_chunks_all)

    @plugin_entry(id="list_tasks", name="列出任务", description="获取所有推送任务列表")
    async def list_tasks(self, **_):
        with self._lock:
            tasks = [self._serialize_task(t) for t in self._tasks.values()]
        tasks.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
        return Ok({"tasks": tasks, "total": len(tasks), "active_task_id": self._active_task_id})

    @plugin_entry(
        id="get_task",
        name="获取任务详情",
        description="获取单个任务的完整信息",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    )
    async def get_task(self, task_id: str, **_):
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return Err(SdkError("任务不存在"))
        return Ok({"task": self._serialize_task(task)})

    @plugin_entry(
        id="delete_task",
        name="删除任务",
        description="删除一个推送任务",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    )
    async def delete_task(self, task_id: str, **_):
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task and self._active_task_id == task_id:
                self._active_task_id = None
                self._next_push_time = 0.0
            self._save_tasks()
        return Ok({"deleted": task is not None, "task_id": task_id})

    @plugin_entry(
        id="start_task",
        name="开始/恢复任务",
        description="启动或恢复一个推送任务",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    )
    async def start_task(self, task_id: str, **_):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return Err(SdkError("任务不存在"))
            if task.get("status") == _TASK_STATUS_RUNNING:
                return Ok({"task_id": task_id, "status": _TASK_STATUS_RUNNING, "message": "任务已在运行中"})
            if task.get("status") == _TASK_STATUS_COMPLETED:
                # 重置进度重新学习
                task["current_doc_index"] = 0
                task["current_chunk_index"] = 0
                task["finished_at"] = None
                task["once_triggered"] = False
                task["last_daily_start"] = None

            task["status"] = _TASK_STATUS_RUNNING
            task["started_at"] = _iso()
            task["updated_at"] = _iso()
            task["last_error"] = ""
            self._active_task_id = task_id
            schedule = task.get("schedule", {})
            interval = self._calculate_next_interval(schedule)
            self._next_push_time = time.time() + interval
            self._save_tasks()

        self._wake_event.set()
        return Ok({"task_id": task_id, "status": _TASK_STATUS_RUNNING, "next_interval_sec": interval})

    @plugin_entry(
        id="pause_task",
        name="暂停任务",
        description="暂停当前运行中的任务",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    )
    async def pause_task(self, task_id: str, **_):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return Err(SdkError("任务不存在"))
            if task.get("status") != _TASK_STATUS_RUNNING:
                return Ok({"task_id": task_id, "status": task.get("status"), "message": "任务未在运行"})
            task["status"] = _TASK_STATUS_PAUSED
            task["updated_at"] = _iso()
            if self._active_task_id == task_id:
                self._active_task_id = None
                self._next_push_time = 0.0
            self._save_tasks()
        return Ok({"task_id": task_id, "status": _TASK_STATUS_PAUSED})

    @plugin_entry(
        id="stop_task",
        name="停止任务",
        description="停止任务并重置进度到开头",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    )
    async def stop_task(self, task_id: str, **_):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return Err(SdkError("任务不存在"))
            task["status"] = _TASK_STATUS_STOPPED
            task["current_doc_index"] = 0
            task["current_chunk_index"] = 0
            task["updated_at"] = _iso()
            if self._active_task_id == task_id:
                self._active_task_id = None
                self._next_push_time = 0.0
            self._save_tasks()
        return Ok({"task_id": task_id, "status": _TASK_STATUS_STOPPED})

    @plugin_entry(
        id="trigger_now",
        name="立即推送",
        description="立即手动推送当前任务的下一个片段",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    )
    async def trigger_now(self, task_id: str, **_):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return Err(SdkError("任务不存在"))
        has_more = self._do_push_next(task_id)
        return Ok({"task_id": task_id, "has_more": has_more, "message": "推送成功" if has_more else "任务已完成"})

    @plugin_entry(id="get_history", name="获取推送历史", description="获取最近的推送记录")
    async def get_history(self, limit: int = 20, **_):
        with self._lock:
            items = self._history[-limit:]
        return Ok({"history": items, "total": len(self._history)})

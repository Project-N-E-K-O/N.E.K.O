# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Computer-Use Agent — single-call Thought + Action + Code paradigm.

Adapted from the Kimi agent pattern (xlang-ai/OSWorld).
Each step: one VLM call with screenshot → structured thought/action/code → execute.
The multimodal model handles visual grounding directly in its generated code.
Supports thinking mode for models that provide it.
"""

from typing import Dict, Any, Optional, List, Tuple
import json
import re
import base64
import platform
import os
import time
import threading
import traceback
from io import BytesIO
from PIL import Image
from config import get_agent_extra_body, COMPUTER_USE_MAX_TOKENS, LLM_PING_MAX_TOKENS
from utils.config_manager import get_config_manager
from utils.llm_client import create_chat_llm, ChatOpenAI
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from utils.screenshot_utils import compress_screenshot

logger = get_module_logger(__name__, "Agent")

try:
    if platform.system().lower() == "windows":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
except Exception:
    pass

pyautogui = None
_PYAUTOGUI_IMPORT_ERROR: Optional[Exception] = None


def _load_pyautogui():
    """Import pyautogui lazily so Linux display authorization can recover."""
    global pyautogui, _PYAUTOGUI_IMPORT_ERROR
    if pyautogui is not None:
        return pyautogui
    try:
        import pyautogui as loaded_pyautogui
    except Exception as exc:
        _PYAUTOGUI_IMPORT_ERROR = exc
        return None
    pyautogui = loaded_pyautogui
    _PYAUTOGUI_IMPORT_ERROR = None
    return pyautogui


def _pyautogui_unavailable_reason() -> str:
    text = str(_PYAUTOGUI_IMPORT_ERROR or "").lower()
    if any(token in text for token in (
        "displayconnectionerror",
        "can't connect to display",
        "cannot connect to display",
        "authorization required",
        "no authorization protocol",
        "display",
        "xlib",
    )):
        return "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"
    return "AGENT_PYAUTOGUI_NOT_INSTALLED"


_load_pyautogui()


# ─── Connectivity probe error classification ────────────────────────────
#
# Maps raw exception/text patterns from the chat completion client into the
# stable ``AGENT_*`` reason codes that ``check_connectivity()`` returns and
# the restore path (see ``_restore_llm_dependent_flags`` in
# ``app/agent_server.py``) uses to decide whether a failure is transient
# (worth retrying within the bounded restore window of ~32s wall-clock,
# 3 attempts × 6s timeout + 2 × 7s gap) or permanent (give up immediately
# and surface ``AGENT_AUTO_DISABLED_*`` to the user).
# Keep the classifier here (not in agent_server) so any caller of
# ``check_connectivity`` gets the same reason vocabulary.
_PERMANENT_AUTH_TOKENS = (
    "authentication",
    "invalid_api_key",
    "invalid api key",
    "unauthorized",
    "forbidden",
    " 401",
    " 403",
)
_QUOTA_TOKENS = (
    "quota",
    "rate_limit",
    "rate limit",
    " 429",
    "insufficient_quota",
)
_DNS_NXDOMAIN_TOKENS = (
    "nxdomain",
    "name or service not known",
    "getaddrinfo failed",
    "no address associated",
)


def _classify_connectivity_exception(exc: Optional[Exception]) -> str:
    """Bucket an exception from ``invoke_raw`` into a stable reason code.

    Match is text-based on ``str(exc)`` because openai-sdk / httpx wraps
    layer the underlying HTTP status into the message rather than exposing a
    typed status; the SDK type hierarchy itself is also imported lazily by
    ``create_chat_llm``, so we cannot ``isinstance`` against it from here
    without a circular import.
    """
    if exc is None:
        return "AGENT_LLM_UNREACHABLE"
    msg = str(exc).lower()
    if any(tok in msg for tok in _PERMANENT_AUTH_TOKENS):
        return "AGENT_API_KEY_INVALID"
    if any(tok in msg for tok in _QUOTA_TOKENS):
        return "AGENT_QUOTA_EXCEEDED"
    if any(tok in msg for tok in _DNS_NXDOMAIN_TOKENS):
        return "AGENT_DNS_NXDOMAIN"
    return "AGENT_LLM_UNREACHABLE"


# Permanent (no point retrying) reason codes — see restore path in agent_server.
PERMANENT_CONNECTIVITY_REASONS = frozenset({
    "AGENT_ENDPOINT_NOT_CONFIGURED",
    "AGENT_API_KEY_INVALID",
    "AGENT_QUOTA_EXCEEDED",
    "AGENT_DNS_NXDOMAIN",
})


# ─── Prompt Templates ───────────────────────────────────────────────────

INSTRUCTION_TEMPLATE = (
    "# Task:\n{instruction}\n\n"
    "Generate the next action based on the screenshot, task, "
    "and previous steps (if any).\n"
)

# The model is NOT fine-tuned for this format, so we provide full scaffolding:
# detailed action API docs, structured output sections, rules, and tips.
SYSTEM_PROMPT_TEMPLATE = """\
You are an expert GUI automation agent. You control a {platform} computer by \
observing screenshots and generating executable Python code using the pyautogui library.

## Coordinate System

All coordinate arguments (x, y) are integers in the range [0, 999]:
- (0, 0) = top-left corner of the screen
- (999, 999) = bottom-right corner of the screen
- (500, 500) = center of the screen

For example, to click the center of the screen: pyautogui.click(500, 500)

## Available Actions

### Mouse
```
pyautogui.click(x, y, clicks=1, interval=0.0, button='left')
    Click at position (x, y). button: 'left' | 'right' | 'middle'.

pyautogui.doubleClick(x, y)
    Double-click at position (x, y).

pyautogui.rightClick(x, y)
    Right-click at position (x, y).

pyautogui.moveTo(x, y, duration=0.0)
    Move the mouse cursor to position (x, y).

pyautogui.dragTo(x, y, duration=0.5, button='left')
    Click-and-drag from current position to position (x, y).

pyautogui.scroll(clicks, x=None, y=None)
    Scroll the mouse wheel. Positive = up, negative = down.
    If x, y given, move there first.
```

### Keyboard
```
pyautogui.write("text")
    Type text into an input field. Works with any language including CJK / Unicode.
    NOTE: Only use for text input fields. For game controls or non-text contexts,
    use press() or keyDown()/keyUp() instead.

pyautogui.press("key")
    Press and release a single key.
    Keys: a-z, 0-9, enter, tab, escape, backspace, delete, space,
    up, down, left, right, home, end, pageup, pagedown, f1-f12, etc.

pyautogui.keyDown("key")
    Hold a key down (without releasing). Pair with keyUp().
    Use for games or apps that require holding keys (e.g. movement with WASD).

pyautogui.keyUp("key")
    Release a previously held key.

pyautogui.hotkey("modifier", "key")
    Key combination. Examples:
    hotkey("ctrl", "c"), hotkey("alt", "f4"), hotkey("win", "r"),
    hotkey("ctrl", "shift", "esc"), hotkey("ctrl", "a").
```

### Utility
```
time.sleep(seconds)
    Pause execution. Use for waiting for animations / page loads.
```

## Special Control Actions

Wait for something to load:
```code
computer.wait(seconds=5)
```

Task completed successfully:
```code
computer.terminate(status="success", answer="Brief summary of accomplishment")
```

Task failed after reasonable attempts:
```code
computer.terminate(status="failure", answer="Why it failed")
```

## Response Format

For EACH step, you MUST output ALL sections in this exact order:

```
## Verification
(Skip on the first step.)
Check whether your previous action succeeded based on the new screenshot.
If it failed, explain why and adjust your approach.

## Observation
Describe the current state of the screen: open applications, visible UI
elements, text, dialog boxes, etc.

## Thought
Analyze the situation, reason about which UI element to target, and
plan your next action step by step.

## Action
One-sentence description of what you will do.

## Code
```python
pyautogui.click(742, 356)
```
```

## Rules
1. ONE action per step — exactly one pyautogui call or one special action.
2. All coordinates MUST be integers in [0, 999].
3. LOOK CAREFULLY at the screenshot to locate the exact target element.
4. Prefer keyboard shortcuts when efficient (Ctrl+C, Ctrl+V, Alt+Tab, etc.).
5. Call computer.terminate(status="success") AS SOON AS the task is done.
6. Call computer.terminate(status="failure") if stuck after multiple tries.
7. Output exactly ONE code block. No more.
8. Do NOT repeat a failing action — try a different approach.
9. On {platform}, use platform-appropriate shortcuts and paths.
"""

STEP_TEMPLATE = "# Step {step_num}:\n"

HISTORY_TEMPLATE_THINKING = "{thought}## Action:\n{action}\n"
HISTORY_TEMPLATE_NON_THINKING = "## Thought:\n{thought}\n\n## Action:\n{action}\n"


# ─── Response Parser ────────────────────────────────────────────────────


def parse_response(
    response_content: str, reasoning_content: Optional[str] = None
) -> Dict[str, str]:
    """Parse structured VLM response into thought / action / code.

    In thinking mode the thought comes from *reasoning_content*; the
    visible *response_content* starts at ``## Action``.
    """
    result: Dict[str, str] = {
        "thought": "",
        "action": "",
        "code": "",
        "raw": response_content,
    }
    text = response_content.lstrip()

    # Thought
    if reasoning_content:
        result["thought"] = reasoning_content.strip()
        m = re.search(r"^##\s*Action\b", text, flags=re.MULTILINE)
        if m:
            text = text[m.start() :]
    else:
        m = re.search(
            r"##\s*Thought\s*:?\s*[\n\r]+(.*?)(?=##\s*Action|##\s*Code|$)",
            text,
            re.DOTALL,
        )
        if m:
            result["thought"] = m.group(1).strip()

    # Action
    m = re.search(
        r"##\s*Action\s*:?\s*[\n\r]+(.*?)(?=##\s*Code|```|$)",
        text,
        re.DOTALL,
    )
    if m:
        result["action"] = m.group(1).strip()

    # Code (last block)
    code_blocks = re.findall(r"```(?:python|code)?\s*\n?(.*?)\s*```", text, re.DOTALL)
    if code_blocks:
        result["code"] = code_blocks[-1].strip()

    return result


def _extract_raw_llm_text(resp: Any) -> Tuple[str, Optional[str]]:
    """Extract text from either OpenAI-compatible or Anthropic raw responses."""
    choices = getattr(resp, "choices", None)
    if choices is not None:
        choice = choices[0] if choices else None
        msg = getattr(choice, "message", None) if choice else None
        content = getattr(msg, "content", "") if msg else ""
        reasoning = getattr(msg, "reasoning_content", None) if msg else None
        return content or "", reasoning

    content = getattr(resp, "content", None)
    if isinstance(content, str):
        return content, None
    if isinstance(content, list):
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(str(block.get("text") or ""))
                elif block.get("type") in ("thinking", "reasoning"):
                    reasoning = block.get("thinking") or block.get("text") or block.get("reasoning")
                    if reasoning:
                        reasoning_parts.append(str(reasoning))
                continue
            if getattr(block, "type", None) == "text":
                text_parts.append(str(getattr(block, "text", "") or ""))
            elif getattr(block, "type", None) in ("thinking", "reasoning"):
                reasoning = (
                    getattr(block, "thinking", None)
                    or getattr(block, "text", None)
                    or getattr(block, "reasoning", None)
                )
                if reasoning:
                    reasoning_parts.append(str(reasoning))
        return "".join(text_parts), "\n".join(reasoning_parts) or None

    return str(resp or ""), None


# ─── Restricted OS proxy for CUA exec sandbox ──────────────────────────

from types import MappingProxyType


class _RestrictedOS:
    """受限 os 模块代理，仅暴露安全的只读操作。

    防止 LLM 生成的代码通过 os.system()、os.popen() 等执行任意系统命令。
    仅允许路径查询、环境变量只读快照和平台信息访问。
    """

    _ALLOWED_ATTRS = frozenset({
        "path", "sep", "linesep", "name", "environ",
        "getcwd", "listdir", "curdir", "pardir", "extsep",
    })

    class _RestrictedPath:
        """os.path 的受限子集，仅允许只读操作。"""
        _ALLOWED = frozenset({
            "join", "split", "splitext", "basename", "dirname",
            "exists", "isfile", "isdir", "isabs", "abspath",
            "normpath", "relpath", "commonpath", "sep", "altsep",
            "expanduser", "expandvars",
        })

        def __getattr__(self, name):
            if name not in self._ALLOWED:
                raise AttributeError(
                    f"os.path.{name} is not allowed in CUA sandbox"
                )
            return getattr(os.path, name)

    def __init__(self):
        self.path = _RestrictedOS._RestrictedPath()
        # 返回不可变的环境变量快照，防止 LLM 代码修改真实进程环境
        self.environ = MappingProxyType(dict(os.environ))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(f"'{name}' is not accessible")
        if name not in self._ALLOWED_ATTRS:
            raise AttributeError(
                f"os.{name} is not allowed in CUA sandbox"
            )
        return getattr(os, name)


# ─── Restricted builtins for CUA exec sandbox ─────────────────────────

# 仅允许的安全内置函数，移除 __import__、open、exec、eval、compile、
# globals、locals、vars、dir、getattr、setattr、delattr、type、super 等
# 危险内置，防止 LLM 代码绕过 _RestrictedOS 代理。
_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "ascii": ascii,
    "bin": bin,
    "bool": bool,
    "bytearray": bytearray,
    "bytes": bytes,
    "callable": callable,
    "chr": chr,
    "complex": complex,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "hash": hash,
    "hex": hex,
    "id": id,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
}


# ─── Coordinate-scaling proxy ───────────────────────────────────────────


class _ScaledPyAutoGUI:
    """Projects model coordinates to physical screen pixels.

    If both x and y are in [0, 999] they are scaled to screen dimensions.
    Float pairs in [0, 1] are accepted as normalized coordinates because
    some OpenAI-compatible vision models emit that form despite the prompt.
    Values > 999 are passed through as absolute pixel coordinates.
    """

    _COORD_MAX = 999
    _FAILSAFE_EDGE_PX = 4

    def __init__(
        self,
        backend,
        screen_w: int,
        screen_h: int,
        cancel_event: Optional[threading.Event] = None,
    ):
        self._backend = backend
        self._w = screen_w
        self._h = screen_h
        self._cancel_event = cancel_event

    def __getattr__(self, name):
        attr = getattr(self._backend, name)
        if callable(attr):

            def _wrapped(*args, **kwargs):
                self._ensure_not_cancelled()
                return attr(*args, **kwargs)

            return _wrapped
        return attr

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise InterruptedError("Task cancelled")

    def _in_range(self, x, y) -> bool:
        return (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and 0 <= x <= self._COORD_MAX
            and 0 <= y <= self._COORD_MAX
        )

    def _in_unit_range(self, x, y) -> bool:
        return (
            isinstance(x, float)
            and isinstance(y, float)
            and 0 <= x <= 1
            and 0 <= y <= 1
        )

    def _safe_screen_point(self, x: float, y: float, *, inset: bool = False) -> Tuple[int, int]:
        max_x = max(0, self._w - 1)
        max_y = max(0, self._h - 1)
        px = int(round(x))
        py = int(round(y))
        px = max(0, min(px, max_x))
        py = max(0, min(py, max_y))
        if inset and self._w > self._FAILSAFE_EDGE_PX * 2:
            px = max(self._FAILSAFE_EDGE_PX, min(px, max_x - self._FAILSAFE_EDGE_PX))
        if inset and self._h > self._FAILSAFE_EDGE_PX * 2:
            py = max(self._FAILSAFE_EDGE_PX, min(py, max_y - self._FAILSAFE_EDGE_PX))
        return px, py

    def _project_pair(self, x, y) -> Tuple[int, int]:
        if self._in_unit_range(x, y):
            return self._safe_screen_point(
                float(x) * max(0, self._w - 1),
                float(y) * max(0, self._h - 1),
                inset=True,
            )
        if self._in_range(x, y):
            return self._safe_screen_point(
                float(x) * max(0, self._w - 1) / self._COORD_MAX,
                float(y) * max(0, self._h - 1) / self._COORD_MAX,
                inset=True,
            )
        return self._safe_screen_point(float(x), float(y))

    def _project(self, args, kwargs):
        if (
            len(args) >= 2
            and isinstance(args[0], (int, float))
            and isinstance(args[1], (int, float))
        ):
            x, y = self._project_pair(args[0], args[1])
            return (x, y) + tuple(args[2:]), kwargs
        if (
            len(args) == 1
            and isinstance(args[0], (int, float))
            and isinstance(kwargs.get("y"), (int, float))
        ):
            x, y = self._project_pair(args[0], kwargs["y"])
            kw = dict(kwargs)
            kw["y"] = y
            return (x,), kw
        if "x" in kwargs and "y" in kwargs:
            kw = dict(kwargs)
            kw["x"], kw["y"] = self._project_pair(kw["x"], kw["y"])
            return args, kw
        return args, kwargs

    def click(self, *a, **kw):
        self._ensure_not_cancelled()
        a, kw = self._project(a, kw)
        tx, ty = self._extract_xy(a, kw)
        if tx is not None:
            self._smooth_move_to(tx, ty)
            self._show_click_halo(tx, ty)
        self._ensure_not_cancelled()
        return self._backend.click(*a, **kw)

    def doubleClick(self, *a, **kw):
        self._ensure_not_cancelled()
        a, kw = self._project(a, kw)
        tx, ty = self._extract_xy(a, kw)
        if tx is not None:
            self._smooth_move_to(tx, ty)
            self._show_click_halo(tx, ty)
        self._ensure_not_cancelled()
        return self._backend.doubleClick(*a, **kw)

    def rightClick(self, *a, **kw):
        self._ensure_not_cancelled()
        a, kw = self._project(a, kw)
        tx, ty = self._extract_xy(a, kw)
        if tx is not None:
            self._smooth_move_to(tx, ty)
            self._show_click_halo(tx, ty)
        self._ensure_not_cancelled()
        return self._backend.rightClick(*a, **kw)

    def moveTo(self, *a, **kw):
        self._ensure_not_cancelled()
        a, kw = self._project(a, kw)
        if "duration" not in kw and len(a) < 3:
            kw["duration"] = 0.3
        return self._backend.moveTo(*a, **kw)

    def dragTo(self, *a, **kw):
        self._ensure_not_cancelled()
        a, kw = self._project(a, kw)
        if "duration" not in kw and len(a) < 3:
            kw["duration"] = 0.5
        return self._backend.dragTo(*a, **kw)

    def scroll(self, clicks, x=None, y=None, *args, **kwargs):
        self._ensure_not_cancelled()
        if x is not None and y is not None:
            scaled_x, scaled_y = self._project_pair(x, y)
            return self._backend.scroll(clicks, x=scaled_x, y=scaled_y, *args, **kwargs)
        return self._backend.scroll(clicks, x=x, y=y, *args, **kwargs)

    # ── Smooth movement & click halo ─────────────────────────────────────

    def _extract_xy(
        self, args: tuple, kwargs: dict
    ) -> Tuple[Optional[int], Optional[int]]:
        """Extract (x, y) from already-projected args/kwargs."""
        if (
            len(args) >= 2
            and isinstance(args[0], (int, float))
            and isinstance(args[1], (int, float))
        ):
            return int(args[0]), int(args[1])
        if (
            len(args) == 1
            and isinstance(args[0], (int, float))
            and isinstance(kwargs.get("y"), (int, float))
        ):
            return int(args[0]), int(kwargs["y"])
        x, y = kwargs.get("x"), kwargs.get("y")
        if x is not None and y is not None:
            return int(x), int(y)
        return None, None

    def _smooth_move_to(self, x: int, y: int, duration: float = 0.3):
        """Smoothly move the cursor to (x, y) with easeOutQuad tween."""
        try:
            tween = getattr(self._backend, "easeOutQuad", None)
            if tween is None and pyautogui is not None:
                tween = getattr(pyautogui, "easeOutQuad", None)
            self._backend.moveTo(
                x,
                y,
                duration=duration,
                _pause=False,
                **({"tween": tween} if tween else {}),
            )
        except Exception:
            # Fallback: instant move
            try:
                self._backend.moveTo(x, y, _pause=False)
            except Exception:
                pass

    def _show_click_halo(self, x: int, y: int):
        """Show a brief expanding-ring halo at (x, y). Windows only, via ctypes."""
        # TODO: 光圈暂未实现。当前方案存在问题：
        #   1. ctypes.wintypes 没有 WNDCLASS 结构体，需手动定义 WNDCLASSEXW
        #   2. GDI 绘制需要消息循环 (PeekMessage/DispatchMessage) 才能渲染
        #   3. 可考虑改用 UpdateLayeredWindow + 内存 DC 一次性贴图，或由 Electron 前端渲染
        pass

    def _clipboard_type(self, text: str):
        """Type text via clipboard paste — handles CJK / Unicode reliably."""
        self._ensure_not_cancelled()
        import pyperclip

        paste_key = "command" if platform.system() == "Darwin" else "ctrl"
        pyperclip.copy(text)
        self._backend.hotkey(paste_key, "v")
        time.sleep(0.05)

    def _coerce_write_args(self, args, kwargs):
        if args:
            return str(args[0]), tuple(args[1:]), kwargs

        kw = dict(kwargs)
        for key in ("text", "message", "string"):
            if key in kw:
                return str(kw.pop(key)), (), kw

        raise TypeError("write() missing required text argument")

    def write(self, *a, **kw):
        self._ensure_not_cancelled()
        text_str, a, kw = self._coerce_write_args(a, kw)
        # Clipboard paste is only needed for non-ASCII (CJK, emoji, etc.)
        # that pyautogui.write() cannot handle natively.
        # For ASCII-only text, use real key simulation so it works in games
        # and other non-text-field contexts where Ctrl+V is ignored.
        if any(ord(c) > 127 for c in text_str):
            try:
                self._clipboard_type(text_str)
                return
            except Exception:
                pass
        self._backend.write(text_str, *a, **kw)

    def typewrite(self, *a, **kw):
        self.write(*a, **kw)


# ─── Main Adapter ───────────────────────────────────────────────────────


class ComputerUseAdapter:
    """GUI automation agent: single-call Thought + Action + Code paradigm.

    Follows the Kimi agent architecture (predict / reset / call_llm /
    history management) with full prompt scaffolding for untrained models.
    """

    def __init__(
        self,
        max_steps: int = 50,
        max_image_history: int = 2,
        max_completion_tokens: int = COMPUTER_USE_MAX_TOKENS,
        thinking: bool = True,
    ):
        self.last_error: Optional[str] = None
        self.init_ok = False
        self._cancelled: bool = False
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._done_event.set()  # initially "done" (no task running)
        self.max_steps = max_steps
        self.max_image_history = max_image_history
        self.max_completion_tokens = max_completion_tokens
        self.thinking = thinking

        # Screen dimensions
        self.screen_width, self.screen_height = 1920, 1080

        # LLM
        self._llm_client: Optional[ChatOpenAI] = None
        self._llm_client_sig: Optional[tuple] = None
        self._config_manager = get_config_manager()
        self._agent_model_cfg = self._config_manager.get_model_api_config("agent")

        self._history_template = (
            HISTORY_TEMPLATE_THINKING
            if self.thinking
            else HISTORY_TEMPLATE_NON_THINKING
        )

        # Kimi-style agent state
        self._current_session_id: Optional[str] = None
        self.actions: List[str] = []
        self.observations: List[bytes] = []
        self.cots: List[Dict[str, str]] = []

        try:
            pyautogui_module = _load_pyautogui()
            if pyautogui_module is None:
                self.last_error = f"pyautogui unavailable: {_pyautogui_unavailable_reason()}"
                return

            self.screen_width, self.screen_height = pyautogui_module.size()

            self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                platform=platform.system(),
            )

            api_key = self._agent_model_cfg.get("api_key") or "EMPTY"
            base_url = self._agent_model_cfg.get("base_url", "")
            model = self._agent_model_cfg.get("model", "")
            provider_type = self._agent_model_cfg.get("provider_type")
            if not base_url or not model:
                self.last_error = "Agent model not configured"
                return

            self._llm_client = create_chat_llm(  # noqa: LLM_OUTPUT_BUDGET  # output budget set per-call via invoke_raw(max_completion_tokens=...) (ping vs main call differ); transport timeout set here.
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=65.0,
                max_retries=0,
                temperature=0,
                provider_type=provider_type,
            )
        except Exception as e:
            self.last_error = str(e)
            logger.error("ComputerUseAdapter init failed: %s", e)

    # ------------------------------------------------------------------
    # Non-blocking LLM connectivity probe
    # ------------------------------------------------------------------

    def check_connectivity(
        self,
        *,
        timeout_s: float = 6.0,
        _retries: int = 0,
    ) -> Tuple[bool, str]:
        """Synchronous LLM ping using the same ChatOpenAI client that real
        tasks will use, so the TCP/TLS connection pool is warmed up.
        Meant to be called from a background thread.

        Returns ``(ok, reason_code)``. ``reason_code`` is empty on success;
        otherwise a stable identifier the caller can match against to decide
        whether the failure is transient (retry) or permanent (give up):

            ``AGENT_ENDPOINT_NOT_CONFIGURED``  — base_url / model missing
            ``AGENT_API_KEY_INVALID``          — HTTP 401/403, auth rejected
            ``AGENT_QUOTA_EXCEEDED``           — HTTP 429 / quota exhausted
            ``AGENT_DNS_NXDOMAIN``             — host does not resolve
            ``AGENT_LLM_UNREACHABLE``          — generic transient (timeout / 5xx / refused)

        ``timeout_s=6.0, _retries=0`` is the current fast/restore default.
        Callers that need extra cold-start TLS / DNS tolerance on slow links
        can pass a larger ``timeout_s``. The previous 20s + 3×retry default
        was over-defensive — TLS resumption + warmed DNS cache settle <2s in
        normal environments — but the 6s budget here leaves room for one
        cold handshake while keeping a single probe well under the
        ``_RESTORE_PING_INTERVAL_S=7s`` gap used by the restore loop, so
        attempts don't overlap.
        """
        cfg = self._config_manager.get_model_api_config("agent")
        api_key = cfg.get("api_key") or "EMPTY"
        base_url = cfg.get("base_url", "")
        model = cfg.get("model", "")
        provider_type = cfg.get("provider_type")
        if not base_url or not model:
            self.init_ok = False
            self.last_error = "Agent model not configured"
            return False, "AGENT_ENDPOINT_NOT_CONFIGURED"

        last_exc: Exception | None = None
        for attempt in range(_retries + 1):
            try:
                current_sig = (base_url.rstrip("/"), api_key, model, provider_type)
                if self._llm_client is None or self._llm_client_sig != current_sig:
                    # CRITICAL: keep the client instance's default timeout at
                    # 65.0s, NOT ``timeout_s``. ``self._llm_client`` is reused
                    # by the live ``_call_llm`` path (line ~1123) which calls
                    # ``invoke_raw(messages, ...)`` WITHOUT a per-call
                    # ``timeout=`` kwarg, so it inherits the instance default.
                    # If we cached a 4s-default client here, real GUI Agent
                    # requests would time out after 4 seconds and break the
                    # whole feature. The fast-probe behavior is achieved
                    # purely by the per-call ``timeout=timeout_s`` argument
                    # on the ping's ``invoke_raw`` below, which routes
                    # through ``_params()`` without mutating the instance.
                    self._llm_client = create_chat_llm(  # noqa: LLM_OUTPUT_BUDGET  # output budget set per-call via invoke_raw(max_completion_tokens=...) (ping vs main call differ); transport timeout set here.
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        timeout=65.0,
                        max_retries=0,
                        temperature=0,
                        provider_type=provider_type,
                    )
                    self._llm_client_sig = current_sig
                extra = get_agent_extra_body(model) or {}
                set_call_type("agent_cua")
                # Per-call overrides via invoke_raw's **kwargs path: routes
                # both max_completion_tokens AND timeout through _params()
                # locally, NOT writing back to the instance. This means a
                # 4s probe ping running concurrently with a real 60s GUI
                # request won't clip the latter's budget or timeout.
                resp = self._llm_client.invoke_raw(
                    [{"role": "user", "content": "ok"}],
                    max_completion_tokens=LLM_PING_MAX_TOKENS,
                    extra_body=extra or None,
                    timeout=timeout_s,
                )
                # 连通性检测只关心 HTTP 层是否通、响应结构是否合法；某些上游
                # （如 free-agent-model）会返回 choices 非空但 message=None
                # 的合法 200，message 为空也视为成功。
                _extract_raw_llm_text(resp)
                self.init_ok = True
                self.last_error = None
                logger.info("[CUA] LLM connectivity OK (%s @ %s)", model, base_url)
                return True, ""
            except Exception as e:
                last_exc = e
                if attempt < _retries:
                    import time
                    delay = 3 * (attempt + 1)
                    logger.info("[CUA] LLM connectivity attempt %d/%d failed (%s), retry in %ds",
                                attempt + 1, _retries + 1, type(e).__name__, delay)
                    time.sleep(delay)

        self.init_ok = False
        self.last_error = str(last_exc) if last_exc else "unknown"
        reason_code = _classify_connectivity_exception(last_exc)
        logger.warning(
            "[CUA] LLM connectivity FAIL after %d attempts (reason=%s): %s",
            _retries + 1, reason_code, last_exc,
        )
        return False, reason_code

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_available(self) -> Dict[str, Any]:
        model_cfg = self._config_manager.get_model_api_config("agent")
        ok = True
        reasons: List[str] = []
        if not model_cfg.get("base_url") or not model_cfg.get("model"):
            ok = False
            reasons.append("AGENT_ENDPOINT_NOT_CONFIGURED")
        if _load_pyautogui() is None:
            ok = False
            reasons.append(_pyautogui_unavailable_reason())
        if not self.init_ok:
            ok = False
            reasons.append("AGENT_NOT_INITIALIZED")
        return {
            "enabled": True,
            "ready": ok,
            "reasons": reasons,
            "provider": "openai",
            "model": model_cfg.get("model", ""),
        }

    def reset(self):
        """Reset agent state for a new task."""
        self.actions.clear()
        self.observations.clear()
        self.cots.clear()

    def _compress_jpeg_to_target(self, jpeg_bytes: bytes, target_bytes: int) -> bytes:
        """Best-effort compress one JPEG below target size."""
        if len(jpeg_bytes) <= target_bytes:
            return jpeg_bytes
        try:
            img = Image.open(BytesIO(jpeg_bytes)).convert("RGB")
        except Exception:
            return jpeg_bytes

        # Quality-only first.
        for q in (68, 60, 52, 44, 36, 30, 26):
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=q, optimize=True)
            out = buf.getvalue()
            if len(out) <= target_bytes:
                return out

        # Then downscale + quality.
        w, h = img.size
        for scale in (0.9, 0.8, 0.72, 0.64, 0.56):
            nw = max(320, int(w * scale))
            nh = max(180, int(h * scale))
            resized = img.resize((nw, nh), Image.LANCZOS)
            for q in (54, 46, 38, 32, 26):
                buf = BytesIO()
                resized.save(buf, format="JPEG", quality=q, optimize=True)
                out = buf.getvalue()
                if len(out) <= target_bytes:
                    return out

        # Fall back to smallest candidate.
        resized = img.resize(
            (max(320, int(w * 0.56)), max(180, int(h * 0.56))), Image.LANCZOS
        )
        buf = BytesIO()
        resized.save(buf, format="JPEG", quality=24, optimize=True)
        return buf.getvalue()

    def _fit_images_to_total_budget(
        self, images: List[bytes], total_budget_bytes: int
    ) -> List[bytes]:
        """Best-effort fit multiple images into a total size budget."""
        if not images:
            return images
        if sum(len(x) for x in images) <= total_budget_bytes:
            return images

        per_img_budget = max(80 * 1024, total_budget_bytes // len(images))
        out = [self._compress_jpeg_to_target(x, per_img_budget) for x in images]
        if sum(len(x) for x in out) <= total_budget_bytes:
            return out

        # Tighten budgets based on current size ratio.
        for _ in range(3):
            total = sum(len(x) for x in out)
            if total <= total_budget_bytes:
                break
            next_out: List[bytes] = []
            for x in out:
                share = max(
                    64 * 1024,
                    int(total_budget_bytes * (len(x) / max(total, 1)) * 0.9),
                )
                next_out.append(self._compress_jpeg_to_target(x, share))
            out = next_out
        return out

    def predict(
        self, instruction: str, obs: Dict[str, Any]
    ) -> Tuple[Dict[str, str], str]:
        """Single-step prediction following the Kimi agent pattern.

        Builds the multi-turn message array (system → history → current
        screenshot), calls the VLM once, and parses the structured response
        into thought / action / executable code.

        Args:
            instruction: Natural-language task description.
            obs: ``{"screenshot": <PNG bytes>}``

        Returns:
            ``(info_dict, executable_code_string)``
        """
        step_num = len(self.actions) + 1
        screenshot_bytes: bytes = obs["screenshot"]

        # ── Build messages ───────────────────────────────────────────
        messages: list = [{"role": "system", "content": self._system_prompt}]

        instruction_prompt = INSTRUCTION_TEMPLATE.format(instruction=instruction)

        n = len(self.actions)
        text_parts: List[str] = []
        # Request policy: at most 3 images per call (2 history + 1 current),
        # and total payload budget <= 600KB to avoid Entity Too Large.
        history_image_limit = max(0, min(self.max_image_history, 2))
        history_start = max(0, n - history_image_limit)
        history_indices = list(range(history_start, n))
        history_images = [self.observations[i] for i in history_indices]
        packed_images = self._fit_images_to_total_budget(
            [*history_images, screenshot_bytes],
            total_budget_bytes=600 * 1024,
        )
        packed_history = packed_images[:-1] if len(packed_images) > 1 else []
        packed_current = packed_images[-1]
        packed_history_by_idx = {
            history_indices[idx]: packed_history[idx]
            for idx in range(min(len(history_indices), len(packed_history)))
        }

        for i in range(n):
            step_text = STEP_TEMPLATE.format(
                step_num=i + 1
            ) + self._history_template.format(
                thought=self.cots[i].get("thought", ""),
                action=self.cots[i].get("action", ""),
            )
            # Recent steps: keep the screenshot image
            if i >= history_start:
                if text_parts:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "\n".join(text_parts),
                        }
                    )
                    text_parts = []
                img_bytes = packed_history_by_idx.get(i, self.observations[i])
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64}",
                                },
                            }
                        ],
                    }
                )
                messages.append({"role": "assistant", "content": step_text})
            else:
                # Older steps: text only (images dropped to save context)
                text_parts.append(step_text)
                if i == history_start - 1:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "\n".join(text_parts),
                        }
                    )
                    text_parts = []

        if text_parts:
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(text_parts),
                }
            )

        # Current screenshot + task prompt
        cur_b64 = base64.b64encode(packed_current).decode("utf-8")
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{cur_b64}"},
                    },
                    {"type": "text", "text": instruction_prompt},
                ],
            }
        )

        # ── Call LLM ─────────────────────────────────────────────────
        parsed = self._call_llm(messages)
        code = parsed.get("code", "")
        thought = parsed.get("thought", "")
        action = parsed.get("action", "")

        print(
            f"[CUA] Step {step_num}, {action[:120]}"
        )  # 敏感日志使用print而不是logger，用于脱敏

        # ── Update agent state ───────────────────────────────────────
        self.observations.append(screenshot_bytes)
        self.actions.append(action)
        self.cots.append(parsed)

        # Force termination at step limit
        if step_num >= self.max_steps and "computer.terminate" not in code.lower():
            logger.warning("Reached max steps %d. Forcing termination.", self.max_steps)
            code = (
                'computer.terminate(status="failure", '
                'answer="Reached maximum step limit")'
            )

        return {"thought": thought, "action": action, "code": code}, code

    def cancel_running(self) -> None:
        """Signal the currently running task to stop at the next step boundary."""
        self._cancelled = True
        self._cancel_event.set()
        logger.info("[CUA] cancel_running called, task will abort at next step")

    def wait_for_completion(self, timeout: float = 15.0) -> bool:
        """Block until run_instruction finishes. Returns True if it finished within *timeout*."""
        return self._done_event.wait(timeout=timeout)

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that returns early when cancel is signalled."""
        self._cancel_event.wait(timeout=seconds)

    def _make_cancellable_time_module(self):
        """Return a *time*-like namespace whose ``sleep`` is interruptible."""
        import types

        fake = types.ModuleType("time")
        cancel_event = self._cancel_event
        for attr in (
            "monotonic",
            "time",
            "perf_counter",
            "strftime",
            "gmtime",
            "localtime",
            "mktime",
        ):
            if hasattr(time, attr):
                setattr(fake, attr, getattr(time, attr))

        def _cancellable_sleep(seconds):
            cancel_event.wait(timeout=min(float(seconds), 30))
            if cancel_event.is_set():
                raise InterruptedError("Task cancelled")

        fake.sleep = _cancellable_sleep
        return fake

    def run_instruction(
        self, instruction: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a natural-language instruction via GUI automation.

        Main loop: screenshot → predict → execute → repeat.

        Returns:
            ``{"success": bool, "result": str, "steps": int}``
            (plus ``"error"`` on exception).
        """
        if not self._llm_client:
            return {"success": False, "error": "Agent not initialized"}

        self._cancelled = False
        self._cancel_event.clear()
        self._done_event.clear()

        if session_id is None or session_id != self._current_session_id:
            self.reset()
            self._current_session_id = session_id

        last_action = ""
        success = False
        answer = ""

        try:
            for step in range(1, self.max_steps + 1):
                if self._cancelled:
                    logger.info("[CUA] Task cancelled by user at step %d", step)
                    return {"success": False, "error": "Task cancelled by user"}

                t0 = time.monotonic()
                shot = pyautogui.screenshot()
                # CUA 自己抓屏做 agent 控制，需要更高分辨率读清小字 UI；不随 vision 分析
                # 一起降到 720p，显式锁定在 1080p（quality 仍走默认）。
                jpg_bytes = compress_screenshot(shot, target_h=1080)
                t_capture = time.monotonic() - t0

                t1 = time.monotonic()
                info, code = self.predict(instruction, {"screenshot": jpg_bytes})
                t_llm = time.monotonic() - t1
                logger.info(
                    "[CUA] Step %d timing: capture=%.1fs (%dKB), llm=%.1fs",
                    step,
                    t_capture,
                    len(jpg_bytes) // 1024,
                    t_llm,
                )

                # Re-check after the (potentially long) LLM call
                if self._cancelled:
                    logger.info("[CUA] Task cancelled after LLM call at step %d", step)
                    return {"success": False, "error": "Task cancelled by user"}

                if not code:
                    continue

                last_action = info.get("action", "")
                code_lower = code.lower()

                # ── Special actions ──────────────────────────────────
                if "computer.terminate" in code_lower:
                    m_status = re.search(r'status\s*=\s*["\'](\w+)["\']', code)
                    success = (
                        (m_status.group(1).lower() == "success") if m_status else False
                    )
                    m_answer = re.search(
                        r'answer\s*=\s*["\'](.+?)["\']', code, re.DOTALL
                    )
                    answer = m_answer.group(1) if m_answer else last_action
                    break

                if "computer.wait" in code_lower:
                    m = re.search(r"seconds\s*=\s*(\d+)", code)
                    wait_s = int(m.group(1)) if m else 5
                    self._interruptible_sleep(min(wait_s, 30))
                    continue

                # ── Execute pyautogui code ───────────────────────────
                try:
                    exec_env: dict = {"__builtins__": _SAFE_BUILTINS}
                    exec_env["pyautogui"] = _ScaledPyAutoGUI(
                        pyautogui,
                        self.screen_width,
                        self.screen_height,
                        cancel_event=self._cancel_event,
                    )
                    exec_env["time"] = self._make_cancellable_time_module()
                    exec_env["os"] = _RestrictedOS()
                    exec(code, exec_env)
                    self._interruptible_sleep(0.3)
                except InterruptedError:
                    logger.info("[CUA] Task cancelled during exec at step %d", step)
                    return {"success": False, "error": "Task cancelled by user"}
                except Exception as e:
                    logger.warning(
                        "[CUA] Exec error step %d: %s\nCode: %s", step, e, code
                    )
                    self._interruptible_sleep(0.3)
            else:
                answer = f"Reached {self.max_steps} steps without completion"
                success = False

        except Exception as e:
            logger.error(
                "[CUA] run_instruction error: %s\n%s", e, traceback.format_exc()
            )
            return {"success": False, "error": str(e)}
        finally:
            self._done_event.set()

        return {
            "success": success,
            "result": answer or last_action,
            "steps": len(self.actions),
        }

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    _CANCEL_TERMINATE = {
        "thought": "",
        "action": "",
        "code": 'computer.terminate(status="failure", answer="Task cancelled by user")',
        "raw": "",
    }

    def _call_llm(self, messages: list) -> Dict[str, str]:
        """Call the VLM with retry, return parsed response."""
        if self._cancelled:
            return dict(self._CANCEL_TERMINATE)

        model = self._agent_model_cfg.get("model", "")
        extra = get_agent_extra_body(model) or {}

        for attempt in range(3):
            if self._cancelled:
                return dict(self._CANCEL_TERMINATE)
            try:
                ok, info = self._config_manager.consume_agent_daily_quota(
                    source="computer_use.call_llm",
                    units=1,
                )
                if not ok:
                    logger.warning(
                        "[CUA] Agent quota exceeded: used=%s, limit=%s",
                        info.get("used"),
                        info.get("limit"),
                    )
                    return {
                        "thought": "",
                        "action": "",
                        "code": 'computer.terminate(status="failure", answer="AGENT_QUOTA_EXCEEDED")',
                        "raw": json.dumps(
                            {
                                "code": "AGENT_QUOTA_EXCEEDED",
                                "details": {
                                    "used": info.get("used", 0),
                                    "limit": info.get("limit", 300),
                                },
                            }
                        ),
                    }
                set_call_type("agent_cua")
                # Per-call overrides via invoke_raw's **kwargs path: routes
                # max_tokens vs max_completion_tokens through _params() by
                # base_url, returns raw SDK response so reasoning_content
                # stays accessible. No instance state mutation, so a
                # background ping running concurrently can't clip this
                # request's budget.
                resp = self._llm_client.invoke_raw(  # noqa: LLM_INPUT_BUDGET  # agent messages (screenshot + bounded history) capped upstream by the CUA history window; output budget set here per-call.
                    messages,
                    max_completion_tokens=self.max_completion_tokens,
                    extra_body=extra or None,
                )
                content, reasoning = _extract_raw_llm_text(resp)

                parsed = parse_response(content, reasoning if self.thinking else None)
                if parsed["code"]:
                    return parsed

                logger.warning(
                    "[CUA] No code (attempt %d): %.300s", attempt + 1, content
                )
            except Exception as e:
                logger.error("[CUA] LLM error (attempt %d): %s", attempt + 1, e)
                if attempt < 2:
                    self._interruptible_sleep(1)

        return {"thought": "", "action": "", "code": "", "raw": ""}

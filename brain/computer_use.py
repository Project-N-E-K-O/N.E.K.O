"""
Computer-Use Agent — single-call Thought + Action + Code paradigm.

Adapted from the Kimi agent pattern (xlang-ai/OSWorld).
Each step: one VLM call with screenshot → structured thought/action/code → execute.
The multimodal model handles visual grounding directly in its generated code.
Supports thinking mode for models that provide it.
"""
from typing import Dict, Any, Optional, List, Tuple
import re
import base64
import logging
import platform
import os
import time
import traceback
from io import BytesIO
from openai import OpenAI
from PIL import Image
from config import get_agent_extra_body
from utils.config_manager import get_config_manager
from utils.screenshot_utils import compress_screenshot

logger = logging.getLogger(__name__)

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

try:
    import pyautogui
except Exception:
    pyautogui = None


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
    Type text. Works with any language including CJK / Unicode.

pyautogui.press("key")
    Press and release a single key.
    Keys: enter, tab, escape, backspace, delete, space,
    up, down, left, right, home, end, pageup, pagedown, f1-f12, etc.

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
        "thought": "", "action": "", "code": "", "raw": response_content,
    }
    text = response_content.lstrip()

    # Thought
    if reasoning_content:
        result["thought"] = reasoning_content.strip()
        m = re.search(r"^##\s*Action\b", text, flags=re.MULTILINE)
        if m:
            text = text[m.start():]
    else:
        m = re.search(
            r"##\s*Thought\s*:?\s*[\n\r]+(.*?)(?=##\s*Action|##\s*Code|$)",
            text, re.DOTALL,
        )
        if m:
            result["thought"] = m.group(1).strip()

    # Action
    m = re.search(
        r"##\s*Action\s*:?\s*[\n\r]+(.*?)(?=##\s*Code|```|$)",
        text, re.DOTALL,
    )
    if m:
        result["action"] = m.group(1).strip()

    # Code (last block)
    code_blocks = re.findall(
        r"```(?:python|code)?\s*\n?(.*?)\s*```", text, re.DOTALL
    )
    if code_blocks:
        result["code"] = code_blocks[-1].strip()

    return result


# ─── Coordinate-scaling proxy ───────────────────────────────────────────

class _ScaledPyAutoGUI:
    """Projects [0, 999] model coordinates to physical screen pixels.

    If both x and y are in [0, 999] they are scaled to screen dimensions.
    Values > 999 are passed through as absolute pixel coordinates.
    """

    _COORD_MAX = 999

    def __init__(self, backend, screen_w: int, screen_h: int):
        self._backend = backend
        self._w = screen_w
        self._h = screen_h

    def __getattr__(self, name):
        return getattr(self._backend, name)

    def _in_range(self, x, y) -> bool:
        return (
            isinstance(x, (int, float)) and isinstance(y, (int, float))
            and 0 <= x <= self._COORD_MAX and 0 <= y <= self._COORD_MAX
        )

    def _project(self, args, kwargs):
        if (
            len(args) >= 2
            and isinstance(args[0], (int, float))
            and isinstance(args[1], (int, float))
        ):
            x, y = args[0], args[1]
            if self._in_range(x, y):
                x = int(round(x * self._w / self._COORD_MAX))
                y = int(round(y * self._h / self._COORD_MAX))
            else:
                x, y = int(round(x)), int(round(y))
            return (x, y) + tuple(args[2:]), kwargs
        if "x" in kwargs and "y" in kwargs:
            kw = dict(kwargs)
            x, y = kw["x"], kw["y"]
            if self._in_range(x, y):
                kw["x"] = int(round(x * self._w / self._COORD_MAX))
                kw["y"] = int(round(y * self._h / self._COORD_MAX))
            else:
                kw["x"] = int(round(x))
                kw["y"] = int(round(y))
            return args, kw
        return args, kwargs

    def click(self, *a, **kw):
        a, kw = self._project(a, kw)
        return self._backend.click(*a, **kw)

    def doubleClick(self, *a, **kw):
        a, kw = self._project(a, kw)
        return self._backend.doubleClick(*a, **kw)

    def rightClick(self, *a, **kw):
        a, kw = self._project(a, kw)
        return self._backend.rightClick(*a, **kw)

    def moveTo(self, *a, **kw):
        a, kw = self._project(a, kw)
        return self._backend.moveTo(*a, **kw)

    def dragTo(self, *a, **kw):
        a, kw = self._project(a, kw)
        return self._backend.dragTo(*a, **kw)

    def scroll(self, clicks, x=None, y=None, *args, **kwargs):
        if x is not None and y is not None:
            if self._in_range(x, y):
                scaled_x = int(round(x * self._w / self._COORD_MAX))
                scaled_y = int(round(y * self._h / self._COORD_MAX))
            else:
                scaled_x, scaled_y = int(round(x)), int(round(y))
            return self._backend.scroll(clicks, x=scaled_x, y=scaled_y, *args, **kwargs)
        return self._backend.scroll(clicks, x=x, y=y, *args, **kwargs)

    def _clipboard_type(self, text: str):
        """Type text via clipboard paste — handles CJK / Unicode reliably."""
        import pyperclip
        paste_key = "command" if platform.system() == "Darwin" else "ctrl"
        pyperclip.copy(text)
        self._backend.hotkey(paste_key, "v")
        time.sleep(0.05)

    def write(self, text, *a, **kw):
        try:
            self._clipboard_type(str(text))
        except Exception:
            self._backend.write(text, *a, **kw)

    def typewrite(self, text, *a, **kw):
        self.write(text, *a, **kw)


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
        max_tokens: int = 6000,
        thinking: bool = True,
    ):
        self.last_error: Optional[str] = None
        self.init_ok = False
        self.max_steps = max_steps
        self.max_image_history = max_image_history
        self.max_tokens = max_tokens
        self.thinking = thinking

        # Screen dimensions
        self.screen_width, self.screen_height = 1920, 1080

        # LLM
        self._llm_client: Optional[OpenAI] = None
        self._config_manager = get_config_manager()
        self._agent_model_cfg = self._config_manager.get_model_api_config("agent")

        self._history_template = (
            HISTORY_TEMPLATE_THINKING if self.thinking
            else HISTORY_TEMPLATE_NON_THINKING
        )

        # Kimi-style agent state
        self._current_session_id: Optional[str] = None
        self.actions: List[str] = []
        self.observations: List[bytes] = []
        self.cots: List[Dict[str, str]] = []

        try:
            if pyautogui is None:
                self.last_error = "pyautogui not available (no display)"
                return

            self.screen_width, self.screen_height = pyautogui.size()

            self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                platform=platform.system(),
            )

            api_key = self._agent_model_cfg.get("api_key") or "EMPTY"
            base_url = self._agent_model_cfg.get("base_url", "")
            model = self._agent_model_cfg.get("model", "")
            if not base_url or not model:
                self.last_error = "Agent model not configured"
                return

            self._llm_client = OpenAI(
                base_url=base_url, api_key=api_key, timeout=65.0,
                max_retries=0,
            )

            # Connectivity test (via langchain for compatibility with extra_body)
            from langchain_openai import ChatOpenAI
            test_llm = ChatOpenAI(
                model=model, base_url=base_url, api_key=api_key,
                extra_body=get_agent_extra_body(model) or None,
            ).bind(max_tokens=5)
            _ = test_llm.invoke("ok").content
            self.init_ok = True
        except Exception as e:
            self.last_error = str(e)
            logger.error("ComputerUseAdapter init failed: %s", e)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_available(self) -> Dict[str, Any]:
        model_cfg = self._config_manager.get_model_api_config("agent")
        ok = True
        reasons: List[str] = []
        if not model_cfg.get("base_url") or not model_cfg.get("model"):
            ok = False
            reasons.append("Agent endpoint not configured")
        if pyautogui is None:
            ok = False
            reasons.append("pyautogui not installed")
        if not self.init_ok:
            ok = False
            msg = "Agent not initialized"
            if self.last_error:
                msg += f": {self.last_error}"
            reasons.append(msg)
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
        resized = img.resize((max(320, int(w * 0.56)), max(180, int(h * 0.56))), Image.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format="JPEG", quality=24, optimize=True)
        return buf.getvalue()

    def _fit_images_to_total_budget(self, images: List[bytes], total_budget_bytes: int) -> List[bytes]:
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
            step_text = (
                STEP_TEMPLATE.format(step_num=i + 1)
                + self._history_template.format(
                    thought=self.cots[i].get("thought", ""),
                    action=self.cots[i].get("action", ""),
                )
            )
            # Recent steps: keep the screenshot image
            if i >= history_start:
                if text_parts:
                    messages.append({
                        "role": "assistant",
                        "content": "\n".join(text_parts),
                    })
                    text_parts = []
                img_bytes = packed_history_by_idx.get(i, self.observations[i])
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                        },
                    }],
                })
                messages.append({"role": "assistant", "content": step_text})
            else:
                # Older steps: text only (images dropped to save context)
                text_parts.append(step_text)
                if i == history_start - 1:
                    messages.append({
                        "role": "assistant",
                        "content": "\n".join(text_parts),
                    })
                    text_parts = []

        if text_parts:
            messages.append({
                "role": "assistant",
                "content": "\n".join(text_parts),
            })

        # Current screenshot + task prompt
        cur_b64 = base64.b64encode(packed_current).decode("utf-8")
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{cur_b64}"},
                },
                {"type": "text", "text": instruction_prompt},
            ],
        })

        # ── Call LLM ─────────────────────────────────────────────────
        parsed = self._call_llm(messages)
        code = parsed.get("code", "")
        thought = parsed.get("thought", "")
        action = parsed.get("action", "")

        print(f"[CUA] Step {step_num}, {action[:120]}") # 敏感日志使用print而不是logger，用于脱敏

        # ── Update agent state ───────────────────────────────────────
        self.observations.append(screenshot_bytes)
        self.actions.append(action)
        self.cots.append(parsed)

        # Force termination at step limit
        if step_num >= self.max_steps and "computer.terminate" not in code.lower():
            logger.warning(
                "Reached max steps %d. Forcing termination.", self.max_steps
            )
            code = (
                'computer.terminate(status="failure", '
                'answer="Reached maximum step limit")'
            )

        return {"thought": thought, "action": action, "code": code}, code

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

        if session_id is None or session_id != self._current_session_id:
            self.reset()
            self._current_session_id = session_id

        last_action = ""
        success = False
        answer = ""

        try:
            for step in range(1, self.max_steps + 1):
                t0 = time.monotonic()
                shot = pyautogui.screenshot()
                jpg_bytes = compress_screenshot(shot)
                t_capture = time.monotonic() - t0

                t1 = time.monotonic()
                info, code = self.predict(instruction, {"screenshot": jpg_bytes})
                t_llm = time.monotonic() - t1
                logger.info(
                    "[CUA] Step %d timing: capture=%.1fs (%dKB), llm=%.1fs",
                    step, t_capture, len(jpg_bytes) // 1024, t_llm,
                )

                if not code:
                    continue

                last_action = info.get("action", "")
                code_lower = code.lower()

                # ── Special actions ──────────────────────────────────
                if "computer.terminate" in code_lower:
                    m_status = re.search(r'status\s*=\s*["\'](\w+)["\']', code)
                    success = (m_status.group(1).lower() == "success") if m_status else False
                    m_answer = re.search(
                        r'answer\s*=\s*["\'](.+?)["\']', code, re.DOTALL
                    )
                    answer = m_answer.group(1) if m_answer else last_action
                    break

                if "computer.wait" in code_lower:
                    m = re.search(r"seconds\s*=\s*(\d+)", code)
                    wait_s = int(m.group(1)) if m else 5
                    time.sleep(min(wait_s, 30))
                    continue

                # ── Execute pyautogui code ───────────────────────────
                try:
                    exec_env: dict = {"__builtins__": __builtins__}
                    exec_env["pyautogui"] = _ScaledPyAutoGUI(
                        pyautogui, self.screen_width, self.screen_height
                    )
                    exec_env["time"] = time
                    exec_env["os"] = os
                    exec(code, exec_env)
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning(
                        "[CUA] Exec error step %d: %s\nCode: %s", step, e, code
                    )
                    time.sleep(0.3)
            else:
                answer = f"Reached {self.max_steps} steps without completion"
                success = False

        except Exception as e:
            logger.error(
                "[CUA] run_instruction error: %s\n%s", e, traceback.format_exc()
            )
            return {"success": False, "error": str(e)}

        return {
            "success": success,
            "result": answer or last_action,
            "steps": len(self.actions),
        }

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, messages: list) -> Dict[str, str]:
        """Call the VLM with retry, return parsed response."""
        model = self._agent_model_cfg.get("model", "")
        extra = get_agent_extra_body(model) or {}

        for attempt in range(3):
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
                        "code": 'computer.terminate(status="failure", answer="免费 Agent 模型今日试用次数已达上限（300次）")',
                        "raw": "",
                    }
                resp = self._llm_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_completion_tokens=self.max_tokens,
                    extra_body=extra or None,
                )
                msg = resp.choices[0].message
                content = msg.content or ""
                reasoning = getattr(msg, "reasoning_content", None)

                parsed = parse_response(
                    content, reasoning if self.thinking else None
                )
                if parsed["code"]:
                    return parsed

                logger.warning(
                    "[CUA] No code (attempt %d): %.300s", attempt + 1, content
                )
            except Exception as e:
                logger.error("[CUA] LLM error (attempt %d): %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(2)

        return {"thought": "", "action": "", "code": "", "raw": ""}

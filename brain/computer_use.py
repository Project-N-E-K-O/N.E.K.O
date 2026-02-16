"""
Adapter for Simular Agent-S gui_agents SDK.
Converts natural-language instructions into Python actions that control the local computer.

This adapter exposes two key methods:
- is_available(): checks environment and config to determine if GUI agents can run
- run_instruction(instruction, screenshot_bytes=None): executes one-shot instruction

Note: This is a minimal integration. For production, add session/state mgmt and safety prompts.
"""
from typing import Dict, Any, Optional
import re
import io
import platform, os, time
from PIL import Image
from langchain_openai import ChatOpenAI
from config import get_extra_body

# Improve DPI accuracy on Windows to avoid coordinate offsets with pyautogui
try:
    if platform.system().lower() == "windows":
        import ctypes  # type: ignore
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
    import pyautogui  # runtime requirement of gui_agents examples
except Exception:
    pyautogui = None

from utils.config_manager import get_config_manager

def scale_screen_dimensions(width: int, height: int, max_dim_size: int):
    scale_factor = min(max_dim_size / width, max_dim_size / height)
    safe_width = int(width * scale_factor)
    safe_height = int(height * scale_factor)
    print("safe_width, safe_height:", safe_width, safe_height)
    return safe_width, safe_height

class _ScaledPyAutoGUI:
    """
    Lightweight proxy to scale coordinates from a logical (scaled) space
    back to the physical screen coordinates before delegating to pyautogui.
    Only wraps common absolute-coordinate mouse functions used by agents.
    """
    def __init__(self, backend, scale_x: float, scale_y: float):
        self._backend = backend
        self._scale_x = scale_x
        self._scale_y = scale_y

    def __getattr__(self, name):
        # Fallback for all other attributes/methods
        return getattr(self._backend, name)

    def _scale_xy_from_args(self, args, kwargs):
        # Returns (new_args, new_kwargs) with x,y scaled when present
        if len(args) >= 2 and isinstance(args[0], (int, float)) and isinstance(args[1], (int, float)):
            x = int(round(args[0] * self._scale_x))
            y = int(round(args[1] * self._scale_y))
            args = (x, y) + tuple(args[2:])
        elif len(args) >= 1 and isinstance(args[0], (tuple, list)) and len(args[0]) == 2:
            x_raw, y_raw = args[0]
            if isinstance(x_raw, (int, float)) and isinstance(y_raw, (int, float)):
                x = int(round(x_raw * self._scale_x))
                y = int(round(y_raw * self._scale_y))
                args = ((x, y),) + tuple(args[1:])
        else:
            # Try kwargs variant
            if 'x' in kwargs and 'y' in kwargs and isinstance(kwargs['x'], (int, float)) and isinstance(kwargs['y'], (int, float)):
                kwargs = dict(kwargs)
                kwargs['x'] = int(round(kwargs['x'] * self._scale_x))
                kwargs['y'] = int(round(kwargs['y'] * self._scale_y))
        return args, kwargs

    # Wrapped absolute-coordinate mouse APIs
    def moveTo(self, *args, **kwargs):
        args, kwargs = self._scale_xy_from_args(args, kwargs)
        return self._backend.moveTo(*args, **kwargs)

    def click(self, *args, **kwargs):
        args, kwargs = self._scale_xy_from_args(args, kwargs)
        return self._backend.click(*args, **kwargs)

    def doubleClick(self, *args, **kwargs):
        args, kwargs = self._scale_xy_from_args(args, kwargs)
        return self._backend.doubleClick(*args, **kwargs)

    def rightClick(self, *args, **kwargs):
        args, kwargs = self._scale_xy_from_args(args, kwargs)
        return self._backend.rightClick(*args, **kwargs)

    def dragTo(self, *args, **kwargs):
        args, kwargs = self._scale_xy_from_args(args, kwargs)
        return self._backend.dragTo(*args, **kwargs)

class ComputerUseAdapter:
    def __init__(self):
        self.last_error: Optional[str] = None
        self.agent = None
        self.grounding_agent = None
        self.init_ok = False
        # 初始化默认屏幕尺寸（避免在无显示器环境如 Docker 中出错）
        self.screen_width, self.screen_height = 1920, 1080
        self.scaled_width, self.scaled_height = 1920, 1080
        self.scale_x, self.scale_y = 1.0, 1.0
        # 获取配置
        self._config_manager = get_config_manager()
        self._agent_model_cfg = self._config_manager.get_model_api_config('agent')
        try:
            from brain.cua.agents.grounding import OSWorldACI
            # Monkey patch: adjust Windows docstring without modifying site-packages
            OSWorldACI.open.__doc__ = (
                "Open any application or file with name app_or_filename. "
                "Use this ONLY on Linux/Darwin platform. Do NOT use this on Windows."
            )
            # Monkey patch: click center of bbox if grounding model outputs a box
            try:
                from brain.cua.utils.common_utils import call_llm_safe

                def _patched_generate_coords(self, ref_expr: str, obs: Dict) -> list[int]:
                    self.grounding_model.reset()
                    # Prefer GLM-4.5V Grounding box tokens when available
                    prompt = (
                        "Locate the referenced target in the screenshot: "
                        f"{ref_expr}\n"
                        "If possible, output a bounding box using [x1, y1, x2, y2]. "
                        "Coordinates must be normalized in the range 0..1000 (x is horizontal, y is vertical; top-left to bottom-right).\n"
                        "If a box is not suitable, output exactly two pixel coordinates: x,y. Do not include any explanations or extra text."
                    )
                    self.grounding_model.add_message(
                        text_content=prompt, image_content=obs["screenshot"], put_text_last=True
                    )
                    response = call_llm_safe(self.grounding_model)
                    print("RAW GROUNDING MODEL RESPONSE:", response)
                    # First, try to parse GLM-4.5V grounding tokens
                    try:
                        box_match = re.search(r"<\|begin_of_box\|>([\s\S]*?)<\|end_of_box\|>", response)
                        if box_match:
                            inner = box_match.group(1)
                            nums = re.findall(r"-?\d+\.?\d*", inner)
                            values = [float(n) for n in nums]
                            if len(values) >= 4:
                                x1, y1, x2, y2 = values[:4]
                                # clamp to [0,1000]
                                def clamp_0_1000(v: float) -> float:
                                    return 0.0 if v < 0 else 1000.0 if v > 1000 else v
                                x1, y1, x2, y2 = map(clamp_0_1000, (x1, y1, x2, y2))
                                cx = (x1 + x2) / 2.0
                                cy = (y1 + y2) / 2.0
                                w = getattr(self, "width", None) or 1000
                                h = getattr(self, "height", None) or 1000
                                x_px = int(round(cx / 1000.0 * w))
                                y_px = int(round(cy / 1000.0 * h))
                                return [x_px, y_px]
                    except Exception:
                        pass

                    # Fallbacks: parse numericals directly
                    numericals = [int(float(x)) for x in re.findall(r"-?\d+\.?\d*", response)]
                    if len(numericals) >= 4:
                        x1, y1, x2, y2 = numericals[:4]
                        if max(x1, y1, x2, y2) <= 1000:
                            w = getattr(self, "width", None) or 1000
                            h = getattr(self, "height", None) or 1000
                            cx = (x1 + x2) / 2.0
                            cy = (y1 + y2) / 2.0
                            return [int(round(cx / 1000.0 * w)), int(round(cy / 1000.0 * h))]
                        return [(x1 + x2) // 2, (y1 + y2) // 2]
                    assert len(numericals) >= 2
                    x, y = numericals[0], numericals[1]
                    if x <= 1000 and y <= 1000:
                        w = getattr(self, "width", None) or 1000
                        h = getattr(self, "height", None) or 1000
                        return [int(round(x / 1000.0 * w)), int(round(y / 1000.0 * h))]
                    return [x, y]

                OSWorldACI.generate_coords = _patched_generate_coords
            except Exception:
                pass
            # Monkey patch: make assign_coordinates tolerant to non-coordinate actions
            try:
                from brain.cua.utils.common_utils import parse_single_code_from_string

                def _patched_assign_coordinates(self, plan: str, obs: Dict):
                    # Reset previous coords
                    self.coords1, self.coords2 = None, None
                    try:
                        segment = plan.split("Grounded Action")[-1]
                        try:
                            action = parse_single_code_from_string(segment)
                        except Exception:
                            action = segment
                        match = re.search(r"(agent\.[a-zA-Z_]+)\(", action)
                        if not match:
                            # No recognizable agent function → nothing to ground
                            return
                        function_name = match.group(1)
                        args = self.parse_function_args(action)
                    except Exception:
                        # If parsing fails, skip grounding instead of raising
                        return

                    try:
                        # Heuristic: if clicking numeric key (e.g., calculator digits), try OCR-first
                        if (
                            function_name == "agent.click"
                            and len(args) >= 1
                            and args[0] is not None
                        ):
                            digit_match = re.search(r"(?<!\d)(\d)(?!\d)", str(args[0]))
                            if digit_match:
                                digit = digit_match.group(1)
                                try:
                                    _, ocr_elements = self.get_ocr_elements(obs["screenshot"])
                                    digit_elems = [e for e in ocr_elements if str(e.get("text", "")).strip() == digit]
                                    if digit_elems:
                                        # Prefer the lowest one on screen (calculator keypad bottom rows)
                                        best = max(digit_elems, key=lambda e: e["top"])
                                        cx = best["left"] + (best["width"] // 2)
                                        cy = best["top"] + (best["height"] // 2)
                                        self.coords1 = [cx, cy]
                                        return
                                except Exception:
                                    pass
                        
                        if (
                            function_name in ["agent.click", "agent.type", "agent.scroll"]
                            and len(args) >= 1
                            and args[0] is not None
                        ):
                            self.coords1 = self.generate_coords(args[0], obs)
                        elif function_name == "agent.drag_and_drop" and len(args) >= 2:
                            self.coords1 = self.generate_coords(args[0], obs)
                            self.coords2 = self.generate_coords(args[1], obs)
                        elif function_name == "agent.highlight_text_span" and len(args) >= 2:
                            self.coords1 = self.generate_text_coords(args[0], obs, alignment="start")
                            self.coords2 = self.generate_text_coords(args[1], obs, alignment="end")
                        # else: functions that do not require coordinates
                    except Exception:
                        # On any failure, avoid raising to keep executor progressing
                        self.coords1, self.coords2 = None, None
                        return

                OSWorldACI.assign_coordinates = _patched_assign_coordinates
            except Exception:
                # If monkey patching fails, continue with the original behavior
                pass
            from brain.cua.agents.agent_s import AgentS2_5
            if pyautogui is None:
                # 无显示器环境（如 Docker），GUI agent 无法工作
                self.last_error = "pyautogui not available (no display). GUI agent cannot run in headless environment."
                print("GUI agent unavailable: pyautogui requires a display")
                return  # 直接返回，不继续初始化
            
            self.screen_width, self.screen_height = pyautogui.size()
            print("screen_width, screen_height:", self.screen_width, self.screen_height)
            self.scaled_width, self.scaled_height = self.screen_width, self.screen_height#scale_screen_dimensions(self.screen_width, self.screen_height, max_dim_size=1920)
            # Precompute scale factors from logical (scaled) space -> physical screen
            self.scale_x = self.screen_width / max(1, self.scaled_width)
            self.scale_y = self.screen_height / max(1, self.scaled_height)

            engine_params, engine_params_for_grounding = self._build_params()
            self.grounding_agent = OSWorldACI(
                platform=platform.system().lower(),
                engine_params_for_generation=engine_params,
                engine_params_for_grounding=engine_params_for_grounding,
                width=self.scaled_width,
                height=self.scaled_height,
            )
            self.agent = AgentS2_5(
                engine_params,
                self.grounding_agent,
                platform=platform.system().lower(),
                max_trajectory_length=3,
                enable_reflection=False,
            )
            # Connectivity check for grounding model via ChatOpenAI
            try:
                self._agent_model_cfg = self._config_manager.get_model_api_config('agent')
                api_key = self._agent_model_cfg.get('api_key') or None
                ground_model = self._agent_model_cfg.get('model', '')
                test_llm = ChatOpenAI(
                    model=ground_model,
                    base_url=self._agent_model_cfg.get('base_url', ''),
                    api_key=api_key,
                    temperature=0,
                    extra_body=get_extra_body(ground_model) or None
                ).bind(max_tokens=5)
                _ = test_llm.invoke("ok").content
                self.init_ok = True
            except Exception as e:
                self.last_error = f"GUI Grounding model initialization failed: {e}"
                print("GUI Grounding model initialization failed:", e)
                try:
                    if self.grounding_agent is not None:
                        setattr(self.grounding_agent, "grounding_model", None)
                except Exception:
                    pass
                self.init_ok = False
        except Exception as e:
            self.last_error = str(e)
            print("Failed to initialize gui_agents. ", e)

    def is_available(self) -> Dict[str, Any]:
        ok = True
        reasons = []
        model_cfg = self._config_manager.get_model_api_config('agent')
        if not model_cfg.get('base_url') or not model_cfg.get('model'):
            ok = False
            reasons.append("Grounding endpoint not configured")
        if pyautogui is None:
            ok = False
            reasons.append("pyautogui not installed")
        if not getattr(self, "init_ok", False):
            ok = False
            msg = "gui_agents not initialized"
            if self.last_error:
                msg += f": {self.last_error}"
            reasons.append(msg)
        return {
            "enabled": True,
            "ready": ok,
            "reasons": reasons,
            "provider": "openai",
            "model": model_cfg.get('model', ''),
            "ground_provider": "openai",
            "ground_model": model_cfg.get('model', ''),
        }

    def _build_params(self) -> Dict[str, Any]:
        model_cfg = self._config_manager.get_model_api_config('agent')
        engine_params = {
            "engine_type": "openai",
            "model": model_cfg.get('model', ''),
            "base_url": model_cfg.get('base_url', '') or "",
            "api_key": model_cfg.get('api_key', '') or "",
            "thinking": False,
            "extra_body": {"thinking": { "type": "disabled"}}
        }
        engine_params_for_grounding = {
            "engine_type": "openai",
            "model": model_cfg.get('model', ''),
            "base_url": model_cfg.get('base_url', ''),
            "api_key": model_cfg.get('api_key', '') or "",
            "grounding_width": self.scaled_width,
            "grounding_height": self.scaled_height,
            "thinking": False,
            "extra_body": {"thinking": { "type": "disabled"}}
        }
        return engine_params, engine_params_for_grounding

    def _take_screenshot(self) -> Optional[bytes]:
        if pyautogui is None:
            return None
        shot = pyautogui.screenshot()
        buf = io.BytesIO()
        shot.save(buf, format="PNG")
        return buf.getvalue()

    def run_instruction(self, instruction: str, session_id: Optional[str] = None):
        """Execute a natural-language instruction via GUI automation.

        Args:
            instruction: What to do (e.g. "打开系统计算器").
            session_id: If provided and matches the current session, agent
                state is preserved for multi-turn execution.  Otherwise the
                agent is reset to avoid cross-task state pollution.
        """
        if not self.agent:
            return {"success": False, "error": "computer-use agent not initialized"}

        # Reset agent state for a fresh task unless continuing a session
        if session_id is None or session_id != getattr(self, "_current_session_id", None):
            self.agent.reset()
            self._current_session_id = session_id

        try:
            obs = {}
            traj = "Task:\n" + instruction
            for _ in range(15):
                # Get screen shot using pyautogui
                screenshot = pyautogui.screenshot()
                screenshot = screenshot.resize((self.scaled_width, self.scaled_height), Image.LANCZOS)

                # Save the screenshot to a BytesIO object
                buffered = io.BytesIO()
                screenshot.save(buffered, format="PNG")

                # Get the byte value of the screenshot
                screenshot_bytes = buffered.getvalue()
                # Convert to base64 string.
                obs["screenshot"] = screenshot_bytes

                # Get next action code from the agent
                info, code = self.agent.predict(instruction=instruction, observation=obs)
                print("EXECUTING CODE:", code[0])
                if code[0] == None:
                    continue

                if "done" in code[0].lower() or "fail" in code[0].lower():
                    if platform.system() == "Darwin":
                        os.system(
                            f'osascript -e \'display dialog "Task Completed" with title "OpenACI Agent" buttons "OK" default button "OK"\''
                        )
                    elif platform.system() == "Linux":
                        os.system(
                            f'zenity --info --title="OpenACI Agent" --text="Task Completed" --width=200 --height=100'
                        )

                    break

                if "next" in code[0].lower():
                    continue

                if "wait" in code[0].lower():
                    time.sleep(3)
                    continue

                else:
                    time.sleep(0.1)
                    # print("EXECUTING CODE:", code[0])

                    # Ask for permission before executing
                    # Inject scaled pyautogui so that logical coords map to physical screen
                    exec_env = globals().copy()
                    if pyautogui is not None and hasattr(self, 'scale_x') and hasattr(self, 'scale_y'):
                        exec_env['pyautogui'] = _ScaledPyAutoGUI(pyautogui, self.scale_x, self.scale_y)
                    exec(code[0], exec_env, exec_env)
                    time.sleep(0.5)

                    # Update task and subtask trajectories
                    if "reflection" in info and "executor_plan" in info:
                        traj += (
                            "\n\nReflection:\n"
                            + str(info["reflection"])
                            + "\n\n----------------------\n\nPlan:\n"
                            + info["executor_plan"]
                        )
        except Exception as e:
            print("ERROR:", e)
            return {"success": False, "error": str(e)}



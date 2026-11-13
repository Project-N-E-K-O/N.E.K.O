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

"""The keyboard-and-mouse tools: one action on the desktop per call.

A task agent looks at a screenshot, names a point on it and an action, and
gets back a new screenshot taken after the action. There is no loop here and
no model call: the task agent's own loop decides every step.

Coordinates are those the keyboard-and-mouse loop this replaces used: both
axes run from 0 to 999 whatever the size of the screen, (0, 0) being the
top-left corner. ``ScaledInput`` turns them into screen pixels. A value
outside that range is refused rather than read as a pixel, since the picture
the model sees is smaller than the screen.

The screenshot code (the Electron bridge on Linux, then the native grab), the
coordinate projection, the clipboard typing of non-ASCII text and the
bookkeeping of held keys and buttons are taken over from
``brain/computer_use.py``, which this module replaces.

Everything that touches the desktop blocks, so ``Desktop.act`` is called in a
worker thread. One thread at a time: ``EXECUTION_LOCK``.
"""

from __future__ import annotations

import base64
import logging
import platform
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)

COORDINATE_MAX = 999
# Screenshots are bounded to this height before they are handed to a model,
# as the loop this replaces did: small interface text must stay readable.
SCREENSHOT_HEIGHT = 1080
# Byte ceiling of one screenshot. Three screenshots stay in a task agent's
# context, of all the built-in tools together (``TASK_AGENT_SCREENSHOTS_KEPT``),
# and the request must stay well under what providers take: the loop this
# replaces kept to about half a megabyte.
SCREENSHOT_MAX_BYTES = 200 * 1024
# How long letting go of held keys waits for a call that still has the
# keyboard and the mouse.
RELEASE_WAIT_SECONDS = 5.0
# How long after an action the screenshot of its result is taken.
SETTLE_SECONDS = 0.6
WAIT_DEFAULT_SECONDS = 1.0
WAIT_MAX_SECONDS = 30.0
HOLD_MAX_SECONDS = 10.0
SCROLL_DEFAULT_NOTCHES = 3
SCROLL_MAX_NOTCHES = 50
TYPE_MAX_CHARACTERS = 4000

ACTIONS: tuple[str, ...] = ("screenshot", "click", "move", "drag", "scroll", "type", "key", "wait")
BUTTONS: tuple[str, ...] = ("left", "right", "middle")
DIRECTIONS: tuple[str, ...] = ("up", "down", "left", "right")

# One thread drives the desktop at a time, whoever asked for it.
EXECUTION_LOCK = threading.Lock()

_KEY_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "cmd": "command",
    "meta": "win",
    "super": "win",
    "windows": "win",
    "option": "alt",
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "spacebar": "space",
}


class DesktopActionError(Exception):
    """An action that cannot be carried out as asked; the text is for the model."""


class DesktopStopped(Exception):
    """The call was stopped while it was working."""


# ---------------------------------------------------------------------------
# pyautogui, loaded on first use
# ---------------------------------------------------------------------------

_pyautogui: Any = None
_import_error: Exception | None = None
_dpi_awareness_set = False


def _set_dpi_awareness() -> None:
    """Make this process see real pixels on Windows, before pyautogui is imported.

    pyautogui only asks for the older, system-wide awareness; with it a
    screenshot and a click disagree on a display scaled differently from the
    main one.
    """
    global _dpi_awareness_set
    if _dpi_awareness_set:
        return
    _dpi_awareness_set = True
    if platform.system().lower() != "windows":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - older Windows, or already set
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - coordinates may be off on scaled displays; input still works
        logger.debug("setting the DPI awareness failed", exc_info=True)


def load_pyautogui() -> Any:
    """Return pyautogui, importing it on first use; None when it cannot be imported.

    Importing is tried again on every call until it works, so a Linux display
    that is authorized later is picked up.
    """
    global _pyautogui, _import_error
    if _pyautogui is not None:
        return _pyautogui
    _set_dpi_awareness()
    try:
        import pyautogui as loaded
    except Exception as exc:  # noqa: BLE001 - reported through pyautogui_unavailable_reason
        _import_error = exc
        return None
    _pyautogui = loaded
    _import_error = None
    return _pyautogui


def pyautogui_unavailable_reason() -> str:
    """Return the stable reason code for pyautogui not being importable."""
    from utils.pyautogui_diagnostics import classify_pyautogui_import_error

    return classify_pyautogui_import_error(_import_error, platform_name=platform.system())


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------

_bridge_backoff_until = 0.0
_BRIDGE_BACKOFF_SECONDS = 20.0


def _post_capture_bridge(cancel_event: threading.Event | None) -> Any:
    """Ask the Electron shell for a screenshot, giving up at once when the call is stopped."""
    import httpx

    from config import MAIN_SERVER_PORT

    active_client: list[httpx.Client] = []
    client_lock = threading.Lock()

    def post() -> httpx.Response:
        timeout = httpx.Timeout(28.0, connect=1.0)
        with httpx.Client(timeout=timeout, proxy=None, trust_env=False) as client:
            with client_lock:
                active_client.append(client)
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("stopped")
                return client.post(f"http://127.0.0.1:{MAIN_SERVER_PORT}/api/capture/computer-use")
            finally:
                with client_lock:
                    active_client.clear()

    if cancel_event is None:
        return post()
    if cancel_event.is_set():
        raise InterruptedError("stopped")

    result: dict[str, Any] = {}
    finished = threading.Event()

    def run_post() -> None:
        try:
            result["response"] = post()
        except Exception as exc:  # noqa: BLE001 - handed to the waiting thread
            result["error"] = exc
        finally:
            finished.set()

    # The read timeout of the request is an idle timeout, so a stalled bridge
    # must not keep a stopped call waiting until the request is over.
    threading.Thread(target=run_post, daemon=True).start()
    while not finished.wait(0.05):
        if cancel_event.is_set():
            with client_lock:
                client = active_client[0] if active_client else None
            if client is not None:
                client.close()
            raise InterruptedError("stopped")
    if cancel_event.is_set():
        raise InterruptedError("stopped")
    if "error" in result:
        raise result["error"]
    return result["response"]


def capture_frame(cancel_event: threading.Event | None = None) -> Any:
    """Return the desktop as a PIL image: the Electron bridge where there is one, else the native grab."""
    import httpx
    from PIL import Image

    from utils.desktop_capture import DesktopCaptureError, capture_desktop_screenshot

    global _bridge_backoff_until
    bridge_error = None
    if platform.system().lower() == "linux" and time.monotonic() >= _bridge_backoff_until:
        try:
            response = _post_capture_bridge(cancel_event)
            _bridge_backoff_until = 0.0
            if response.status_code == 504:
                # The bridge gives up (25 s) before this client does (28 s),
                # so a stalled renderer normally shows up here.
                _bridge_backoff_until = time.monotonic() + _BRIDGE_BACKOFF_SECONDS
            payload = response.json()
            if response.status_code != 200:
                reason = payload.get("error") if isinstance(payload, dict) else None
                raise DesktopCaptureError(f"renderer capture unavailable: {reason or response.status_code}")
            data_url = payload.get("image") if isinstance(payload, dict) else None
            if (
                not isinstance(data_url, str)
                or not data_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
                or len(data_url) > 10 * 1024 * 1024
            ):
                raise DesktopCaptureError("renderer returned an invalid image payload")
            image_bytes = base64.b64decode(data_url.split(",", 1)[1], validate=True)
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                if image.width < 1 or image.height < 1:
                    raise DesktopCaptureError("renderer returned an empty image")
                return image.copy()
        except InterruptedError:
            raise
        except (httpx.HTTPError, ValueError, OSError, DesktopCaptureError) as exc:
            bridge_error = exc
            if isinstance(exc, httpx.TimeoutException):
                _bridge_backoff_until = time.monotonic() + _BRIDGE_BACKOFF_SECONDS
            logger.info("desktop capture through the shell unavailable (%s); trying the native grab", type(exc).__name__)

    try:
        return capture_desktop_screenshot()
    except Exception as exc:
        if bridge_error is not None:
            raise DesktopCaptureError(f"the shell's capture and the native screenshot both failed: {bridge_error}; {exc}") from exc
        raise


def fit_jpeg(jpeg_bytes: bytes, target_bytes: int) -> bytes:
    """Return the JPEG re-encoded to at most ``target_bytes`` where that can be done: quality first, then size."""
    from PIL import Image

    if len(jpeg_bytes) <= target_bytes:
        return jpeg_bytes
    try:
        image = Image.open(BytesIO(jpeg_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 - sent as it is
        return jpeg_bytes
    for quality in (68, 60, 52, 44, 36, 30, 26):
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        if buffer.tell() <= target_bytes:
            return buffer.getvalue()
    width, height = image.size
    smallest = jpeg_bytes
    for scale in (0.9, 0.8, 0.72, 0.64, 0.56):
        resized = image.resize((max(320, int(width * scale)), max(180, int(height * scale))), Image.Resampling.LANCZOS)
        for quality in (54, 46, 38, 32, 26):
            buffer = BytesIO()
            resized.save(buffer, format="JPEG", quality=quality, optimize=True)
            smallest = buffer.getvalue()
            if len(smallest) <= target_bytes:
                return smallest
    return smallest


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

_MOUSEEVENTF_HWHEEL = 0x01000


def turn_wheel_sideways_on_windows(amount: int) -> None:
    """Turn the horizontal wheel on Windows; a positive amount is to the right, 120 is one notch.

    pyautogui cannot be asked for it: its ``hscroll`` turns the vertical
    wheel there (``_pyautogui_win._hscroll`` calls ``_scroll``).
    """
    import ctypes

    ctypes.windll.user32.mouse_event(_MOUSEEVENTF_HWHEEL, 0, 0, amount, 0)


class ScaledInput:
    """Sends input through pyautogui at coordinates given on the 0 to 999 scale.

    Keeps track of the keys and buttons it holds down, so they can be let go
    when a call is stopped half way.
    """

    _FAILSAFE_EDGE_PX = 4

    def __init__(
        self,
        backend: Any = None,
        screen_w: int = 1920,
        screen_h: int = 1080,
        cancel_event: threading.Event | None = None,
        held_keys: list[str] | None = None,
        held_buttons: list[str] | None = None,
    ) -> None:
        self._backend = backend if backend is not None else load_pyautogui()
        self._held_keys = held_keys if held_keys is not None else []
        self._held_buttons = held_buttons if held_buttons is not None else []
        self._w = screen_w
        self._h = screen_h
        self._cancel_event = cancel_event

    def ensure_not_stopped(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise InterruptedError("stopped")

    def settle_for(self, seconds: float) -> None:
        """Wait, or stop waiting and raise as soon as the call is stopped."""
        if self._cancel_event is None:
            time.sleep(seconds)
            return
        if self._cancel_event.wait(timeout=seconds):
            raise InterruptedError("stopped")

    def to_pixels(self, x: float, y: float) -> tuple[int, int]:
        """Return the screen pixel of a point on the 0 to 999 scale.

        Kept a few pixels off the edges: pyautogui stops everything when the
        pointer reaches a corner, which is for the user to do, not for us.
        """
        max_x = max(0, self._w - 1)
        max_y = max(0, self._h - 1)
        px = int(round(float(x) * max_x / COORDINATE_MAX))
        py = int(round(float(y) * max_y / COORDINATE_MAX))
        px = max(0, min(px, max_x))
        py = max(0, min(py, max_y))
        if self._w > self._FAILSAFE_EDGE_PX * 2:
            px = max(self._FAILSAFE_EDGE_PX, min(px, max_x - self._FAILSAFE_EDGE_PX))
        if self._h > self._FAILSAFE_EDGE_PX * 2:
            py = max(self._FAILSAFE_EDGE_PX, min(py, max_y - self._FAILSAFE_EDGE_PX))
        return px, py

    def _glide_to(self, px: int, py: int, duration: float = 0.3) -> None:
        """Move the pointer there along an eased path, so the user can follow it."""
        try:
            tween = getattr(self._backend, "easeOutQuad", None)
            self._backend.moveTo(px, py, duration=duration, _pause=False, **({"tween": tween} if tween else {}))
        except Exception:  # noqa: BLE001 - the plain move is tried next
            try:
                self._backend.moveTo(px, py, _pause=False)
            except Exception:  # noqa: BLE001 - the click itself still names the point
                logger.debug("moving the pointer failed", exc_info=True)

    def click(self, x: float, y: float, *, button: str = "left", clicks: int = 1) -> tuple[int, int]:
        self.ensure_not_stopped()
        px, py = self.to_pixels(x, y)
        self._glide_to(px, py)
        self.ensure_not_stopped()
        self._backend.click(px, py, clicks=clicks, interval=0.08 if clicks > 1 else 0.0, button=button)
        return px, py

    def move(self, x: float, y: float) -> tuple[int, int]:
        self.ensure_not_stopped()
        px, py = self.to_pixels(x, y)
        self._glide_to(px, py)
        return px, py

    def drag(self, x: float, y: float, to_x: float, to_y: float, *, button: str = "left") -> tuple[int, int, int, int]:
        self.ensure_not_stopped()
        sx, sy = self.to_pixels(x, y)
        ex, ey = self.to_pixels(to_x, to_y)
        self._glide_to(sx, sy)
        self.ensure_not_stopped()
        self._backend.mouseDown(sx, sy, button=button)
        if button not in self._held_buttons:
            self._held_buttons.append(button)
        try:
            self.settle_for(0.1)
            self._backend.moveTo(ex, ey, duration=0.5)
            self.settle_for(0.1)
        finally:
            self._backend.mouseUp(ex, ey, button=button)
            if button in self._held_buttons:
                self._held_buttons.remove(button)
        return sx, sy, ex, ey

    def scroll(self, direction: str, notches: int, x: float | None = None, y: float | None = None) -> None:
        self.ensure_not_stopped()
        if x is not None and y is not None:
            px, py = self.to_pixels(x, y)
            self._glide_to(px, py, duration=0.15)
        # pyautogui hands the number to Windows as it is, and there one notch
        # of the wheel is 120; elsewhere the number is the notches.
        windows = platform.system().lower() == "windows"
        amount = notches * (120 if windows else 1)
        if direction in ("up", "down"):
            self._backend.scroll(amount if direction == "up" else -amount)
        elif windows:
            # pyautogui's ``hscroll`` turns the vertical wheel on Windows.
            turn_wheel_sideways_on_windows(amount if direction == "right" else -amount)
        else:
            self._backend.hscroll(amount if direction == "right" else -amount)

    def _paste(self, text: str) -> None:
        import pyperclip

        pyperclip.copy(text)
        self._backend.hotkey("command" if platform.system() == "Darwin" else "ctrl", "v")
        time.sleep(0.05)

    def write(self, text: str) -> None:
        """Type text where the keyboard focus is; text beyond ASCII goes through the clipboard."""
        self.ensure_not_stopped()
        if any(ord(char) > 127 for char in text):
            try:
                self._paste(text)
                return
            except Exception:  # noqa: BLE001 - typed key by key instead
                logger.debug("typing through the clipboard failed", exc_info=True)
        self._backend.write(text)

    def press(self, keys: list[str], hold_seconds: float = 0.0) -> None:
        """Press a key or a combination; with ``hold_seconds`` keep it down that long."""
        self.ensure_not_stopped()
        if hold_seconds <= 0:
            if len(keys) == 1:
                self._backend.press(keys[0])
            else:
                self._backend.hotkey(*keys)
            return
        down: list[str] = []
        try:
            for key in keys:
                self._backend.keyDown(key)
                self._held_keys.append(key)
                down.append(key)
            self.settle_for(hold_seconds)
        finally:
            for key in reversed(down):
                try:
                    self._backend.keyUp(key)
                except Exception:  # noqa: BLE001 - left in the held list for release_held
                    continue
                while key in self._held_keys:
                    self._held_keys.remove(key)

    def release_held(self) -> bool:
        """Let go of every key and button still held; return whether none is left.

        pyautogui's fail-safe (the pointer in a corner) stops actions, but it
        must not stop held input from being released.
        """
        failsafe = getattr(self._backend, "FAILSAFE", None)
        try:
            if failsafe is not None:
                self._backend.FAILSAFE = False
            for key in list(self._held_keys):
                try:
                    self._backend.keyUp(key, _pause=False)
                except Exception:  # noqa: BLE001 - kept for the next try
                    continue
                while key in self._held_keys:
                    self._held_keys.remove(key)
            for button in list(self._held_buttons):
                try:
                    self._backend.mouseUp(button=button, _pause=False)
                except Exception:  # noqa: BLE001 - kept for the next try
                    continue
                while button in self._held_buttons:
                    self._held_buttons.remove(button)
        finally:
            if failsafe is not None:
                self._backend.FAILSAFE = failsafe
        return not self._held_keys and not self._held_buttons


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DesktopResult:
    """What one action came back with."""

    text: str
    # The screenshot taken after the action, JPEG bytes.
    screenshot: bytes | None = None
    mime_type: str = "image/jpeg"


def _coordinate(arguments: dict[str, Any], name: str, *, required: bool = True) -> int | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise DesktopActionError(f"{name} is required for this action")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopActionError(f"{name} must be an integer from 0 to {COORDINATE_MAX}")
    if value != int(value) or not 0 <= value <= COORDINATE_MAX:
        raise DesktopActionError(
            f"{name}={value} is not a coordinate: both axes run from 0 to {COORDINATE_MAX} whatever the size "
            "of the screen ((0, 0) top-left, (999, 999) bottom-right). Do not use pixels of the screenshot."
        )
    return int(value)


def _number(arguments: dict[str, Any], name: str, default: float, low: float, high: float) -> float:
    value = arguments.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopActionError(f"{name} must be a number")
    return max(low, min(float(value), high))


def parse_keys(raw: Any, known: Any = None) -> list[str]:
    """Return the pyautogui names of a key or of a combination joined with ``+``.

    ``known`` is the set of names the backend accepts; a name outside it is
    refused, since pyautogui silently ignores keys it does not know.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise DesktopActionError("keys is required for this action, for example enter or ctrl+c")
    text = raw.strip()
    parts: list[str] = []
    # "+" joins; a "+" that is itself the key comes last ("ctrl++") or stands alone.
    while text:
        if text == "+":
            parts.append("+")
            break
        head, separator, rest = text.partition("+")
        parts.append(head.strip())
        if separator and not rest:
            raise DesktopActionError(f"keys {raw!r} ends with a + that joins nothing")
        text = rest
    names = []
    for part in parts:
        name = part if len(part) == 1 else part.lower()
        name = _KEY_ALIASES.get(name, name)
        if not name:
            raise DesktopActionError(f"keys {raw!r} has an empty part")
        if known is not None and name not in known:
            if name.lower() not in known:
                raise DesktopActionError(
                    f"{part!r} is not a key name. Use names such as enter, tab, esc, space, backspace, delete, "
                    "up, down, left, right, home, end, pageup, pagedown, f1 to f12, ctrl, alt, shift, win, "
                    "or a single character."
                )
            name = name.lower()
        names.append(name)
    return names


class Desktop:
    """Carries out single actions on the desktop. Blocking: call ``act`` in a worker thread."""

    def __init__(self, *, backend: Any = None, capture: Any = None, settle_seconds: float = SETTLE_SECONDS) -> None:
        self._backend = backend
        self._capture = capture if capture is not None else capture_frame
        self._settle_seconds = settle_seconds
        # Shared by every call, so what one call left held the next can let go.
        self._held_keys: list[str] = []
        self._held_buttons: list[str] = []

    def backend(self) -> Any:
        return self._backend if self._backend is not None else load_pyautogui()

    def available(self) -> tuple[bool, str]:
        """Return whether input can be sent, and if not, the reason code."""
        if self.backend() is None:
            return False, pyautogui_unavailable_reason()
        return True, ""

    def _input(self, cancel_event: threading.Event | None) -> ScaledInput:
        backend = self.backend()
        if backend is None:
            raise DesktopActionError(f"the keyboard and the mouse cannot be used here ({pyautogui_unavailable_reason()})")
        width, height = backend.size()
        return ScaledInput(
            backend,
            int(width),
            int(height),
            cancel_event=cancel_event,
            held_keys=self._held_keys,
            held_buttons=self._held_buttons,
        )

    def release_held(self) -> bool:
        """Let go of what an earlier call left held; True when nothing is left.

        Not while a call has the keyboard and the mouse: it would let go of
        a key that call is holding down, and both would switch pyautogui's
        fail-safe off and on again, which can leave it off for good (the
        pointer in a corner is the one brake the user has). It waits a
        moment for the call; if the call is still at it, False comes back
        and what is held is let go of by that call, or by the next.
        """
        if not EXECUTION_LOCK.acquire(timeout=RELEASE_WAIT_SECONDS):
            return False
        try:
            return self._release_held()
        finally:
            EXECUTION_LOCK.release()

    def _release_held(self) -> bool:
        """As ``release_held``, for the thread that has ``EXECUTION_LOCK``."""
        if not self._held_keys and not self._held_buttons:
            return True
        backend = self.backend()
        if backend is None:
            return False
        return ScaledInput(backend, held_keys=self._held_keys, held_buttons=self._held_buttons).release_held()

    def screenshot(self, cancel_event: threading.Event | None = None) -> tuple[bytes, tuple[int, int]]:
        """Return the screen as JPEG bytes within the size limits, and the size of that picture."""
        from PIL import Image

        from utils.screenshot_utils import compress_screenshot

        frame = self._capture(cancel_event)
        encoded = fit_jpeg(compress_screenshot(frame, target_h=SCREENSHOT_HEIGHT), SCREENSHOT_MAX_BYTES)
        with Image.open(BytesIO(encoded)) as image:
            size = image.size
        return encoded, size

    def act(self, action: str, arguments: dict[str, Any], cancel_event: threading.Event | None = None) -> DesktopResult:
        """Carry out one action and return what happened with a screenshot taken afterwards.

        Raises ``DesktopActionError`` for an action that cannot be carried
        out as asked and ``DesktopStopped`` when the call was stopped.
        """
        if action not in ACTIONS:
            raise DesktopActionError(f"unknown action {action!r}; the actions are: {', '.join(ACTIONS)}")
        if not EXECUTION_LOCK.acquire(blocking=False):
            raise DesktopActionError("the keyboard and the mouse are busy with an earlier call; try again in a moment")
        try:
            try:
                gui = self._input(cancel_event)
                if not gui.release_held():
                    raise DesktopActionError("keys or buttons left held by an earlier call could not be released")
                done = self._perform(gui, action, arguments)
                if action != "screenshot":
                    gui.settle_for(self._settle_seconds)
                encoded, (width, height) = self.screenshot(cancel_event)
            except InterruptedError as exc:
                raise DesktopStopped("stopped") from exc
            finally:
                if self._held_keys or self._held_buttons:
                    self._release_held()
        finally:
            EXECUTION_LOCK.release()
        return DesktopResult(
            text=(
                f"{done} Screenshot after it: {width}x{height} px. Coordinates are 0 to {COORDINATE_MAX} "
                "on both axes, not pixels."
            ),
            screenshot=encoded,
        )

    def _perform(self, gui: ScaledInput, action: str, arguments: dict[str, Any]) -> str:
        if action == "screenshot":
            return "Looked at the screen."
        if action == "wait":
            seconds = _number(arguments, "seconds", WAIT_DEFAULT_SECONDS, 0.0, WAIT_MAX_SECONDS)
            gui.settle_for(seconds)
            return f"Waited {seconds:g} s."
        if action == "click":
            x, y = _coordinate(arguments, "x"), _coordinate(arguments, "y")
            button = arguments.get("button") or "left"
            if button not in BUTTONS:
                raise DesktopActionError(f"button must be one of: {', '.join(BUTTONS)}")
            clicks = arguments.get("clicks") or 1
            if isinstance(clicks, bool) or clicks not in (1, 2, 3):
                raise DesktopActionError("clicks must be 1, 2 or 3")
            gui.click(x, y, button=button, clicks=int(clicks))
            kind = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}[int(clicks)]
            return f"{kind} the {button} button at ({x}, {y})."
        if action == "move":
            x, y = _coordinate(arguments, "x"), _coordinate(arguments, "y")
            gui.move(x, y)
            return f"Moved the pointer to ({x}, {y})."
        if action == "drag":
            x, y = _coordinate(arguments, "x"), _coordinate(arguments, "y")
            to_x, to_y = _coordinate(arguments, "to_x"), _coordinate(arguments, "to_y")
            gui.drag(x, y, to_x, to_y)
            return f"Dragged from ({x}, {y}) to ({to_x}, {to_y})."
        if action == "scroll":
            direction = arguments.get("direction") or "down"
            if direction not in DIRECTIONS:
                raise DesktopActionError(f"direction must be one of: {', '.join(DIRECTIONS)}")
            notches = int(_number(arguments, "amount", SCROLL_DEFAULT_NOTCHES, 1, SCROLL_MAX_NOTCHES))
            x, y = _coordinate(arguments, "x", required=False), _coordinate(arguments, "y", required=False)
            if (x is None) != (y is None):
                raise DesktopActionError("give both x and y, or neither to scroll where the pointer is")
            gui.scroll(direction, notches, x, y)
            where = f" at ({x}, {y})" if x is not None else ""
            return f"Scrolled {direction} {notches} notch(es){where}."
        if action == "type":
            text = arguments.get("text")
            if not isinstance(text, str) or not text:
                raise DesktopActionError("text is required for this action")
            if len(text) > TYPE_MAX_CHARACTERS:
                raise DesktopActionError(f"text is longer than {TYPE_MAX_CHARACTERS} characters; type it in parts")
            gui.write(text)
            return f"Typed {len(text)} character(s)."
        # action == "key"
        known = getattr(gui._backend, "KEYBOARD_KEYS", None)
        keys = parse_keys(arguments.get("keys"), set(known) if known else None)
        hold = _number(arguments, "hold_seconds", 0.0, 0.0, HOLD_MAX_SECONDS)
        gui.press(keys, hold)
        held = f" and held it {hold:g} s" if hold > 0 else ""
        return f"Pressed {'+'.join(keys)}{held}."

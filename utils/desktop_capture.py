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

"""Cross-desktop screenshot capture with isolated system-tool processes.

PyAutoGUI delegates Linux capture to PyScreeze/Pillow.  That stack selects an
installed ``gnome-screenshot`` before ``spectacle``, even on KDE, and inherits
the frozen application's native-library search path.  A system executable can
therefore either use the wrong desktop backend or load private libraries from
the N.E.K.O bundle.

This module owns backend selection and keeps system screenshot helpers outside
the application's private loader environment.  It intentionally returns a PIL
image so existing ComputerUse, proactive vision, and HTTP fallback callers keep
one common contract.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence

from PIL import Image


DEFAULT_CAPTURE_TIMEOUT_SECONDS = 15.0
_PATH_ENV_KEYS = ("PATH", "LD_LIBRARY_PATH", "XDG_DATA_DIRS")
_WAYLAND_WLROOTS_DESKTOPS = {
    "hyprland",
    "niri",
    "river",
    "sway",
    "wayfire",
    "wlroots",
}


class DesktopCaptureError(RuntimeError):
    """Raised when no desktop screenshot backend can return a valid image."""


def _is_inside(path_value: str, root_value: str) -> bool:
    try:
        path = Path(path_value).expanduser().resolve(strict=False)
        root = Path(root_value).expanduser().resolve(strict=False)
        return path == root or root in path.parents
    except (OSError, RuntimeError, ValueError):
        return False


def _runtime_roots(env: Mapping[str, str]) -> tuple[str, ...]:
    roots: list[str] = []
    app_dir = str(env.get("APPDIR", "")).strip()
    if app_dir and os.path.isabs(app_dir):
        roots.append(app_dir)

    executable = str(getattr(sys, "executable", "") or "")
    is_frozen = bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()
    if is_frozen and executable and os.path.isabs(executable):
        roots.append(os.path.dirname(executable))

    meipass = str(getattr(sys, "_MEIPASS", "") or "")
    if meipass and os.path.isabs(meipass):
        roots.append(meipass)

    # Source mode resolves to the repository root; Nuitka resolves to the
    # frozen ``resources/bin`` directory that contains private native libs.
    roots.append(str(Path(__file__).resolve().parents[1]))
    return tuple(dict.fromkeys(roots))


def build_system_tool_env(
    env: Mapping[str, str] | None = None,
    *,
    runtime_roots: Sequence[str] | None = None,
) -> dict[str, str]:
    """Return an environment safe for host desktop executables.

    Only application-owned path entries are removed.  Display, Wayland,
    D-Bus, locale, input-method, and unrelated user paths remain intact.
    """

    cleaned = dict(os.environ if env is None else env)
    roots = tuple(runtime_roots or _runtime_roots(cleaned))

    for key in _PATH_ENV_KEYS:
        value = cleaned.get(key)
        if not value:
            continue
        entries = [entry for entry in value.split(os.pathsep) if entry]
        kept = [
            entry
            for entry in entries
            if not (
                os.path.isabs(entry)
                and any(_is_inside(entry, root) for root in roots)
            )
        ]
        if kept:
            cleaned[key] = os.pathsep.join(kept)
        else:
            cleaned.pop(key, None)

    schema_dir = cleaned.get("GSETTINGS_SCHEMA_DIR")
    if schema_dir and os.path.isabs(schema_dir):
        if any(_is_inside(schema_dir, root) for root in roots):
            cleaned.pop("GSETTINGS_SCHEMA_DIR", None)

    preload = cleaned.get("LD_PRELOAD")
    if preload:
        preload_entries = [entry for entry in re.split(r"[\s:]+", preload) if entry]
        kept_preloads = [
            entry
            for entry in preload_entries
            if not (
                os.path.isabs(entry)
                and any(_is_inside(entry, root) for root in roots)
            )
        ]
        if kept_preloads:
            cleaned["LD_PRELOAD"] = " ".join(kept_preloads)
        else:
            cleaned.pop("LD_PRELOAD", None)

    return cleaned


def _desktop_tokens(env: Mapping[str, str]) -> set[str]:
    raw = ":".join(
        (
            str(env.get("XDG_CURRENT_DESKTOP", "")),
            str(env.get("XDG_SESSION_DESKTOP", "")),
            str(env.get("DESKTOP_SESSION", "")),
        )
    ).lower()
    return {token for token in re.split(r"[^a-z0-9_-]+", raw) if token}


def _wayland_helper(
    env: Mapping[str, str],
    *,
    which: Callable[[str], str | None],
) -> tuple[str, list[str]]:
    desktops = _desktop_tokens(env)

    if desktops.intersection({"kde", "plasma"}):
        executable = which("spectacle")
        if executable:
            return "spectacle", [executable, "-n", "-b", "-f", "-o"]
        raise DesktopCaptureError(
            "KDE Wayland screenshot requires the system 'spectacle' command"
        )

    if desktops.intersection(_WAYLAND_WLROOTS_DESKTOPS):
        executable = which("grim")
        if executable:
            return "grim", [executable]
        raise DesktopCaptureError(
            "wlroots Wayland screenshot requires the system 'grim' command"
        )

    if "gnome" in desktops:
        executable = which("gnome-screenshot")
        if executable:
            return "gnome-screenshot", [executable, "-f"]
        raise DesktopCaptureError(
            "GNOME Wayland screenshot requires the system 'gnome-screenshot' command"
        )

    # Unknown Wayland desktops: prefer protocol-specific helpers before the
    # GNOME-only helper.  This is a fallback for incomplete desktop metadata,
    # not a replacement for the explicit desktop routes above.
    for backend, command in (
        ("grim", [which("grim")]),
        ("spectacle", [which("spectacle"), "-n", "-b", "-f", "-o"]),
        ("gnome-screenshot", [which("gnome-screenshot"), "-f"]),
    ):
        if command[0]:
            return backend, [str(part) for part in command]
    raise DesktopCaptureError("no supported Wayland screenshot helper is installed")


def native_wayland_capture_available(
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> bool:
    """Whether this host has a supported Wayland screenshot fallback.

    This is a cheap preflight, not a guarantee that a later capture succeeds.
    ComputerUse reports the actual screenshot error if the helper fails.
    """

    capture_env = dict(os.environ if env is None else env)
    if not (capture_env.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
            or capture_env.get("WAYLAND_DISPLAY")):
        return False
    system_env = build_system_tool_env(capture_env)
    lookup = which or (lambda command: shutil.which(
        command, path=system_env.get("PATH", os.defpath)
    ))
    try:
        _wayland_helper(capture_env, which=lookup)
    except DesktopCaptureError:
        return False
    return True


def _capture_with_system_tool(
    backend: str,
    command_prefix: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: float,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="neko-screenshot-") as temp_dir:
        output_path = os.path.join(temp_dir, "capture.png")
        command = [*command_prefix, output_path]
        try:
            completed = run(
                command,
                capture_output=True,
                check=False,
                env=build_system_tool_env(env),
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise DesktopCaptureError(
                f"{backend} screenshot timed out after {timeout:g}s"
            ) from exc
        except OSError as exc:
            raise DesktopCaptureError(f"failed to start {backend}: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            if len(detail) > 500:
                detail = detail[-500:]
            suffix = f": {detail}" if detail else ""
            raise DesktopCaptureError(
                f"{backend} screenshot exited with {completed.returncode}{suffix}"
            )
        if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
            raise DesktopCaptureError(f"{backend} did not create a screenshot")

        try:
            with Image.open(output_path) as image:
                image.load()
                return image.copy()
        except Exception as exc:
            raise DesktopCaptureError(
                f"{backend} created an invalid screenshot: {type(exc).__name__}"
            ) from exc


def _capture_x11_with_mss() -> Image.Image:
    import mss

    with mss.mss() as capture:
        monitors = capture.monitors
        if not monitors:
            raise DesktopCaptureError("mss did not report any X11 monitors")
        screenshot = capture.grab(monitors[0])
        return Image.frombytes("RGB", screenshot.size, screenshot.rgb)


def capture_desktop_screenshot(
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    timeout: float = DEFAULT_CAPTURE_TIMEOUT_SECONDS,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Image.Image:
    """Capture the current desktop and return a fully loaded PIL image."""

    capture_env = dict(os.environ if env is None else env)
    current_platform = platform_name or sys.platform
    if not current_platform.startswith("linux"):
        import pyautogui

        return pyautogui.screenshot()

    session_type = str(capture_env.get("XDG_SESSION_TYPE", "")).strip().lower()
    is_wayland = session_type == "wayland" or bool(capture_env.get("WAYLAND_DISPLAY"))
    if is_wayland:
        system_env = build_system_tool_env(capture_env)
        lookup = which or (lambda command: shutil.which(
            command, path=system_env.get("PATH", os.defpath)
        ))
        backend, command_prefix = _wayland_helper(capture_env, which=lookup)
        return _capture_with_system_tool(
            backend,
            command_prefix,
            env=capture_env,
            timeout=timeout,
            run=run,
        )

    try:
        return _capture_x11_with_mss()
    except Exception as mss_error:
        try:
            import pyautogui

            return pyautogui.screenshot()
        except Exception as pyautogui_error:
            raise DesktopCaptureError(
                "X11 screenshot failed with both mss and pyautogui "
                f"({type(mss_error).__name__}, {type(pyautogui_error).__name__})"
            ) from pyautogui_error

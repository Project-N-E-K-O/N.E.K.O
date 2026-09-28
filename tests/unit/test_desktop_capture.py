from __future__ import annotations

import os
import subprocess
import sys

from PIL import Image
import pytest

from utils import desktop_capture


@pytest.mark.unit
def test_source_interpreter_directory_is_not_a_private_runtime_root(monkeypatch, tmp_path):
    interpreter = tmp_path / "python3"
    monkeypatch.setattr(desktop_capture.sys, "executable", str(interpreter))
    monkeypatch.delattr(desktop_capture.sys, "frozen", raising=False)

    roots = desktop_capture._runtime_roots({})

    assert os.path.dirname(str(interpreter)) not in roots


def _successful_capture(calls: list[dict]):
    def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        Image.new("RGB", (320, 180), (20, 40, 60)).save(command[-1], format="PNG")
        return subprocess.CompletedProcess(command, 0, "", "")

    return _run


@pytest.mark.unit
def test_native_wayland_capture_preflight_uses_desktop_helper_without_taking_screenshot():
    env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE", "PATH": "/usr/bin"}
    assert desktop_capture.native_wayland_capture_available(
        env=env, which=lambda name: "/usr/bin/spectacle" if name == "spectacle" else None
    )
    assert not desktop_capture.native_wayland_capture_available(env=env, which=lambda _name: None)
    assert not desktop_capture.native_wayland_capture_available(
        env={"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"},
        which=lambda _name: "/usr/bin/spectacle",
    )


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PATH and AppImage paths are Linux-only")
def test_kde_wayland_prefers_spectacle_even_when_gnome_screenshot_exists():
    calls: list[dict] = []
    app_dir = "/tmp/.mount_N.E.K.Oabc"
    env = {
        "APPDIR": app_dir,
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "PATH": f"{app_dir}/usr/bin:/usr/bin",
        "LD_LIBRARY_PATH": f"{app_dir}/resources/bin:{app_dir}/usr/lib:/usr/lib",
        "LD_PRELOAD": f"{app_dir}/resources/bin/libprivate.so /usr/lib/libhost.so",
        "XDG_DATA_DIRS": f"{app_dir}/usr/share:/usr/share",
        "GSETTINGS_SCHEMA_DIR": f"{app_dir}/usr/share/glib-2.0/schemas",
    }
    executables = {
        "spectacle": "/usr/bin/spectacle",
        "gnome-screenshot": "/usr/bin/gnome-screenshot",
    }

    image = desktop_capture.capture_desktop_screenshot(
        env=env,
        platform_name="linux",
        which=executables.get,
        run=_successful_capture(calls),
    )

    assert image.size == (320, 180)
    assert calls[0]["command"][:-1] == [
        "/usr/bin/spectacle",
        "-n",
        "-b",
        "-f",
        "-o",
    ]
    child_env = calls[0]["env"]
    assert child_env["PATH"] == "/usr/bin"
    assert child_env["LD_LIBRARY_PATH"] == "/usr/lib"
    assert child_env["LD_PRELOAD"] == "/usr/lib/libhost.so"
    assert child_env["XDG_DATA_DIRS"] == "/usr/share"
    assert "GSETTINGS_SCHEMA_DIR" not in child_env
    assert child_env["WAYLAND_DISPLAY"] == "wayland-0"
    assert child_env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX AppImage PATH is Linux-only")
def test_wayland_tool_lookup_uses_the_cleaned_host_path(monkeypatch):
    app_dir = "/tmp/.mount_N.E.K.Oabc"
    calls: list[dict] = []

    def _which(command, *, path):
        calls.append({"lookup": command, "path": path})
        return "/usr/bin/spectacle" if command == "spectacle" else None

    monkeypatch.setattr(desktop_capture.shutil, "which", _which)
    desktop_capture.capture_desktop_screenshot(
        env={
            "APPDIR": app_dir,
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "KDE",
            "PATH": f"{app_dir}/usr/bin:/usr/bin",
        },
        platform_name="linux",
        run=_successful_capture(calls),
    )

    assert calls[0] == {"lookup": "spectacle", "path": "/usr/bin"}
    assert calls[1]["env"]["PATH"] == "/usr/bin"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("desktop", "executables", "expected_prefix"),
    [
        (
            "GNOME",
            {"gnome-screenshot": "/usr/bin/gnome-screenshot"},
            ["/usr/bin/gnome-screenshot", "-f"],
        ),
        (
            "niri",
            {"grim": "/usr/bin/grim"},
            ["/usr/bin/grim"],
        ),
    ],
)
def test_wayland_desktops_select_their_native_helper(
    desktop,
    executables,
    expected_prefix,
):
    calls: list[dict] = []

    image = desktop_capture.capture_desktop_screenshot(
        env={
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": desktop,
        },
        platform_name="linux",
        which=executables.get,
        run=_successful_capture(calls),
    )

    assert image.size == (320, 180)
    assert calls[0]["command"][:-1] == expected_prefix


@pytest.mark.unit
def test_kde_wayland_does_not_fall_back_to_gnome_screenshot():
    with pytest.raises(
        desktop_capture.DesktopCaptureError,
        match="requires the system 'spectacle' command",
    ):
        desktop_capture.capture_desktop_screenshot(
            env={
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "KDE",
            },
            platform_name="linux",
            which=lambda name: (
                "/usr/bin/gnome-screenshot" if name == "gnome-screenshot" else None
            ),
        )


@pytest.mark.unit
def test_system_screenshot_timeout_is_reported_without_opening_temp_file():
    def _timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    with pytest.raises(desktop_capture.DesktopCaptureError, match="timed out after 2s"):
        desktop_capture.capture_desktop_screenshot(
            env={
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "KDE",
            },
            platform_name="linux",
            timeout=2,
            which=lambda name: "/usr/bin/spectacle" if name == "spectacle" else None,
            run=_timeout,
        )


@pytest.mark.unit
def test_system_screenshot_rejects_a_non_image_output():
    def _invalid_capture(command, **_kwargs):
        with open(command[-1], "wb") as output:
            output.write(b"not a png")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(
        desktop_capture.DesktopCaptureError,
        match="spectacle created an invalid screenshot",
    ):
        desktop_capture.capture_desktop_screenshot(
            env={
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "KDE",
            },
            platform_name="linux",
            which=lambda name: "/usr/bin/spectacle" if name == "spectacle" else None,
            run=_invalid_capture,
        )


@pytest.mark.unit
def test_x11_prefers_mss(monkeypatch):
    expected = Image.new("RGB", (640, 480))
    monkeypatch.setattr(desktop_capture, "_capture_x11_with_mss", lambda: expected)

    actual = desktop_capture.capture_desktop_screenshot(
        env={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
        platform_name="linux",
    )

    assert actual is expected

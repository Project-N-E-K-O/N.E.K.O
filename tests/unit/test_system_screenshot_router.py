from unittest.mock import patch
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from main_routers import system_router as system_router_module
from main_routers.shared_state import init_shared_state


SCREENSHOT_ENDPOINT = "/api/screenshot"
INTERACTIVE_SCREENSHOT_ENDPOINT = "/api/screenshot/interactive"


@pytest.fixture(autouse=True)
def _reset_shared_state_after_test(monkeypatch):
    monkeypatch.setattr(system_router_module, "AUTOSTART_CSRF_TOKEN", "test-csrf-token")
    yield
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=None,
        logger=None,
    )


def _build_client():
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=None,
        logger=None,
    )
    app = FastAPI()
    app.include_router(system_router_module.router)
    return TestClient(app)


def _local_headers():
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": "test-csrf-token",
    }


@pytest.mark.unit
def test_is_loopback_request_accepts_ipv4_mapped_ipv6_loopback():
    request = SimpleNamespace(client=SimpleNamespace(host="::ffff:127.0.0.1"))
    assert system_router_module._is_loopback_request(request) is True


@pytest.mark.unit
@pytest.mark.parametrize("env_name", ["NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"])
@pytest.mark.parametrize("env_value", ["1", "true", "TRUE", "yes", "on"])
def test_is_remote_backend_deployment_truthy(monkeypatch, env_name, env_value):
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.setenv(env_name, env_value)
    assert system_router_module._is_remote_backend_deployment() is True


@pytest.mark.unit
@pytest.mark.parametrize("env_value", ["", "0", "false", "no", "off", "anything-else"])
def test_is_remote_backend_deployment_falsy(monkeypatch, env_value):
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    if env_value:
        monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", env_value)
    assert system_router_module._is_remote_backend_deployment() is False


@pytest.mark.unit
def test_backend_screenshot_blocked_when_backend_marked_remote(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")

    with _build_client() as client:
        response = client.post(SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 501
    payload = response.json()
    assert payload["success"] is False
    assert "remote" in payload["error"].lower()
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"


@pytest.mark.unit
def test_interactive_screenshot_blocked_when_backend_marked_remote(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")
    monkeypatch.setenv("ACTIVITY_TRACKER_REMOTE", "true")

    def _should_not_run(_path):
        raise AssertionError("interactive screenshot must not run when backend is remote")

    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        _should_not_run,
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 501
    payload = response.json()
    assert payload["success"] is False
    assert "remote" in payload["error"].lower()


@pytest.mark.unit
def test_backend_screenshot_rejects_missing_csrf_headers():
    with _build_client() as client:
        response = client.post(SCREENSHOT_ENDPOINT)

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_code"] == "csrf_validation_failed"
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"


@pytest.mark.unit
def test_interactive_screenshot_rejects_non_loopback_requests():
    with _build_client() as client, patch.object(
        system_router_module,
        "_is_loopback_request",
        return_value=False,
    ):
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 403
    assert response.json()["error"] == "only available from localhost"


@pytest.mark.unit
def test_interactive_screenshot_skips_csrf_when_no_origin_or_referer(monkeypatch):
    """纯服务端 loopback 调用（无 Origin/Referer）不需要 CSRF 头。"""
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        lambda _path: (1, ""),
    )

    with _build_client() as client:
        # No Origin / Referer / CSRF — should pass CSRF gate and reach the runner.
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is True


@pytest.mark.unit
def test_interactive_screenshot_requires_csrf_when_origin_present(monkeypatch):
    """带 Origin 的浏览器请求必须通过 CSRF/origin 校验，否则 localhost CSRF。"""
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")

    def _should_not_run(_path):
        raise AssertionError("interactive screenshot must not run when CSRF check fails")

    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        _should_not_run,
    )

    with _build_client() as client:
        # 模拟跨站页面：有 Origin（浏览器自动塞），但既无 CSRF token，origin 也不在白名单里。
        response = client.post(
            INTERACTIVE_SCREENSHOT_ENDPOINT,
            headers={"Origin": "https://evil.example"},
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_code"] == "csrf_validation_failed"


@pytest.mark.unit
@pytest.mark.parametrize(
    "header_name, header_value",
    [
        ("Origin", "null"),       # sandboxed iframe / file:// / data:
        ("Origin", "ftp://x"),    # 非 http(s) scheme，归一化会变空
        ("Referer", "null"),
    ],
)
def test_interactive_screenshot_blocks_browser_requests_with_unparseable_origin(
    monkeypatch, header_name, header_value
):
    """`Origin: null` 等归一化后为空的浏览器请求必须仍走 CSRF 校验，不能旁路。"""
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")

    def _should_not_run(_path):
        raise AssertionError(
            "interactive screenshot must not run when Origin/Referer header is "
            "present but unparseable — that's a sandboxed iframe / file:// CSRF vector"
        )

    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        _should_not_run,
    )

    with _build_client() as client:
        response = client.post(
            INTERACTIVE_SCREENSHOT_ENDPOINT,
            headers={header_name: header_value},
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_code"] == "csrf_validation_failed"


@pytest.mark.unit
def test_interactive_screenshot_passes_with_valid_csrf_and_origin(monkeypatch):
    """合法本地前端：带 Origin + CSRF token，应当通过校验进入 runner。"""
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        lambda _path: (1, ""),
    )

    with _build_client() as client:
        response = client.post(
            INTERACTIVE_SCREENSHOT_ENDPOINT,
            headers=_local_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is True


@pytest.mark.unit
def test_interactive_screenshot_returns_unsupported_on_non_macos(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "linux")

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 501
    assert "only supported on macOS and Windows" in response.json()["error"]


@pytest.mark.unit
def test_interactive_screenshot_returns_canceled_when_user_aborts(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        lambda _path: (1, ""),
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is True


@pytest.mark.unit
def test_interactive_screenshot_treats_macos_cancel_with_stderr_as_canceled(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        lambda _path: (1, "User canceled."),
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is True


@pytest.mark.unit
def test_interactive_screenshot_returns_failure_for_runtime_errors(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "win32")
    monkeypatch.setattr(
        system_router_module,
        "_run_windows_interactive_screenshot",
        lambda _path: (2, "boom"),
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 500
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is False
    assert payload["error"] == "boom"


@pytest.mark.unit
def test_interactive_screenshot_returns_cropped_image_data(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")

    def _fake_run(output_path: str):
        Image.new("RGB", (64, 48), (32, 128, 224)).save(output_path, format="PNG")
        return 0, ""

    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        _fake_run,
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["interactive"] is True
    assert payload["size"] > 0
    assert payload["data"].startswith("data:image/jpeg;base64,")
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"


@pytest.mark.unit
def test_interactive_screenshot_returns_canceled_when_windows_user_aborts(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "win32")
    monkeypatch.setattr(
        system_router_module,
        "_run_windows_interactive_screenshot",
        lambda _path: (1, ""),
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is True


@pytest.mark.unit
def test_interactive_screenshot_returns_windows_cropped_image_data(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "win32")

    def _fake_run(output_path: str):
        Image.new("RGB", (90, 60), (220, 120, 40)).save(output_path, format="PNG")
        return 0, ""

    monkeypatch.setattr(
        system_router_module,
        "_run_windows_interactive_screenshot",
        _fake_run,
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT, headers=_local_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["interactive"] is True
    assert payload["size"] > 0
    assert payload["data"].startswith("data:image/jpeg;base64,")

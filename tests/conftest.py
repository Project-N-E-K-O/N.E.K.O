import asyncio
import asyncio.runners
import asyncio.coroutines
import nest_asyncio

nest_asyncio.apply()

_orig_asyncio_run = asyncio.run
_orig_runner_run = asyncio.runners.Runner.run

def _nested_runner_run(self, coro, *, context=None):
    """Allow Runner.run() when an event loop is already running (Playwright greenlet)."""
    if not asyncio.coroutines.iscoroutine(coro):
        raise ValueError(f"a coroutine was expected, got {coro!r}")
    self._lazy_init()
    nest_asyncio._patch_loop(self._loop)
    task = self._loop.create_task(coro, context=context)
    try:
        return self._loop.run_until_complete(task)
    finally:
        if not task.done():
            task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                self._loop.run_until_complete(task)


def _compat_asyncio_run(main, *, debug=None, loop_factory=None):
    """Preserve Python 3.12's loop_factory support after nest_asyncio patches asyncio.run."""
    if loop_factory is None:
        return _orig_asyncio_run(main, debug=debug)

    with asyncio.runners.Runner(debug=debug, loop_factory=loop_factory) as runner:
        return runner.run(main)


asyncio.runners.Runner.run = _nested_runner_run
asyncio.run = _compat_asyncio_run

import pytest
import os
import threading
import time
import uvicorn
import json
import logging
import socket
from unittest.mock import patch
from pathlib import Path

# Add project root to sys.path if needed, or rely on pytest pythonpath
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tests.utils.llm_judger import LLMJudger

logger = logging.getLogger(__name__)

SYSTEM_CHROME_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_RUNTIME_TEST_PORTS: dict[str, int] = {}
_RUNTIME_TEST_PORT_RETRY_LIMIT = 10

# Map camelCase keys in api_keys.json to UPPER_SNAKE_CASE env vars expected by ConfigManager
KEY_MAPPING = {
    "assistApiKeyQwen": "ASSIST_API_KEY_QWEN",
    "assistApiKeyOpenai": "ASSIST_API_KEY_OPENAI",
    "assistApiKeyGlm": "ASSIST_API_KEY_GLM",
    "assistApiKeyStep": "ASSIST_API_KEY_STEP",
    "assistApiKeySilicon": "ASSIST_API_KEY_SILICON",
    "assistApiKeyGemini": "ASSIST_API_KEY_GEMINI",
    "assistApiKeyKimi": "ASSIST_API_KEY_KIMI"
}

def pytest_addoption(parser):
    parser.addoption(
        "--run-manual",
        action="store_true",
        default=False,
        help="run manual integration tests (real API calls, screen/browser control)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "manual: requires human supervision and real API/screen/browser")
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "frontend: frontend integration tests")

    # Auto-install Playwright browsers if not already present.
    _ensure_playwright_browsers()


def _ensure_playwright_browsers():
    """Try to install Playwright chromium if missing. Never blocks the session.

    When the default Playwright CDN (cdn.playwright.dev) is unreachable or
    returns an error (e.g. 403), we fall back to Google's public
    Chrome-for-Testing storage bucket as an alternative download mirror by
    setting ``PLAYWRIGHT_DOWNLOAD_HOST``.
    """
    import subprocess

    # ── 1. Probe: can we already launch chromium? ──────────────────────
    try:
        probe = subprocess.run(
            [sys.executable, "-c",
             ("from playwright.sync_api import sync_playwright;"
              "p=sync_playwright().start(); b=p.chromium.launch(headless=True);"
              "b.close(); p.stop()")],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            return  # Already installed – nothing to do.
    except Exception as exc:
        logger.debug("Playwright probe failed, will attempt install: %s", exc)

    # ── 2. Attempt installation ────────────────────────────────────────
    logger.info("Playwright chromium not found, attempting install...")

    # Google's public bucket mirrors the same paths that Playwright expects
    # under cdn.playwright.dev.  We use it as a fallback when the default
    # CDN is blocked or unavailable (common in CI / sandboxed environments).
    _FALLBACK_MIRROR = "https://storage.googleapis.com/chrome-for-testing-public"

    install_commands = [
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
        [sys.executable, "-m", "playwright", "install", "chromium"],
    ]

    # Try each command twice: first with the default CDN, then with the
    # Google mirror.  We iterate (default-env, mirror-env) x (commands).
    env_variants = [
        None,           # default environment (Playwright's own CDN)
        {"PLAYWRIGHT_DOWNLOAD_HOST": _FALLBACK_MIRROR},
    ]

    for extra_env in env_variants:
        for cmd in install_commands:
            try:
                run_env = os.environ.copy()
                if extra_env:
                    run_env.update(extra_env)
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=300, env=run_env,
                )
                if result.returncode == 0:
                    logger.info("Playwright chromium installed successfully.")
                    return
                else:
                    logger.debug(
                        "Install attempt failed (rc=%d): %s\nstderr: %s",
                        result.returncode, " ".join(cmd), result.stderr[-500:] if result.stderr else "",
                    )
            except subprocess.TimeoutExpired:
                logger.warning("Playwright install timed out for command: %s", " ".join(cmd))
            except Exception as exc:
                logger.debug("Playwright install error: %s", exc)

    logger.warning(
        "Could not auto-install Playwright browsers. "
        "Frontend/e2e tests will likely fail. "
        "Run manually: python -m playwright install chromium --with-deps"
    )

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-manual", default=False):
        skip_manual = pytest.mark.skip(reason="needs --run-manual to run")
        for item in items:
            if "manual" in item.keywords:
                item.add_marker(skip_manual)


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _set_runtime_test_port(port_name: str, port_value: int) -> None:
    os.environ[f"NEKO_{port_name}"] = str(port_value)

    try:
        import config as config_module
    except (ModuleNotFoundError, ImportError) as exc:
        if getattr(exc, "name", None) == "config":
            return
        raise

    setattr(config_module, port_name, port_value)


def _resolve_runtime_test_port(port_name: str) -> int:
    env_name = f"NEKO_{port_name}"
    raw_value = os.environ.get(env_name)
    if raw_value:
        try:
            port_value = int(raw_value)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r", env_name, raw_value)
        else:
            if 1 <= port_value <= 65535:
                return port_value
            # 0 会让 uvicorn 随机绑端口但 readiness probe 仍连 0，测试必卡死；
            # 负数 / >65535 直接非法。一律视为未设置，重新分配。
            logger.warning(
                "Ignoring out-of-range %s=%r (must be 1..65535)",
                env_name,
                raw_value,
            )
    return _find_free_local_port()


def _initialize_runtime_test_ports() -> None:
    if _RUNTIME_TEST_PORTS:
        for port_name, port_value in _RUNTIME_TEST_PORTS.items():
            _set_runtime_test_port(port_name, port_value)
        return

    for port_name in ("MEMORY_SERVER_PORT", "MAIN_SERVER_PORT"):
        port_value = _resolve_runtime_test_port(port_name)
        if port_value in _RUNTIME_TEST_PORTS.values():
            logger.warning(
                "Resolved duplicate runtime test port %s=%s; selecting a new port",
                port_name,
                port_value,
            )
            for attempt in range(1, _RUNTIME_TEST_PORT_RETRY_LIMIT + 1):
                fallback_port = _find_free_local_port()
                if fallback_port not in _RUNTIME_TEST_PORTS.values():
                    port_value = fallback_port
                    break
                logger.warning(
                    "Duplicate fallback runtime test port %s=%s on attempt %s/%s",
                    port_name,
                    fallback_port,
                    attempt,
                    _RUNTIME_TEST_PORT_RETRY_LIMIT,
                )
            else:
                raise RuntimeError(
                    f"Unable to allocate unique runtime test port for {port_name} "
                    f"after {_RUNTIME_TEST_PORT_RETRY_LIMIT} attempts"
                )
        _RUNTIME_TEST_PORTS[port_name] = port_value
        _set_runtime_test_port(port_name, port_value)


def _get_runtime_test_port(port_name: str) -> int:
    _initialize_runtime_test_ports()
    return _RUNTIME_TEST_PORTS[port_name]


_initialize_runtime_test_ports()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """
    Force locale to zh-CN and enable fake media streams for testing.
    """
    return {
        **browser_context_args,
        "locale": "zh-CN",
        "permissions": ["microphone", "camera"],
    }

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, browser_name):
    launch_args = {
        **browser_type_launch_args,
        "args": [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ]
    }
    if browser_name == "chromium" and SYSTEM_CHROME_PATH.exists():
        launch_args["executable_path"] = str(SYSTEM_CHROME_PATH)
    return launch_args

@pytest.fixture(scope="session", autouse=True)
def loaded_api_keys():
    """Load API keys from tests/api_keys.json and set environment variables."""
    # Find api_keys.json in tests directory relative to this conftest file
    key_file = os.path.join(os.path.dirname(__file__), 'api_keys.json')
    if not os.path.exists(key_file):
        logger.warning(f"API keys file not found at {key_file}. Integration tests may fail.")
        return {}
    
    try:
        with open(key_file, 'r', encoding='utf-8') as f:
            keys = json.load(f)
        
        # Set env vars and return the keys dict for reference
        for json_key, env_var in KEY_MAPPING.items():
            if json_key in keys and keys[json_key]:
                os.environ[env_var] = keys[json_key]
            else:
                logger.warning(f"Key {json_key} missing in api_keys.json")
                
        return keys
    except Exception as e:
        logger.error(f"Failed to load API keys: {e}")
        return {}

@pytest.fixture(scope="session")
def llm_judger():
    """Fixture providing an LLMJudger instance. Generates report at session end."""
    judger = LLMJudger()
    yield judger
    # Auto-generate report when session finishes
    report_path = judger.generate_report()
    if report_path:
        logger.info(f"Test report generated: {report_path}")

@pytest.fixture(scope="session")
def clean_user_data_dir(tmp_path_factory):
    """
    Creates a temporary user data directory for testing (Session scoped).
    Patches ConfigManager to use this directory.
    """
    # Create session temp dir
    tmp_path = tmp_path_factory.mktemp("neko_test_data")
    if not (tmp_path / "Xiao8").exists():
        (tmp_path / "Xiao8").mkdir()
    
    # Hot-patch the existing ConfigManager singleton if it exists
    # And patch any NEW instances via class patch
    from utils.config_manager import get_config_manager
    from pathlib import Path

    # Ensure we get the singleton (creating it if necessary)
    # Use 'N.E.K.O' as default app name if creating new
    cm = get_config_manager('N.E.K.O') 
    
    # Save original state
    original_docs_dir = cm.docs_dir
    original_app_docs_dir = cm.app_docs_dir
    original_config_dir = cm.config_dir
    original_memory_dir = cm.memory_dir
    original_live2d_dir = cm.live2d_dir
    original_vrm_dir = cm.vrm_dir
    original_vrm_animation_dir = cm.vrm_animation_dir
    original_workshop_dir = cm.workshop_dir
    original_chara_dir = cm.chara_dir
    original_project_config_dir = cm.project_config_dir
    original_project_memory_dir = cm.project_memory_dir

    # Overwrite with temp paths
    # We essentially re-run the path logic from __init__ but with tmp_path as docs_dir
    cm.docs_dir = Path(tmp_path)
    # Ensure app docs dir exists
    import shutil
    if cm.app_docs_dir.exists():
        new_app_docs_dir = Path(tmp_path) / "N.E.K.O"
        shutil.copytree(str(cm.app_docs_dir), str(new_app_docs_dir), dirs_exist_ok=True)
    
    cm.app_docs_dir = cm.docs_dir / "N.E.K.O"
    cm.app_docs_dir.mkdir(parents=True, exist_ok=True)
    
    cm.config_dir = cm.app_docs_dir / "config"
    cm.memory_dir = cm.app_docs_dir / "memory"
    cm.live2d_dir = cm.app_docs_dir / "live2d"
    cm.vrm_dir = cm.app_docs_dir / "vrm"
    cm.vrm_animation_dir = cm.vrm_dir / "animation"
    cm.workshop_dir = cm.app_docs_dir / "workshop"
    cm.chara_dir = cm.app_docs_dir / "character_cards"
    
    # Update project dirs to mimic app/config separation or point to temp if needed
    cm.project_config_dir = cm.config_dir
    cm.project_memory_dir = cm.memory_dir

    # Also patch the class method for any NEW instances that might be created
    patcher = patch("utils.config_manager.ConfigManager._get_documents_directory", return_value=tmp_path)
    patcher.start()
    
    try:
        yield tmp_path
    finally:
        patcher.stop()
        # Restore original state
        cm.docs_dir = original_docs_dir
        cm.app_docs_dir = original_app_docs_dir
        cm.config_dir = original_config_dir
        cm.memory_dir = original_memory_dir
        cm.live2d_dir = original_live2d_dir
        cm.vrm_dir = original_vrm_dir
        cm.vrm_animation_dir = original_vrm_animation_dir
        cm.workshop_dir = original_workshop_dir
        cm.chara_dir = original_chara_dir
        cm.project_config_dir = original_project_config_dir
        cm.project_memory_dir = original_project_memory_dir

@pytest.fixture
def mock_page(page):
    """
    Configures a Playwright page with console logging and error capture.
    """
    def log_console(msg):
        print(f"Browser Console: {msg.text}")
    
    page.on("console", log_console)
    page.on("pageerror", lambda err: print(f"Browser Error: {err}"))
    return page

@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """
    Runs a minimal mock memory server on a free local port to satisfy core.py's
    requirement to fetch contextual memory before starting a session.
    """
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    memory_port = _get_runtime_test_port("MEMORY_SERVER_PORT")

    app = FastAPI()

    @app.get("/new_dialog/{character}")
    def get_memory(character: str):
        return PlainTextResponse(f"Mock memory context for {character}.")

    config = uvicorn.Config(app, host="127.0.0.1", port=memory_port, log_level="error")
    server = uvicorn.Server(config)

    def run_server():
        server.run()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            with socket.create_connection(("127.0.0.1", memory_port), timeout=1):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
            continue
    else:
        raise RuntimeError(f"Mock memory server failed to start on {memory_port}")

    yield

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def running_server(clean_user_data_dir, mock_memory_server):
    """
    Starts the backend server in a background thread for testing.
    Waits for port to be ready.
    Depends on clean_user_data_dir to ensure config is patched BEFORE import.
    """
    test_port = _get_runtime_test_port("MAIN_SERVER_PORT")

    from main_server import app

    config = uvicorn.Config(app, host="127.0.0.1", port=test_port, log_level="error")
    server = uvicorn.Server(config)

    def run_server():
        server.run()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait for server to start
    # Simple check loop
    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            with socket.create_connection(("127.0.0.1", test_port), timeout=1):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
            continue
    else:
        raise RuntimeError("Test server failed to start")

    yield f"http://127.0.0.1:{test_port}"

    # Force-terminate uvicorn: graceful shutdown first, then force-kill
    server.should_exit = True
    thread.join(timeout=10)
    if thread.is_alive():
        logger.warning("Uvicorn server didn't stop gracefully, force-killing thread")
        import ctypes
        tid = thread.ident
        if tid is not None:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid), ctypes.py_object(SystemExit)
            )
            if res > 1:
                # If it returns > 1, we need to reset it
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
        thread.join(timeout=3)

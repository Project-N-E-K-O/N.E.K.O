import pytest
import os
import threading
import time
import uvicorn
import json
import logging
from unittest.mock import patch

# Add project root to sys.path if needed, or rely on pytest pythonpath
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tests.utils.llm_judger import LLMJudger

logger = logging.getLogger(__name__)

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

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-manual", default=False):
        skip_manual = pytest.mark.skip(reason="needs --run-manual to run")
        for item in items:
            if "manual" in item.keywords:
                item.add_marker(skip_manual)
                


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
def browser_type_launch_args(browser_type_launch_args):
    return {
        **browser_type_launch_args,
        "args": [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ]
    }

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
    Runs a minimal mock memory server on port 48912 to satisfy core.py's
    requirement to fetch contextual memory before starting a session.
    """
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    
    app = FastAPI()
    
    @app.get("/new_dialog/{character}")
    def get_memory(character: str):
        return PlainTextResponse(f"Mock memory context for {character}.")
        
    config = uvicorn.Config(app, host="127.0.0.1", port=48912, log_level="error")
    server = uvicorn.Server(config)
    
    def run_server():
        server.run()
        
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    import socket
    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            with socket.create_connection(("127.0.0.1", 48912), timeout=1):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
            continue
    else:
        raise RuntimeError("Mock memory server failed to start on 48912")
        
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
    from main_server import app
    
    # Use a different port for testing to avoid conflict
    TEST_PORT = 8001
    
    config = uvicorn.Config(app, host="127.0.0.1", port=TEST_PORT, log_level="error")
    server = uvicorn.Server(config)
    
    
    def run_server():
        server.run()
        
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    # Wait for server to start
    # Simple check loop
    import socket
    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            with socket.create_connection(("127.0.0.1", TEST_PORT), timeout=1):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
            continue
    else:
        raise RuntimeError("Test server failed to start")
        
    yield f"http://127.0.0.1:{TEST_PORT}"
    
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

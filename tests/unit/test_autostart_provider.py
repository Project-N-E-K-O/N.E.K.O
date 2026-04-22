import json
import shutil
import subprocess
from pathlib import Path

import pytest


AUTOSTART_PROVIDER_PATH = Path(__file__).resolve().parents[2] / "static" / "app-autostart-provider.js"


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: provider tests do not need it."""
    yield


def _run_autostart_provider_scenario(script_body: str) -> dict:
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")

    node_harness = f"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
// Node 21+ ships a built-in `navigator` that refuses plain assignment; override
// the two properties our provider inspects so the harness works on Windows too.
if (typeof navigator === 'undefined') {{
  global.navigator = {{ platform: 'MacIntel' }};
}} else {{
  Object.defineProperty(navigator, 'platform', {{
    value: 'MacIntel',
    configurable: true,
    writable: true,
  }});
  Object.defineProperty(navigator, 'userAgentData', {{
    value: undefined,
    configurable: true,
    writable: true,
  }});
}}
global.fetch = function () {{
  throw new Error('fetch should not be called');
}};
global.console = {{
  log() {{}},
  warn() {{}},
  error(...args) {{
    process.stderr.write(args.join(' ') + '\\n');
  }},
}};

const source = fs.readFileSync({json.dumps(str(AUTOSTART_PROVIDER_PATH))}, 'utf8');
vm.runInThisContext(source, {{ filename: {json.dumps(str(AUTOSTART_PROVIDER_PATH))} }});

async function runScenario() {{
{script_body}
}}

runScenario()
  .then((result) => {{
    process.stdout.write(JSON.stringify(result));
  }})
  .catch((error) => {{
    process.stderr.write(String(error && error.stack ? error.stack : error));
    process.exit(1);
  }});
"""

    result = subprocess.run(
        [node_executable, "-"],
        input=node_harness,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    if result.returncode != 0:
        raise AssertionError(
            "Node autostart_provider scenario failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    return json.loads(result.stdout)


@pytest.mark.unit
def test_provider_reports_unsupported_without_desktop_bridge():
    result = _run_autostart_provider_scenario(
        """
    return window.nekoAutostartProvider.getStatus();
        """
    )

    assert result == {
        "ok": True,
        "supported": False,
        "enabled": False,
        "authoritative": True,
        "provider": "neko-pc",
        "mechanism": "desktop-bridge-unavailable",
        "platform": "MacIntel",
    }


@pytest.mark.unit
def test_provider_enable_returns_explicit_unsupported_error_without_desktop_bridge():
    result = _run_autostart_provider_scenario(
        """
    return window.nekoAutostartProvider.enable();
        """
    )

    assert result == {
        "ok": False,
        "supported": False,
        "enabled": False,
        "authoritative": True,
        "provider": "neko-pc",
        "mechanism": "desktop-bridge-unavailable",
        "platform": "MacIntel",
        "error": "autostart_not_supported",
        "error_code": "autostart_not_supported",
    }

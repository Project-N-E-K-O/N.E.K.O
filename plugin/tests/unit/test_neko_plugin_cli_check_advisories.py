from __future__ import annotations

from pathlib import Path

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands.validate_cmd import collect_advisory_issues
from plugin.neko_plugin_cli.core.lint import RUFF_LINT_SPEC, RuffLintSpec

pytestmark = pytest.mark.plugin_unit


def _make_plugin(tmp_path: Path, source: str, plugin_id: str = "adv_demo") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Advisory Demo"',
                'version = "0.0.1"',
                'type = "plugin"',
                f'entry = "plugin.plugins.{plugin_id}:DemoPlugin"',
                "",
                "[plugin.sdk]",
                'recommended = ">=0.1.0,<0.2.0"',
                'supported = ">=0.1.0,<0.3.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from plugin.sdk.plugin import neko_plugin\n\n"
        "@neko_plugin\n"
        "class DemoPlugin: pass\n",
        encoding="utf-8",
    )
    (plugin_dir / "worker.py").write_text(source, encoding="utf-8")
    return plugin_dir


def _advisories(tmp_path: Path, source: str) -> list[str]:
    plugin_dir = _make_plugin(tmp_path, source)
    issues = collect_advisory_issues(plugin_dir)
    assert all(level == "advisory" for level, _ in issues)
    return [message for _, message in issues]


# (category, hit source, miss source, text unique to the category)
CASES = [
    (
        "print",
        "def f():\n    print('x')\n",
        "def f(logger):\n    logger.info('x')\n    pprint = 1\n",
        "print() never reaches the plugin log",
    ),
    (
        "eval",
        "def f(code):\n    return eval(code)\n",
        "def f(model):\n    return model.eval()\n",
        "eval() runs arbitrary code",
    ),
    (
        "exec",
        "def f(code):\n    exec(code)\n",
        "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('ls')\n",
        "exec() runs arbitrary code",
    ),
    (
        "pickle",
        "import pickle\ndef f(blob):\n    return pickle.loads(blob)\n",
        "import pickle\ndef f(obj):\n    return pickle.dumps(obj)\n",
        "can run code from the data it reads",
    ),
    (
        "shell",
        "import subprocess\ndef f(cmd):\n    subprocess.run(cmd, shell=True)\n",
        "import subprocess\ndef f(cmd):\n    subprocess.run(['ls', cmd], shell=False)\n",
        "shell=True passes the command through a shell",
    ),
    (
        "verify",
        "import requests\ndef f(url):\n    return requests.get(url, verify=False)\n",
        "import requests\ndef f(url):\n    return requests.get(url, verify=True)\n",
        "verify=False disables TLS certificate checks",
    ),
    (
        "internal_import",
        "from utils.config_manager import get_config_manager\nimport plugin.core.host\n",
        "from plugin.sdk.plugin import neko_plugin\n"
        "from plugin.sdk.adapter.gateway_models import GatewayRequest\n"
        "from plugin.sdk import SDK_VERSION\n"
        "from plugin.plugins.adv_demo.helpers import helper\n"
        "from .local import thing\n",
        "is a N.E.K.O host internal",
    ),
    (
        "blocking_in_async",
        "import time\nasync def f():\n    time.sleep(1)\n",
        "import asyncio, time, requests\n"
        "def sync_entry():\n    time.sleep(1)\n"
        "async def f(url):\n"
        "    await asyncio.sleep(1)\n"
        "    await asyncio.to_thread(lambda: requests.get(url))\n"
        "    def inner():\n        time.sleep(1)\n"
        "    return inner\n",
        "blocks the event loop",
    ),
]


@pytest.mark.parametrize(("category", "hit", "miss", "marker"), CASES, ids=[case[0] for case in CASES])
def test_advisory_category_hits(tmp_path: Path, category: str, hit: str, miss: str, marker: str) -> None:
    messages = _advisories(tmp_path, hit)
    assert messages, category
    assert all(marker in message for message in messages), messages
    assert all(message.startswith("worker.py:") for message in messages), messages


@pytest.mark.parametrize(("category", "hit", "miss", "marker"), CASES, ids=[case[0] for case in CASES])
def test_advisory_category_misses(tmp_path: Path, category: str, hit: str, miss: str, marker: str) -> None:
    assert _advisories(tmp_path, miss) == []


def test_internal_import_reports_each_module(tmp_path: Path) -> None:
    messages = _advisories(
        tmp_path,
        "from utils.config_manager import get_config_manager\n"
        "import plugin.core.host\n"
        "from plugin.sdk.shared.core.router import PluginRouter\n",
    )
    assert [message.split(":", 2)[1] for message in messages] == ["1", "2", "3"]
    assert "'plugin.sdk.shared.core.router'" in messages[2]


@pytest.mark.parametrize(
    "call",
    ["time.sleep(1)", "requests.post(url)", "subprocess.run(['ls'])", "subprocess.check_output(['ls'])"],
)
def test_blocking_calls_in_async_def(tmp_path: Path, call: str) -> None:
    messages = _advisories(
        tmp_path,
        f"import requests, subprocess, time\nasync def handler(url):\n    {call}\n",
    )
    assert len(messages) == 1
    assert messages[0].startswith("worker.py:3: ")
    assert "async def handler" in messages[0]


def test_messages_are_trilingual_and_cite_constraint(tmp_path: Path) -> None:
    (message,) = _advisories(tmp_path, "print('x')\n")
    english, chinese, japanese = message.split(": ", 1)[1].split(" / ")
    assert "AGENTS.md constraint 4" in english
    assert "AGENTS.md 硬约束 4" in chinese
    assert "AGENTS.md 制約 4" in japanese


def test_vendor_tests_and_unparsable_files_are_skipped(tmp_path: Path) -> None:
    plugin_dir = _make_plugin(tmp_path, "x = 1\n")
    for relative in ("vendor/pkg/__init__.py", "tests/test_smoke.py"):
        path = plugin_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("print('x')\neval('1')\n", encoding="utf-8")
    (plugin_dir / "broken.py").write_text("def f(:\n    print('x')\n", encoding="utf-8")
    nested = plugin_dir / "sub" / "tests" / "helper.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("print('x')\n", encoding="utf-8")

    messages = [message for _, message in collect_advisory_issues(plugin_dir)]

    # Only the plugin-root tests/ directory is skipped.
    assert len(messages) == 1
    assert messages[0].startswith(f"{Path('sub', 'tests', 'helper.py')}:1: ")


ALL_VIOLATIONS = """\
import asyncio
import pickle
import subprocess
import time

import requests

from plugin.sdk.plugin import neko_plugin
from plugin.sdk.shared.core.router import PluginRouter
from utils.config_manager import get_config_manager


async def fetch(url):
    print("fetching", url)
    time.sleep(1)
    return requests.get(url, verify=False)


def run(code, blob, cmd):
    eval(code)
    exec(code)
    pickle.loads(blob)
    subprocess.run(cmd, shell=True)
"""


def test_check_prints_advisories_without_changing_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = _make_plugin(tmp_path, ALL_VIOLATIONS)

    exit_code = neko_plugin_cli.main(["check", str(plugin_dir)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[OK] adv_demo: check found 0 error(s)" in captured.out
    advisory_lines = [line for line in captured.out.splitlines() if line.startswith("  [ADVISORY] ")]
    for marker in [case[3] for case in CASES]:
        assert any(marker in line for line in advisory_lines), marker
    assert len(advisory_lines) == 10
    assert "[ADVISORY]" not in captured.err


def test_check_exit_code_still_follows_errors_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = _make_plugin(tmp_path, ALL_VIOLATIONS)
    (plugin_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

    exit_code = neko_plugin_cli.main(["check", str(plugin_dir)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[ADVISORY]" in captured.out


def test_release_check_does_not_run_advisories(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = _make_plugin(tmp_path, ALL_VIOLATIONS)

    neko_plugin_cli.main(["check", str(plugin_dir), "--release", "--skip-tests"])

    captured = capsys.readouterr()
    assert "ADVISORY" not in captured.out + captured.err


def test_ruff_lint_spec_is_unchanged() -> None:
    # Market verify/release workflows pin these; advisories must not widen them.
    assert RUFF_LINT_SPEC == RuffLintSpec(
        version="0.12.4",
        args=("--target-version", "py311", "--line-length", "120", "--select", "E4,E7,E9,F,I"),
    )

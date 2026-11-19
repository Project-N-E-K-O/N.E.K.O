from __future__ import annotations

from pathlib import Path

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands.validate_cmd import _ADVISORY_RULES, collect_advisory_issues
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


LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt")


def _issues(plugin_dir: Path) -> list[str]:
    issues = collect_advisory_issues(plugin_dir)
    assert all(level == "advisory" for level, _ in issues)
    return [message for _, message in issues]


def _is_explanation(message: str) -> bool:
    return "\n" in message


def _advisories(tmp_path: Path, source: str) -> list[str]:
    """Hit lines only; the per-rule explanation blocks are dropped."""
    return [m for m in _issues(_make_plugin(tmp_path, source)) if not _is_explanation(m)]


# (rule code, hit source, miss source, AGENTS.md constraint number)
CASES = [
    (
        "print",
        "def f():\n    print('x')\n",
        "def f(logger):\n    logger.info('x')\n    pprint = 1\n",
        4,
    ),
    (
        "eval-exec",
        "def f(code):\n    return eval(code)\n",
        "def f(model):\n    return model.eval()\n",
        10,
    ),
    (
        "eval-exec",
        "def f(code):\n    exec(code)\n",
        "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('ls')\n",
        10,
    ),
    (
        "pickle",
        "import pickle\ndef f(blob):\n    return pickle.loads(blob)\n",
        "import pickle\ndef f(obj):\n    return pickle.dumps(obj)\n",
        10,
    ),
    (
        "shell-true",
        "import subprocess\ndef f(cmd):\n    subprocess.run(cmd, shell=True)\n",
        "import subprocess\ndef f(cmd):\n    subprocess.run(['ls', cmd], shell=False)\n",
        10,
    ),
    (
        "verify-false",
        "import requests\ndef f(url):\n    return requests.get(url, verify=False)\n",
        "import requests\ndef f(url):\n    return requests.get(url, verify=True)\n",
        10,
    ),
    (
        "host-import",
        "from utils.config_manager import get_config_manager\nimport plugin.core.host\n",
        "from plugin.sdk.plugin import neko_plugin\n"
        "from plugin.sdk.adapter.gateway_models import GatewayRequest\n"
        "from plugin.sdk import SDK_VERSION, plugin\n"
        "from plugin import sdk, plugins\n"
        "from plugin.plugins.adv_demo.helpers import helper\n"
        "from .local import thing\n",
        2,
    ),
    (
        "async-blocking",
        "import time\nasync def f():\n    time.sleep(1)\n",
        "import asyncio, time, requests\n"
        "def sync_entry():\n    time.sleep(1)\n"
        "async def f(url):\n"
        "    await asyncio.sleep(1)\n"
        "    await asyncio.to_thread(lambda: requests.get(url))\n"
        "    await asyncio.to_thread(list, (requests.get(u) for u in [url]))\n"
        "    def inner():\n        time.sleep(1)\n"
        "    return inner\n",
        7,
    ),
]


CASE_IDS = ["print", "eval", "exec", "pickle", "shell", "verify", "host_import", "async_blocking"]


@pytest.mark.parametrize(("code", "hit", "miss", "constraint"), CASES, ids=CASE_IDS)
def test_advisory_category_hits(tmp_path: Path, code: str, hit: str, miss: str, constraint: int) -> None:
    messages = _advisories(tmp_path, hit)
    assert messages, code
    for message in messages:
        assert message.startswith(f"{code}: worker.py:"), message
        assert message.endswith(f"-> AGENTS.md #{constraint}"), message


@pytest.mark.parametrize(("code", "hit", "miss", "constraint"), CASES, ids=CASE_IDS)
def test_advisory_category_misses(tmp_path: Path, code: str, hit: str, miss: str, constraint: int) -> None:
    assert _issues(_make_plugin(tmp_path, miss)) == []


def test_internal_import_reports_each_module(tmp_path: Path) -> None:
    messages = _advisories(
        tmp_path,
        "from utils.config_manager import get_config_manager\n"
        "import plugin.core.host\n"
        "from plugin.sdk.shared.core.router import PluginRouter\n"
        "from plugin import core\n"
        "from plugin.sdk import SDK_VERSION, shared\n"
        "from plugin import sdk, core, logging_config\n"
        "import utils.tokenize, plugin.sdk.plugin, config\n",
    )
    assert messages == [
        "host-import: worker.py:1 utils.config_manager -> AGENTS.md #2",
        "host-import: worker.py:2 plugin.core.host -> AGENTS.md #2",
        "host-import: worker.py:3 plugin.sdk.shared.core.router -> AGENTS.md #2",
        "host-import: worker.py:4 plugin.core -> AGENTS.md #2",
        "host-import: worker.py:5 plugin.sdk.shared -> AGENTS.md #2",
        "host-import: worker.py:6 plugin.core -> AGENTS.md #2",
        "host-import: worker.py:6 plugin.logging_config -> AGENTS.md #2",
        "host-import: worker.py:7 utils.tokenize -> AGENTS.md #2",
        "host-import: worker.py:7 config -> AGENTS.md #2",
    ]


@pytest.mark.parametrize(
    "call",
    ["time.sleep(1)", "requests.post(url)", "subprocess.run(['ls'])", "subprocess.check_output(['ls'])"],
)
def test_blocking_calls_in_async_def(tmp_path: Path, call: str) -> None:
    messages = _advisories(
        tmp_path,
        f"import requests, subprocess, time\nasync def handler(url):\n    {call}\n",
    )
    assert messages == [f"async-blocking: worker.py:3 handler(): {call.split('(')[0]}() -> AGENTS.md #7"]


@pytest.mark.parametrize("code", list(_ADVISORY_RULES))
def test_every_rule_has_all_eight_locales(code: str) -> None:
    _, texts = _ADVISORY_RULES[code]
    assert tuple(texts) == LOCALES
    assert all(text.strip() for text in texts.values())
    # zh-TW is written separately, not a copy of zh-CN.
    assert texts["zh-TW"] != texts["zh-CN"]


def test_one_eight_locale_explanation_per_rule_with_hits(tmp_path: Path) -> None:
    messages = _issues(_make_plugin(tmp_path, "print('a')\nprint('b')\n"))

    assert messages[:2] == ["print: worker.py:1 -> AGENTS.md #4", "print: worker.py:2 -> AGENTS.md #4"]
    assert len(messages) == 3
    header, *locale_lines = messages[2].split("\n")
    assert header == "print: AGENTS.md #4, .agents/skills/neko-plugin/references/execution-boundary.md"
    assert [line.strip().split(":", 1)[0] for line in locale_lines] == list(LOCALES)
    assert "self.logger" in locale_lines[0]


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

    messages = [m for m in _issues(plugin_dir) if not _is_explanation(m)]

    # Only the plugin-root tests/ directory is skipped.
    assert messages == [f"print: {Path('sub', 'tests', 'helper.py')}:1 -> AGENTS.md #4"]


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
    hit_lines = [line for line in advisory_lines if " -> AGENTS.md #" in line]
    explanation_lines = [line for line in advisory_lines if line not in hit_lines]
    # 10 hits; one explanation per rule (all 7 fire).
    assert len(hit_lines) == 10
    assert len(explanation_lines) == len(_ADVISORY_RULES)
    for code in _ADVISORY_RULES:
        assert any(line.startswith(f"  [ADVISORY] {code}: ") for line in hit_lines), code
    for locale in LOCALES:
        assert sum(line.startswith(f"      {locale}: ") for line in captured.out.splitlines()) == 7, locale
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


def test_generator_first_iterable_runs_on_the_loop(tmp_path: Path) -> None:
    messages = _advisories(
        tmp_path,
        "import requests\nasync def f(url):\n    return list(x for x in requests.get(url).json())\n",
    )
    assert messages == ["async-blocking: worker.py:3 f(): requests.get() -> AGENTS.md #7"]


@pytest.mark.parametrize(
    "expression",
    ["list(requests.get(u) for u in urls)", "sum(1 for u in urls if requests.head(u).ok)"],
)
def test_generator_consumed_on_the_loop_is_reported(tmp_path: Path, expression: str) -> None:
    messages = _advisories(
        tmp_path,
        f"import requests\nasync def f(urls):\n    return {expression}\n",
    )
    assert len(messages) == 1
    assert messages[0].startswith("async-blocking: worker.py:3 f(): requests.")


def test_generator_handed_to_executor_is_not_reported(tmp_path: Path) -> None:
    assert _advisories(
        tmp_path,
        "import asyncio, requests\n"
        "async def f(loop, urls):\n"
        "    await loop.run_in_executor(None, list, (requests.get(u) for u in urls))\n",
    ) == []

"""Unit tests for the pre-release check aggregation (lint, sync, release check)."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
import yaml

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import precheck_cmd
from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir
from plugin.neko_plugin_cli.core.lint import RUFF_LINT_SPEC, ruff_command

pytestmark = pytest.mark.plugin_unit

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_plugin_dir(tmp_path: Path, plugin_id: str = "pre_demo") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Pre Demo"',
                'version = "0.0.1"',
                'type = "plugin"',
                f'entry = "plugin.plugins.{plugin_id}:DemoPlugin"',
                "",
                "[plugin.sdk]",
                'recommended = ">=0.1.0,<0.2.0"',
                'supported = ">=0.1.0,<0.3.0"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    return plugin_dir


def test_ruff_command_pins_version_and_target() -> None:
    command = ruff_command("some/dir")
    assert command[0] == "uvx"
    assert command[1] == f"ruff=={RUFF_LINT_SPEC.version}"
    assert command[-1] == "some/dir"
    assert "--select" in command and "E4,E7,E9,F,I" in command


@pytest.mark.parametrize("workflow", ["plugin-market-verify.yml", "plugin-market-release.yml"])
def test_market_workflows_run_the_pinned_ruff_command(workflow: str) -> None:
    # core.lint owns the pinned invocation; the workflow text must stay
    # token-identical to it or one side has drifted.
    steps = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    )["jobs"][workflow.removesuffix(".yml").removeprefix("plugin-market-")]["steps"]
    run = next(step["run"] for step in steps if step["name"] == "Ruff check")
    target = "plugin-repo/${MARKET_PLUGIN_DIRECTORY}"
    assert shlex.split(run) == ruff_command(target)


def test_validate_plugin_dir_flags_symbolic_links(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    try:
        (plugin_dir / "link.py").symlink_to(plugin_dir / "__init__.py")
    except OSError as exc:
        pytest.skip(f"this environment cannot create symlinks: {exc}")
    issues = validate_plugin_dir(plugin_dir)
    assert any(
        severity == "error" and "symbolic links" in message for severity, message in issues
    )


def test_validate_plugin_dir_ignores_vendor_links(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    try:
        (vendor / "link.py").symlink_to(plugin_dir / "__init__.py")
    except OSError as exc:
        pytest.skip(f"this environment cannot create symlinks: {exc}")
    issues = validate_plugin_dir(plugin_dir)
    assert not [
        message for severity, message in issues if severity == "error" and "symbolic links" in message
    ]


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_precheck(
    monkeypatch: pytest.MonkeyPatch, plugin_dir: Path, results: list[_Completed]
) -> tuple[int, list[list[str]]]:
    calls: list[list[str]] = []
    real_run = precheck_cmd.subprocess.run

    def fake_run(argv, **kwargs):
        if argv[0] == "git":
            # The repository diagnosis reads git through the same module.
            return real_run(argv, **kwargs)
        calls.append(list(argv))
        return results[len(calls) - 1]

    monkeypatch.setattr(precheck_cmd.subprocess, "run", fake_run)
    exit_code = neko_plugin_cli.main(["precheck", str(plugin_dir), "--json"])
    return exit_code, calls


def test_precheck_runs_the_three_ci_gates_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    exit_code, calls = _run_precheck(monkeypatch, plugin_dir, [_Completed(0), _Completed(0), _Completed(0)])
    assert exit_code == 0
    stages = [[part for part in argv if part in {"lint", "sync", "check"}] for argv in calls]
    assert stages == [["lint"], ["sync"], ["check"]]
    assert "--release" in calls[2]
    assert "--clean" in calls[1]


def test_precheck_stops_after_a_failing_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    exit_code, calls = _run_precheck(monkeypatch, plugin_dir, [_Completed(1, stderr="boom")])
    assert exit_code == 1
    assert len(calls) == 1


def test_precheck_json_report_keeps_its_shape_when_the_directory_is_unknown(
    tmp_path: Path, capsys
) -> None:
    exit_code = neko_plugin_cli.main(["precheck", str(tmp_path / "nope"), "--json"])
    captured = capsys.readouterr()
    assert exit_code == 1
    report = json.loads(captured.out.strip().splitlines()[-1])
    assert report["passed"] is False
    assert report["plugin_id"] is None
    assert report["environment_issues"] == []
    assert [stage["name"] for stage in report["stages"]] == ["lint"]


def test_precheck_json_report_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    exit_code, _ = _run_precheck(
        monkeypatch, plugin_dir, [_Completed(0), _Completed(0), _Completed(1, stdout="tests failed")]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    report = json.loads(captured.out.strip().splitlines()[-1])
    assert report["passed"] is False
    assert report["plugin_id"] == "pre_demo"
    assert [stage["name"] for stage in report["stages"]] == ["lint", "sync", "release-check"]
    assert report["stages"][-1]["passed"] is False
    assert "tests failed" in report["stages"][-1]["output"]
    assert report["ruff_version"] == RUFF_LINT_SPEC.version
    assert isinstance(report["environment_issues"], list)



def test_ruff_command_extends_rather_than_replaces_the_default_excludes() -> None:
    # --exclude would drop ruff's defaults (.venv, build, dist, ...).
    command = ruff_command("some/dir")
    assert "--exclude" not in command
    assert command[command.index("--extend-exclude") + 1] == "vendor"


def test_precheck_prints_environment_issues_without_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    monkeypatch.setattr(
        precheck_cmd, "_diagnose_repository", lambda *_args, **_kwargs: [("warning", "git status failed")]
    )
    monkeypatch.setattr(precheck_cmd.subprocess, "run", lambda argv, **kwargs: _Completed(0))
    assert neko_plugin_cli.main(["precheck", str(plugin_dir)]) == 0
    out = capsys.readouterr().out
    assert "[WARNING] git status failed" in out
    assert "precheck passed" in out

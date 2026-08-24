from __future__ import annotations

import asyncio
import json
from pathlib import Path
import runpy
import subprocess

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import init_cmd
from plugin.neko_plugin_cli.paths import CliDefaults


pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _isolate_github_actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the release checks from reading the CI job's own repo and ref.

    On a pull-request run ``GITHUB_REF_NAME`` is ``<pr>/merge`` and
    ``GITHUB_REPOSITORY`` is this repository, so a market-release check that
    falls back to the environment reports a tag/version mismatch against the
    fixture plugin. Tests that exercise those variables set them explicitly.
    """
    for name in ("GITHUB_REPOSITORY", "GITHUB_REF_NAME", "GITHUB_REF"):
        monkeypatch.delenv(name, raising=False)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _source_tree_defaults(tmp_path: Path) -> CliDefaults:
    plugin_root = tmp_path / "plugin"
    return CliDefaults(
        plugin_root=plugin_root,
        target_dir=plugin_root / "neko_plugin_cli" / "target",
        plugins_root=plugin_root / "plugins",
        profiles_root=plugin_root / ".neko-package-profiles",
    )


def test_init_creates_complete_plugin_source_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        neko_plugin_cli,
        "resolve_default_paths",
        lambda: _source_tree_defaults(tmp_path),
    )

    exit_code = neko_plugin_cli.main(["init", "market_demo"])

    repo = tmp_path / "plugin" / "plugins" / "market_demo"
    assert exit_code == 0
    assert {
        "plugin.toml",
        "config.example.toml",
        "__init__.py",
        "pyproject.toml",
        "README.md",
        "tests/test_smoke.py",
        ".gitignore",
        ".vscode/settings.json",
        ".vscode/tasks.json",
        "ruff.toml",
        ".github/workflows/verify.yml",
        ".github/workflows/release.yml",
        ".git/HEAD",
    } <= {
        path.relative_to(repo).as_posix()
        for path in repo.rglob("*")
    }
    assert _git(repo, "branch", "--show-current") == "main"
    assert "plugin-market-verify.yml@main" in (
        repo / ".github/workflows/verify.yml"
    ).read_text(encoding="utf-8")
    assert "plugin-market-release.yml@main" in (
        repo / ".github/workflows/release.yml"
    ).read_text(encoding="utf-8")
    settings = json.loads(
        (repo / ".vscode/settings.json").read_text(encoding="utf-8")
    )
    tasks = json.loads(
        (repo / ".vscode/tasks.json").read_text(encoding="utf-8")
    )["tasks"]
    check_task = next(
        task for task in tasks if task["label"] == "N.E.K.O: check market_demo"
    )
    sync_task = next(
        task for task in tasks if task["label"] == "N.E.K.O: sync market_demo"
    )
    assert settings["nekoPlugin.repoRoot"] == "../../.."
    assert check_task["command"] == "uv run neko-plugin check market_demo"
    assert sync_task["command"] == (
        "uv run --with pip neko-plugin sync market_demo --clean"
    )
    assert check_task["options"]["cwd"] == "${config:nekoPlugin.repoRoot}"


def test_init_supports_git_without_initial_branch_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        neko_plugin_cli,
        "resolve_default_paths",
        lambda: _source_tree_defaults(tmp_path),
    )
    real_run_git = init_cmd._run_git

    def run_as_git_227(command: list[str], *, cwd: Path) -> None:
        if command[:2] == ["init", "-b"]:
            raise RuntimeError("error: unknown option `b'")
        real_run_git(command, cwd=cwd)

    monkeypatch.setattr(init_cmd, "_run_git", run_as_git_227)

    exit_code = neko_plugin_cli.main(["init", "legacy_git"])

    repo = tmp_path / "plugin" / "plugins" / "legacy_git"
    assert exit_code == 0
    assert _git(repo, "branch", "--show-current") == "main"


def test_init_creates_minimal_callable_plugin_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        neko_plugin_cli,
        "resolve_default_paths",
        lambda: _source_tree_defaults(tmp_path),
    )

    exit_code = neko_plugin_cli.main(["init", "hello_world", "--name", "Hello World"])

    entry = tmp_path / "plugin" / "plugins" / "hello_world" / "__init__.py"
    assert exit_code == 0
    assert entry.read_text(encoding="utf-8") == '''from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    @plugin_entry(id="hello", name="Hello", description="Say hello")
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})
'''


def test_generated_quick_start_entry_accepts_runtime_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        neko_plugin_cli,
        "resolve_default_paths",
        lambda: _source_tree_defaults(tmp_path),
    )
    assert neko_plugin_cli.main(["init", "hello_world"]) == 0
    entry = tmp_path / "plugin" / "plugins" / "hello_world" / "__init__.py"
    plugin_class = runpy.run_path(str(entry))["HelloWorldPlugin"]
    handler = object.__new__(plugin_class).hello
    result = asyncio.run(handler(name="Neko", _ctx={"run_id": "run-1"}))

    assert result.value == {"message": "Hello, Neko!"}


@pytest.mark.parametrize(
    "guide_path",
    [
        "docs/plugins/quick-start.md",
        "docs/zh-CN/plugins/quick-start.md",
        "docs/ja/plugins/quick-start.md",
    ],
)
def test_quick_start_guides_show_generated_minimal_entry(guide_path: str) -> None:
    root = Path(__file__).resolve().parents[3]
    guide = (root / guide_path).read_text(encoding="utf-8")
    feature_section = guide.split("## 7.", maxsplit=1)[1].split("## 8.", maxsplit=1)[0]

    assert '''from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    @plugin_entry(id="hello", name="Hello", description="Say hello")
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})
''' in feature_section
    assert "from typing import Any" not in feature_section
    assert "@lifecycle" not in feature_section
    assert "input_schema" not in feature_section


def test_init_repo_command_is_removed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main(["init-repo", "demo"])

    assert exc_info.value.code == 2
    assert "invalid choice: 'init-repo'" in capsys.readouterr().err


def test_init_uses_exact_custom_output_and_custom_directory_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "custom-local-directory"

    exit_code = neko_plugin_cli.main(
        ["init", "custom_output", "--output", str(output)]
    )

    assert exit_code == 0
    assert output.is_dir()
    assert not (tmp_path / "n.e.k.o_plugin_custom_output").exists()
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "uv run --with pip --project" in readme
    assert "neko-plugin sync . --clean" in readme
    assert "neko-plugin check ." in readme
    assert "neko-plugin check -r ." in readme
    assert "From this plugin repository root" in readme
    assert "N.E.K.O/plugin/plugins/custom_output" not in readme
    assert neko_plugin_cli.main(["check", str(output)]) == 0
    assert "does not match directory name" not in capsys.readouterr().out


def test_init_does_not_delete_directory_created_during_mkdir_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "competing-directory"
    sentinel = output / "owned-by-another-process.txt"
    real_mkdir = Path.mkdir

    def competing_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == output:
            real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
            sentinel.write_text("do not delete\n", encoding="utf-8")
            raise FileExistsError(output)
        real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", competing_mkdir)

    exit_code = neko_plugin_cli.main(
        ["init", "race_demo", "--output", str(output)]
    )

    assert exit_code == 1
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"


def test_init_accepts_only_matching_github_remote(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    remote = "git@github.com:alice/n.e.k.o_plugin_remote_demo.git"

    assert (
        neko_plugin_cli.main(
            [
                "init",
                "remote_demo",
                "--output",
                str(repo),
                "--remote",
                remote,
            ]
        )
        == 0
    )
    assert _git(repo, "remote", "get-url", "origin") == remote

    bad_repo = tmp_path / "bad-repo"
    assert (
        neko_plugin_cli.main(
            [
                "init",
                "remote_demo",
                "--output",
                str(bad_repo),
                "--remote",
                "https://github.com/alice/wrong-name.git",
            ]
        )
        == 1
    )
    assert not bad_repo.exists()


def test_init_and_market_check_accept_github_ssh_over_https_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    remote = (
        "ssh://git@ssh.github.com:443/"
        "alice/n.e.k.o_plugin_remote_demo.git"
    )
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)

    assert (
        neko_plugin_cli.main(
            [
                "init",
                "remote_demo",
                "--output",
                str(repo),
                "--remote",
                remote,
            ]
        )
        == 0
    )
    assert _git(repo, "remote", "get-url", "origin") == remote
    assert (
        neko_plugin_cli.main(
            [
                "check",
                "--release",
                "--market-release",
                "--skip-tests",
                "--target-dir",
                str(tmp_path / "target"),
                str(repo),
            ]
        )
        == 0
    )


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/al ice/n.e.k.o_plugin_remote_demo.git",
        "https://github.com/alice/n.e.k.o_plugin_remote_demo.git?token=x",
        "https://github.com/alice/n.e.k.o_plugin_remote_demo.git#fragment",
        "https://github.com/@alice/n.e.k.o_plugin_remote_demo.git",
    ],
)
def test_init_rejects_malformed_github_remote(
    remote: str,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"

    assert (
        neko_plugin_cli.main(
            [
                "init",
                "remote_demo",
                "--output",
                str(repo),
                "--remote",
                remote,
            ]
        )
        == 1
    )
    assert not repo.exists()


def test_market_release_check_uses_origin_instead_of_local_directory_name(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "custom-local-directory"
    remote = "https://github.com/alice/n.e.k.o_plugin_identity_demo.git"
    assert (
        neko_plugin_cli.main(
            [
                "init",
                "identity_demo",
                "--output",
                str(repo),
                "--remote",
                remote,
            ]
        )
        == 0
    )

    exit_code = neko_plugin_cli.main(
        [
            "check",
            "--release",
            "--market-release",
            "--skip-tests",
            "--target-dir",
            str(tmp_path / "target"),
            str(repo),
        ]
    )

    assert exit_code == 0


def test_market_release_check_without_origin_does_not_use_local_directory_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "custom-local-directory"
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert (
        neko_plugin_cli.main(
            ["init", "identity_demo", "--output", str(repo)]
        )
        == 0
    )

    exit_code = neko_plugin_cli.main(
        [
            "check",
            "--release",
            "--market-release",
            "--skip-tests",
            "--target-dir",
            str(tmp_path / "target"),
            str(repo),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "git remote 'origin' is not configured" in captured.err
    assert "got custom-local-directory" not in captured.err


def test_market_release_check_rejects_non_github_origin_containing_github_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert neko_plugin_cli.main(["init", "remote_guard", "--output", str(repo)]) == 0
    _git(
        repo,
        "remote",
        "add",
        "origin",
        "https://evil.example/github.com/n.e.k.o_plugin_remote_guard",
    )

    exit_code = neko_plugin_cli.main(
        [
            "check",
            "--release",
            "--market-release",
            "--skip-tests",
            "--target-dir",
            str(tmp_path / "target"),
            str(repo),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "must point to GitHub" in captured.err


def test_init_and_market_check_accept_case_equivalent_github_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "custom-case-directory"
    remote = "https://GitHub.com/Alice/N.E.K.O_PLUGIN_REMOTE_CASE.git"
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    assert (
        neko_plugin_cli.main(
            [
                "init",
                "remote_case",
                "--output",
                str(repo),
                "--remote",
                remote,
            ]
        )
        == 0
    )
    assert _git(repo, "remote", "get-url", "origin") == remote
    assert (
        neko_plugin_cli.main(
            [
                "check",
                "--release",
                "--market-release",
                "--skip-tests",
                "--target-dir",
                str(tmp_path / "target"),
                str(repo),
            ]
        )
        == 0
    )


@pytest.mark.parametrize("plugin_id", ["_demo", "Demo", "demo-plugin"])
def test_init_rejects_non_market_plugin_ids(
    plugin_id: str,
    tmp_path: Path,
) -> None:
    output = tmp_path / "repo"

    assert (
        neko_plugin_cli.main(
            ["init", plugin_id, "--output", str(output)]
        )
        == 1
    )
    assert not output.exists()


@pytest.mark.parametrize(
    "removed_flag",
    [
        "--plugins-root",
        "--git",
        "--no-git",
        "--github-actions",
        "--no-github-actions",
        "--no-readme",
        "--no-tests",
        "--no-gitignore",
        "--no-vscode",
        "--neko-repo",
        "--neko-ref",
        "--no-interactive",
    ],
)
def test_init_does_not_expose_partial_repository_flags(
    removed_flag: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main(["init", "demo", removed_flag])

    assert exc_info.value.code == 2


@pytest.mark.parametrize("removed_flag", ["--neko-repo", "--neko-ref"])
def test_setup_repo_does_not_expose_custom_workflow_source_flags(
    removed_flag: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main(
            ["setup-repo", "missing-plugin", removed_flag, "custom-value"]
        )

    assert exc_info.value.code == 2
    assert removed_flag in capsys.readouterr().err

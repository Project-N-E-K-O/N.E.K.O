from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import runpy
import subprocess
import zipfile

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import init_cmd, release_cmd
from plugin.neko_plugin_cli.paths import CliDefaults


pytestmark = pytest.mark.plugin_unit


def test_creation_rollback_preserves_a_replacement_directory(tmp_path):
    from plugin.neko_plugin_cli.core.project import create_project
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    target = tmp_path / "replacement_demo"
    original = tmp_path / "original"

    def replace_then_fail(*args, **kwargs):
        assert target.resolve().parent == tmp_path.resolve()
        target.rename(original)
        target.mkdir()
        (target / "user.txt").write_text("keep", encoding="utf-8")
        raise RuntimeError("git failed after replacement")

    with pytest.raises(RuntimeError):
        create_project(PluginSpec("replacement_demo"), target, repo_root=None, git_runner=replace_then_fail)
    assert (target / "user.txt").read_text(encoding="utf-8") == "keep"


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


def _commit_all(repo: Path) -> None:
    """Commit the scaffold: check --release rejects files CI would not check out."""
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Init Test", "-c", "user.email=init@example.com", "commit", "-qm", "scaffold")


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
        "uv run neko-plugin sync market_demo --clean"
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
    assert "uv run --project" in readme
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


def test_init_accepts_any_github_remote_and_warns_about_an_unconventional_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
    assert "[WARNING]" not in capsys.readouterr().out

    # The Market does not require the n.e.k.o_plugin_<id> name.
    other_repo = tmp_path / "other-repo"
    other_remote = "https://github.com/alice/other-name.git"
    assert (
        neko_plugin_cli.main(
            [
                "init",
                "remote_demo",
                "--output",
                str(other_repo),
                "--remote",
                other_remote,
            ]
        )
        == 0
    )
    assert _git(other_repo, "remote", "get-url", "origin") == other_remote
    output = capsys.readouterr().out
    assert "[WARNING] the recommended repository name is n.e.k.o_plugin_remote_demo" in output
    assert "other-name" in output


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
    _commit_all(repo)
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
    _commit_all(repo)

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
    _commit_all(repo)
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



def _market_check(repo: Path, tmp_path: Path) -> int:
    return neko_plugin_cli.main(
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


def test_market_release_check_rejects_committed_files_the_market_scan_rejects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    remote = "https://github.com/alice/n.e.k.o_plugin_limits_demo.git"
    assert neko_plugin_cli.main(["init", "limits_demo", "--output", str(repo), "--remote", remote]) == 0
    (repo / "native").mkdir()
    (repo / "native" / "helper.DLL").write_bytes(b"MZ")
    (repo / "data.txt").write_text("0" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    # The scan streams media files, so the per-file limit does not apply to them.
    (repo / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024))
    _commit_all(repo)

    assert _market_check(repo, tmp_path) == 1

    error = capsys.readouterr().err
    assert "the Market rejects binary files in the repository: native/helper.DLL" in error
    assert "the Market rejects files over 2 MiB: data.txt /" in error
    assert "cover.png" not in error


def test_market_release_check_rejects_a_version_the_market_cannot_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    remote = "https://github.com/alice/n.e.k.o_plugin_version_demo.git"
    assert neko_plugin_cli.main(["init", "version_demo", "--output", str(repo), "--remote", remote]) == 0
    plugin_toml = repo / "plugin.toml"
    long_version = "1.0.0-" + "a" * 45
    plugin_toml.write_text(
        plugin_toml.read_text(encoding="utf-8").replace('version = "0.1.0"', f'version = "{long_version}"'),
        encoding="utf-8",
    )
    _commit_all(repo)

    assert _market_check(repo, tmp_path) == 1

    assert "Market version must be at most 50 characters, got 51" in capsys.readouterr().err


def test_market_plugin_id_is_limited_to_100_characters() -> None:
    assert release_cmd._diagnose_market_plugin_id("a" * 100) == []
    [(severity, message)] = release_cmd._diagnose_market_plugin_id("a" * 101)
    assert severity == "error"
    assert "at most 100 characters, got 101" in message


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        # 4999 files plus the archive's top-level directory: 5000 entries.
        ([(f"f{index}.py", 1) for index in range(4999)], None),
        ([(f"f{index}.py", 1) for index in range(5000)], "more than 5000 files and directories, got 5001"),
        # Each file is in its own directory, so the directories count too.
        ([(f"d{index}/f.py", 1) for index in range(2500)], "got 5001"),
        ([(f"cover{index}.png", 2 * 1024 * 1024) for index in range(17)], None),
        ([(f"cover{index}.png", 2 * 1024 * 1024) for index in range(65)], "committed files total 130.0 MiB"),
        ([(f"cover{index}.png", 2 * 1024 * 1024) for index in range(16)], None),
    ],
)
def test_market_source_archive_limits_follow_the_market_scan(
    files: list[tuple[str, int]],
    expected: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(release_cmd, "committed_file_sizes", lambda _root: files)

    issues = release_cmd._diagnose_market_source_archive(tmp_path)

    if expected is None:
        assert issues == []
    else:
        [(severity, message)] = issues
        assert severity == "error"
        assert expected in message



def _write_package(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        ({"manifest.toml": b"id = 'demo'", "payload/plugins/demo/__init__.py": b"x = 1\n"}, None),
        # A binary by suffix, and one renamed to look like text.
        ({"payload/plugins/demo/native.pyd": b"\0"}, "binary files in the package: payload/plugins/demo/native.pyd"),
        ({"payload/plugins/demo/data.txt": b"MZ\x90\x00"}, "binary files in the package: payload/plugins/demo/data.txt"),
        ({"payload/plugins/demo/big.json": b" " * (2 * 1024 * 1024 + 1)}, "package files over 2 MiB"),
        # Media is streamed by the scan, so its size alone is not rejected.
        ({"payload/plugins/demo/cover.png": b"\x89PNG\r\n\x1a\n" + b"\0" * (3 * 1024 * 1024)}, None),
        ({f"payload/plugins/demo/f{index}.py": b"" for index in range(5001)}, "more than 5000 entries, got 5001"),
    ],
)
def test_market_package_limits_follow_the_market_scan(
    members: dict[str, bytes], expected: str | None, tmp_path: Path
) -> None:
    package = _write_package(tmp_path / "demo.neko-plugin", members)

    issues = release_cmd._diagnose_market_package(package)

    if expected is None:
        assert issues == []
    else:
        [(severity, message)] = issues
        assert severity == "error"
        assert expected in message


def test_market_package_over_32_mib_is_rejected(tmp_path: Path) -> None:
    # Stored random-looking bytes keep the archive itself over the limit.
    package = tmp_path / "demo.neko-plugin"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(17):
            archive.writestr(f"payload/plugins/demo/cover{index}.png", b"\x89PNG\r\n\x1a\n" + os.urandom(2 * 1024 * 1024))

    messages = [message for _, message in release_cmd._diagnose_market_package(package)]

    assert any("package over 32 MiB" in message for message in messages)

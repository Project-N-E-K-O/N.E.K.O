from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli


pytestmark = pytest.mark.plugin_unit


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


def test_init_creates_complete_market_repository_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = neko_plugin_cli.main(["init", "market_demo"])

    repo = tmp_path / "n.e.k.o_plugin_market_demo"
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


def test_init_repo_command_is_removed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main(["init-repo", "demo"])

    assert exc_info.value.code == 2
    assert "invalid choice: 'init-repo'" in capsys.readouterr().err


def test_init_uses_exact_custom_output_and_standalone_commands(
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
    assert "neko-plugin sync . --clean" in readme
    assert "neko-plugin check ." in readme
    assert "neko-plugin check -r ." in readme
    assert "From the N.E.K.O repository root" not in readme
    settings = (output / ".vscode/settings.json").read_text(encoding="utf-8")
    tasks = (output / ".vscode/tasks.json").read_text(encoding="utf-8")
    assert '"nekoPlugin.repoRoot"' not in settings
    assert '"cwd": "${workspaceFolder}"' in tasks
    assert "neko-plugin check ." in tasks
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

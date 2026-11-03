from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import publish_cmd
from plugin.neko_plugin_cli.paths import CliDefaults
from plugin.neko_plugin_cli.templates.generator import (
    PluginSpec,
    render_release_workflow,
)
from tests.fake_clock import patch_module_clock

pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def release_ruff_process(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    real_run = subprocess.run
    state: dict[str, Any] = {
        "calls": [],
        "exception": None,
        "returncode": 0,
        "stdout": "",
    }

    def run(command: list[str], *args: Any, **kwargs: Any) -> Any:
        if command[:2] == ["uvx", "ruff==0.12.4"]:
            state["calls"].append({"command": command, "kwargs": kwargs})
            if state["exception"] is not None:
                raise state["exception"]
            return subprocess.CompletedProcess(
                command,
                state["returncode"],
                stdout=state["stdout"],
            )
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return state


class _RecordingClient:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __enter__(self) -> _RecordingClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


class _FailingGitHubClient(_RecordingClient):
    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("connection refused", request=request)


def _ready_release(release_url: str) -> httpx.Response:
    assets = [
        "publish_demo.neko-plugin",
        "publish_demo.market-release-check.txt",
        "market-evidence.json",
    ]
    return httpx.Response(
        200,
        json={
            "html_url": release_url,
            "assets": [
                {
                    "name": name,
                    "state": "uploaded",
                    "size": 123,
                    "browser_download_url": f"{release_url}/download/{name}",
                }
                for name in assets
            ],
        },
    )


def _run_git(repo: Path, *args: str) -> str:
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
    plugin_root = tmp_path / "source" / "plugin"
    return CliDefaults(
        plugin_root=plugin_root,
        target_dir=plugin_root / "neko_plugin_cli" / "target",
        plugins_root=plugin_root / "plugins",
        profiles_root=plugin_root / ".neko-package-profiles",
    )


def _redirect_origin_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plugin_dir: Path,
    remote: Path,
) -> None:
    delegated_run = subprocess.run
    resolved_plugin_dir = plugin_dir.resolve()

    def run(command: list[str], *args: Any, **kwargs: Any) -> Any:
        rewritten = command
        cwd = kwargs.get("cwd")
        if (
            command[:2] in (["git", "push"], ["git", "ls-remote"])
            and len(command) > 2
            and command[2] == "origin"
            and cwd is not None
            and Path(cwd).resolve() == resolved_plugin_dir
        ):
            rewritten = [*command]
            rewritten[2] = f"file://{remote}"
        return delegated_run(rewritten, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


def _make_publish_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    plugin_dir = tmp_path / "n.e.k.o_plugin_publish_demo"
    remote = tmp_path / "publish-demo.git"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text(
        "[plugin]\n"
        'id = "publish_demo"\n'
        'name = "Publish Demo"\n'
        'version = "1.2.0"\n'
        'type = "plugin"\n'
        'entry = "plugin.plugins.publish_demo:PublishDemoPlugin"\n',
        encoding="utf-8",
    )
    workflow = plugin_dir / ".github" / "workflows" / "release.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        render_release_workflow(PluginSpec(plugin_id="publish_demo")),
        encoding="utf-8",
    )
    _run_git(plugin_dir, "init", "-b", "main")
    _run_git(plugin_dir, "config", "user.name", "Publish Test")
    _run_git(plugin_dir, "config", "user.email", "publish@example.com")
    _run_git(plugin_dir, "add", "plugin.toml", ".github/workflows/release.yml")
    _run_git(plugin_dir, "commit", "-m", "initial")
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    github_url = "https://github.com/neko/n.e.k.o_plugin_publish_demo"
    _run_git(plugin_dir, "remote", "add", "origin", f"file://{remote}")
    _run_git(plugin_dir, "push", "-u", "origin", "main")
    _run_git(plugin_dir, "remote", "set-url", "origin", github_url)
    _redirect_origin_transport(
        monkeypatch,
        plugin_dir=plugin_dir,
        remote=remote,
    )
    return plugin_dir, remote


def _make_cli_publish_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    defaults: CliDefaults | None = None,
) -> tuple[Path, Path]:
    if defaults is None:
        defaults = _source_tree_defaults(tmp_path)
    monkeypatch.setattr(
        neko_plugin_cli,
        "resolve_default_paths",
        lambda: defaults,
    )
    remote = tmp_path / "publish-demo.git"
    github_url = "https://github.com/neko/n.e.k.o_plugin_publish_demo"
    assert (
        neko_plugin_cli.main(
            ["init", "publish_demo", "--remote", github_url]
        )
        == 0
    )
    plugin_dir = defaults.plugins_root / "publish_demo"
    _run_git(plugin_dir, "config", "user.name", "Publish Test")
    _run_git(plugin_dir, "config", "user.email", "publish@example.com")
    _run_git(plugin_dir, "add", ".")
    _run_git(plugin_dir, "commit", "-m", "initial")
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _run_git(plugin_dir, "remote", "set-url", "origin", f"file://{remote}")
    _run_git(plugin_dir, "push", "-u", "origin", "main")
    _run_git(plugin_dir, "remote", "set-url", "origin", github_url)
    _redirect_origin_transport(
        monkeypatch,
        plugin_dir=plugin_dir,
        remote=remote,
    )
    return plugin_dir, remote


def test_publish_market_anonymously_notifies_market(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_url = "https://github.com/neko/n.e.k.o_plugin_demo/releases/tag/v1.2.0"
    response = httpx.Response(
        201,
        json={
            "status": "published",
            "version": {"version": "1.2.0"},
        },
    )
    client = _RecordingClient(response)
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(["publish", "market", release_url])

    assert exit_code == 0
    assert client.requests == [
        {
            "url": "https://market.project-neko.cn/api/v1/release-publications",
            "json": {"release_url": release_url},
        }
    ]
    assert "Authorization" not in str(client.requests)
    assert "[OK] Market published v1.2.0" in capsys.readouterr().out


def test_publish_github_pushes_version_tag_and_waits_for_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 0
    assert _run_git(remote, "rev-list", "-n", "1", "v1.2.0") == _run_git(
        plugin_dir,
        "rev-parse",
        "HEAD",
    )
    assert client.requests == [
        {
            "method": "GET",
            "url": (
                "https://api.github.com/repos/neko/"
                "n.e.k.o_plugin_publish_demo/releases/tags/v1.2.0"
            ),
            "headers": {
                "Accept": "application/vnd.github+json",
                "User-Agent": "neko-plugin",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        }
    ]
    assert release_url in capsys.readouterr().out


def test_publish_ignores_ambient_branch_ref_when_creating_version_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv(
        "GITHUB_REPOSITORY",
        "neko/n.e.k.o_plugin_publish_demo",
    )
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _, remote = _make_cli_publish_repo(tmp_path, monkeypatch)

    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
    )
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", "publish_demo"]
    )

    assert exit_code == 0
    assert _run_git(remote, "tag", "--list") == "v0.1.0"
    assert len(client.requests) == 1


def test_publish_uses_writable_preflight_directory_when_default_is_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = _source_tree_defaults(tmp_path)
    blocked_install_path = tmp_path / "installed-cli"
    blocked_install_path.write_text("not a directory\n", encoding="utf-8")
    defaults = CliDefaults(
        plugin_root=defaults.plugin_root,
        target_dir=blocked_install_path / "target",
        plugins_root=defaults.plugins_root,
        profiles_root=defaults.profiles_root,
    )
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _, remote = _make_cli_publish_repo(
        tmp_path,
        monkeypatch,
        defaults=defaults,
    )

    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
    )
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", "publish_demo"]
    )

    assert exit_code == 0
    assert _run_git(remote, "tag", "--list") == "v0.1.0"
    assert len(client.requests) == 1


def test_publish_rejects_wrong_origin_even_when_github_repository_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "GITHUB_REPOSITORY",
        "neko/n.e.k.o_plugin_publish_demo",
    )
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)

    wrong_github_url = "https://github.com/neko/wrong-repository"
    _run_git(plugin_dir, "remote", "set-url", "origin", wrong_github_url)
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/wrong-repository/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", "publish_demo"]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    assert "market repository name must be" in capsys.readouterr().err


def test_publish_rejects_push_url_for_different_github_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, origin_remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    push_remote = tmp_path / "wrong-push.git"
    subprocess.run(
        ["git", "init", "--bare", str(push_remote)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    push_url = "https://github.com/neko/n.e.k.o_plugin_wrong_destination"
    _run_git(plugin_dir, "remote", "set-url", "--push", "origin", push_url)
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/"
            "n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(plugin_dir, "tag", "--list") == ""
    assert _run_git(origin_remote, "tag", "--list") == ""
    assert _run_git(push_remote, "tag", "--list") == ""
    assert client.requests == []
    assert "push URL must point to the same GitHub repository" in (
        capsys.readouterr().err
    )


def test_publish_accepts_same_repository_with_different_push_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    push_url = "git@github.com:neko/n.e.k.o_plugin_publish_demo.git"
    _run_git(plugin_dir, "remote", "set-url", "--push", "origin", push_url)
    release_url = (
        "https://github.com/neko/"
        "n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
    )
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 0
    assert _run_git(remote, "tag", "--list") == "v0.1.0"
    assert len(client.requests) == 1


def test_publish_rejects_multiple_push_urls_before_tagging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    first_push_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo.git"
    )
    second_push_url = (
        "git@github.com:neko/n.e.k.o_plugin_publish_demo.git"
    )
    _run_git(
        plugin_dir,
        "remote",
        "set-url",
        "--add",
        "--push",
        "origin",
        first_push_url,
    )
    _run_git(
        plugin_dir,
        "remote",
        "set-url",
        "--add",
        "--push",
        "origin",
        second_push_url,
    )
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/"
            "n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(plugin_dir, "tag", "--list") == ""
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    assert "at most one push URL" in capsys.readouterr().err


def test_publish_rejects_multiple_origin_urls_before_tagging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, origin_remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    other_remote = tmp_path / "other-origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(other_remote)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _run_git(plugin_dir, "push", f"file://{other_remote}", "main")
    other_url = "https://github.com/neko/n.e.k.o_plugin_other"
    expected_url = "https://github.com/neko/n.e.k.o_plugin_publish_demo"
    _run_git(plugin_dir, "remote", "set-url", "origin", other_url)
    _run_git(plugin_dir, "remote", "set-url", "--add", "origin", expected_url)
    _run_git(plugin_dir, "remote", "set-url", "--push", "origin", expected_url)
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/"
            "n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(plugin_dir, "tag", "--list") == ""
    assert _run_git(origin_remote, "tag", "--list") == ""
    assert _run_git(other_remote, "tag", "--list") == ""
    assert client.requests == []
    assert "exactly one fetch URL" in capsys.readouterr().err


@pytest.mark.parametrize("rewrite_key", ["insteadOf", "pushInsteadOf"])
def test_publish_rejects_rewritten_push_destination_before_tagging(
    rewrite_key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, origin_remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    origin_url = "https://github.com/neko/n.e.k.o_plugin_publish_demo"
    rewritten_url = "https://github.com/neko/n.e.k.o_plugin_wrong_destination"
    _run_git(
        plugin_dir,
        "config",
        f"url.{rewritten_url}.{rewrite_key}",
        origin_url,
    )
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/"
            "n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(plugin_dir, "tag", "--list") == ""
    assert _run_git(origin_remote, "tag", "--list") == ""
    assert client.requests == []
    assert "push URL must point to the same GitHub repository" in (
        capsys.readouterr().err
    )


def test_git_config_failure_has_trilingual_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_git_config(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", "config"], 2, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fail_git_config)

    with pytest.raises(RuntimeError) as exc_info:
        publish_cmd._git_config_values(tmp_path, "remote.origin.pushurl")

    message = str(exc_info.value)
    assert "failed" in message
    assert "执行失败" in message
    assert "実行に失敗しました" in message


def test_publish_defaults_to_github_then_market(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(
        _ready_release(release_url),
        httpx.Response(
            201,
            json={
                "status": "published",
                "version": {"version": "1.2.0"},
            },
        ),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setenv("GH_TOKEN", "github-token-must-not-reach-market")
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir)])

    assert exit_code == 0
    assert [request.get("method", "POST") for request in client.requests] == [
        "GET",
        "POST",
    ]
    assert client.requests[0]["headers"]["Authorization"] == (
        "Bearer github-token-must-not-reach-market"
    )
    assert client.requests[1] == {
        "url": "https://market.project-neko.cn/api/v1/release-publications",
        "json": {"release_url": release_url},
    }
    output = capsys.readouterr().out
    assert "[OK] GitHub Release ready:" in output
    assert "[OK] Market published v1.2.0" in output


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (201, {"status": "failed", "version": {"version": "1.2.0"}}),
        (200, {"status": "already_published", "version": {}}),
        (200, {"status": "published", "version": {"version": "1.2.0"}}),
    ],
)
def test_publish_market_rejects_malformed_success_response(
    status_code: int,
    payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_url = "https://github.com/neko/n.e.k.o_plugin_demo/releases/tag/v1.2.0"
    client = _RecordingClient(httpx.Response(status_code, json=payload))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(["publish", "market", release_url])

    assert exit_code == 1
    assert "unexpected response" in capsys.readouterr().err


def test_publish_github_reports_network_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        publish_cmd.httpx,
        "Client",
        lambda **_: _FailingGitHubClient(),
    )
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir), "--timeout", "0"]
    )

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "GitHub publication failed" in error
    assert "connection refused" in error
    assert "Traceback" not in error


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf"])
def test_publish_rejects_non_finite_timeout_at_cli_boundary(
    timeout: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main(
            ["publish", "github", "missing-plugin", f"--timeout={timeout}"]
        )

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "timeout must be finite" in error
    assert "GitHub publication failed" not in error


def test_publish_github_requires_head_to_be_pushed_before_tagging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    (plugin_dir / "README.md").write_text("local-only commit\n", encoding="utf-8")
    _run_git(plugin_dir, "add", "README.md")
    _run_git(plugin_dir, "commit", "-m", "local only")
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    error = capsys.readouterr().err
    assert "HEAD is not pushed" in error


def test_publish_github_rechecks_worktree_after_release_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    def dirty_worktree_during_preflight(_: object) -> int:
        (plugin_dir / "preflight-output.txt").write_text(
            "generated during preflight\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        dirty_worktree_during_preflight,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    assert "git working tree has uncommitted changes" in capsys.readouterr().err


@pytest.mark.parametrize("command", [["publish"], ["publish", "github"]])
def test_publish_stops_before_release_check_when_clean_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    calls: list[str] = []

    def fail_clean_sync(args: object) -> int:
        assert getattr(args, "plugin") == str(plugin_dir)
        assert getattr(args, "clean") is True
        assert getattr(args, "python") == sys.executable
        assert getattr(args, "discard_backups") is False
        calls.append("sync")
        return 1

    def unexpected_release_check(_: object) -> int:
        calls.append("release-check")
        return 0

    monkeypatch.setattr(publish_cmd.deps_cmd, "handle_sync", fail_clean_sync)
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        unexpected_release_check,
    )

    exit_code = neko_plugin_cli.main([*command, str(plugin_dir)])

    assert exit_code == 1
    assert calls == ["sync"]
    assert _run_git(remote, "tag", "--list") == ""
    assert "dependency sync did not pass" in capsys.readouterr().err


def test_publish_ignores_retained_dependency_recovery_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    backup = plugin_dir / ".vendor.backup-0a1b2c3d"
    backup.mkdir()
    (backup / "old.py").write_text("keep", encoding="utf-8")
    (plugin_dir / ".vendor.backup-0a1b2c3d.pending").touch()

    publish_cmd._ensure_clean_worktree(plugin_dir)

    # A plugin's own look-alike directory is plugin source and must be committed.
    look_alike = plugin_dir / ".vendor.backup-notes"
    look_alike.mkdir()
    (look_alike / "notes.md").write_text("mine", encoding="utf-8")
    with pytest.raises(RuntimeError):
        publish_cmd._ensure_clean_worktree(plugin_dir)


def test_release_ruff_excludes_dependency_sync_work_dirs(
    tmp_path: Path,
    release_ruff_process: dict[str, Any],
) -> None:
    publish_cmd._ensure_release_ruff_passes(tmp_path)

    command = release_ruff_process["calls"][0]["command"]
    excludes = command[command.index("--exclude") + 1].split(",")
    hex8 = "[0-9a-f]" * 8
    assert excludes == [
        "vendor",
        f"./.vendor.staging-{hex8}",
        f"./.vendor.backup-{hex8}",
        f"./.vendor.backup-{hex8}.pending",
    ]


def test_publish_stops_before_tag_when_ruff_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    release_ruff_process: dict[str, Any],
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    (plugin_dir / "ruff_failure.py").write_text(
        "print(undefined_name)\n",
        encoding="utf-8",
    )
    _run_git(plugin_dir, "add", "ruff_failure.py")
    _run_git(plugin_dir, "commit", "-m", "add ruff violation")
    _run_git(plugin_dir, "push", "origin", "main")
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    release_ruff_process["returncode"] = 1
    release_ruff_process["stdout"] = "ruff_failure.py:1:7: F821 Undefined name"

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    assert "Ruff check failed" in capsys.readouterr().err


def test_publish_reports_missing_uvx_before_tagging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    release_ruff_process: dict[str, Any],
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    release_ruff_process["exception"] = FileNotFoundError("uvx")

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    error = capsys.readouterr().err
    assert "uvx executable was not found" in error
    assert "Traceback" not in error


def test_publish_reports_ruff_timeout_before_tagging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    release_ruff_process: dict[str, Any],
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    client = _RecordingClient(
        _ready_release(
            "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    release_ruff_process["exception"] = subprocess.TimeoutExpired(
        ["uvx", "ruff==0.12.4"],
        120,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    assert release_ruff_process["calls"][0]["kwargs"]["timeout"] == 120
    error = capsys.readouterr().err
    assert "Ruff check timed out after 120 seconds" in error
    assert "Traceback" not in error


def test_publish_github_stops_before_tag_when_release_workflow_is_not_standard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    workflow = plugin_dir / ".github" / "workflows" / "release.yml"
    workflow.write_text("name: custom release\n", encoding="utf-8")
    _run_git(plugin_dir, "add", ".github/workflows/release.yml")
    _run_git(plugin_dir, "commit", "-m", "custom release workflow")
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    error = capsys.readouterr().err
    assert "standard release workflow is not current" in error
    assert "setup-repo" in error


def test_publish_can_resume_when_remote_tag_already_points_to_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(
        _ready_release(release_url),
        httpx.Response(
            201,
            json={"status": "published", "version": {"version": "1.2.0"}},
        ),
        _ready_release(release_url),
        httpx.Response(
            200,
            json={
                "status": "already_published",
                "version": {"version": "1.2.0"},
            },
        ),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    assert neko_plugin_cli.main(["publish", str(plugin_dir)]) == 0
    assert neko_plugin_cli.main(["publish", str(plugin_dir)]) == 0

    assert _run_git(remote, "tag", "--list") == "v1.2.0"
    assert "[OK] Market already published v1.2.0" in capsys.readouterr().out


def test_publish_rejects_remote_tag_that_points_to_another_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    _run_git(plugin_dir, "tag", "v1.2.0")
    _run_git(plugin_dir, "push", "origin", "refs/tags/v1.2.0")
    (plugin_dir / "README.md").write_text("new commit\n", encoding="utf-8")
    _run_git(plugin_dir, "add", "README.md")
    _run_git(plugin_dir, "commit", "-m", "new head")
    _run_git(plugin_dir, "push", "origin", "main")
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir)])

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == "v1.2.0"
    assert "remote tag v1.2.0 points to" in capsys.readouterr().err


def test_publish_does_not_notify_market_before_github_release_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    client = _RecordingClient(httpx.Response(404, json={"message": "Not Found"}))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", str(plugin_dir), "--timeout", "0"]
    )

    assert exit_code == 1
    assert len(client.requests) == 1
    assert client.requests[0]["method"] == "GET"
    assert "timed out waiting for GitHub Release" in capsys.readouterr().err


def test_publish_waits_until_all_release_assets_are_downloadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    incomplete_release = httpx.Response(
        200,
        json={
            "html_url": release_url,
            "assets": [
                    {
                        "name": "publish_demo.neko-plugin",
                        "state": "uploaded",
                        "size": 123,
                    "browser_download_url": f"{release_url}/download/package",
                }
            ],
        },
    )
    client = _RecordingClient(
        incomplete_release,
        _ready_release(release_url),
        httpx.Response(
            201,
            json={"status": "published", "version": {"version": "1.2.0"}},
        ),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    patch_module_clock(monkeypatch, publish_cmd, sleep=lambda _: None)
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", str(plugin_dir), "--timeout", "30"]
    )

    assert exit_code == 0
    assert [request.get("method", "POST") for request in client.requests] == [
        "GET",
        "GET",
        "POST",
    ]
    assert "[OK] Market published v1.2.0" in capsys.readouterr().out


def test_publish_does_not_notify_market_for_incomplete_release_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(
        httpx.Response(200, json={"html_url": release_url, "assets": []})
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", str(plugin_dir), "--timeout", "0"]
    )

    assert exit_code == 1
    assert len(client.requests) == 1
    error = capsys.readouterr().err
    assert "timed out waiting for GitHub Release assets" in error
    assert "publish_demo.neko-plugin" in error
    assert "market-evidence.json" in error


def test_release_asset_is_ready_only_after_github_marks_it_uploaded() -> None:
    asset = {
        "name": "publish_demo.neko-plugin",
        "state": "open",
        "size": 123,
        "browser_download_url": "https://example.invalid/publish_demo.neko-plugin",
    }

    assert publish_cmd._release_asset_is_downloadable(asset) is False

    asset["state"] = "uploaded"
    assert publish_cmd._release_asset_is_downloadable(asset) is True


def test_publish_market_surfaces_stable_market_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_url = "https://github.com/neko/n.e.k.o_plugin_demo/releases/tag/v1.2.0"
    client = _RecordingClient(
        httpx.Response(
            422,
            json={
                "code": "release_verification_rejected",
                "detail": "GitHub release 未提供可验证的标准发布证据",
            },
        )
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(["publish", "market", release_url])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "release_verification_rejected" in error
    assert "未提供可验证的标准发布证据" in error


@pytest.mark.parametrize("mode", [[], ["github"]])
def test_publish_upgrade_commits_only_configuration_then_tags_and_notifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: list[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    workflow = plugin_dir / ".github/workflows/release.yml"
    # A recognized older template can be upgraded together with missing files.
    workflow.write_text("\n".join(workflow.read_text(encoding="utf-8").splitlines()[2:]) + "\n", encoding="utf-8")
    _run_git(plugin_dir, "add", ".github/workflows/release.yml")
    _run_git(plugin_dir, "commit", "-m", "older managed template")
    _run_git(plugin_dir, "push", "origin", "main")
    original_head = _run_git(plugin_dir, "rev-parse", "HEAD")
    release_url = "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    client = _RecordingClient(
        _ready_release(release_url),
        httpx.Response(201, json={"status": "published", "version": {"version": "1.2.0"}}),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    assert neko_plugin_cli.main(["publish", *mode, str(plugin_dir), "--upgrade-github-actions"]) == 0

    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    assert _run_git(plugin_dir, "rev-parse", "HEAD^") == original_head
    assert set(_run_git(plugin_dir, "diff", "--name-only", original_head, head).splitlines()) == {
        "ruff.toml", ".github/workflows/verify.yml", ".github/workflows/release.yml",
    }
    assert _run_git(remote, "rev-parse", "refs/heads/main") == head
    assert _run_git(remote, "rev-parse", "refs/tags/v1.2.0") == head
    assert _run_git(plugin_dir, "status", "--porcelain") == ""
    assert len(client.requests) == (1 if mode else 2)


def test_publish_upgrade_is_idempotent_when_configuration_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    client = _RecordingClient(*[_ready_release(
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"
    ) for _ in range(2)])
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    for _ in range(2):
        assert neko_plugin_cli.main(["publish", "github", str(plugin_dir), "--upgrade-github-actions"]) == 0
    assert _run_git(plugin_dir, "rev-parse", "HEAD") == head
    assert _run_git(remote, "rev-parse", "refs/tags/v0.1.0") == head


@pytest.mark.parametrize("path", ["ruff.toml", ".github/workflows/verify.yml", ".github/workflows/release.yml"])
def test_publish_upgrade_conflict_does_not_change_any_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    (plugin_dir / path).write_text("# author's custom configuration\n", encoding="utf-8")
    _run_git(plugin_dir, "add", "--", path)
    _run_git(plugin_dir, "commit", "-m", "custom configuration")
    _run_git(plugin_dir, "push", "origin", "main")
    head = _run_git(plugin_dir, "rev-parse", "HEAD")

    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 1
    assert "custom configuration conflicts" in capsys.readouterr().err
    assert _run_git(plugin_dir, "rev-parse", "HEAD") == head
    assert _run_git(plugin_dir, "status", "--porcelain") == ""
    assert _run_git(remote, "tag", "--list") == ""


@pytest.mark.parametrize("state", ["untracked", "staged", "unpushed", "tagged"])
def test_publish_upgrade_stops_before_writing_on_unready_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    if state == "tagged":
        _run_git(plugin_dir, "tag", "v1.2.0")
    else:
        (plugin_dir / "README.md").write_text("author's change\n", encoding="utf-8")
        if state in {"staged", "unpushed"}:
            _run_git(plugin_dir, "add", "README.md")
        if state == "unpushed":
            _run_git(plugin_dir, "commit", "-m", "author's unpushed change")
    status = _run_git(plugin_dir, "status", "--porcelain")
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    remote_head = _run_git(remote, "rev-parse", "refs/heads/main")

    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 1
    assert not (plugin_dir / "ruff.toml").exists()
    assert not (plugin_dir / ".github/workflows/verify.yml").exists()
    assert _run_git(plugin_dir, "status", "--porcelain") == status
    assert _run_git(plugin_dir, "rev-parse", "HEAD") == head
    assert _run_git(remote, "rev-parse", "refs/heads/main") == remote_head
    assert _run_git(remote, "tag", "--list") == ""


@pytest.mark.parametrize("failure", ["sync", "tests", "ruff", "unrelated-write", "configuration-write"])
def test_publish_upgrade_never_commits_or_pushes_failed_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, release_ruff_process: dict[str, Any],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    head = _run_git(plugin_dir, "rev-parse", "HEAD")

    def check(_: object) -> int:
        if failure == "unrelated-write":
            (plugin_dir / "output.txt").write_text("test output\n", encoding="utf-8")
        if failure == "configuration-write":
            (plugin_dir / "ruff.toml").write_text("# changed during tests\n", encoding="utf-8")
        return 1 if failure == "tests" else 0

    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", check)
    monkeypatch.setattr(publish_cmd.deps_cmd, "handle_sync", lambda _: 1 if failure == "sync" else 0)
    if failure == "ruff":
        release_ruff_process["returncode"] = 1

    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 1
    assert _run_git(plugin_dir, "rev-parse", "HEAD") == head
    assert _run_git(remote, "rev-parse", "refs/heads/main") == head
    assert _run_git(remote, "tag", "--list") == ""
    assert _run_git(plugin_dir, "diff", "--cached", "--name-only") == ""
    assert (plugin_dir / "ruff.toml").exists()


def test_publish_market_rejects_workflow_upgrade_without_network_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    client = _RecordingClient()
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    assert neko_plugin_cli.main([
        "publish", "market", "https://github.com/neko/demo/releases/tag/v1.0.0", "--upgrade-github-actions",
    ]) == 1
    assert "requires a plugin repository" in capsys.readouterr().err
    assert client.requests == []


def test_publish_upgrade_runs_real_generated_plugin_checks_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, remote = _make_cli_publish_repo(tmp_path, monkeypatch)
    _run_git(plugin_dir, "rm", ".github/workflows/verify.yml")
    _run_git(plugin_dir, "commit", "-m", "missing verification workflow")
    _run_git(plugin_dir, "push", "origin", "main")
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    client = _RecordingClient(
        _ready_release("https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v0.1.0"),
        httpx.Response(202, json={"status": "pending_review", "version": None, "candidate": {
            "id": 12, "plugin_id": 20, "version": "0.1.0", "channel": "stable", "status": "pending_review",
        }}),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 0
    new_head = _run_git(plugin_dir, "rev-parse", "HEAD")
    assert _run_git(plugin_dir, "diff", "--name-only", head, new_head) == ".github/workflows/verify.yml"
    assert _run_git(remote, "rev-parse", "refs/tags/v0.1.0") == new_head


@pytest.mark.parametrize("status", ["pending_review", "rejected", "superseded"])
def test_publish_market_reports_valid_review_receipts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str,
) -> None:
    client = _RecordingClient(httpx.Response(202, json={"status": status, "version": None, "candidate": {
        "id": 12, "plugin_id": 20, "version": "1.2.0", "channel": "stable", "status": status,
    }}))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    result = neko_plugin_cli.main(["publish", "market", "https://github.com/neko/demo/releases/tag/v1.2.0"])
    captured = capsys.readouterr()
    assert result == (0 if status == "pending_review" else 1)
    assert "Market published" not in captured.out
    assert "#12" in captured.out + captured.err


@pytest.mark.parametrize("candidate", [None, {}, {"id": True}, {
    "id": 12, "plugin_id": 20, "version": "1.2.0", "channel": [], "status": "pending_review",
}])
def test_publish_market_rejects_malformed_review_receipts(
    monkeypatch: pytest.MonkeyPatch, candidate: object,
) -> None:
    client = _RecordingClient(httpx.Response(202, json={"status": "pending_review", "candidate": candidate}))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    assert neko_plugin_cli.main(["publish", "market", "https://github.com/neko/demo/releases/tag/v1.2.0"]) == 1


@pytest.mark.parametrize("retry_after", ["3", "120", "invalid", "nan", "Sun, 01 Jan 2023 00:02:00 GMT"])
@pytest.mark.parametrize("pending_count", [1, 2])
def test_publish_market_retries_only_pending_verification(
    monkeypatch: pytest.MonkeyPatch, retry_after: str, pending_count: int,
) -> None:
    client = _RecordingClient(
        *[httpx.Response(503, headers={"Retry-After": retry_after}, json={"code": "release_verification_pending"}) for _ in range(pending_count)],
        httpx.Response(201, json={"status": "published", "version": {"version": "1.2.0"}}),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    sleeps: list[float] = []
    patch_module_clock(monkeypatch, publish_cmd, sleep=sleeps.append, time=lambda: 1672531200.0)
    assert neko_plugin_cli.main(["publish", "market", "https://github.com/neko/demo/releases/tag/v1.2.0"]) == 0
    expected = 120.0 if retry_after.endswith("GMT") else float(retry_after) if retry_after in {"3", "120"} else 5.0
    assert sleeps == [expected] * pending_count
    assert len(client.requests) == pending_count + 1
    assert all(item == client.requests[0] for item in client.requests)


def test_publish_market_pending_wait_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient(*[httpx.Response(503, json={"code": "release_verification_pending"}) for _ in range(2)])
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd, "_MARKET_WAIT_SECONDS", 0)
    assert neko_plugin_cli.main(["publish", "market", "https://github.com/neko/demo/releases/tag/v1.2.0"]) == 1
    assert len(client.requests) == 1


def test_publish_market_does_not_send_again_after_sleep_reaches_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient(httpx.Response(503, headers={"Retry-After": "120"}, json={"code": "release_verification_pending"}))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd, "_MARKET_WAIT_SECONDS", 2)
    clock = [0.0]
    def sleep(delay: float) -> None:
        clock[0] += delay
    patch_module_clock(monkeypatch, publish_cmd, monotonic=lambda: clock[0], sleep=sleep)
    assert neko_plugin_cli.main(["publish", "market", "https://github.com/neko/demo/releases/tag/v1.2.0"]) == 1
    assert clock == [2.0]
    assert len(client.requests) == 1


def test_publish_market_does_not_retry_unrelated_service_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient(httpx.Response(503, json={"code": "service_unavailable"}))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    assert neko_plugin_cli.main(["publish", "market", "https://github.com/neko/demo/releases/tag/v1.2.0"]) == 1
    assert len(client.requests) == 1


def test_publish_upgrade_rejected_push_preserves_local_commit_without_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    _run_git(remote, "config", "receive.denyCurrentBranch", "refuse")
    _run_git(remote, "config", "core.bare", "false")
    _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)
    client = _RecordingClient()
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 1
    assert "Configuration commit" in capsys.readouterr().err
    new_head = _run_git(plugin_dir, "rev-parse", "HEAD")
    assert new_head != head
    assert _run_git(plugin_dir, "rev-parse", "HEAD^") == head
    assert _run_git(remote, "rev-parse", "refs/heads/main") == head
    assert _run_git(remote, "tag", "--list") == ""
    assert client.requests == []
    # The user can recover with a normal push, without resetting or re-running tests.
    _run_git(remote, "config", "core.bare", "true")
    _run_git(plugin_dir, "push", "origin", "main")
    assert _run_git(remote, "rev-parse", "refs/heads/main") == new_head


def test_publish_upgrade_does_not_push_a_branch_advanced_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    # A real competing commit advances origin while the release checks run.
    competitor = tmp_path / "competitor"
    _run_git(tmp_path, "clone", str(remote), str(competitor))
    _run_git(competitor, "checkout", "main")
    _run_git(competitor, "config", "user.name", "Other Author")
    _run_git(competitor, "config", "user.email", "other@example.com")

    def check(_: object) -> int:
        (competitor / "README.md").write_text("remote advanced\n", encoding="utf-8")
        _run_git(competitor, "add", "README.md")
        _run_git(competitor, "commit", "-m", "other author's change")
        _run_git(competitor, "push", "origin", "main")
        return 0

    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", check)
    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 1
    assert _run_git(plugin_dir, "rev-parse", "HEAD") == head
    assert _run_git(remote, "rev-parse", "refs/heads/main") == _run_git(competitor, "rev-parse", "HEAD")
    assert _run_git(remote, "tag", "--list") == ""


def test_publish_upgrade_does_not_switch_push_destination_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    _run_git(plugin_dir, "push", "origin", "HEAD:refs/heads/other")
    _run_git(plugin_dir, "update-ref", "refs/remotes/origin/other", head)
    def check(_: object) -> int:
        _run_git(plugin_dir, "branch", "--set-upstream-to=origin/other", "main")
        return 0
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", check)

    assert neko_plugin_cli.main(["publish", str(plugin_dir), "--upgrade-github-actions"]) == 1
    assert _run_git(plugin_dir, "rev-parse", "HEAD") == head
    assert _run_git(remote, "rev-parse", "refs/heads/main") == head
    assert _run_git(remote, "rev-parse", "refs/heads/other") == head
    assert _run_git(remote, "tag", "--list") == ""

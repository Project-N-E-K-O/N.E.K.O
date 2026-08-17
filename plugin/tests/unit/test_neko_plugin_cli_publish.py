from __future__ import annotations

from typing import Any
import subprocess
from pathlib import Path

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


def test_publish_stops_before_release_check_when_clean_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    calls: list[str] = []

    def fail_clean_sync(args: object) -> int:
        assert getattr(args, "plugin") == str(plugin_dir)
        assert getattr(args, "clean") is True
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

    exit_code = neko_plugin_cli.main(
        ["publish", "github", str(plugin_dir)]
    )

    assert exit_code == 1
    assert calls == ["sync"]
    assert _run_git(remote, "tag", "--list") == ""
    assert "dependency sync did not pass" in capsys.readouterr().err


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

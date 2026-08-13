from __future__ import annotations

from typing import Any
import subprocess
from pathlib import Path

import httpx
import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import publish_cmd
from plugin.neko_plugin_cli.templates.generator import (
    PluginSpec,
    render_release_workflow,
)

pytestmark = pytest.mark.plugin_unit


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


def _make_publish_repo(tmp_path: Path) -> tuple[Path, Path]:
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
    _run_git(plugin_dir, "remote", "add", "origin", github_url)
    _run_git(
        plugin_dir,
        "config",
        f"url.file://{remote}.insteadOf",
        github_url,
    )
    _run_git(plugin_dir, "push", "-u", "origin", "main")
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
    plugin_dir, remote = _make_publish_repo(tmp_path)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(
        httpx.Response(200, json={"html_url": release_url})
    )
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


def test_publish_defaults_to_github_then_market(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(
        httpx.Response(200, json={"html_url": release_url}),
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
    plugin_dir, _ = _make_publish_repo(tmp_path)
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


def test_publish_github_requires_head_to_be_pushed_before_tagging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path)
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


def test_publish_github_stops_before_tag_when_release_workflow_is_not_standard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path)
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
    plugin_dir, remote = _make_publish_repo(tmp_path)
    release_url = (
        "https://github.com/neko/n.e.k.o_plugin_publish_demo/releases/tag/v1.2.0"
    )
    client = _RecordingClient(
        httpx.Response(200, json={"html_url": release_url}),
        httpx.Response(
            201,
            json={"status": "published", "version": {"version": "1.2.0"}},
        ),
        httpx.Response(200, json={"html_url": release_url}),
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
    plugin_dir, remote = _make_publish_repo(tmp_path)
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
    plugin_dir, _ = _make_publish_repo(tmp_path)
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

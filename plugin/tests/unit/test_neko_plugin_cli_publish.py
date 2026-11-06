from __future__ import annotations

from typing import Any
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.utils.github_release import release_asset_is_downloadable
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


_real_gh_auth_token = publish_cmd._gh_auth_token


@pytest.fixture(autouse=True)
def offline_github(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never read the developer's real gh login or reach GitHub in tests; a
    # test that expects HTTP traffic installs its own client.
    monkeypatch.setattr(publish_cmd, "_gh_auth_token", lambda: None)
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: _RecordingClient())


_REPOSITORY_API_RE = re.compile(r"https://api\.github\.com/repos/[^/]+/[^/]+")


class _RecordingClient:
    """Fake httpx client.

    Release lookups and Market posts are answered from ``responses`` and
    recorded in ``requests``; an exception in ``responses`` is raised instead.
    The repository visibility lookup and the release workflow run lookup are
    answered from ``repository`` / ``runs`` (one per lookup, the last one
    repeated) and recorded in ``github_reads``.
    """

    def __init__(
        self,
        *responses: httpx.Response | Exception,
        repository: httpx.Response | None = None,
        runs: httpx.Response | list[httpx.Response] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.github_reads: list[str] = []
        self.repository = repository or httpx.Response(200, json={"private": False})
        if runs is None:
            runs = _successful_runs()
        self.runs = runs if isinstance(runs, list) else [runs]

    def __enter__(self) -> _RecordingClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _next(self) -> httpx.Response:
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        return self._next()

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if _REPOSITORY_API_RE.fullmatch(url):
            self.github_reads.append(url)
            return self.repository
        if "/actions/workflows/release.yml/runs" in url:
            self.github_reads.append(url)
            return self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
        self.requests.append({"method": "GET", "url": url, **kwargs})
        return self._next()


def _workflow_runs(*runs: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": list(runs)})


def _run(
    tag: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    event: str = "push",
) -> dict[str, Any]:
    return {
        "id": 42,
        "event": event,
        "head_branch": tag,
        "status": status,
        "conclusion": conclusion,
        "path": ".github/workflows/release.yml",
        "html_url": "https://github.com/neko/n.e.k.o_plugin_publish_demo/actions/runs/42",
    }


def _successful_runs() -> httpx.Response:
    # The tests publish v1.2.0 (hand-made plugin.toml) or v0.1.0 (init scaffold).
    return _workflow_runs(_run("v1.2.0"), _run("v0.1.0"))


class _FailingGitHubClient(_RecordingClient):
    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("connection refused", request=request)


def _market_version(version: str = "1.2.0") -> dict[str, Any]:
    # Fields of the Market's Version schema (app/schemas/version.py) the CLI reads plus context.
    return {
        "id": 12,
        "plugin_id": 3,
        "version": version,
        "channel": "stable",
        "is_latest": True,
        "release_tag": version,
        "release_url": f"https://github.com/neko/n.e.k.o_plugin_demo/releases/tag/v{version}",
        "verification_status": "passed",
    }


def _market_published() -> dict[str, Any]:
    return {"status": "published", "version": _market_version(), "candidate": None}


def _market_review(status: str) -> dict[str, Any]:
    # ReleaseReviewReceipt (app/schemas/release_review.py).
    return {
        "status": status,
        "version": None,
        "candidate": {
            "id": 7,
            "plugin_id": 3,
            "version": "1.2.0",
            "channel": "stable",
            "status": status,
        },
    }


def _market_pending(code: str = "release_verification_pending", *, retry_after: str = "5") -> httpx.Response:
    # VersionDomainError handler (app/main.py) with Retry-After.
    return httpx.Response(
        503,
        headers={"Retry-After": retry_after},
        json={
            "detail": "Release verification is queued or running. Retry shortly.",
            "code": code,
            "issues": None,
            "verification_job_id": 41,
        },
    )


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
    response = httpx.Response(201, json=_market_published())
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
    output = capsys.readouterr().out
    assert "[MARKET] status=published\n" in output
    assert "[OK] Market published v1.2.0" in output


_MARKET_RELEASE_URL = "https://github.com/neko/n.e.k.o_plugin_demo/releases/tag/v1.2.0"


@pytest.mark.parametrize(
    ("response", "exit_code", "market_line", "message"),
    [
        (
            httpx.Response(200, json={"status": "already_published", "version": _market_version()}),
            0,
            "[MARKET] status=already_published\n",
            "[OK] Market already published v1.2.0",
        ),
        (
            httpx.Response(202, json=_market_review("pending_review")),
            0,
            "[MARKET] status=pending_review candidate=7\n",
            "waiting for a Market reviewer",
        ),
        (
            httpx.Response(202, json=_market_review("rejected")),
            1,
            "[MARKET] status=rejected candidate=7\n",
            "publish a new version",
        ),
    ],
)
def test_publish_market_reports_the_market_status(
    response: httpx.Response,
    exit_code: int,
    market_line: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _RecordingClient(response)
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    assert neko_plugin_cli.main(["publish", "market", _MARKET_RELEASE_URL]) == exit_code

    captured = capsys.readouterr()
    assert captured.out.count("[MARKET]") == 1
    assert market_line in captured.out
    assert message in captured.out + captured.err


@pytest.mark.parametrize(
    ("first", "expected_sleep"),
    [
        (_market_pending(), 5.0),
        (_market_pending("release_verification_busy", retry_after="120"), 60.0),
        (httpx.Response(429, headers={"Retry-After": "2"}, json={"detail": "Too Many Requests"}), 2.0),
        (_market_pending(retry_after="soon"), 5.0),
        (httpx.ConnectError("connection refused"), 5.0),
    ],
)
def test_publish_market_retries_the_same_body_while_the_market_is_verifying(
    first: httpx.Response | Exception,
    expected_sleep: float,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _RecordingClient(first, httpx.Response(201, json=_market_published()))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    sleeps: list[float] = []
    patch_module_clock(monkeypatch, publish_cmd, monotonic=lambda: 0.0, sleep=sleeps.append)

    assert neko_plugin_cli.main(["publish", "market", _MARKET_RELEASE_URL]) == 0

    assert sleeps == [expected_sleep]
    assert [request["json"] for request in client.requests] == [
        {"release_url": _MARKET_RELEASE_URL},
        {"release_url": _MARKET_RELEASE_URL},
    ]
    output = capsys.readouterr().out
    assert "[WAIT] Market is still verifying the release" in output
    assert "[MARKET] status=published" in output


def test_publish_market_gives_up_when_the_wait_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _RecordingClient(*(_market_pending() for _ in range(3)))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    patch_module_clock(monkeypatch, publish_cmd, monotonic=lambda: clock["now"], sleep=sleep)

    exit_code = neko_plugin_cli.main(
        ["publish", "market", _MARKET_RELEASE_URL, "--market-wait", "8"]
    )

    assert exit_code == 1
    assert sleeps == [5.0, 3.0]
    assert len(client.requests) == 3
    captured = capsys.readouterr()
    # The verifying message is printed once, not on every retry.
    assert captured.out.count("[WAIT]") == 1
    assert "[MARKET]" not in captured.out
    assert "[FAIL]" in captured.err
    assert "release_verification_pending" in captured.err
    assert "still not accepted after 8 s" in captured.err


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
    assert client.github_reads[0] == (
        "https://api.github.com/repos/neko/n.e.k.o_plugin_publish_demo"
    )
    output = capsys.readouterr().out
    assert "[WAIT] waiting for GitHub Release v1.2.0" in output
    assert release_url in output


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


def test_publish_judges_the_repository_name_by_origin_and_only_warns(
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

    other_github_url = "https://github.com/neko/other-repository"
    _run_git(plugin_dir, "remote", "set-url", "origin", other_github_url)
    release_url = "https://github.com/neko/other-repository/releases/tag/v0.1.0"
    client = _RecordingClient(_ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)

    exit_code = neko_plugin_cli.main(
        ["publish", "github", "publish_demo"]
    )

    # The Market does not require the n.e.k.o_plugin_<id> name.
    assert exit_code == 0
    assert _run_git(remote, "tag", "--list") == "v0.1.0"
    assert client.github_reads[0] == "https://api.github.com/repos/neko/other-repository"
    assert (
        "recommended market repository name is n.e.k.o_plugin_publish_demo, "
        "got other-repository"
    ) in capsys.readouterr().out


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
        (202, {"status": "pending_review", "version": None, "candidate": None}),
        (202, {"status": "published", "version": None, "candidate": {"id": 7}}),
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
    error = capsys.readouterr().err
    assert "git working tree has uncommitted changes" in error
    assert "preflight-output.txt" in error


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
    # Publishing keeps respecting gitignore; precheck follows the stricter CI scan.
    assert "--no-respect-gitignore" not in command
    assert command[-1] == "."
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
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: _RecordingClient())
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir)])

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == "v1.2.0"
    error = capsys.readouterr().err
    assert "remote tag v1.2.0 points to" in error
    assert "a new version number is needed" in error
    assert "bump the version in plugin.toml" in error
    # The Market keeps every version it has seen; moving the tag cannot help.
    assert "git push origin :refs/tags/v1.2.0" not in error


def test_publish_rejects_local_tag_on_another_commit_with_recovery_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    _run_git(plugin_dir, "tag", "v1.2.0")
    (plugin_dir / "README.md").write_text("new commit\n", encoding="utf-8")
    _run_git(plugin_dir, "add", "README.md")
    _run_git(plugin_dir, "commit", "-m", "new head")
    _run_git(plugin_dir, "push", "origin", "main")
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: _RecordingClient())
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(["publish", "github", str(plugin_dir)])

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    error = capsys.readouterr().err
    assert "local tag v1.2.0 points to" in error
    assert "git tag -d v1.2.0" in error


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
    sleeps: list[float] = []
    patch_module_clock(
        monkeypatch, publish_cmd, monotonic=lambda: 0.0, sleep=sleeps.append
    )
    monkeypatch.setattr(
        publish_cmd.release_cmd,
        "handle_release_check",
        lambda _: 0,
    )

    exit_code = neko_plugin_cli.main(
        ["publish", str(plugin_dir), "--timeout", "30"]
    )

    assert exit_code == 0
    assert sleeps == [15.0]
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

    assert release_asset_is_downloadable(asset) is False

    asset["state"] = "uploaded"
    assert release_asset_is_downloadable(asset) is True


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



_PUBLISH_REPOSITORY = "neko/n.e.k.o_plugin_publish_demo"


def test_github_headers_fall_back_to_gh_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(publish_cmd, "_gh_auth_token", lambda: "gh-login-token")

    assert publish_cmd._github_headers()["Authorization"] == "Bearer gh-login-token"

    monkeypatch.setenv("GH_TOKEN", "env-token")
    assert publish_cmd._github_headers()["Authorization"] == "Bearer env-token"


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [(0, "gho_token\n", "gho_token"), (1, "", None), (0, "\n", None)],
)
def test_gh_auth_token_reads_the_gh_login(
    returncode: int,
    stdout: str,
    expected: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(publish_cmd.subprocess, "run", run)

    assert _real_gh_auth_token() == expected
    assert calls[0]["command"] == ["gh", "auth", "token"]
    assert calls[0]["encoding"] == "utf-8"


def test_gh_auth_token_is_none_without_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(publish_cmd.subprocess, "run", run)

    assert _real_gh_auth_token() is None


def _rate_limited(reset: int) -> httpx.Response:
    return httpx.Response(
        403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
        json={"message": "API rate limit exceeded"},
    )


def test_wait_for_release_waits_out_a_rate_limit_inside_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_url = f"https://github.com/{_PUBLISH_REPOSITORY}/releases/tag/v1.2.0"
    client = _RecordingClient(_rate_limited(1010), _ready_release(release_url))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    sleeps: list[float] = []
    patch_module_clock(
        monkeypatch,
        publish_cmd,
        monotonic=lambda: 0.0,
        time=lambda: 1000.0,
        sleep=sleeps.append,
    )

    assert (
        publish_cmd._wait_for_release(
            _PUBLISH_REPOSITORY,
            plugin_id="publish_demo",
            tag="v1.2.0",
            head="a" * 40,
            headers={},
            timeout=60,
        )
        == release_url
    )
    assert sleeps == [11.0]


def test_wait_for_release_reports_a_rate_limit_that_outlasts_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient(_rate_limited(4600))
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    sleeps: list[float] = []
    patch_module_clock(
        monkeypatch,
        publish_cmd,
        monotonic=lambda: 0.0,
        time=lambda: 1000.0,
        sleep=sleeps.append,
    )

    with pytest.raises(RuntimeError, match="rate limit") as exc_info:
        publish_cmd._wait_for_release(
            _PUBLISH_REPOSITORY,
            plugin_id="publish_demo",
            tag="v1.2.0",
            head="a" * 40,
            headers={},
            timeout=60,
        )

    assert "gh auth login" in str(exc_info.value)
    assert sleeps == []


def test_a_plain_403_is_not_treated_as_a_rate_limit() -> None:
    assert publish_cmd._rate_limit_wait(httpx.Response(403, json={})) is None
    assert (
        publish_cmd._rate_limit_wait(
            httpx.Response(429, headers={"retry-after": "7"}, json={})
        )
        == 7.0
    )


@pytest.mark.parametrize(
    ("repository_response", "message"),
    [
        (httpx.Response(200, json={"private": True}), "is private"),
        (httpx.Response(404, json={"message": "Not Found"}), "is not visible"),
    ],
)
def test_publish_refuses_a_repository_the_market_cannot_read_before_tagging(
    repository_response: httpx.Response,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, remote = _make_publish_repo(tmp_path, monkeypatch)
    client = _RecordingClient(repository=repository_response)
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    exit_code = neko_plugin_cli.main(["publish", "github", str(plugin_dir)])

    assert exit_code == 1
    assert _run_git(remote, "tag", "--list") == ""
    assert _run_git(plugin_dir, "tag", "--list") == ""
    assert client.requests == []
    error = capsys.readouterr().err
    assert message in error
    assert "Market needs a public repository" in error


def test_publish_fails_fast_when_the_release_workflow_run_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    run_url = f"https://github.com/{_PUBLISH_REPOSITORY}/actions/runs/42"
    client = _RecordingClient(
        httpx.Response(404, json={"message": "Not Found"}),
        runs=_workflow_runs(_run("v1.2.0", conclusion="failure")),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    sleeps: list[float] = []
    patch_module_clock(
        monkeypatch, publish_cmd, monotonic=lambda: 0.0, sleep=sleeps.append
    )
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir), "--timeout", "900"])

    assert exit_code == 1
    assert sleeps == []
    head = _run_git(plugin_dir, "rev-parse", "HEAD")
    assert any(f"head_sha={head}" in url for url in client.github_reads)
    error = capsys.readouterr().err
    assert "concluded failure" in error
    assert run_url in error


def test_publish_waits_for_the_release_workflow_run_to_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    release_url = f"https://github.com/{_PUBLISH_REPOSITORY}/releases/tag/v1.2.0"
    client = _RecordingClient(
        # The assets are uploaded before the run's last step finishes.
        _ready_release(release_url),
        _ready_release(release_url),
        httpx.Response(201, json=_market_published()),
        runs=[
            _workflow_runs(
                # Another tag on the same commit does not decide this release.
                _run("v1.1.0", conclusion="failure"),
                _run("v1.2.0", status="in_progress", conclusion=None),
            ),
            _workflow_runs(_run("v1.2.0")),
        ],
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    sleeps: list[float] = []
    patch_module_clock(monkeypatch, publish_cmd, monotonic=lambda: 0.0, sleep=sleeps.append)
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir), "--timeout", "900"])

    assert exit_code == 0
    assert sleeps == [15.0]
    assert [request.get("method", "POST") for request in client.requests] == [
        "GET",
        "GET",
        "POST",
    ]
    assert "[MARKET] status=published" in capsys.readouterr().out


def test_publish_times_out_while_the_release_workflow_run_is_unfinished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    release_url = f"https://github.com/{_PUBLISH_REPOSITORY}/releases/tag/v1.2.0"
    client = _RecordingClient(
        _ready_release(release_url),
        runs=_workflow_runs(_run("v1.2.0", status="in_progress", conclusion=None)),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir), "--timeout", "0"])

    assert exit_code == 1
    assert [request.get("method", "POST") for request in client.requests] == ["GET"]
    error = capsys.readouterr().err
    assert "timed out waiting for the release workflow run for v1.2.0" in error
    assert "in_progress" in error


def test_publish_fails_fast_when_a_dispatched_release_run_on_the_tag_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    release_url = f"https://github.com/{_PUBLISH_REPOSITORY}/releases/tag/v1.2.0"
    client = _RecordingClient(
        _ready_release(release_url),
        runs=_workflow_runs(
            # Newest first: a manual re-run on the tag decides over the push run.
            _run("v1.2.0", conclusion="failure", event="workflow_dispatch"),
            _run("v1.2.0"),
            # A dispatch from a branch is not a release run for the tag.
            _run("main", conclusion="success", event="workflow_dispatch"),
        ),
    )
    monkeypatch.setattr(publish_cmd.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(publish_cmd.release_cmd, "handle_release_check", lambda _: 0)

    exit_code = neko_plugin_cli.main(["publish", str(plugin_dir), "--timeout", "900"])

    assert exit_code == 1
    assert not any("event=" in url for url in client.github_reads)
    assert "concluded failure" in capsys.readouterr().err


def test_publish_names_the_upstream_command_when_branch_has_no_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    _run_git(plugin_dir, "branch", "--unset-upstream")
    head = _run_git(plugin_dir, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="has no upstream") as exc_info:
        publish_cmd._ensure_head_pushed(plugin_dir, head=head)

    assert "git push -u origin main" in str(exc_info.value)


def test_publish_reports_a_detached_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    _run_git(plugin_dir, "checkout", "--detach")
    head = _run_git(plugin_dir, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="HEAD is detached"):
        publish_cmd._ensure_head_pushed(plugin_dir, head=head)


def test_publish_rejects_an_upstream_on_another_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    other = tmp_path / "other.git"
    subprocess.run(
        ["git", "init", "--bare", str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(plugin_dir, "remote", "add", "fork", f"file://{other}")
    _run_git(plugin_dir, "push", "-u", "fork", "main")
    head = _run_git(plugin_dir, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="tracks fork/main") as exc_info:
        publish_cmd._ensure_head_pushed(plugin_dir, head=head)

    assert "git push -u origin main" in str(exc_info.value)


def test_publish_lists_untracked_files_that_block_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    (plugin_dir / "notes.txt").write_text("todo\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted changes: notes.txt"):
        publish_cmd._ensure_clean_worktree(plugin_dir)


def test_publish_keeps_a_multi_line_git_error_on_the_fail_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_: Any, **__: Any) -> str:
        raise RuntimeError(
            "To https://github.com/o/r.git\n"
            " ! [rejected]        v1.2.0 -> v1.2.0 (already exists)\n"
            "error: failed to push some refs\n"
        )

    monkeypatch.setattr(publish_cmd, "_publish_github", fail)

    assert neko_plugin_cli.main(["publish", "github", "."]) == 1

    fail_lines = [
        line for line in capsys.readouterr().err.splitlines() if "[FAIL]" in line
    ]
    assert len(fail_lines) == 1
    assert fail_lines[0].endswith(
        "To https://github.com/o/r.git | ! [rejected]        v1.2.0 -> v1.2.0 "
        "(already exists) | error: failed to push some refs"
    )


def test_publish_decodes_git_and_ruff_output_as_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_ruff_process: dict[str, Any],
) -> None:
    plugin_dir, _ = _make_publish_repo(tmp_path, monkeypatch)
    with (plugin_dir / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write("[neko]\n\tnote = 插件发布\n")

    assert publish_cmd._git(plugin_dir, "config", "neko.note") == "插件发布"

    publish_cmd._ensure_release_ruff_passes(plugin_dir)
    kwargs = release_ruff_process["calls"][0]["kwargs"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"

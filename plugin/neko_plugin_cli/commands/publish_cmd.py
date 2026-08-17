"""Publish a plugin through GitHub Releases and the N.E.K.O Plugin Market."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import httpx

from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ..repo_action_migration import ActionFileStatus, migrate_github_actions
from ..templates.generator import PluginSpec
from . import deps_cmd, release_cmd
from ._resolve import parse_github_repository_remote, resolve_plugin_dir_candidate

_MARKET_PUBLICATION_URL = (
    "https://market.project-neko.cn/api/v1/release-publications"
)
_RELEASE_CHECK_SUFFIX = ".market-release-check.txt"
_MARKET_EVIDENCE_NAME = "market-evidence.json"
_RELEASE_RUFF_TIMEOUT_SECONDS = 120


def _tri(english: str, chinese: str, japanese: str) -> str:
    return f"{english} / {chinese} / {japanese}"


def _finite_timeout(value: str) -> float:
    timeout = float(value)
    if not math.isfinite(timeout):
        raise argparse.ArgumentTypeError(
            _tri(
                "timeout must be finite",
                "timeout 必须是有限数值",
                "timeout には有限の数値を指定してください",
            )
        )
    return timeout


def register(
    subparsers: argparse._SubParsersAction,
    *,
    defaults: CliDefaults,
) -> None:
    parser = subparsers.add_parser(
        "publish",
        help=_tri(
            "Publish through GitHub and Market, or select one destination",
            "通过 GitHub 和 Market 发布，或只选择一个目标",
            "GitHub と Market に公開、または一方のみを選択",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=_tri(
            "Publish a plugin through its standard GitHub Release workflow and "
            "the N.E.K.O Plugin Market.",
            "通过标准 GitHub Release 工作流和 N.E.K.O 插件市场发布插件。",
            "標準 GitHub Release ワークフローと N.E.K.O プラグインマーケットで"
            "プラグインを公開します。",
        ),
        epilog=(
            "Modes / 模式 / モード:\n"
            "  neko-plugin publish <plugin>\n"
            "      Create/wait for the GitHub Release, then notify Market. /\n"
            "      创建或等待 GitHub Release，然后通知 Market。 /\n"
            "      GitHub Release を作成または待機して Market に通知します。\n"
            "  neko-plugin publish github <plugin>\n"
            "      GitHub Release only / 仅 GitHub Release / GitHub Release のみ\n"
            "  neko-plugin publish market <github-release-url>\n"
            "      Market notification only / 仅通知 Market / Market への通知のみ"
        ),
    )
    parser.add_argument(
        "mode_or_plugin",
        nargs="?",
        default=".",
        help=_tri(
            "Plugin path, or the explicit 'github' / 'market' destination",
            "插件路径，或明确指定 'github' / 'market' 目标",
            "プラグインパス、または 'github' / 'market' の明示的な宛先",
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        help=_tri(
            "Plugin path for 'github', or GitHub Release URL for 'market'",
            "'github' 使用插件路径，'market' 使用 GitHub Release URL",
            "'github' はプラグインパス、'market' は GitHub Release URL",
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_finite_timeout,
        default=900.0,
        help=_tri(
            "Seconds to wait for the GitHub Release assets (default: 900)",
            "等待 GitHub Release 资产上传完成的秒数（默认：900）",
            "GitHub Release アセットのアップロード完了を待つ秒数（既定値：900）",
        ),
    )
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    if args.mode_or_plugin == "market":
        if not args.target:
            print(
                "[FAIL] "
                + _tri(
                    "publish market requires a GitHub Release URL",
                    "publish market 需要 GitHub Release URL",
                    "publish market には GitHub Release URL が必要です",
                ),
                file=sys.stderr,
            )
            return 1
        return _notify_market(args.target)

    if args.mode_or_plugin == "github":
        plugin = args.target or "."
        release_url = _run_github_phase(args, plugin)
        if release_url is None:
            return 1
        print(
            f"[OK] GitHub Release ready: {release_url} / "
            f"GitHub Release 已就绪 / GitHub Release の準備完了"
        )
        return 0

    if args.target:
        print(
            "[FAIL] "
            + _tri(
                "publish accepts one plugin path; use 'publish github' or "
                "'publish market' for an explicit destination",
                "publish 只接受一个插件路径；请用 'publish github' 或 "
                "'publish market' 明确指定目标",
                "publish が受け取るプラグインパスは 1 つです。宛先を指定する場合は "
                "'publish github' または 'publish market' を使用してください",
            ),
            file=sys.stderr,
        )
        return 1
    release_url = _run_github_phase(args, args.mode_or_plugin)
    if release_url is None:
        return 1
    print(
        f"[OK] GitHub Release ready: {release_url} / "
        f"GitHub Release 已就绪 / GitHub Release の準備完了"
    )
    return _notify_market(release_url)


def _run_github_phase(args: argparse.Namespace, plugin: str) -> str | None:
    try:
        return _publish_github(
            plugin,
            defaults=args._defaults,
            timeout=args.timeout,
        )
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(
            f"[FAIL] {_tri('GitHub publication failed', 'GitHub 发布失败', 'GitHub への公開に失敗')}: {exc}",
            file=sys.stderr,
        )
        return None


def _notify_market(release_url: str) -> int:
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                _MARKET_PUBLICATION_URL,
                json={"release_url": release_url},
            )
    except httpx.HTTPError as exc:
        print(
            f"[FAIL] {_tri('Market publication request failed', 'Market 发布请求失败', 'Market 公開リクエストに失敗')}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if response.status_code not in {200, 201}:
        code = payload.get("code") or f"http_{response.status_code}"
        detail = payload.get("detail") or _tri(
            "Market rejected the release",
            "Market 拒绝了此 Release",
            "Market がこの Release を拒否しました",
        )
        print(
            f"[FAIL] {_tri('Market publication failed', 'Market 发布失败', 'Market への公開に失敗')}: "
            f"{code}: {detail}",
            file=sys.stderr,
        )
        return 1

    status = payload.get("status")
    version = payload.get("version") or {}
    version_name = version.get("version") if isinstance(version, dict) else None
    expected = (response.status_code, status) in {
        (201, "published"),
        (200, "already_published"),
    }
    if not expected or not isinstance(version_name, str) or not version_name:
        print(
            "[FAIL] "
            + _tri(
                "Market publication failed: unexpected response",
                "Market 发布失败：响应格式不符合协议",
                "Market への公開に失敗：予期しないレスポンス",
            ),
            file=sys.stderr,
        )
        return 1

    if status == "already_published":
        print(
            f"[OK] Market already published v{version_name} / "
            f"Market 已发布 v{version_name} / Market 公開済み v{version_name}"
        )
    else:
        print(
            f"[OK] Market published v{version_name} / "
            f"Market 发布成功 v{version_name} / Market 公開完了 v{version_name}"
        )
    return 0


def _publish_github(
    plugin: str,
    *,
    defaults: CliDefaults,
    timeout: float,
) -> str:
    plugin_dir = resolve_plugin_dir_candidate(plugin, defaults=defaults)
    source = load_plugin_source(plugin_dir)
    _ensure_clean_worktree(plugin_dir)
    _ensure_standard_release_workflow(plugin_dir, plugin_id=source.plugin_id)
    repository = _github_repository(plugin_dir)
    tag = f"v{source.version}"

    sync_args = argparse.Namespace(
        _defaults=defaults,
        plugin=str(plugin_dir),
        python=sys.executable,
        clean=True,
    )
    if deps_cmd.handle_sync(sync_args) != 0:
        raise RuntimeError(
            _tri(
                "dependency sync did not pass",
                "依赖同步未通过",
                "依存関係の同期に失敗しました",
            )
        )

    with tempfile.TemporaryDirectory(prefix="neko-plugin-publish-") as target_dir:
        check_args = argparse.Namespace(
            _defaults=defaults,
            _command_label=_tri(
                "publish github preflight",
                "publish github 发布前检查",
                "publish github 公開前チェック",
            ),
            plugin=str(plugin_dir),
            plugins_root=None,
            strict=True,
            skip_tests=False,
            target_dir=target_dir,
            market_release=True,
            _release_repository=repository,
            _release_ref_name=tag,
        )
        if release_cmd.handle_release_check(check_args) != 0:
            raise RuntimeError(
                _tri(
                    "release checks did not pass",
                    "发布检查未通过",
                    "リリースチェックに合格しませんでした",
                )
            )
    _ensure_clean_worktree(plugin_dir)

    head = _git(plugin_dir, "rev-parse", "HEAD")
    _ensure_head_pushed(plugin_dir, head=head)
    _ensure_release_ruff_passes(plugin_dir)
    _ensure_remote_tag(plugin_dir, tag=tag, head=head)
    return _wait_for_release(
        repository,
        plugin_id=source.plugin_id,
        tag=tag,
        timeout=timeout,
    )


def _ensure_standard_release_workflow(
    plugin_dir: Path,
    *,
    plugin_id: str,
) -> None:
    changes = migrate_github_actions(
        PluginSpec(plugin_id=plugin_id),
        plugin_dir,
        dry_run=True,
    )
    release_change = next(
        change
        for change in changes
        if change.relative_path == Path(".github/workflows/release.yml")
    )
    if release_change.status is not ActionFileStatus.CURRENT:
        raise RuntimeError(
            _tri(
                "standard release workflow is not current; run "
                f"neko-plugin setup-repo {plugin_dir} --upgrade-github-actions",
                "标准发布工作流不是当前版本；请运行 "
                f"neko-plugin setup-repo {plugin_dir} --upgrade-github-actions",
                "標準リリースワークフローが最新ではありません。次を実行してください："
                f"neko-plugin setup-repo {plugin_dir} --upgrade-github-actions",
            )
        )


def _ensure_clean_worktree(plugin_dir: Path) -> None:
    if not (plugin_dir / ".git").exists():
        raise RuntimeError(
            _tri(
                "plugin source directory does not have its own git repository",
                "插件源码目录没有自己的 Git 仓库",
                "プラグインのソースディレクトリに専用 Git リポジトリがありません",
            )
        )
    if _git(plugin_dir, "status", "--porcelain"):
        raise RuntimeError(
            _tri(
                "git working tree has uncommitted changes",
                "Git 工作区存在未提交的修改",
                "Git ワークツリーに未コミットの変更があります",
            )
        )


def _ensure_head_pushed(plugin_dir: Path, *, head: str) -> None:
    upstream = _git(
        plugin_dir,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    remote, separator, branch = upstream.partition("/")
    if separator != "/" or remote != "origin" or not branch:
        raise RuntimeError(
            _tri(
                "the current branch must track an origin branch",
                "当前分支必须跟踪 origin 上的分支",
                "現在のブランチは origin のブランチを追跡する必要があります",
            )
        )
    output = _git(plugin_dir, "ls-remote", "origin", f"refs/heads/{branch}")
    remote_head = output.split(maxsplit=1)[0] if output else None
    if remote_head != head:
        raise RuntimeError(
            _tri(
                "HEAD is not pushed to its origin branch; push the branch first",
                "HEAD 尚未推送到 origin 分支；请先推送当前分支",
                "HEAD が origin ブランチに push されていません。先にブランチを push してください",
            )
        )


def _ensure_release_ruff_passes(plugin_dir: Path) -> None:
    try:
        completed = subprocess.run(
            [
                "uvx",
                "ruff==0.12.4",
                "check",
                "--ignore-noqa",
                "--isolated",
                "--target-version",
                "py311",
                "--line-length",
                "120",
                "--select",
                "E4,E7,E9,F,I",
                "--exclude",
                "vendor",
                ".",
            ],
            cwd=plugin_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_RELEASE_RUFF_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            _tri(
                "uvx executable was not found; install uv before publishing",
                "未找到 uvx 可执行文件；请先安装 uv 再发布",
                "uvx 実行ファイルが見つかりません。公開前に uv をインストールしてください",
            )
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            _tri(
                "Ruff check timed out after 120 seconds",
                "Ruff 检查在 120 秒后超时",
                "Ruff チェックが 120 秒でタイムアウトしました",
            )
        ) from exc
    if completed.returncode == 0:
        return
    output = completed.stdout.strip()
    if output:
        print(output, file=sys.stderr)
    raise RuntimeError(
        _tri(
            "Ruff check failed",
            "Ruff 检查未通过",
            "Ruff チェックに失敗しました",
        )
    )


def _github_repository(plugin_dir: Path) -> str:
    fetch_urls = _git_config_values(plugin_dir, "remote.origin.url")
    if len(fetch_urls) != 1:
        raise RuntimeError(
            _tri(
                "git remote 'origin' must have exactly one fetch URL for publishing",
                "发布时 Git 远程 'origin' 必须恰好配置一个 fetch URL",
                "公開時、Git リモート 'origin' の fetch URL はちょうど 1 つ必要です",
            )
        )
    origin = fetch_urls[0]
    repository = parse_github_repository_remote(origin)
    if repository is None:
        raise RuntimeError(
            _tri(
                "git remote 'origin' must point to a GitHub repository",
                "Git 远程 'origin' 必须指向 GitHub 仓库",
                "Git リモート 'origin' は GitHub リポジトリを指す必要があります",
            )
        )
    push_urls = _git_config_values(plugin_dir, "remote.origin.pushurl")
    if len(push_urls) > 1:
        raise RuntimeError(
            _tri(
                "git remote 'origin' must have at most one push URL for publishing",
                "发布时 Git 远程 'origin' 最多只能配置一个 push URL",
                "公開時、Git リモート 'origin' の push URL は 1 つまでです",
            )
        )
    resolved_push_urls = [
        line.strip()
        for line in _git(
            plugin_dir,
            "remote",
            "get-url",
            "--push",
            "--all",
            "origin",
        ).splitlines()
        if line.strip()
    ]
    if len(resolved_push_urls) != 1:
        raise RuntimeError(
            _tri(
                "git remote 'origin' must resolve to exactly one push URL for publishing",
                "发布时 Git 远程 'origin' 必须恰好解析出一个 push URL",
                "公開時、Git リモート 'origin' の push URL はちょうど 1 つに解決される必要があります",
            )
        )
    push_url = resolved_push_urls[0]
    push_repository = parse_github_repository_remote(push_url)
    if push_repository is None:
        raise RuntimeError(
            _tri(
                "git remote 'origin' push URL must point to a GitHub repository",
                "Git 远程 'origin' 的 push URL 必须指向 GitHub 仓库",
                "Git リモート 'origin' の push URL は GitHub リポジトリを指す必要があります",
            )
        )
    if push_repository.casefold() != repository.casefold():
        raise RuntimeError(
            _tri(
                "git remote 'origin' push URL must point to the same GitHub repository as its fetch URL",
                "Git 远程 'origin' 的 push URL 必须与 fetch URL 指向同一个 GitHub 仓库",
                "Git リモート 'origin' の push URL と fetch URL は同じ GitHub リポジトリを指す必要があります",
            )
        )
    return repository


def _ensure_remote_tag(plugin_dir: Path, *, tag: str, head: str) -> None:
    remote_commit = _remote_tag_commit(plugin_dir, tag)
    if remote_commit:
        if remote_commit != head:
            raise RuntimeError(
                _tri(
                    f"remote tag {tag} points to {remote_commit[:12]}, not HEAD {head[:12]}",
                    f"远程 tag {tag} 指向 {remote_commit[:12]}，不是 HEAD {head[:12]}",
                    f"リモート tag {tag} は {remote_commit[:12]} を指し、HEAD {head[:12]} ではありません",
                )
            )
        return

    local = _local_tag_commit(plugin_dir, tag)
    if local and local != head:
        raise RuntimeError(
            _tri(
                f"local tag {tag} points to {local[:12]}, not HEAD {head[:12]}",
                f"本地 tag {tag} 指向 {local[:12]}，不是 HEAD {head[:12]}",
                f"ローカル tag {tag} は {local[:12]} を指し、HEAD {head[:12]} ではありません",
            )
        )
    if not local:
        _git(plugin_dir, "tag", tag, head)
    _git(plugin_dir, "push", "origin", f"refs/tags/{tag}")
    if _remote_tag_commit(plugin_dir, tag) != head:
        raise RuntimeError(
            _tri(
                f"remote tag {tag} was not published at HEAD",
                f"远程 tag {tag} 未发布到 HEAD",
                f"リモート tag {tag} は HEAD に公開されませんでした",
            )
        )


def _remote_tag_commit(plugin_dir: Path, tag: str) -> str | None:
    output = _git(
        plugin_dir,
        "ls-remote",
        "origin",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    lines = [line.split() for line in output.splitlines() if line.strip()]
    peeled = [sha for sha, ref in lines if ref.endswith("^{}")]
    direct = [sha for sha, ref in lines if ref == f"refs/tags/{tag}"]
    values = peeled or direct
    return values[0] if values else None


def _local_tag_commit(plugin_dir: Path, tag: str) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
        cwd=plugin_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 1:
        return None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            detail
            or _tri(
                f"git rev-parse failed for tag {tag}",
                f"无法解析 Git tag {tag}",
                f"Git tag {tag} の解析に失敗しました",
            )
        )
    return completed.stdout.strip()


def _wait_for_release(
    repository: str,
    *,
    plugin_id: str,
    tag: str,
    timeout: float,
) -> str:
    api_url = (
        f"https://api.github.com/repos/{repository}/releases/tags/"
        f"{quote(tag, safe='')}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "neko-plugin",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    deadline = time.monotonic() + max(timeout, 0)
    expected_assets = (
        f"{plugin_id}.neko-plugin",
        f"{plugin_id}{_RELEASE_CHECK_SUFFIX}",
        _MARKET_EVIDENCE_NAME,
    )
    unready_assets = list(expected_assets)
    with httpx.Client(timeout=30.0) as client:
        while True:
            response = client.get(api_url, headers=headers)
            if response.status_code == 200:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError(
                        _tri(
                            "GitHub Release response is not an object",
                            "GitHub Release 响应不是对象",
                            "GitHub Release レスポンスがオブジェクトではありません",
                        )
                    )
                release_url = str(payload.get("html_url") or "")
                if not release_url:
                    raise RuntimeError(
                        _tri(
                            "GitHub Release response has no html_url",
                            "GitHub Release 响应缺少 html_url",
                            "GitHub Release レスポンスに html_url がありません",
                        )
                    )
                assets = payload.get("assets")
                assets_by_name = (
                    {
                        asset.get("name"): asset
                        for asset in assets
                        if isinstance(asset, dict)
                        and isinstance(asset.get("name"), str)
                    }
                    if isinstance(assets, list)
                    else {}
                )
                unready_assets = [
                    name
                    for name in expected_assets
                    if not _release_asset_is_downloadable(assets_by_name.get(name))
                ]
                if not unready_assets:
                    return release_url
            if response.status_code != 404:
                if response.status_code != 200:
                    raise RuntimeError(
                        _tri(
                            f"GitHub Release lookup returned HTTP {response.status_code}",
                            f"查询 GitHub Release 返回 HTTP {response.status_code}",
                            f"GitHub Release の照会が HTTP {response.status_code} を返しました",
                        )
                    )
            if time.monotonic() >= deadline:
                if response.status_code == 200:
                    missing = ", ".join(unready_assets)
                    raise RuntimeError(
                        _tri(
                            f"timed out waiting for GitHub Release assets: {missing}; check GitHub Actions",
                            f"等待 GitHub Release 资产上传完成超时：{missing}；请检查 GitHub Actions",
                            f"GitHub Release アセットの待機がタイムアウトしました：{missing}。GitHub Actions を確認してください",
                        )
                    )
                raise RuntimeError(
                    _tri(
                        f"timed out waiting for GitHub Release {tag}; check GitHub Actions",
                        f"等待 GitHub Release {tag} 超时；请检查 GitHub Actions",
                        f"GitHub Release {tag} の待機がタイムアウトしました。GitHub Actions を確認してください",
                    )
                )
            time.sleep(min(5.0, max(deadline - time.monotonic(), 0)))


def _release_asset_is_downloadable(asset: object) -> bool:
    if not isinstance(asset, dict):
        return False
    size = asset.get("size")
    state = asset.get("state")
    download_url = asset.get("browser_download_url")
    return (
        state == "uploaded"
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size > 0
        and isinstance(download_url, str)
        and bool(download_url.strip())
    )


def _git(plugin_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=plugin_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            detail
            or _tri(
                f"git {' '.join(args)} failed",
                f"Git 命令 {' '.join(args)} 失败",
                f"Git コマンド {' '.join(args)} が失敗しました",
            )
        )
    return completed.stdout.strip()


def _git_config_values(plugin_dir: Path, key: str) -> list[str]:
    completed = subprocess.run(
        ["git", "config", "--get-all", key],
        cwd=plugin_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 1:
        return []
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            detail
            or _tri(
                f"git config --get-all {key} failed",
                f"git config --get-all {key} 执行失败",
                f"git config --get-all {key} の実行に失敗しました",
            )
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


__all__ = ["handle", "register"]

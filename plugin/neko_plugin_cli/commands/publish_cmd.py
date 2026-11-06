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

from plugin.utils.github_release import (
    missing_release_assets,
    release_ready,
    select_release_run,
    workflow_run_state,
)

from ..core.build_rules import VENDOR_SYNC_GLOBS
from ..core.lint import ruff_command
from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ..repo_action_migration import ActionFileStatus, migrate_github_actions
from ..repo_state import git_output, worktree_dirty_paths
from ..templates.generator import PluginSpec
from . import deps_cmd, release_cmd
from ._resolve import parse_github_repository_remote, resolve_plugin_dir_candidate

_MARKET_PUBLICATION_URL = (
    "https://market.project-neko.cn/api/v1/release-publications"
)
_GITHUB_API_URL = "https://api.github.com"
_RELEASE_RUFF_TIMEOUT_SECONDS = 120
# Unauthenticated callers get 60 GitHub API requests per hour per IP.
_RELEASE_POLL_SECONDS = 15.0
_GH_TOKEN_TIMEOUT_SECONDS = 20
_DIRTY_PATHS_SHOWN = 5
# The Market answers these while its verification job is queued or running.
_MARKET_RETRY_STATUSES = frozenset({429, 503})
_MARKET_DEFAULT_RETRY_SECONDS = 5.0
_MARKET_MAX_RETRY_SECONDS = 60
_MARKET_NETWORK_RETRY_SECONDS = 5.0
_MARKET_REVIEW_STATUSES = frozenset(
    {"pending_review", "approved", "superseded", "rejected"}
)


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
            "Seconds to wait for the GitHub Release and its workflow run (default: 900)",
            "等待 GitHub Release 及其工作流运行完成的秒数（默认：900）",
            "GitHub Release とそのワークフロー実行の完了を待つ秒数（既定値：900）",
        ),
    )
    parser.add_argument(
        "--market-wait",
        type=_finite_timeout,
        default=1800.0,
        help=_tri(
            "Seconds to keep retrying while the Market is still verifying the release (default: 1800)",
            "Market 仍在核验 Release 时持续重试的秒数（默认：1800）",
            "Market が Release を検証中の間、再試行を続ける秒数（既定値：1800）",
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
        return _notify_market(args.target, wait=args.market_wait)

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
    return _notify_market(release_url, wait=args.market_wait)


def _run_github_phase(args: argparse.Namespace, plugin: str) -> str | None:
    try:
        return _publish_github(
            plugin,
            defaults=args._defaults,
            timeout=args.timeout,
        )
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        # Git errors span several lines and the cause is usually on the last
        # one; keep them on the single [FAIL] line callers read.
        detail = " | ".join(
            line.strip() for line in str(exc).splitlines() if line.strip()
        )
        print(
            f"[FAIL] {_tri('GitHub publication failed', 'GitHub 发布失败', 'GitHub への公開に失敗')}: {detail}",
            file=sys.stderr,
        )
        return None


def _notify_market(release_url: str, *, wait: float) -> int:
    """POST the release to the Market, retrying while it is still verifying.

    The Market answers 503 (scan queued or running) or 429 with Retry-After
    until its verification job finishes; re-posting the same body re-attaches
    to that job (N.E.K.O-PluginMarket .github/workflows/notify-market-release.yml).
    """
    deadline = time.monotonic() + max(wait, 0)
    waiting_reason: str | None = None
    with httpx.Client(timeout=30.0) as client:
        while True:
            try:
                response = client.post(
                    _MARKET_PUBLICATION_URL,
                    json={"release_url": release_url},
                )
            except httpx.HTTPError as exc:
                reason = f"{_tri('Market publication request failed', 'Market 发布请求失败', 'Market 公開リクエストに失敗')}: {exc}"
                delay = _MARKET_NETWORK_RETRY_SECONDS
            else:
                payload = _response_object(response)
                if response.status_code not in _MARKET_RETRY_STATUSES:
                    return _report_market_response(response.status_code, payload)
                code = payload.get("code") or f"http_{response.status_code}"
                reason = f"{code}: {payload.get('detail') or ''}".rstrip(": ")
                delay = _market_retry_delay(response)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(
                    f"[FAIL] {_tri('Market publication failed', 'Market 发布失败', 'Market への公開に失敗')}: "
                    f"{reason}; "
                    + _tri(
                        f"still not accepted after {wait:.0f} s; run neko-plugin publish market {release_url} again later",
                        f"{wait:.0f} 秒后仍未完成；请稍后再次运行 neko-plugin publish market {release_url}",
                        f"{wait:.0f} 秒経っても完了していません。後で neko-plugin publish market {release_url} を再実行してください",
                    ),
                    file=sys.stderr,
                )
                return 1
            if reason != waiting_reason:
                waiting_reason = reason
                print(
                    "[WAIT] "
                    + _tri(
                        f"Market is still verifying the release, retrying ({reason})",
                        f"Market 仍在核验此 Release，正在重试（{reason}）",
                        f"Market はまだ Release を検証中です。再試行します（{reason}）",
                    ),
                    flush=True,
                )
            time.sleep(min(delay, remaining))


def _response_object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _market_retry_delay(response: httpx.Response) -> float:
    retry_after = response.headers.get("retry-after", "")
    if not retry_after.isdigit():
        return _MARKET_DEFAULT_RETRY_SECONDS
    return float(min(max(int(retry_after), 1), _MARKET_MAX_RETRY_SECONDS))


def _report_market_response(status_code: int, payload: dict) -> int:
    """Print the ``[MARKET] status=...`` line for a final Market answer."""
    if status_code not in {200, 201, 202}:
        code = payload.get("code") or f"http_{status_code}"
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
    version = payload.get("version")
    version_name = version.get("version") if isinstance(version, dict) else None
    candidate = payload.get("candidate")
    candidate_id = candidate.get("id") if isinstance(candidate, dict) else None
    if status_code == 202:
        expected = (
            status in _MARKET_REVIEW_STATUSES
            and isinstance(candidate_id, int)
            and not isinstance(candidate_id, bool)
        )
    else:
        expected = (
            (status_code, status) in {(201, "published"), (200, "already_published")}
            and isinstance(version_name, str)
            and bool(version_name)
        )
    if not expected:
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

    print(f"[MARKET] status={status}" + (f" candidate={candidate_id}" if status_code == 202 else ""))
    if status == "published":
        print(
            f"[OK] Market published v{version_name} / "
            f"Market 发布成功 v{version_name} / Market 公開完了 v{version_name}"
        )
    elif status == "already_published":
        print(
            f"[OK] Market already published v{version_name} / "
            f"Market 已发布 v{version_name} / Market 公開済み v{version_name}"
        )
    elif status == "rejected":
        print(
            "[FAIL] "
            + _tri(
                "the Market rejected this exact release before; publish a new version",
                "Market 之前已拒绝过这个完全相同的 Release；请发布新版本",
                "Market はこれと同一の Release を以前に拒否しました。新しいバージョンを公開してください",
            ),
            file=sys.stderr,
        )
        return 1
    elif status == "pending_review":
        print(
            "[OK] "
            + _tri(
                "Market accepted the release; it is waiting for a Market reviewer",
                "Market 已接收此 Release，正在等待 Market 审核员审核",
                "Market は Release を受け付けました。Market のレビュアーによる審査待ちです",
            )
        )
    elif status == "approved":
        print(
            "[OK] "
            + _tri(
                "a Market reviewer already approved this release",
                "Market 审核员已批准此 Release",
                "Market のレビュアーはこの Release を承認済みです",
            )
        )
    else:
        print(
            "[OK] "
            + _tri(
                "a newer release of this version replaced this one in Market review",
                "此版本的更新 Release 已在 Market 审核中取代了这一个",
                "このバージョンの新しい Release が Market の審査でこれを置き換えました",
            )
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
        # A clean rebuild by default must not silently drop an unreconciled
        # backup of vendor/; only an explicit `sync --clean` may.
        discard_backups=False,
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
    headers = _github_headers()
    _ensure_public_repository(repository, headers=headers)
    _ensure_remote_tag(plugin_dir, tag=tag, head=head)
    return _wait_for_release(
        repository,
        plugin_id=source.plugin_id,
        tag=tag,
        head=head,
        headers=headers,
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
    dirty = worktree_dirty_paths(plugin_dir)
    if dirty is None:
        raise RuntimeError(
            _tri(
                "git status failed",
                "无法读取 Git 工作区状态",
                "Git ワークツリーの状態を読み取れません",
            )
        )
    if dirty:
        shown = ", ".join(dirty[:_DIRTY_PATHS_SHOWN])
        if len(dirty) > _DIRTY_PATHS_SHOWN:
            shown += f", … (+{len(dirty) - _DIRTY_PATHS_SHOWN})"
        raise RuntimeError(
            _tri(
                f"git working tree has uncommitted changes: {shown}",
                f"Git 工作区存在未提交的修改：{shown}",
                f"Git ワークツリーに未コミットの変更があります：{shown}",
            )
        )


def _ensure_head_pushed(plugin_dir: Path, *, head: str) -> None:
    branch = git_output(plugin_dir, "symbolic-ref", "--short", "-q", "HEAD")
    if not branch:
        raise RuntimeError(
            _tri(
                "HEAD is detached; check out the branch to publish from",
                "HEAD 处于分离状态；请先切换到要发布的分支",
                "HEAD がデタッチ状態です。公開するブランチをチェックアウトしてください",
            )
        )
    set_upstream = f"git push -u origin {branch}"
    upstream = git_output(
        plugin_dir,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if not upstream:
        raise RuntimeError(
            _tri(
                f"branch {branch} has no upstream; run {set_upstream}",
                f"分支 {branch} 没有上游分支；请运行 {set_upstream}",
                f"ブランチ {branch} に上流ブランチがありません。{set_upstream} を実行してください",
            )
        )
    remote, _, remote_branch = upstream.partition("/")
    if remote != "origin" or not remote_branch:
        raise RuntimeError(
            _tri(
                f"branch {branch} tracks {upstream}, not an origin branch; run {set_upstream}",
                f"分支 {branch} 跟踪的是 {upstream}，不是 origin 上的分支；请运行 {set_upstream}",
                f"ブランチ {branch} は origin ではなく {upstream} を追跡しています。{set_upstream} を実行してください",
            )
        )
    output = _git(plugin_dir, "ls-remote", "origin", f"refs/heads/{remote_branch}")
    remote_head = output.split(maxsplit=1)[0] if output else None
    if remote_head != head:
        push = (
            f"git push origin {branch}"
            if remote_branch == branch
            else f"git push origin {branch}:{remote_branch}"
        )
        raise RuntimeError(
            _tri(
                f"HEAD is not pushed to origin/{remote_branch}; run {push}",
                f"HEAD 尚未推送到 origin/{remote_branch}；请运行 {push}",
                f"HEAD が origin/{remote_branch} に push されていません。{push} を実行してください",
            )
        )


def _ensure_release_ruff_passes(plugin_dir: Path) -> None:
    try:
        completed = subprocess.run(
            ruff_command(
                ".",
                respect_gitignore=True,
                # "./" anchors the sync work dirs to the plugin root, matching
                # the build rules; a bare pattern would match at any depth.
                extra_excludes=tuple(f"./{pattern}" for pattern in VENDOR_SYNC_GLOBS),
            ),
            cwd=plugin_dir,
            encoding="utf-8",
            errors="replace",
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
            # The Market keeps every version it has seen, including yanked
            # ones, so moving the tag cannot publish this commit as that version.
            raise RuntimeError(
                _tri(
                    f"remote tag {tag} points to {remote_commit[:12]}, not HEAD {head[:12]}; "
                    "a new version number is needed: bump the version in plugin.toml",
                    f"远程 tag {tag} 指向 {remote_commit[:12]}，不是 HEAD {head[:12]}；"
                    "需要新的版本号：请提升 plugin.toml 中的版本号",
                    f"リモート tag {tag} は {remote_commit[:12]} を指し、HEAD {head[:12]} ではありません。"
                    "新しいバージョン番号が必要です：plugin.toml のバージョンを上げてください",
                )
            )
        return

    local = _local_tag_commit(plugin_dir, tag)
    if local and local != head:
        delete = f"git tag -d {tag}"
        raise RuntimeError(
            _tri(
                f"local tag {tag} points to {local[:12]}, not HEAD {head[:12]}; "
                f"bump the version in plugin.toml, or delete the tag ({delete})",
                f"本地 tag {tag} 指向 {local[:12]}，不是 HEAD {head[:12]}；"
                f"请提升 plugin.toml 中的版本号，或删除该 tag（{delete}）",
                f"ローカル tag {tag} は {local[:12]} を指し、HEAD {head[:12]} ではありません。"
                f"plugin.toml のバージョンを上げるか、tag を削除してください（{delete}）",
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
        encoding="utf-8",
        errors="replace",
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


def _gh_auth_token() -> str | None:
    """The token of the user's ``gh auth login``, or None when gh cannot give one."""
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GH_TOKEN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = completed.stdout.strip()
    return token if completed.returncode == 0 and token else None


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "neko-plugin",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or _gh_auth_token()
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _rate_limit_wait(response: httpx.Response) -> float | None:
    """Seconds until GitHub accepts requests again, or None when not rate limited."""
    if response.status_code not in {403, 429}:
        return None
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isdigit():
        return float(retry_after)
    reset = response.headers.get("x-ratelimit-reset", "")
    if response.headers.get("x-ratelimit-remaining") != "0" or not reset.isdigit():
        return None
    return max(int(reset) - time.time(), 0.0) + 1.0


def _github_get(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    deadline: float,
) -> httpx.Response:
    """GET from the GitHub API, waiting out a rate limit that resets before ``deadline``."""
    while True:
        response = client.get(url, headers=headers)
        wait = _rate_limit_wait(response)
        if wait is None:
            return response
        if time.monotonic() + wait > deadline:
            seconds = math.ceil(wait)
            raise RuntimeError(
                _tri(
                    f"GitHub API rate limit reached (resets in {seconds} s); "
                    "sign in with gh auth login or set GH_TOKEN",
                    f"已达到 GitHub API 速率限制（{seconds} 秒后重置）；"
                    "请运行 gh auth login 登录或设置 GH_TOKEN",
                    f"GitHub API のレート制限に達しました（{seconds} 秒後にリセット）。"
                    "gh auth login でサインインするか GH_TOKEN を設定してください",
                )
            )
        time.sleep(wait)


def _ensure_public_repository(repository: str, *, headers: dict[str, str]) -> None:
    """Refuse before tagging when the Market could not read the repository."""
    with httpx.Client(timeout=30.0) as client:
        response = _github_get(
            client,
            f"{_GITHUB_API_URL}/repos/{repository}",
            headers=headers,
            deadline=time.monotonic(),
        )
    if response.status_code == 404:
        raise RuntimeError(
            _tri(
                f"GitHub repository {repository} is not visible (private or missing); "
                "the Market needs a public repository",
                f"GitHub 仓库 {repository} 不可见（私有或不存在）；Market 需要公开仓库",
                f"GitHub リポジトリ {repository} が見えません（非公開または存在しません）。"
                "Market には公開リポジトリが必要です",
            )
        )
    if response.status_code != 200:
        raise RuntimeError(
            _tri(
                f"GitHub repository lookup returned HTTP {response.status_code}",
                f"查询 GitHub 仓库返回 HTTP {response.status_code}",
                f"GitHub リポジトリの照会が HTTP {response.status_code} を返しました",
            )
        )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("private") is not False:
        raise RuntimeError(
            _tri(
                f"GitHub repository {repository} is private; "
                "the Market needs a public repository",
                f"GitHub 仓库 {repository} 是私有仓库；Market 需要公开仓库",
                f"GitHub リポジトリ {repository} は非公開です。Market には公開リポジトリが必要です",
            )
        )


def _release_run(
    client: httpx.Client,
    repository: str,
    *,
    tag: str,
    head: str,
    headers: dict[str, str],
    deadline: float,
) -> dict | None:
    """The newest release workflow run for ``tag`` at ``head``, or None when there is none yet.

    The Market accepts a run started by the tag push, or a manual dispatch on
    the tag (N.E.K.O-PluginMarket app/services/release_fetcher.py).
    """
    response = _github_get(
        client,
        f"{_GITHUB_API_URL}/repos/{repository}/actions/workflows/release.yml/runs"
        f"?head_sha={head}",
        headers=headers,
        deadline=deadline,
    )
    # Unreadable Actions (disabled, no permission) leave the run unknown; the
    # wait then ends at its timeout.
    if response.status_code != 200:
        return None
    return select_release_run(response.json(), tag)


def _raise_if_release_run_failed(run: dict | None, *, tag: str) -> None:
    """Fail fast when the release workflow run for ``tag`` finished without success."""
    if workflow_run_state(run) != "failure":
        return
    conclusion = run.get("conclusion")
    run_url = run.get("html_url") or ""
    raise RuntimeError(
        _tri(
            f"release workflow for {tag} concluded {conclusion}: {run_url}; "
            "fix it and re-run the workflow, or bump the version",
            f"{tag} 的发布工作流结果为 {conclusion}：{run_url}；"
            "请修复后重新运行该工作流，或提升版本号",
            f"{tag} のリリースワークフローの結果は {conclusion} です：{run_url}。"
            "修正してワークフローを再実行するか、バージョンを上げてください",
        )
    )


def _wait_for_release(
    repository: str,
    *,
    plugin_id: str,
    tag: str,
    head: str,
    headers: dict[str, str],
    timeout: float,
) -> str:
    print(
        "[WAIT] "
        + _tri(
            f"waiting for GitHub Release {tag}",
            f"正在等待 GitHub Release {tag}",
            f"GitHub Release {tag} を待機しています",
        ),
        flush=True,
    )
    api_url = (
        f"{_GITHUB_API_URL}/repos/{repository}/releases/tags/"
        f"{quote(tag, safe='')}"
    )
    deadline = time.monotonic() + max(timeout, 0)
    unready_assets = missing_release_assets({}, plugin_id)
    with httpx.Client(timeout=30.0) as client:
        while True:
            assets_ready = False
            response = _github_get(client, api_url, headers=headers, deadline=deadline)
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
                unready_assets = missing_release_assets(payload, plugin_id)
                assets_ready = not unready_assets
            elif response.status_code != 404:
                raise RuntimeError(
                    _tri(
                        f"GitHub Release lookup returned HTTP {response.status_code}",
                        f"查询 GitHub Release 返回 HTTP {response.status_code}",
                        f"GitHub Release の照会が HTTP {response.status_code} を返しました",
                    )
                )
            run = _release_run(
                client,
                repository,
                tag=tag,
                head=head,
                headers=headers,
                deadline=deadline,
            )
            _raise_if_release_run_failed(run, tag=tag)
            # The Market refuses a release whose workflow run has not
            # completed with success (release_fetcher.py), and uploading the
            # assets is not the run's last step.
            if assets_ready and release_ready(unready_assets, workflow_run_state(run)):
                return release_url
            if time.monotonic() >= deadline:
                if assets_ready:
                    run_state = run.get("status") if run is not None else "not found"
                    raise RuntimeError(
                        _tri(
                            f"timed out waiting for the release workflow run for {tag} to succeed "
                            f"(run: {run_state}); check GitHub Actions",
                            f"等待 {tag} 的发布工作流成功超时（运行状态：{run_state}）；请检查 GitHub Actions",
                            f"{tag} のリリースワークフローの成功待機がタイムアウトしました"
                            f"（実行状態：{run_state}）。GitHub Actions を確認してください",
                        )
                    )
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
            time.sleep(min(_RELEASE_POLL_SECONDS, max(deadline - time.monotonic(), 0)))


def _git(plugin_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=plugin_dir,
        encoding="utf-8",
        errors="replace",
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
        encoding="utf-8",
        errors="replace",
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

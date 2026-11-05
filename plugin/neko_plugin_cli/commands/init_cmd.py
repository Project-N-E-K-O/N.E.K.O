"""Create complete, Market-ready plugin source directories."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from plugin._types.plugin_types import SCAFFOLDABLE_PLUGIN_TYPES

from ..paths import CliDefaults
from ..repo_action_migration import ActionFileStatus, migrate_github_actions
from ..templates.generator import PluginSpec, generate_repo_support_files
from ..core.plugin_source import load_plugin_source
from ..core.project import create_project
from ._resolve import parse_github_repository_remote

_MARKET_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MARKET_REPO_PREFIX = "n.e.k.o_plugin_"


def _tri(english: str, chinese: str, japanese: str) -> str:
    return f"{english} / {chinese} / {japanese}"


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser(
        "init",
        help=_tri(
            "Create a Market-ready plugin in the N.E.K.O source tree",
            "在 N.E.K.O 源码目录中创建可发布到 Market 的插件",
            "N.E.K.O ソースツリーに Market 公開可能なプラグインを作成",
        ),
    )
    parser.add_argument(
        "plugin_id",
        help=_tri("Market plugin ID", "Market 插件 ID", "Market プラグイン ID"),
    )
    parser.add_argument(
        "--type",
        dest="plugin_type",
        choices=tuple(sorted(SCAFFOLDABLE_PLUGIN_TYPES)),
        default="plugin",
        help=_tri("Plugin type", "插件类型", "プラグイン種別"),
    )
    parser.add_argument(
        "--name",
        help=_tri("Display name", "显示名称", "表示名"),
    )
    parser.add_argument(
        "--output",
        help=_tri(
            "Exact plugin source directory (default: N.E.K.O/plugin/plugins/<id>)",
            "插件源码目录（默认：N.E.K.O/plugin/plugins/<id>）",
            "プラグインのソースディレクトリ（既定値：N.E.K.O/plugin/plugins/<id>）",
        ),
    )
    parser.add_argument(
        "--remote",
        help=_tri(
            "Add a matching GitHub repository as origin",
            "将匹配的 GitHub 仓库添加为 origin",
            "一致する GitHub リポジトリを origin として追加",
        ),
    )
    parser.set_defaults(handler=handle, _defaults=defaults)

    setup_parser = subparsers.add_parser(
        "setup-repo",
        help="Add repository support files to an existing plugin",
    )
    setup_parser.add_argument("plugin", help="Plugin directory name under plugin/plugins or explicit plugin path")
    setup_parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    setup_parser.add_argument("--github-actions", action="store_true", help="Generate a GitHub Actions verification workflow")
    setup_parser.add_argument(
        "--upgrade-github-actions",
        action="store_true",
        help=(
            "Safely upgrade standard GitHub Actions / "
            "安全升级标准 GitHub Actions / 標準 GitHub Actions を安全に更新"
        ),
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview without writing / 仅预览、不写入 / "
            "プレビューのみ（書き込みなし）"
        ),
    )
    setup_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing support files")
    setup_parser.add_argument("--git", action="store_true", help="Initialize a git repository if this plugin directory is not already inside one")
    setup_parser.add_argument("--remote", help="Add a git remote named origin after --git initialization")
    setup_parser.add_argument("--no-readme", action="store_true", help="Do not generate README.md")
    setup_parser.add_argument("--no-tests", action="store_true", help="Do not generate tests/test_smoke.py")
    setup_parser.add_argument("--no-gitignore", action="store_true", help="Do not generate .gitignore")
    setup_parser.add_argument("--no-vscode", action="store_true", help="Do not generate VSCode settings and tasks")
    setup_parser.set_defaults(handler=handle_setup_repo, _defaults=defaults)

    create_repo_parser = subparsers.add_parser(
        "create-repo",
        help=_tri(
            "Create the plugin's GitHub repository with gh and wire it as origin",
            "用 gh 创建插件的 GitHub 仓库并设为 origin",
            "gh でプラグインの GitHub リポジトリを作成し origin に設定",
        ),
    )
    create_repo_parser.add_argument(
        "plugin",
        help="Plugin directory name under plugin/plugins or explicit plugin path",
    )
    create_repo_parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    create_repo_parser.add_argument(
        "--owner",
        help=_tri(
            "GitHub owner for the new repository (default: the gh account)",
            "新仓库的 GitHub 拥有者（默认：gh 登录账号）",
            "新しいリポジトリの GitHub オーナー（既定値：gh のログインアカウント）",
        ),
    )
    create_repo_parser.add_argument(
        "--private",
        action="store_true",
        help=_tri(
            "Create a private repository (the Market requires a public one to publish)",
            "创建私有仓库（发布到 Market 需要公开仓库）",
            "プライベートリポジトリを作成（Market 公開には公開リポジトリが必要）",
        ),
    )
    create_repo_parser.set_defaults(handler=handle_create_repo, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    plugin_id = args.plugin_id.strip()
    if not _MARKET_PLUGIN_ID_RE.fullmatch(plugin_id):
        print(
            "[FAIL] "
            + _tri(
                f"invalid Market plugin ID: '{plugin_id}' "
                "(use lowercase letters, numbers, and underscores; start with a letter)",
                f"无效的 Market 插件 ID：'{plugin_id}'（使用小写字母、数字和下划线，且以字母开头）",
                f"無効な Market プラグイン ID：'{plugin_id}'（小文字、数字、アンダースコアを使用し、文字で始めてください）",
            ),
            file=sys.stderr,
        )
        return 1

    target_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (defaults.plugins_root / plugin_id).resolve()
    )
    if target_dir.exists():
        print(
            "[FAIL] "
            + _tri(
                f"directory already exists: {target_dir}",
                f"目录已存在：{target_dir}",
                f"ディレクトリは既に存在します：{target_dir}",
            ),
            file=sys.stderr,
        )
        return 1
    if shutil.which("git") is None:
        print(
            "[FAIL] "
            + _tri(
                "git executable not found",
                "未找到 Git 可执行文件",
                "Git 実行ファイルが見つかりません",
            ),
            file=sys.stderr,
        )
        return 1
    if args.remote and not _remote_matches_plugin(args.remote, plugin_id=plugin_id):
        print(
            "[FAIL] "
            + _tri(
                f"remote must be a GitHub repository named {_market_repo_name(plugin_id)}",
                f"remote 必须是名为 {_market_repo_name(plugin_id)} 的 GitHub 仓库",
                f"remote は {_market_repo_name(plugin_id)} という名前の GitHub リポジトリである必要があります",
            ),
            file=sys.stderr,
        )
        return 1

    spec = PluginSpec(
        plugin_id=plugin_id,
        name=args.name or plugin_id,
        plugin_type=args.plugin_type,
        quick_start=True,
        features=["entry_point"],
        create_github_actions=True,
    )
    try:
        created = create_project(
            spec, target_dir, repo_root=defaults.repo_root,
            remote=args.remote, git_runner=_run_git,
        )
    except Exception as exc:
        print(
            "[FAIL] "
            + _tri(
                f"repository creation failed: {exc}",
                f"创建仓库失败：{exc}",
                f"リポジトリの作成に失敗しました：{exc}",
            ),
            file=sys.stderr,
        )
        return 1

    print(
        "\n[OK] "
        + _tri(
            f"created {target_dir}/",
            f"已创建 {target_dir}/",
            f"{target_dir}/ を作成しました",
        )
    )
    for path in created:
        print(f"  └── {path.relative_to(target_dir)}")
    print(f"\n  {_tri('plugin', '插件', 'プラグイン')}: {plugin_id}")
    print(f"  {_tri('repository', '仓库', 'リポジトリ')}: {_market_repo_name(plugin_id)}")
    print(f"  {_tri('entry', '入口', 'エントリ')}: {spec.entry_point}")
    print(f"  Git: {_tri('initialized (main)', '已初始化（main）', '初期化済み（main）')}")
    if args.remote:
        print(f"  Git remote: {args.remote}")
    else:
        print(
            "  " + _tri("next", "下一步", "次の手順") + ": "
            + _tri(
                "add the matching GitHub repository as origin",
                "将匹配的 GitHub 仓库添加为 origin",
                "一致する GitHub リポジトリを origin として追加してください",
            )
        )
    return 0


def handle_setup_repo(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    if args.remote and not args.git:
        print("[FAIL] --remote requires --git", file=sys.stderr)
        return 1
    if args.dry_run and not args.upgrade_github_actions:
        print(
            "[FAIL] --dry-run requires --upgrade-github-actions / "
            "--dry-run 需要 --upgrade-github-actions / "
            "--dry-run には --upgrade-github-actions が必要です",
            file=sys.stderr,
        )
        return 1
    if args.upgrade_github_actions and args.overwrite:
        print(
            "[FAIL] --upgrade-github-actions cannot be used with --overwrite / "
            "--upgrade-github-actions 不能与 --overwrite 同时使用 / "
            "--upgrade-github-actions と --overwrite は併用できません",
            file=sys.stderr,
        )
        return 1

    try:
        plugin_dir = _resolve_existing_plugin_dir(args.plugin, args=args, defaults=defaults)
        source = load_plugin_source(plugin_dir)
        spec = PluginSpec(
            plugin_id=source.plugin_id,
            name=source.name,
            plugin_type=source.package_type,
            description=source.description,
            version=source.version,
            author_name=source.author_name,
            author_email=source.author_email,
            entry_point_override=source.entry_point,
            quick_start=True,
            create_pyproject=False,
            create_readme=not args.no_readme,
            create_tests=not args.no_tests,
            create_gitignore=not args.no_gitignore,
            create_vscode=not args.no_vscode,
            create_github_actions=args.github_actions or args.upgrade_github_actions,
        )
        if args.upgrade_github_actions:
            changes = migrate_github_actions(spec, plugin_dir, dry_run=args.dry_run)
            conflicts = [
                change
                for change in changes
                if change.status is ActionFileStatus.CONFLICT
            ]
            if conflicts:
                for change in conflicts:
                    print(f"[CONFLICT] {change.relative_path.as_posix()}", file=sys.stderr)
                print(
                    "No files were changed. / 未修改任何文件。 / "
                    "ファイルは変更されませんでした。",
                    file=sys.stderr,
                )
                return 1
            for change in changes:
                print(f"[{change.status}] {change.relative_path.as_posix()}")
            if args.dry_run:
                print(
                    "\nDry run; no files were changed. / "
                    "预览模式；未修改任何文件。 / "
                    "ドライランのため、ファイルは変更されませんでした。"
                )
            else:
                print(
                    "\n[OK] Standard GitHub Actions migration completed. / "
                    "标准 GitHub Actions 迁移完成。 / "
                    "標準 GitHub Actions の移行が完了しました。"
                )
            return 0
        _preflight_git_request(plugin_dir, initialize_git=args.git, remote=args.remote)
        created = generate_repo_support_files(
            spec,
            plugin_dir,
            repo_root=defaults.repo_root,
            overwrite=args.overwrite,
        )
        git_initialized = False
        if args.git:
            git_initialized = _initialize_git_repo(plugin_dir, remote=args.remote)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    print(f"\n[OK] 已配置 {plugin_dir}/")
    if created:
        for path in created:
            print(f"  └── {path.relative_to(plugin_dir)}")
    else:
        print("  support files already exist; use --overwrite to regenerate them")
    print(f"\n  plugin: {source.plugin_id}")
    print(f"  entry:  {source.entry_point}")
    if git_initialized:
        print("  git:    initialized")
        if args.remote:
            print(f"  remote: {args.remote}")
    elif args.git:
        print("  git:    skipped (already inside an existing repository)")
    return 0


def _market_repo_name(plugin_id: str) -> str:
    return f"{_MARKET_REPO_PREFIX}{plugin_id}"


# Exit code the UI reads as "gh cannot do this; show the manual steps" rather
# than a hard failure. 0 = done, 1 = failed, 3 = manual fallback needed.
MANUAL_FALLBACK_EXIT = 3


def handle_create_repo(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    try:
        plugin_dir = _resolve_existing_plugin_dir(args.plugin, args=args, defaults=defaults)
        source = load_plugin_source(plugin_dir)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    repo_name = _market_repo_name(source.plugin_id)

    existing_origin = _existing_origin_url(plugin_dir)
    if existing_origin is not None:
        repository = parse_github_repository_remote(existing_origin)
        if repository and repository.rsplit("/", 1)[-1].casefold() == repo_name.casefold():
            print(
                "[OK] "
                + _tri(
                    f"repository already configured: {repository}",
                    f"仓库已配置：{repository}",
                    f"リポジトリは既に設定済み：{repository}",
                )
            )
            return 0
        print(
            "[FAIL] "
            + _tri(
                f"git remote 'origin' already points to {existing_origin}; "
                f"expected a repository named {repo_name}",
                f"Git 远程 'origin' 已指向 {existing_origin}；应为名为 {repo_name} 的仓库",
                f"Git リモート 'origin' は既に {existing_origin} を指しています；{repo_name} という名前のリポジトリが必要です",
            ),
            file=sys.stderr,
        )
        return 1

    if not _gh_available() or not _gh_authenticated():
        _print_manual_repo_steps(plugin_dir, repo_name=repo_name)
        return MANUAL_FALLBACK_EXIT

    owner = args.owner or _gh_default_owner()
    if not owner:
        _print_manual_repo_steps(plugin_dir, repo_name=repo_name)
        return MANUAL_FALLBACK_EXIT
    slug = f"{owner}/{repo_name}"

    try:
        _initialize_git_repo(plugin_dir)
        _gh_create_repository(plugin_dir, slug=slug, private=args.private)
    except _RepositoryNameTaken:
        print(
            "[FAIL] "
            + _tri(
                f"a GitHub repository named {slug} already exists; "
                "choose a different owner or delete it first",
                f"名为 {slug} 的 GitHub 仓库已存在；请换一个拥有者或先删除它",
                f"{slug} という GitHub リポジトリが既に存在します；別のオーナーを選ぶか先に削除してください",
            ),
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    origin = _existing_origin_url(plugin_dir)
    repository = parse_github_repository_remote(origin) if origin else None
    if repository is None:
        print(
            "[FAIL] "
            + _tri(
                "the repository was created but origin was not wired to it",
                "仓库已创建，但 origin 未正确关联",
                "リポジトリは作成されましたが origin が正しく設定されていません",
            ),
            file=sys.stderr,
        )
        return 1
    print(
        "[OK] "
        + _tri(
            f"repository ready: https://github.com/{repository}",
            f"仓库已就绪：https://github.com/{repository}",
            f"リポジトリの準備完了：https://github.com/{repository}",
        )
    )
    return 0


class _RepositoryNameTaken(RuntimeError):
    """The GitHub repository name already exists."""


def _existing_origin_url(plugin_dir: Path) -> str | None:
    if _find_parent_git_dir(plugin_dir) is None:
        return None
    completed = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=plugin_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return None
    url = completed.stdout.strip()
    return url or None


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_authenticated() -> bool:
    try:
        completed = subprocess.run(
            ["gh", "auth", "status"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _gh_default_owner() -> str | None:
    try:
        completed = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    owner = completed.stdout.strip()
    return owner or None


def _gh_create_repository(plugin_dir: Path, *, slug: str, private: bool) -> None:
    visibility = "--private" if private else "--public"
    try:
        completed = subprocess.run(
            ["gh", "repo", "create", slug, visibility, "--source", ".", "--remote", "origin"],
            cwd=plugin_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("gh executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("gh repo create timed out after 120 seconds") from exc
    if completed.returncode == 0:
        return
    output = (completed.stdout or "").strip()
    if "already exists" in output.casefold() or "name already exists" in output.casefold():
        raise _RepositoryNameTaken(slug)
    raise RuntimeError(output or f"gh repo create {slug} failed")


def _print_manual_repo_steps(plugin_dir: Path, *, repo_name: str) -> None:
    """Steps to create the repository by hand when gh cannot do it."""
    print(
        "[MANUAL] "
        + _tri(
            "GitHub CLI (gh) is not available or not signed in; create the repository manually:",
            "GitHub CLI（gh）不可用或未登录；请手动创建仓库：",
            "GitHub CLI（gh）が利用できないかサインインしていません。手動でリポジトリを作成してください：",
        )
    )
    print(
        "  1. "
        + _tri(
            f"Create a GitHub repository named {repo_name} (public).",
            f"在 GitHub 上创建名为 {repo_name} 的公开仓库。",
            f"GitHub で {repo_name} という公開リポジトリを作成します。",
        )
    )
    print(
        "  2. "
        + _tri(
            "Copy its URL, then wire it as origin:",
            "复制其 URL，然后将其设为 origin：",
            "その URL をコピーして origin に設定します：",
        )
    )
    print(f"       neko-plugin setup-repo {plugin_dir} --git --remote <repository-url>")


def _remote_matches_plugin(remote: str, *, plugin_id: str) -> bool:
    repository = parse_github_repository_remote(remote)
    if repository is None:
        return False
    return repository.rsplit("/", 1)[-1].casefold() == _market_repo_name(
        plugin_id
    ).casefold()


def _resolve_plugins_root(args: argparse.Namespace, *, defaults: CliDefaults) -> Path:
    plugins_root = getattr(args, "plugins_root", None)
    if plugins_root:
        return Path(plugins_root).expanduser().resolve()
    return defaults.plugins_root


def _resolve_existing_plugin_dir(raw: str, *, args: argparse.Namespace, defaults: CliDefaults) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        plugin_dir = candidate.resolve()
    else:
        plugin_dir = (_resolve_plugins_root(args, defaults=defaults) / raw).resolve()

    plugin_toml = plugin_dir / "plugin.toml"
    if not plugin_toml.is_file():
        raise FileNotFoundError(f"plugin.toml not found for plugin '{raw}': {plugin_toml}")
    return plugin_dir


def _initialize_git_repo(target_dir: Path, *, remote: str | None = None) -> bool:
    existing_git = _find_parent_git_dir(target_dir)
    if existing_git is not None:
        if remote:
            raise RuntimeError("--remote can only be used when initializing a new git repository")
        return False
    _run_git(["init"], cwd=target_dir)
    if remote:
        _run_git(["remote", "add", "origin", remote], cwd=target_dir)
    return True


def _preflight_git_request(target_dir: Path, *, initialize_git: bool, remote: str | None = None) -> None:
    if not initialize_git:
        return
    existing_git = _find_parent_git_dir(target_dir)
    if existing_git is not None:
        if remote:
            raise RuntimeError("--remote can only be used when initializing a new git repository")
        return
    if shutil.which("git") is None:
        raise RuntimeError("git executable not found; install git or omit --git")


def _find_parent_git_dir(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        git_dir = candidate / ".git"
        if git_dir.exists():
            return git_dir
    return None


def _run_git(command: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(
            ["git", *command],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable not found; install git or omit --git") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"git {' '.join(command)} failed: {message}") from exc

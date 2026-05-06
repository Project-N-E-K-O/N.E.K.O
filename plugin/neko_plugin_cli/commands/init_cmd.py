"""neko-plugin init — interactive plugin scaffolding.

Flow:
  Page 1: plugin_id → name → type → quick start?
  If quick start: generate hello-world template and exit.
  Page 2: description → author → features → pyproject
  Generate files.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from ..paths import CliDefaults
from ..templates.generator import PluginSpec, generate_plugin, generate_repo_support_files
from ..core.plugin_source import load_plugin_source
from ._prompt import ask_checkbox, ask_confirm, ask_select, ask_text

_PLUGIN_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("init", help="Create a new plugin from template")
    parser.add_argument("plugin_id", nargs="?", help="Plugin ID (optional, will prompt if omitted)")
    parser.add_argument("--type", dest="plugin_type", choices=("plugin", "extension", "adapter"), help="Plugin type")
    parser.add_argument("--name", help="Display name")
    parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    parser.add_argument("--git", action="store_true", help="Initialize a git repository in the generated plugin directory")
    parser.add_argument("--remote", help="Add a git remote named origin after --git initialization")
    parser.add_argument("--github-actions", action="store_true", help="Generate a GitHub Actions verification workflow")
    parser.add_argument("--neko-repo", default="owner/N.E.K.O", help="N.E.K.O repository used by generated GitHub Actions")
    parser.add_argument("--neko-ref", default="main", help="N.E.K.O git ref used by generated GitHub Actions")
    parser.add_argument("--no-readme", action="store_true", help="Do not generate README.md")
    parser.add_argument("--no-tests", action="store_true", help="Do not generate tests/test_smoke.py")
    parser.add_argument("--no-gitignore", action="store_true", help="Do not generate .gitignore")
    parser.add_argument("--no-vscode", action="store_true", help="Do not generate VSCode settings and tasks")
    parser.add_argument("--no-interactive", action="store_true", help="Skip interactive prompts")
    parser.set_defaults(handler=handle, _defaults=defaults)

    repo_parser = subparsers.add_parser(
        "init-repo",
        help="Create a ready-to-use standalone plugin repository",
    )
    repo_parser.add_argument("plugin_id", help="Plugin ID")
    repo_parser.add_argument("--type", dest="plugin_type", choices=("plugin", "adapter"), default="plugin", help="Plugin type")
    repo_parser.add_argument("--name", help="Display name")
    repo_parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    repo_parser.add_argument("--remote", help="Add a git remote named origin after git initialization")
    repo_parser.add_argument("--no-git", action="store_true", help="Do not initialize a git repository")
    repo_parser.add_argument("--no-github-actions", action="store_true", help="Do not generate the GitHub Actions verification workflow")
    repo_parser.add_argument("--neko-repo", default="owner/N.E.K.O", help="N.E.K.O repository used by generated GitHub Actions")
    repo_parser.add_argument("--neko-ref", default="main", help="N.E.K.O git ref used by generated GitHub Actions")
    repo_parser.set_defaults(handler=handle_init_repo, _defaults=defaults)

    setup_parser = subparsers.add_parser(
        "setup-repo",
        help="Add repository support files to an existing plugin",
    )
    setup_parser.add_argument("plugin", help="Plugin directory name under plugin/plugins or explicit plugin path")
    setup_parser.add_argument("--plugins-root", help="Plugin root directory (default: N.E.K.O/plugin/plugins)")
    setup_parser.add_argument("--github-actions", action="store_true", help="Generate a GitHub Actions verification workflow")
    setup_parser.add_argument("--neko-repo", default="owner/N.E.K.O", help="N.E.K.O repository used by generated GitHub Actions")
    setup_parser.add_argument("--neko-ref", default="main", help="N.E.K.O git ref used by generated GitHub Actions")
    setup_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing support files")
    setup_parser.add_argument("--git", action="store_true", help="Initialize a git repository if this plugin directory is not already inside one")
    setup_parser.add_argument("--remote", help="Add a git remote named origin after --git initialization")
    setup_parser.add_argument("--no-readme", action="store_true", help="Do not generate README.md")
    setup_parser.add_argument("--no-tests", action="store_true", help="Do not generate tests/test_smoke.py")
    setup_parser.add_argument("--no-gitignore", action="store_true", help="Do not generate .gitignore")
    setup_parser.add_argument("--no-vscode", action="store_true", help="Do not generate VSCode settings and tasks")
    setup_parser.set_defaults(handler=handle_setup_repo, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    if getattr(args, "remote", None) and not getattr(args, "git", False):
        print("[FAIL] --remote requires --git", file=sys.stderr)
        return 1

    if args.no_interactive:
        return _handle_non_interactive(args, defaults=defaults)

    return _handle_interactive(args, defaults=defaults)


def handle_init_repo(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    initialize_git = not args.no_git
    if args.remote and not initialize_git:
        print("[FAIL] --remote requires git initialization; remove --no-git", file=sys.stderr)
        return 1

    init_args = argparse.Namespace(
        plugin_id=args.plugin_id,
        plugin_type=args.plugin_type,
        name=args.name,
        plugins_root=args.plugins_root,
        git=initialize_git,
        remote=args.remote,
        github_actions=not args.no_github_actions,
        neko_repo=args.neko_repo,
        neko_ref=args.neko_ref,
        no_readme=False,
        no_tests=False,
        no_gitignore=False,
        no_vscode=False,
    )
    return _handle_non_interactive(init_args, defaults=defaults)


def handle_setup_repo(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults
    if args.remote and not args.git:
        print("[FAIL] --remote requires --git", file=sys.stderr)
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
            create_github_actions=args.github_actions,
            neko_repository=args.neko_repo,
            neko_ref=args.neko_ref,
        )
        created = generate_repo_support_files(spec, plugin_dir, overwrite=args.overwrite)
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


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def _handle_interactive(args: argparse.Namespace, *, defaults: CliDefaults) -> int:
    # ── Page 1: Basic info ──

    # Plugin ID
    plugin_id = args.plugin_id
    if not plugin_id:
        plugin_id = ask_text(
            "插件 ID (Plugin ID)",
            validate=_validate_plugin_id,
        )
    if not plugin_id:
        return _cancelled()
    plugin_id = plugin_id.strip()
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        print(f"[FAIL] invalid plugin ID: '{plugin_id}' (use a valid Python package name: A-Z, a-z, 0-9, _)", file=sys.stderr)
        return 1

    # Check if directory already exists
    plugins_root = _resolve_plugins_root(args, defaults=defaults)
    target_dir = plugins_root / plugin_id
    if target_dir.exists():
        print(f"[FAIL] directory already exists: {target_dir}", file=sys.stderr)
        return 1

    # Display name
    name = args.name
    if not name:
        name = ask_text("显示名称 (Display Name)", default=plugin_id)
    if name is None:
        return _cancelled()

    # Plugin type
    plugin_type = args.plugin_type
    if not plugin_type:
        plugin_type = ask_select(
            "插件类型 (Plugin Type)",
            choices=[
                {"value": "plugin", "name": "Plugin — 独立功能插件"},
                {"value": "extension", "name": "Extension — 为现有插件添加路由/钩子"},
                {"value": "adapter", "name": "Adapter — 对接外部协议 (MCP 等)"},
            ],
            default="plugin",
        )
    if not plugin_type:
        return _cancelled()

    # Quick start? Extensions must collect host plugin settings first.
    if plugin_type == "extension":
        quick_start = False
    else:
        quick_start = ask_confirm("快速开始? (生成 Hello World 模板，跳过高级配置)", default=True)
        if quick_start is None:
            return _cancelled()

    if quick_start:
        spec = PluginSpec(
            plugin_id=plugin_id,
            name=name,
            plugin_type=plugin_type,
            quick_start=True,
            features=["lifecycle", "entry_point"],
            create_readme=not getattr(args, "no_readme", False),
            create_tests=not getattr(args, "no_tests", False),
            create_gitignore=not getattr(args, "no_gitignore", False),
            create_vscode=not getattr(args, "no_vscode", False),
            create_github_actions=getattr(args, "github_actions", False),
            neko_repository=getattr(args, "neko_repo", "owner/N.E.K.O"),
            neko_ref=getattr(args, "neko_ref", "main"),
        )
        return _generate_and_report(
            spec,
            target_dir,
            initialize_git=getattr(args, "git", False),
            remote=getattr(args, "remote", None),
        )

    # ── Page 2: Advanced config ──

    # Description
    description = ask_text("插件描述 (Description)", default="")
    if description is None:
        return _cancelled()

    # Author
    author_name = ask_text("作者名称 (Author Name)", default="")
    if author_name is None:
        return _cancelled()

    author_email = ""
    if author_name:
        author_email_value = ask_text("作者邮箱 (Author Email)", default="")
        if author_email_value is None:
            return _cancelled()
        author_email = author_email_value

    # Extension-specific: host plugin
    host_plugin_id = ""
    host_prefix = ""
    if plugin_type == "extension":
        host_plugin_id_value = ask_text("宿主插件 ID (Host Plugin ID)")
        if host_plugin_id_value is None:
            return _cancelled()
        host_plugin_id = host_plugin_id_value.strip()
        if not host_plugin_id:
            print("[FAIL] extension type requires a host plugin ID", file=sys.stderr)
            return 1
        if not _PLUGIN_ID_RE.fullmatch(host_plugin_id):
            print(f"[FAIL] invalid host plugin ID: '{host_plugin_id}'", file=sys.stderr)
            return 1
        host_prefix_value = ask_text("路由前缀 (Route Prefix)", default="")
        if host_prefix_value is None:
            return _cancelled()
        host_prefix = host_prefix_value.strip()

    # Features
    feature_choices = _get_feature_choices(plugin_type)
    default_features = ["lifecycle", "entry_point"]
    features = ask_checkbox(
        "选择功能 (Features)",
        choices=feature_choices,
        defaults=default_features,
    )
    if features is None:
        return _cancelled()

    # pyproject.toml
    create_pyproject = ask_confirm("创建 pyproject.toml?", default=True)
    if create_pyproject is None:
        return _cancelled()

    spec = PluginSpec(
        plugin_id=plugin_id,
        name=name,
        plugin_type=plugin_type,
        description=description,
        author_name=author_name,
        author_email=author_email,
        host_plugin_id=host_plugin_id,
        host_prefix=host_prefix,
        features=features,
        create_pyproject=create_pyproject,
        create_readme=not getattr(args, "no_readme", False),
        create_tests=not getattr(args, "no_tests", False),
        create_gitignore=not getattr(args, "no_gitignore", False),
        create_vscode=not getattr(args, "no_vscode", False),
        create_github_actions=getattr(args, "github_actions", False),
        neko_repository=getattr(args, "neko_repo", "owner/N.E.K.O"),
        neko_ref=getattr(args, "neko_ref", "main"),
    )
    return _generate_and_report(
        spec,
        target_dir,
        initialize_git=getattr(args, "git", False),
        remote=getattr(args, "remote", None),
    )


# ---------------------------------------------------------------------------
# Non-interactive flow
# ---------------------------------------------------------------------------

def _handle_non_interactive(args: argparse.Namespace, *, defaults: CliDefaults) -> int:
    plugin_id = args.plugin_id
    if not plugin_id:
        print("[FAIL] plugin_id is required in non-interactive mode", file=sys.stderr)
        return 1
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        print(f"[FAIL] invalid plugin ID: '{plugin_id}'", file=sys.stderr)
        return 1

    plugins_root = _resolve_plugins_root(args, defaults=defaults)
    target_dir = plugins_root / plugin_id
    if target_dir.exists():
        print(f"[FAIL] directory already exists: {target_dir}", file=sys.stderr)
        return 1

    plugin_type = args.plugin_type or "plugin"
    initialize_git = getattr(args, "git", False)
    if plugin_type == "extension":
        print("[FAIL] --type extension requires interactive setup for host plugin ID", file=sys.stderr)
        return 1

    spec = PluginSpec(
        plugin_id=plugin_id,
        name=args.name or plugin_id,
        plugin_type=plugin_type,
        quick_start=True,
        features=["lifecycle", "entry_point"],
        create_readme=not getattr(args, "no_readme", False),
        create_tests=not getattr(args, "no_tests", False),
        create_gitignore=not getattr(args, "no_gitignore", False),
        create_vscode=not getattr(args, "no_vscode", False),
        create_github_actions=getattr(args, "github_actions", False),
        neko_repository=getattr(args, "neko_repo", "owner/N.E.K.O"),
        neko_ref=getattr(args, "neko_ref", "main"),
    )
    return _generate_and_report(spec, target_dir, initialize_git=initialize_git, remote=getattr(args, "remote", None))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_and_report(
    spec: PluginSpec,
    target_dir: Path,
    *,
    initialize_git: bool = False,
    remote: str | None = None,
) -> int:
    try:
        created = generate_plugin(spec, target_dir)
        git_initialized = False
        if initialize_git:
            git_initialized = _initialize_git_repo(target_dir, remote=remote)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    print(f"\n[OK] 已创建 {target_dir}/")
    for path in created:
        print(f"  └── {path.relative_to(target_dir)}")
    print(f"\n  入口类: {spec.class_name}")
    print(f"  entry:  {spec.entry_point}")
    if git_initialized:
        print("  git:    initialized")
        if remote:
            print(f"  remote: {remote}")
    elif initialize_git:
        print("  git:    skipped (already inside an existing repository)")
    return 0


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


def _cancelled() -> int:
    print("\n已取消。", file=sys.stderr)
    return 1


def _validate_plugin_id(text: str) -> bool | str:
    text = text.strip()
    if not text:
        return "Plugin ID 不能为空"
    if not _PLUGIN_ID_RE.fullmatch(text):
        return "必须是合法 Python 包名：字母或下划线开头，只允许字母、数字、下划线"
    return True


def _get_feature_choices(plugin_type: str) -> list[dict[str, str]]:
    """Return feature choices appropriate for the plugin type."""
    choices = [
        {"value": "lifecycle", "name": "生命周期 (startup/shutdown)"},
        {"value": "entry_point", "name": "入口点 (plugin_entry)"},
        {"value": "timer", "name": "定时任务 (timer_interval)"},
        {"value": "message", "name": "消息处理 (message handler)"},
        {"value": "store", "name": "持久化存储 (PluginStore)"},
        {"value": "cross_plugin", "name": "跨插件调用 (self.plugins)"},
        {"value": "static_ui", "name": "静态 Web UI"},
        {"value": "async_support", "name": "异步支持 (async entry points)"},
        {"value": "bus_events", "name": "事件总线 (Bus pub/sub)"},
        {"value": "settings", "name": "类型安全配置 (PluginSettings)"},
    ]

    if plugin_type == "extension":
        # Extensions don't need some features
        skip = {"timer", "message", "static_ui"}
        choices = [c for c in choices if c["value"] not in skip]

    return choices

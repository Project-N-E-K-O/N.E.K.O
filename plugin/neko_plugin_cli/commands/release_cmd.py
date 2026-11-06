"""Repository health and release readiness commands."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

from ..core import inspect_package, build_plugin
from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ..plugin_import_alias import PLUGIN_ID_ENV, PLUGIN_SOURCE_ENV
from ..repo_state import committed_file_sizes, git_output, worktree_dirty_paths
from ._resolve import parse_github_repository_remote, resolve_plugin_dir_candidate
from .validate_cmd import validate_plugin_dir


Issue = tuple[str, str]
_MARKET_REPO_PREFIX = "n.e.k.o_plugin_"
_MARKET_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DIRTY_PATHS_SHOWN = 5
_WORKTREE_NOT_COMMITTED = "git working tree has uncommitted or untracked files"
# Market limits (N.E.K.O-PluginMarket app/core/plugin_identity.py, app/core/plugin_version.py).
_MARKET_MAX_PLUGIN_ID_CHARS = 100
_MARKET_MAX_VERSION_CHARS = 50
# Limits of the Market's security scan of the source archive GitHub builds for
# the release commit (N.E.K.O-PluginMarket app/services/plugin_security.py).
_MARKET_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MARKET_MAX_FILE_BYTES = 2 * 1024 * 1024
_MARKET_MAX_ARCHIVE_ENTRIES = 5000
_MARKET_BINARY_SUFFIXES = frozenset(
    {
        ".exe", ".dll", ".pyd", ".so", ".dylib", ".wasm", ".zip", ".whl", ".pyc",
        ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".neko-plugin", ".neko-bundle",
    }
)
# The scan streams media and model files instead of reading them, so the
# per-file limit does not apply to them.
_MARKET_ASSET_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp3", ".mp4", ".wav",
        ".woff", ".woff2", ".ttf", ".onnx", ".pt", ".safetensors", ".gguf",
    }
)


def _tri(english: str, chinese: str, japanese: str) -> str:
    return f"{english} / {chinese} / {japanese}"


def handle_check(args: argparse.Namespace) -> int:
    command_label = getattr(args, "_command_label", "check")
    try:
        defaults = args._defaults.with_plugins_root(getattr(args, "plugins_root", None))
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
        source = load_plugin_source(plugin_dir)
        issues = validate_plugin_dir(
            plugin_dir,
            strict=args.strict,
            require_matching_directory_name=_is_builtin_source_plugin_dir(
                plugin_dir,
                defaults,
            ),
        )
        issues.extend(_diagnose_repository(plugin_dir, defaults=defaults))
    except Exception as exc:
        print(f"[FAIL] {command_label}: {exc}", file=sys.stderr)
        return 1

    errors = [issue for issue in issues if issue[0] == "error"]
    warnings = [issue for issue in issues if issue[0] == "warning"]

    status = "[FAIL]" if errors else "[OK]"
    stream = sys.stderr if errors else sys.stdout
    print(f"{status} {source.plugin_id}: {command_label} found {len(errors)} error(s), {len(warnings)} warning(s)", file=stream)
    print(f"  path={plugin_dir}")
    print(f"  version={source.version}")
    print(f"  entry={source.entry_point}")
    _print_issues(issues, plugin_id=source.plugin_id, plugin_dir=plugin_dir, show_fixes=True)
    return 1 if errors else 0


def handle_release_check(args: argparse.Namespace) -> int:
    command_label = getattr(args, "_command_label", "check --release")
    try:
        defaults = args._defaults.with_plugins_root(getattr(args, "plugins_root", None))
        plugin_dir = resolve_plugin_dir_candidate(args.plugin, defaults=defaults)
        source = load_plugin_source(plugin_dir)
        issues = validate_plugin_dir(
            plugin_dir,
            strict=True,
            require_matching_directory_name=_is_builtin_source_plugin_dir(
                plugin_dir,
                defaults,
            ),
        )
        # Both Market workflows reject such an ID before anything else runs.
        issues.extend(_diagnose_market_plugin_id(source.plugin_id))
        # CI builds the pushed commit, not the working tree.
        issues.extend(_diagnose_repository(plugin_dir, defaults=defaults, release=True))
        if getattr(args, "market_release", False):
            issues.extend(
                _diagnose_market_release(
                    plugin_dir,
                    repository_root=_repository_root(plugin_dir, defaults),
                    plugin_id=source.plugin_id,
                    version=source.version,
                    github_repository=getattr(args, "_release_repository", None),
                    ref_name=getattr(args, "_release_ref_name", None),
                )
            )
        errors = [issue for issue in issues if issue[0] == "error"]
        if errors:
            print(f"[FAIL] {source.plugin_id}: {command_label} blocked by validation errors", file=sys.stderr)
            _print_issues(issues, plugin_id=source.plugin_id, plugin_dir=plugin_dir, show_fixes=True)
            return 1

        test_result = _run_tests(
            plugin_dir,
            plugin_id=source.plugin_id,
            defaults=defaults,
            skip_tests=args.skip_tests,
        )
        target_dir = Path(args.target_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        package_path = target_dir / f"{source.plugin_id}.neko-plugin"
        build_result = build_plugin(plugin_dir, package_path)
        inspect_result = inspect_package(build_result.package_path)
        if inspect_result.payload_hash_verified is not True:
            print("[FAIL] package payload hash verification failed", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"[FAIL] {command_label}: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] {source.plugin_id}: {command_label} passed")
    print(f"  version={source.version}")
    print(f"  package={build_result.package_path}")
    print(f"  package_sha256={_sha256_file(build_result.package_path)}")
    print(f"  payload_hash={inspect_result.payload_hash}")
    print(f"  payload_hash_verified={inspect_result.payload_hash_verified}")
    print(f"  tests={test_result}")
    for severity, message in issues:
        if severity == "warning":
            print(f"  [WARNING] {message}")
    return 0


def _is_builtin_source_plugin_dir(plugin_dir: Path, defaults: CliDefaults) -> bool:
    built_in_plugins_root = (defaults.plugin_root / "plugins").resolve()
    return (
        defaults.plugins_root.resolve() == built_in_plugins_root
        and plugin_dir.parent.resolve() == built_in_plugins_root
    )


def _repository_root(plugin_dir: Path, defaults: CliDefaults) -> Path | None:
    """The git repository that holds the plugin source, or None.

    The plugin may sit in a subdirectory of its repository. A plugin at
    ``<checkout>/plugin/plugins/<id>`` belongs to that N.E.K.O tree, whether
    it is the checkout running the CLI or the one Market CI copies the plugin
    (and any ``.git`` it carries) into; it has no repository of its own to judge.
    """
    parent = plugin_dir.resolve().parent
    if parent.name == "plugins" and parent.parent.name == "plugin":
        return None
    toplevel = git_output(plugin_dir, "rev-parse", "--show-toplevel")
    if not toplevel:
        return None
    root = Path(toplevel).resolve()
    return None if root == defaults.repo_root.resolve() else root


def _diagnose_repository(plugin_dir: Path, *, defaults: CliDefaults, release: bool = False) -> list[Issue]:
    """Local git readiness; with ``release`` an uncommitted tree is an error."""
    if shutil.which("git") is None:
        return [("warning", "git executable not found")]

    repository_root = _repository_root(plugin_dir, defaults)
    if repository_root is None:
        return [("warning", "plugin source directory does not have its own git repository")]

    issues: list[Issue] = []
    # check --market-release reports a missing origin as an error itself.
    if not release and not git_output(repository_root, "remote", "get-url", "origin"):
        issues.append(("warning", "git remote 'origin' is not configured"))

    dirty = worktree_dirty_paths(plugin_dir)
    if dirty is None:
        issues.append(("warning", "git status failed"))
    elif dirty:
        shown = _shown_paths(dirty)
        issues.append(
            (
                "error" if release else "warning",
                _tri(
                    f"{_WORKTREE_NOT_COMMITTED}: {shown}",
                    f"Git 工作区有未提交或未跟踪的文件：{shown}",
                    f"Git 作業ツリーに未コミットまたは未追跡のファイルがあります: {shown}",
                ),
            )
        )
    return issues


def _diagnose_market_plugin_id(plugin_id: str) -> list[Issue]:
    if len(plugin_id) > _MARKET_MAX_PLUGIN_ID_CHARS:
        return [
            (
                "error",
                _tri(
                    f"Market plugin ID must be at most {_MARKET_MAX_PLUGIN_ID_CHARS} characters, got {len(plugin_id)}",
                    f"Market 插件 ID 最多 {_MARKET_MAX_PLUGIN_ID_CHARS} 个字符，当前为 {len(plugin_id)} 个",
                    f"Market プラグイン ID は {_MARKET_MAX_PLUGIN_ID_CHARS} 文字以内である必要があります。現在: {len(plugin_id)} 文字",
                ),
            )
        ]
    if _MARKET_PLUGIN_ID_RE.fullmatch(plugin_id) is not None:
        return []
    return [
        (
            "error",
            _tri(
                f"Market plugin ID must match ^[a-z][a-z0-9_]*$, got {plugin_id}",
                f"Market 插件 ID 必须符合 ^[a-z][a-z0-9_]*$，当前为 {plugin_id}",
                f"Market プラグイン ID は ^[a-z][a-z0-9_]*$ に一致する必要があります。現在値: {plugin_id}",
            ),
        )
    ]


def _diagnose_market_release(
    plugin_dir: Path,
    *,
    repository_root: Path | None,
    plugin_id: str,
    version: str,
    github_repository: str | None = None,
    ref_name: str | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    expected_repo = f"{_MARKET_REPO_PREFIX}{plugin_id}"
    if github_repository is None:
        github_repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    remote_url = ""
    if repository_root is not None:
        remote_url = git_output(repository_root, "remote", "get-url", "origin") or ""
    repo_name: str | None = None
    if github_repository:
        parts = github_repository.split("/")
        if len(parts) == 2 and all(parts):
            repo_name = parts[1]
        else:
            issues.append(
                (
                    "error",
                    _tri(
                        "GITHUB_REPOSITORY must look like owner/repo",
                        "GITHUB_REPOSITORY 必须采用 owner/repo 格式",
                        "GITHUB_REPOSITORY は owner/repo 形式である必要があります",
                    ),
                )
            )
    elif remote_url:
        repository = parse_github_repository_remote(remote_url)
        if repository is None:
            issues.append(
                (
                    "error",
                    _tri(
                        "git remote 'origin' must point to GitHub for market release",
                        "Market 发布要求 Git remote 'origin' 指向 GitHub",
                        "Market 公開では Git remote 'origin' が GitHub を指す必要があります",
                    ),
                )
            )
        else:
            repo_name = repository.rsplit("/", 1)[-1]
    else:
        issues.append(
            (
                "error",
                _tri(
                    "git remote 'origin' is not configured for market release",
                    "尚未为 Market 发布配置 Git remote 'origin'",
                    "Market 公開用の Git remote 'origin' が設定されていません",
                ),
            )
        )

    if repo_name is not None and repo_name.casefold() != expected_repo.casefold():
        issues.append(("warning", f"recommended market repository name is {expected_repo}, got {repo_name}"))

    if len(version) > _MARKET_MAX_VERSION_CHARS:
        issues.append(
            (
                "error",
                _tri(
                    f"Market version must be at most {_MARKET_MAX_VERSION_CHARS} characters, got {len(version)}",
                    f"Market 版本号最多 {_MARKET_MAX_VERSION_CHARS} 个字符，当前为 {len(version)} 个",
                    f"Market のバージョンは {_MARKET_MAX_VERSION_CHARS} 文字以内である必要があります。現在: {len(version)} 文字",
                ),
            )
        )

    if ref_name is None:
        ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    if ref_name:
        ref_version = ref_name[1:] if ref_name.startswith(("v", "V")) else ref_name
        if ref_version != version:
            issues.append(("error", f"release tag {ref_name} does not match plugin.toml version {version}"))
    else:
        issues.append(("warning", "GITHUB_REF_NAME is missing; tag/version alignment was not checked"))

    # CI reads release.yml at the repository root, also for a plugin in a subdirectory.
    release_workflow = (repository_root or plugin_dir) / ".github" / "workflows" / "release.yml"
    if not release_workflow.is_file():
        issues.append(("error", ".github/workflows/release.yml is missing"))

    if repository_root is not None:
        issues.extend(_diagnose_market_source_archive(repository_root))

    return issues


def _diagnose_market_source_archive(repository_root: Path) -> list[Issue]:
    """The Market's scan limits for the source archive of the HEAD commit."""
    files = committed_file_sizes(repository_root)
    if files is None:
        return [("warning", "git ls-tree failed; Market source archive limits were not checked")]

    issues: list[Issue] = []
    binaries = [path for path, _ in files if PurePosixPath(path).suffix.lower() in _MARKET_BINARY_SUFFIXES]
    if binaries:
        shown = _shown_paths(binaries)
        issues.append(
            (
                "error",
                _tri(
                    f"the Market rejects binary files in the repository: {shown}",
                    f"Market 拒绝仓库中的二进制文件：{shown}",
                    f"Market はリポジトリ内のバイナリファイルを拒否します: {shown}",
                ),
            )
        )
    oversized = [
        path
        for path, size in files
        if size > _MARKET_MAX_FILE_BYTES
        and PurePosixPath(path).suffix.lower() not in _MARKET_BINARY_SUFFIXES | _MARKET_ASSET_SUFFIXES
    ]
    if oversized:
        shown = _shown_paths(oversized)
        issues.append(
            (
                "error",
                _tri(
                    f"the Market rejects files over 2 MiB: {shown}",
                    f"Market 拒绝超过 2 MiB 的文件：{shown}",
                    f"Market は 2 MiB を超えるファイルを拒否します: {shown}",
                ),
            )
        )
    # GitHub's source zip holds one entry per file and per directory, under a
    # top-level directory of its own.
    directories = {parent for path, _ in files for parent in PurePosixPath(path).parents}
    entries = len(files) + len(directories)
    if entries > _MARKET_MAX_ARCHIVE_ENTRIES:
        issues.append(
            (
                "error",
                _tri(
                    f"the Market rejects a source archive with more than {_MARKET_MAX_ARCHIVE_ENTRIES} "
                    f"files and directories, got {entries}",
                    f"Market 拒绝文件和目录总数超过 {_MARKET_MAX_ARCHIVE_ENTRIES} 的源码包，当前为 {entries}",
                    f"Market はファイルとディレクトリが合計 {_MARKET_MAX_ARCHIVE_ENTRIES} を超えるソースアーカイブを拒否します。現在: {entries}",
                ),
            )
        )
    total = sum(size for _, size in files)
    if total > _MARKET_MAX_ARCHIVE_BYTES:
        mib = f"{total / (1024 * 1024):.1f} MiB"
        issues.append(
            (
                "error",
                _tri(
                    f"the Market rejects a source archive over 32 MiB; committed files total {mib}",
                    f"Market 拒绝超过 32 MiB 的源码包；已提交文件共 {mib}",
                    f"Market は 32 MiB を超えるソースアーカイブを拒否します。コミット済みファイルの合計: {mib}",
                ),
            )
        )
    return issues


def _shown_paths(paths: list[str]) -> str:
    shown = ", ".join(paths[:_DIRTY_PATHS_SHOWN])
    if len(paths) > _DIRTY_PATHS_SHOWN:
        shown += f" (+{len(paths) - _DIRTY_PATHS_SHOWN})"
    return shown


def _run_tests(plugin_dir: Path, *, plugin_id: str, defaults: CliDefaults, skip_tests: bool) -> str:
    tests_dir = plugin_dir / "tests"
    if skip_tests:
        return "skipped"
    if not tests_dir.is_dir():
        return "not-found"
    command = [sys.executable, "-m", "pytest"]
    env: dict[str, str] | None = None
    pytest_ini = defaults.repo_root / "pytest.ini"
    if pytest_ini.is_file():
        # CI runs the tests from plugin/plugins/<id> of a N.E.K.O checkout, where
        # pytest picks up that checkout's pytest.ini; use the same configuration.
        command += ["-c", str(pytest_ini), "--rootdir", str(defaults.repo_root)]
        if (defaults.plugin_root / "plugins" / plugin_id).resolve() != plugin_dir.resolve():
            # Out of tree: import plugin.plugins.<id> from here instead of a mount.
            command += ["-p", "plugin.neko_plugin_cli.plugin_import_alias"]
            env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    filter(None, [str(defaults.repo_root), os.environ.get("PYTHONPATH")])
                ),
                PLUGIN_ID_ENV: plugin_id,
                PLUGIN_SOURCE_ENV: str(plugin_dir),
            }
    completed = subprocess.run(
        [*command, str(tests_dir)],
        cwd=plugin_dir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        output = completed.stdout.strip()
        if output:
            print(output, file=sys.stderr)
        raise RuntimeError(f"tests failed with exit code {completed.returncode}")
    return "passed"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _print_issues(
    issues: list[Issue],
    *,
    plugin_id: str = "",
    plugin_dir: Path | None = None,
    show_fixes: bool = False,
) -> None:
    for severity, message in issues:
        stream = sys.stderr if severity == "error" else sys.stdout
        print(f"  [{severity.upper()}] {message}", file=stream)
        if show_fixes:
            fix = _suggest_fix(message, plugin_id=plugin_id, plugin_dir=plugin_dir)
            if fix:
                print(f"    fix: {fix}", file=stream)


def _suggest_fix(message: str, *, plugin_id: str, plugin_dir: Path | None) -> str:
    label = plugin_id or "<plugin>"
    if message.endswith("is missing"):
        missing = message.removesuffix(" is missing")
        if missing == "pyproject.toml":
            return "add pyproject.toml when this plugin needs standalone metadata or build rules"
        if missing in {
            "README.md",
            "tests/test_smoke.py",
            ".vscode/settings.json",
            ".vscode/tasks.json",
            ".github/workflows/verify.yml",
            ".github/workflows/release.yml",
            ".gitignore",
        }:
            return f"neko-plugin setup-repo {label} --github-actions"
    if message.startswith("recommended market repository name is "):
        return "the Market accepts any public GitHub repository name; n.e.k.o_plugin_<plugin_id> is the convention"
    if message.startswith("release tag ") and "does not match plugin.toml version" in message:
        return "update plugin.toml [plugin].version or push a matching tag such as v0.1.0"
    if message == "[plugin.sdk] is missing":
        return "add a [plugin.sdk] table to plugin.toml with recommended and supported SDK ranges"
    if message.startswith("plugin.entry should usually start with"):
        return "check plugin.toml [plugin].entry and make sure it points at the plugin entry class"
    if message.startswith("plugin.id ") and "does not match directory name" in message:
        return _tri(
            "rename the directory to match the plugin id",
            "将目录重命名为与插件 ID 相同的名称",
            "ディレクトリ名をプラグイン ID と同じ名前に変更してください",
        )
    if message.startswith(".gitignore should include "):
        pattern = message.removeprefix(".gitignore should include ")
        return f"add {pattern} to .gitignore"
    if message == "plugin source directory does not have its own git repository":
        if plugin_dir is None:
            return "run git init inside the plugin directory"
        return f"cd {plugin_dir} && git init"
    if message == "git remote 'origin' is not configured":
        return "git remote add origin <repo-url>"
    if message.startswith(_WORKTREE_NOT_COMMITTED):
        return _tri(
            "commit these files or add them to .gitignore; CI checks the pushed commit, not the working tree",
            "提交这些文件或将其加入 .gitignore；CI 检查的是已推送的提交，而不是工作区",
            "これらのファイルをコミットするか .gitignore に追加してください。CI は作業ツリーではなくプッシュ済みのコミットを検査します",
        )
    if message == "git executable not found":
        return "install git, then rerun neko-plugin check"
    return ""

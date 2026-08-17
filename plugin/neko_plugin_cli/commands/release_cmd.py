"""Repository health and release readiness commands."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import inspect_package, build_plugin
from ..core.plugin_source import load_plugin_source
from ..paths import CliDefaults
from ._resolve import parse_github_repository_remote, resolve_plugin_dir_candidate
from .validate_cmd import validate_plugin_dir


Issue = tuple[str, str]
_MARKET_REPO_PREFIX = "n.e.k.o_plugin_"
_MARKET_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _tri(english: str, chinese: str, japanese: str) -> str:
    return f"{english} / {chinese} / {japanese}"


def handle_check(args: argparse.Namespace) -> int:
    command_label = getattr(args, "_command_label", "check")
    try:
        defaults = _defaults_from_args(args, defaults=args._defaults)
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
        issues.extend(_diagnose_repository(plugin_dir))
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
        defaults = _defaults_from_args(args, defaults=args._defaults)
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
        if getattr(args, "market_release", False):
            issues.extend(
                _diagnose_market_release(
                    plugin_dir,
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

        test_result = _run_tests(plugin_dir, skip_tests=args.skip_tests)
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


def _defaults_from_args(args: argparse.Namespace, *, defaults: CliDefaults) -> CliDefaults:
    plugins_root = getattr(args, "plugins_root", None)
    if not plugins_root:
        return defaults
    return CliDefaults(
        plugin_root=defaults.plugin_root,
        target_dir=defaults.target_dir,
        plugins_root=Path(plugins_root).expanduser().resolve(),
        profiles_root=defaults.profiles_root,
    )


def _is_builtin_source_plugin_dir(plugin_dir: Path, defaults: CliDefaults) -> bool:
    built_in_plugins_root = (defaults.plugin_root / "plugins").resolve()
    return (
        defaults.plugins_root.resolve() == built_in_plugins_root
        and plugin_dir.parent.resolve() == built_in_plugins_root
    )


def _diagnose_repository(plugin_dir: Path) -> list[Issue]:
    issues: list[Issue] = []
    if shutil.which("git") is None:
        return [("warning", "git executable not found")]

    if not (plugin_dir / ".git").exists():
        issues.append(("warning", "plugin source directory does not have its own git repository"))
        return issues

    remote = _run_git(["remote", "get-url", "origin"], cwd=plugin_dir)
    if remote.returncode != 0 or not remote.stdout.strip():
        issues.append(("warning", "git remote 'origin' is not configured"))

    status = _run_git(["status", "--porcelain"], cwd=plugin_dir)
    if status.returncode != 0:
        issues.append(("warning", "git status failed"))
    elif status.stdout.strip():
        issues.append(("warning", "git working tree has uncommitted changes"))

    return issues


def _diagnose_market_release(
    plugin_dir: Path,
    *,
    plugin_id: str,
    version: str,
    github_repository: str | None = None,
    ref_name: str | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if _MARKET_PLUGIN_ID_RE.fullmatch(plugin_id) is None:
        issues.append(
            (
                "error",
                _tri(
                    f"Market plugin ID must match ^[a-z][a-z0-9_]*$, got {plugin_id}",
                    f"Market 插件 ID 必须符合 ^[a-z][a-z0-9_]*$，当前为 {plugin_id}",
                    f"Market プラグイン ID は ^[a-z][a-z0-9_]*$ に一致する必要があります。現在値: {plugin_id}",
                ),
            )
        )
    expected_repo = f"{_MARKET_REPO_PREFIX}{plugin_id}"
    if github_repository is None:
        github_repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    remote_url = ""
    if (plugin_dir / ".git").exists():
        remote = _run_git(["remote", "get-url", "origin"], cwd=plugin_dir)
        if remote.returncode == 0:
            remote_url = remote.stdout.strip()
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
        issues.append(("error", f"market repository name must be {expected_repo}, got {repo_name}"))

    if ref_name is None:
        ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    if ref_name:
        ref_version = ref_name[1:] if ref_name.startswith(("v", "V")) else ref_name
        if ref_version != version:
            issues.append(("error", f"release tag {ref_name} does not match plugin.toml version {version}"))
    else:
        issues.append(("warning", "GITHUB_REF_NAME is missing; tag/version alignment was not checked"))

    release_workflow = plugin_dir / ".github" / "workflows" / "release.yml"
    if not release_workflow.is_file():
        issues.append(("error", ".github/workflows/release.yml is missing"))

    return issues


def _run_tests(plugin_dir: Path, *, skip_tests: bool) -> str:
    tests_dir = plugin_dir / "tests"
    if skip_tests:
        return "skipped"
    if not tests_dir.is_dir():
        return "not-found"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir)],
        cwd=plugin_dir,
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


def _run_git(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *command],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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
    if message.startswith("market repository name must be "):
        return "set origin to a GitHub repository named n.e.k.o_plugin_<plugin_id>"
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
    if message == "git working tree has uncommitted changes":
        return "commit or stash changes before publishing"
    if message == "git executable not found":
        return "install git, then rerun neko-plugin check"
    return ""

"""Unit tests for the plugin-manager locale groups of ``scripts/check_i18n_sync.py``
and ``scripts/check_pr_report.py``.

The sync check runs against a throwaway git repository so that the
auto-discovered ``src/i18n/<module>/`` groups are exercised end to end.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
I18N = "frontend/plugin-manager/src/i18n"
LOCALES = ["en-US", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"]
SPLIT_DIRS = [
    "power", "plugin-consent", "plugin-permissions", "authoring",
    "model-api", "model-bindings", "market-feedback", "model-binding-errors",
]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = _load("check_i18n_sync")
REPORT = _load("check_pr_report")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Inside a git hook these point at the outer repository; drop every
    # repository-local variable git knows of (GIT_DIR, GIT_COMMON_DIR,
    # GIT_INDEX_FILE ...) so the throwaway repository below stays isolated.
    local_vars = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    ).stdout.split()
    for var in {*local_vars, "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}:
        monkeypatch.delenv(var, raising=False)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    for locale in LOCALES:
        # The en-US file carries the type, so the files are not line-aligned.
        extra = "export type W = typeof messages\n" if locale == "en-US" else ""
        _write(tmp_path, f"{I18N}/widgets/{locale}.ts", f"const messages = {{\n  a: '{locale}',\n}}\n{extra}")
        _write(tmp_path, f"{I18N}/locales/{locale}.ts", f"export default {{\n  a: '{locale}',\n}}\n")
    _write(tmp_path, f"{I18N}/widgets/index.ts", "export type { W } from './en-US'\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "branch", "base")
    monkeypatch.setattr(SYNC, "REPO_ROOT", tmp_path)
    return tmp_path


def _commit(repo: Path, files: dict[str, str]) -> int:
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return SYNC.main(["--base", "base"])


def _add_key(repo: Path, rel: str, at_line: int) -> str:
    lines = (repo / rel).read_text(encoding="utf-8").splitlines(keepends=True)
    lines.insert(at_line, "  b: 'new',\n")
    return "".join(lines)


def test_split_dir_missing_one_language_fails(repo, capsys):
    rc = _commit(repo, {
        f"{I18N}/widgets/{locale}.ts": _add_key(repo, f"{I18N}/widgets/{locale}.ts", 2)
        for locale in LOCALES if locale != "ko"
    })
    assert rc == 1
    out = capsys.readouterr().out
    assert "plugin-manager i18n widgets/" in out
    assert f"{I18N}/widgets/ko.ts" in out


def test_uncommitted_rename_does_not_hide_a_group(repo, capsys):
    # Groups come from HEAD, the snapshot the diff is taken from: renaming the
    # directory in the working tree afterwards must not skip the check.
    rel = f"{I18N}/widgets/en-US.ts"
    assert _commit(repo, {rel: _add_key(repo, rel, 2)}) == 1
    capsys.readouterr()
    (repo / I18N / "widgets").rename(repo / I18N / "widgets-next")
    assert SYNC.main(["--base", "base"]) == 1
    assert f"{I18N}/widgets/ko.ts" in capsys.readouterr().out


def test_split_dir_all_languages_pass_without_line_alignment(repo):
    # Different insertion lines per file: hunk ranges differ, which is fine here.
    files = {
        f"{I18N}/widgets/{locale}.ts": _add_key(repo, f"{I18N}/widgets/{locale}.ts", 1 + index % 2)
        for index, locale in enumerate(LOCALES)
    }
    assert _commit(repo, files) == 0


def test_split_dir_non_locale_file_is_not_grouped(repo):
    assert _commit(repo, {f"{I18N}/widgets/index.ts": "export type { W as X } from './en-US'\n"}) == 0


def test_locales_dir_keeps_hunk_alignment(repo, capsys):
    files = {
        f"{I18N}/locales/{locale}.ts": _add_key(repo, f"{I18N}/locales/{locale}.ts", 1 + index % 2)
        for index, locale in enumerate(LOCALES)
    }
    assert _commit(repo, files) == 1
    assert "hunk ranges" in capsys.readouterr().out


def test_every_split_dir_of_the_repository_is_discovered(monkeypatch):
    # Guard rail on the real tree: renaming one of these dirs should update this list.
    monkeypatch.setattr(SYNC, "REPO_ROOT", PROJECT_ROOT)
    dirs = {str(group["dir"]) for group in SYNC._expand_groups(SYNC.GROUPS)}
    for name in SPLIT_DIRS:
        assert f"{I18N}/{name}" in dirs
    assert f"{I18N}/locales" in dirs
    # locales/ is listed explicitly and must not also be expanded without alignment.
    assert sum(1 for group in SYNC._expand_groups(SYNC.GROUPS) if group["dir"] == f"{I18N}/locales") == 1


@pytest.mark.parametrize("directory", [*SPLIT_DIRS, "locales", "some-future-module"])
def test_pr_report_does_not_count_a_language_group(directory):
    counted = [
        path for path in (f"{I18N}/{directory}/{locale}.ts" for locale in LOCALES)
        if REPORT._counts_for_no_split("M", path)
    ]
    assert counted == []


@pytest.mark.parametrize("path", [
    f"{I18N}/authoring/index.ts",
    f"{I18N}/plugin-permissions/shared.ts",
    f"{I18N}/plugin-consent/testing.ts",
    f"{I18N}/index.ts",
    f"{I18N}/power/nested/en-US.ts",
    "frontend/plugin-manager/src/views/en-US.ts",
])
def test_pr_report_still_counts_other_files(path):
    assert REPORT._counts_for_no_split("M", path)

"""Every top-level directory the code keeps under ``app_docs_dir`` must be
either migrated or known to be regenerable; anything else would be left
behind in the old root by a storage-location migration (see #3336).

Discovery walks the AST rather than matching text, so it also sees names
built from constants, ``os.path.join`` calls and variables that hold the
runtime root (``base = cm.app_docs_dir`` ... ``base / "state"``).
"""

import ast
from pathlib import Path

import pytest

from utils.storage_migration import (
    MIGRATED_RUNTIME_ENTRY_NAMES,
    REGENERABLE_RUNTIME_ENTRY_NAMES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_SKIPPED_PARTS = {"tests", ".venv", "node_modules", ".git", ".claude", "dist", "build"}

# Directories that are deliberately neither migrated nor regenerable, with
# the reason. Keep this list short and explained.
DELIBERATELY_UNMIGRATED = {
    # At the anchor root this holds the storage policy and the migration
    # checkpoint itself, so it can never move as a whole; what the code keeps
    # below it under the runtime root is checked one level down instead.
    "state",
}

# Files under state that belong to the anchor root and stay with it, with the
# reason. Only anchor-local state goes here, never user data.
ANCHOR_STATE_FILES = {
    # config_manager.local_state_dir (anchor_root / "state"); the legacy
    # merge reads it from a root that is the anchor there.
    "character_tombstones.json",
}


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: this check reads source only."""
    yield


def _mentions_runtime_root(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == "app_docs_dir":
            return True
        if isinstance(child, ast.Name) and child.id == "app_docs_dir":
            return True
        if isinstance(child, ast.Constant) and child.value == "app_docs_dir":
            return True
    return False


def _is_runtime_root(node: ast.AST, aliases: set[str]) -> bool:
    """Whether ``node`` evaluates to the runtime root itself (not a child or parent)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return False
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return False
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    return _mentions_runtime_root(node)


def _string_value(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _first_segment(value: str) -> str:
    return value.replace("\\", "/").strip("/").split("/")[0]


def _scan_module(tree: ast.Module) -> set[str]:
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    found: set[str] = set()
    scopes = [tree] + [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        aliases = {
            target.id
            for node in ast.walk(scope)
            if isinstance(node, ast.Assign) and _is_runtime_root(node.value, set())
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(scope):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                if _is_runtime_root(node.left, aliases):
                    value = _string_value(node.right, constants)
                    if value:
                        found.add(_first_segment(value))
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name not in {"join", "joinpath"}:
                    continue
                args = list(node.args)
                if isinstance(func, ast.Attribute) and name == "joinpath":
                    args = [func.value, *args]
                for index, arg in enumerate(args[:-1]):
                    if _is_runtime_root(arg, aliases):
                        value = _string_value(args[index + 1], constants)
                        if value:
                            found.add(_first_segment(value))
                        break
    found.discard("")
    return found


def _division_chain(node: ast.AST) -> list[ast.AST]:
    """``a / b / c`` as ``[a, b, c]``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return [*_division_chain(node.left), node.right]
    return [node]


def _scan_state_children(tree: ast.Module) -> set[str]:
    """Directories the code builds as ``<runtime root> / "state" / <name>``."""
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    found: set[str] = set()
    scopes = [tree] + [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        aliases = {
            target.id
            for node in ast.walk(scope)
            if isinstance(node, ast.Assign) and _is_runtime_root(node.value, set())
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(scope):
            chain = _division_chain(node)
            if len(chain) < 3 or not _is_runtime_root(chain[0], aliases):
                continue
            if _string_value(chain[1], constants) != "state":
                continue
            child = _string_value(chain[2], constants)
            if child:
                found.add(_first_segment(child))
    return found


def _state_children_in_source() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in REPO_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        if _SKIPPED_PARTS.intersection(relative.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for name in _scan_state_children(tree):
            found.setdefault(name, []).append(relative.as_posix())
    return found


def _top_level_dirs_in_source() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in REPO_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        if _SKIPPED_PARTS.intersection(relative.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for name in _scan_module(tree):
            found.setdefault(name, []).append(relative.as_posix())
    return found


@pytest.mark.unit
def test_every_runtime_top_level_dir_is_migrated_or_regenerable():
    found = _top_level_dirs_in_source()
    # The scan must see directories built every way the code builds them, or
    # it proves nothing: a literal (config), a module constant through
    # os.path.join (embedding_models), and a variable holding the root (state).
    assert {"config", "pngtuber", "logs", "embedding_models", "state", "theater"} <= set(found)

    known = (
        set(MIGRATED_RUNTIME_ENTRY_NAMES)
        | set(REGENERABLE_RUNTIME_ENTRY_NAMES)
        | DELIBERATELY_UNMIGRATED
    )
    unlisted = {name: sorted(set(files)) for name, files in found.items() if name not in known}

    assert unlisted == {}, (
        "These directories live under app_docs_dir but are neither migrated nor "
        "regenerable. Add each to MIGRATED_RUNTIME_ENTRY_NAMES (user data) or "
        "REGENERABLE_RUNTIME_ENTRY_NAMES (recreated by the app) in "
        "utils/storage/migration.py."
    )


@pytest.mark.unit
def test_migrated_and_regenerable_lists_do_not_overlap():
    assert not set(MIGRATED_RUNTIME_ENTRY_NAMES) & set(REGENERABLE_RUNTIME_ENTRY_NAMES)
    assert not DELIBERATELY_UNMIGRATED & (
        set(MIGRATED_RUNTIME_ENTRY_NAMES) | set(REGENERABLE_RUNTIME_ENTRY_NAMES)
    )


@pytest.mark.unit
def test_every_runtime_dir_under_state_is_migrated():
    """state stays where it is, but data the code keeps below it under the
    runtime root (mini-game scores) must move with a migration."""
    found = _state_children_in_source()
    assert "game_scores" in found, "the scan must see state/game_scores, or it proves nothing"

    migrated_under_state = {
        name.split("/", 1)[1] for name in MIGRATED_RUNTIME_ENTRY_NAMES if name.startswith("state/")
    }
    unlisted = {
        name: sorted(set(files))
        for name, files in found.items()
        if name not in migrated_under_state | ANCHOR_STATE_FILES
    }

    assert unlisted == {}, (
        "These directories live under app_docs_dir/state but are not migrated. "
        "Add each as \"state/<name>\" to MIGRATED_RUNTIME_ENTRY_NAMES in "
        "utils/storage/migration.py."
    )

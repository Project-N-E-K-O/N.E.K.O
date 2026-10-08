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


# Calls that return the same path they are given, in another form.
_SAME_PATH_FUNCTIONS = {
    "Path",
    "PurePath",
    "PosixPath",
    "WindowsPath",
    "PurePosixPath",
    "PureWindowsPath",
    "str",
    "fspath",
    "abspath",
    "normpath",
    "realpath",
    "expanduser",
}
_SAME_PATH_METHODS = {"resolve", "absolute", "expanduser"}
# Given several arguments, these join them like os.path.join.
_PATH_CONSTRUCTORS = {"Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath"}


def _call_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _joined_call_args(node: ast.AST) -> list[ast.AST] | None:
    """``[base, part, ...]`` for a call that joins paths; ``None`` otherwise.

    ``os.path.join(base, ...)``, ``base.joinpath(...)`` and a path
    constructor given more than one argument (``Path(base, "state")``).
    """
    name = _call_name(node)
    if name == "joinpath" and isinstance(node.func, ast.Attribute):
        return [node.func.value, *node.args]
    if name == "join" and node.args:
        return list(node.args)
    if name in _PATH_CONSTRUCTORS and len(node.args) > 1:
        return list(node.args)
    return None


def _unwrap_same_path(node: ast.AST) -> ast.AST | None:
    """The path ``node`` merely wraps (``Path(x)``, ``x.resolve()``); else ``None``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name in _SAME_PATH_FUNCTIONS and len(node.args) == 1:
        return node.args[0]
    if name in _SAME_PATH_METHODS and isinstance(func, ast.Attribute) and not node.args:
        return func.value
    return None


def _is_runtime_root(node: ast.AST, aliases: set[str]) -> bool:
    """Whether ``node`` evaluates to the runtime root itself (not a child or parent)."""
    wrapped = _unwrap_same_path(node)
    if wrapped is not None:
        return _is_runtime_root(wrapped, aliases)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return False
    # A join is a path below the root, like a division: it merely mentions
    # app_docs_dir inside.
    if _joined_call_args(node) is not None:
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
                args = _joined_call_args(node)
                if args is None:
                    continue
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


def _parts_after_runtime_root(node: ast.AST, aliases: set[str]) -> list[ast.AST] | None:
    """The path parts after the runtime root in ``node``; ``None`` if not rooted.

    Divisions, ``os.path.join`` and ``joinpath`` may be mixed and chained
    (``root.joinpath("state").joinpath(name)``); each step adds its parts.
    """
    # Taken apart first: a whole join call merely mentions app_docs_dir
    # somewhere inside, which would otherwise pass for the root itself.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _parts_after_runtime_root(node.left, aliases)
        return None if left is None else [*left, node.right]
    if isinstance(node, ast.Call):
        joined = _joined_call_args(node)
        if joined is not None:
            base = _parts_after_runtime_root(joined[0], aliases)
            return None if base is None else [*base, *joined[1:]]
        wrapped = _unwrap_same_path(node)
        if wrapped is not None:
            return _parts_after_runtime_root(wrapped, aliases)
    if _is_runtime_root(node, aliases):
        return []
    return None


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
            # The parts after the runtime root, however the path is built:
            # root / "state" / name, os.path.join(root, "state", name),
            # root.joinpath("state", name), or "state/name" in one string.
            parts = _parts_after_runtime_root(node, aliases) or []
            segments: list[str] = []
            for part in parts:
                value = _string_value(part, constants)
                if value is None:
                    break
                segments.extend(segment for segment in value.replace("\\", "/").split("/") if segment)
            if len(segments) >= 2 and segments[0] == "state":
                found.add(segments[1])
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


@pytest.mark.unit
def test_state_child_scan_sees_every_way_a_path_is_built():
    lines = [
        "import os",
        "def f(cm):",
        "    base = cm.app_docs_dir",
        "    a = base / 'state' / 'by_division'",
        "    b = os.path.join(base, 'state', 'by_join')",
        "    c = base.joinpath('state', 'by_joinpath')",
        "    d = base / 'state/in_one_string'",
        "    e = base.joinpath('state').joinpath('by_chained_joinpath')",
        "    f = base.joinpath('state') / 'by_mixed'",
        "    g = cm.app_docs_dir.joinpath('state', 'on_the_attribute')",
        "    h = os.path.join(cm.app_docs_dir, 'state', 'joined_on_the_attribute')",
    ]
    tree = ast.parse(chr(10).join(lines))

    assert _scan_state_children(tree) == {
        "by_division",
        "by_join",
        "by_joinpath",
        "in_one_string",
        "by_chained_joinpath",
        "by_mixed",
        "on_the_attribute",
        "joined_on_the_attribute",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "expression",
    [
        "cm.app_docs_dir / 'state' / 'new_child'",
        "cm.app_docs_dir.joinpath('state', 'new_child')",
        "os.path.join(cm.app_docs_dir, 'state', 'new_child')",
        "cm.app_docs_dir.joinpath('state').joinpath('new_child')",
        "Path(cm.app_docs_dir / 'state') / 'new_child'",
        "Path(os.path.join(cm.app_docs_dir, 'state')) / 'new_child'",
        "Path(cm.app_docs_dir) / 'state' / 'new_child'",
        "Path(cm.app_docs_dir).resolve() / 'state' / 'new_child'",
        "(cm.app_docs_dir / 'state').resolve() / 'new_child'",
        "Path(cm.app_docs_dir / 'state').resolve() / 'new_child'",
        "Path(cm.app_docs_dir, 'state', 'new_child')",
        "Path(cm.app_docs_dir, 'state') / 'new_child'",
        "PurePosixPath(os.fspath(cm.app_docs_dir), 'state') / 'new_child'",
    ],
)
def test_a_path_joined_straight_from_app_docs_dir_is_scanned_below_the_root(expression):
    """No alias in between: the join itself must not pass for the root, or the
    child is lost here and turns up as a top-level directory instead."""
    tree = ast.parse(chr(10).join(["import os", "def f(cm):", f"    target = {expression}"]))

    assert _scan_state_children(tree) == {"new_child"}
    assert _scan_module(tree) == {"state"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "expression",
    [
        # Same-named calls on something that is not the runtime root.
        "other.resolve() / 'state' / 'new_child'",
        "Path(other, 'state', 'new_child')",
        "str(other) + 'state'",
    ],
)
def test_a_wrapped_path_that_is_not_the_runtime_root_is_not_scanned(expression):
    tree = ast.parse(chr(10).join(["import os", "def f(cm, other):", f"    target = {expression}"]))

    assert _scan_state_children(tree) == set()
    assert _scan_module(tree) == set()

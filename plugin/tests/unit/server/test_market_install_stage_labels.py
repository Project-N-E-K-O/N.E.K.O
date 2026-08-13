"""Keep the Market install stage labels in step with the stages the bridge sets.

#2714 renamed the upgrade write stage (stop_old/backup_old/install/restart ->
replace) without touching the locale tables, so every upgrade rendered a bare
``replace`` in all eight languages: MarketPanel's label helper falls back to the
raw stage id when the key is missing. These tests read both sides from source so
the next rename fails here instead of on a user's screen.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_BRIDGE = _REPO_ROOT / "plugin" / "server" / "routes" / "market_bridge.py"
_LOCALE_DIR = _REPO_ROOT / "frontend" / "plugin-manager" / "src" / "i18n" / "locales"

# Stages the frontend sets on its own, with no backend producer:
#   pending -> beginInstallTaskTracking, failed -> markInstallTaskFailed
# (both in frontend/plugin-manager/src/components/plugin/MarketPanel.vue)
_FRONTEND_ONLY_STAGES = frozenset({"pending", "failed"})


def _string_constants(node: ast.AST) -> set[str]:
    """Literals the expression can evaluate to, not every literal it mentions.

    ``task["stage"] = task.get("stage") or "failed"`` must yield ``failed``
    alone — descending into the call would also pick up its ``"stage"`` argument.
    """

    found: set[str] = set()
    pending: list[ast.AST] = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, ast.Constant):
            if isinstance(current.value, str):
                found.add(current.value)
            continue
        if isinstance(current, ast.Call):
            continue
        pending.extend(ast.iter_child_nodes(current))
    return found


def _backend_stages() -> set[str]:
    """Every stage value market_bridge can publish on a task."""

    tree = ast.parse(_BRIDGE.read_text(encoding="utf-8"))
    stages: set[str] = set()

    for node in ast.walk(tree):
        # _set_task_stage(..., stage="download", ...)
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "_set_task_stage":
                for keyword in node.keywords:
                    if keyword.arg == "stage":
                        stages |= _string_constants(keyword.value)

        # task["stage"] = "completed"  /  task.get("stage") or "failed"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "stage"
                ):
                    stages |= _string_constants(node.value)

    return {stage for stage in stages if stage}


def _locale_stage_keys(locale_path: Path) -> set[str]:
    text = locale_path.read_text(encoding="utf-8")
    block = re.search(r"installStage:\s*\{(?P<body>.*?)\}", text, re.S)
    assert block is not None, f"{locale_path.name} has no installStage block"
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", block.group("body"), re.M))


def _locale_files() -> list[Path]:
    files = sorted(_LOCALE_DIR.glob("*.ts"))
    assert files, f"no locale files under {_LOCALE_DIR}"
    return files


def test_backend_stage_extraction_finds_the_known_stages() -> None:
    """Guard the parser itself: if it silently matched nothing the other tests pass vacuously."""

    stages = _backend_stages()
    assert {"download", "verify", "install", "replace", "rollback", "completed"} <= stages


@pytest.mark.parametrize("locale_path", _locale_files(), ids=lambda p: p.stem)
def test_every_backend_stage_has_a_label(locale_path: Path) -> None:
    """A stage with no key renders as its raw id (MarketPanel's installTaskStageLabel fallback)."""

    missing = _backend_stages() - _locale_stage_keys(locale_path)
    assert not missing, (
        f"{locale_path.name} has no installStage label for {sorted(missing)}; "
        "the install dialog and the resume bar would show the raw stage id"
    )


@pytest.mark.parametrize("locale_path", _locale_files(), ids=lambda p: p.stem)
def test_no_stage_label_outlives_its_producer(locale_path: Path) -> None:
    """Labels nobody can ever set are dead weight that hides the next rename."""

    orphans = _locale_stage_keys(locale_path) - _backend_stages() - _FRONTEND_ONLY_STAGES
    assert not orphans, (
        f"{locale_path.name} keeps installStage labels {sorted(orphans)} that no producer sets"
    )


def test_all_locales_agree_on_the_stage_key_set() -> None:
    """One language drifting is how a stage ends up untranslated for a subset of users."""

    per_locale = {path.stem: _locale_stage_keys(path) for path in _locale_files()}
    reference_name, reference = next(iter(sorted(per_locale.items())))
    mismatched = {
        name: sorted(keys ^ reference)
        for name, keys in per_locale.items()
        if keys != reference
    }
    assert not mismatched, f"installStage keys differ from {reference_name}: {mismatched}"

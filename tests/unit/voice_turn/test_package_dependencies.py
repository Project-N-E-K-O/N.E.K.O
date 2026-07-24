"""Package dependency contracts for the VoiceTurn layer."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VOICE_TURN_ROOT = REPO_ROOT / "main_logic" / "voice_turn"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    module_parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    package_parts = module_parts[:-1]
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            keep = len(package_parts) - (node.level - 1)
            assert keep >= 0, f"relative import escapes repository package: {path}"
            base_parts = package_parts[:keep]
            if node.module:
                base_parts.extend(node.module.split("."))
            base = ".".join(base_parts)
        else:
            base = node.module or ""
        if base:
            imported.add(base)
            imported.update(
                f"{base}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return imported


def test_voice_turn_does_not_import_asr_client() -> None:
    paths = sorted(VOICE_TURN_ROOT.rglob("*.py"))
    assert paths, "VoiceTurn dependency scan matched no Python files"

    forbidden = "main_logic.asr_client"
    for path in paths:
        imports = _imports(path)
        assert not {
            name
            for name in imports
            if name == forbidden or name.startswith(f"{forbidden}.")
        }, str(path.relative_to(REPO_ROOT))

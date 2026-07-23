"""Architecture contracts for the provider-neutral voice identity foundation."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VOICE_IDENTITY_ROOT = REPO_ROOT / "main_logic" / "voice_identity"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_voice_identity_has_no_core_provider_or_transport_dependencies() -> None:
    forbidden = (
        "main_logic.core",
        "main_logic.asr_client",
        "main_logic.transport",
        "main_logic.providers",
        "main_logic.provider",
    )

    for path in VOICE_IDENTITY_ROOT.glob("*.py"):
        imports = _imports(path)
        assert not {
            name
            for name in imports
            if any(name.startswith(prefix) for prefix in forbidden)
        }, path.name


def test_core_uses_public_voice_identity_composition_only() -> None:
    core_path = REPO_ROOT / "main_logic" / "core" / "asr_runtime.py"
    imports = _imports(core_path)

    assert "main_logic.voice_identity.campplus" not in imports
    assert "main_logic.voice_identity.runtime" not in imports
    assert "main_logic.voice_identity.profile" not in imports


def test_asr_adapter_imports_contracts_but_not_model_or_profile() -> None:
    for filename in ("runtime.py", "detector_runtime.py"):
        path = REPO_ROOT / "main_logic" / "asr_client" / filename
        imports = _imports(path)
        assert "main_logic.voice_identity.contracts" in imports
        assert "main_logic.voice_identity.campplus" not in imports
        assert "main_logic.voice_identity.profile" not in imports
        assert "main_logic.voice_identity.runtime" not in imports


def test_provider_workers_do_not_import_voice_identity() -> None:
    provider_root = REPO_ROOT / "main_logic" / "asr_client"
    for path in provider_root.rglob("*.py"):
        if "provider" not in path.parts and "providers" not in path.parts:
            continue
        assert not {
            name
            for name in _imports(path)
            if name.startswith("main_logic.voice_identity")
        }, str(path.relative_to(REPO_ROOT))


def test_campplus_backend_has_one_owner() -> None:
    definitions: list[Path] = []
    for path in (REPO_ROOT / "main_logic").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ClassDef)
            and node.name == "CampPlusEmbeddingModel"
            for node in ast.walk(tree)
        ):
            definitions.append(path)

    assert definitions == [VOICE_IDENTITY_ROOT / "campplus.py"]

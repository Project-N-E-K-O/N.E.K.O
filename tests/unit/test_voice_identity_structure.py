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


def test_voice_identity_dependency_graph_is_acyclic() -> None:
    modules = {path.stem: path for path in VOICE_IDENTITY_ROOT.glob("*.py")}
    dependencies: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module in modules
            ):
                dependencies[name].add(node.module)

    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        assert name not in active, f"voice_identity import cycle at {name}"
        if name in visited:
            return
        active.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        active.remove(name)
        visited.add(name)

    for module_name in dependencies:
        visit(module_name)


def test_campplus_has_one_model_specific_onnx_session_creation() -> None:
    owners = []
    for path in VOICE_IDENTITY_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        owners.extend([path] * source.count("InferenceSession("))

    assert owners == [VOICE_IDENTITY_ROOT / "campplus.py"]


def test_legacy_asr_campplus_module_is_only_a_compatibility_facade() -> None:
    facade = REPO_ROOT / "main_logic" / "asr_client" / "campplus.py"
    assert _imports(facade) == {"main_logic.voice_identity.campplus"}


def test_core_tests_use_formal_observer_port() -> None:
    source = (
        REPO_ROOT / "tests" / "unit" / "test_core_independent_asr.py"
    ).read_text(encoding="utf-8")

    assert "runtime._speaker_shadow_observation_callback =" not in source

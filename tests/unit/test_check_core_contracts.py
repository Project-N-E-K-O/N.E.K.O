from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_core_contracts.py"


@pytest.fixture(scope="module")
def contract_checker():
    spec = importlib.util.spec_from_file_location(
        "check_core_contracts_test",
        SCRIPT_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import main_logic as ml\nvalue = ml.core.manager", "main_logic.core"),
        ("import main_logic.core as core\nvalue = core.manager", "main_logic.core.manager"),
        ("from main_logic import core as facade\nvalue = facade.manager", "main_logic.core.manager"),
    ],
)
def test_imported_paths_resolves_package_alias_attribute_chains(
    contract_checker,
    source: str,
    expected: str,
) -> None:
    tree = ast.parse(source)
    aliases = contract_checker.module_alias_paths(tree, "main_logic.asr_client")
    referenced = {
        path
        for node in ast.walk(tree)
        for path in contract_checker._imported_paths(
            node,
            "main_logic.asr_client",
            aliases,
        )
    }

    assert expected in referenced


def _dynamic_import_results(contract_checker, source: str) -> list[tuple[str | None, bool]]:
    tree = ast.parse(source)
    aliases = contract_checker.module_alias_paths(tree, "main_logic.asr_client")
    return [
        (target, dynamic)
        for node in ast.walk(tree)
        for target, dynamic in [contract_checker._dynamic_import_target(node, aliases)]
        if dynamic
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nmod = importlib.import_module('main_logic.core')",
        "import importlib as il\nmod = il.import_module('main_logic.core')",
        "from importlib import import_module\nmod = import_module('main_logic.core')",
        "from importlib import import_module as im\nmod = im('main_logic.core')",
        "mod = __import__('main_logic.core')",
        "import importlib\nmod = importlib.import_module(name='main_logic.core')",
    ],
)
def test_dynamic_import_target_resolves_string_literal_forms(
    contract_checker,
    source: str,
) -> None:
    assert _dynamic_import_results(contract_checker, source) == [
        ("main_logic.core", True)
    ]


@pytest.mark.unit
def test_dynamic_import_target_reports_non_literal_argument(contract_checker) -> None:
    source = "import importlib\ndef load(name):\n    return importlib.import_module(name)"

    assert _dynamic_import_results(contract_checker, source) == [(None, True)]


@pytest.mark.unit
def test_dynamic_import_target_ignores_unrelated_calls(contract_checker) -> None:
    source = "def import_module(name):\n    return name\nmod = import_module('main_logic.core')"

    assert _dynamic_import_results(contract_checker, source) == []


@pytest.mark.unit
def test_asr_runtime_alias_reads_flags_single_assignment_alias(contract_checker) -> None:
    source = (
        "class Bridge:\n"
        "    def peek(self):\n"
        "        rt = self._asr_runtime\n"
        "        direct = self._asr_runtime.display_name\n"
        "        return rt.lifecycle, rt.route_mode, direct\n"
    )
    fn = ast.parse(source).body[0].body[0]

    sites = contract_checker._asr_runtime_alias_reads(
        fn, {"lifecycle", "route_mode", "required"}
    )

    assert sorted(attr for _line, _col, attr in sites) == ["lifecycle", "route_mode"]


@pytest.mark.unit
def test_registry_provider_keys_extracts_dict_literal(contract_checker, tmp_path) -> None:
    registry = tmp_path / "_registry_meta.py"
    registry.write_text(
        "ASR_PROVIDER_REGISTRY: dict[str, object] = {\n"
        '    "provider_a": object,\n'
        '    "provider_b": object,\n'
        "}\n",
        encoding="utf-8",
    )

    assert contract_checker._registry_provider_keys(registry) == frozenset(
        {"provider_a", "provider_b"}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "ASR_PROVIDER_REGISTRY = dict(provider_a=object)",
        "OTHER_NAME = {'provider_a': object}",
    ],
)
def test_registry_provider_keys_hard_fails_on_unrecognized_shape(
    contract_checker,
    tmp_path,
    capsys,
    source: str,
) -> None:
    registry = tmp_path / "_registry_meta.py"
    registry.write_text(source, encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        contract_checker._registry_provider_keys(registry)

    assert excinfo.value.code == 2
    assert "ASR_PROVIDER_REGISTRY" in capsys.readouterr().err


def _write_minimal_core_layout(root: Path) -> None:
    core = root / "main_logic" / "core"
    core.mkdir(parents=True)
    (root / "tests").mkdir()
    (core / "__init__.py").write_text(
        '"""facade."""\nfrom .manager import LLMSessionManager\n',
        encoding="utf-8",
    )
    (core / "manager.py").write_text(
        '"""manager."""\n\n\nclass LLMSessionManager:\n'
        "    def __init__(self):\n        pass\n",
        encoding="utf-8",
    )


@pytest.mark.unit
def test_run_flags_dynamic_imports_of_core_inside_asr_client(
    contract_checker,
    tmp_path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    asr_client = tmp_path / "main_logic" / "asr_client"
    asr_client.mkdir()
    loader = asr_client / "loader.py"
    loader.write_text(
        "import importlib\n\n\n"
        "def load_core():\n"
        '    return importlib.import_module("main_logic.core")\n\n\n'
        "def load_any(name):\n"
        "    return importlib.import_module(name)\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == loader and violation.code == "ASR_LAYERING"
    ]

    assert "asr_client must not import main_logic.core (dynamic import)" in messages
    assert any("non-literal module name" in message for message in messages)


@pytest.mark.unit
def test_run_flags_forbidden_runtime_reads_through_local_alias(
    contract_checker,
    tmp_path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    bridge = tmp_path / "main_logic" / "core" / "asr_runtime.py"
    bridge.write_text(
        '"""bridge."""\n\n\n'
        "class AsrRuntimeMixin:\n"
        '    """m."""\n\n'
        "    def _set_microphone_route(self):\n"
        "        return None\n\n"
        "    def _peek(self):\n"
        "        rt = self._asr_runtime\n"
        "        return rt.lifecycle\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == bridge and violation.code == "ASR_LAYERING"
    ]

    assert (
        "Core must not read IndependentAsrRuntime.lifecycle "
        "(via a local alias of self._asr_runtime)"
    ) in messages

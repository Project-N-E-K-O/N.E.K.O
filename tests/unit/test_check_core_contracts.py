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

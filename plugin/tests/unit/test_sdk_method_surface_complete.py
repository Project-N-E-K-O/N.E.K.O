from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


SDK_ROOT = Path(__file__).resolve().parents[2] / "sdk"


def _module_name_from_path(path: Path) -> str:
    rel = path.relative_to(Path(__file__).resolve().parents[2])
    return "plugin." + ".".join(rel.with_suffix("").parts)


def _iter_public_api_from_ast(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs: list[str] = []
    classes: dict[str, list[str]] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            funcs.append(node.name)
            continue

        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods: list[str] = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                    methods.append(child.name)
            if methods:
                # de-dup while preserving order
                deduped: list[str] = []
                for name in methods:
                    if name not in deduped:
                        deduped.append(name)
                classes[node.name] = deduped

    return funcs, classes


@pytest.mark.plugin_unit
def test_sdk_public_method_surface_exists_runtime() -> None:
    py_files = sorted(p for p in SDK_ROOT.rglob("*.py") if p.name != "__init__.py")

    missing: list[str] = []
    checked = 0

    for path in py_files:
        module_name = _module_name_from_path(path)
        funcs, classes = _iter_public_api_from_ast(path)
        if not funcs and not classes:
            continue

        module = importlib.import_module(module_name)

        for fn in funcs:
            checked += 1
            if not hasattr(module, fn):
                missing.append(f"{module_name}.{fn}")

        for cls_name, methods in classes.items():
            checked += 1
            cls = getattr(module, cls_name, None)
            if cls is None:
                missing.append(f"{module_name}.{cls_name}")
                continue
            for method in methods:
                checked += 1
                if not hasattr(cls, method):
                    missing.append(f"{module_name}.{cls_name}.{method}")

    assert checked > 0
    assert missing == []

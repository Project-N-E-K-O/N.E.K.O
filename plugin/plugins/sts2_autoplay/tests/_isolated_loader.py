from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any


STS2_PACKAGE_DIR = Path(__file__).resolve().parents[1]


def load_isolated_sts2_module(package_name: str, module_name: str) -> Any:
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(STS2_PACKAGE_DIR)]
        sys.modules[package_name] = package

    qualified_name = f"{package_name}.{module_name}"
    spec = importlib.util.spec_from_file_location(qualified_name, STS2_PACKAGE_DIR / f"{module_name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module

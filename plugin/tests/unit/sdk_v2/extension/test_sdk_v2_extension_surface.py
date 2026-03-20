from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

import pytest

import plugin.sdk_v2.extension as extension
from plugin.sdk_v2.extension import decorators as dec

PACKAGE = "plugin.sdk_v2.extension"
ROOT = Path(__file__).resolve().parents[4] / "sdk_v2" / "extension"
FORBIDDEN_PREFIXES = (
    "plugin.sdk_v2.plugin",
    "plugin.sdk_v2.adapter",
    "plugin.sdk_v2.public",
)


def _import_targets(tree: ast.AST, path: Path) -> list[str]:
    targets: list[str] = []
    relative_parts = path.relative_to(ROOT).with_suffix("").parts[:-1]
    current_package = [*PACKAGE.split("."), *relative_parts]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = current_package[: len(current_package) - (node.level - 1)]
                if node.module:
                    base = [*base, *node.module.split(".")]
            else:
                base = node.module.split(".") if node.module else []

            if not base:
                targets.extend(alias.name for alias in node.names)
                continue

            for alias in node.names:
                if alias.name == "*":
                    targets.append(".".join(base))
                else:
                    targets.append(".".join([*base, *alias.name.split(".")]))
    return targets


def test_extension_exports_exist() -> None:
    assert extension.__all__
    for name in extension.__all__:
        assert hasattr(extension, name)


def test_extension_meta_construct() -> None:
    meta = extension.ExtensionMeta(id="ext", name="Extension")
    assert meta.version == "0.0.0"
    assert meta.capabilities == []


def test_extension_decorators_construct() -> None:
    def fn() -> str:
        return "ready"
    assert dec.extension_entry()(fn) is fn
    assert getattr(fn, dec.EXTENSION_ENTRY_META).id is None
    assert dec.extension_hook()(fn) is fn
    assert getattr(fn, dec.EXTENSION_HOOK_META).target == "*"


@pytest.mark.asyncio
async def test_extension_runtime_health() -> None:
    router = Mock(name="router")
    router.name.return_value = "router"
    rt = extension.ExtensionRuntime(config=Mock(name="config"), router=router, transport=Mock(name="transport"))
    health = await rt.health()
    assert health.is_ok()
    assert health.unwrap()["status"] == "healthy"


def test_extension_runtime_common_exports() -> None:
    assert extension.SDK_VERSION == "0.1.0"
    assert extension.Result is not None
    assert extension.ErrorCode is not None


def test_extension_decorator_metadata_exports() -> None:
    assert dec.EXTENSION_ENTRY_META == "__extension_entry_meta__"
    assert dec.EXTENSION_HOOK_META == "__extension_hook_meta__"
    assert dec.ExtensionEntryMeta(id="x", name=None, description="", timeout=None).id == "x"
    assert dec.ExtensionHookMeta(target="*", timing="before", priority=0).timing == "before"
    assert dec._not_impl() is None


def test_extension_hook_validates_timing() -> None:
    def fn() -> str:
        return "ready"

    assert dec.extension_hook(timing="after")(fn) is fn
    with pytest.raises(ValueError, match="timing must be one of"):
        dec.extension_hook(timing="befroe")


def test_extension_proxy_object_forwards() -> None:
    sentinel = object()

    def fake_entry(**kwargs: object):
        assert kwargs["id"] == "x"
        return sentinel

    def fake_hook(**kwargs: object):
        assert kwargs["target"] == "a"
        return sentinel

    original_entry = dec.extension_entry
    original_hook = dec.extension_hook
    dec.extension_entry = fake_entry  # type: ignore[assignment]
    dec.extension_hook = fake_hook  # type: ignore[assignment]
    try:
        assert dec.extension.entry(id="x") is sentinel
        assert dec.extension.hook(target="a") is sentinel
    finally:
        dec.extension_entry = original_entry  # type: ignore[assignment]
        dec.extension_hook = original_hook  # type: ignore[assignment]


def test_extension_runtime_surface_is_more_aligned() -> None:
    assert extension.PluginConfigError is not None
    assert extension.ConfigPathError is not None
    assert extension.ConfigValidationError is not None
    assert extension.PluginRouterError is not None
    assert extension.EntryConflictError is not None
    assert extension.RouteHandler is not None
    assert extension.CallChain.__name__ == "CallChain"
    assert extension.AsyncCallChain.__name__ == "AsyncCallChain"
    assert isinstance(extension.CircularCallError("e"), RuntimeError)
    assert isinstance(extension.CallChainTooDeepError("e"), RuntimeError)


def test_import_targets_resolve_absolute_and_relative_importfrom() -> None:
    source = """
from plugin.sdk_v2 import adapter
from . import decorators
from ..shared import runtime
"""
    path = ROOT / "sample.py"
    targets = _import_targets(ast.parse(source), path)
    assert "plugin.sdk_v2.adapter" in targets
    assert "plugin.sdk_v2.extension.decorators" in targets
    assert "plugin.sdk_v2.shared.runtime" in targets


def test_extension_surface_depends_only_on_lower_layers() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for target in _import_targets(tree, path):
            if target.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path} imports forbidden surface {target}")
    assert violations == []

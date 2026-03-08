from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path('plugin/sdk_v2/extension')
FORBIDDEN_PREFIXES = (
    'plugin.sdk_v2.plugin',
    'plugin.sdk_v2.adapter',
    'plugin.sdk_v2.public',
)


def _import_targets(tree: ast.AST) -> list[str]:
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
    return targets


def test_extension_surface_depends_only_on_lower_layers() -> None:
    for path in ROOT.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(path))
        for target in _import_targets(tree):
            assert not target.startswith(FORBIDDEN_PREFIXES), f'{path} imports forbidden surface {target}'

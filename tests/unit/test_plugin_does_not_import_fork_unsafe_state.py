"""Plugin code must not import recent_file or chat_sync (premise of their fork-safety handling).

On Linux the plugin host forks plugin processes from a multithreaded parent
(the main server, or the merged server). The after-fork hooks in
``utils/recent_file.py`` and ``services/chat_sync/writer.py`` rebuild locks, but
the modules' in-process state (redirects, deletions, generations, pending
batches, writer registry) can be caught half-updated by another parent thread.
That is harmless only because plugin processes never read it. This test keeps
that premise checked; it scans all of ``plugin/`` except its tests, built-in
plugins included.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / 'plugin'
FORBIDDEN = ('utils.recent_file', 'services.chat_sync')
_IMPORTLIB_CALLS = {'import_module', '__import__'}


def _forbidden(module: str) -> bool:
    return any(module == name or module.startswith(name + '.') for name in FORBIDDEN)


def _import_function_names(tree: ast.AST) -> set[str]:
    """Names that call importlib.import_module here, aliases included (`import_module as load`)."""
    names = {'__import__'}  # a builtin; import_module counts only when bound from importlib
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == 'importlib':
            for alias in node.names:
                if alias.name == '*':  # importlib.__all__ exports import_module
                    names.add('import_module')
                elif alias.name == 'import_module':
                    names.add(alias.asname or alias.name)
    return names


def _imported_modules(tree: ast.AST):
    """Yield (line, module) for absolute imports and importlib string literals."""
    import_functions = _import_function_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            # `from utils import recent_file` / `from services import chat_sync`
            for alias in node.names:
                yield node.lineno, f'{node.module}.{alias.name}'
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            if isinstance(func, ast.Attribute):  # importlib.import_module / il.import_module
                matches = func.attr in _IMPORTLIB_CALLS
            else:
                matches = getattr(func, 'id', None) in import_functions
            first = node.args[0]
            if matches and isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield node.lineno, first.value


def _violations(directory: Path) -> list[str]:
    found = []
    for path in sorted(directory.rglob('*.py')):
        relative = path.relative_to(directory)
        if 'tests' in relative.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        seen = set()
        for line, module in _imported_modules(tree):
            if _forbidden(module) and line not in seen:
                seen.add(line)
                found.append(f'{path.relative_to(ROOT).as_posix()}:{line} imports {module}')
    return found


def test_plugin_code_does_not_import_recent_file_or_chat_sync():
    violations = _violations(PLUGIN_DIR)
    assert not violations, (
        'plugin/ must not import utils.recent_file or services.chat_sync. The plugin host '
        'forks plugin processes from a multithreaded parent, so these modules\' in-process '
        'state (redirects, deletions, generations, pending batches, writer registry) can be '
        'inherited half-updated. Make that state fork-safe before using it from plugins.\n'
        + '\n'.join(violations)
    )


def test_scanner_catches_import_module_from_a_star_import():
    tree = ast.parse('from importlib import *\nimport_module("utils.recent_file")\n')
    assert [module for _, module in _imported_modules(tree) if _forbidden(module)] == ['utils.recent_file']


def test_scanner_ignores_an_unbound_import_module_name():
    tree = ast.parse(
        'def import_module(name):\n'
        '    return name\n'
        'import_module("utils.recent_file")\n'
    )
    assert [module for _, module in _imported_modules(tree) if _forbidden(module)] == []


def test_scanner_catches_each_import_form(tmp_path):
    sample = tmp_path / 'plugin_sample' / 'mod.py'
    sample.parent.mkdir()
    sample.write_text(
        'import utils.recent_file\n'
        'from utils.recent_file import recent_file_lock\n'
        'from utils import recent_file\n'
        'from services.chat_sync.writer import ChatSyncWriter\n'
        'from services import chat_sync\n'
        'import importlib\n'
        'importlib.import_module("services.chat_sync.runtime")\n'
        '__import__("utils.recent_file")\n'
        'from importlib import import_module as load\n'
        'load("utils.recent_file")\n'
        'from importlib import import_module\n'
        'import_module("services.chat_sync")\n'
        'unrelated_loader("utils.recent_file")\n'  # not bound to importlib.import_module
        'from .utils import recent_file as local_helper\n'  # plugin's own relative module
        'import utils.recent_file_helpers\n',  # a different module that shares the prefix
        encoding='utf-8',
    )
    tree = ast.parse(sample.read_text(encoding='utf-8'))
    lines = sorted({line for line, module in _imported_modules(tree) if _forbidden(module)})
    assert lines == [1, 2, 3, 4, 5, 7, 8, 10, 12]

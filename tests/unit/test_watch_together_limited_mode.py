import ast
from pathlib import Path


def test_scene_and_media_apis_are_both_gated_during_storage_initialization():
    tree = ast.parse((Path(__file__).resolve().parents[2] / 'app/main_server/__init__.py').read_text(encoding='utf-8'))
    names = {'_MAIN_LIMITED_MODE_ALLOWED_EXACT_PATHS', '_MAIN_LIMITED_MODE_ALLOWED_PAGE_PATHS',
             '_MAIN_LIMITED_MODE_ALLOWED_PREFIXES'}
    nodes = [node for node in tree.body if
             (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets))
             or (isinstance(node, ast.FunctionDef) and node.name == '_is_main_limited_mode_allowed_path')]
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<limited-mode-policy>', 'exec'), namespace)
    allowed = namespace['_is_main_limited_mode_allowed_path']
    for method in ('GET', 'HEAD'):
        assert not allowed('/watch_together', method)
        assert not allowed('/api/watch-together/history', method)
    assert allowed('/api_key', 'GET')

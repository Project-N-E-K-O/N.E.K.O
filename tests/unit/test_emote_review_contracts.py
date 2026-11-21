"""Storage initialization policy and final image-reference bounds."""
import ast
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from main_logic.emote_wardrobe import prepare_spritesheet_crops


def test_packaged_zip_worker_dependencies_are_all_required():
    from scripts.check_nuitka_dist import _REQUIRED_ASSETS

    root = Path(__file__).resolve().parents[2]
    worker = (root / 'static/js/emote_template_zip_worker.js').read_text(encoding='utf-8')
    imports = re.findall(r"['\"]([^'\"]+\.js)['\"]", worker.split('importScripts(', 1)[1].split(');', 1)[0])
    assert imports
    required = {Path(folder, name).as_posix() for folder, name in _REQUIRED_ASSETS if name is not None}
    for dependency in imports:
        path = (root / 'static/js' / dependency).resolve()
        assert path.is_file()
        assert path.relative_to(root).as_posix() in required


def test_editor_and_write_apis_are_gated_together_until_storage_is_ready():
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
        assert not allowed('/emote_maker', method)
        assert not allowed('/api/emote-maker/library', method)
    assert not allowed('/api/emote-maker/import', 'POST')
    assert allowed('/api_key', 'GET')


@pytest.mark.parametrize('aspect_limit', [8, 10])
@pytest.mark.parametrize('size', [(1279, 129), (129, 1279), (1280, 161), (161, 1280)])
def test_final_low_clarity_reference_respects_model_aspect_limit(aspect_limit, size, monkeypatch):
    monkeypatch.setattr('main_logic.emote_wardrobe._edge_sharpness', lambda image: .1)
    stream = io.BytesIO()
    Image.new('RGBA', size, 'gray').save(stream, 'PNG')
    crops, _, refs = prepare_spritesheet_crops(stream.getvalue(), ((0, 0, 1, 1),), aspect_limit)
    assert refs.keys() == {0}
    for raw in (crops[0], refs[0]):
        with Image.open(io.BytesIO(raw)) as image:
            assert max(image.size) <= aspect_limit * min(image.size)

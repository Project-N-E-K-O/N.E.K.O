# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Selection rules of scripts/ci/select_related_tests.py and the workflow wiring around it.

The PR gate runs only what the selector picks, so a rule that silently stops
selecting is a hole in CI that stays green. Every rule is pinned against a
small synthetic repository laid out like this one: application packages with
a facade ``__init__``, test helpers between tests and application code, a
sub-directory conftest, shard-marked files and tests that read non-Python
files by name.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "select_related_tests.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unit-tests.yml"


def _load_selector():
    spec = importlib.util.spec_from_file_location("select_related_tests_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sel = _load_selector()

FILES = {
    "pyproject.toml": "[project]\nname = 'fake'\n",
    "pytest.ini": "[pytest]\n",
    "docs/guide.md": "# guide\n",
    "README.md": "# readme\n",
    "static/widget-panel.js": "export const x = 1;\n",
    "templates/index.html": "<html></html>\n",
    "static/index.html": "<html></html>\n",
    "app/__init__.py": "from .core import Engine\nfrom . import heavy\n\nLOCAL = 1\n",
    "app/core.py": "from app.util import helper\n\nclass Engine:\n    pass\n",
    "app/util.py": "def helper():\n    return 1\n",
    "app/heavy.py": "VALUE = 2\n",
    "app/other.py": "VALUE = 3\n",
    "app/patched.py": "VALUE = 4\n",
    "app/dynamic.py": "VALUE = 5\n",
    "app/server_mod.py": "VALUE = 6\n",
    "app/state_reset.py": "from app.reset_impl import do_reset\n\ndef reset():\n    do_reset()\n",
    "app/reset_impl.py": "from app.pkgy.thing import THING\n\ndef do_reset():\n    return THING\n",
    "app/pkgy/__init__.py": "",
    "app/pkgy/thing.py": "THING = 1\n",
    "app/pkgx/core.py": "VALUE = 13\n",
    "tests/fixtures/policy.json": "{}\n",
    "tests/unit/fixtures/wire_samples/case_one.json": "{}\n",
    "tests/unit/sub/fixtures/sub_samples/one.json": "{}\n",
    "tests/unit/test_wire_samples.py": (
        "from pathlib import Path\n\n"
        "def test_samples():\n    assert list((Path(__file__).parent / 'fixtures' / 'wire_samples').glob('*.json'))\n"
    ),
    "layouts/index.html": "<html></html>\n",
    "tests/frontend/panel_flow.test.cjs": "test('x', () => {});\n",
    "tests/root_level_check.mjs": "export {};\n",
    "static/widget-board.mjs": "export const board = 1;\n",
    "static/widget-core.mjs": "export const core = 1;\n",
    "static/sub-widget.mjs": "export const sub = 1;\n",
    "tests/frontend/sub_setup.mjs": "import {sub} from '../../static/sub-widget.mjs';\n",
    "tests/frontend/test_widget_flow_board.mjs": "import {board} from '../../static/widget-board.mjs';\n",
    "tests/frontend/test_widget_flow_core.mjs": "import './widget_harness.mjs';\n",
    "tests/frontend/widget_harness.mjs": "import {core} from '../../static/widget-core.mjs';\n",
    "tests/unit/test_widget_frontend.py": (
        "from pathlib import Path\n\n"
        "SCRIPTS = sorted((Path('.') / 'tests/frontend').glob('test_widget_flow*.mjs'))\n\n"
        "def test_scripts():\n    assert SCRIPTS is not None\n"
    ),
    "tests/unit/test_broad_glob.py": (
        "from pathlib import Path\n\n"
        "def test_all():\n    assert list(Path('tests/frontend').glob('*.mjs')) is not None\n"
    ),
    "tests/unit/test_mentions_frontend.py": "def test_frontend():\n    assert 'tests/frontend'\n",
    "tests/unit/test_template_param.py": (
        "import pytest\nfrom pathlib import Path\n\n"
        "@pytest.mark.parametrize('name', ['index.html'])\n"
        "def test_template(name):\n    assert (Path('layouts') / name).name\n"
    ),
    "plugin/plugins/kitx/pyproject.toml": "[project]\nname = 'kitx'\n",
    "plugin/plugins/kitx/__init__.py": "",
    "plugin/plugins/kitx/routes_table.py": "VALUE = 15\n",
    "tests/unit/test_mentions_init.py": "def test_init():\n    assert 'routes_table.main'\n",
    "tests/fixtures/test_payload.py": "PAYLOAD = 1\n",
    "pages/index.html": "<html></html>\n",
    "deps/libx/pyproject.toml": "[project]\nname = 'libx'\n",
    "deps/libx/libx_pkg/__init__.py": "from libx_pkg.geometry_ops import VALUE\n",
    "tests/unit/test_vendored_facade.py": (
        "import sys\n\ndef test_facade():\n    sys.path.insert(0, 'deps/libx')\n"
        "    from libx_pkg import VALUE\n    assert VALUE\n"
    ),
    "deps/libx/libx_pkg/geometry_ops.py": "VALUE = 14\n",
    "tests/unit/test_vendored.py": (
        "import sys\n\ndef test_vendored():\n    sys.path.insert(0, 'deps/libx')\n"
        "    from libx_pkg.geometry_ops import VALUE\n    assert VALUE\n"
    ),
    "app/base_service.py": "VALUE = 7\n",
    "app/sub_service.py": "VALUE = 8\n",
    "app/engine.py": "VALUE = 9\n",
    "app/sub_backend.py": "VALUE = 10\n",
    "app/pkgx/__init__.py": "from . import other\n",
    "app/pkgx/other.py": "VALUE = 12\n",
    "app/pkgx/backend.py": "VALUE = 11\n",
    "app/sub_state.py": "def reset():\n    pass\n",
    "tests/__init__.py": "",
    "tests/conftest.py": (
        "import pytest\nfrom tests.root_helper import guard\nfrom tests.gone_helper import gone\n\n"
        "@pytest.fixture\ndef base_service():\n    from app import base_service\n    return base_service\n\n"
        "@pytest.fixture\ndef service():\n    import app.engine\n    return app.engine\n\n"
        "@pytest.fixture\ndef boot_dep():\n    return None\n\n"
        "@pytest.fixture(autouse=True)\ndef bootstrap(boot_dep):\n    return boot_dep\n"
    ),
    "tests/root_helper.py": "def guard():\n    return None\n",
    "tests/unit/__init__.py": "",
    "tests/unit/conftest.py": (
        "import sys\nimport pytest\n\n"
        "@pytest.fixture(autouse=True)\ndef _reset():\n"
        "    from app.state_reset import reset\n    reset()\n"
        "    sys.modules.get('app.heavy')\n\n"
        "@pytest.fixture\ndef server():\n    from app import server_mod\n    return server_mod\n\n"
        "@pytest.fixture\ndef wrapped_server(server):\n    return server\n\n"
        "@pytest.fixture\ndef wrapped(base_service):\n    return base_service\n\n"
        "@pytest.fixture\ndef service(service):\n    return service\n\n"
        "@pytest.fixture\ndef pkg_backend():\n    import app.pkgx.backend\n    return app.pkgx.backend\n"
    ),
    "tests/unit/fakes_helper.py": "from app.other import VALUE\n\ndef make():\n    return VALUE\n",
    "tests/unit/sibling_helper.py": "import app.heavy\n",
    "tests/unit/test_core.py": "from app.core import Engine\n\ndef test_engine():\n    assert Engine\n",
    "tests/unit/test_facade.py": "from app import Engine\n\ndef test_facade():\n    assert Engine\n",
    "tests/unit/test_via_helper.py": (
        "from tests.unit.fakes_helper import make\n\ndef test_make():\n    assert make() == 3\n"
    ),
    "tests/unit/test_sibling.py": "import sibling_helper\n\ndef test_sibling():\n    assert sibling_helper\n",
    "tests/unit/test_static_text.py": (
        "from pathlib import Path\n\n"
        "def test_reads_widget():\n    assert Path('static/widget-panel.js').name\n"
    ),
    "tests/unit/test_monkeypatch_string.py": (
        "def test_patch(monkeypatch):\n    monkeypatch.setattr('app.patched.VALUE', 9)\n"
    ),
    # import_module in application code (outside tests/, where dotted strings
    # are not read as module names) is the only edge to app.dynamic.
    "app/registry.py": "import importlib\n\ndef load():\n    return importlib.import_module('app.dynamic')\n",
    "tests/unit/test_importlib.py": (
        "from app.registry import load\n\ndef test_dynamic():\n    assert load()\n"
    ),
    "tests/unit/test_marked.py": (
        "import pytest\nfrom app.core import Engine\n\n"
        "pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]\n\n"
        "def test_marked():\n    assert Engine\n"
    ),
    "tests/unit/test_function_marked.py": (
        "import pytest\nfrom app.util import helper\n\n"
        "@pytest.mark.unit_fast\ndef test_fast():\n    assert helper\n\n"
        "def test_plain():\n    assert helper\n"
    ),
    "tests/unit/test_unrelated.py": "def test_alone():\n    assert True\n",
    "tests/unit/test_template_paren.py": (
        "from pathlib import Path\n\n"
        "def test_page():\n    assert (Path('pages') / 'index.html').name\n"
    ),
    "tests/unit/test_template_parts.py": (
        "from pathlib import Path\n\n"
        "def test_template():\n    assert Path('.') / 'templates' / 'index.html'\n"
    ),
    # Names a doc and a workflow: the docs rule must win over the literal rule.
    "tests/unit/test_docs_reader.py": (
        "def test_docs():\n    assert 'docs/guide.md' and 'README.md' and 'workflows/docs.yml'\n"
        "# see tests/conftest.py and core.py; the plugin docs live elsewhere\n"
    ),
    "tests/unit/sub/__init__.py": "",
    "tests/unit/sub/conftest.py": (
        "import pytest\nimport tests.unit.pkgb\nfrom tests.unit.sub.sub_helper import fixture_value\n"
        "from tests.unit.shared_helper import shared\n\nPOLICY = 'tests/fixtures/policy.json'\n"
        "PAYLOAD = 'tests/fixtures/test_payload.py'\n"
        "SAMPLES = Path(__file__).parent / 'fixtures' / 'sub_samples'\n"
        "SETUP_SCRIPT = 'tests/frontend/sub_setup.mjs'\n\n"
        "@pytest.fixture(autouse=True)\ndef _sub_reset():\n    import app.sub_state\n    import app.gone_backend\n\n"
        "@pytest.fixture\ndef boot_dep():\n    import app.sub_backend\n    return app.sub_backend\n\n"
        "@pytest.fixture\ndef sub_service():\n    from app import sub_service\n    return sub_service\n\n"
        "@pytest.fixture(autouse=True)\ndef _sub_bootstrap(sub_service):\n    return sub_service\n"
    ),
    "tests/unit/test_uses_fixture.py": "def test_server(server):\n    assert server\n",
    "tests/unit/test_wrapped_chain.py": "def test_wrapped(wrapped):\n    assert wrapped\n",
    "tests/unit/test_service.py": "def test_service(service):\n    assert service\n",
    "tests/unit/test_uses_pkg.py": "def test_pkg(pkg_backend):\n    assert pkg_backend\n",
    "tests/unit/pkgb/__init__.py": "",
    "tests/unit/pkgb/test_c.py": "def test_c():\n    assert True\n",
    "tests/unit/test_uses_fixture_indirectly.py": (
        "import pytest\n\n@pytest.mark.usefixtures('wrapped_server')\ndef test_wrapped():\n    pass\n"
    ),
    "tests/unit/sub/sub_helper.py": (
        "SKIP = ['test_unrelated.py']\n\ndef fixture_value():\n    return 1\n"
    ),
    "tests/unit/shared_helper.py": "def shared():\n    return 1\n",
    "tests/unit/pkgb/conftest.py": "from tests.unit.shared_helper import shared\n",
    "tests/unit/sub/test_a.py": "def test_a():\n    assert True\n",
    "tests/unit/sub/test_b.py": "class TestB:\n    def test_one(self):\n        pass\n\n    def test_two(self):\n        pass\n",
}

ALL_CANDIDATES = sorted(
    p for p in FILES if p.startswith("tests/unit/") and Path(p).name.startswith("test_") and p != "tests/unit/test_marked.py"
)


def _write_repo(root: Path) -> None:
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("fake_repo")
    _write_repo(root)
    return root


@pytest.fixture(scope="module")
def index(repo):
    return sel.build_index(repo)


def _selected(index, *changed: str) -> list[str]:
    return sel.select(index, list(changed)).files


def test_transitive_dependency_selects_every_importer(index):
    picked = _selected(index, "app/util.py")
    # test_core -> app.core -> app.util; test_facade reaches app.core through the facade's re-export.
    assert "tests/unit/test_core.py" in picked
    assert "tests/unit/test_facade.py" in picked
    assert "tests/unit/test_unrelated.py" not in picked
    assert "tests/unit/test_via_helper.py" not in picked


def test_dependency_through_a_test_helper_is_followed(index):
    # The nekoclaw regression in #3963 was only visible this way: the test
    # imports a helper under tests/, and the helper imports the changed code.
    picked = _selected(index, "app/other.py")
    assert picked == ["tests/unit/test_via_helper.py"]


def test_changed_test_helper_selects_its_importers(index):
    assert _selected(index, "tests/unit/fakes_helper.py") == ["tests/unit/test_via_helper.py"]


def test_bare_sibling_import_inside_a_test_directory_resolves(index):
    assert "tests/unit/test_sibling.py" in _selected(index, "app/heavy.py")


def test_dotted_string_and_import_module_literals_count_as_imports(index):
    assert _selected(index, "app/patched.py") == ["tests/unit/test_monkeypatch_string.py"]
    assert _selected(index, "app/dynamic.py") == ["tests/unit/test_importlib.py"]


def test_file_name_mentioned_in_a_test_selects_it(index):
    assert _selected(index, "static/widget-panel.js") == ["tests/unit/test_static_text.py"]


def test_generic_file_name_matches_with_its_directory_as_separate_parts(index):
    # The test spells the path as Path(...) / "templates" / "index.html".
    assert _selected(index, "templates/index.html") == ["tests/unit/test_template_parts.py"]
    # A closing parenthesis between the parts: (Path("pages") / "index.html").
    assert _selected(index, "pages/index.html") == ["tests/unit/test_template_paren.py"]
    # The same generic name in another directory is a different file.
    assert _selected(index, "static/index.html") == []


def test_file_name_inside_a_longer_name_does_not_match(index):
    # "panel.js" is a suffix of "widget-panel.js" but names a different file.
    assert _selected(index, "static/panel.js") == []


def test_facade_only_carries_what_the_importer_uses(index):
    # app/__init__ imports heavy too, but test_facade only takes Engine from it.
    assert "tests/unit/test_facade.py" not in _selected(index, "app/heavy.py")
    # A change to the facade itself still reaches everything that executes it.
    picked = _selected(index, "app/__init__.py")
    assert {"tests/unit/test_facade.py", "tests/unit/test_core.py"} <= set(picked)


def test_changed_test_file_selects_itself(index):
    assert _selected(index, "tests/unit/test_unrelated.py") == ["tests/unit/test_unrelated.py"]


@pytest.mark.parametrize("path", sorted(sel.GLOBAL_FILES))
def test_global_files_select_the_full_suite(index, path):
    result = sel.select(index, ["app/heavy.py", path])
    assert result.mode == "full"
    assert result.files == ALL_CANDIDATES


def test_unit_package_init_is_global(index):
    # tests/ has no __init__.py, so every test module is imported inside the
    # `unit` package; the parametrized case above reads GLOBAL_FILES itself.
    assert sel.select(index, ["tests/unit/__init__.py"]).mode == "full"


def test_helper_of_root_conftest_counts_as_global(index):
    result = sel.select(index, ["tests/root_helper.py"])
    assert result.mode == "full"


def test_sub_directory_conftest_selects_its_directory(index):
    # A test package's __init__.py runs before each of its test modules, like a conftest.
    for changed in ("tests/unit/sub/conftest.py", "tests/unit/sub/sub_helper.py", "tests/unit/sub/__init__.py"):
        assert _selected(index, changed) == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_fixture_parameter_counts_as_a_dependency(index):
    # Neither test imports app.server_mod; they get it from a conftest fixture,
    # one through a parameter and one through usefixtures on a fixture chain.
    assert _selected(index, "app/server_mod.py") == [
        "tests/unit/test_uses_fixture.py",
        "tests/unit/test_uses_fixture_indirectly.py",
    ]


def test_module_imported_by_a_root_autouse_fixture_selects_everything(index):
    result = sel.select(index, ["app/state_reset.py"])
    assert result.mode == "full"
    assert "app.state_reset" in result.reason
    # sys.modules lookups in that fixture load nothing and do not count.
    assert sel.select(index, ["app/heavy.py"]).mode == "partial"


def test_module_imported_by_a_sub_directory_autouse_fixture_selects_that_directory(index):
    assert _selected(index, "app/sub_state.py") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_fixture_chain_crosses_conftests(index):
    # tests/unit/conftest.py's `wrapped` requests `base_service` from tests/conftest.py.
    assert _selected(index, "app/base_service.py") == ["tests/unit/test_wrapped_chain.py"]


def test_fixture_requested_by_an_autouse_fixture_runs_for_its_directory(index):
    assert _selected(index, "app/sub_service.py") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_what_a_root_autouse_import_imports_selects_everything(index):
    assert sel.select(index, ["app/reset_impl.py"]).mode == "full"
    # `from app.state_reset import reset` in that fixture runs app/__init__.py first.
    assert sel.select(index, ["app/__init__.py"]).mode == "full"


def test_deleted_helper_of_a_root_conftest_selects_everything(repo):
    # The change deletes tests/gone_helper.py, which tests/conftest.py still imports.
    index = sel.build_index(repo, extra_modules={"tests.gone_helper": "tests/gone_helper.py"})
    assert sel.select(index, ["tests/gone_helper.py"]).mode == "full"


def test_package_init_keeps_the_scope_of_the_conftest_importing_it(index):
    # tests/unit/sub/conftest.py imports tests.unit.pkgb, so its __init__ runs for sub/ too.
    assert _selected(index, "tests/unit/pkgb/__init__.py") == [
        "tests/unit/pkgb/test_c.py",
        "tests/unit/sub/test_a.py",
        "tests/unit/sub/test_b.py",
    ]


def test_fixture_overriding_itself_still_runs_the_parent(index):
    # tests/unit/conftest.py: service(service) wraps tests/conftest.py's service, which imports app.engine.
    assert _selected(index, "app/engine.py") == ["tests/unit/test_service.py"]


def test_package_init_executed_by_a_fixture_selects_its_users(index):
    # pkg_backend imports app.pkgx.backend, which runs app/pkgx/__init__.py first.
    assert _selected(index, "app/pkgx/__init__.py") == ["tests/unit/test_uses_pkg.py"]
    assert _selected(index, "app/pkgx/backend.py") == ["tests/unit/test_uses_pkg.py"]
    # `app.pkgx.backend` names the module, not the package: what else the package imports does not count.
    assert _selected(index, "app/pkgx/other.py") == []


def test_inherited_autouse_fixture_resolves_its_requests_in_the_child_directory(index):
    # The root autouse bootstrap(boot_dep) gets sub/conftest.py's boot_dep under sub/.
    assert _selected(index, "app/sub_backend.py") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_deleted_module_imported_by_an_autouse_fixture_still_counts(repo):
    index = sel.build_index(repo, extra_modules={"app.gone_backend": "app/gone_backend.py"})
    assert _selected(index, "app/gone_backend.py") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_package_init_run_by_what_a_root_autouse_import_imports_selects_everything(index):
    # The root autouse fixture imports app.state_reset -> app.reset_impl -> app.pkgy.thing,
    # which runs app/pkgy/__init__.py on the way.
    assert sel.select(index, ["app/pkgy/__init__.py"]).mode == "full"


def test_helper_shared_by_two_conftests_scopes_both_directories(index):
    assert _selected(index, "tests/unit/shared_helper.py") == [
        "tests/unit/pkgb/test_c.py",
        "tests/unit/sub/test_a.py",
        "tests/unit/sub/test_b.py",
    ]


def test_file_a_conftest_names_selects_that_conftests_directory(index):
    # sub/conftest.py names tests/fixtures/policy.json; no test there mentions it.
    assert _selected(index, "tests/fixtures/policy.json") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_fixture_data_named_test_star_still_scopes_like_any_other_file(index):
    # tests/fixtures/test_payload.py looks like a test module by name but is data a conftest reads.
    assert _selected(index, "tests/fixtures/test_payload.py") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_test_file_named_by_a_conftest_helper_only_selects_itself(index):
    # sub_helper.py lists test_unrelated.py by name (a skip list); its conftest's tests do not depend on it.
    assert _selected(index, "tests/unit/test_unrelated.py") == ["tests/unit/test_unrelated.py"]


def test_shared_file_names_need_their_directories(index):
    # app/core.py and app/pkgx/core.py share a name, so a bare "core.py" names neither.
    assert "tests/unit/test_docs_reader.py" not in _selected(index, "app/core.py")
    # "tests/conftest.py" in a comment is not plugin/tests/conftest.py.
    assert sel._literal_for("plugin/tests/conftest.py") == ("plugin", "tests", "conftest.py")
    assert _selected(index, "plugin/tests/conftest.py") == []


def test_separately_packaged_directory_resolves_by_its_import_name(index):
    # vendor/libx has its own pyproject.toml; the test puts it on sys.path and imports libx_pkg.
    # test_vendored_facade.py takes VALUE from the package, which re-exports it by import name.
    assert _selected(index, "deps/libx/libx_pkg/geometry_ops.py") == [
        "tests/unit/test_vendored.py",
        "tests/unit/test_vendored_facade.py",
    ]


def test_data_file_a_test_globs_by_directory_selects_it(index):
    assert _selected(index, "tests/unit/fixtures/wire_samples/case_one.json") == ["tests/unit/test_wire_samples.py"]
    # A conftest that globs the directory brings its whole directory along.
    assert _selected(index, "tests/unit/sub/fixtures/sub_samples/one.json") == [
        "tests/unit/sub/test_a.py",
        "tests/unit/sub/test_b.py",
    ]
    # A generic directory name ("fixtures") alone names nothing.
    assert sel._data_dir_literal("tests/fixtures/policy.json") is None
    # A test file outside a data directory does not match through its directory ("frontend").
    assert _selected(index, "tests/frontend/panel_flow.test.cjs") == []


def test_template_read_through_a_parameter_selects_the_test(index):
    # index.html sits in the parametrize list, "layouts" in the path expression: not adjacent.
    assert _selected(index, "layouts/index.html") == ["tests/unit/test_template_param.py"]


def test_only_vendored_packages_get_import_name_aliases(index):
    # plugin/plugins/kitx has a pyproject.toml but is imported as plugin.plugins.kitx:
    # "routes_table.main" in a test must not resolve to it.
    assert _selected(index, "plugin/plugins/kitx/routes_table.py") == []


def test_node_scripts_run_by_a_python_wrapper_select_the_wrapper(index):
    # The wrapper globs test_widget_flow*.mjs in tests/frontend; the script imports the module.
    assert _selected(index, "static/widget-board.mjs") == ["tests/unit/test_widget_frontend.py"]
    # Through a shared harness the script imports.
    assert _selected(index, "static/widget-core.mjs") == ["tests/unit/test_widget_frontend.py"]
    # A change to the script itself.
    assert _selected(index, "tests/frontend/test_widget_flow_board.mjs") == ["tests/unit/test_widget_frontend.py"]
    # A conftest running a script brings its whole directory along.
    assert _selected(index, "static/sub-widget.mjs") == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]
    # "*.mjs" next to "tests/frontend" is too broad to claim a script.
    assert "tests/unit/test_broad_glob.py" not in _selected(index, "static/widget-board.mjs")


def test_scripts_directly_under_tests_are_indexed(index):
    assert "tests/root_level_check.mjs" in index.script_tokens


def test_deleting_a_globbed_script_selects_its_wrapper(repo):
    gone = "tests/frontend/test_widget_flow_gone.mjs"
    index = sel.build_index(repo, extra_scripts=[gone])
    assert _selected(index, gone) == ["tests/unit/test_widget_frontend.py"]


def test_shard_marked_files_are_left_to_the_shards(index):
    assert index.tests["tests/unit/test_marked.py"].shard_marked
    assert "tests/unit/test_marked.py" not in _selected(index, "app/core.py")
    assert "tests/unit/test_marked.py" not in _selected(index, "tests/unit/test_marked.py")
    assert "tests/unit/test_marked.py" not in sel.select(index, ["pytest.ini"]).files
    # A marker on one function leaves the rest of the file to this gate; pytest's -m handles the function.
    assert not index.tests["tests/unit/test_function_marked.py"].shard_marked
    assert "tests/unit/test_function_marked.py" in _selected(index, "app/util.py")


@pytest.mark.parametrize("changed", [["docs/unread_page.md"], ["notes/CHANGES_NOTE.md"]])
def test_docs_nobody_reads_select_nothing(index, changed):
    result = sel.select(index, changed)
    assert result.mode == "none"
    assert result.files == []


def test_doc_a_test_reads_selects_that_test(index):
    # Docs stay out of the import graph, but a contract test that reads one by name still runs.
    assert _selected(index, "docs/guide.md") == ["tests/unit/test_docs_reader.py"]
    # README.md is too generic on its own: it counts only together with its directory,
    # and a root README.md names nothing in particular.
    assert _selected(index, "notes/README.md") == []
    assert sel._literal_for("README.md") is None
    # "tests" is everywhere, so tests/README.md needs the two words next to each other.
    assert "tests/unit/test_docs_reader.py" not in _selected(index, "tests/README.md")


def test_other_workflows_only_select_tests_that_read_them(index):
    assert _selected(index, ".github/workflows/docs.yml") == ["tests/unit/test_docs_reader.py"]
    assert _selected(index, ".github/workflows/analyze.yml") == []


def test_test_weights_count_methods_in_test_classes(index):
    assert index.tests["tests/unit/sub/test_b.py"].weight == 2
    assert index.tests["tests/unit/test_function_marked.py"].weight == 2


# ---- shard planning -------------------------------------------------------


def _weights(n: int) -> dict[str, int]:
    return {f"t{i:03d}.py": 1 + (i * 7) % 23 for i in range(n)}


@pytest.mark.parametrize("share_of_total, shards", [(0.05, 1), (0.29, 1), (0.31, 2), (0.55, 2), (0.61, 3), (0.9, 3), (0.95, 4), (1.0, 4)])
def test_shard_count_follows_selected_share(share_of_total, shards):
    weights = {f"t{i:04d}.py": 1 for i in range(1000)}
    files = sorted(weights)[: int(1000 * share_of_total)]
    plan = sel.plan_shards(files, weights, total_weight=1000)
    assert len(plan) == shards


def test_shards_are_balanced_and_cover_every_file_once():
    weights = _weights(300)
    files = sorted(weights)
    plan = sel.plan_shards(files, weights, total_weight=sum(weights.values()))
    assert len(plan) == sel.MAX_SHARDS
    flat = [f for shard in plan for f in shard]
    assert sorted(flat) == files and len(flat) == len(set(flat))
    loads = [sum(weights[f] for f in shard) for shard in plan]
    # Greedy longest-first: no shard exceeds another by more than the heaviest file.
    assert max(loads) - min(loads) <= max(weights.values())
    assert plan == sel.plan_shards(list(reversed(files)), weights, total_weight=sum(weights.values()))


def test_no_files_means_no_shards():
    assert sel.plan_shards([], {}, total_weight=100) == []


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
<testcase classname="tests.unit.test_core" name="test_engine" file="tests\\unit\\test_core.py" line="3" time="30.0" />
<testcase classname="tests.unit.sub.test_b.TestB" name="test_one" file="tests/unit/sub/test_b.py" line="2" time="1.5" />
<testcase classname="tests.unit.sub.test_b.TestB" name="test_two" file="tests/unit/sub/test_b.py" line="5" time="2.5" />
<testcase classname="x" name="no_file" time="99" />
</testsuite></testsuites>
"""


def test_durations_are_read_per_file_from_junit_reports(tmp_path):
    (tmp_path / "full.xml").write_bytes(JUNIT.encode("utf-8"))
    (tmp_path / "broken.xml").write_bytes(b"<testsuites><testcase")
    assert sel.load_durations(tmp_path) == {"tests/unit/test_core.py": 30.0, "tests/unit/sub/test_b.py": 4.0}
    assert sel.load_durations(tmp_path / "missing") == {}


def test_costs_use_durations_and_estimate_unknown_files(index):
    costs, basis = sel.shard_costs(index, {"tests/unit/test_core.py": 30.0, "tests/unit/sub/test_b.py": 4.0})
    assert basis == "durations"
    assert costs["tests/unit/test_core.py"] == 30.0
    # 34 s over 3 known tests: an unknown two-test file is estimated at two tests' worth.
    assert costs["tests/unit/test_function_marked.py"] == pytest.approx(2 * 34.0 / 3)
    assert "tests/unit/test_marked.py" not in costs


def test_costs_fall_back_to_test_count_without_durations(index):
    costs, basis = sel.shard_costs(index, {})
    assert basis == "test count"
    assert costs["tests/unit/sub/test_b.py"] == 2.0


def test_slow_file_gets_a_shard_of_its_own():
    # By test count these four files look alike; by time one dominates.
    costs = {"slow.py": 600.0, "a.py": 100.0, "b.py": 100.0, "c.py": 100.0, "d.py": 100.0}
    plan = sel.plan_shards(sorted(costs), costs, total_weight=1000.0)
    assert len(plan) == 4
    assert ["slow.py"] in plan


# ---- command line, end to end on a real git history --------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _run_cli(root: Path, out: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--out-dir", str(out), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture()
def git_repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    (root / "app" / "other.py").write_bytes(b"VALUE = 30\n")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-am", "change")
    # main moves on after the branch point: the diff must come from the merge-base.
    _git(root, "checkout", "-q", "main")
    (root / "app" / "util.py").write_bytes(b"def helper():\n    return 2\n")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-am", "main moves")
    _git(root, "checkout", "-q", "feature")
    return root


def test_cli_writes_args_files_and_step_outputs(git_repo, tmp_path):
    out = tmp_path / "out"
    gh_output = tmp_path / "gh_output"
    summary = tmp_path / "summary.md"
    proc = _run_cli(git_repo, out, "--base", "main", "--head", "HEAD", "--github-output", str(gh_output), "--summary", str(summary))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "selection.json").read_bytes().decode("utf-8"))
    assert report["changed_files"] == ["app/other.py"]
    assert report["selected_files"] == ["tests/unit/test_via_helper.py"]
    assert (out / "shard-1.args").read_bytes().decode("utf-8").splitlines() == ["tests/unit/test_via_helper.py"]
    outputs = dict(line.split("=", 1) for line in gh_output.read_bytes().decode("utf-8").splitlines())
    assert outputs == {"mode": "partial", "count": "1", "shard_count": "1", "matrix": "[1]"}
    assert report["cost_basis"] == "test count"
    assert "Related unit tests" in summary.read_bytes().decode("utf-8")


def test_cli_balances_by_the_durations_report(git_repo, tmp_path):
    report_dir = tmp_path / "durations"
    report_dir.mkdir()
    # Every file takes a second, except test_core.py, which takes 1000.
    cases = "".join(
        f'<testcase classname="c" name="t" file="{path}" time="{1000.0 if path.endswith("test_core.py") else 1.0}" />'
        for path in ALL_CANDIDATES
    )
    (report_dir / "full.xml").write_bytes(f"<testsuites><testsuite>{cases}</testsuite></testsuites>".encode("utf-8"))
    changed = tmp_path / "changed.txt"
    changed.write_bytes(b"pytest.ini\n")
    out = tmp_path / "out"
    proc = _run_cli(git_repo, out, "--changed-files", str(changed), "--durations", str(report_dir))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "selection.json").read_bytes().decode("utf-8"))
    assert report["mode"] == "full" and report["cost_basis"] == "durations"
    # The full suite is the full cost: it needs every shard, whatever the file count.
    assert len(report["shards"]) == sel.MAX_SHARDS
    heaviest = max(report["shards"], key=lambda s: s["cost"])
    assert (out / f"shard-{heaviest['index']}.args").read_bytes().decode("utf-8").split() == ["tests/unit/test_core.py"]


def test_cli_counts_a_deleted_script(git_repo, tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_bytes(b"tests/frontend/test_widget_flow_gone.mjs\n")
    out = tmp_path / "out"
    proc = _run_cli(git_repo, out, "--changed-files", str(changed))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "selection.json").read_bytes().decode("utf-8"))
    assert report["selected_files"] == ["tests/unit/test_widget_frontend.py"]


def test_cli_falls_back_to_the_full_suite_when_the_diff_fails(git_repo, tmp_path):
    out = tmp_path / "out"
    gh_output = tmp_path / "gh_output"
    proc = _run_cli(git_repo, out, "--base", "no-such-revision", "--github-output", str(gh_output))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "selection.json").read_bytes().decode("utf-8"))
    assert report["mode"] == "full"
    assert report["selected_files"] == ALL_CANDIDATES
    assert "count=0" not in gh_output.read_bytes().decode("utf-8")


def test_cli_reports_nothing_to_run_for_a_docs_change(git_repo, tmp_path):
    (git_repo / "docs" / "guide.md").write_bytes(b"# changed\n")
    _git(git_repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-am", "docs")
    changed = tmp_path / "changed.txt"
    changed.write_bytes(b"docs/unread_page.md\n")
    out = tmp_path / "out"
    gh_output = tmp_path / "gh_output"
    proc = _run_cli(git_repo, out, "--changed-files", str(changed), "--github-output", str(gh_output))
    assert proc.returncode == 0, proc.stderr
    outputs = dict(line.split("=", 1) for line in gh_output.read_bytes().decode("utf-8").splitlines())
    assert outputs["count"] == "0" and outputs["matrix"] == "[]"
    assert not list(out.glob("shard-*.args"))


# ---- workflow wiring ----------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_bytes().decode("utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def _step_runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_full_suite_no_longer_runs_on_pull_requests(workflow):
    job = workflow["jobs"]["unit-pytest"]
    assert job["name"] == "Unit pytest (Windows)"
    assert job["if"] == "github.event_name != 'pull_request'"
    triggers = _triggers(workflow)
    assert set(triggers) >= {"push", "workflow_dispatch", "pull_request"}


def test_related_summary_check_always_reports_on_pull_requests(workflow):
    triggers = _triggers(workflow)
    # A paths filter would make the required check disappear from some PRs.
    assert "paths" not in (triggers["pull_request"] or {})
    assert "paths-ignore" not in (triggers["pull_request"] or {})
    summary = workflow["jobs"]["unit-pytest-related"]
    assert summary["name"] == "Unit pytest (related)"
    assert summary["if"].startswith("always()")
    assert set(summary["needs"]) == {"select-related", "unit-pytest-related-shard"}


def test_related_shards_run_exactly_like_the_full_job(workflow):
    full = _step_runs(workflow["jobs"]["unit-pytest"])
    shard_job = workflow["jobs"]["unit-pytest-related-shard"]
    shard = _step_runs(shard_job)
    assert shard_job["runs-on"] == workflow["jobs"]["unit-pytest"]["runs-on"]
    marker_expr = '-m "not unit_fast and not runtime and not integration_serial and not frontend_contract"'
    for snippet in (marker_expr, "-n auto --dist loadfile", "--basetemp \"${{ runner.temp }}/", "-o junit_family=xunit1"):
        assert snippet in full and snippet in shard
    for package in ("pyclipper", "kaldi-native-fbank"):
        pins = {re.search(rf"{package}==([\w.]+)", text).group(1) for text in (full, shard)}
        assert len(pins) == 1, f"{package} pinned differently: {pins}"
    # The file list must never be spelled out on the Windows command line.
    assert '"@related-tests/shard-${{ matrix.shard }}.args"' in shard


def test_powershell_steps_have_no_scope_qualified_variable_typos(workflow):
    # In a PowerShell string "$rc:" parses as a drive/scope-qualified variable
    # and the whole script fails to parse before its first command runs.
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            shell = step.get("shell") or ("pwsh" if str(job.get("runs-on", "")).startswith("windows") else "bash")
            if shell != "pwsh":
                continue
            bad = [m for m in re.findall(r"\$([A-Za-z_]\w*):", run) if m.lower() not in {"env", "global", "script", "using"}]
            assert not bad, f"{step.get('name')}: ${bad[0]}: is parsed as a qualified variable; write ${{{bad[0]}}}"


def test_full_run_durations_feed_the_pr_shard_plan(workflow):
    full = workflow["jobs"]["unit-pytest"]
    upload = next(step for step in full["steps"] if step.get("with", {}).get("name") == "unit-durations")
    assert upload["if"].startswith("always()")
    assert upload["with"]["path"] in _step_runs(full)
    select_job = workflow["jobs"]["select-related"]
    fetch = next(step for step in select_job["steps"] if "unit-durations" in step.get("run", ""))
    assert fetch["continue-on-error"] is True
    assert "--branch main --event push" in fetch["run"]
    assert "--durations" in _step_runs(select_job)
    assert select_job["permissions"]["actions"] == "read"


def test_selection_job_does_not_install_the_app(workflow):
    job = workflow["jobs"]["select-related"]
    assert job["runs-on"] == "ubuntu-latest"
    runs = _step_runs(job)
    assert "scripts/ci/select_related_tests.py" in runs
    assert "uv sync" not in runs and "pip install" not in runs


def test_selector_never_imports_application_code():
    import ast

    tree = ast.parse(SCRIPT.read_bytes().decode("utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in stdlib | {"__future__"}, name

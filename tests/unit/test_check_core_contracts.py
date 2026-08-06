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


def _dynamic_import_results(
    contract_checker,
    source: str,
) -> list[tuple[tuple[str, ...] | None, bool]]:
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
        (("main_logic.core",), True)
    ]


@pytest.mark.unit
def test_dynamic_import_target_reports_non_literal_argument(contract_checker) -> None:
    source = "import importlib\ndef load(name):\n    return importlib.import_module(name)"

    assert _dynamic_import_results(contract_checker, source) == [(None, True)]


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        (
            "import importlib\n\n"
            "def load():\n"
            '    return importlib.import_module(".core", "main_logic")\n'
        ),
        (
            "def load():\n"
            '    return __import__("main_logic", fromlist=["core"])\n'
        ),
    ],
)
def test_dynamic_import_gate_resolves_relative_and_fromlist_targets(
    contract_checker,
    source: str,
) -> None:
    assert (
        "asr_client must not import main_logic.core (dynamic import)"
        in _dynamic_import_violation_messages(contract_checker, source)
    )


@pytest.mark.unit
def test_dynamic_import_target_ignores_unrelated_calls(contract_checker) -> None:
    source = "def import_module(name):\n    return name\nmod = import_module('main_logic.core')"

    assert _dynamic_import_results(contract_checker, source) == []


def _dynamic_import_violation_messages(contract_checker, source: str) -> list[str]:
    tree = ast.parse(source)
    aliases = contract_checker.module_alias_paths(tree, "main_logic.asr_client")
    return [
        violation.message
        for violation in contract_checker._dynamic_import_violations(
            Path("loader.py"), tree, aliases, "main_logic.core", "asr_client"
        )
    ]


@pytest.mark.unit
def test_dynamic_import_docstring_describes_multiple_forbidden_prefixes(
    contract_checker,
) -> None:
    docstring = contract_checker._dynamic_import_violations.__doc__ or ""

    assert "forbidden prefixes" in docstring


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "def load():\n"
        "    import importlib as il\n"
        "    return il.import_module('main_logic.core')",
        "def load():\n"
        "    from importlib import import_module as im\n"
        "    return im('main_logic.core')",
    ],
)
def test_dynamic_import_violations_sees_function_local_importlib_aliases(
    contract_checker,
    source: str,
) -> None:
    assert _dynamic_import_violation_messages(contract_checker, source) == [
        "asr_client must not import main_logic.core (dynamic import)"
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        # Plain assignment re-binding of the module.
        "import importlib\n"
        "def load():\n"
        "    il = importlib\n"
        "    return il.import_module('main_logic.core')",
        # Annotated assignment must not disable the gate.
        "import importlib\n"
        "from types import ModuleType\n"
        "def load():\n"
        "    il: ModuleType = importlib\n"
        "    return il.import_module('main_logic.core')",
        # Walrus binding, then a read through the bound name.
        "import importlib\n"
        "def load():\n"
        "    if (il := importlib):\n"
        "        return il.import_module('main_logic.core')",
        # Re-binding the entry-point attribute itself.
        "import importlib\n"
        "def load():\n"
        "    im = importlib.import_module\n"
        "    return im('main_logic.core')",
        # Chained re-aliasing needs the fixpoint pass.
        "import importlib\n"
        "def load():\n"
        "    a = importlib\n"
        "    b = a\n"
        "    return b.import_module('main_logic.core')",
        # The __import__ builtin under an assigned alias.
        "def load():\n"
        "    f = __import__\n"
        "    return f('main_logic.core')",
    ],
)
def test_dynamic_import_violations_sees_assignment_rebindings(
    contract_checker,
    source: str,
) -> None:
    assert _dynamic_import_violation_messages(contract_checker, source) == [
        "asr_client must not import main_logic.core (dynamic import)"
    ]


@pytest.mark.unit
def test_dynamic_import_violations_ignores_non_importlib_local_alias(
    contract_checker,
) -> None:
    source = (
        "def load(factory):\n"
        "    il = factory()\n"
        "    return il.import_module('main_logic.core')"
    )

    assert _dynamic_import_violation_messages(contract_checker, source) == []


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
@pytest.mark.parametrize(
    "alias_stmt",
    [
        "rt: IndependentAsrRuntime = self._asr_runtime",
        "rt = self._asr_runtime",
        "(rt := self._asr_runtime)",
    ],
)
def test_asr_runtime_alias_reads_flags_annotated_and_walrus_aliases(
    contract_checker,
    alias_stmt: str,
) -> None:
    source = (
        "class Bridge:\n"
        "    def peek(self):\n"
        f"        {alias_stmt}\n"
        "        return rt.lifecycle\n"
    )
    fn = ast.parse(source).body[0].body[0]

    sites = contract_checker._asr_runtime_alias_reads(fn, {"lifecycle"})

    assert [attr for _line, _col, attr in sites] == ["lifecycle"]


@pytest.mark.unit
def test_asr_runtime_alias_reads_ignores_valueless_annotation_and_augassign(
    contract_checker,
) -> None:
    # A bare annotation binds nothing, and ``+=`` never creates a fresh alias;
    # neither may turn ``rt`` into a tracked runtime alias.
    source = (
        "class Bridge:\n"
        "    def peek(self, rt):\n"
        "        other: IndependentAsrRuntime\n"
        "        rt += self._asr_runtime\n"
        "        return rt.lifecycle, other\n"
    )
    fn = ast.parse(source).body[0].body[0]

    assert contract_checker._asr_runtime_alias_reads(fn, {"lifecycle"}) == []


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


def _write_minimal_speaker_shadow_layout(root: Path) -> Path:
    package = root / "main_logic" / "asr_client" / "speaker_shadow"
    package.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "contracts.py", "runtime.py"):
        (package / name).write_text('"""speaker shadow."""\n', encoding="utf-8")
    return package


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
@pytest.mark.parametrize(
    "source",
    [
        "import main_logic.core\n",
        "from main_logic import asr_client\n",
        "from main_logic.voice_turn import audio_input\n",
        "import main_routers.game_router\n",
        "from utils import preferences\n",
        "import plugin.plugins.demo\n",
        "import importlib\nimportlib.import_module('main_logic.core')\n",
    ],
)
def test_run_flags_forbidden_voice_input_dependencies(
    contract_checker,
    tmp_path,
    source: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    for package in (tmp_path / "main_routers", tmp_path / "utils"):
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
    plugin = tmp_path / "plugin" / "plugins" / "demo"
    plugin.mkdir(parents=True)
    for package in (plugin.parent.parent, plugin.parent, plugin):
        (package / "__init__.py").write_text("", encoding="utf-8")
    voice_input = tmp_path / "main_logic" / "voice_input"
    voice_input.mkdir()
    probe = voice_input / "probe.py"
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "VOICE_INPUT_LAYERING"
    ]

    assert messages


@pytest.mark.unit
def test_missing_voice_input_registry_uses_voice_input_violation_code(
    contract_checker,
    tmp_path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    missing = tmp_path / "main_logic" / "voice_input"

    violations = [
        violation
        for violation in contract_checker.run(tmp_path)
        if violation.path == missing
    ]

    assert len(violations) == 1
    assert violations[0].code == "VOICE_INPUT_LAYERING"
    assert (
        violations[0].message
        == "required layering path is missing (VOICE_INPUT_LAYERING)"
    )


@pytest.mark.unit
def test_run_accepts_frozen_voice_input_dependency_direction(
    contract_checker,
    tmp_path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    voice_input = tmp_path / "main_logic" / "voice_input"
    consumers = voice_input / "consumers"
    consumers.mkdir(parents=True)
    probe = consumers / "game.py"
    probe.write_text(
        "from main_logic.voice_input.contracts import VoiceInputConsumer\n"
        "from main_logic.voice_turn.contracts import VoiceTurnToken\n"
        "from utils.game_route_state import is_game_route_active\n",
        encoding="utf-8",
    )

    violations = [
        violation
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "VOICE_INPUT_LAYERING"
    ]

    assert violations == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "import main_logic.voice_input\n",
        "import importlib\n"
        "importlib.import_module('main_logic.voice_input.registry')\n",
    ],
)
def test_run_flags_asr_client_importing_core_owned_registry(
    contract_checker,
    tmp_path,
    source: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    (tmp_path / "main_logic" / "voice_input").mkdir()
    asr_client = tmp_path / "main_logic" / "asr_client"
    asr_client.mkdir()
    probe = asr_client / "probe.py"
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert any(
        "asr_client must not import main_logic.voice_input" in message
        for message in messages
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative_path", "source", "expected"),
    [
        (
            "main_logic/voice_turn/probe.py",
            "from main_logic.asr_client import runtime\n",
            "voice_turn must not import main_logic.asr_client",
        ),
        (
            "main_logic/core/probe.py",
            "from main_logic.asr_client.endpointing import detector\n",
            "Core must not import main_logic.asr_client.endpointing",
        ),
        (
            "main_logic/asr_client/endpointing/probe.py",
            "from main_logic.core import manager\n",
            "endpointing must not import main_logic.core",
        ),
        (
            "main_logic/asr_client/endpointing/probe.py",
            "from main_logic.asr_client.workers import glm\n",
            "endpointing must not import provider workers",
        ),
        (
            "main_logic/asr_client/endpointing/probe.py",
            "from scripts import prepare_voice_turn_assets\n",
            "endpointing must not import scripts",
        ),
        (
            "main_logic/asr_client/workers/probe.py",
            "from main_logic.asr_client.endpointing import silero_vad\n",
            "provider workers must not import endpointing implementations",
        ),
        (
            "main_logic/asr_client/lifecycle.py",
            "from main_logic.asr_client.endpointing import detector\n",
            "lifecycle.py must not import endpointing",
        ),
        (
            "main_logic/asr_client/provider_policy.py",
            "from main_logic.asr_client.endpointing import detector\n",
            "provider_policy.py must not import endpointing",
        ),
    ],
)
def test_run_flags_endpointing_layer_violations(
    contract_checker,
    tmp_path,
    relative_path: str,
    source: str,
    expected: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    probe = tmp_path / relative_path
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert expected in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative_path", "source", "expected"),
    [
        (
            "main_logic/asr_client/endpointing/__init__.py",
            "",
            "endpointing/__init__.py may contain only a package docstring",
        ),
        (
            "main_logic/asr_client/endpointing/__init__.py",
            '"""package."""\nfrom .smart_turn_v3 import SmartTurnV3\n',
            "endpointing/__init__.py may contain only a package docstring",
        ),
        (
            "main_logic/asr_client/endpointing/onnx_runtime.py",
            "import onnxruntime\n",
            "onnxruntime must remain a lazy function-local import",
        ),
        (
            "main_logic/asr_client/endpointing/onnx_runtime.py",
            "try:\n"
            "    import onnxruntime\n"
            "except ImportError:\n"
            "    onnxruntime = None\n",
            "onnxruntime must remain a lazy function-local import",
        ),
    ],
)
def test_run_flags_endpointing_import_time_regressions(
    contract_checker,
    tmp_path,
    relative_path: str,
    source: str,
    expected: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    probe = tmp_path / relative_path
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source, encoding="utf-8")

    violations = contract_checker.run(tmp_path)
    messages = [
        violation.message
        for violation in violations
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert expected in messages
    assert not any(
        violation.code == "CORE_FACADE_LAYOUT" for violation in violations
    )


@pytest.mark.unit
def test_endpointing_nonliteral_dynamic_import_is_reported_once(
    contract_checker,
    tmp_path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    probe = tmp_path / "main_logic" / "asr_client" / "endpointing" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "import importlib\n\n\n"
        "def load(module_name):\n"
        "    return importlib.import_module(module_name)\n",
        encoding="utf-8",
    )

    violations = [
        violation
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "ASR_LAYERING"
        and "non-literal module name" in violation.message
    ]

    assert len(violations) == 1


@pytest.mark.unit
def test_endpointing_allows_function_local_onnxruntime_import(
    contract_checker,
    tmp_path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    probe = (
        tmp_path
        / "main_logic"
        / "asr_client"
        / "endpointing"
        / "onnx_runtime.py"
    )
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def load_runtime():\n"
        "    import onnxruntime\n"
        "    return onnxruntime\n",
        encoding="utf-8",
    )

    assert not [
        violation
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "ASR_LAYERING"
        and "onnxruntime must remain" in violation.message
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from main_logic.asr_client.speaker_shadow import runtime\n",
            "endpointing may import only speaker_shadow.contracts",
        ),
        (
            "import importlib\n"
            "runtime = importlib.import_module("
            "'main_logic.asr_client.speaker_shadow.campplus')\n",
            "endpointing may import only speaker_shadow.contracts "
            "(dynamic import)",
        ),
    ],
)
def test_endpointing_can_see_only_speaker_shadow_contracts(
    contract_checker,
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    _write_minimal_speaker_shadow_layout(tmp_path)
    probe = (
        tmp_path
        / "main_logic"
        / "asr_client"
        / "endpointing"
        / "speaker_shadow_probe.py"
    )
    probe.parent.mkdir(parents=True)
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert expected in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "from main_logic.asr_client.speaker_shadow.contracts import "
        "SpeakerShadowObserver\n",
        "import importlib\n"
        "contracts = importlib.import_module("
        "'main_logic.asr_client.speaker_shadow.contracts')\n",
    ],
)
def test_endpointing_allows_speaker_shadow_contracts(
    contract_checker,
    tmp_path: Path,
    source: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    _write_minimal_speaker_shadow_layout(tmp_path)
    probe = (
        tmp_path
        / "main_logic"
        / "asr_client"
        / "endpointing"
        / "speaker_shadow_probe.py"
    )
    probe.parent.mkdir(parents=True)
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "ASR_LAYERING"
        and "speaker_shadow" in violation.message
    ]

    assert messages == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from main_logic.asr_client.speaker_shadow.contracts import "
            "SpeakerShadowObserver\n",
            "Core must not import main_logic.asr_client.speaker_shadow",
        ),
        (
            "from somewhere import SpeakerShadowFactory\n",
            "Core must obtain SpeakerShadowFactory only from "
            "main_logic.asr_client.runtime",
        ),
    ],
)
def test_core_speaker_shadow_boundary_is_opaque(
    contract_checker,
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    _write_minimal_speaker_shadow_layout(tmp_path)
    probe = tmp_path / "main_logic" / "core" / "shadow_probe.py"
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert expected in messages


@pytest.mark.unit
def test_core_allows_opaque_speaker_shadow_factory_from_asr_runtime(
    contract_checker,
    tmp_path: Path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    _write_minimal_speaker_shadow_layout(tmp_path)
    probe = tmp_path / "main_logic" / "core" / "shadow_probe.py"
    probe.write_text(
        "from main_logic.asr_client.runtime import SpeakerShadowFactory\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "ASR_LAYERING"
        and "SpeakerShadow" in violation.message
    ]

    assert messages == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("forbidden", "expected_prefix"),
    [
        (
            "main_logic.asr_client.runtime",
            "main_logic.asr_client.runtime",
        ),
        (
            "main_logic.asr_client.endpointing.detector_runtime",
            "main_logic.asr_client.endpointing",
        ),
        (
            "main_logic.asr_client.workers.openai",
            "main_logic.asr_client.workers",
        ),
        (
            "main_logic.asr_client.provider_policy",
            "main_logic.asr_client.provider_policy",
        ),
        (
            "main_logic.asr_client.lifecycle",
            "main_logic.asr_client.lifecycle",
        ),
        (
            "main_logic.voice_turn.audio_input",
            "main_logic.voice_turn",
        ),
        (
            "main_logic.voice_input.consumers",
            "main_logic.voice_input",
        ),
        ("main_routers.voice", "main_routers"),
        ("scripts.prepare_speaker_model", "scripts"),
    ],
)
def test_speaker_shadow_cannot_depend_back_on_owners(
    contract_checker,
    tmp_path: Path,
    forbidden: str,
    expected_prefix: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    package = _write_minimal_speaker_shadow_layout(tmp_path)
    probe = package / "probe.py"
    probe.write_text(f"import {forbidden}\n", encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert f"speaker_shadow must not import {expected_prefix}" in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "from .runtime import SpeakerShadowRuntime\n",
        "import onnxruntime\n",
    ],
)
def test_speaker_shadow_initializer_is_inert(
    contract_checker,
    tmp_path: Path,
    source: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    package = _write_minimal_speaker_shadow_layout(tmp_path)
    package_init = package / "__init__.py"
    package_init.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == package_init and violation.code == "ASR_LAYERING"
    ]

    assert "speaker_shadow/__init__.py may contain only a package docstring" in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "import onnxruntime\n",
        "try:\n    import onnxruntime\nexcept ImportError:\n    onnxruntime = None\n",
        "import importlib\nonnxruntime = importlib.import_module('onnxruntime')\n",
    ],
)
def test_speaker_shadow_onnxruntime_must_be_lazy(
    contract_checker,
    tmp_path: Path,
    source: str,
) -> None:
    _write_minimal_core_layout(tmp_path)
    package = _write_minimal_speaker_shadow_layout(tmp_path)
    probe = package / "campplus.py"
    probe.write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe and violation.code == "ASR_LAYERING"
    ]

    assert (
        "speaker_shadow onnxruntime must remain a lazy function-local import"
        in messages
    )


@pytest.mark.unit
def test_speaker_shadow_allows_function_local_onnxruntime_import(
    contract_checker,
    tmp_path: Path,
) -> None:
    _write_minimal_core_layout(tmp_path)
    package = _write_minimal_speaker_shadow_layout(tmp_path)
    probe = package / "campplus.py"
    probe.write_text(
        "def load_runtime():\n"
        "    import onnxruntime\n"
        "    return onnxruntime\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == probe
        and violation.code == "ASR_LAYERING"
        and "onnxruntime" in violation.message
    ]

    assert messages == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative_path", "is_dir"),
    [
        ("data/speaker_models", True),
        ("tools/voice_eval", True),
        ("main_logic/asr_client/detector_runtime.py", False),
    ],
)
def test_legacy_speaker_shadow_paths_cannot_be_restored(
    contract_checker,
    tmp_path: Path,
    relative_path: str,
    is_dir: bool,
) -> None:
    _write_minimal_core_layout(tmp_path)
    _write_minimal_speaker_shadow_layout(tmp_path)
    legacy_path = tmp_path / relative_path
    if is_dir:
        legacy_path.mkdir(parents=True)
    else:
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text('"""legacy."""\n', encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.run(tmp_path)
        if violation.path == legacy_path and violation.code == "ASR_LAYERING"
    ]

    assert "legacy speaker-shadow path must not be restored" in messages


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


def _chokepoint_core_dir(tmp_path: Path, probe_source: str) -> Path:
    """A minimal core package: the canonical chokepoint plus one probe module."""

    core = tmp_path / "core"
    core.mkdir()
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "asr_runtime.py").write_text(
        '"""m."""\n\n'
        "class AsrRuntimeMixin:\n"
        '    """m."""\n\n'
        "    async def _fail_closed_voice_route(self, reason):\n"
        "        return await self._revoke_lease_for_blocked_route(reason)\n",
        encoding="utf-8",
    )
    (core / "probe.py").write_text(probe_source, encoding="utf-8")
    return core


def _chokepoint_codes(contract_checker, core: Path) -> list[str]:
    return [
        violation.code
        for violation in contract_checker.check_fail_closed_chokepoint(core)
    ]


@pytest.mark.unit
def test_fail_closed_gate_accepts_a_package_that_only_uses_the_chokepoint(
    contract_checker,
    tmp_path: Path,
) -> None:
    core = _chokepoint_core_dir(
        tmp_path,
        '"""m."""\n\n'
        "class Probe:\n"
        '    """m."""\n\n'
        "    async def exit_blocked(self):\n"
        "        return await self._fail_closed_voice_route('reason')\n",
    )

    assert _chokepoint_codes(contract_checker, core) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        # The form the gate always caught...
        "await self._revoke_lease_for_blocked_route('r')",
        # ...and the one-line rewrite that used to walk straight past it:
        # the callee is a Call node, so no Name/Attribute name was resolved.
        "await getattr(self, '_revoke_lease_for_blocked_route')('r')",
        "await getattr(self, '_revoke_voice_input_connection')('r')",
    ],
)
def test_fail_closed_gate_catches_direct_and_dynamic_revokes(
    contract_checker,
    tmp_path: Path,
    call: str,
) -> None:
    core = _chokepoint_core_dir(
        tmp_path,
        '"""m."""\n\n'
        "class Probe:\n"
        '    """m."""\n\n'
        "    async def exit_blocked(self):\n"
        f"        {call}\n",
    )

    assert _chokepoint_codes(contract_checker, core) == [
        "VOICE_FAIL_CLOSED_CHOKEPOINT"
    ]


@pytest.mark.unit
def test_fail_closed_gate_does_not_let_a_same_named_function_exempt_itself(
    contract_checker,
    tmp_path: Path,
) -> None:
    # The exemption is what makes the chokepoint able to call the revoke at all,
    # so it has to be pinned to the canonical module. Otherwise any module can
    # opt out by naming its function _fail_closed_voice_route -- and that name
    # also satisfied the "chokepoint still exists" check, so removing the real
    # one would not have been noticed either.
    core = _chokepoint_core_dir(
        tmp_path,
        '"""m."""\n\n'
        "class Probe:\n"
        '    """m."""\n\n'
        "    async def _fail_closed_voice_route(self, reason):\n"
        "        return await self._revoke_lease_for_blocked_route(reason)\n",
    )

    assert _chokepoint_codes(contract_checker, core) == [
        "VOICE_FAIL_CLOSED_CHOKEPOINT"
    ]


@pytest.mark.unit
def test_fail_closed_gate_reports_a_missing_chokepoint(
    contract_checker,
    tmp_path: Path,
) -> None:
    core = tmp_path / "core"
    core.mkdir()
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "asr_runtime.py").write_text('"""m."""\n', encoding="utf-8")

    assert _chokepoint_codes(contract_checker, core) == [
        "VOICE_FAIL_CLOSED_CHOKEPOINT"
    ]


def _write_minimal_voice_identity_layout(root: Path) -> Path:
    package = root / "main_logic" / "voice_identity"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""identity."""\n', encoding="utf-8")
    (package / "contracts.py").write_text('"""contracts."""\n', encoding="utf-8")
    (package / "reference.py").write_text(
        '"""reference."""\n'
        "import numpy as np\n"
        "from .contracts import SpeakerModelIdentity\n",
        encoding="utf-8",
    )
    (package / "profile.py").write_text(
        '"""profile."""\nfrom .reference import SpeakerReference\n',
        encoding="utf-8",
    )
    return package


@pytest.mark.unit
def test_voice_identity_contract_accepts_in_memory_dependency_direction(
    contract_checker,
    tmp_path: Path,
) -> None:
    _write_minimal_voice_identity_layout(tmp_path)

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_fails_closed_when_package_is_missing(
    contract_checker,
    tmp_path: Path,
) -> None:
    violations = contract_checker.check_voice_identity_contracts(tmp_path)

    assert any(
        violation.path == tmp_path / "main_logic" / "voice_identity"
        and violation.message == "required voice_identity package is missing"
        for violation in violations
    )
    assert sum(
        violation.message == "required voice_identity domain file is missing"
        for violation in violations
    ) == 4


@pytest.mark.unit
def test_voice_identity_contract_rejects_unlisted_modules(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    probe = package / "nested" / "store.py"
    probe.parent.mkdir()
    probe.write_text("import cryptography\n", encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
        if violation.path == probe
    ]

    assert "voice_identity module is missing an explicit dependency allowlist" in messages
    assert "voice_identity domain must not import cryptography" in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "from . import contracts\n",
        "from main_logic.voice_identity import contracts\n",
    ],
)
def test_voice_identity_contract_allows_approved_importfrom_package_anchors(
    contract_checker,
    tmp_path: Path,
    source: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(source, encoding="utf-8")

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_rejects_broad_parent_package_import(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text("import main_logic\n", encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any("found main_logic" in message for message in messages)


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "def persist():\n"
        "    from numpy import save as write\n"
        "    write('embedding.npy', [1.0])\n",
        "def persist():\n"
        "    import numpy as np\n"
        "    np.save('embedding.npy', [1.0])\n",
    ],
)
def test_voice_identity_contract_rejects_local_import_aliases(
    contract_checker,
    tmp_path: Path,
    source: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert "voice_identity imports must be declared at module scope" in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    "rebind",
    [
        "import math as np",
        "np = object()",
        "np: object = object()",
        "(np := object())",
        "def np():\n    pass",
        "class np:\n    pass",
        "np, other = object(), object()",
        "if True:\n    np = object()",
        "async def np():\n    pass",
        "values = [(np := value) for value in ()]",
    ],
)
def test_voice_identity_contract_rejects_module_scope_import_rebinding(
    contract_checker,
    tmp_path: Path,
    rebind: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import numpy as np\n{rebind}\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert (
        "voice_identity module-scope import bindings must not be rebound; found np"
        in messages
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "binding",
    [
        "for math in (np,):\n    pass",
        "with np.errstate() as math:\n    pass",
        "try:\n    raise Exception()\nexcept Exception as math:\n    pass",
        "match np:\n    case math:\n        pass",
        "math += np",
        "def holder(value=(math := np)):\n    pass",
    ],
)
def test_voice_identity_contract_rejects_other_module_scope_binding_forms(
    contract_checker,
    tmp_path: Path,
    binding: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import numpy as np\nimport math\n{binding}\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert (
        "voice_identity module-scope import bindings must not be rebound; "
        "found math"
        in messages
    )


@pytest.mark.unit
def test_voice_identity_contract_rejects_global_rebinding_from_class_body(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "import numpy as np\n"
        "import math\n"
        "class Rebind:\n"
        "    global math\n"
        "    math = np\n"
        "    math.save('embedding.npy', [1.0])\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert (
        "voice_identity module-scope import bindings must not be rebound; "
        "found math"
        in messages
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "local_source",
    [
        "def local():\n    np = object()\n    return np",
        "class Local:\n    np = object()",
        "values = [np for np in ()]",
        "np: object",
    ],
)
def test_voice_identity_contract_allows_non_rebinding_name_reuse(
    contract_checker,
    tmp_path: Path,
    local_source: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import numpy as np\n{local_source}\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_allows_class_local_import_alias_shadowing(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "import numpy as np\n"
        "class SnapshotFactory:\n"
        "    def save(self):\n"
        "        pass\n"
        "class Snapshot:\n"
        "    np = SnapshotFactory()\n"
        "    np.save()\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_rejects_wildcard_imports(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "from numpy import *\nsave('embedding.npy', [1.0])\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert "voice_identity domain must not use wildcard imports" in messages


@pytest.mark.unit
def test_voice_identity_initializer_must_be_docstring_only(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "__init__.py").write_text(
        '"""identity."""\nfrom .profile import SpeakerProfile\n',
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert "voice_identity/__init__.py may contain only a package docstring" in messages


@pytest.mark.unit
def test_voice_identity_contract_requires_complete_domain_layout(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "profile.py").unlink()

    violations = contract_checker.check_voice_identity_contracts(tmp_path)

    assert any(
        violation.code == "VOICE_IDENTITY_LAYERING"
        and violation.path == package / "profile.py"
        and "required voice_identity domain file is missing" in violation.message
        for violation in violations
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "forbidden_import",
    [
        "from main_logic.asr_client import runtime",
        "from main_logic.core import LLMSessionManager",
        "from main_logic.voice_turn import contracts",
        "import main_routers",
        "import app",
        "import onnxruntime",
        "import keyring",
        "import cryptography",
        "import importlib",
        "import logging",
        "import os",
        "import pathlib",
        "import pickle",
        "import sqlite3",
    ],
)
def test_voice_identity_domain_rejects_cross_layer_imports(
    contract_checker,
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(f"{forbidden_import}\n", encoding="utf-8")

    violations = contract_checker.check_voice_identity_contracts(tmp_path)

    assert any(
        violation.code == "VOICE_IDENTITY_LAYERING"
        for violation in violations
    )


@pytest.mark.unit
def test_voice_identity_domain_rejects_dynamic_cross_layer_import(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "import importlib\n"
        "runtime = importlib.import_module('main_logic.asr_client.runtime')\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert (
        "voice_identity domain must not call importlib.import_module"
        in messages
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "owner_path",
    [
        "main_logic/asr_client/probe.py",
        "main_logic/core/probe.py",
        "main_logic/voice_turn/probe.py",
    ],
)
def test_voice_identity_contract_allows_outer_layers_to_consume_domain(
    contract_checker,
    tmp_path: Path,
    owner_path: str,
) -> None:
    _write_minimal_voice_identity_layout(tmp_path)
    probe = tmp_path / owner_path
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "from main_logic.voice_identity import profile\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("eval('1 + 1')\n", "must not call eval"),
        ("exec('value = 1')\n", "must not call exec"),
        ("__import__('math')\n", "must not call __import__"),
        ("open('embedding.bin', 'wb')\n", "file I/O via open"),
        (
            "import numpy as np\nnp.save('embedding.npy', np.ones(1))\n",
            "file I/O via numpy.save",
        ),
        (
            "from numpy import load\nload('embedding.npy')\n",
            "file I/O via numpy.load",
        ),
        (
            "import numpy as np\nnp.savetxt('embedding.txt', np.ones(1))\n",
            "file I/O via numpy.savetxt",
        ),
        (
            "import numpy as np\nnp.loadtxt('embedding.txt')\n",
            "file I/O via numpy.loadtxt",
        ),
        (
            "import numpy as np\nnp.genfromtxt('embedding.csv')\n",
            "file I/O via numpy.genfromtxt",
        ),
        (
            "import numpy as np\nnp.lib.format.open_memmap('embedding.npy')\n",
            "file I/O via numpy.lib.format.open_memmap",
        ),
        (
            "import numpy as np\nnp.array([1.0]).tofile('embedding.bin')\n",
            "file I/O via .tofile",
        ),
        (
            "class Holder:\n"
            "    def persist(self):\n"
            "        self._embedding.tofile('embedding.bin')\n",
            "file I/O via self._embedding.tofile",
        ),
    ],
)
def test_voice_identity_contract_rejects_direct_dynamic_and_file_io_calls(
    contract_checker,
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any(expected in message for message in messages)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        "np.copy([1.0])",
        "np.zeros(1)",
        "np.linalg.norm([1.0])",
    ],
)
def test_voice_identity_contract_allows_in_memory_numpy_calls(
    contract_checker,
    tmp_path: Path,
    call: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import numpy as np\n{call}\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_allows_required_threading_lock(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "import threading\nthreading.Lock()\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        "threading.Thread(target=lambda: None)",
        "threading.Timer(1.0, lambda: None)",
    ],
)
def test_voice_identity_contract_rejects_thread_creation(
    contract_checker,
    tmp_path: Path,
    call: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import threading\n{call}.start()\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any(
        "may only call threading.Lock" in message
        for message in messages
    )


@pytest.mark.unit
def test_voice_identity_contract_applies_comprehension_targets_in_order(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "import numpy as np\n"
        "values = [x for x in (1,) for np in np.load('embedding.npy')]\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert "voice_identity domain must not perform file I/O via numpy.load" in messages


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "def invoke(open):\n    open()\n",
        "def invoke(eval):\n    eval()\n",
        "def invoke(exec):\n    exec()\n",
        "def invoke(__import__):\n    __import__()\n",
        "def open():\n    pass\nopen()\n",
    ],
)
def test_voice_identity_contract_allows_shadowed_protected_builtins(
    contract_checker,
    tmp_path: Path,
    source: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(source, encoding="utf-8")

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("call", "resolved"),
    [
        ("np.lib.npyio.load('embedding.npy')", "numpy.lib.npyio.load"),
        ("np.lib.format.read_array(stream)", "numpy.lib.format.read_array"),
        (
            "np.lib.format.write_array(stream, np.zeros(1))",
            "numpy.lib.format.write_array",
        ),
        ("np.lib.npyio.DataSource()", "numpy.lib.npyio.DataSource"),
        ("np.DataSource().open('embedding.npy')", "numpy.DataSource"),
    ],
)
def test_voice_identity_contract_rejects_unapproved_direct_numpy_calls(
    contract_checker,
    tmp_path: Path,
    call: str,
    resolved: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import numpy as np\n{call}\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any(
        "may only call approved in-memory NumPy APIs" in message
        and resolved in message
        for message in messages
    )


@pytest.mark.unit
def test_voice_identity_contract_allows_unresolved_dump_method(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "class Snapshot:\n"
        "    def dump(self):\n"
        "        return b'snapshot'\n"
        "\n"
        "value = Snapshot().dump()\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_allows_local_variable_dump_method(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "class Snapshot:\n"
        "    def dump(self):\n"
        "        return b'snapshot'\n"
        "\n"
        "snapshot = Snapshot()\n"
        "value = snapshot.dump()\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "function_source",
    [
        "def use(np):\n    np.save()",
        "async def use(np):\n    np.save()",
        "def use():\n    np = Snapshot()\n    np.save()",
        "use = lambda np: np.save()",
        "values = [np.save() for np in (Snapshot(),)]",
        "values = {np.save() for np in (Snapshot(),)}",
        "values = (np.save() for np in (Snapshot(),))",
        "values = {np: np.save() for np in (Snapshot(),)}",
    ],
)
def test_voice_identity_contract_respects_local_import_shadowing(
    contract_checker,
    tmp_path: Path,
    function_source: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "import numpy as np\n"
        "class Snapshot:\n"
        "    def save(self):\n"
        "        return None\n"
        f"{function_source}\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import numpy as np\nnp.array([1.0]).dump('embedding.pkl')\n",
            "file I/O via numpy.ndarray.dump",
        ),
        (
            "import numpy as np\n"
            "np.f2py.compile('end', modulename='carrier')\n",
            "native code via numpy.f2py.compile",
        ),
    ],
)
def test_voice_identity_contract_rejects_direct_numpy_boundary_bypasses(
    contract_checker,
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any(expected in message for message in messages)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import numpy as np\n"
            "embedding = np.array([1.0])\n"
            "embedding.dump('embedding.pkl')\n",
            "file I/O via embedding.dump",
        ),
        (
            "import numpy as np\n"
            "embedding = np.array([1.0])\n"
            "np.ndarray.dump(embedding, 'embedding.pkl')\n",
            "file I/O via numpy.ndarray.dump",
        ),
        (
            "import numpy as np\n"
            "def persist():\n"
            "    embedding = np.array([1.0])\n"
            "    embedding.dump('embedding.pkl')\n",
            "file I/O via embedding.dump",
        ),
    ],
)
def test_voice_identity_contract_rejects_other_ndarray_dump_forms(
    contract_checker,
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(source, encoding="utf-8")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any(expected in message for message in messages)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            "np.recfromtxt('embedding.csv')",
            "file I/O via numpy.recfromtxt",
        ),
        (
            "np.fromregex('embedding.txt', r'.*', [('value', float)])",
            "file I/O via numpy.fromregex",
        ),
        (
            "np.ctypeslib.load_library('carrier', '.')",
            "native library via numpy.ctypeslib.load_library",
        ),
    ],
)
def test_voice_identity_contract_rejects_explicitly_dangerous_numpy_calls(
    contract_checker,
    tmp_path: Path,
    call: str,
    expected: str,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        f"import numpy as np\n{call}\n",
        encoding="utf-8",
    )

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert any(expected in message for message in messages)


@pytest.mark.unit
def test_voice_identity_contract_does_not_claim_reflection_is_a_sandbox(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    (package / "reference.py").write_text(
        "getter = getattr(__builtins__, 'open')\n",
        encoding="utf-8",
    )

    assert contract_checker.check_voice_identity_contracts(tmp_path) == []


@pytest.mark.unit
def test_voice_identity_contract_rejects_model_assets_inside_domain_package(
    contract_checker,
    tmp_path: Path,
) -> None:
    package = _write_minimal_voice_identity_layout(tmp_path)
    model_path = package / "models" / "speaker.onnx"
    model_path.parent.mkdir()
    model_path.write_bytes(b"not-a-real-model")

    messages = [
        violation.message
        for violation in contract_checker.check_voice_identity_contracts(tmp_path)
    ]

    assert (
        "voice_identity domain must not contain packaged assets; "
        "found models/speaker.onnx"
        in messages
    )


# ── CORE_LOCK_NO_AWAIT ──────────────────────────────────────────────────
#
# The gate exists because the atomicity of every ``current_speech_id`` +
# TTS-done-flag write rests on one property of ``self.lock``: no holder ever
# suspends, so the lock is never observed held, so ``acquire()`` always takes
# the uncontended fast path and is therefore not a cancellation point (#2619).
# These tests pin what makes that property checkable.


def _lock_core_dir(
    tmp_path: Path,
    probe_source: str,
    manager_source: str | None = None,
) -> tuple[Path, Path]:
    """A minimal core package: manager.py binding the lock plus one probe module."""

    core = tmp_path / "core"
    core.mkdir()
    (core / "__init__.py").write_text("", encoding="utf-8")
    manager = core / "manager.py"
    manager.write_text(
        manager_source
        if manager_source is not None
        else (
            '"""m."""\n\n'
            "import asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.lock = asyncio.Lock()\n"
        ),
        encoding="utf-8",
    )
    (core / "probe.py").write_text(probe_source, encoding="utf-8")
    return core, manager


def _lock_violations(contract_checker, tmp_path: Path, probe_source: str, **kw):
    core, manager = _lock_core_dir(tmp_path, probe_source, **kw)
    return contract_checker.check_session_lock_atomicity(core, manager)


_CLEAN_PROBE = (
    '"""m."""\n\n'
    "class ProbeMixin:\n"
    '    """m."""\n\n'
    "    async def rotate(self):\n"
    "        await self.prepare()\n"
    "        async with self.lock:\n"
    "            self.current_speech_id = new_id()\n"
    "            self._tts_done_queued_for_turn = False\n"
    "        await self.announce()\n"
)


@pytest.mark.unit
def test_lock_gate_accepts_a_critical_section_with_no_suspension(
    contract_checker,
    tmp_path: Path,
) -> None:
    """Awaits before and after the block are exactly the sanctioned shape."""

    assert _lock_violations(contract_checker, tmp_path, _CLEAN_PROBE) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "statement",
    [
        # The plain form: this is what would make the rotation tearable.
        "await self.flush()",
        "value = await self.flush()",
        # ...and the spellings that suspend without an ``await`` statement of
        # their own, each of which an Await-only scan walks straight past.
        "async for item in self.stream():\n                pass",
        "async with self.other_lock:\n                pass",
        "self.rows = [x async for x in self.stream()]",
        "self.rows = [await self.one(x) for x in self.batch]",
        "yield self.current_speech_id",
    ],
)
def test_lock_gate_rejects_every_suspension_spelling(
    contract_checker,
    tmp_path: Path,
    statement: str,
) -> None:
    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        async with self.lock:\n"
        f"            {statement}\n"
        "            self.current_speech_id = new_id()\n"
    )

    violations = _lock_violations(contract_checker, tmp_path, source)

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "async with self.lock" in violations[0].message


@pytest.mark.unit
def test_lock_gate_reports_one_violation_per_block_not_per_suspension(
    contract_checker,
    tmp_path: Path,
) -> None:
    """The defect is the block, not each await in it — three awaits, one report."""

    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        async with self.lock:\n"
        "            await self.a()\n"
        "            await self.b()\n"
        "            await self.c()\n"
    )

    assert len(_lock_violations(contract_checker, tmp_path, source)) == 1


@pytest.mark.unit
def test_lock_gate_ignores_awaits_in_closures_defined_inside_the_block(
    contract_checker,
    tmp_path: Path,
) -> None:
    """A coroutine DEFINED under the lock does not RUN under it.

    Flagging this would be a false positive: the closure body executes when
    someone awaits the returned object, which by definition is after the
    ``async with`` has exited.
    """

    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        async with self.lock:\n"
        "            async def later():\n"
        "                await self.flush()\n"
        "            self._deferred = later\n"
    )

    assert _lock_violations(contract_checker, tmp_path, source) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("definition", "why"),
    [
        pytest.param(
            "            async def later(x=await self.flush()):\n"
            "                return x\n",
            "default values are evaluated at def time",
            id="awaited-default",
        ),
        pytest.param(
            "            @self.deco(await self.flush())\n"
            "            def later():\n"
            "                pass\n",
            "decorator expressions are evaluated at def time",
            id="awaited-decorator",
        ),
        pytest.param(
            "            self._f = lambda x=await self.flush(): x\n",
            "a lambda default is evaluated at def time too",
            id="awaited-lambda-default",
        ),
    ],
)
def test_lock_gate_still_sees_definition_time_awaits_in_nested_defs(
    contract_checker,
    tmp_path: Path,
    definition: str,
    why: str,
) -> None:
    """Only the deferred BODY of a nested def is exempt, not its setup.

    Measured: ``async def later(x=await flush())`` parses, and the default
    runs at definition time — i.e. right here, with the lock held. Skipping
    the whole node would let that through.
    """

    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        async with self.lock:\n"
        f"{definition}"
        "            self.current_speech_id = new_id()\n"
    )

    violations = _lock_violations(contract_checker, tmp_path, source)

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"], why


@pytest.mark.unit
def test_lock_gate_rejects_a_chained_binding_that_aliases_the_lock(
    contract_checker,
    tmp_path: Path,
) -> None:
    """One object, two names — and the second name is not gate-checked.

    Other lock attributes are intentionally allowed to be held across awaits,
    so an alias of the session lock under another name is a way to suspend
    while holding it.
    """

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.other_lock = self.lock = asyncio.Lock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "exactly one target" in violations[0].message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("preamble", "signature"),
    [
        pytest.param("import custom_locks as asyncio\n", "self", id="import-as"),
        pytest.param("from vendor import locks as asyncio\n", "self", id="from-import-as"),
        pytest.param("import asyncio\nasyncio = custom_locks\n", "self", id="plain-rebind"),
        # Not an import at all: the caller supplies the module.
        pytest.param("import asyncio\n", "self, asyncio", id="parameter"),
        pytest.param("import asyncio\n", "self, *, asyncio=custom_locks", id="kwonly-parameter"),
    ],
)
def test_lock_gate_rejects_shadowing_the_asyncio_name(
    contract_checker,
    tmp_path: Path,
    preamble: str,
    signature: str,
) -> None:
    """The primitive is matched by spelling, so the name must mean stdlib.

    Checked as "exactly one binding, and it is a plain ``import asyncio``"
    rather than as a list of rebinding forms — the list of ways to bind a
    name is open-ended, so a checker built from it stays one form behind.
    """

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            f"{preamble}\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            f"    def __init__({signature}):\n"
            "        self.lock = asyncio.Lock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "only by a plain 'import asyncio'" in violations[0].message


@pytest.mark.unit
@pytest.mark.parametrize(
    "imports",
    [
        pytest.param("import asyncio\n", id="plain"),
        pytest.param("import asyncio\nimport os\n", id="alongside-others"),
        # A submodule import still binds the top-level name to the same stdlib
        # package, so it is the same guarantee; rejecting it would be a false
        # positive that pushes people to work around the gate.
        pytest.param("import asyncio\nimport asyncio.subprocess\n", id="submodule"),
    ],
)
def test_lock_gate_accepts_the_plain_asyncio_import(
    contract_checker,
    tmp_path: Path,
    imports: str,
) -> None:
    """The sanctioned shape must stay accepted."""

    assert _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            f"{imports}\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.lock = asyncio.Lock()\n"
        ),
    ) == []


@pytest.mark.unit
def test_lock_gate_ignores_other_locks(
    contract_checker,
    tmp_path: Path,
) -> None:
    """Only ``self.lock`` carries the contract.

    ``self.tts_cache_lock`` and friends are ordinary locks whose holders may
    await; widening the rule to every attribute named ``*lock`` would fail the
    package on code that is fine.
    """

    source = _CLEAN_PROBE + (
        "\n"
        "    async def flush(self):\n"
        "        async with self.tts_cache_lock:\n"
        "            await self.drain()\n"
    )

    assert _lock_violations(contract_checker, tmp_path, source) == []


# The three rewrites that hold the lock across an await while presenting no
# ``async with self.lock`` block for a shape-only scan to inspect. Kept as
# named constants rather than inline list entries: adjacent string literals
# inside a list are how a missing comma silently merges two cases into one.
_MANUAL_ACQUIRE_RELEASE = (
    "        await self.lock.acquire()\n"
    "        await self.flush()\n"
    "        self.lock.release()\n"
)
_ALIASED_THEN_ENTERED = (
    "        held = self.lock\n"
    "        async with held:\n"
    "            await self.flush()\n"
)
_HANDED_TO_A_HELPER = "        await self._helper_that_awaits(self.lock)\n"


@pytest.mark.unit
@pytest.mark.parametrize(
    "manual",
    [
        pytest.param(_MANUAL_ACQUIRE_RELEASE, id="manual-acquire-release"),
        pytest.param(_ALIASED_THEN_ENTERED, id="aliased-then-entered"),
        pytest.param(_HANDED_TO_A_HELPER, id="handed-to-a-helper"),
    ],
)
def test_lock_gate_rejects_taking_the_lock_outside_a_context_manager(
    contract_checker,
    tmp_path: Path,
    manual: str,
) -> None:
    source = (
        _CLEAN_PROBE
        + "\n"
        + "    async def sneaky(self):\n"
        + manual
    )

    violations = _lock_violations(contract_checker, tmp_path, source)

    # One report per illegal mention, so the acquire/release pair yields two.
    assert violations, "the manual form must not walk past the gate"
    assert {v.code for v in violations} == {"CORE_LOCK_NO_AWAIT"}
    assert all(
        "outside an 'async with' block" in v.message for v in violations
    )


@pytest.mark.unit
def test_lock_gate_rejects_a_context_manager_entered_after_the_lock(
    contract_checker,
    tmp_path: Path,
) -> None:
    """``async with self.lock, other:`` suspends twice with the lock held.

    ``other.__aenter__`` runs after the lock is taken, and its ``__aexit__``
    runs before the lock is released — neither is in the block body, so the
    body scan alone never sees them.
    """

    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        async with self.lock, self.tts_cache_lock:\n"
        "            self.current_speech_id = new_id()\n"
    )

    violations = _lock_violations(contract_checker, tmp_path, source)

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "entered after self.lock" in violations[0].message


@pytest.mark.unit
def test_lock_gate_allows_a_context_manager_entered_before_the_lock(
    contract_checker,
    tmp_path: Path,
) -> None:
    """The other order is genuinely safe, so it must not be rejected.

    On the way in the lock is not held yet; on the way out it is released
    first (``__aexit__`` runs in reverse). Nothing suspends under it.
    """

    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        async with self.tts_cache_lock, self.lock:\n"
        "            self.current_speech_id = new_id()\n"
    )

    assert _lock_violations(contract_checker, tmp_path, source) == []


@pytest.mark.unit
def test_lock_gate_rejects_a_second_binding_that_swaps_the_primitive(
    contract_checker,
    tmp_path: Path,
) -> None:
    """A rebind must not hide behind an earlier, valid-looking binding.

    Checking that SOME binding is ``asyncio.Lock()`` lets a later
    ``self.lock = OtherLock()`` through: the first line still satisfies the
    primitive check while the object the code actually takes is the second.
    """

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self, reentrant=False):\n"
            "        self.lock = asyncio.Lock()\n"
            "        if reentrant:\n"
            "            self.lock = OtherLock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "exactly once" in violations[0].message


@pytest.mark.unit
def test_lock_gate_rejects_an_annotated_rebind_too(
    contract_checker,
    tmp_path: Path,
) -> None:
    """``self.lock: X = Y`` is an AnnAssign, a different node than Assign."""

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.lock = asyncio.Lock()\n"
            "        self.lock: object = OtherLock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "exactly once" in violations[0].message


@pytest.mark.unit
def test_lock_gate_sees_the_lock_taken_through_an_alias_of_self(
    contract_checker,
    tmp_path: Path,
) -> None:
    """``owner = self`` then ``async with owner.lock:`` is the same object.

    Resolving aliases properly is dataflow analysis; matching the attribute
    name whatever the receiver over-approximates instead, which is the safe
    direction. Measured, it costs nothing on the real package: it holds no
    ``.lock`` acquisition with any receiver other than ``self``.
    """

    # Built on the clean probe so the package still holds a real
    # ``async with self.lock`` block: without one the non-vacuity guard fires
    # and the test would pass for the wrong reason.
    source = _CLEAN_PROBE + (
        "\n"
        "    async def sneaky(self):\n"
        "        owner = self\n"
        "        async with owner.lock:\n"
        "            await self.flush()\n"
    )

    violations = _lock_violations(contract_checker, tmp_path, source)

    assert violations
    assert {v.code for v in violations} == {"CORE_LOCK_NO_AWAIT"}
    assert any("must never be held across a suspension" in v.message for v in violations)


@pytest.mark.unit
def test_lock_gate_rejects_a_compound_import_whose_last_alias_shadows(
    contract_checker,
    tmp_path: Path,
) -> None:
    """One import statement, two aliases — Python keeps the last one.

    Judging the containing statement let this pass on the strength of its
    first alias while the name was actually bound to the second.
    """

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import asyncio, custom_locks as asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.lock = asyncio.Lock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "only by a plain 'import asyncio'" in violations[0].message


@pytest.mark.unit
def test_lock_gate_counts_a_bare_local_annotation_of_the_module_name(
    contract_checker,
    tmp_path: Path,
) -> None:
    """``asyncio: object`` in a function makes the NAME local for that scope.

    Unlike an attribute annotation, this one changes what the later
    ``asyncio.Lock()`` resolves to — measured, it raises UnboundLocalError
    rather than reaching the module — so the name no longer means the import
    and the primitive claim no longer holds.
    """

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        asyncio: object\n"
            "        self.lock = asyncio.Lock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "only by a plain 'import asyncio'" in violations[0].message


@pytest.mark.unit
@pytest.mark.parametrize(
    "relative",
    [
        pytest.param("__init__.py", id="facade-initializer"),
        pytest.param("helpers/session.py", id="subpackage-module"),
    ],
)
def test_lock_gate_scans_every_module_in_the_package(
    contract_checker,
    tmp_path: Path,
    relative: str,
) -> None:
    """A holder in the facade or a subpackage is still inside the package.

    ``glob('*.py')`` is non-recursive and the initializer used to be skipped
    outright, so a holder in either place was never parsed.
    """

    core, manager = _lock_core_dir(tmp_path, _CLEAN_PROBE)
    holder = core / relative
    holder.parent.mkdir(parents=True, exist_ok=True)
    holder.write_text(
        '"""m."""\n\n'
        "class SneakyMixin:\n"
        '    """m."""\n\n'
        "    async def hold(self):\n"
        "        async with self.lock:\n"
        "            await self.work()\n",
        encoding="utf-8",
    )

    violations = contract_checker.check_session_lock_atomicity(core, manager)

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "must never be held across a suspension" in violations[0].message


@pytest.mark.unit
def test_lock_gate_does_not_count_a_valueless_annotation_as_a_binding(
    contract_checker,
    tmp_path: Path,
) -> None:
    """``self.lock: asyncio.Lock`` declares a type and assigns nothing.

    It cannot change the runtime primitive, so counting it as a second
    binding would fail the contract on a harmless type declaration — and a
    gate that rejects harmless code teaches people to route around it.
    """

    assert _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import asyncio\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.lock: asyncio.Lock\n"
            "        self.lock = asyncio.Lock()\n"
        ),
    ) == []


@pytest.mark.unit
def test_lock_gate_allows_the_one_binding_assignment_in_manager(
    contract_checker,
    tmp_path: Path,
) -> None:
    """``self.lock = asyncio.Lock()`` is the single sanctioned non-block mention."""

    assert _lock_violations(contract_checker, tmp_path, _CLEAN_PROBE) == []


@pytest.mark.unit
def test_lock_gate_fails_loudly_when_no_lock_block_is_left(
    contract_checker,
    tmp_path: Path,
) -> None:
    """A gate that silently matches nothing is worse than no gate."""

    source = (
        '"""m."""\n\n'
        "class ProbeMixin:\n"
        '    """m."""\n\n'
        "    async def rotate(self):\n"
        "        self.current_speech_id = new_id()\n"
    )

    messages = [v.message for v in _lock_violations(contract_checker, tmp_path, source)]

    assert any("vacuous" in m for m in messages)


@pytest.mark.unit
def test_lock_gate_rejects_swapping_the_lock_primitive(
    contract_checker,
    tmp_path: Path,
) -> None:
    """The fast-path argument is specific to ``asyncio.Lock``.

    A reentrant or threading primitive would break "never observed held" for
    reasons no AST walk over the critical sections can see, so the binding
    itself is part of the contract.
    """

    violations = _lock_violations(
        contract_checker,
        tmp_path,
        _CLEAN_PROBE,
        manager_source=(
            '"""m."""\n\n'
            "import threading\n\n\n"
            "class LLMSessionManager:\n"
            '    """m."""\n\n'
            "    def __init__(self):\n"
            "        self.lock = threading.RLock()\n"
        ),
    )

    assert [v.code for v in violations] == ["CORE_LOCK_NO_AWAIT"]
    assert "asyncio.Lock()" in violations[0].message


@pytest.mark.unit
def test_lock_gate_holds_on_the_real_core_package(contract_checker) -> None:
    """The contract this gate encodes is true of the tree today.

    Not a tautology with the synthetic cases above: this is the measurement
    #2619's premise turns on. If it ever fails, the torn-state window that
    issue described becomes real and the rotation sites need revisiting.
    """

    core_dir = PROJECT_ROOT / "main_logic" / "core"
    violations = contract_checker.check_session_lock_atomicity(
        core_dir, core_dir / "manager.py"
    )

    assert violations == [], "\n".join(v.render(PROJECT_ROOT) for v in violations)

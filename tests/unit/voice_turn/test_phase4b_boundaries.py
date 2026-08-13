from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = node.module or ""
            targets.add(prefix + module)
            targets.update(
                prefix + (f"{module}." if module else "") + alias.name
                for alias in node.names
            )
    return targets


def test_import_targets_include_import_from_symbols(tmp_path: Path) -> None:
    source = tmp_path / "imports.py"
    source.write_text(
        "\n".join(
            (
                "from main_logic.asr_client import endpointing",
                "from . import endpointing",
            )
        ),
        encoding="utf-8",
    )

    assert _import_targets(source) >= {
        "main_logic.asr_client.endpointing",
        ".endpointing",
    }


def test_core_and_lifecycle_do_not_reverse_import_endpointing() -> None:
    for relative in (
        "main_logic/core/asr_runtime.py",
        "main_logic/asr_client/lifecycle.py",
    ):
        targets = _import_targets(PROJECT_ROOT / relative)
        assert all("endpointing" not in target for target in targets)


def test_endpointing_package_init_is_docstring_only() -> None:
    path = PROJECT_ROOT / "main_logic/asr_client/endpointing/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert len(tree.body) == 1
    expression = tree.body[0]
    assert isinstance(expression, ast.Expr)
    assert isinstance(expression.value, ast.Constant)
    assert isinstance(expression.value.value, str)


def test_legacy_detector_and_evaluation_paths_do_not_exist() -> None:
    forbidden = (
        "main_logic/asr_client/detector.py",
        "main_logic/asr_client/detector_runtime.py",
        "main_logic/voice_turn/silero_vad.py",
        "tools/voice_eval",
    )

    assert all(not (PROJECT_ROOT / relative).exists() for relative in forbidden)


def test_evaluation_tool_uses_endpointing_assets_and_project_root() -> None:
    path = PROJECT_ROOT / "scripts/evaluate_speech_presence.py"
    source = path.read_text(encoding="utf-8")
    targets = _import_targets(path)

    assert "PROJECT_ROOT = Path(__file__).resolve().parents[1]" in source
    assert (
        "main_logic.asr_client.endpointing.silero_vad"
        in targets
    )
    assert (
        '"main_logic"\n            / "asr_client"\n'
        '            / "endpointing"\n            / "models"'
    ) in source


def test_online_report_contract_is_chunk_level_and_low_cardinality() -> None:
    path = PROJECT_ROOT / "scripts/evaluate_speech_presence.py"
    source = path.read_text(encoding="utf-8")

    assert '"granularity": "one_input_chunk"' in source
    assert '"rnnoise_fields": ["frame_count", "peak", "mean", "last", "ema"]' in source
    assert '"action_count"' in source
    assert '"evidence_chunk_count"' in source
    assert '"disagreement_count"' in source
    assert '"production_behavior": False' in source


def test_shadow_metric_zero_values_use_named_production_fields() -> None:
    path = PROJECT_ROOT / "scripts/evaluate_speech_presence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    expected_fields = {
        "evidence_chunk_count",
        "incomplete_chunk_count",
        "rnnoise_trigger_count",
        "silero_trigger_count",
        "rnnoise_silero_disagreement_count",
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ThrottleShadowMetrics"
    ]

    assert calls
    assert all(not call.args for call in calls)
    assert all({keyword.arg for keyword in call.keywords} == expected_fields for call in calls)


def test_rnnoise_silence_duration_uses_shared_chunk_constant() -> None:
    source = (
        PROJECT_ROOT / "scripts/evaluate_speech_presence.py"
    ).read_text(encoding="utf-8")

    assert "trailing_frames * 0.01" not in source
    assert "trailing_frames * RNNOISE_CHUNK_MS / 1000.0" in source

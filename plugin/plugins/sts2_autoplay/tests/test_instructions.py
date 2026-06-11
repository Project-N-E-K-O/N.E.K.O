from __future__ import annotations

from plugin.plugins.sts2_autoplay.instructions import instruction_summary, normalize_guidance_instruction, normalize_strategy_instruction


def test_normalize_guidance_instruction_produces_unified_schema() -> None:
    instruction = normalize_guidance_instruction("先防一下", source="user", guidance_type="soft_guidance")

    assert instruction["schema_version"] == 1
    assert instruction["source"] == "neko_guidance"
    assert instruction["kind"] == "directive"
    assert instruction["scope"] == "step"
    assert instruction["content"] == "先防一下"
    assert instruction["payload"]["guidance_type"] == "soft_guidance"
    assert instruction["lifetime"]["consume_on_use"] is True


def test_normalize_strategy_instruction_wraps_constraints() -> None:
    instruction = normalize_strategy_instruction("defect", {"must": ["先保血"], "prefer": ["优先充能"]})

    assert instruction["source"] == "strategy_constraints"
    assert instruction["kind"] == "constraint"
    assert instruction["payload"]["strategy_name"] == "defect"
    assert instruction["payload"]["constraints"]["must"] == ["先保血"]
    assert "strategy=defect" in instruction["content"]


def test_instruction_summary_renders_bullets() -> None:
    summary = instruction_summary(
        [
            normalize_guidance_instruction("先防一下"),
            normalize_strategy_instruction("defect", {"prefer": ["优先充能"]}),
        ]
    )

    assert "- 先防一下" in summary
    assert "- strategy=defect" in summary

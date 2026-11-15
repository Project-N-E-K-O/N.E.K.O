"""Keep authored motivation private and intact, without certifying model behavior."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from services.theater.numeric_v2_registry import NumericV2PackageRegistry
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from utils.tokenize import count_tokens

from .test_numeric_v2_generation import _generation_setup, _idea_outline
from .test_sdk_lifecycle import opened


MOTIVATION = "动机唯一锚MOTIVE_47：她想弄清失约的缘由。\n即使得到解释，也不要求玩家留下或恢复旧日关系。"
LABEL = "作者给定初始动机（可随实际经历改变，不是玩家义务或完成证明）："


def _generate(motivation=MOTIVATION):
    outline = _idea_outline()
    outline["story_protagonist"]["motivation"] = motivation
    before = deepcopy(outline)
    calls = []
    generator = NumericV2Generator()

    def reply(messages, **options):
        calls.append(options["operation"])
        return json.dumps(outline, ensure_ascii=False)

    generator.call_llm = reply
    result = generator.generate(title="旧信", setup=_generation_setup())
    assert calls == ["numeric_v2_mainline_generation"]
    assert outline == before
    return result


def test_motivation_survives_sdk_roundtrip_but_is_absent_from_public_registry(opened, tmp_path):
    sdk = opened[0].sdk
    story = _generate()["story"]
    assert MOTIVATION in story["catgirl_binding"]["role_overlay"]
    before = deepcopy(story)
    project = sdk.import_story(story)
    compiled = sdk.compile(project["project_id"], base_revision=project["revision"])
    sdk.validate(project["project_id"], base_revision=project["revision"])
    exported = sdk.export(project["project_id"], base_revision=project["revision"])
    imported = sdk.import_story(json.loads(exported.json_bytes))
    recompiled = sdk.compile(imported["project_id"], base_revision=imported["revision"])
    assert exported.json_bytes == compiled["json_bytes"] == recompiled["json_bytes"]
    assert compiled["package_hash"] == recompiled["package_hash"]
    assert imported["story"] == story == before
    registry = NumericV2PackageRegistry(tmp_path / "packages")
    registry.import_package(story)
    assert "MOTIVE_47" not in json.dumps(registry.list_packages(), ensure_ascii=False)
    assert registry.load_engine(story["meta"]["story_id"]).story["catgirl_binding"] == story["catgirl_binding"]


def test_node_enhancement_and_quality_repair_preserve_full_global_motivation(opened, monkeypatch):
    sdk = opened[0].sdk
    result = _generate()
    project = sdk.import_story(result["story"])
    original = deepcopy(project["story"]["catgirl_binding"])
    assert MOTIVATION in original["role_overlay"]
    node_id = project["story"]["nodes"][0]["id"]
    monkeypatch.setattr(sdk._generator, "enhance_node", lambda **kwargs: {
        "opening_scene": "花店的窗边仍放着旧信。",
    })
    enhanced = sdk.enhance_node(project["project_id"], node_id, base_revision=project["revision"])["project"]
    assert enhanced["story"]["catgirl_binding"] == original
    repaired = NumericV2QualityAssessor._apply_node_updates(enhanced["story"], {
        "node_updates": [{"node_id": node_id, "story_beat": {"opening_scene": "窗外雨声渐缓，旧信仍在桌上。"}}],
    }, [node_id])
    NumericV2Compiler().compile_v2_2(repaired)
    assert repaired["catgirl_binding"] == original
    context = NumericV2QualityAssessor._assessment_context(
        repaired, enhanced["setup"], enhanced["authoring"],
    )
    assert context["characters"]["relationship"]["current_dynamic"] == original["role_overlay"]
    assert enhanced["story"]["nodes"][0]["story_beat"]["opening_scene"] == "花店的窗边仍放着旧信。"


def test_overlong_motivation_is_preserved_then_rejected_by_existing_compile_limit():
    motivation = "motive " * 400 + "；尾部限制不得删除。"
    story = _generate(motivation)["story"]
    role = story["catgirl_binding"]["role_overlay"]
    assert role.endswith(LABEL + motivation)
    assert count_tokens(role) > 384
    before = deepcopy(story)
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(issue.code == "actor_prompt_field_too_large"
               and issue.path == "catgirl_binding.role_overlay" for issue in caught.value.issues)
    assert story == before

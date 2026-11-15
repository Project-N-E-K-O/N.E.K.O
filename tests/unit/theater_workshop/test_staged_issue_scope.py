"""Saved diagnostics stay with their own writer phase across resumed work."""

from tests.utils.theater_prompt import prompt_payload

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.generation import staged
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
from .test_numeric_v2_generation import _generation_setup, _idea_outline
from .test_staged_generation import staged_reply


def _completed_checkpoint(outline):
    snapshots = []
    NumericV2Generator(lambda messages, **options: staged_reply(messages, options["operation"], outline)).generate_staged(
        title="诊断归属", setup=_generation_setup(), save_checkpoint=snapshots.append)
    return deepcopy(snapshots[-1])


@pytest.mark.parametrize("continuation", [False, True])
def test_saved_sibling_diagnostics_are_scoped_deduplicated_and_preserved(continuation):
    outline = _idea_outline(3)
    checkpoint = _completed_checkpoint(outline)
    second = {"code": "review_timing", "path": "mainline_chapters[1].opening_scene", "message": "第二幕语义问题。"}
    third = {"code": "review_timing", "path": "mainline_chapters[2].opening_scene", "message": "第三幕语义问题。"}
    native = {"code": "native_review_probe", "path": "nodes.mainline_03.story_beat.summary", "message": "第三幕正式诊断。"}
    checkpoint.update(stage="chapter" if continuation else "repair", completed_chapters=1 if continuation else 3,
                      phase_candidate=None, issues=[second, third, deepcopy(third), native], native_issues=[native, deepcopy(native)])
    if continuation:
        checkpoint.pop("repairs", None)
    else:
        checkpoint["repairs"] = [1, 2]
    before = deepcopy(checkpoint)
    seen, snapshots = [], []

    def model(messages, **options):
        data = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation":
            index = data["chapter_index"]
            expected = second if index == 2 else third
            assert index in {2, 3}
            assert data["issues"] == [{**expected, "path": "chapter.opening_scene", "source_path": expected["path"]}]
            assert data["native_validation_issues"] == ([] if index == 2 else [native])
            seen.append(index)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="诊断归属", setup=_generation_setup(),
        checkpoint=checkpoint, save_checkpoint=snapshots.append)
    assert seen == [2, 3]
    assert checkpoint == before
    assert any(third in snapshot["issues"] and second not in snapshot["issues"] for snapshot in snapshots)
    assert snapshots[-1]["issues"] == []
    NumericV2Compiler().compile(result["story"])


def test_ending_consumes_only_ending_diagnostics_and_retains_unhandled_global_issue():
    outline = _idea_outline(3)
    checkpoint = _completed_checkpoint(outline)
    ending = {"code": "review_ending", "path": "ending.summary", "message": "结局语义问题。"}
    global_issue = {"code": "review_world", "path": "tone", "message": "全局语义问题不能交给结局修。"}
    checkpoint.update(stage="repair", repairs=[3], phase_candidate=None,
                      issues=[ending, deepcopy(ending), global_issue])
    snapshots = []

    def model(messages, **options):
        data = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_ending_generation":
            assert data["issues"] == [{**ending, "source_path": "ending.summary"}]
        return staged_reply(messages, options["operation"], outline)

    NumericV2Generator(model).generate_staged(title="结局诊断", setup=_generation_setup(),
        checkpoint=checkpoint, save_checkpoint=snapshots.append)
    assert snapshots[-1]["issues"] == [global_issue]


def test_frame_still_receives_saved_global_diagnostics():
    outline = _idea_outline(3)
    checkpoint = _completed_checkpoint(outline)
    issue = {"code": "review_world", "path": "tone", "message": "全局语义问题。"}
    checkpoint.update(stage="frame", phase_candidate=None, issues=[issue])
    seen = []

    def model(messages, **options):
        if options["operation"] == "numeric_v2_story_frame":
            data = prompt_payload(messages[1]["content"])
            assert data["issues"] == [{**issue, "source_path": "tone"}]
            seen.append(True)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="全局诊断", setup=_generation_setup(), checkpoint=checkpoint)
    assert seen == [True]
    NumericV2Compiler().compile(result["story"])


@pytest.mark.parametrize("path", ["tone", "key_props"])
def test_existing_frame_route_consumes_global_issue_before_preserved_scene_issue(path):
    outline = _idea_outline(3)
    checkpoint = _completed_checkpoint(outline)
    global_issue = {"code": "review_global", "path": path, "message": "由框架负责。"}
    scene = {"code": "review_scene", "path": "mainline_chapters[2].opening_scene", "message": "由第三幕负责。"}
    checkpoint.update(stage="frame", repairs=[2], phase_candidate=None, issues=[global_issue, scene])
    original_frame = deepcopy(checkpoint["story_frame"])
    repaired_value = ["平和"] if path == "tone" else deepcopy(original_frame["key_props"])
    if path == "key_props":
        repaired_value[0]["purpose"] = "用于双方核对纸面记录。"
    seen = []

    def model(messages, **options):
        data = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_story_frame":
            assert data["issues"] == [{**global_issue, "source_path": path}]
            seen.append("frame")
            reply = json.loads(staged_reply(messages, options["operation"], outline))
            reply[path] = deepcopy(repaired_value)
            reply["world"]["background"] = "不在授权路径中的新背景。"
            reply["mainline_chapters"][0]["narrative"] = "不在授权路径中的新剧情。"
            return json.dumps(reply, ensure_ascii=False)
        elif options["operation"] == "numeric_v2_chapter_generation":
            assert data["chapter_index"] == 3
            assert data["story_frame"][path] == repaired_value
            assert data["issues"] == [{**scene, "path": "chapter.opening_scene", "source_path": scene["path"]}]
            seen.append("scene")
        return staged_reply(messages, options["operation"], outline)

    snapshots = []
    result = NumericV2Generator(model).generate_staged(title="现有责任路由", setup=_generation_setup(),
        checkpoint=checkpoint, save_checkpoint=snapshots.append)
    assert seen == ["frame", "scene"]
    assert snapshots[-1]["story_frame"]["world"] == original_frame["world"]
    assert snapshots[-1]["story_frame"]["mainline_chapters"] == original_frame["mainline_chapters"]
    assert snapshots[-1]["story_frame"][path] == snapshots[-1]["candidate"][path] == repaired_value
    assert snapshots[-1]["issues"] == []
    NumericV2Compiler().compile(result["story"])


def test_new_validator_cross_field_issue_is_not_filtered_with_saved_issues(monkeypatch):
    outline = _idea_outline(3)
    checkpoint = _completed_checkpoint(outline)
    draft = json.loads(staged_reply([{}, {"content": '{"chapter_index":2}'}], "numeric_v2_chapter_generation", outline))
    draft["chapter"]["opening_scene"] += "新跨字段错误。"
    checkpoint.update(stage="repair", repairs=[1], phase_candidate=draft, issues=[])
    original = staged._validate_idea_outline
    issue = {"code": "new_cross_field", "path": "key_props", "message": "由本次变更引发的新跨字段错误。"}

    def validate(candidate, **kwargs):
        issues = original(candidate, **kwargs)
        if "新跨字段错误" in candidate["mainline_chapters"][1]["opening_scene"]:
            issues.append(issue)
        return issues

    monkeypatch.setattr(staged, "_validate_idea_outline", validate)
    seen = []

    def model(messages, **options):
        if options["operation"] == "numeric_v2_chapter_generation":
            data = prompt_payload(messages[1]["content"])
            assert {**issue, "source_path": "key_props"} in data["issues"]
            seen.append(True)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="新错误不能被过滤", setup=_generation_setup(), checkpoint=checkpoint)
    assert seen == [True]
    NumericV2Compiler().compile(result["story"])


def test_written_scene_fact_budget_fallback_preserves_later_saved_diagnostic():
    outline = _idea_outline(3)
    checkpoint = _completed_checkpoint(outline)
    written = json.loads(staged_reply([{}, {"content": '{"chapter_index":2}'}], "numeric_v2_chapter_generation", outline))
    written["chapter"]["completion_facts"].extend({"id": f"extra_{index}", "description": "另一个已知结果。",
        "value_type": "bool", "target_value": True, "visibility": "public"} for index in range(8))
    later = {"code": "review_later", "path": "mainline_chapters[2].opening_scene", "message": "第三幕尚未处理。"}
    checkpoint.update(stage="repair", repairs=[1, 2], phase_candidate=None, written_scene=written, issues=[later])
    seen = []

    def model(messages, **options):
        if options["operation"] == "numeric_v2_chapter_generation":
            data = prompt_payload(messages[1]["content"])
            seen.append(data["chapter_index"])
            if data["chapter_index"] == 2:
                assert len(data["current_candidate"]["chapter"]["completion_facts"]) == 9
                assert data["issues"]
                assert all(issue["source_path"] != later["path"] for issue in data["issues"])
            else:
                assert data["issues"] == [{**later, "path": "chapter.opening_scene", "source_path": later["path"]}]
        return staged_reply(messages, options["operation"], outline)

    snapshots = []
    result = NumericV2Generator(model).generate_staged(title="事实额度回退", setup=_generation_setup(),
        checkpoint=checkpoint, save_checkpoint=snapshots.append)
    assert seen == [2, 3] and snapshots[-1]["issues"] == []
    NumericV2Compiler().compile(result["story"])

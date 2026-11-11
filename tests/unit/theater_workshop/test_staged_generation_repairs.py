"""Bounded staged retries preserve valid fields while repairing located omissions."""
from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, NumericV2GenerationError
from .test_numeric_v2_generation import _generation_setup, _idea_outline
from .test_staged_generation import staged_reply


@pytest.mark.parametrize("resume", [False, True])
def test_missing_scene_text_is_repaired_without_rewriting_valid_values(resume):
    outline = _idea_outline(3)
    chapter = outline["mainline_chapters"][1]
    chapter["completion_facts"].extend([
        {"id": "record_read", "description": "已阅读当前记录。", "value_type": "bool",
         "target_value": True, "visibility": "public"},
        {"id": "records_checked", "description": "已核对两条记录。", "value_type": "int",
         "target_value": 2, "visibility": "public"},
        {"id": "record_result", "description": "当前记录已标为核验完成。", "value_type": "string",
         "target_value": "verified", "visibility": "public"},
    ])
    chapter["fixed_narrations"] = [{"id": "record_original", "text": "记录原文：编号 A-17，保留原始标点。",
                                    "trigger": {"type": "entry"}, "after": [], "required_before_exit": False}]
    broken = json.loads(staged_reply([{}, {"content": '{"chapter_index":2}'}],
                                    "numeric_v2_chapter_generation", outline))
    broken["chapter"]["exit_plan"]["proposal"] = ""
    checkpoint = None
    if resume:
        snapshots = []
        NumericV2Generator(lambda messages, **options: staged_reply(messages, options["operation"], outline)).generate_staged(
            title="字段保持", setup=_generation_setup(), save_checkpoint=snapshots.append)
        checkpoint = next(row for row in snapshots if row["completed_chapters"] == 1
                          and row["stage"] == "chapter" and "written_scene" not in row)
        checkpoint["phase_candidate"] = deepcopy(broken)
    before = deepcopy(checkpoint)
    writes, snapshots = [], []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            writes.append(data)
            if not resume and len(writes) == 1:
                return json.dumps(broken, ensure_ascii=False)
            assert set(data["requested_replacements"]) == {"chapter.exit_plan.proposal"}
            assert data["current_candidate"] == broken
            assert data["requested_replacements"]["chapter.exit_plan.proposal"]["current_value"] == ""
            return json.dumps({"replacements": {"chapter.exit_plan.proposal": chapter["exit_plan"]["proposal"]}},
                              ensure_ascii=False)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="字段保持", setup=_generation_setup(),
        checkpoint=checkpoint, save_checkpoint=snapshots.append)
    assert len(writes) == (1 if resume else 2)
    assert checkpoint == before
    assert snapshots[-1]["candidate"]["mainline_chapters"][1] == chapter
    NumericV2Compiler().compile(result["story"])


@pytest.mark.parametrize("invalid_response", ["extra_path", "missing_path", "extra_top_level", "whole_scene"])
def test_unrequested_repair_output_is_rejected_without_consuming_the_candidate(invalid_response):
    outline = _idea_outline(3)
    broken = json.loads(staged_reply([{}, {"content": '{"chapter_index":2}'}],
                                    "numeric_v2_chapter_generation", outline))
    broken["chapter"]["exit_plan"]["proposal"] = ""
    writes = []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            writes.append(data)
            if len(writes) == 1:
                return json.dumps(broken, ensure_ascii=False)
            assert set(data["requested_replacements"]) == {"chapter.exit_plan.proposal"}
            assert data["current_candidate"] == broken
            if len(writes) == 3:
                assert data["response_error"]["code"] == "invalid_replacements"
                assert data["response_error"]["expected_paths"] == ["chapter.exit_plan.proposal"]
            response = {"replacements": {"chapter.exit_plan.proposal": "继续核对下一份收信记录。"}}
            if invalid_response == "extra_path":
                response["replacements"]["chapter.completion_facts[0].target_value"] = "true"
            elif invalid_response == "missing_path":
                response["replacements"] = {}
            elif invalid_response == "extra_top_level":
                response["chapter"] = {"opening_scene": "未获准的新开场。"}
            else:
                response = deepcopy(broken)
                response["chapter"]["exit_plan"]["proposal"] = "继续核对下一份收信记录。"
            return json.dumps(response, ensure_ascii=False)
        return staged_reply(messages, options["operation"], outline)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(model).generate_staged(title="拒绝越界补丁", setup=_generation_setup())
    checkpoint = caught.value.checkpoint
    assert len(writes) == 3
    assert checkpoint["completed_chapters"] == 1
    assert checkpoint["phase_candidate"] == broken
    assert checkpoint["candidate"]["mainline_chapters"][0] == outline["mainline_chapters"][0]
    before = deepcopy(checkpoint)
    resumed_writes = []

    def resume(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            resumed_writes.append(data)
            assert data["current_candidate"] == broken
            return json.dumps({"replacements": {"chapter.exit_plan.proposal": "继续核对下一份收信记录。"}},
                              ensure_ascii=False)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(resume).generate_staged(title="拒绝越界补丁", setup=_generation_setup(), checkpoint=checkpoint)
    assert len(resumed_writes) == 1
    assert checkpoint == before
    NumericV2Compiler().compile(result["story"])


def test_malformed_local_replacement_keeps_the_same_scope_and_candidate():
    outline = _idea_outline(3)
    broken = json.loads(staged_reply([{}, {"content": '{"chapter_index":2}'}],
                                    "numeric_v2_chapter_generation", outline))
    broken["chapter"]["exit_plan"]["proposal"] = ""
    writes, snapshots = [], []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            writes.append(data)
            if len(writes) == 1:
                return json.dumps(broken, ensure_ascii=False)
            assert set(data["requested_replacements"]) == {"chapter.exit_plan.proposal"}
            assert data["current_candidate"] == broken
            if len(writes) == 2:
                return '{"replacements":{"chapter.exit_plan.proposal":"未获采用的"坏引文"。"}}'
            assert data["response_error"]["code"] == "invalid_json"
            assert "未获采用的" not in json.dumps(messages, ensure_ascii=False)
            return json.dumps({"replacements": {
                "chapter.exit_plan.proposal": outline["mainline_chapters"][1]["exit_plan"]["proposal"],
            }}, ensure_ascii=False)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="补丁解析恢复", setup=_generation_setup(),
                                                     save_checkpoint=snapshots.append)
    assert len(writes) == 3
    assert snapshots[-1]["candidate"]["mainline_chapters"][1] == outline["mainline_chapters"][1]
    NumericV2Compiler().compile(result["story"])


@pytest.mark.parametrize("fenced", [False, True])
def test_json_parse_failure_is_reported_without_replaying_the_rejected_draft(fenced):
    outline = _idea_outline(3)
    marker = "INVALID_DRAFT_MUST_NOT_BECOME_CONTEXT"
    malformed = '{"chapter":{"opening_scene":"' + marker + '她说"继续核对"。"}}'
    if fenced:
        malformed = "```json\n" + malformed + "\n```"
    writes = []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            writes.append(data)
            if len(writes) == 1:
                assert "response_error" not in data
                return malformed
            error = data["response_error"]
            assert error["code"] == "invalid_json"
            assert error["message"] and error["line"] >= 1 and error["column"] >= 1
            assert marker not in json.dumps(messages, ensure_ascii=False)
            assert data["current_candidate"] is None
        elif data.get("chapter_index") == 3:
            assert "response_error" not in data
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="解析诊断", setup=_generation_setup())
    assert len(writes) == 2
    NumericV2Compiler().compile(result["story"])


def test_repeated_json_parse_failures_stop_after_three_attempts_and_keep_prior_scenes():
    outline = _idea_outline(3)
    writes = []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            writes.append(data)
            if len(writes) > 1:
                assert data["response_error"]["code"] == "invalid_json"
            return '{"chapter":{"opening_scene":"未转义的"引文"。"}}'
        return staged_reply(messages, options["operation"], outline)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(model).generate_staged(title="解析失败保留进度", setup=_generation_setup())
    checkpoint = caught.value.checkpoint
    assert len(writes) == 3
    assert checkpoint["completed_chapters"] == 1
    assert checkpoint["phase_candidate"] is None
    assert checkpoint["candidate"]["mainline_chapters"][0] == outline["mainline_chapters"][0]


def test_ending_missing_text_is_repaired_without_rewriting_other_ending_fields():
    outline = _idea_outline(3)
    writes, snapshots = [], []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_ending_generation":
            writes.append(data)
            if len(writes) == 1:
                response = json.loads(staged_reply(messages, options["operation"], outline))
                response["ending"]["summary"] = ""
                return json.dumps(response, ensure_ascii=False)
            assert set(data["requested_replacements"]) == {"ending.summary"}
            return json.dumps({"replacements": {"ending.summary": outline["ending"]["summary"]}}, ensure_ascii=False)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="结局局部修订", setup=_generation_setup(),
                                                     save_checkpoint=snapshots.append)
    assert len(writes) == 2
    assert snapshots[-1]["candidate"]["ending"] == outline["ending"]
    NumericV2Compiler().compile(result["story"])


@pytest.mark.parametrize("missing_text", [False, True])
def test_linked_exit_validation_keeps_full_scene_repair_for_related_fields(missing_text):
    outline = _idea_outline(3)
    broken = deepcopy(outline)
    broken["mainline_chapters"][1]["exit_plan"]["advance_on_completion"] = True
    if missing_text:
        broken["mainline_chapters"][1]["exit_plan"]["proposal"] = ""
    outline["mainline_chapters"][1]["exit_plan"].update(
        advance_on_completion=True, player_decision="", fallback_offer="", accept_input="")
    writes = []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            writes.append(data)
            if len(writes) == 1:
                return staged_reply(messages, options["operation"], broken)
            assert any(issue["code"] == "transition_completion_advance_invalid" for issue in data["issues"])
            assert "requested_replacements" not in data
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="关联字段修订", setup=_generation_setup())
    assert len(writes) == 2
    NumericV2Compiler().compile(result["story"])


def test_frame_text_validation_keeps_the_existing_full_frame_protocol():
    outline = _idea_outline(3)
    frames = []

    def model(messages, **options):
        data = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_story_frame":
            frames.append(data)
            assert "requested_replacements" not in data
            if len(frames) == 1:
                response = json.loads(staged_reply(messages, options["operation"], outline))
                response["world"]["background"] = ""
                return json.dumps(response, ensure_ascii=False)
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(title="框架修订边界", setup=_generation_setup())
    assert len(frames) == 2
    NumericV2Compiler().compile(result["story"])

"""Staged creation preserves completed scenes and the native v2.2 package contract."""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, NumericV2GenerationError
from services.theater_workshop.sdk.model import LLMCallFailure
from .test_numeric_v2_generation import _idea_outline, _generation_setup


def staged_reply(messages, operation, outline):
    context = prompt_payload(messages[1]["content"])
    if operation == "numeric_v2_opening_state_extraction":
        index = next((i for i, chapter in enumerate(outline["mainline_chapters"])
                      if chapter["opening_scene"] == context["opening_scene"]), None)
        stage = (outline["character_state_arc"]["ending_stage"] if index is None else
                 outline["character_state_arc"]["stages"][index])
        result = {key: stage[key] for key in ("catgirl_state", "player_state", "environment_state")}
        if "catgirl_situation" in context["output_contract"]:
            result["catgirl_situation"] = outline["mainline_chapters"][index]["catgirl_situation"]
        if "temporal_context" in stage:
            result["temporal_context"] = deepcopy(stage["temporal_context"])
        return json.dumps(result, ensure_ascii=False)
    if operation == "numeric_v2_story_frame":
        frame = deepcopy(outline)
        frame.pop("character_state_arc")
        frame["relationship_arc"].pop("stages")
        frame["mainline_chapters"] = [
            {key: chapter[key] for key in ("title", "narrative")}
            for chapter in outline["mainline_chapters"]
        ]
        frame["ending"] = {key: outline["ending"][key] for key in ("type", "title", "summary")}
        return json.dumps(frame, ensure_ascii=False)
    if operation == "numeric_v2_chapter_generation":
        index = context["chapter_index"] - 1
        return json.dumps({
            "chapter": outline["mainline_chapters"][index],
            "relationship_stage": outline["relationship_arc"]["stages"][index],
            "character_state_stage": outline["character_state_arc"]["stages"][index],
        }, ensure_ascii=False)
    return json.dumps({"ending": outline["ending"],
                       "ending_stage": outline["character_state_arc"]["ending_stage"]}, ensure_ascii=False)


@pytest.mark.parametrize("resume", [False, True])
def test_invalid_fixed_entry_repairs_the_source_after_the_target_is_written(resume):
    outline = _idea_outline(3)
    invalid = deepcopy(outline)
    invalid["mainline_chapters"][0]["exit_plan"]["fixed_entry_only"] = True
    calls, snapshots = [], []

    def model(messages, **options):
        op = options["operation"]
        data = prompt_payload(messages[1]["content"])
        index = data.get("chapter_index")
        issues = [issue["path"] for issue in data.get("issues", ())]
        calls.append((op, index, issues))
        if op == "numeric_v2_chapter_generation" and index == 1 and not issues:
            return staged_reply(messages, op, invalid)
        return staged_reply(messages, op, outline)

    checkpoint = None
    if resume:
        baseline = NumericV2Generator(lambda messages, **kw: staged_reply(messages, kw["operation"], outline))
        baseline.generate_staged(title="跨幕检查", setup=_generation_setup(), save_checkpoint=snapshots.append)
        checkpoint = next(row for row in snapshots if row["stage"] == "ending" and "written_scene" not in row)
        checkpoint["candidate"]["mainline_chapters"][0] = invalid["mainline_chapters"][0]
    story = NumericV2Generator(model).generate_staged(title="跨幕检查", setup=_generation_setup(), checkpoint=checkpoint)["story"]
    writes = [(index, issues) for op, index, issues in calls if op == "numeric_v2_chapter_generation"]
    assert writes == ([] if resume else [(1, []), (2, [])]) + [(1, ["chapter.exit_plan.fixed_entry_only"])] + (
        [] if resume else [(3, [])])
    assert sum(op == "numeric_v2_ending_generation" for op, _, _ in calls) == 1
    assert story["nodes"][0]["route_gates"][0]["transition_contract"].get("fixed_entry_only") is not True
    NumericV2Compiler().compile_v2_2(story)


@pytest.mark.parametrize("resume", ["fresh", "written", "completed"])
def test_staged_repairs_author_exit_instructions_and_pseudo_fact_targets(resume):
    outline = _idea_outline(3)
    setup = _generation_setup()
    bad = deepcopy(outline)
    first = bad["mainline_chapters"][0]
    first["completion_facts"][0].update(value_type="string", target_value="any_non_empty")
    first["exit_plan"]["accept_input"] = "明确选择全拓、只挑部分或暂缓"
    snapshots = []
    checkpoint = None
    if resume != "fresh":
        baseline = NumericV2Generator(lambda messages, **kw: staged_reply(messages, kw["operation"], outline))
        baseline.generate_staged(title="离幕合同", setup=setup, save_checkpoint=snapshots.append)
        if resume == "written":
            checkpoint = next(row for row in snapshots if row.get("written_scene") and row["completed_chapters"] == 0)
            checkpoint["written_scene"]["chapter"] = first
        else:
            checkpoint = next(row for row in snapshots if row["completed_chapters"] == 1
                              and row["stage"] == "chapter" and "written_scene" not in row)
            checkpoint["candidate"]["mainline_chapters"][0] = first

    repaired = []
    def model(messages, **options):
        operation = options["operation"]
        data = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_chapter_generation" and data["chapter_index"] == 1:
            if not data.get("issues"):
                assert resume == "fresh"
                return staged_reply(messages, operation, bad)
            issues = {(issue["code"], issue["path"]) for issue in data["issues"]}
            assert ("completion_fact_target_placeholder", "chapter.completion_facts[0]") in issues
            assert ("transition_accept_input_placeholder", "chapter.exit_plan") in issues
            repaired.append(issues)
        if operation == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            prior_exit = data["previous_scene"]["chapter"]["exit_plan"]
            assert prior_exit == outline["mainline_chapters"][0]["exit_plan"]
            assert "承接已定范围与分工，不重问" in messages[0]["content"]
        return staged_reply(messages, operation, outline)

    result = NumericV2Generator(model).generate_staged(title="离幕合同", setup=setup, checkpoint=checkpoint)
    assert len(repaired) == 1
    NumericV2Compiler().compile_v2_2(result["story"])


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("repair_trigger", [False, True])
def test_over_budget_chapter_retry_repairs_only_the_invalid_field(monkeypatch, resume, repair_trigger):
    from services.theater_workshop.host import NekoWorkshopModel
    from utils.tokenize import count_tokens

    outline = _idea_outline(9)
    setup = {**_generation_setup(), "length_preset": "standard"}
    originals = [(f" {name}" * 1780).strip() + f". Archived text {name}." for name in ("lamp", "book")]
    for chapter, original in zip(outline["mainline_chapters"][:2], originals, strict=True):
        chapter["fixed_narrations"] = [{"id": "author_log", "text": original, "trigger": {"type": "entry"},
                                        "after": [], "required_before_exit": True}]
        chapter["completion_facts"].append({"id": "shown", "description": "已展示本幕登记原文。",
            "fixed_narration_id": "author_log", "value_type": "bool", "target_value": True, "visibility": "public"})
        chapter["exit_plan"]["trigger_fact_ids"].append("shown")

    calls, checkpoints = [], []
    # Keep the full retry over budget while the scoped replacement fits with
    # the independently supplied author brief.
    # Valid requests stay just above 9k since 4ff82de lengthened the scene
    # prompt; the full repair is above 9.6k while the scoped replacement stays
    # near 8k, so 9.3k keeps only the full repair over budget.
    budget = 9300
    host = NekoWorkshopModel({"model": "offline", "base_url": "https://example.invalid/v1", "max_input_tokens": budget})

    def unexpected_client(**_):
        pytest.fail("An over-budget request must not reach the client")

    monkeypatch.setattr("utils.llm_client.create_chat_llm", unexpected_client)
    repair_path = "chapter.fixed_narrations[0].trigger" if repair_trigger else "chapter.expected_turns"
    invalid_trigger = {"type": "condition", "condition": "玩家打开档案柜后。"}

    def bad_chapter(messages, operation):
        response = json.loads(staged_reply(messages, operation, outline))
        if repair_trigger:
            response["chapter"]["fixed_narrations"][0]["trigger"] = invalid_trigger
        else:
            response["chapter"]["expected_turns"] = 0
        return response

    def model(messages, **options):
        operation = options["operation"]
        data = prompt_payload(messages[1]["content"])
        size = count_tokens(json.dumps(messages, ensure_ascii=False))
        if size > budget:
            calls.append(("over_budget", size))
            return host(messages, **options)
        if data.get("requested_replacements"):
            assert operation == "numeric_v2_chapter_generation" and data["chapter_index"] == 2
            assert list(data["requested_replacements"]) == [repair_path]
            field = data["requested_replacements"][repair_path]
            assert field["current_value"] == (invalid_trigger if repair_trigger else 0)
            assert "current_candidate" not in data and "output_contract" not in data
            assert originals[0] in messages[1]["content"]
            assert messages[1]["content"].count(originals[1]) == int(repair_trigger)
            if repair_trigger:
                assert field["original_text"] == originals[1] and field["output_contract"] == {"type": "entry"}
                assert "若必须等待玩家或未知结果" in messages[0]["content"]
            calls.append(("scoped", size))
            patch = {repair_path: {"type": "entry"} if repair_trigger else 4}
            if sum(name == "scoped" for name, _ in calls) == 1:
                patch["chapter.opening_scene"] = "不在修订范围内。"
            return json.dumps({"replacements": patch}, ensure_ascii=False)
        if not resume and operation == "numeric_v2_chapter_generation" and data.get("chapter_index") == 2:
            calls.append(("initial", size))
            return json.dumps(bad_chapter(messages, operation), ensure_ascii=False)
        return staged_reply(messages, operation, outline)

    checkpoint = None
    if resume:
        baseline = NumericV2Generator(lambda messages, **kw: staged_reply(messages, kw["operation"], outline),
                                      token_counter=count_tokens)
        baseline.generate_staged(title="容量", setup=setup, save_checkpoint=checkpoints.append)
        checkpoint = next(row for row in checkpoints if row["completed_chapters"] == 1 and row["stage"] == "chapter"
                          and "written_scene" not in row)
        second = {"role": "user", "content": '{"chapter_index":2}'}
        checkpoint["phase_candidate"] = bad_chapter([{}, second], "numeric_v2_chapter_generation")
        checkpoints.clear()

    story = NumericV2Generator(model, token_counter=count_tokens).generate_staged(
        title="容量", setup=setup, checkpoint=checkpoint, save_checkpoint=checkpoints.append)["story"]
    assert [kind for kind, _ in calls] == ([] if resume else ["initial"]) + ["over_budget", "scoped"] * 2
    assert [chapter["fixed_narrations"][0]["text"] for chapter in checkpoints[-1]["candidate"]["mainline_chapters"][:2]] == originals
    assert checkpoints[-1]["candidate"]["mainline_chapters"][1] == outline["mainline_chapters"][1]
    NumericV2Compiler().compile(story)


def test_staged_scoped_trigger_repair_cannot_modify_author_owned_originals():
    from services.theater_workshop.sdk.generation.staged import _local_repair_fields

    pieces = [
        {"id": "author_original_1", "text": "作者原文。", "trigger": {"type": "entry"}},
        {"id": "generated", "text": "柜边旧钟已经停摆。", "trigger": {"type": "condition", "condition": "玩家推门。"}},
    ]
    draft = {"chapter": {"fixed_narrations": pieces}}
    assert _local_repair_fields(draft, {}, [{"path": "chapter.fixed_narrations[0].trigger"}],
                                author_ids=("author_original_1",)) == {}
    assert _local_repair_fields(draft, {}, [{"path": "chapter.fixed_narrations[1].text"}],
                                author_ids=("author_original_1",)) == {}
    fields = _local_repair_fields(draft, {}, [{"path": "chapter.fixed_narrations[1].trigger"}],
                                  author_ids=("author_original_1",))
    assert fields["chapter.fixed_narrations[1].trigger"]["original_text"] == pieces[1]["text"]


@pytest.mark.parametrize("resume", [False, True])
def test_staged_later_chapters_and_ending_receive_exact_entry_originals(resume):
    outline = _idea_outline(3)
    for index in (0, 2):
        chapter = outline["mainline_chapters"][index]
        chapter["fixed_narrations"] = [
            {"id": "receipt", "text": f"第{index}卷封面写着收信记录。", "trigger": {"type": "entry"},
             "after": [], "required_before_exit": True},
            {"id": "case_number", "text": f"案号 QX-75{index} 已公开。", "trigger": {"type": "entry"},
             "after": [], "required_before_exit": False},
        ]
        chapter["completion_facts"].append({"id": "shown", "description": "已展示收信记录的封面。",
            "fixed_narration_id": "receipt", "value_type": "bool", "target_value": True, "visibility": "public"})
        chapter["exit_plan"]["trigger_fact_ids"].append("shown")

    checked = []

    def model(messages, **options):
        data = prompt_payload(messages[1]["content"])
        op = options["operation"]
        if (op == "numeric_v2_chapter_generation" and data.get("chapter_index") == 2) or op == "numeric_v2_ending_generation":
            index = 0 if op == "numeric_v2_chapter_generation" else 2
            source = data["previous_scene"]["chapter"]
            first, second = outline["mainline_chapters"][index]["fixed_narrations"]
            assert source["completion_originals"] == {"receipt": first["text"]}
            assert source["other_entry_originals"] == {"case_number": second["text"]}
            assert messages[1]["content"].count(second["text"]) == 1
        if (op == "numeric_v2_chapter_generation" and data.get("chapter_index") == 3) or op == "numeric_v2_ending_generation":
            first, second = outline["mainline_chapters"][0]["fixed_narrations"]
            assert data["earlier_entry_originals"] == {"1": {piece["id"]: piece["text"] for piece in (first, second)}}
            assert data["omitted_earlier_entry_chapters"] == []
            assert messages[1]["content"].count(second["text"]) == 1
            checked.append(op)
        return staged_reply(messages, op, outline)

    checkpoint = None
    if resume:
        checkpoints = []
        NumericV2Generator(lambda messages, **opts: staged_reply(messages, opts["operation"], outline)).generate_staged(
            title="公开案号", setup=_generation_setup(), save_checkpoint=checkpoints.append)
        checkpoint = next(row for row in checkpoints if row["completed_chapters"] == 2 and "written_scene" not in row)
    story = NumericV2Generator(model).generate_staged(title="公开案号", setup=_generation_setup(),
                                                     checkpoint=checkpoint)["story"]
    assert checked == ["numeric_v2_chapter_generation", "numeric_v2_ending_generation"]
    NumericV2Compiler().compile(story)


def test_staged_adjacent_author_originals_fit_host_budget_without_truncation(monkeypatch):
    from services.theater_workshop.host import NekoWorkshopModel
    from utils.tokenize import count_tokens

    outline = _idea_outline(6)
    for chapter in outline["mainline_chapters"]:
        chapter["narrative"] += (
            "本幕围绕广播交接展开：逐一核对资料，读出值班员签署的备注，讨论后续联络。"
            "值班员给出两种可行的安排，交接完成后核对下阶段的值班地点和可用时段。"
        )
    setup = {**_generation_setup(), "length_preset": "standard"}
    originals, references = [], []
    for index, (label, noun) in enumerate((("首幕", "archive"), ("第二幕", "register")), 1):
        original = f"第{index}份原始抄件\n" + "\n".join(
            f"{index}-{line:03} {noun} on file for the station at end of shift. Read before forwarding."
            for line in range(1, 85))
        start = f"{label}入幕展示[原文起]"
        end = f"[原文止]{label}抄件结束"
        setup["brief"] += f"{start}{original}{end}。"
        originals.append(original)
        references.append({"chapter_index": index, "trigger_type": "entry",
                           "text_source": {"start_after": start, "end_before": end}})

    budget = 10_000
    host = NekoWorkshopModel({"model": "offline", "base_url": "https://example.invalid/v1", "max_input_tokens": budget})
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda **_: pytest.fail("Offline fixture reached client"))
    checked = []

    def model(messages, **options):
        if count_tokens(json.dumps(messages, ensure_ascii=False)) > budget:
            return host(messages, **options)
        operation = options["operation"]
        data = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_chapter_generation" and data["chapter_index"] == 2:
            assert data["previous_scene"]["chapter"]["other_entry_originals"] == {"author_original_1": originals[0]}
            assert [entry["text"] for entry in data["author_entry_narrations"]] == originals[1:]
            checked.append(True)
        reply = json.loads(staged_reply(messages, operation, outline))
        if operation == "numeric_v2_story_frame":
            reply["author_passages"] = references
        return json.dumps(reply, ensure_ascii=False)

    story = NumericV2Generator(model, token_counter=count_tokens).generate_staged(
        title="相邻原文容量", setup=setup)["story"]
    assert checked == [True]
    assert [node["story_beat"]["fixed_narrations"][0]["text"] for node in story["nodes"][:2]] == originals
    NumericV2Compiler().compile(story)


def test_staged_frame_can_reference_explicit_author_originals_after_host_refusal(monkeypatch):
    from services.theater_workshop.host import NekoWorkshopModel
    from utils.tokenize import count_tokens

    outline = _idea_outline(6)
    setup = {**_generation_setup(), "length_preset": "standard"}
    originals, references = [], []
    for index, label in enumerate(("首幕", "第二幕", "第三幕", "第四幕", "第五幕", "第六幕"), 1):
        original = f"第{index}站值班记\n" + "\n".join(
            f"{line:03}桩|{(line + index) % 19:02}μV|铆闩锁" for line in range(105))
        start = f"{label}入幕展示[原文起]"
        end = f"[原文止]{label}抄件结束"
        setup["brief"] += f"{start}{original}{end}。"
        originals.append(original)
        references.append({"chapter_index": index, "trigger_type": "entry",
                           "text_source": {"start_after": start, "end_before": end}})
    assert len(setup["brief"]) < 12_000
    assert all(count_tokens(text) < 2000 for text in originals)
    host = NekoWorkshopModel({"model": "offline", "base_url": "https://example.invalid/v1", "max_input_tokens": 10_000})
    monkeypatch.setattr("utils.llm_client.create_chat_llm", lambda **_: pytest.fail("Offline fixture reached client"))
    frames = []

    def model(messages, **options):
        op = options["operation"]
        data = prompt_payload(messages[1]["content"])
        if op == "numeric_v2_story_frame":
            frames.append(data["core_idea"])
        if count_tokens(json.dumps(messages, ensure_ascii=False)) > 10_000:
            return host(messages, **options)
        reply = json.loads(staged_reply(messages, op, outline))
        if op == "numeric_v2_story_frame":
            assert data["core_idea"].count("原文编号") == 6
            assert all(text not in data["core_idea"] for text in originals)
            reply["author_passages"] = references
        return json.dumps(reply, ensure_ascii=False)

    story = NumericV2Generator(model, token_counter=count_tokens).generate_staged(
        title="六站值班", setup=setup)["story"]
    assert len(frames) == 2 and frames[0] == setup["brief"]
    assert [node["story_beat"]["fixed_narrations"][0]["text"] for node in story["nodes"][:6]] == originals
    NumericV2Compiler().compile(story)


@pytest.mark.parametrize("overflow", [False, True])
def test_marked_author_original_cannot_be_omitted_or_reach_writing_over_budget(overflow):
    from utils.tokenize import count_tokens

    outline = _idea_outline(3)
    setup = _generation_setup()
    original = "封存钟楼铭牌原文。" * (400 if overflow else 1)
    setup["brief"] += "第二幕须入幕逐字展示[原文起]" + original + "[原文止]"
    calls = []

    def model(messages, **options):
        op = options["operation"]
        calls.append(op)
        return staged_reply(messages, op, outline)  # The frame omits author_passages.

    with pytest.raises(NumericV2GenerationError) as exc:
        NumericV2Generator(model, token_counter=count_tokens).generate_staged(title="带原文", setup=setup)
    assert calls == ([] if overflow else ["numeric_v2_story_frame"] * 3)
    assert exc.value.issues[0]["code"] == ("author_passages_budget_exceeded" if overflow else "author_passages_missing")


@pytest.mark.parametrize("instruction", [
    "第二幕只有玩家亲手打开保险箱以后，才逐字展示{marked}。",
    "第二幕展示{marked}，须等玩家亲手打开保险箱后才逐字展示原文。",
])
def test_explicitly_conditional_author_original_is_rejected_before_generation(instruction):
    setup = _generation_setup()
    setup["brief"] += instruction.format(marked="[原文起]封存通知编号 QX-81。[原文止]")

    def model(messages, **options):
        pytest.fail("条件原文不应进入模型生成")

    with pytest.raises(NumericV2GenerationError, match="invalid_author_original") as exc:
        NumericV2Generator(model).generate_staged(title="条件原文", setup=setup)
    assert exc.value.issues[0]["code"] == "author_passage_condition_unsupported"
    assert exc.value.attempts == 0


def test_frame_reported_conditional_original_stops_without_entry_or_retry():
    outline = _idea_outline(3)
    setup = _generation_setup()
    setup["brief"] += "保险箱的内容要等适当的时机才能公开。[原文起]封存通知编号 QX-81。[原文止]"
    calls = []

    def model(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        reply = json.loads(staged_reply(messages, operation, outline))
        reply["author_passages"] = [{"chapter_index": 2, "trigger_type": "condition",
                                    "text_source": {"start_after": "[原文起]", "end_before": "[原文止]"}}]
        return json.dumps(reply, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError, match="invalid_author_original") as exc:
        NumericV2Generator(model).generate_staged(title="条件原文", setup=setup)
    assert exc.value.issues[0]["code"] == "author_passage_condition_unsupported"
    assert calls == ["numeric_v2_story_frame"]


def test_frame_keeps_each_marked_occurrence_of_identical_author_text():
    outline = _idea_outline(3)
    setup = _generation_setup()
    original = "【001】钟声停住。\n【002】数字归零。"
    setup["brief"] += f"首幕入幕展示[原文起]{original}[原文止]首幕结束。第二幕入幕展示[原文起]{original}[原文止]第二幕结束。"
    rows = [
        {"chapter_index": index, "trigger_type": "entry", "text_source": {
            "start_after": f"{label}入幕展示[原文起]", "end_before": f"[原文止]{label}结束"}}
        for index, label in enumerate(("首幕", "第二幕"), 1)
    ]
    frame_calls = []

    def model(messages, **options):
        op = options["operation"]
        reply = json.loads(staged_reply(messages, op, outline))
        if op == "numeric_v2_story_frame":
            request = prompt_payload(messages[1]["content"])
            frame_calls.append([issue["code"] for issue in request["issues"]])
            reply["author_passages"] = rows[:len(frame_calls)]
        return json.dumps(reply, ensure_ascii=False)

    story = NumericV2Generator(model).generate_staged(title="重复原文", setup=setup)["story"]
    assert frame_calls == [[], ["author_passages_missing"]]
    assert [node["story_beat"]["fixed_narrations"][0]["text"] for node in story["nodes"][:2]] == [original, original]
    NumericV2Compiler().compile_v2_2(story)


def test_staged_ending_receives_the_frame_direction_and_cannot_rename_it():
    outline = _idea_outline(3)
    direction = "在柜台克制道别，将未决定的下一次见面留给玩家。"
    ending_requests = []

    def model(messages, **options):
        op = options["operation"]
        reply = json.loads(staged_reply(messages, op, outline))
        if op == "numeric_v2_story_frame":
            reply["ending"]["summary"] = direction
        if op == "numeric_v2_ending_generation":
            request = prompt_payload(messages[1]["content"])
            ending_requests.append(request)
            assert request["ending_identity"]["summary"] == direction
            if len(ending_requests) == 1:
                reply["ending"]["title"] = "擅自改名"
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model).generate_staged(title="结局方向", setup=_generation_setup())
    assert len(ending_requests) == 2
    assert ending_requests[1]["issues"][0]["code"] == "assigned_ending_identity_changed"
    assert result["story"]["endings"][0]["title"] == outline["ending"]["title"]
    NumericV2Compiler().compile_v2_2(result["story"])


def test_staged_scene_and_ending_receive_prerequisites_separately_from_completion_targets():
    outline = _idea_outline(3)
    for index in (0, 2):
        chapter = outline["mainline_chapters"][index]
        chapter["completion_facts"] = [
            {"id": key, "description": description, "value_type": "bool", "target_value": True, "visibility": "public"}
            for key, description in (
                ("permit", "玩家签署编号 XQ-701 的通行许可。"),
                ("notice", "角色已经确认通知送达。"),
                ("lever", "角色已经拔下红色拉杆。"),
            )
        ]
        chapter["exit_plan"].update(trigger_fact_ids=["notice", "lever"], entry_prerequisite_fact_ids=["permit", "notice"])

    received = []

    def model(messages, **options):
        op = options["operation"]
        context = prompt_payload(messages[1]["content"])
        if op == "numeric_v2_ending_generation" or op == "numeric_v2_chapter_generation" and context["chapter_index"] == 2:
            previous = context["previous_scene"]["chapter"]
            received.append(previous)
            assert [fact["id"] for fact in previous["entry_prerequisites"]] == ["permit", "notice"]
            assert "XQ-701" in previous["entry_prerequisites"][0]["description"]
            assert [fact["id"] for fact in previous["completion_facts"]] == ["lever"]
            assert "合法入路可能尚未完成" in messages[0]["content"]
        return staged_reply(messages, op, outline)

    story = NumericV2Generator(model).generate_staged(title="入口前提", setup=_generation_setup())["story"]
    assert len(received) == 2
    for index in (0, 2):
        route = story["nodes"][index]["route_gates"][0]
        assert {row["key"].split(":")[-1] for row in route["conditions"]["all"]} == {"permit", "notice"}


def test_staged_fact_capacity_repairs_only_affected_chapters_and_rechecks_written_drafts():
    outline = _idea_outline(9)
    setup = {**_generation_setup(), "length_preset": "standard"}
    for chapter, size in zip(outline["mainline_chapters"], [8] * 7 + [7, 1], strict=True):
        chapter["completion_facts"] = [
            {"id": f"fact_{number}", "description": f"核对档案的第{number}项结果。", "value_type": "bool",
             "target_value": True, "visibility": "public"}
            for number in range(size)
        ]
        chapter["exit_plan"]["trigger_fact_ids"] = ["fact_0"]
    calls, checkpoints = [], []

    def model(messages, **options):
        op = options["operation"]
        request = prompt_payload(messages[1]["content"])
        if op == "numeric_v2_chapter_generation":
            calls.append((request["chapter_index"], request["remaining_typed_fact_keys"],
                          [issue["code"] for issue in request["issues"]]))
        return staged_reply(messages, op, outline)

    generator = NumericV2Generator(model)
    original = generator.generate_staged(title="事实容量", setup=setup, save_checkpoint=checkpoints.append)["story"]
    assert calls[-2:] == [(8, 7, []), (9, 1, [])]
    assert len(NumericV2Compiler().compile_v2_2(original).story["fact_contract"]["facts"]) == 64

    old = deepcopy(checkpoints[-1])
    old.update(stage="frame", repairs=[], native_issues=[{
        "code": "fact_contract_limit_exceeded", "path": "fact_contract.facts", "message": "超过64项。",
    }])
    for index in (7, 8):
        old["candidate"]["mainline_chapters"][index]["completion_facts"] = deepcopy(outline["mainline_chapters"][0]["completion_facts"])
    calls.clear()
    recovered = generator.generate_staged(title="事实容量", setup=setup, checkpoint=old)["story"]
    assert calls == [(8, 7, ["fact_contract_limit_exceeded"]), (9, 1, ["fact_contract_limit_exceeded"])]
    assert recovered == original

    written = next(checkpoint for checkpoint in checkpoints
                   if checkpoint["completed_chapters"] == 7 and "written_scene" in checkpoint)
    written["written_scene"]["chapter"]["completion_facts"] = deepcopy(outline["mainline_chapters"][0]["completion_facts"])
    calls.clear()
    recovered = generator.generate_staged(title="事实容量", setup=setup, checkpoint=written)["story"]
    assert calls == [(8, 7, ["fact_contract_limit_exceeded"]), (9, 1, [])]
    assert recovered == original

    partial = next(checkpoint for checkpoint in checkpoints if checkpoint["completed_chapters"] == 8
                   and "written_scene" not in checkpoint)
    partial["candidate"]["mainline_chapters"][7]["completion_facts"] = deepcopy(outline["mainline_chapters"][0]["completion_facts"])
    calls.clear()
    recovered = generator.generate_staged(title="事实容量", setup=setup, checkpoint=partial)["story"]
    assert calls == [(8, 7, ["fact_contract_limit_exceeded"]), (9, 1, [])]
    assert recovered == original


def test_completed_legacy_checkpoint_repairs_condition_original_in_ending():
    outline, checkpoints, calls = _idea_outline(3), [], []

    def model(messages, **options):
        op = options["operation"]
        calls.append((op, prompt_payload(messages[1]["content"])["issues"]))
        return staged_reply(messages, op, outline)

    generator = NumericV2Generator(model)
    original = generator.generate_staged(title="结局原文", setup=_generation_setup(), save_checkpoint=checkpoints.append)["story"]
    saved = checkpoints[-1]
    saved["candidate"]["ending"]["fixed_narrations"] = [{
        "id": "report", "text": "最后一份报告。", "trigger": {"type": "condition", "condition": "玩家打开报告。"},
        "after": [], "required_before_exit": False,
    }]
    calls.clear()
    recovered = generator.generate_staged(title="结局原文", setup=_generation_setup(), checkpoint=saved)["story"]
    assert [op for op, _ in calls] == ["numeric_v2_ending_generation", "numeric_v2_opening_state_extraction"]
    assert "author_passage_condition_unsupported" in {issue["code"] for issue in calls[0][1]}
    assert recovered == original


@pytest.mark.parametrize("label", ["第二幕逐字展示", "第二幕开场：逐字展示", "请在第 2 幕开场原样播放", "第二幕开场："])
def test_frame_references_author_originals_once_and_only_sends_them_to_their_scenes(label):
    outline = _idea_outline(3)
    setup = _generation_setup()
    first = "【001】钟声停住。\n【002】原样保存标点。"
    ending = "最后一页保留原样。"
    setup["brief"] += f"\n{label}[原文起]{first}[原文止]。结局逐字展示[开头B]{ending}[结尾B]。"
    second = outline["mainline_chapters"][1]
    second["completion_facts"].append({
        "id": "notice_shown", "description": "程序已展示作者原文。", "value_type": "bool",
        "target_value": True, "visibility": "public", "fixed_narration_id": "author_original_1",
    })
    second["exit_plan"]["trigger_fact_ids"].append("notice_shown")
    stages = []
    def model(messages, **options):
        op = options["operation"]
        request = messages[1]["content"]
        data = prompt_payload(request)
        stages.append((op, data))
        reply = json.loads(staged_reply(messages, op, outline))
        if op == "numeric_v2_story_frame":
            assert request.count(first.replace("\n", "\\n")) == 1
            assert "brief" not in data["author_setup"] and "length_preset" not in data["author_setup"]
            if data["current_candidate"] is not None:
                assert data["issues"][0]["code"] == "author_passage_wrong_chapter"
            reply["author_passages"] = [
                {"chapter_index": 1 if data["current_candidate"] is None else 2, "trigger_type": "entry",
                 "text_source": {"start_after": "[原文起]", "end_before": "[原文止]"}},
                {"chapter_index": 4, "trigger_type": "entry",
                 "text_source": {"start_after": "[开头B]", "end_before": "[结尾B]"}},
            ]
        elif op == "numeric_v2_chapter_generation":
            assert "assigned_chapter" not in data and "next_outline" not in data
            assert data["author_brief"].count("〔原文由对应幕提供〕") == 2
            assert first not in data["author_brief"] and ending not in data["author_brief"]
            if data["chapter_index"] == 2:
                assert [piece["text"] for piece in data["author_entry_narrations"]] == [first]
                # The program owns the referenced text even if a writer returns an altered version.
                reply["chapter"]["fixed_narrations"] = [{
                    **data["author_entry_narrations"][0], "text": "错误改写。",
                }]
                if data["current_candidate"] is None:
                    reply["chapter"]["opening_scene"] += first
                else:
                    assert data["issues"][0]["code"] == "fixed_narration_echo_in_opening"
            else:
                assert "author_entry_narrations" not in data
                if data["chapter_index"] == 3:
                    assert data["previous_scene"]["chapter"]["completion_originals"] == {"author_original_1": first}
        elif op == "numeric_v2_ending_generation":
            assert [piece["text"] for piece in data["author_entry_narrations"]] == [ending]
            assert first not in data["author_brief"] and ending not in data["author_brief"]
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model).generate_staged(title="作者原文", setup=setup)
    compiled = NumericV2Compiler().compile_v2_2(result["story"])
    assert compiled.story["nodes"][1]["story_beat"]["fixed_narrations"][0]["text"] == first
    assert first not in compiled.story["nodes"][1]["story_beat"]["opening_scene"]
    assert compiled.story["nodes"][-1]["story_beat"]["fixed_narrations"][0]["text"] == ending
    assert "错误改写" not in json.dumps(compiled.story, ensure_ascii=False)
    assert [data["entry_narrations"] for op, data in stages if op == "numeric_v2_opening_state_extraction"
            and "entry_narrations" in data] == [[first], [ending]]


@pytest.mark.parametrize("empty_ending_bridge", [False, True])
@pytest.mark.parametrize("empty_scene_bridge", [False, True])
def test_staged_generation_compiles_and_passes_previous_scene_without_rewriting_it(empty_ending_bridge, empty_scene_bridge):
    outline = _idea_outline(3)
    outline["mainline_chapters"][-1]["opening_scene"] += "此刻是午后。"
    outline["character_state_arc"]["stages"][-1]["temporal_context"] = {
        "anchor": "午后", "relation_to_previous": "same_day"}
    if empty_ending_bridge:
        outline["ending"]["entry_bridge"] = ""
    if empty_scene_bridge:
        for chapter in outline["mainline_chapters"][1:]:
            chapter["entry_bridge"] = ""
    checkpoints, calls = [], []

    def model(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append(options["operation"])
        assert "json" in "".join(message["content"] for message in messages).lower()
        if options["operation"] == "numeric_v2_chapter_generation":
            chapter_contract = context["output_contract"]["chapter"]
            assert "evidence_source" not in chapter_contract["completion_facts"][0]
            assert "玩家自主行动及由玩家单方作出的决定标 evidence_source=player_input" in messages[0]["content"]
            assert "首次 states.chapter_index 不得晚于本幕" in chapter_contract["exit_plan"]["carry_props"][0]
            assert "原文已完整承担角色开场时用 optional" in messages[0]["content"]
            assert "fixed_narration_id" not in chapter_contract["completion_facts"][0]
            assert chapter_contract["fixed_narrations"][0]["trigger"] == {"type": "entry"}
            state_contract = context["output_contract"]["character_state_stage"]
            assert not {"catgirl_state", "player_state", "environment_state"} & state_contract.keys()
            assert "catgirl_situation" not in chapter_contract
            assert chapter_contract["fixed_narrations"][0]["required_before_exit"] is False
            assert context["length"] == {"preset": "short", "scene_expected_turns_target": 8}
            assert "narrative 和 opening_scene 各不超过384 tokens" in messages[0]["content"]
            index = context["chapter_index"] - 1
            if index == len(outline["mainline_chapters"]) - 1:
                assert "玩家行动仍由玩家输入证明" in messages[0]["content"]
                assert "只为这个可见结果标 evidence_source=performance" in messages[0]["content"]
            previous = outline["mainline_chapters"][index - 1]
            expected = None if index == 0 else {
                # The old entry cannot reset the next opening or poses.
                "chapter": {"title": previous["title"], "entry_prerequisites": [], "completion_facts": [
                    fact for fact in previous["completion_facts"]
                    if fact["id"] in previous["exit_plan"]["trigger_fact_ids"]],
                    "exit_plan": previous["exit_plan"]},
                "relationship_stage": outline["relationship_arc"]["stages"][index - 1],
                "character_state_stage": {key: value for key, value in
                    outline["character_state_arc"]["stages"][index - 1].items()
                    if key not in ("catgirl_state", "player_state", "environment_state",
                                   "continuity_from_previous", "temporal_context", "scene_boundaries")},
            }
            assert context["previous_scene"] == expected
        elif options["operation"] == "numeric_v2_ending_generation":
            assert set(context["story_frame"]) == {"world", "story_protagonist", "player_role", "tone"}
            assert context["ending_identity"] == {key: outline["ending"][key] for key in ("type", "title", "summary")}
            last = outline["mainline_chapters"][-1]
            assert context["previous_scene"]["chapter"] == {"title": last["title"], "entry_prerequisites": [], "completion_facts": [
                fact for fact in last["completion_facts"] if fact["id"] in last["exit_plan"]["trigger_fact_ids"]]}
            assert context["previous_scene"]["character_state_stage"] == {key: value for key, value in
                outline["character_state_arc"]["stages"][-1].items()
                if key not in ("catgirl_state", "player_state", "environment_state", "continuity_from_previous", "temporal_context", "scene_boundaries")}
            assert "未明确的姿态与持物省略，不另设计画面或新道具" in messages[0]["content"]
            assert not {"catgirl_state", "player_state", "environment_state"} & context["output_contract"]["ending_stage"].keys()
        assert options["max_retries"] == 1
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(model).generate_staged(
        title="逐幕生成", setup=_generation_setup(), save_checkpoint=checkpoints.append)
    NumericV2Compiler().compile_v2_2(result["story"])
    if empty_ending_bridge:
        transition = result["story"]["nodes"][-2]["route_gates"][0]["transition_contract"]
        assert "bridge_scene_narration" not in transition
        assert transition["must_deliver"] == []
    if empty_scene_bridge:
        for node in result['story']['nodes'][:-2]:
            transition = node['route_gates'][0]['transition_contract']
            assert 'bridge_scene_narration' not in transition
            assert transition['must_deliver'] == []
    assert calls == ["numeric_v2_story_frame",
                     *(["numeric_v2_chapter_generation", "numeric_v2_opening_state_extraction"] * 3),
                     "numeric_v2_ending_generation", "numeric_v2_opening_state_extraction"]
    assert [state["completed_chapters"] for state in checkpoints] == [0, 0, 1, 1, 2, 2, 3, 3, 3]
    assert checkpoints[2]["candidate"]["mainline_chapters"][0] == outline["mainline_chapters"][0]


def test_staged_writer_can_leave_situation_for_the_required_opening_extractor():
    outline = _idea_outline(3)
    calls, checkpoints = [], []

    def model(messages, **options):
        calls.append(options['operation'])
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] == 'numeric_v2_chapter_generation':
            reply['chapter'].pop('catgirl_situation')
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model).generate_staged(title='摘录负责处境', setup=_generation_setup(),
        save_checkpoint=checkpoints.append)
    NumericV2Compiler().compile_v2_2(result['story'])
    assert calls.count('numeric_v2_chapter_generation') == 3
    assert calls.count('numeric_v2_opening_state_extraction') == 4
    assert [chapter['catgirl_situation'] for chapter in checkpoints[-1]['candidate']['mainline_chapters']] == [
        chapter['catgirl_situation'] for chapter in outline['mainline_chapters']]


def test_staged_writer_keeps_author_boundaries_after_the_frame():
    outline = _idea_outline(3)
    boundary = "实物只在玩家确认后交付。"
    writer_operations = {"numeric_v2_chapter_generation", "numeric_v2_ending_generation"}
    seen = []

    def model(messages, **options):
        operation = options["operation"]
        request = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_story_frame":
            assert request["author_setup"]["content_boundaries"] == [boundary]
        if operation in writer_operations:
            assert request["author_content_boundaries"] == [boundary]
            assert "策划与限制冲突时，以作者限制为准" in messages[0]["content"]
            assert "不抄入 scene_boundaries" in messages[0]["content"]
            assert messages[1]["content"].count(boundary) == 1
            seen.append(operation)
        return staged_reply(messages, operation, outline)

    setup = {**_generation_setup(), "content_boundaries": [boundary, boundary]}
    story = NumericV2Generator(model).generate_staged(title="保留作者边界", setup=setup)["story"]
    assert seen == ["numeric_v2_chapter_generation"] * 3 + ["numeric_v2_ending_generation"]
    for node in story["nodes"]:
        assert node["story_beat"]["content_boundaries"] == [boundary]
        assert boundary not in node["story_beat"]["must_not_happen"]
    assert story["nodes"][0]["story_beat"]["must_not_happen"] == (
        outline["character_state_arc"]["stages"][0]["scene_boundaries"]
    )


@pytest.mark.parametrize("ending", [False, True])
@pytest.mark.parametrize("fixed_entry_only", [False, True])
def test_staged_state_extractor_sees_the_opening_that_the_route_plays(ending, fixed_entry_only):
    outline = _idea_outline(3)
    source = outline["mainline_chapters"][-1 if ending else 0]
    target = outline["ending"] if ending else outline["mainline_chapters"][1]
    stage = (outline["character_state_arc"]["ending_stage"] if ending
             else outline["character_state_arc"]["stages"][1])
    source["exit_plan"]["fixed_entry_only"] = fixed_entry_only
    stage["acting_contract"]["dialogue_policy"] = "optional"
    target["opening_scene"] = "黄昏时，花店前台左墙上挂着一枚铜钟。"
    original = "正午，花店窗边放着一束白花。"
    target["fixed_narrations"] = [{
        "id": "only_entry", "text": original, "trigger": {"type": "entry"},
        "after": [], "required_before_exit": False,
    }]
    excerpts = []

    def model(messages, **options):
        operation = options["operation"]
        request = prompt_payload(messages[1]["content"])
        if operation != "numeric_v2_opening_state_extraction" or request.get("entry_narrations") != [original]:
            return staged_reply(messages, operation, outline)
        excerpts.append(request)
        if fixed_entry_only:
            assert "opening_scene" not in request
        else:
            assert request["opening_scene"] == target["opening_scene"]
        reply = {field: stage[field] for field in ("catgirl_state", "player_state", "environment_state")}
        if not ending:
            reply["catgirl_situation"] = target["catgirl_situation"]
        reply["temporal_context"] = {
            "anchor": "黄昏" if len(excerpts) == 1 else "正午",
            "relation_to_previous": "unspecified",
        }
        return json.dumps(reply, ensure_ascii=False)

    story = NumericV2Generator(model).generate_staged(title="只摘录可见入幕", setup=_generation_setup())["story"]
    NumericV2Compiler().compile_v2_2(story)
    node = story["nodes"][-1 if ending else 1]
    assert node["story_beat"]["character_state"]["temporal_context"]["anchor"] == (
        "正午" if fixed_entry_only else "黄昏"
    )
    assert len(excerpts) == (2 if fixed_entry_only else 1)
    if fixed_entry_only:
        assert excerpts[1]["issues"][0]["code"] == "temporal_anchor_without_source"


@pytest.mark.parametrize('oversized', [False, True])
def test_staged_writer_state_placeholders_are_replaced_before_validation(oversized):
    from utils.tokenize import count_tokens

    outline = _idea_outline(3)
    checkpoints = []

    def model(messages, **options):
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] in {'numeric_v2_chapter_generation', 'numeric_v2_ending_generation'}:
            stage = reply.get('character_state_stage', reply.get('ending_stage'))
            for field in ('catgirl_state', 'player_state', 'environment_state'):
                if oversized:
                    stage[field] = '以指定主体开头的状态说明' * 100
                else:
                    stage.pop(field)
            if options['operation'] == 'numeric_v2_chapter_generation' and not oversized:
                reply['chapter'].pop('catgirl_situation')
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model, token_counter=count_tokens).generate_staged(title='只摘录一次状态', setup=_generation_setup(),
        save_checkpoint=checkpoints.append)
    NumericV2Compiler().compile_v2_2(result['story'])
    states = checkpoints[-1]['candidate']['character_state_arc']
    for actual, expected in zip(states['stages'] + [states['ending_stage']],
                                outline['character_state_arc']['stages'] + [outline['character_state_arc']['ending_stage']]):
        for field in ('catgirl_state', 'player_state', 'environment_state'):
            assert actual[field] == expected[field]


@pytest.mark.parametrize('field', ['catgirl_state', 'player_state', 'environment_state'])
@pytest.mark.parametrize('oversized', [False, True])
def test_staged_extracted_state_still_requires_the_actual_subject(field, oversized):
    from utils.tokenize import count_tokens

    outline = _idea_outline(3)

    def model(messages, **options):
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] == 'numeric_v2_opening_state_extraction':
            reply[field] = reply[field] * 100 if oversized else '以指定主体开头的状态说明'
        return json.dumps(reply, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(model, token_counter=count_tokens).generate_staged(title='摘录主体不可跳过', setup=_generation_setup())
    assert caught.value.checkpoint['completed_chapters'] == 0
    assert any((issue['code'] == 'projected_prompt_field_too_large' if oversized else
                issue['code'].endswith('_subject_invalid')) for issue in caught.value.issues)


@pytest.mark.parametrize('index', [2])
def test_staged_selected_performance_result_requires_an_explicit_exit_prerequisite(index):
    outline = _idea_outline(3)
    outline['mainline_chapters'][index]['completion_facts'][0]['evidence_source'] = 'performance'
    calls = []

    def model(messages, **options):
        context = prompt_payload(messages[1]['content'])
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] == 'numeric_v2_chapter_generation':
            assert 'entry_prerequisite_fact_ids' in context['output_contract']['chapter']['exit_plan']
            assert '玩家自主行动及由玩家单方作出的决定标 evidence_source=player_input' in messages[0]['content']
            calls.append(context['chapter_index'])
            if context['chapter_index'] == index + 1 and context['issues']:
                reply['chapter']['exit_plan']['entry_prerequisite_fact_ids'] = ['letter_date_verified']
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model).generate_staged(title='实际结果先于进入', setup=_generation_setup())
    node = result['story']['nodes'][index]
    assert node['route_gates'][0]['conditions']['all'] == [
        {'type': 'fact_equals', 'key': f'scene:mainline_{index + 1:02d}:letter_date_verified', 'value': True}]
    facts = result['story']['fact_contract']['facts']
    assert facts[f'scene:mainline_{index + 1:02d}:letter_date_verified']['evidence_source'] == 'performance'
    assert 'evidence_source' not in facts['scene:mainline_01:letter_date_verified']
    assert calls.count(index + 1) == 2
    NumericV2Compiler().compile_v2_2(result['story'])


@pytest.mark.parametrize('entry_only', [False, True])
def test_staged_requires_performance_results_in_both_terminal_contracts(entry_only):
    outline = _idea_outline(3)
    last = outline['mainline_chapters'][-1]
    if entry_only:
        last['completion_facts'].append({
            'id': 'result_delivered', 'description': '参与者直接听到了完整反馈。',
            'value_type': 'bool', 'target_value': True, 'visibility': 'public', 'evidence_source': 'performance',
        })
        last['exit_plan']['entry_prerequisite_fact_ids'] = ['result_delivered']
    else:
        last['completion_facts'][0]['evidence_source'] = 'performance'
    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(lambda messages, **options: staged_reply(messages, options['operation'], outline)).generate_staged(
            title='不把待签字当已签字', setup=_generation_setup())
    assert caught.value.checkpoint['completed_chapters'] == 2
    expected = 'performance_exit_completion_required' if entry_only else 'performance_exit_prerequisite_required'
    assert any(issue['code'] == expected for issue in caught.value.issues)


def test_staged_ending_waits_for_its_authored_feedback_evidence():
    from dataclasses import replace
    from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening

    outline = _idea_outline(3)
    last = outline["mainline_chapters"][-1]
    last["completion_facts"].append({
        "id": "feedback_delivered", "description": "见证者亲口给出了核验反馈。",
        "value_type": "bool", "target_value": True, "visibility": "public", "evidence_source": "performance",
    })
    last["exit_plan"]["trigger_fact_ids"].append("feedback_delivered")
    last["exit_plan"]["entry_prerequisite_fact_ids"] = ["feedback_delivered"]
    generated = NumericV2Generator(lambda messages, **options: staged_reply(messages, options["operation"], outline)).generate_staged(
        title="等现场反馈再收束", setup=_generation_setup())
    engine = NumericV2Engine.from_mapping(generated["story"])
    session = replace(engine.create_session(session_id="feedback", catgirl_binding=_binding(),
                      opening_performance=_opening()), current_node_id="mainline_03")
    blocked = engine.resolve_turn(session, TurnRequestV2("end", 0, "我们意见一致。"), (),
                                  scene_complete=True, natural_ending_ready=True)
    assert blocked.route is None
    ready = engine.resolve_turn(session, TurnRequestV2("feedback", 0, "请说说核验结果。"), (),
        fact_operations=({"op": "set", "key": "scene:mainline_03:feedback_delivered",
                          "value": True, "visibility": "public"},)).session
    accepted = engine.resolve_turn(ready, TurnRequestV2("end", 1, "我们意见一致。"), (),
                                   scene_complete=True, natural_ending_ready=True)
    assert accepted.session.status == "ended"


@pytest.mark.parametrize('explicit_prerequisite', [False, True])
def test_staged_ordinary_performance_completion_does_not_force_an_entry_lock(explicit_prerequisite):
    outline = _idea_outline(3)
    chapter = outline['mainline_chapters'][0]
    chapter['completion_facts'][0]['evidence_source'] = 'performance'
    if explicit_prerequisite:
        chapter['exit_plan']['entry_prerequisite_fact_ids'] = ['letter_date_verified']
    result = NumericV2Generator(lambda messages, **options: staged_reply(messages, options['operation'], outline)).generate_staged(
        title='收束条件与必要许可分开', setup=_generation_setup())
    node = result['story']['nodes'][0]
    assert node['completion_contract']['all'] == [
        {'key': 'scene:mainline_01:letter_date_verified', 'equals': True}]
    assert node['route_gates'][0]['conditions']['all'] == ([
        {'type': 'fact_equals', 'key': 'scene:mainline_01:letter_date_verified', 'value': True}]
        if explicit_prerequisite else [])
    NumericV2Compiler().compile_v2_2(result['story'])


def test_staged_optional_performance_result_does_not_become_an_exit_prerequisite():
    outline = _idea_outline(3)
    outline['mainline_chapters'][0]['completion_facts'].append({
        'id': 'optional_reply', 'description': '女主另外表达了个人感受。',
        'value_type': 'bool', 'target_value': True, 'visibility': 'public', 'evidence_source': 'performance'})
    result = NumericV2Generator(lambda messages, **options: staged_reply(messages, options['operation'], outline)).generate_staged(
        title='可选反应不锁出口', setup=_generation_setup())
    assert result['story']['nodes'][0]['route_gates'][0]['conditions']['all'] == []


def test_staged_oversized_narrative_is_repaired_before_state_extraction():
    from utils.tokenize import count_tokens

    outline = _idea_outline(3)
    calls = []
    def model(messages, **options):
        context = prompt_payload(messages[1]['content'])
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] == 'numeric_v2_chapter_generation' and context['chapter_index'] == 2:
            calls.append(context['issues'])
            if len(calls) == 1:
                reply['chapter']['narrative'] = '详细剧情' * 400
            else:
                assert any(issue['code'] == 'projected_prompt_field_too_large'
                    and issue['path'] == 'chapter.narrative' for issue in context['issues'])
        return json.dumps(reply, ensure_ascii=False)
    result = NumericV2Generator(model, token_counter=count_tokens).generate_staged(
        title='正文容量先校验', setup=_generation_setup())
    assert len(calls) == 2
    assert result['story']['nodes'][1]['story_beat']['summary'] == outline['mainline_chapters'][1]['narrative']
    NumericV2Compiler().compile_v2_2(result['story'])


@pytest.mark.parametrize('ending', [False, True])
def test_state_extraction_reads_actual_entry_pieces_without_future_condition_text(ending):
    outline = _idea_outline(3)
    scene = outline['ending'] if ending else outline['mainline_chapters'][0]
    scene['opening_scene'] += '花店门边多了一盏铜钟。'
    entries = ['午后，门外雨声渐止。', '环境中旧信仍平铺桌面。']
    scene['fixed_narrations'] = [
        {'id': 'weather', 'text': entries[0], 'trigger': {'type': 'entry'},
         'after': [], 'required_before_exit': False},
        {'id': 'letter', 'text': entries[1], 'trigger': {'type': 'entry'},
         'after': ['weather'], 'required_before_exit': False}]
    if not ending:
        scene['fixed_narrations'].append({'id': 'future', 'text': '尚未打开的信中藏有未来答案。',
            'trigger': {'type': 'condition', 'condition': '玩家主动打开密封附页'},
            'after': [], 'required_before_exit': False})
    extracted = []
    rejected_conditions = []
    def model(messages, **options):
        context = prompt_payload(messages[1]['content'])
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if (not ending and options['operation'] == 'numeric_v2_chapter_generation'
                and context['chapter_index'] == 1 and context['issues']):
            assert context['issues'][0]['code'] == 'author_passage_condition_unsupported'
            rejected_conditions.append(True)
            reply['chapter']['fixed_narrations'].pop()
        if (options['operation'] == 'numeric_v2_opening_state_extraction'
                and context['opening_scene'] == scene['opening_scene']):
            extracted.append(context['entry_narrations'])
            assert context['entry_narrations'] == entries
            reply['environment_state'] = '环境为午后的花店，旧信在桌面上。'
            reply['temporal_context'] = {'anchor': '午后', 'relation_to_previous': 'unspecified'}
        return json.dumps(reply, ensure_ascii=False)
    result = NumericV2Generator(model).generate_staged(title='原文与开场共同收笔', setup=_generation_setup())
    node = result['story']['nodes'][-1 if ending else 0]
    assert extracted == [entries]
    assert rejected_conditions == ([] if ending else [True])
    assert node['story_beat']['character_state']['environment_state'] == '环境为午后的花店，旧信在桌面上。'
    assert node['story_beat']['character_state']['temporal_context']['anchor'] == '午后'
    NumericV2Compiler().compile_v2_2(result['story'])


def test_staged_bad_prerequisite_repair_receives_current_scene_allowed_ids():
    outline = _idea_outline(3)
    calls = []
    def model(messages, **options):
        context = prompt_payload(messages[1]['content'])
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] == 'numeric_v2_chapter_generation' and context['chapter_index'] == 3:
            calls.append(context['issues'])
            if len(calls) == 1:
                reply['chapter']['exit_plan']['entry_prerequisite_fact_ids'] = ['earlier_scene_fact']
            else:
                issue = next(item for item in context['issues'] if item['code'] == 'entry_prerequisite_invalid')
                assert '本幕可引用的typed事实ID' in issue['message']
                assert 'letter_date_verified' in issue['message']
                reply['chapter']['exit_plan']['entry_prerequisite_fact_ids'] = ['letter_date_verified']
        return json.dumps(reply, ensure_ascii=False)
    result = NumericV2Generator(model).generate_staged(title='不猜前提引用', setup=_generation_setup())
    assert len(calls) == 2
    assert result['story']['nodes'][2]['route_gates'][0]['conditions']['all'][0]['key'] == 'scene:mainline_03:letter_date_verified'


@pytest.mark.parametrize('missing', [False, True])
def test_staged_situation_is_still_mandatory_after_extraction(missing):
    outline = _idea_outline(3)

    def model(messages, **options):
        reply = json.loads(staged_reply(messages, options['operation'], outline))
        if options['operation'] == 'numeric_v2_chapter_generation':
            reply['chapter'].pop('catgirl_situation')
        elif options['operation'] == 'numeric_v2_opening_state_extraction':
            if missing:
                reply.pop('catgirl_situation')
            else:
                reply['catgirl_situation'] = ''
        return json.dumps(reply, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(model).generate_staged(title='不补造处境', setup=_generation_setup())
    assert caught.value.checkpoint['completed_chapters'] == 0
    assert 'catgirl_situation' not in caught.value.checkpoint['written_scene']['chapter']
    assert any(issue['code'] in {'expected_state_strings', 'required_text'} for issue in caught.value.issues)


@pytest.mark.parametrize("reported_issue", [False, True])
@pytest.mark.parametrize("legacy_frame", [False, True])
def test_failed_scene_resume_keeps_frame_and_completed_scene_without_model_replay(reported_issue, legacy_frame):
    outline = _idea_outline(3)
    planned = "首幕先确认双方是否愿意听真相。"
    checkpoints = []
    original_frames = []

    def fail_second(messages, **options):
        operation = options["operation"]
        context = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_chapter_generation" and context["chapter_index"] == 2:
            original_frames.append(context["story_frame"])
            return LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError")
        response = staged_reply(messages, operation, outline)
        if operation == "numeric_v2_story_frame":
            frame = json.loads(response)
            frame["mainline_chapters"][0]["narrative"] = planned
            response = json.dumps(frame, ensure_ascii=False)
        return response

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(fail_second).generate_staged(
            title="保留前幕", setup=_generation_setup(), save_checkpoint=checkpoints.append)
    checkpoint = caught.value.checkpoint
    assert checkpoint["completed_chapters"] == 1
    assert checkpoint["candidate"]["mainline_chapters"][0] == outline["mainline_chapters"][0]
    assert checkpoint["story_frame"] == original_frames[0]
    assert checkpoint["story_frame"]["mainline_chapters"][0]["narrative"] == planned
    if legacy_frame:
        checkpoint.pop("story_frame")
    assert caught.value.attempts == 6
    if reported_issue:
        # Semantic review feedback cannot be reproduced by the structural validator.
        checkpoint["issues"] = [{"code": "opening_timing", "path": "mainline_chapters[1].opening_scene",
                                 "message": "后续事件尚未发生，开场不能当作已完成。"}]
    before = deepcopy(checkpoint)
    calls = []

    def resume(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append((options["operation"], context.get("chapter_index")))
        if options["operation"] == "numeric_v2_chapter_generation" and context["chapter_index"] == 2:
            expected_frame = deepcopy(original_frames[0])
            if legacy_frame:
                expected_frame["mainline_chapters"][0]["narrative"] = outline["mainline_chapters"][0]["narrative"]
            assert context["story_frame"] == expected_frame
            assert context["issues"] == ([{
                **checkpoint["issues"][0], "path": "chapter.opening_scene",
                "source_path": "mainline_chapters[1].opening_scene",
            }] if reported_issue else [])
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(resume).generate_staged(
        title="保留前幕", setup=_generation_setup(), checkpoint=checkpoint)
    assert checkpoint == before
    assert calls == [("numeric_v2_chapter_generation", 2), ("numeric_v2_opening_state_extraction", None),
                     ("numeric_v2_chapter_generation", 3), ("numeric_v2_opening_state_extraction", None),
                     ("numeric_v2_ending_generation", None), ("numeric_v2_opening_state_extraction", None)]
    NumericV2Compiler().compile_v2_2(result["story"])


def test_staged_current_scene_repair_does_not_change_prior_scene():
    outline = _idea_outline(3)
    calls = []

    def model(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append((options["operation"], context.get("chapter_index")))
        raw = staged_reply(messages, options["operation"], outline)
        if calls.count(("numeric_v2_chapter_generation", 2)) == 1 and context.get("chapter_index") == 2:
            broken = json.loads(raw)
            broken["character_state_stage"]["chapter_index"] = 9
            return json.dumps(broken, ensure_ascii=False)
        if options["operation"] == "numeric_v2_chapter_generation" and context["chapter_index"] == 2:
            issue = next(issue for issue in context["issues"]
                         if issue["source_path"] == "character_state_arc.stages[1].chapter_index")
            assert issue["path"] == "character_state_stage.chapter_index"
        return raw

    result = NumericV2Generator(model).generate_staged(title="局部修订", setup=_generation_setup())
    assert calls.count(("numeric_v2_chapter_generation", 1)) == 1
    assert calls.count(("numeric_v2_chapter_generation", 2)) == 2
    assert calls.count(("numeric_v2_chapter_generation", 3)) == 1
    NumericV2Compiler().compile_v2_2(result["story"])


@pytest.mark.parametrize("legacy_excerpt", [False, True])
def test_failed_state_extraction_resumes_without_rewriting_the_written_scene(legacy_excerpt):
    outline = _idea_outline(3)
    for index, chapter in enumerate(outline["mainline_chapters"]):
        chapter["opening_scene"] += f"这是第{index + 1}幕的场景。"
    opening = outline["mainline_chapters"][1]["opening_scene"]

    def fail_state(messages, **options):
        context = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_opening_state_extraction":
            # The extractor sees the current opening, never prior-state or whole-plot anchors.
            assert set(context) == {"opening_scene", "entry_bridge", "cast_names", "output_contract", "current_candidate", "issues"}
            if context["opening_scene"] == opening:
                return LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError")
        return staged_reply(messages, options["operation"], outline)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(fail_state).generate_staged(title="状态摘录续写", setup=_generation_setup())
    checkpoint = caught.value.checkpoint
    assert checkpoint["completed_chapters"] == 1
    assert checkpoint["written_scene"]["chapter"] == outline["mainline_chapters"][1]
    if legacy_excerpt:
        checkpoint["phase_candidate"] = {key: outline["character_state_arc"]["stages"][1][key]
                                         for key in ("catgirl_state", "player_state", "environment_state")}
    before = deepcopy(checkpoint)
    calls = []

    def resume(messages, **options):
        calls.append(options["operation"])
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(resume).generate_staged(
        title="状态摘录续写", setup=_generation_setup(), checkpoint=checkpoint)
    assert checkpoint == before
    assert calls == ["numeric_v2_opening_state_extraction", "numeric_v2_chapter_generation",
                     "numeric_v2_opening_state_extraction", "numeric_v2_ending_generation",
                     "numeric_v2_opening_state_extraction"]
    NumericV2Compiler().compile_v2_2(result["story"])


@pytest.mark.parametrize("issue", ["fixed_narration_echo_in_opening", "author_passage_condition_unsupported"])
def test_resuming_legacy_written_scene_repairs_unsupported_original_before_extraction(issue):
    outline = _idea_outline(3)
    original = "铭牌标明：编号 FQ-19。"
    second = outline["mainline_chapters"][1]
    second["opening_scene"] += "这是第二幕。"
    second["fixed_narrations"] = [{
        "id": "plaque", "text": original, "trigger": {"type": "entry"}, "after": [],
        "required_before_exit": False,
    }]
    opening = second["opening_scene"]

    def fail_state(messages, **options):
        context = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_opening_state_extraction" and context["opening_scene"] == opening:
            return LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError")
        return staged_reply(messages, options["operation"], outline)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(fail_state).generate_staged(title="续写旧检查点", setup=_generation_setup())
    checkpoint = caught.value.checkpoint
    if issue == "fixed_narration_echo_in_opening":
        checkpoint["written_scene"]["chapter"]["opening_scene"] += original
    else:
        checkpoint["written_scene"]["chapter"]["fixed_narrations"][0]["trigger"] = {
            "type": "condition", "condition": "玩家触碰铭牌。",
        }
    calls = []

    def resume(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append(options["operation"])
        if len(calls) == 1:
            assert context["chapter_index"] == 2
            assert context["issues"][0]["code"] == issue
        elif options["operation"] == "numeric_v2_opening_state_extraction":
            assert original not in context["opening_scene"]
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(resume).generate_staged(
        title="续写旧检查点", setup=_generation_setup(), checkpoint=checkpoint)
    assert calls[:2] == ["numeric_v2_chapter_generation", "numeric_v2_opening_state_extraction"]
    compiled = NumericV2Compiler().compile_v2_2(result["story"])
    beat = compiled.story["nodes"][1]["story_beat"]
    assert original not in beat["opening_scene"]
    assert beat["fixed_narrations"][0]["text"] == original


def test_invented_time_anchor_only_retries_the_current_excerpt():
    outline = _idea_outline(3)
    calls = []

    def model(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append(options["operation"])
        reply = json.loads(staged_reply(messages, options["operation"], outline))
        if options["operation"] == "numeric_v2_opening_state_extraction":
            if calls.count(options["operation"]) == 1:
                reply["temporal_context"] = {"anchor": "次日清晨", "relation_to_previous": "next_day"}
            elif calls.count(options["operation"]) == 2:
                assert any(issue["code"] == "temporal_anchor_without_source" for issue in context["issues"])
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model).generate_staged(title="时间依据", setup=_generation_setup())
    assert calls.count("numeric_v2_chapter_generation") == 3
    assert calls.count("numeric_v2_opening_state_extraction") == 5
    assert "temporal_context" not in result["story"]["nodes"][0]["story_beat"]["character_state"]



@pytest.mark.parametrize("unknown_time", [False, True])
def test_opening_excerpt_replaces_writer_situation_and_carries_explicit_time(unknown_time):
    outline = _idea_outline(3)
    outline["mainline_chapters"][0]["opening_scene"] = "午后，女主看着封好的信，男主尚未作出决定。"
    good = "女主对信中未确定的事情仍有顾虑，等待当下交流。"
    calls = []

    def model(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append(options["operation"])
        reply = json.loads(staged_reply(messages, options["operation"], outline))
        if options["operation"] == "numeric_v2_chapter_generation" and context["chapter_index"] == 1:
            reply["chapter"]["catgirl_situation"] = "女主已经收好信，男主已经答应。"
            reply["character_state_stage"]["temporal_context"] = {"anchor": "", "relation_to_previous": "unknown-writer-plan"}
        if options["operation"] == "numeric_v2_opening_state_extraction" and context["opening_scene"] == outline["mainline_chapters"][0]["opening_scene"]:
            reply["catgirl_situation"] = good
            reply["temporal_context"] = {"anchor": "" if unknown_time else "午后", "relation_to_previous": "unspecified"}
        return json.dumps(reply, ensure_ascii=False)

    result = NumericV2Generator(model).generate_staged(title="统一当前处境", setup=_generation_setup())
    node = result["story"]["nodes"][0]
    assert good in node["story_beat"]["catgirl_situation"]
    assert "已经答应" not in node["story_beat"]["catgirl_situation"]
    if unknown_time:
        assert "temporal_context" not in node["story_beat"]["character_state"]
    else:
        assert node["story_beat"]["character_state"]["temporal_context"] == {"anchor": "午后", "relation_to_previous": "unspecified"}
    assert len(calls) == 9
    NumericV2Compiler().compile_v2_2(result["story"])


def test_extractor_unfixable_situation_budget_resumes_from_the_scene_writer():
    from utils.tokenize import count_tokens

    outline = _idea_outline(3)
    oversized = deepcopy(outline)
    # Writer-owned relationship facts alone fill the combined situation budget;
    # the extractor can only edit the three states and the situation.
    oversized["relationship_arc"]["stages"][1]["known_player_facts"][0] += " a" * 450

    def first(messages, **options):
        return staged_reply(messages, options["operation"], oversized)

    with pytest.raises(NumericV2GenerationError) as caught:
        NumericV2Generator(first, token_counter=count_tokens).generate_staged(
            title="组合处境超限", setup=_generation_setup())
    checkpoint = caught.value.checkpoint
    assert checkpoint["completed_chapters"] == 1
    assert "written_scene" not in checkpoint
    assert checkpoint["phase_candidate"]["relationship_stage"] == oversized["relationship_arc"]["stages"][1]
    assert any(issue["code"] == "projected_prompt_field_too_large"
               and issue["path"] == "relationship_arc.stages[1].known_player_facts[0]"
               for issue in checkpoint["issues"])
    calls = []

    def resume(messages, **options):
        context = prompt_payload(messages[1]["content"])
        calls.append(options["operation"])
        if options["operation"] == "numeric_v2_chapter_generation" and context["chapter_index"] == 2:
            assert any(issue["code"] == "projected_prompt_field_too_large"
                       and issue["path"] == "relationship_stage.known_player_facts[0]" for issue in context["issues"])
        return staged_reply(messages, options["operation"], outline)

    result = NumericV2Generator(resume, token_counter=count_tokens).generate_staged(
        title="组合处境超限", setup=_generation_setup(), checkpoint=checkpoint)
    assert calls[:2] == ["numeric_v2_chapter_generation", "numeric_v2_opening_state_extraction"]
    NumericV2Compiler().compile_v2_2(result["story"])


def test_next_scene_does_not_receive_previous_scene_local_boundaries():
    outline = _idea_outline(3)
    for index, stage in enumerate(outline["character_state_arc"]["stages"], 1):
        stage["scene_boundaries"] = [f"不得在第{index}幕提前展示下一幕的证物。"]
    original = deepcopy(outline)
    persistent = "禁止向公众公开个人档案。"
    inspected = []

    def model(messages, **options):
        context = prompt_payload(messages[1]["content"])
        operation = options["operation"]
        if operation in {"numeric_v2_chapter_generation", "numeric_v2_ending_generation"}:
            assert context["author_content_boundaries"] == [persistent]
            previous = context["previous_scene"]
            if previous is not None:
                state = previous["character_state_stage"]
                assert "scene_boundaries" not in state
                index = context.get("chapter_index", 4) - 2
                assert state["acting_contract"] == original["character_state_arc"]["stages"][index]["acting_contract"]
                inspected.append(operation)
        return staged_reply(messages, operation, outline)

    result = NumericV2Generator(model).generate_staged(
        title="幕内限制作用域", setup={**_generation_setup(), "content_boundaries": [persistent]})
    assert inspected == ["numeric_v2_chapter_generation", "numeric_v2_chapter_generation", "numeric_v2_ending_generation"]
    assert outline == original
    for index, node in enumerate(result["story"]["nodes"][:3]):
        assert original["character_state_arc"]["stages"][index]["scene_boundaries"][0] in node["story_beat"]["must_not_happen"]
    NumericV2Compiler().compile_v2_2(result["story"])


def _without_invitation(checkpoint):
    """A checkpoint saved before world.invitation existed."""
    legacy = deepcopy(checkpoint)
    legacy.pop("checkpoint_version", None)
    for holder in (legacy.get("candidate"), legacy.get("story_frame"), legacy.get("phase_candidate")):
        if isinstance(holder, dict) and isinstance(holder.get("world"), dict):
            holder["world"].pop("invitation", None)
    return legacy


@pytest.mark.parametrize("stage_index", [2, -1], ids=["mid_scenes", "complete"])
def test_checkpoint_saved_before_invitation_resumes_and_exports_without_it(stage_index):
    outline, checkpoints, calls = _idea_outline(3), [], []

    def model(messages, **options):
        op = options["operation"]
        calls.append((op, prompt_payload(messages[1]["content"]).get("issues", [])))
        return staged_reply(messages, op, outline)

    generator = NumericV2Generator(model)
    original = generator.generate_staged(title="旧存档", setup=_generation_setup(),
                                         save_checkpoint=checkpoints.append)["story"]
    legacy = _without_invitation(checkpoints[stage_index])
    calls.clear()
    story = generator.generate_staged(title="旧存档", setup=_generation_setup(), checkpoint=legacy)["story"]

    NumericV2Compiler().compile_v2_2(story)
    assert "invitation" not in story["intro"]
    assert {key: value for key, value in original["intro"].items() if key != "invitation"} == story["intro"]
    # Scene and ending phases cannot write world fields, so they are never asked to.
    assert "numeric_v2_story_frame" not in [op for op, _ in calls]
    assert not any(issue.get("path") == "world.invitation" for _, issues in calls for issue in issues)


def test_new_frame_without_invitation_is_sent_back_for_it():
    outline, issues_seen = _idea_outline(3), []
    frames = []

    def model(messages, **options):
        op = options["operation"]
        if op == "numeric_v2_story_frame":
            frames.append(prompt_payload(messages[1]["content"]).get("issues", []))
            reply = json.loads(staged_reply(messages, op, outline))
            if len(frames) == 1:
                reply["world"].pop("invitation")
            return json.dumps(reply, ensure_ascii=False)
        return staged_reply(messages, op, outline)

    story = NumericV2Generator(model).generate_staged(title="新稿", setup=_generation_setup())["story"]
    assert len(frames) == 2
    assert any(issue.get("path") == "world.invitation" for issue in frames[1])
    assert story["intro"]["invitation"] == outline["world"]["invitation"]


@pytest.mark.parametrize("stage_index", [2, -1])
def test_versioned_checkpoint_missing_invitation_cannot_claim_legacy_compatibility(stage_index):
    outline, checkpoints = _idea_outline(3), []
    generator = NumericV2Generator(lambda messages, **options: staged_reply(messages, options["operation"], outline))
    generator.generate_staged(title="新存档", setup=_generation_setup(), save_checkpoint=checkpoints.append)
    checkpoint = _without_invitation(checkpoints[stage_index])
    checkpoint["checkpoint_version"] = checkpoints[stage_index]["checkpoint_version"]
    assert checkpoint["checkpoint_version"] == 1
    with pytest.raises(NumericV2GenerationError) as error:
        generator.generate_staged(title="新存档", setup=_generation_setup(), checkpoint=checkpoint)
    assert any(issue.get("path") == "world.invitation" for issue in error.value.issues)


@pytest.mark.parametrize("invitation", [None, "", "   ", [], {}])
def test_existing_invalid_checkpoint_invitation_is_not_treated_as_a_missing_legacy_field(invitation):
    outline, checkpoints = _idea_outline(3), []
    generator = NumericV2Generator(lambda messages, **options: staged_reply(messages, options["operation"], outline))
    generator.generate_staged(title="旧存档", setup=_generation_setup(), save_checkpoint=checkpoints.append)
    checkpoint = _without_invitation(checkpoints[2])
    checkpoint["candidate"]["world"]["invitation"] = invitation

    with pytest.raises(NumericV2GenerationError) as error:
        generator.generate_staged(title="旧存档", setup=_generation_setup(), checkpoint=checkpoint)
    assert any(issue.get("path") == "world.invitation" and issue.get("code") == "required_text"
               for issue in error.value.issues)

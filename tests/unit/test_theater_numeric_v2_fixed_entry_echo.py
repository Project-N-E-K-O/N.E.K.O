"""An exact model echo must not play twice beside the immutable entry original."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_actor import _prompt_container, _story_so_far_row_text
from services.theater.numeric_v2_archive import _performance_memory_parts
from services.theater.numeric_v2_performance import content_blocks, performance_content_blocks
from tests.unit.test_theater_review_round2_frontend import RUNTIME_HARNESS, _run


# Frozen bread R12: the generated target narration equalled the inserted entry text.
FORM = "“旧街区夏日祭”活动报名表：需要一家甜点供应商提供50份试吃品，以及两名志愿者负责下午两小时的现场引导。"
BODY = "（拿起桌上的报名表）这表格来得突然。（抬眼看向你）你可以先看看时间安排。"


def _target():
    return {"phase": "target_opening", "scene_narration": FORM, "performance": BODY,
            "fixed_narrations": [{"node_id": "community", "id": "registration", "text": FORM,
                                   "bindings": {"catgirl_name": "猫娘", "player_name": "玩家"},
                                   "position": "before"}]}


def _transition():
    return {"transition_delivered": True, "visible_node_id": "community", "segments": [
        {"phase": "source_response", "performance": ""},
        {"phase": "transition_bridge", "scene_narration": "张阿姨进店，把活动报名表放在柜台上。"},
        _target(),
    ]}


def test_real_r12_shape_emits_one_original_and_keeps_raw_record():
    record = _transition()
    original = deepcopy(record)
    blocks = performance_content_blocks(record)
    assert [b["text"] for b in blocks].count(FORM) == 1
    assert [i for i, b in enumerate(blocks) if b["type"] == "dialogue"] == [3, 5]
    assert record == original
    assert record["segments"][2]["fixed_narrations"] == original["segments"][2]["fixed_narrations"]


@pytest.mark.parametrize("projection", ["archive", "prompt"])
def test_archive_and_actor_history_show_one_original(projection):
    target = _target()
    original = deepcopy(target)
    if projection == "archive":
        parts, text = _performance_memory_parts(target, phase="target_opening")
        assert sum(part["text"] == FORM for part in parts) == 1
    else:
        row = _prompt_container(target, phase="target_opening")
        assert row["fixed_narration_before"] == FORM
        assert "scene_narration" not in row
        text = _story_so_far_row_text(row)
    assert text.count(FORM) == 1
    assert text.index(FORM) < text.index("拿起桌上的报名表")
    assert target == original


EQUALITY_CASES = [
    ("  原文。\n", [{"position": "before", "text": "原文。"}], ["原文。"]),
    ("原文。", [{"position": "after", "text": "原文。"}], ["原文。", "原文。"]),
    ("原文。还有新情况。", [{"position": "before", "text": "原文。"}], ["原文。还有新情况。", "原文。"]),
    ("原文。", [{"position": "before", "text": "原文！"}], ["原文。", "原文！"]),
    ("原文。", [{"position": "before", "text": "  原文。  "}], ["原文。", "  原文。  "]),
    ("原文。", [{"position": "before", "text": "第一张。"}, {"position": "before", "text": "原文。"}], ["第一张。", "原文。"]),
    ("第一行\n  第二行。", [{"position": "before", "text": "第一行\n  第二行。"},
                            {"position": "after", "text": "\n  尾注  \n"}], ["第一行\n  第二行。", "\n  尾注  \n"]),
]


@pytest.mark.parametrize("scene,fixed,expected", EQUALITY_CASES, ids=['  原文。\n-fixed1-expected1', '原文。-fixed2-expected2', '原文。还有新情况。-fixed3-expected3', '原文。-fixed4-expected4', '原文。-fixed5-expected5', '原文。-fixed6-expected6', '第一行\n  第二行。-fixed7-expected7'])
def test_only_complete_equal_before_text_suppresses_dynamic_scene(scene, fixed, expected):
    container = {"scene_narration": scene, "performance": "", "fixed_narrations": fixed}
    original = deepcopy(container)
    assert [b["text"] for b in content_blocks(container)] == expected
    assert container == original


def test_equal_dialogue_and_other_container_are_not_removed():
    first = {"phase": "source_response", "scene_narration": FORM, "performance": FORM}
    second = {"phase": "target_opening", "scene_narration": "店门开着。", "performance": "先听安排。",
              "fixed_narrations": [{"position": "before", "text": FORM}]}
    blocks = performance_content_blocks({"segments": [first, second]})
    assert [b["text"] for b in blocks].count(FORM) == 3
    assert blocks[1]["type"] == "dialogue"


def test_archive_and_prompt_keep_fixed_internal_and_outer_whitespace():
    before = "第一行\n  第二行。"
    after = "\n  尾注  \n"
    target = {"scene_narration": before, "performance": "（点头）好。",
              "fixed_narrations": [{"position": "before", "text": before},
                                   {"position": "after", "text": after}]}
    parts, _ = _performance_memory_parts(target, phase="target_opening")
    assert parts[0]["text"] == before and parts[-1]["text"] == after
    row = _prompt_container(target, phase="target_opening")
    assert row["fixed_narration_before"] == before and row["fixed_narration_after"] == after
    assert "scene_narration" not in row


def test_ordinary_and_legacy_content_keep_their_order():
    assert content_blocks({"scene_narration": "雨停了。", "performance": "（抬头）走吧。"}) == [
        {"type": "narration", "text": "雨停了。"}, {"type": "action", "text": "抬头"},
        {"type": "dialogue", "speaker_id": "active_catgirl", "text": "走吧。"},
    ]
    legacy = {"content": [{"type": "narration", "text": FORM},
                          {"type": "dialogue", "speaker_id": "active_catgirl", "text": FORM}]}
    assert content_blocks(legacy) == legacy["content"]


@pytest.mark.parametrize("mode", ["live", "restored"])
def test_real_runtime_single_display_and_speech_indexes_match_backend(mode):
    record = _transition()
    expected_indexes = [i for i, block in enumerate(performance_content_blocks(record)) if block["type"] == "dialogue"]
    setup = "const record = " + json.dumps(record, ensure_ascii=False) + ";\n"
    setup += "const expectedIndexes = " + json.dumps(expected_indexes) + ";\n"
    setup += "const form = " + json.dumps(FORM, ensure_ascii=False) + ";\n"
    if mode == "live":
        scenario = r"""
          const ctx = createContext(); await launch(ctx);
          const request = await submit(ctx, '好，听听社区的安排。');
          const response = snapshot('session_a', 5);
          response.performance = {...copy(record), revision: 5};
          response.session.performance_history = [response.performance];
          await respond(request, response);
          for (let i = 0; i < 300 && !ctx.requests.some(r => /speak-block$/.test(r.url)); i++) await tick();
          const speech = take(ctx, /speak-block$/), body = JSON.parse(speech.options.body);
          assert.deepEqual(body.dialogue_block_indexes, expectedIndexes);
          assert.deepEqual(body.dialogue_block_indexes, [3, 5]);
          assert.equal(ctx.runtime.getState().history.filter(row => row.text === form).length, 1);
          assert.equal(ctx.runtime.skipPlayback(), true);
          assert.equal(ctx.runtime.getState().history.filter(row => row.text === form).length, 1);
          await respond(speech, {ok: true, audio_queued: false});
          for (let i = 0; i < 10; i++) await tick();
          assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
        """
    else:
        scenario = r"""
          const first = createContext(); await launch(first);
          const pointer = JSON.parse(first.storage['neko.theater.numeric.v2.capsule-pointer.v1']);
          const ctx = createContext({pointer});
          const response = snapshot('session_a', 5);
          response.session.performance_history = [{...copy(record), revision: 5, input_text: '先听安排。'}];
          await respond(take(ctx, /\/session\/session_a\?/), response);
          assert.equal(ctx.runtime.getState().history.filter(row => row.text === form).length, 1);
          assert.equal(ctx.runtime.getState().history.filter(row => row.text === '先听安排。').length, 1);
          assert.equal(ctx.requests.filter(row => /speak-block$/.test(row.url)).length, 0);
        """
    _run(RUNTIME_HARNESS + setup + "\nasync function run() {\n" + scenario + "\n}\n")


def test_real_runtime_preserves_nonmatches_after_text_and_other_containers():
    cases = [
        {"record": {"scene_narration": scene, "performance": "", "fixed_narrations": fixed},
         # The existing UI trims only outer bubble whitespace; inner original spacing must survive.
         "expected": [text.strip() for text in expected]}
        for scene, fixed, expected in EQUALITY_CASES
    ]
    cases.extend([
        {"record": {"segments": [
            {"phase": "source_response", "scene_narration": FORM, "performance": ""},
            {"phase": "target_opening", "performance": "", "fixed_narrations": [{"position": "before", "text": FORM}]},
        ]}, "expected": [FORM, FORM]},
        {"record": {"content": [{"type": "narration", "text": "旧版旁白。"},
                                  {"type": "dialogue", "speaker_id": "active_catgirl", "text": "旧版对白。"}]},
         "expected": ["（旧版旁白。）\n旧版对白。"]},
    ])
    setup = "const cases = " + json.dumps(cases, ensure_ascii=False) + ";\n"
    scenario = r"""
      for (const item of cases) {
        const ctx = createContext(); sendLaunch(ctx);
        const response = snapshot();
        response.session.opening_performance = {performance: ''};
        response.session.performance_history = [{...copy(item.record), revision: 4}];
        await respond(take(ctx, /\/session\/session_a/), response);
        await claimLaunch(ctx, response);
        assert.deepEqual(copy(ctx.runtime.getState().history.map(row => row.text)), item.expected);
        ctx.runtime.clear('finished');
      }
    """
    _run(RUNTIME_HARNESS + setup + "\nasync function run() {\n" + scenario + "\n}\n")


@pytest.mark.asyncio
async def test_equal_entry_keeps_nonempty_raw_contract_through_commit_restore_and_fork(tmp_path):
    from services.theater.numeric_v2_actor_output import _parse_output
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_fixed_narration import _piece, OPENING
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
    from tests.unit.test_theater_numeric_v2_runtime import _binding
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    story = numeric_v2_story()
    for node in story["nodes"]:
        node["story_beat"]["fixed_narrations"] = [_piece("entry", FORM, entry=True)]
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    opening = {**OPENING, "scene_narration": FORM}
    current = await runtime.start_session(session_id="entry-echo", catgirl_binding=_binding(), opening_performance=opening)
    assert current.session.opening_performance["scene_narration"] == FORM
    assert [b["text"] for b in performance_content_blocks(current.session.opening_performance)].count(FORM) == 1
    outcome = runtime.prepare_turn(current, TurnRequestV2("finish", 0, "就到这里吧。"), (),
                                   scene_complete=True, natural_ending_ready=True)
    candidate = {**_candidate(), "target_scene_narration": FORM}
    parsed = _parse_output(json.dumps(candidate), transition_required=True)
    performance = runtime.engine.finalize_transition_performance(outcome, parsed, target_opening=FORM)
    assert performance["segments"][2]["scene_narration"] == FORM
    assert [b["text"] for b in performance_content_blocks(performance)].count(FORM) == 1
    saved = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session("entry-echo") == saved
    fork = await runtime.fork_session_for_test("entry-echo", session_id="echo-fork", through_revision=1)
    assert await runtime.restore_session("echo-fork") == fork
    assert fork.session.performance_history[0]["segments"] == performance["segments"]

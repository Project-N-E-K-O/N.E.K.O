"""Ending labels show titles while committed terminal and archived bodies keep their playback paths."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.unit.test_theater_review_round2_frontend import RUNTIME_HARNESS, _run
from tests.unit.test_theater_selector_locale import SCRIPT as SELECTOR_SCRIPT


ENDING_SETUP = r"""
const finalNarration = '窗外渐渐亮了，两人仍坐在原来的桌边。';
const finalDialogue = '我会记得今晚。';
const authorSummary = '作者预写的结局摘要，不代表本次实际演出。';
function endedSnapshot(title = '天亮以后') {
  const response = snapshot('session_a', 5, 'ended');
  const record = {revision: 5, input_text: '再坐一会儿吧。',
    scene_narration: finalNarration, performance: finalDialogue, suggested_inputs: []};
  response.session.performance_history = [record];
  response.performance = record;
  response.scene = {terminal: true, summary: '终幕的原有剧情方向。',
    ending: {title, summary: authorSummary}};
  return response;
}
function assertEnding(ctx, title) {
  const state = ctx.runtime.getState();
  assert.equal(state.phase, 'ended');
  assert.deepEqual(copy(state.history.filter(row => row.type === 'ending').map(row => row.text)), [title]);
  assert.ok(state.history.some(row => row.text === finalNarration));
  assert.ok(state.history.some(row => row.text === finalDialogue));
  assert.equal(state.scene.ending.summary, authorSummary, '显示收窄不能删除 API 快照中的旧摘要');
  assert.equal(state.scene.summary, '终幕的原有剧情方向。');
  assert.equal(ctx.views.at(-1).composerDisabled, true);
}
async function beginEnding(ctx) {
  await launch(ctx);
  const request = await submit(ctx, '再坐一会儿吧。');
  const response = endedSnapshot();
  const original = JSON.stringify(response.scene);
  await respond(request, response);
  for (let i = 0; i < 100 && !ctx.requests.some(r => /\/speak-block$/.test(r.url)); i++) await tick();
  assert.equal(JSON.stringify(response.scene), original);
  return take(ctx, /\/session\/speak-block$/);
}
"""


RUNTIME_SCENARIOS = (
    ("live_ending_waits_for_committed_performance_then_shows_title", r"""
      const ctx = createContext();
      const speech = await beginEnding(ctx);
      const speechId = JSON.parse(speech.options.body).playback_request_id;
      assert.equal(ctx.runtime.getState().history.filter(row => row.type === 'ending').length, 0);
      await respond(speech, {ok: true, audio_queued: true, speech_id: speechId});
      ctx.emit('neko-assistant-speech-end', {turnId: speechId});
      for (let i = 0; i < 100 && ctx.runtime.getState().phase !== 'ended'; i++) await tick();
      assertEnding(ctx, '天亮以后');
      assert.equal(ctx.requests.filter(r => /\/speak-block$/.test(r.url)).length, 0);
    """),
    ("skip_ending_and_late_audio_keep_one_title_and_full_body", r"""
      const ctx = createContext();
      const speech = await beginEnding(ctx);
      const speechId = JSON.parse(speech.options.body).playback_request_id;
      assert.equal(ctx.runtime.skipPlayback(), true);
      assertEnding(ctx, '天亮以后');
      await respond(speech, {ok: true, audio_queued: true, speech_id: speechId});
      ctx.emit('neko-assistant-speech-end', {turnId: speechId});
      for (let i = 0; i < 20; i++) await tick();
      assertEnding(ctx, '天亮以后');
    """),
    ("restored_ending_keeps_body_without_replaying_tts", r"""
      const ctx = createContext();
      const response = endedSnapshot();
      sendLaunch(ctx, 'session_a', 5);
      await respond(take(ctx, /\/session\/session_a/), response);
      await claimLaunch(ctx, response);
      assertEnding(ctx, '天亮以后');
      assert.equal(ctx.requests.filter(r => /\/speak-block$/.test(r.url)).length, 0);
    """),
    ("empty_title_uses_existing_localized_status_not_summary", r"""
      const ctx = createContext();
      ctx.window.t = key => key === 'theater.ended' ? 'Completed' : key;
      const response = endedSnapshot('   ');
      sendLaunch(ctx, 'session_a', 5);
      await respond(take(ctx, /\/session\/session_a/), response);
      await claimLaunch(ctx, response);
      assertEnding(ctx, 'Completed');
    """),
    ("nonterminal_narration_equal_to_summary_is_not_removed", r"""
      const ctx = createContext();
      const response = endedSnapshot();
      response.session.status = 'active';
      response.scene.terminal = false;
      response.scene.ending = null;
      response.session.performance_history[0].scene_narration = authorSummary;
      sendLaunch(ctx, 'session_a', 5);
      await respond(take(ctx, /\/session\/session_a/), response);
      await claimLaunch(ctx, response);
      assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
      assert.ok(ctx.runtime.getState().history.some(row => row.text === authorSummary));
      assert.equal(ctx.runtime.getState().history.filter(row => row.type === 'ending').length, 0);
    """),
)


@pytest.mark.parametrize("_name,scenario", RUNTIME_SCENARIOS, ids=[case[0] for case in RUNTIME_SCENARIOS])
def test_ending_title_runtime(_name, scenario):
    _run(RUNTIME_HARNESS + ENDING_SETUP + "\nasync function run() {\n" + scenario + "\n}\n")


@pytest.mark.parametrize("legacy", [False, True], ids=["structured_parts", "legacy_performance"])
@pytest.mark.parametrize("title", ["天亮以后", ""])
def test_archive_view_keeps_actual_body_and_hides_only_ending_summary(legacy, title):
    """Exercise the real archive button and response without replacing production formatters."""
    archive = {
        "session_id": "old_session", "story_title": "测试剧本",
        "player_name": "玩家", "catgirl_name": "猫娘",
        "ending": {"title": title, "summary": "只属于作者的结局摘要。"},
        "opening": {"performance": "真实开场。"},
        "turns": [{
            "revision": 1, "player_input": "再坐一会儿吧。",
            "performance": "真实终幕对白。",
            **({} if legacy else {"parts": [
                {"kind": "narration", "text": "真实终幕旁白。"},
                {"kind": "dialogue", "text": "真实终幕对白。"},
            ]}),
        }],
    }
    harness = SELECTOR_SCRIPT.split("async function run() {")[0]
    scenario = r"""
  FakeElement.prototype.querySelector = function (selector) { return element(this.key + ' ' + selector); };
  docListeners.DOMContentLoaded();
  await respond(/\/stories$/, {ok: true, character_id: 'cat_a',
    stories: [{story_id: 'story_a', title: '测试剧本', revision: 2, display_intro: {}}]});
  await respond(/\/session\/active\?/, {ok: true, session: null});
  await respond(/\/memory\/archives\?/, {ok: true, archives: [
    {session_id: 'old_session', revision: 1, episode_status: 'completed', pinned: false}
  ]});
  await respond(/\/memory\/stories$/, {ok: true, character_id: 'cat_a', stories: []});
  const original = JSON.stringify(archive);
  const row = element('#theater-memory-list').children[0];
  const view = row.children[1].children[0];
  assert.equal(view.dataset.theaterViewSession, 'old_session');
  view.listeners.click();
  await respond(/\/memory\/archive\?/, {ok: true, archive});
  const body = element('#theater-modal-body').textContent;
  assert.ok(body.includes('真实开场。'));
  assert.ok(body.includes('玩家：再坐一会儿吧。'));
  assert.ok(body.includes('猫娘：真实终幕对白。'));
  if (archive.turns[0].parts) assert.ok(body.includes('真实终幕旁白。'));
  assert.ok(!body.includes(archive.ending.summary), '归档查看器不能把作者摘要追加成实际终幕');
  if (archive.ending.title) assert.ok(body.endsWith('已完成的演绎\n' + archive.ending.title));
  assert.equal(JSON.stringify(archive), original, '旧归档摘要仍保留在原始响应中');
"""
    _run(harness + "\nconst archive = " + json.dumps(archive, ensure_ascii=False)
         + ";\nasync function run() {\n" + scenario + "\n}\n")


def _ended_session(performance):
    return SimpleNamespace(
        story_package_id="ending_projection", session_id="ending_session",
        revision=2, status="ended", ended_reason=None, catgirl_binding={},
        opening_performance={"performance": "实际开场。"},
        performance_history=(
            {"revision": 1, "performance": "此前的演出。"},
            {"revision": 2, **performance},
        ),
    )


def test_new_memory_capsule_uses_actual_refusal_and_preserves_author_metadata():
    from memory.recent import _theater_episode_capsule
    from services.theater.numeric_v2_archive import (
        build_numeric_v2_memory_messages,
        build_numeric_v2_public_archive,
    )
    from utils.llm_client import SystemMessage

    session = _ended_session({"segments": [
        {"phase": "source_response", "performance": ""},
        {"phase": "transition_bridge", "scene_narration": ""},
        {"phase": "target_opening", "scene_narration": "合同仍未签署。",
         "performance": "（合上文件）我不能接受这个条件。"},
    ]})
    ending = {"title": "各自的选择", "summary": "两人签好合同，决定从此共同生活。"}
    original = deepcopy((vars(session), ending))
    messages = build_numeric_v2_memory_messages(title="测试剧本", session=session, ending=ending)
    capsule = messages[0]
    text = capsule["content"][0]["text"]
    assert "合同仍未签署。" in text
    assert "我不能接受这个条件。" in text
    assert ending["summary"] not in text
    assert capsule["metadata"]["episode_summary"] == text
    assert capsule["metadata"]["ending_title"] == ending["title"]
    assert capsule["metadata"]["ending_summary"] == ending["summary"]
    # 正常聊天的胶囊归一入口须优先使用新的实际演出摘要，不回退到作者元数据。
    remembered = _theater_episode_capsule([SystemMessage(
        content=text, metadata=capsule["metadata"],
    )])
    assert remembered.content == text
    public = build_numeric_v2_public_archive(title="测试剧本", session=session, ending=ending)
    assert public["ending"] == ending
    assert "我不能接受这个条件。" in public["turns"][-1]["performance"]
    assert (vars(session), ending) == original


@pytest.mark.parametrize("has_empty_record", [False, True])
def test_empty_performance_cannot_create_memory_from_author_ending(has_empty_record):
    from services.theater.numeric_v2_archive import build_numeric_v2_memory_messages

    session = _ended_session({"performance": ""})
    session.opening_performance = {}
    session.performance_history = (session.performance_history[-1],) if has_empty_record else ()
    assert build_numeric_v2_memory_messages(
        title="测试剧本", session=session,
        ending={"title": "作者标题", "summary": "作者计划中的幸福生活。"},
    ) == []


def test_actual_ending_keeps_existing_compact_budget_and_full_cold_archive():
    from services.theater.numeric_v2_archive import (
        build_numeric_v2_memory_messages,
        build_numeric_v2_public_archive,
    )

    performed = "这是实际演出的长段落。" * 70
    session = _ended_session({"performance": performed})
    ending = {"title": "落幕", "summary": "不能拿这个作者摘要取代实际正文。" * 90}
    compact = build_numeric_v2_memory_messages(title="测试剧本", session=session, ending=ending)[0]
    original_budget_projection = build_numeric_v2_memory_messages(
        title="测试剧本", session=session, ending=None,
    )[0]
    assert compact["content"] == original_budget_projection["content"]
    assert len(compact["content"][0]["text"]) <= 362  # 原有 360 字 + 最多两个省略号。
    public = build_numeric_v2_public_archive(title="测试剧本", session=session, ending=ending)
    assert public["turns"][-1]["performance"] == performed
    assert public["ending"] == ending

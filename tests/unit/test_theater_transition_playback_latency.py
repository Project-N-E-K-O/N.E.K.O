"""Committed transition prose must not defer its first speech behind silent typing."""

import pytest

from tests.unit.test_theater_review_round2_frontend import RUNTIME_HARNESS, _run


SETUP = r"""
// Frozen mountain9 R4 prose: 67 bridge + 71 target narration + 27 action characters.
const mountain = {
  transition_delivered: true, suggested_inputs: ['（环顾四周）先看看棚里有没有人。'],
  segments: [
    {phase: 'source_response', performance: ''},
    {phase: 'transition_bridge', scene_narration: '窄口一带已在身后，山径在前方重新收拢。天色忽然暗下，风卷着雪扑来；两人和马匹继续沿山径前行，抵达猎户棚外附近，棚檐就在近旁，尚未入棚。'},
    {phase: 'target_opening', scene_narration: '风雪如絮，迷得人睁不开眼。马匹不安地打着响鼻，蹄铁在碎石上发出刺耳的摩擦声。Phone-local勒住缰绳，蹲身查看自己马匹的蹄铁，眉头微蹙。',
     performance: '（站起身，拍去膝上的雪沫，目光落在你马匹打滑的后蹄上）这雪太深，马掌吃不住劲。棚子就在跟前，要不要进去避避风，让马也歇口气？'},
  ]
};
function watchTyping(ctx) {
  const meter = {scheduled: 0, completed: 0, speech: []};
  const originalTimeout = ctx.window.setTimeout;
  ctx.window.setTimeout = (fn, ms) => {
    if (ms === 32) meter.scheduled++;
    return originalTimeout(() => { if (ms === 32) meter.completed++; fn(); }, ms);
  };
  const originalFetch = ctx.window.fetch;
  ctx.window.fetch = (url, options) => {
    if (/\/session\/speak-block$/.test(url)) meter.speech.push({
      completedTicks: meter.completed, body: JSON.parse(options.body),
      history: copy(ctx.runtime.getState().history),
    });
    return originalFetch(url, options);
  };
  return meter;
}
async function playCommitted(ctx, performance) {
  await launch(ctx);
  const meter = watchTyping(ctx);
  const request = await submit(ctx, '好，继续沿山径去猎户棚，到了棚外再商量。');
  const response = snapshot('session_a', 5);
  const record = {...copy(performance), revision: 5};
  response.performance = record; response.session.performance_history = [record];
  await respond(request, response);
  return meter;
}
async function awaitSpeech(ctx, meter, count = 1) {
  for (let i = 0; i < 1500 && meter.speech.length < count; i++) await tick();
  assert.equal(meter.speech.length, count, 'expected one request per dialogue group');
  return take(ctx, /\/session\/speak-block$/);
}
"""


SCENARIOS = (
    ("real_transition_has_no_silent_prefix_typing_wait", r"""
      const ctx = createContext(), meter = await playCommitted(ctx, mountain);
      await awaitSpeech(ctx, meter);
      const first = meter.speech[0];
      assert.equal(first.completedTicks, 0,
        JSON.stringify({completedTicksBeforeSpeech: first.completedTicks, delayMs: first.completedTicks * 32}));
      assert.deepEqual(first.body.dialogue_block_indexes, [3]);
      assert.ok(first.history.some(row => row.text === mountain.segments[1].scene_narration));
      assert.ok(first.history.some(row => row.text === mountain.segments[2].scene_narration));
      assert.equal(ctx.views.at(-1).composerDisabled, false);
      assert.equal(ctx.runtime.skipPlayback(), true);
    """),
    ("scene_blocks_after_source_speech_are_immediate_and_fixed_text_is_preserved", r"""
      const performance = copy(mountain);
      const fixedBefore = '[固定前]\n  原文 保留。', fixedAfter = '[固定后]\n  不可改写。';
      performance.segments[0] = {phase: 'source_response', scene_narration: '管事答道：可以通过。',
        performance: '（点头）我听清了。',
        fixed_narrations: [{position: 'before', text: fixedBefore}, {position: 'after', text: fixedAfter}]};
      performance.segments[2].fixed_narrations = [{position: 'before', text: fixedBefore}];
      const ctx = createContext(), meter = await playCommitted(ctx, performance);
      const source = await awaitSpeech(ctx, meter);
      assert.equal(meter.speech[0].completedTicks, 0);
      assert.ok(meter.speech[0].history.some(row => row.text === fixedBefore));
      const id = JSON.parse(source.options.body).playback_request_id;
      await respond(source, {ok: true, audio_queued: true, speech_id: id});
      for (let i = 0; i < 30; i++) await tick();
      assert.equal(meter.speech.length, 1, 'target voice must wait for source voice completion');
      ctx.emit('neko-assistant-speech-end', {turnId: id});
      await awaitSpeech(ctx, meter, 2);
      assert.equal(meter.speech[1].completedTicks, Array.from('我听清了。').length + 27,
        'only source dialogue and non-prefix target action keep typing delays');
      const history = meter.speech[1].history.map(row => row.text);
      assert.equal(history.filter(text => text === fixedBefore).length, 2);
      assert.equal(history.filter(text => text === fixedAfter).length, 1);
      assert.ok(history.includes(performance.segments[1].scene_narration));
      assert.ok(history.includes(performance.segments[2].scene_narration));
      assert.equal(ctx.runtime.skipPlayback(), true);
    """),
    ("ordinary_turn_retains_typing_delays", r"""
      const ctx = createContext();
      const meter = await playCommitted(ctx, {scene_narration: '风慢慢停了。', performance: '（点头）我听清了。'});
      await awaitSpeech(ctx, meter);
      assert.equal(meter.speech[0].completedTicks, Array.from('风慢慢停了。（点头）').length);
      assert.equal(ctx.runtime.skipPlayback(), true);
    """),
    ("non_boolean_transition_marker_does_not_enable_immediate_display", r"""
      const ctx = createContext(), performance = {...copy(mountain), transition_delivered: 'true'};
      const meter = await playCommitted(ctx, performance);
      await awaitSpeech(ctx, meter);
      assert.equal(meter.speech[0].completedTicks, 165);
      assert.equal(ctx.runtime.skipPlayback(), true);
    """),
    ("send_during_transition_cancels_old_voice_and_keeps_full_committed_text", r"""
      const ctx = createContext(), meter = await playCommitted(ctx, mountain);
      const speech = await awaitSpeech(ctx, meter);
      const speechId = JSON.parse(speech.options.body).playback_request_id;
      ctx.callbacks.freeform('先看一看棚内。'); await tick();
      const next = take(ctx, /\/session\/input$/);
      assert.equal(JSON.parse(next.options.body).base_revision, 5);
      assert.equal(ctx.runtime.getState().phase, 'evaluating');
      assert.ok(ctx.runtime.getState().history.some(row => row.text === mountain.segments[2].performance));
      await respond(speech, {ok: true, audio_queued: true, speech_id: speechId});
      ctx.emit('neko-assistant-speech-end', {turnId: speechId});
      for (let i = 0; i < 15; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'evaluating');
      assert.equal(ctx.runtime.allowsSpeechCorrelation(speechId), false);
      assert.equal(meter.speech.length, 1);
    """),
)


@pytest.mark.parametrize('_name,scenario', SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_transition_playback_latency(_name, scenario):
    _run(RUNTIME_HARNESS + SETUP + '\nasync function run() {\n' + scenario + '\n}\n')

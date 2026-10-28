"""Exercise playback interruption against the real runtime and existing browser harness."""

import pytest

from tests.unit.test_theater_review_round2_frontend import RUNTIME_HARNESS, _run


PLAYBACK_SETUP = r"""
async function beginPlayback(ctx, status = 'active') {
  await launch(ctx);
  const request = await submit(ctx, '继续。');
  const record = { revision: 5, input_text: '继续。', performance: '这是一段已经提交的完整对白。',
    suggested_inputs: ['往前走。'] };
  const response = snapshot('session_a', 5, status);
  response.session.performance_history = [record];
  response.performance = record;
  if (status === 'ended') response.scene = { terminal: true, ending: {title: '落幕', summary: '故事结束。'} };
  await respond(request, response);
  assert.equal(ctx.runtime.getState().phase, 'performing');
  return take(ctx, /\/session\/speak-block$/);
}
function invitationSnapshot(revision = 4, available = true) {
  return { ...snapshot('session_a', revision), invitation_acceptance_available: available,
    suggested_inputs: ['接受邀请', '再等一会。', '接受邀请'] };
}
async function launchInvitation(ctx, available = true) {
  sendLaunch(ctx);
  await respond(take(ctx, /\/session\/session_/), invitationSnapshot(4, available));
  await claimLaunch(ctx, invitationSnapshot(4, available));
  assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
}
const visibleSuggestions = ctx => ctx.views.at(-1).theaterPresentation.suggestedInputs;
"""


SCENARIOS = (
    ("reviewed_choices_appear_after_text_without_waiting_for_voice", r"""
      const ctx = createContext();
      const speech = await beginPlayback(ctx);
      assert.deepEqual(visibleSuggestions(ctx), [], 'choices must wait for the complete text');
      for (let i = 0; i < 200 && !visibleSuggestions(ctx).length; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'performing', 'voice is still pending');
      assert.deepEqual(visibleSuggestions(ctx), ['观察桌面。']);
      assert.ok(ctx.runtime.getState().history.some(row => row.text === '这是一段已经提交的完整对白。'));
      const speechId = JSON.parse(speech.options.body).playback_request_id;
      ctx.callbacks.suggestion('观察桌面。'); ctx.callbacks.suggestion('观察桌面。'); await tick();
      const next = take(ctx, /\/session\/input$/), payload = JSON.parse(next.options.body);
      assert.equal(payload.input_source, 'suggestion'); assert.equal(payload.base_revision, 5);
      assert.equal(ctx.requests.filter(row => /\/session\/input$/.test(row.url)).length, 0);
      await respond(speech, {ok: true, audio_queued: true, speech_id: speechId});
      ctx.emit('neko-assistant-speech-end', {turnId: speechId});
      for (let i = 0; i < 10; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'evaluating');
      assert.equal(ctx.runtime.allowsSpeechCorrelation(speechId), false);
    """),
    ("early_choice_keeps_server_invitation_control_semantics", r"""
      const ctx = createContext(); await launch(ctx);
      const request = await submit(ctx, '继续。');
      await respond(request, {...invitationSnapshot(5), performance: {performance: '要一起去看看吗？'}});
      for (let i = 0; i < 200 && !visibleSuggestions(ctx).length; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'performing');
      assert.deepEqual(visibleSuggestions(ctx), ['接受邀请', '再等一会。']);
      ctx.callbacks.suggestion('接受邀请'); await tick();
      const payload = JSON.parse(take(ctx, /\/session\/input$/).options.body);
      assert.equal(payload.input_source, 'accept_invitation'); assert.equal(payload.message, '');
      assert.equal(payload.base_revision, 5);
    """),
    ("first_input_can_interrupt_opening_without_releasing_its_session", r"""
      const ctx = createContext();
      sendLaunch(ctx, 'session_a', 0, 'theater:start-request');
      for (let i = 0; i < 4; i++) await tick();
      const opening = snapshot('session_a', 0);
      opening.session.opening_performance.performance = '开场正在说一段完整的对白。';
      await respond(take(ctx, /\/session\/start$/), opening);
      assert.equal(ctx.runtime.getState().phase, 'performing');
      const speech = take(ctx, /\/session\/speak-block$/);
      const next = await submit(ctx, '我知道了，继续。');
      assert.equal(JSON.parse(next.options.body).base_revision, 0);
      assert.ok(ctx.runtime.getState().history.some(row => row.text === '开场正在说一段完整的对白。'));
      await respond(speech, {ok: true, audio_queued: false});
      for (let i = 0; i < 20; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'evaluating');
      assert.equal(ctx.requests.filter(row => /\/session\/(end|release)$/.test(row.url)).length, 0);
    """),
    ("skip_restores_committed_text_and_withdraws_peer_audio", r"""
      const ctx = createContext({channel: true}), peer = createContext({channel: true});
      let stopped = 0;
      peer.window.appAudioPlayback = {clearAudioQueueWithoutDecoderReset() { stopped++; }};
      const speech = await beginPlayback(ctx);
      const speechId = JSON.parse(speech.options.body).playback_request_id;
      await respond(speech, {ok: true, audio_queued: true, speech_id: speechId});
      flush(); peer.window.appState.currentPlayingSpeechCorrelationId = speechId;
      assert.equal(peer.runtime.allowsSpeechCorrelation(speechId), true);
      assert.equal(ctx.views.at(-1).composerDisabled, false);
      assert.equal(ctx.views.at(-1).compactChatState, 'input');
      assert.equal(ctx.runtime.skipPlayback(), true);
      flush();
      assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
      assert.ok(ctx.runtime.getState().history.some(row => row.text === '这是一段已经提交的完整对白。'));
      assert.deepEqual(copy(ctx.runtime.getState().suggestedInputs), ['观察桌面。']);
      assert.equal(peer.runtime.allowsSpeechCorrelation(speechId), false);
      assert.equal(stopped, 1);
      assert.equal(ctx.runtime.skipPlayback(), false);
      assert.equal(ctx.requests.filter(row => /\/session\/input$/.test(row.url)).length, 0);
    """),
    ("send_during_playback_serializes_and_ignores_late_speech", r"""
      const ctx = createContext();
      const speech = await beginPlayback(ctx);
      const speechId = JSON.parse(speech.options.body).playback_request_id;
      ctx.callbacks.freeform('我已经看完了，继续。');
      ctx.callbacks.freeform('重复点击。');
      await tick();
      const next = take(ctx, /\/session\/input$/);
      assert.equal(JSON.parse(next.options.body).base_revision, 5);
      assert.equal(ctx.requests.filter(row => /\/session\/input$/.test(row.url)).length, 0);
      assert.equal(ctx.runtime.getState().phase, 'evaluating');
      assert.ok(ctx.runtime.getState().history.some(row => row.text === '这是一段已经提交的完整对白。'));
      await respond(speech, {ok: true, audio_queued: true, speech_id: speechId});
      ctx.emit('neko-assistant-speech-end', {turnId: speechId});
      for (let i = 0; i < 10; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'evaluating');
      assert.equal(ctx.runtime.allowsSpeechCorrelation(speechId), false);
      await respond(next, {ok: false, reason: 'numeric_input_request_failed'}, 502);
      assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
      assert.equal(ctx.runtime.getState().draftRestore.text, '我已经看完了，继续。');
      assert.equal(ctx.runtime.getState().history.filter(row => row.text === '这是一段已经提交的完整对白。').length, 1);
    """),
    ("ending_can_expand_but_cannot_submit_and_normal_chat_is_untouched", r"""
      const ctx = createContext();
      assert.equal(ctx.runtime.skipPlayback(), false);
      const speech = await beginPlayback(ctx, 'ended');
      ctx.callbacks.freeform('不应产生新回合。');
      await tick();
      assert.equal(ctx.requests.filter(row => /\/session\/input$/.test(row.url)).length, 0);
      assert.equal(ctx.runtime.skipPlayback(), true);
      assert.equal(ctx.runtime.getState().phase, 'ended');
      assert.equal(ctx.views.at(-1).composerDisabled, true);
      assert.equal(ctx.runtime.getState().history.filter(row => row.type === 'ending').length, 1);
      ctx.runtime.clear('test-exit');
      assert.equal(ctx.runtime.skipPlayback(), false);
      await respond(speech, {ok: true, audio_queued: false});
      for (let i = 0; i < 10; i++) await tick();
      assert.equal(ctx.runtime.getState().active, false);
    """),
    ("acceptance_control_is_server_bound_empty_input_and_serialized", r"""
      const ctx = createContext();
      await launchInvitation(ctx);
      assert.deepEqual(visibleSuggestions(ctx), ['接受邀请', '再等一会。']);
      const history = copy(ctx.runtime.getState().history);
      ctx.callbacks.suggestion('接受邀请'); ctx.callbacks.suggestion('接受邀请'); await tick();
      const request = take(ctx, /\/session\/input$/), payload = JSON.parse(request.options.body);
      assert.equal(payload.input_source, 'accept_invitation');
      assert.equal(payload.message, ''); assert.equal(payload.base_revision, 4);
      assert.equal(ctx.requests.filter(row => /\/session\/input$/.test(row.url)).length, 0);
      assert.deepEqual(copy(ctx.runtime.getState().history), history, '控制点击不能伪造玩家台词');
      await respond(request, { ...snapshot('session_a', 5), idempotent_replay: true });
      assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, false);
      assert.deepEqual(visibleSuggestions(ctx), ['观察桌面。']);
    """),
    ("same_text_without_server_capability_remains_an_ordinary_suggestion", r"""
      const ctx = createContext();
      await launchInvitation(ctx, false);
      ctx.callbacks.suggestion('接受邀请'); await tick();
      const request = take(ctx, /\/session\/input$/), payload = JSON.parse(request.options.body);
      assert.equal(payload.input_source, 'suggestion'); assert.equal(payload.message, '接受邀请');
      assert.ok(ctx.runtime.getState().history.some(row => row.type === 'player_action' && row.text === '接受邀请'));
      await respond(request, {ok: false, reason: 'numeric_input_request_failed'}, 502);
      assert.equal(ctx.runtime.getState().draftRestore.text, '接受邀请');
    """),
    ("acceptance_control_retries_idempotently_without_restoring_a_role_line", r"""
      const ctx = createContext();
      await launchInvitation(ctx);
      ctx.callbacks.suggestion('接受邀请'); await tick();
      const first = take(ctx, /\/session\/input$/), firstPayload = JSON.parse(first.options.body);
      await respond(first, {ok: false, reason: 'numeric_v2_actor_failed'}, 502);
      assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
      assert.equal(ctx.runtime.getState().draftRestore.text, '');
      assert.ok(visibleSuggestions(ctx).includes('接受邀请'));
      assert.ok(!ctx.runtime.getState().history.some(row => row.type === 'player_action' && row.text === '接受邀请'));
      ctx.window.t = key => key === 'theater.acceptInvitation' ? 'Accept invitation' : key;
      ctx.emit('localechange'); for (let i = 0; i < 4; i++) await tick();
      ctx.callbacks.suggestion('Accept invitation'); await tick();
      const retry = take(ctx, /\/session\/input$/), retryPayload = JSON.parse(retry.options.body);
      assert.equal(retryPayload.client_turn_id, firstPayload.client_turn_id);
      assert.equal(retryPayload.message, ''); assert.equal(retryPayload.input_source, 'accept_invitation');
      await respond(retry, { ...snapshot('session_a', 5), idempotent_replay: true });
      assert.equal(ctx.runtime.getState().draftRestore, null);
      assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, false);
    """),
    ("freeform_with_control_label_and_capability_is_still_player_speech", r"""
      const ctx = createContext(); await launchInvitation(ctx);
      const request = await submit(ctx, '接受邀请');
      const payload = JSON.parse(request.options.body);
      assert.equal(payload.input_source, 'freeform'); assert.equal(payload.message, '接受邀请');
      assert.ok(ctx.runtime.getState().history.some(row => row.type === 'player_action' && row.text === '接受邀请'));
    """),
    ("acceptance_conflict_refreshes_capability_and_never_restores_button_as_draft", r"""
      for (const reason of ['numeric_base_revision_mismatch', 'numeric_invitation_acceptance_not_available']) {
        const ctx = createContext();
        await launchInvitation(ctx);
        ctx.callbacks.suggestion('接受邀请'); await tick();
        await respond(take(ctx, /\/session\/input$/), {ok: false, reason}, 409);
        await respond(take(ctx, /\/session\/session_a\?/), snapshot('session_a', 5));
        assert.equal(ctx.runtime.getState().revision, 5);
        assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, false);
        assert.equal(ctx.runtime.getState().draftRestore.text, '');
        assert.ok(!visibleSuggestions(ctx).includes('接受邀请'));
        assert.ok(!ctx.runtime.getState().history.some(row => row.type === 'player_action' && row.text === '接受邀请'));
        assert.equal(ctx.views.at(-1).theaterPresentation.errorMessage, '演出状态已更新，请确认当前可用操作。');
      }
    """),
    ("known_acceptance_conflict_with_failed_refresh_does_not_revive_stale_control", r"""
      for (const reason of ['numeric_base_revision_mismatch', 'numeric_invitation_acceptance_not_available']) {
        const ctx = createContext(); await launchInvitation(ctx);
        ctx.callbacks.suggestion('接受邀请'); await tick();
        await respond(take(ctx, /\/session\/input$/), {ok: false, reason}, 409);
        assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, false, '明确冲突已撤销旧接受能力');
        await respond(take(ctx, /\/session\/session_a\?/), {ok: false, reason: 'session_refresh_failed'}, 502);
        assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
        assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, false);
        assert.ok(!visibleSuggestions(ctx).includes('接受邀请'));
        assert.equal(ctx.runtime.getState().draftRestore.text, '');
        assert.ok(!ctx.runtime.getState().history.some(row => row.type === 'player_action' && row.text === '接受邀请'));
      }
    """),
    ("unknown_acceptance_network_result_preserves_idempotent_control_retry", r"""
      const ctx = createContext(); await launchInvitation(ctx);
      ctx.callbacks.suggestion('接受邀请'); await tick();
      const first = take(ctx, /\/session\/input$/), firstPayload = JSON.parse(first.options.body);
      first.reject(new Error('connection interrupted'));
      for (let i = 0; i < 8; i++) await tick();
      assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
      assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, true);
      assert.ok(visibleSuggestions(ctx).includes('接受邀请'));
      assert.equal(ctx.runtime.getState().draftRestore.text, '');
      ctx.callbacks.suggestion('接受邀请'); await tick();
      const payload = JSON.parse(take(ctx, /\/session\/input$/).options.body);
      assert.equal(payload.client_turn_id, firstPayload.client_turn_id);
      assert.equal(payload.input_source, 'accept_invitation'); assert.equal(payload.message, '');
    """),
    ("acceptance_label_tracks_locale_but_request_does_not_depend_on_translation", r"""
      const ctx = createContext();
      await launchInvitation(ctx);
      ctx.window.t = key => key === 'theater.acceptInvitation' ? 'Accept invitation' : key;
      ctx.emit('localechange'); for (let i = 0; i < 4; i++) await tick();
      assert.equal(visibleSuggestions(ctx)[0], 'Accept invitation');
      assert.equal(visibleSuggestions(ctx).filter(text => text === 'Accept invitation').length, 1);
      ctx.callbacks.suggestion('Accept invitation'); await tick();
      const payload = JSON.parse(take(ctx, /\/session\/input$/).options.body);
      assert.equal(payload.input_source, 'accept_invitation'); assert.equal(payload.message, '');
    """),
    ("acceptance_capability_survives_pointer_restore_and_playback_completion", r"""
      const ctx = createContext(); await launchInvitation(ctx);
      const pointer = JSON.parse(ctx.storage['neko.theater.numeric.v2.capsule-pointer.v1']);
      const restored = createContext({pointer});
      await respond(take(restored, /\/session\/session_a\?/), invitationSnapshot());
      assert.equal(restored.runtime.getState().invitationAcceptanceAvailable, true);
      assert.equal(visibleSuggestions(restored)[0], '接受邀请');
      const request = await submit(restored, '稍等。');
      const response = invitationSnapshot(5);
      response.performance = {performance: '我们现在可以继续。', suggested_inputs: []};
      await respond(request, response);
      assert.equal(restored.runtime.getState().phase, 'performing');
      assert.deepEqual(visibleSuggestions(restored), []);
      const speech = take(restored, /\/session\/speak-block$/);
      const id = JSON.parse(speech.options.body).playback_request_id;
      await respond(speech, {ok: true, audio_queued: true, speech_id: id});
      restored.emit('neko-assistant-speech-end', {turnId: id});
      for (let i = 0; i < 12; i++) await tick();
      assert.equal(restored.runtime.getState().phase, 'awaiting_player');
      assert.equal(visibleSuggestions(restored)[0], '接受邀请');
      assert.equal(restored.runtime.getState().invitationAcceptanceAvailable, true);
    """),
    ("acceptance_capability_survives_playback_skip_without_submitting", r"""
      const ctx = createContext(); await launch(ctx);
      const request = await submit(ctx, '继续。');
      await respond(request, { ...invitationSnapshot(5), performance: {performance: '我们接着看。', suggested_inputs: []} });
      assert.equal(ctx.runtime.getState().phase, 'performing');
      assert.deepEqual(visibleSuggestions(ctx), []);
      assert.equal(ctx.runtime.skipPlayback(), true);
      assert.equal(visibleSuggestions(ctx)[0], '接受邀请');
      assert.equal(ctx.runtime.getState().invitationAcceptanceAvailable, true);
      assert.equal(ctx.requests.filter(row => /\/session\/input$/.test(row.url)).length, 0);
    """),
)


@pytest.mark.parametrize("_name,scenario", SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_playback_controls(_name, scenario):
    _run(RUNTIME_HARNESS + PLAYBACK_SETUP + "\nasync function run() {\n" + scenario + "\n}\n")

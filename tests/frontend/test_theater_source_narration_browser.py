"""Verify source narration order, TTS indices and refresh recovery in the real capsule."""

import json

import pytest
from playwright.sync_api import Page, Route, expect

from tests.frontend.test_theater_capsule_browser import _snapshot


@pytest.mark.frontend
@pytest.mark.parametrize('bridge_narration', ['两人来到门边。', ''])
def test_source_npc_reply_is_visible_before_catgirl_and_never_spoken_as_catgirl(
    mock_page: Page, running_server: str, bridge_narration: str,
):
    reply = '值班员回答：“编号是丙七。”'
    transition = {
        'revision': 1, 'input_text': '编号是多少？', 'suggested_inputs': [],
        'transition_delivered': True, 'visible_node_id': 'mainline_02',
        'segments': [
            {'phase': 'source_response', 'scene_narration': reply, 'performance': '记住了。'},
            {'phase': 'transition_bridge', 'scene_narration': bridge_narration},
            {'phase': 'target_opening', 'scene_narration': '门外吹来微风。', 'performance': '这里安静些。'},
        ],
    }
    current = _snapshot(revision=0)
    speech_indexes = []

    def handler(route: Route):
        nonlocal current
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/session/capsule-browser-session'):
            response = current
        elif path.endswith('/session/input'):
            current = _snapshot(revision=1, performance_history=[transition])
            response = {**current, 'performance': transition}
        elif path.endswith('/session/speak-block'):
            body = route.request.post_data_json
            if body['revision'] == 1:
                speech_indexes.append(body['dialogue_block_indexes'])
            response = {'ok': True, 'skipped': 'test_no_audio'}
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(response, ensure_ascii=False))

    mock_page.route('**/api/theater-numeric/**', handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f'{running_server}/', wait_until='domcontentloaded')
    mock_page.wait_for_function('() => window.reactChatWindowHost && window.nekoTheaterRuntime')
    mock_page.evaluate('''() => {
        window.isMainUIHiddenByModelManager = () => false;
        document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
        window.reactChatWindowHost.openWindow();
        window.postMessage({schema:'neko.theater.interpage.v1', action:'theater:launch-request',
            launch_id:'source-narration-launch', launch_action:'continue', story_id:'capsule-browser-story',
            session_id:'capsule-browser-session', revision:0}, window.location.origin);
    }''')
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'")
    mock_page.evaluate("() => window.nekoTheaterRuntime.handleComposerSubmit('编号是多少？')")
    mock_page.wait_for_function("""() => {
        const s = window.nekoTheaterRuntime.getState();
        return s.revision === 1 && s.phase === 'awaiting_player';
    }""", timeout=20000)

    history = mock_page.locator('.compact-export-history-anchor')
    expect(history).to_contain_text(reply)
    expect(history.locator('.is-system').filter(has_text=reply)).to_have_count(1)
    text = history.inner_text()
    assert text.index(reply) < text.index('记住了。') < text.index('门外吹来微风。')
    if bridge_narration:
        assert text.index('记住了。') < text.index(bridge_narration) < text.index('门外吹来微风。')
    else:
        assert '两人来到门边。' not in text
    # 空桥段不会生成播放块，后续TTS索引必须与服务端过滤后的内容一致。
    expected_indexes = [[1], [4 if bridge_narration else 3]]
    assert speech_indexes == expected_indexes

    # 冷刷新从实际快照重建旁白，不重复播放已经保存的对白。
    mock_page.reload(wait_until='domcontentloaded')
    mock_page.wait_for_function("""() => window.nekoTheaterRuntime
        && window.nekoTheaterRuntime.getState().revision === 1
        && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'""")
    expect(mock_page.locator('.compact-export-history-anchor')).to_contain_text(reply)
    assert speech_indexes == expected_indexes

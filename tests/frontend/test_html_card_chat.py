"""Real chat renderer + adapter; plugin HTTP dispatch is covered separately."""
import pytest
from playwright.sync_api import expect


def _open_card_chat(page, running_server, surface_path):
    page.add_init_script("localStorage.setItem('neko_tutorial_settings', 'seen')")
    page.goto(f"{running_server}/{surface_path}", wait_until="domcontentloaded")
    page.wait_for_function("() => window.reactChatWindowHost && window.appendReactChatBlocks && window.appButtons && window.appChat && window.appState && typeof window.sendTextPayload === 'function'")
    page.wait_for_function("() => appState.socket && appState.socket.readyState === 1")
    page.evaluate("""() => {
        reactChatWindowHost.openWindow();
        reactChatWindowHost.clearMessages();
    }""")


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["chat_full", "chat"])
def test_html_cards_update_and_call_buttons_without_executing_scripts(mock_page, running_server, surface_path):
    page = mock_page
    calls = []

    def action(route):
        calls.append(route.request.post_data_json)
        route.fulfill(json={"result": {"message": "Track started"}})

    page.route("**/api/plugin-cards/demo/action/play", action)
    _open_card_chat(page, running_server, surface_path)
    page.evaluate("window.cardScriptRan = false")
    block = {"type": "html_card", "operation": "create", "cardId": "one", "pluginId": "demo",
             "targetLanlan": "Alice", "html": '<h3>Song one</h3><button data-neko-action="go" onclick="parent.cardScriptRan=true">Play</button><script>parent.cardScriptRan=true</script>',
             "css": "button {padding:10px}", "summary": "Song one",
             "actions": {"go": {"entry": "play", "args": {"track_id": "123"}}}}
    page.evaluate("block => appendReactChatBlocks({blocks:[block]})", block)
    page.evaluate("block => appendReactChatBlocks({blocks:[{...block,cardId:'two',html:'<p>Song two</p><button data-neko-action=go>Play</button>',summary:'Song two',actions:{go:{entry:'play',args:{track_id:'456'}}}}]})", block)
    page.wait_for_function("() => reactChatWindowHost.getState().messages.length === 2")
    if surface_path == "chat":
        page.evaluate("() => reactChatWindowHost.setCompactHistoryOpen(true)")
    attribute = 'data-message-id' if surface_path == 'chat_full' else 'data-compact-export-history-message-id'
    first = page.locator(f'[{attribute}="plugin-card-demo:one"]')
    frame = first.frame_locator('iframe')
    expect(frame.get_by_role('button', name='Play')).to_be_visible()
    frame.get_by_role('button', name='Play').click()
    expect(first.get_by_role('status')).to_have_text('Track started')
    assert calls == [{"card_id": "one", "target_lanlan": "Alice", "presentation": "chat", "args": {"track_id": "123"}, "locale": page.locator('html').get_attribute('lang') or 'en'}]
    second = page.locator(f'[{attribute}="plugin-card-demo:two"]')
    second.frame_locator('iframe').get_by_role('button', name='Play').click()
    expect(second.get_by_role('status')).to_have_text('Track started')
    assert calls[1]['card_id'] == 'two'
    assert calls[1]['args'] == {'track_id': '456'}
    assert page.evaluate('window.cardScriptRan') is False
    page.evaluate("""() => appendReactChatBlocks({blocks:[{
        type:'html_card',operation:'update',cardId:'one',pluginId:'demo',targetLanlan:'Alice',
        html:'<p>Playing</p>',summary:'Playing',actions:{}
    }]})""")
    expect(frame.get_by_text('Playing', exact=True)).to_be_visible()
    expect(frame.locator('button')).to_have_count(0)
    assert page.evaluate('reactChatWindowHost.getState().messages.length') == 2
    assert page.evaluate("reactChatWindowHost.getState().messages[0].blocks[0].css") == block['css']

    preview = page.evaluate("""() => appChatExport.buildCompactInlinePreview({
        messageIds:['plugin-card-demo:one','plugin-card-demo:two'],format:'markdown'
    })""")
    assert preview['previewKind'] == 'document'
    assert 'Playing' in preview['previewDocument'] and 'Song two' in preview['previewDocument']
    assert 'data-neko-action' not in preview['previewDocument']
    assert len(calls) == 2


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["chat_full", "chat"])
def test_card_partial_updates_preserve_pending_action(mock_page, running_server, surface_path):
    page = mock_page
    pending = []
    page.route("**/api/plugin-cards/demo/action/slow", lambda route: pending.append(route))
    _open_card_chat(page, running_server, surface_path)
    page.evaluate("""() => appendReactChatBlocks({blocks:[{
        type:'html_card',operation:'create',cardId:'pending',pluginId:'demo',targetLanlan:'Alice',
        html:'<button data-neko-action="go">Run</button>',summary:'Ready',css:'',
        actions:{go:{entry:'slow',args:{}}}
    }]})""")
    if surface_path == "chat":
        page.evaluate("() => reactChatWindowHost.setCompactHistoryOpen(true)")
    attribute = 'data-message-id' if surface_path == 'chat_full' else 'data-compact-export-history-message-id'
    card = page.locator(f'[{attribute}="plugin-card-demo:pending"]')
    button = card.frame_locator('iframe').get_by_role('button', name='Run')
    button.click()
    expect(button).to_be_disabled()
    assert len(pending) == 1

    for fields in [{"summary": "Working"}, {"html": '<p>Working</p><button data-neko-action="go">Run</button>'}]:
        page.evaluate("""fields => appendReactChatBlocks({blocks:[{
            type:'html_card',operation:'update',cardId:'pending',pluginId:'demo',targetLanlan:'Alice',...fields
        }]})""", fields)
        expect(button).to_be_disabled()
        # Dispatch also exercises the request guard independently of native disabled behavior.
        button.dispatch_event('click')
        assert len(pending) == 1

    pending[0].fulfill(json={"result": {"message": "Finished once"}})
    expect(card.get_by_role('status')).to_have_text('Finished once')
    expect(button).to_be_enabled()
    assert len(pending) == 1

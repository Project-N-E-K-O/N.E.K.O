"""Which page opens a mini-game pushed over the WebSocket (keyword invite / slash command)."""
import json
import shutil
from pathlib import Path

import pytest
from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('static/app/app-react-chat-window/message-bundle-actions-and-prompts.js', 'utf8');
const results = {};
for (const [name, pathname, multiWindow] of [
  ['browser', '/', undefined],
  ['electron_pet', '/', true],
  ['electron_chat', '/chat', true],
  ['electron_chat_full', '/chat_full', true],
]) {
  const opened = [];
  const window = {location: {pathname}, open: url => { opened.push(url); return {}; }};
  if (multiWindow !== undefined) window.__NEKO_MULTI_WINDOW__ = multiWindow;
  window.window = window;
  const context = vm.createContext({window, console, setTimeout, clearTimeout,
    document: {querySelector: () => null, addEventListener() {}}});
  vm.runInContext(source, context);
  const parts = window.__appReactChatWindowParts;
  parts.state = {choicePrompt: null, _launchedMiniGameSessionIds: {}};
  parts.renderWindow = () => {};
  parts.handleMiniGameInviteResolved({
    sessionId: 'session', action: 'open_game', gameType: 'watch-together', url: '/watch_together?session_id=session',
  });
  results[name] = opened.length;
}
console.log(JSON.stringify(results));
"""


def test_ws_game_launch_is_opened_by_the_pet_page_only():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the chat window host check')
    result = run_node_script(node, SCRIPT, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    opened = json.loads(result.stdout.strip().splitlines()[-1])
    # Electron's pet preload also sets __NEKO_MULTI_WINDOW__, so the flag alone
    # must not make the pet a follower, or no window opens the game at all.
    assert opened == {'browser': 1, 'electron_pet': 1, 'electron_chat': 0, 'electron_chat_full': 0}

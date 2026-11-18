"""Proactive chat / vision interval defaults have a single source.

``static/app/app-state.js`` (``window.appConst``) owns the defaults. The
settings popup in ``static/avatar/avatar-ui-popup.js`` must read them from
there; its literal fallbacks only cover pages that do not load app-state.js
and must equal the app-state.js values. Previously the popup hard-coded 15s
for the vision interval while the effective default was 10s.
"""

import json
import re
import textwrap
from pathlib import Path

import pytest

from tests.support.websocket_source_harness import _run_settings_node_harness

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_STATE_PATH = REPO_ROOT / "static" / "app" / "app-state.js"
POPUP_PATH = REPO_ROOT / "static" / "avatar" / "avatar-ui-popup.js"

pytestmark = pytest.mark.frontend_contract

_INTERVALS = (
    # (state / settings key, appConst key, popup fallback constant)
    ("proactiveChatInterval", "DEFAULT_PROACTIVE_CHAT_INTERVAL",
     "AVATAR_POPUP_FALLBACK_PROACTIVE_CHAT_INTERVAL"),
    ("proactiveVisionInterval", "DEFAULT_PROACTIVE_VISION_INTERVAL",
     "AVATAR_POPUP_FALLBACK_PROACTIVE_VISION_INTERVAL"),
)


def _load_app_state() -> dict:
    """Run the real app-state.js in a minimal sandbox and read back defaults."""
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync(__APP_STATE_PATH__, 'utf8');
        const sandbox = { console: { log() {}, warn() {}, error() {} } };
        sandbox.window = sandbox;
        sandbox.navigator = { userAgent: '' };
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox);
        const C = sandbox.appConst;
        const S = sandbox.appState;
        process.stdout.write(JSON.stringify({
          const: {
            DEFAULT_PROACTIVE_CHAT_INTERVAL: C.DEFAULT_PROACTIVE_CHAT_INTERVAL,
            DEFAULT_PROACTIVE_VISION_INTERVAL: C.DEFAULT_PROACTIVE_VISION_INTERVAL,
          },
          state: {
            proactiveChatInterval: S.proactiveChatInterval,
            proactiveVisionInterval: S.proactiveVisionInterval,
          },
        }));
        """
    ).replace("__APP_STATE_PATH__", json.dumps(str(APP_STATE_PATH)))
    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_app_state_initial_intervals_use_app_const_defaults():
    loaded = _load_app_state()
    # The effective defaults today; changing them is a product decision.
    assert loaded["const"]["DEFAULT_PROACTIVE_CHAT_INTERVAL"] == 15
    assert loaded["const"]["DEFAULT_PROACTIVE_VISION_INTERVAL"] == 10
    for state_key, const_key, _ in _INTERVALS:
        assert loaded["state"][state_key] == loaded["const"][const_key], state_key


def test_popup_interval_defaults_come_from_app_const():
    loaded = _load_app_state()
    source = POPUP_PATH.read_text(encoding="utf-8")
    for state_key, const_key, fallback_name in _INTERVALS:
        row = re.search(
            r"\{[^{}]*intervalKey:\s*'" + state_key + r"'[^{}]*\}", source
        )
        assert row, f"settings toggle row for {state_key} not found"
        default_expr = re.search(r"defaultInterval:\s*([^,}]+\([^)]*\)|[^,}]+)", row.group(0))
        assert default_expr, f"{state_key} row has no defaultInterval"
        assert default_expr.group(1).strip() == (
            f"getAvatarPopupDefaultInterval('{const_key}', {fallback_name})"
        ), f"{state_key} popup default must be read from window.appConst"

        fallback = re.search(
            r"^const " + fallback_name + r"\s*=\s*(\d+)\s*;", source, re.MULTILINE
        )
        assert fallback, f"{fallback_name} not declared"
        assert int(fallback.group(1)) == loaded["const"][const_key], (
            f"{fallback_name} must equal app-state.js {const_key}"
        )

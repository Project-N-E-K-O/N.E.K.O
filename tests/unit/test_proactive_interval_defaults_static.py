"""Proactive chat / vision interval defaults have a single source.

``static/app/app-state.js`` (``window.appConst``) owns the defaults. The
settings popup in ``static/avatar/avatar-ui-popup.js`` must read them from
there; its literal fallbacks only cover pages that do not load app-state.js
and must equal the app-state.js values. Previously the popup hard-coded 15s
for the vision interval while the effective default was 10s.
"""

import functools
import json
import re
import textwrap
from pathlib import Path

import pytest

from tests.support.websocket_source_harness import _run_settings_node_harness

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_STATE_PATH = REPO_ROOT / "static" / "app" / "app-state.js"
POPUP_PATH = REPO_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
APP_SETTINGS_PATH = REPO_ROOT / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.frontend_contract

_INTERVALS = (
    # (state / settings key, appConst key, popup fallback constant)
    ("proactiveChatInterval", "DEFAULT_PROACTIVE_CHAT_INTERVAL",
     "AVATAR_POPUP_FALLBACK_PROACTIVE_CHAT_INTERVAL"),
    ("proactiveVisionInterval", "DEFAULT_PROACTIVE_VISION_INTERVAL",
     "AVATAR_POPUP_FALLBACK_PROACTIVE_VISION_INTERVAL"),
)

# Sentinel values distinct from the product defaults, so a value that is
# re-hard-coded to 15 / 10 cannot pass by coincidence.
_PROBE_CONST = {
    "DEFAULT_PROACTIVE_CHAT_INTERVAL": 25,
    "DEFAULT_PROACTIVE_VISION_INTERVAL": 30,
}


def _run_node(script: str) -> dict:
    result = _run_settings_node_harness(script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _app_state_source(const_overrides: tuple = ()) -> str:
    source = APP_STATE_PATH.read_text(encoding="utf-8")
    for const_key, value in const_overrides:
        source, count = re.subn(
            r"(\b" + const_key + r":\s*)\d+", r"\g<1>" + str(value), source
        )
        assert count == 1, f"{const_key} not declared once in app-state.js"
    return source


@functools.cache
def _load_app_state(const_overrides: tuple = ()) -> dict:
    """Run app-state.js in a minimal sandbox and read back the defaults."""
    harness = textwrap.dedent(
        """
        const vm = require('node:vm');
        const source = __SOURCE__;
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
    ).replace("__SOURCE__", json.dumps(_app_state_source(const_overrides)))
    return _run_node(harness)


def test_app_state_product_defaults():
    # The effective defaults today; changing them is a product decision.
    loaded = _load_app_state()
    assert loaded["const"]["DEFAULT_PROACTIVE_CHAT_INTERVAL"] == 15
    assert loaded["const"]["DEFAULT_PROACTIVE_VISION_INTERVAL"] == 10
    for state_key, const_key, _ in _INTERVALS:
        assert loaded["state"][state_key] == loaded["const"][const_key], state_key


def test_app_state_initial_intervals_follow_app_const():
    loaded = _load_app_state(tuple(sorted(_PROBE_CONST.items())))
    for state_key, const_key, _ in _INTERVALS:
        assert loaded["const"][const_key] == _PROBE_CONST[const_key]
        assert loaded["state"][state_key] == _PROBE_CONST[const_key], (
            f"appState.{state_key} must be initialised from appConst.{const_key}"
        )


def _popup_defaults(source: str) -> dict:
    """Evaluate the popup's real defaultInterval expressions in a vm.

    Runs the fallback constants and getAvatarPopupDefaultInterval exactly as
    written in avatar-ui-popup.js, then evaluates each settings row's
    defaultInterval expression with and without window.appConst.
    """
    helper = re.search(
        r"^const AVATAR_POPUP_FALLBACK_PROACTIVE_CHAT_INTERVAL\b.*?"
        r"^function getAvatarPopupDefaultInterval\(.*?^\}\n",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert helper, "popup interval default helper block not found"
    expressions = {}
    for state_key, _, _ in _INTERVALS:
        row = re.search(
            r"\{[^{}\n]*intervalKey:\s*'" + state_key + r"'[^{}\n]*\}", source
        )
        assert row, f"settings toggle row for {state_key} not found"
        expr = re.search(
            r"defaultInterval:\s*(.+?),\s*(?:inverted|controlStyle)\b", row.group(0)
        )
        assert expr, f"{state_key} row has no defaultInterval"
        expressions[state_key] = expr.group(1)

    harness = textwrap.dedent(
        """
        const vm = require('node:vm');
        const helper = __HELPER__;
        const expressions = __EXPRESSIONS__;
        function evaluate(appConst) {
          const sandbox = {};
          sandbox.window = appConst === undefined ? {} : { appConst };
          vm.createContext(sandbox);
          vm.runInContext(helper, sandbox);
          const out = {};
          for (const [key, expr] of Object.entries(expressions)) {
            out[key] = vm.runInContext(expr, sandbox);
          }
          return out;
        }
        process.stdout.write(JSON.stringify({
          withConst: evaluate(__PROBE__),
          withoutConst: evaluate(undefined),
        }));
        """
    )
    harness = (
        harness.replace("__HELPER__", json.dumps(helper.group(0)))
        .replace("__EXPRESSIONS__", json.dumps(expressions))
        .replace("__PROBE__", json.dumps(_PROBE_CONST))
    )
    return _run_node(harness)


def test_popup_interval_defaults_read_app_const_and_fall_back():
    product = _load_app_state()["const"]
    result = _popup_defaults(POPUP_PATH.read_text(encoding="utf-8"))
    for state_key, const_key, _ in _INTERVALS:
        assert result["withConst"][state_key] == _PROBE_CONST[const_key], (
            f"popup {state_key} default must come from window.appConst"
        )
        assert result["withoutConst"][state_key] == product[const_key], (
            f"popup {state_key} fallback must equal app-state.js {const_key}"
        )


def test_app_settings_fallback_literals_match_app_const():
    # app-settings.js keeps its own literal fallback for when appConst lacks the
    # key; it must not drift from app-state.js either.
    loaded = _load_app_state()
    source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    for state_key, const_key, _ in _INTERVALS:
        match = re.search(
            state_key + r":\s*Number\.isFinite\(C\." + const_key + r"\)\s*"
            r"\?\s*C\." + const_key + r"\s*:\s*(\d+)\s*,",
            source,
        )
        assert match, f"app-settings.js default for {state_key} not found"
        assert int(match.group(1)) == loaded["const"][const_key], (
            f"app-settings.js fallback for {state_key} must equal {const_key}"
        )

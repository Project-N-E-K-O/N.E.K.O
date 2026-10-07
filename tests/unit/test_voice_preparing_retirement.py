"""Local ASR lifecycle notices and terminal voice notices have separate scope."""

import json
from pathlib import Path
import shutil

import pytest

from tests.node_harness import run_node_script


@pytest.mark.unit
def test_local_clear_preserves_voice_start_but_terminal_clear_hides_it():
    source = (Path(__file__).parents[2] / "static/app/app-websocket.js").read_text(encoding="utf-8")
    begin = source.index("    function clearLocalAsrPreparingNotice(options)")
    end = source.index("    function tearDownBlockedVoiceRoute()", begin)
    function = source[begin:end]
    # Exercise the actual helper and the three cleanup callsites' arguments.
    suffix = source[source.index("// -------- session_failed --------"):]
    calls = suffix.split("clearLocalAsrPreparingNotice(")[1:]
    args = [call.split(");", 1)[0] for call in calls]
    node = shutil.which("node")
    assert node, "Node is required for frontend contracts"
    script = """
const assert = require('node:assert/strict');
let hidden = 0;
const window = { hideVoicePreparingToast() { hidden++; } };
const S = { localAsrPreparingMessage: null, voiceStartPending: true };
""" + function + """
clearLocalAsrPreparingNotice();
assert.equal(hidden, 0);
S.localAsrPreparingMessage = 'local preparation';
clearLocalAsrPreparingNotice({preserveVoiceToast:true});
assert.equal(hidden, 0);
assert.equal(S.localAsrPreparingMessage, null);
""" + "\n".join("clearLocalAsrPreparingNotice(" + arg + ");" for arg in args) + "\nassert.equal(hidden, " + json.dumps(len(args)) + ");"
    result = run_node_script(node, script, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr

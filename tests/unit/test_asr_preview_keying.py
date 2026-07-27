"""Frontend pins for the turn-keyed independent-ASR preview bubble.

Codex P2 (second dispatcher boundary): ``TranscriptDispatcher.submit()`` only
queues the final on its own worker task while ``_handle_independent_asr_final``
goes on to activate the pending turn, so a previous turn's ``on_final`` can
reach Core *after* the next turn already streamed partials. Both frontend
removal paths used to drop the singleton preview unconditionally, erasing the
new turn's bubble. Previews now carry ``asr_turn_id`` and the clear path is
keyed by it; the identity-free ``user_transcript`` path stays unconditional and
is repaired backend-side (asr_runtime.py _restore_core_asr_preview_after_final).
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_WEBSOCKET_PATH = REPO_ROOT / "static" / "app" / "app-websocket.js"
ASR_RUNTIME_PATH = REPO_ROOT / "main_logic" / "core" / "asr_runtime.py"

PREVIEW_BRANCH_MARKER = "} else if (response.type === 'user_transcript_preview') {"
TRANSCRIPT_BRANCH_MARKER = "// -------- user_transcript --------"


def _slice(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def _preview_branch_body() -> str:
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    return _slice(source, PREVIEW_BRANCH_MARKER, TRANSCRIPT_BRANCH_MARKER)


def test_preview_clear_is_keyed_by_asr_turn_id():
    body = _preview_branch_body()

    assert "String(response.asr_turn_id || '')" in body
    # The clear no longer removes unconditionally: it is gated on the id of
    # the bubble currently on screen.
    empty_branch = _slice(body, "if (externalPreviewText === '') {", "} else {")
    assert "displayedPreview.asrTurnId" in empty_branch
    assert "externalPreviewTurnId === displayedPreviewTurnId" in empty_branch
    # Backward compat: a missing id on either side keeps the old behaviour.
    assert "!externalPreviewTurnId || !displayedPreviewTurnId" in empty_branch
    # Non-empty partials keep stamping the bubble with their own turn id.
    else_branch = body.split("} else {", 1)[1]
    assert "S.externalAsrPreviewMessage.asrTurnId = externalPreviewTurnId;" in else_branch


def test_user_transcript_removal_is_repaired_backend_side():
    # user_transcript is emitted by main_logic/core/turn.py without any turn
    # identity, so the frontend cannot key it; the negative half of the fix
    # lives in Core, which re-sends the newer turn's preview behind it.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    final_block = source.split(TRANSCRIPT_BRANCH_MARKER, 1)[1].split("// --------", 1)[0]
    assert "removeExternalAsrPreview();" in final_block
    assert "_restore_core_asr_preview_after_final" in final_block

    runtime_source = ASR_RUNTIME_PATH.read_text(encoding="utf-8")
    assert "async def _restore_core_asr_preview_after_final(" in runtime_source
    # The clear message carries the key the frontend compares against.
    assert '"asr_turn_id": turn_id,' in runtime_source


def _run_preview_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping preview keying harness test")
    return subprocess.run(
        [node_path, "-e", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_stale_clear_keeps_newer_turn_preview_harness():
    # Behavioral pin: drive the real upsert/remove helpers and the real
    # user_transcript_preview branch. A turn-7 clear arriving after turn 8
    # already rendered must be a no-op; turn 8's own clear must still remove.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    upsert_fn = "function upsertExternalAsrPreview(text) {" + _slice(
        source,
        "function upsertExternalAsrPreview(text) {",
        "function removeExternalAsrPreview()",
    )
    remove_fn = "function removeExternalAsrPreview() {" + _slice(
        source,
        "function removeExternalAsrPreview() {",
        "window.removeExternalAsrPreview = removeExternalAsrPreview;",
    )
    harness = textwrap.dedent(
        """
        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }

        const messages = new Map();
        const host = {
          appendMessage(msg) { messages.set(msg.id, msg); },
          updateMessage(id, patch) {
            const existing = messages.get(id);
            assert(existing, 'updateMessage on a missing bubble: ' + id);
            Object.assign(existing, patch);
          },
          removeMessage(id) { messages.delete(id); },
        };
        const window = { reactChatWindowHost: host, getCurrentTimeString: () => '00:00' };
        const S = {
          externalAsrPreviewMessage: null,
          lastVoiceUserMessage: null,
          lastVoiceUserMessageTime: 0,
        };

        __UPSERT__
        __REMOVE__

        function handlePreview(response) {
        __BRANCH__
        }

        function bubbleText() {
          const preview = S.externalAsrPreviewMessage;
          if (!preview) return null;
          const message = messages.get(preview.dataset.reactChatMessageId);
          return message ? message.blocks[0].text : null;
        }

        // Turn 7 streams, then turn 8 takes the bubble over.
        handlePreview({ text: 'old text', asr_turn_id: 'asr-1-7' });
        assert(bubbleText() === 'old text', 'turn 7 partial must render');
        handlePreview({ text: 'new text', asr_turn_id: 'asr-1-8' });
        assert(bubbleText() === 'new text', 'turn 8 partial must render');

        // The delayed turn-7 clear must NOT erase turn 8's bubble.
        handlePreview({ text: '', asr_turn_id: 'asr-1-7' });
        assert(bubbleText() === 'new text', 'stale clear erased the newer preview');
        assert(messages.size === 1, 'stale clear removed the react message');

        // Turn 8's own clear still removes it.
        handlePreview({ text: '', asr_turn_id: 'asr-1-8' });
        assert(S.externalAsrPreviewMessage === null, 'matching clear must remove');
        assert(messages.size === 0, 'matching clear must drop the react message');

        // Backward compat: an unkeyed backend keeps the unconditional path.
        handlePreview({ text: 'legacy' });
        assert(bubbleText() === 'legacy', 'unkeyed partial must render');
        handlePreview({ text: '' });
        assert(S.externalAsrPreviewMessage === null, 'unkeyed clear must remove');

        // Mixed pairing (keyed clear, unkeyed bubble) also stays unconditional.
        handlePreview({ text: 'legacy again' });
        handlePreview({ text: '', asr_turn_id: 'asr-1-9' });
        assert(S.externalAsrPreviewMessage === null, 'unkeyed bubble must still clear');

        console.log('OK');
        """
    )
    harness = (
        harness.replace("__UPSERT__", upsert_fn)
        .replace("__REMOVE__", remove_fn)
        .replace("__BRANCH__", _preview_branch_body())
    )

    result = _run_preview_node_harness(harness)

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout

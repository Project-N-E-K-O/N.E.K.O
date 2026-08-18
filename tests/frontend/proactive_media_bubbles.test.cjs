// Regression lock for the chat-visible proactive media (proactive_media)
// image-bubble rendering behaviour.
//
// Background: the host sends the proactive_media WS frame (turn_id +
// images) DIRECTLY at event ingestion — the frame's turn_id is a fresh
// host-generated id that is NOT guaranteed to match the proactive reply
// text's turn_id. Both the websocket branch and _showProactiveImageBubbles
// previously gated rendering on "gemini_response with the same turn_id
// arrives first", which made the image bubble appear never. The fix:
// render immediately on frame arrival (the image is the event's own
// visible result, no turn binding).
//
// Additionally the URL whitelist (/user_proactive_media/ prefix) must stay
// in both the websocket branch and the render sink: WS frame content is
// not trusted and must never reach img.src / openExternal sinks.
//
// These are source-text assertions that lock the key behaviours against
// being reverted to the old gated/unchecked logic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const WS_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static', 'app', 'app-websocket.js'), 'utf8');
const PROACTIVE_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static', 'app', 'app-proactive.js'), 'utf8');

test('proactive_media branch whitelists host URLs, buffers, and flushes immediately (no same-turn text gate)', () => {
    const proactiveMediaBranch = WS_SOURCE.slice(
        WS_SOURCE.indexOf("response.type === 'proactive_media'"),
        WS_SOURCE.indexOf("response.type === 'response_discarded'"),
    );
    assert.ok(proactiveMediaBranch.includes('_proactiveAttachmentBuffer'), 'should write into the attachment buffer');
    assert.ok(proactiveMediaBranch.includes('mediaBuf.images.push'), 'should buffer images');
    // Key regression: flushing must not depend on a realisticGeminiCurrentTurnId
    // match. Any reference to that variable inside this branch implies a gate
    // (any spelling — ===, !==, hoisted temp) that can permanently suppress
    // the bubble, so assert it is absent entirely.
    assert.ok(!proactiveMediaBranch.includes('realisticGeminiCurrentTurnId'),
        'branch must not reference realisticGeminiCurrentTurnId at all');
    assert.ok(proactiveMediaBranch.includes('_flushProactiveAttachments(response.turn_id)'),
        'flush immediately on frame arrival');
    // URL whitelist: only same-origin /user_proactive_media/ URLs may be buffered.
    assert.ok(proactiveMediaBranch.includes("indexOf('/user_proactive_media/') === 0"),
        'non-host image urls must be rejected before buffering');
    // No appProactive → the one-shot buffer entry must be cleaned up, not leaked.
    assert.ok(proactiveMediaBranch.includes('delete window._proactiveAttachmentBuffer[response.turn_id]'),
        'missing appProactive must delete the buffered entry');
});

test('_showProactiveImageBubbles renders immediately, whitelists host URLs, no turn gate', () => {
    const signature = 'function _showProactiveImageBubbles(';
    const fnStart = PROACTIVE_SOURCE.indexOf(signature);
    assert.notEqual(fnStart, -1, 'missing _showProactiveImageBubbles');
    // Slice to the DOM-fallback IIFE (first nested `function (`), which
    // covers the entry guard, the whitelist and the React path.
    const fnEnd = PROACTIVE_SOURCE.indexOf('function (', fnStart + signature.length);
    const fn = PROACTIVE_SOURCE.slice(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 1600);
    // Old implementation's first line: if (window.realisticGeminiCurrentTurnId !== targetTurnId) return;
    assert.ok(!fn.includes('realisticGeminiCurrentTurnId'),
        '_showProactiveImageBubbles must not gate on the current turn id (any spelling)');
    // The empty-array guard stays (unchanged contract).
    assert.ok(fn.includes('imageUrls.length === 0'), 'empty-array guard should stay');
    // Render-sink whitelist (same rule as the websocket branch).
    assert.ok(fn.includes("indexOf('/user_proactive_media/') === 0"),
        'render sink must reject non-host image urls');
});

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
const APP_TSX = fs.readFileSync(path.join(PROJECT_ROOT, 'frontend', 'react-neko-chat', 'src', 'App.tsx'), 'utf8');

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
    assert.ok(proactiveMediaBranch.includes('_flushProactiveAttachments(mediaTurnKey)'),
        'flush immediately on frame arrival');
    // URL whitelist: only the exact host-generated media URL shape passes
    // (32-hex filename + whitelisted extension). A bare prefix check lets
    // "../" traversal strings through (CodeRabbit #2905), so the prefix form
    // must be absent while the full-shape regex must be present.
    assert.ok(proactiveMediaBranch.includes('/^\\/user_proactive_media\\/[0-9a-f]{32}\\.(png|jpg|gif|webp)$/.test(mediaUrl)'),
        'whitelist must fully match the host media URL shape');
    assert.ok(!proactiveMediaBranch.includes("indexOf('/user_proactive_media/') === 0"),
        'prefix-only whitelist is forbidden (traversal passes it)');
    // turn_id is only the buffer key, not a render precondition: an empty
    // turn_id must fall back to a local one-shot key instead of dropping
    // the whole frame (CodeRabbit #2905).
    assert.ok(proactiveMediaBranch.includes('var mediaTurnKey'),
        'missing turn_id must get a local fallback buffer key');
    // No appProactive → the one-shot buffer entry must be cleaned up, not leaked.
    assert.ok(proactiveMediaBranch.includes('delete window._proactiveAttachmentBuffer[mediaTurnKey]'),
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
    // Render-sink whitelist: same full-shape rule as the websocket branch and
    // notify.py's _HOST_PROACTIVE_MEDIA_URL_RE (no prefix-only matching).
    assert.ok(fn.includes('/^\\/user_proactive_media\\/[0-9a-f]{32}\\.(png|jpg|gif|webp)$/.test(rawUrl)'),
        'render sink must fully match the host media URL shape');
    assert.ok(!fn.includes("indexOf('/user_proactive_media/') === 0"),
        'prefix-only whitelist is forbidden in the render sink');
});

test('compact surface overlay recognizes proactive-media- bubbles alongside memes', () => {
    // Codex #2905 P1: image-only proactive messages were invisible on the
    // compact pet surface — compactMemeOverlay only scanned meme- ids while
    // the new bubbles carry the proactive-media- prefix. Both are image-only
    // messages living in the folded history and must share the overlay.
    const scanStart = APP_TSX.indexOf('const compactMemeOverlay = useMemo');
    assert.notEqual(scanStart, -1, 'missing compactMemeOverlay');
    const scanEnd = APP_TSX.indexOf('if (memeIdx < 0) return null;', scanStart);
    const scan = APP_TSX.slice(scanStart, scanEnd > scanStart ? scanEnd : scanStart + 1200);
    assert.ok(scan.includes("'meme-'"), 'overlay scan must still match meme-');
    assert.ok(scan.includes("'proactive-media-'"),
        'overlay scan must also match proactive-media- (compact visibility)');
});

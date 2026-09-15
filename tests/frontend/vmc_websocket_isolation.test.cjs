const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// The pytest entry point hands this file to node as a temp file, so __dirname
// is the system temp directory rather than tests/frontend. Fall back to the
// cwd the wrapper sets, which is the repo root. Same guard the other
// pytest-driven suites use.
const fileRoot = path.resolve(__dirname, '..', '..');
const projectRoot = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const vmcSenderPath = path.join(projectRoot, 'static/vrm/vrm-vmc-sender.js');
const appWebsocketPath = path.join(projectRoot, 'static/app/app-websocket.js');

test('vrm-vmc-sender uses iframe-borrowed WebSocket constructor to avoid Electron preload cross-wiring', () => {
  const source = fs.readFileSync(vmcSenderPath, 'utf8');

  // Contract 1: nativeWebSocketCtor() function exists and borrows from iframe
  assert.ok(
    source.includes('function nativeWebSocketCtor()'),
    'nativeWebSocketCtor function must be defined'
  );
  assert.ok(
    source.includes("probe.contentWindow && probe.contentWindow.WebSocket"),
    'Must borrow WebSocket from iframe contentWindow'
  );
  assert.ok(
    source.includes("data-neko-websocket-probe"),
    'Probe iframe must carry data-neko-websocket-probe attribute for cleanup protection'
  );

  // Contract 2: Socket construction uses borrowed constructor, not window.WebSocket directly
  assert.ok(
    source.includes('new (nativeWebSocketCtor())(websocketUrl())'),
    'WebSocket instantiation must use nativeWebSocketCtor() wrapper'
  );

  // Contract 3: readyState checks use borrowed realm constants, not window.WebSocket constants
  const wsReadyStatePattern = /wsReadyState\(\)\.(OPEN|CLOSING|CLOSED|CONNECTING)/;
  assert.ok(
    wsReadyStatePattern.test(source),
    'readyState comparisons must use wsReadyState() helper to avoid mixed-realm constants'
  );

  // Contract 4: CSP degradation is logged
  assert.ok(
    source.includes('CSP frame-src or sandbox attribute may block same-origin frames'),
    'Must warn when iframe creation fails due to CSP or other restrictions'
  );

  // Contract 5: the CSP fallback must not hand VMC the preload wrapper. When
  // the iframe borrow fails and the top-level constructor is not native, the
  // module has to refuse rather than construct a socket that the Electron
  // preload would register as the desktop chat proxy target. The plain-browser
  // case (native window.WebSocket, iframe blocked by CSP) must still work, so
  // the refusal is gated on the nativeness check, not on the borrow failing.
  assert.ok(
    /if \(!borrowedFromFrame && !looksNative\(ctor\)\) \{/.test(source),
    'the fallback must refuse only when the top-level constructor is not native'
  );
  assert.ok(
    source.includes('hijacking the desktop chat channel'),
    'the refusal must say why VMC is disabled'
  );

  // Contract 6: every socket-opening path consults the block before acting.
  // Missing one would either construct from the wrapper anyway or spin a
  // reconnect timer against a transport that can never become available.
  // Pinning the `if (...) return` shape rather than a bare mention of the
  // helper: a substring check stays green if the call is demoted to a dead
  // `const reason = webSocketTransportBlockedReason();` with the branch
  // deleted. The runtime consequence of that regression is covered by
  // vmc_expression_budget.test.cjs, which drives sample() for real; this is
  // the cheap structural half.
  for (const caller of ['function ensureWebSocket(', 'function scheduleReconnect(', 'function sample(']) {
    const start = source.indexOf(caller);
    assert.ok(start !== -1, `${caller} not found`);
    const body = source.slice(start, start + 700);
    assert.ok(
      /if \(webSocketTransportBlockedReason\(\)\) return/.test(body),
      `${caller} must bail out when the transport is blocked`
    );
  }
});

test('vrm-vmc-sender does not directly reference window.WebSocket constants after initialization', () => {
  const source = fs.readFileSync(vmcSenderPath, 'utf8');

  // Find the extent of nativeWebSocketCtor by proper brace matching
  const functionStart = source.indexOf('function nativeWebSocketCtor()');
  assert.ok(functionStart !== -1, 'nativeWebSocketCtor function not found');

  const openBrace = source.indexOf('{', functionStart);
  assert.ok(openBrace !== -1, 'nativeWebSocketCtor opening brace not found');

  let braceDepth = 0;
  let functionEnd = -1;
  for (let i = openBrace; i < source.length; i++) {
    if (source[i] === '{') braceDepth++;
    else if (source[i] === '}') {
      braceDepth--;
      if (braceDepth === 0) {
        functionEnd = i;
        break;
      }
    }
  }
  assert.ok(functionEnd !== -1, 'nativeWebSocketCtor closing brace not found');

  // Find all WebSocket.OPEN/CLOSING/CLOSED/CONNECTING references
  const directConstantPattern = /\bWebSocket\.(OPEN|CLOSING|CLOSED|CONNECTING)\b/g;
  const matches = [];
  let match;
  while ((match = directConstantPattern.exec(source)) !== null) {
    const lineStart = source.lastIndexOf('\n', match.index) + 1;
    const lineEnd = source.indexOf('\n', match.index);
    const line = source.slice(lineStart, lineEnd);

    // Allow direct references only inside nativeWebSocketCtor() for initial constant capture
    const insideNativeCtor = match.index > functionStart && match.index < functionEnd;

    // Also allow initialization lines like "CONNECTING: window.WebSocket.CONNECTING"
    const isInitLine = /^\s*(CONNECTING|OPEN|CLOSING|CLOSED):\s*window\.WebSocket\.\w+,?\s*$/.test(line);

    if (!insideNativeCtor && !isInitLine) {
      const lineNum = source.slice(0, match.index).split('\n').length;
      matches.push({ line: line.trim(), match: match[0], lineNum });
    }
  }

  assert.strictEqual(
    matches.length,
    0,
    `Found ${matches.length} direct window.WebSocket constant references outside nativeWebSocketCtor(): ${JSON.stringify(matches, null, 2)}`
  );
});

test('chat socket recovers a VMC enable it missed, without breaking lazy loading', () => {
  const source = fs.readFileSync(appWebsocketPath, 'utf8');

  // Contract 1: onopen runs the recovery, so a page that loaded after the
  // one-shot vmc_state_changed broadcast still learns the sender is enabled.
  assert.ok(
    source.includes('function _syncVmcStateOnConnect()'),
    '_syncVmcStateOnConnect must be defined'
  );
  assert.ok(
    /S\.socket\.onopen = function[\s\S]*?_syncVmcStateOnConnect\(\)/.test(source),
    'onopen must call _syncVmcStateOnConnect()'
  );

  // Contract 2: the probe is a bare fetch. Reaching into the lazy facade first
  // would load the full sender on every VRM page and defeat vrm-vmc-loader.js'
  // "no VMC work until someone enables it" contract.
  const fnStart = source.indexOf('function _syncVmcStateOnConnect()');
  const openBrace = source.indexOf('{', fnStart);
  let depth = 0;
  let fnEnd = -1;
  for (let i = openBrace; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) { fnEnd = i; break; }
    }
  }
  assert.ok(fnEnd !== -1, '_syncVmcStateOnConnect closing brace not found');
  const body = source.slice(fnStart, fnEnd);

  assert.ok(
    body.includes("fetch('/api/vmc/status'"),
    'must probe /api/vmc/status directly rather than through the lazy facade'
  );
  assert.ok(
    body.includes('data.enabled !== true'),
    'must bail out when the backend reports VMC disabled, keeping the sender unloaded'
  );

  const enabledGuard = body.indexOf('data.enabled !== true');
  const syncCall = body.indexOf('syncStatusFromBackend()');
  assert.ok(
    enabledGuard !== -1 && syncCall !== -1 && enabledGuard < syncCall,
    'the enabled guard must precede the sender wake-up, or every page loads the sender'
  );

  // Contract 3: a failed probe must not break the chat socket's onopen chain.
  assert.ok(
    /\.catch\(function \(error\) \{[\s\S]*?console\.warn\('\[VMC\] connect-time state sync failed:'/.test(body),
    'probe failures must be caught and logged, not left to reject unhandled'
  );

  // Contract 4: the lazy facade is installed by vrm-vmc-loader.js, which only
  // starts fetching after three-ready. If the chat socket beats it, the
  // recovery must retry once the VRM modules land instead of silently
  // no-opping for the connection's whole lifetime.
  assert.ok(
    body.includes("window.addEventListener('vrm-modules-ready'"),
    'a missing facade must arm a one-shot retry on vrm-modules-ready, not silently return'
  );
});

test('expression retirements get a reserved per-frame quota', () => {
  const source = fs.readFileSync(vmcSenderPath, 'utf8');

  // Contract 1: a quota constant exists and is smaller than the frame cap.
  const quotaMatch = source.match(/const RETIREMENT_QUOTA_PER_FRAME = (\d+);/);
  assert.ok(quotaMatch, 'RETIREMENT_QUOTA_PER_FRAME must be defined');
  const capMatch = source.match(/const MAX_EXPRESSIONS_PER_FRAME = (\d+);/);
  assert.ok(capMatch, 'MAX_EXPRESSIONS_PER_FRAME must be defined');
  assert.ok(
    Number(quotaMatch[1]) > 0 && Number(quotaMatch[1]) < Number(capMatch[1]),
    'the retirement quota must reserve part of the frame, not all or none of it'
  );

  // Contract 2: the live loop stops at the reduced cap, otherwise a model with
  // MAX_EXPRESSIONS_PER_FRAME expressions strands the previous model's weights.
  // The reservation is min(quota, pending retirements): a single retiring
  // name must not displace a whole quota's worth of live expressions.
  assert.ok(
    /const liveCap = MAX_EXPRESSIONS_PER_FRAME - Math\.min\(\s*RETIREMENT_QUOTA_PER_FRAME,\s*state\.retiringExpressionNames\.size\s*\)/.test(source),
    'liveCap must reserve only the slots pending retirements actually need'
  );
  assert.ok(
    /for \(const name of state\.knownExpressionNames\) \{\s*\n\s*if \(state\.exprBuf\.length >= liveCap\) break;/.test(source),
    'the live expression loop must break on liveCap, not the raw frame cap'
  );

  // Contract 3: the retirement loop still fills up to the full frame cap.
  assert.ok(
    /for \(const name of state\.retiringExpressionNames\) \{[\s\S]*?if \(state\.exprBuf\.length >= MAX_EXPRESSIONS_PER_FRAME\) break;/.test(source),
    'the retirement loop must be bounded by the full frame cap'
  );

  // Contract 4: no stale comment claiming retirements can never be starved.
  assert.ok(
    !source.includes('MAX_EXPRESSIONS_PER_FRAME cannot starve them'),
    'the superseded starvation comment must not survive the quota change'
  );

  // Contract 5: same-name retirements are pruned before liveCap is computed.
  // A name the new model also uses never enters the ack list, so leaving it
  // in the set would permanently hold a reservation it can never spend.
  assert.ok(
    /for \(const name of state\.retiringExpressionNames\) \{\s*\n\s*if \(state\.knownExpressionNames\.has\(name\)\) \{\s*\n\s*state\.retiringExpressionNames\.delete\(name\);/.test(source),
    'same-name retirements must be deleted from the set, or they squat the reservation forever'
  );
});

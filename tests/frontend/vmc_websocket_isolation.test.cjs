const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '..', '..');
const vmcSenderPath = path.join(projectRoot, 'static/vrm/vrm-vmc-sender.js');

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

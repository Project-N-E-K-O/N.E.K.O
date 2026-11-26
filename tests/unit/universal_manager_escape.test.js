const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '../..');
const universalManagerSource = fs.readFileSync(
  path.join(repoRoot, 'static/tutorial/core/universal-manager.js'),
  'utf8',
);

function getMethodBlock(source, methodName) {
  const start = source.indexOf(`    ${methodName}(`);
  assert.notEqual(start, -1, `Method ${methodName} not found`);
  let index = source.indexOf('{', start);
  let depth = 0;
  for (; index < source.length; index += 1) {
    const ch = source[index];
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Unclosed method ${methodName}`);
}

test('F0486: safeEscapeHtml escapes quotes and HTML delimiters in attributes and text', () => {
  const safeEscapeSrc = getMethodBlock(universalManagerSource, 'safeEscapeHtml');
  const modalSrc = getMethodBlock(universalManagerSource, 'showDay1SystrayIntroModal');

  const overlay = {
    setAttribute() {},
    addEventListener() {},
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  const document = {
    createElement() { return overlay; },
    body: { appendChild() {}, classList: { add() {} } },
  };
  const window = { homeTutorialPlatformCapabilities: { detectPlatform: () => 'windows' } };
  const manager = new Function('window', 'document', `return ({${safeEscapeSrc},\n${modalSrc}});`)(window, document);

  assert.equal(manager.safeEscapeHtml('a"b\'c<d>e&f'), 'a&quot;b&#39;c&lt;d&gt;e&amp;f');
  assert.equal(manager.safeEscapeHtml(null), '');
  assert.equal(manager.safeEscapeHtml(undefined), '');

  const payload = 'x"><img src=x onerror=alert(1)>';
  manager.shouldShowDay1SystrayIntro = () => true;
  manager.closeDay1SystrayIntroModal = () => {};
  manager.t = (key, fallback) => {
    if (key === 'common.close' || key === 'tutorial.systray.location.alt') {
      return payload;
    }
    return fallback;
  };

  manager.showDay1SystrayIntroModal({ reason: 'complete' }, { day: 1 });
  const expectedEscaped = 'x&quot;&gt;&lt;img src=x onerror=alert(1)&gt;';
  assert.ok(overlay.innerHTML.includes(`aria-label="${expectedEscaped}"`));
  assert.ok(overlay.innerHTML.includes(`alt="${expectedEscaped}"`));
  assert.ok(!overlay.innerHTML.includes('<img src=x'));
});

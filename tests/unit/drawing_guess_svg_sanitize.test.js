const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../..');
const drawingGuessSource = fs.readFileSync(
  path.join(repoRoot, 'static/game/games/drawing_guess/drawing-guess.js'),
  'utf8',
);

function extractSection(startMarker, endMarker) {
  const start = drawingGuessSource.indexOf(startMarker);
  assert.notEqual(start, -1, `Could not find ${startMarker}`);
  const end = drawingGuessSource.indexOf(endMarker, start);
  assert.notEqual(end, -1, `Could not find ${endMarker} after ${startMarker}`);
  return drawingGuessSource.slice(start, end);
}

// Lightweight XML/SVG DOM + DOMParser/XMLSerializer stub (Node built-ins only)
function createSvgDomHarness({ includeParser = true } = {}) {
  class FakeSvgNode {
    constructor(tagName, namespaceURI = 'http://www.w3.org/2000/svg') {
      this.nodeType = 1;
      this.tagName = tagName;
      this.localName = tagName;
      this.namespaceURI = namespaceURI;
      this.attributes = [];
      this.childNodes = [];
      this.style = {};
    }
    setAttribute(name, value) {
      const str = String(value);
      const existing = this.attributes.find((a) => a.name === name);
      if (existing) existing.value = str;
      else this.attributes.push({ name, value: str });
    }
    getAttribute(name) {
      const found = this.attributes.find((a) => a.name === name);
      return found ? found.value : null;
    }
    removeAttribute(name) {
      this.attributes = this.attributes.filter((a) => a.name !== name);
    }
    appendChild(child) {
      this.childNodes.push(child);
      return child;
    }
    get children() {
      return this.childNodes.filter((c) => c.nodeType === 1);
    }
    querySelector(selector) {
      const target = selector.toLowerCase();
      if (this.tagName.toLowerCase() === target) return this;
      for (const child of this.children) {
        const hit = child.querySelector(selector);
        if (hit) return hit;
      }
      return null;
    }
    querySelectorAll(selector) {
      const targets = selector.split(',').map((s) => s.trim().toLowerCase());
      const out = [];
      const walk = (node) => {
        for (const child of node.children) {
          if (targets.includes(child.tagName.toLowerCase())) out.push(child);
          walk(child);
        }
      };
      walk(this);
      return out;
    }
    getElementsByTagName(name) {
      return this.querySelectorAll(name);
    }
    get outerHTML() {
      const attrs = this.attributes.map((a) => ` ${a.name}="${a.value.replace(/"/g, '&quot;')}"`).join('');
      const inner = this.children.map((c) => c.outerHTML).join('');
      return `<${this.tagName}${attrs}>${inner}</${this.tagName}>`;
    }
  }

  function parseXmlSimple(markup) {
    const tokens = [];
    const tagRe = /<(\/?)([A-Za-z0-9:_-]+)([^>]*?)(\/?)>/g;
    let lastIndex = 0;
    let match;
    while ((match = tagRe.exec(markup)) !== null) {
      if (match.index > lastIndex) {
        const text = markup.slice(lastIndex, match.index);
        if (text.trim()) tokens.push({ type: 'text', text });
      }
      const isClose = match[1] === '/';
      const tagName = match[2];
      const attrStr = match[3];
      const selfClose = match[4] === '/';
      tokens.push({ type: isClose ? 'close' : 'open', tagName, attrStr, selfClose });
      lastIndex = tagRe.lastIndex;
    }
    const rootWrapper = new FakeSvgNode('document');
    const stack = [rootWrapper];
    for (const tok of tokens) {
      if (tok.type === 'text') {
        stack[stack.length - 1].childNodes.push({ nodeType: 3, textContent: tok.text });
        continue;
      }
      if (tok.type === 'close') {
        if (stack.length <= 1 || stack[stack.length - 1].tagName.toLowerCase() !== tok.tagName.toLowerCase()) {
          const errDoc = new FakeSvgNode('html');
          errDoc.appendChild(new FakeSvgNode('parsererror'));
          return { documentElement: errDoc, getElementsByTagName: (n) => errDoc.getElementsByTagName(n) };
        }
        stack.pop();
        continue;
      }
      const node = new FakeSvgNode(tok.tagName);
      const attrRe = /([A-Za-z0-9:_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
      let am;
      while ((am = attrRe.exec(tok.attrStr)) !== null) {
        node.setAttribute(am[1], am[2] !== undefined ? am[2] : am[3]);
      }
      stack[stack.length - 1].appendChild(node);
      if (!tok.selfClose) stack.push(node);
    }
    if (stack.length !== 1) {
      const errDoc = new FakeSvgNode('html');
      errDoc.appendChild(new FakeSvgNode('parsererror'));
      return { documentElement: errDoc, getElementsByTagName: (n) => errDoc.getElementsByTagName(n) };
    }
    const documentElement = rootWrapper.children[0] || null;
    return {
      documentElement,
      getElementsByTagName: (name) => (documentElement ? documentElement.getElementsByTagName(name) : []),
    };
  }

  const aiDrawingEl = new FakeSvgNode('div');
  aiDrawingEl.classList = { remove() {}, add() {} };
  let rawAiInnerHtmlAssigned = false;
  Object.defineProperty(aiDrawingEl, 'innerHTML', {
    get() {
      return this.children.map((c) => c.outerHTML).join('');
    },
    set(val) {
      if (String(val)) rawAiInnerHtmlAssigned = true;
      this.childNodes = [];
    },
  });

  const summaryAiArtEl = new FakeSvgNode('div');
  let rawSummaryInnerHtmlAssigned = false;
  Object.defineProperty(summaryAiArtEl, 'innerHTML', {
    get() {
      return this.children.map((c) => c.outerHTML).join('');
    },
    set(val) {
      if (String(val)) rawSummaryInnerHtmlAssigned = true;
      this.childNodes = [];
    },
  });

  const summaryEl = new FakeSvgNode('div');
  summaryEl.classList = { toggle() {}, add() {}, remove() {} };
  summaryEl.querySelector = (sel) => (sel.startsWith('[data-summary-ai-art=') ? summaryAiArtEl : null);
  summaryEl.querySelectorAll = () => [];

  const downloads = [];
  const sanitizerSection = extractSection("  var AI_SVG_NS = 'http://www.w3.org/2000/svg';", '  function parseSvgViewBox(');
  const summarySection = extractSection('  function renderSummaryList(', '  function escapeHtml(value)');
  const saveSection = extractSection('  function saveAiSvgFile(', '  function saveAiPngFile(');
  const escapeSection = extractSection('  function escapeHtml(value)', '  function downloadBlob(');

  const sandbox = {
    document: {
      createElementNS: (ns, tag) => new FakeSvgNode(tag, ns),
    },
    window: { matchMedia: () => ({ matches: true }) },
    requestAnimationFrame: () => {},
    els: { aiDrawing: aiDrawingEl, summary: summaryEl },
    state: { aiSvg: '' },
    hideAllStageViews: () => {},
    fitAiDrawingSvgToContent: () => {},
    serializeAiDrawingSvg: (el) => (el.querySelector('svg') ? el.querySelector('svg').outerHTML : ''),
    animateAiDrawing: () => {},
    t: (_k, fb) => fb,
    currentLanguage: () => 'en',
    downloadBlob: (filename, content, type) => downloads.push({ filename, content, type }),
    XMLSerializer: class XMLSerializer {
      serializeToString(node) {
        return node.outerHTML;
      }
    },
  };
  if (includeParser) {
    sandbox.DOMParser = class DOMParser {
      parseFromString(str) {
        return parseXmlSimple(str);
      }
    };
  }

  vm.runInNewContext(`${sanitizerSection}\n${summarySection}\n${escapeSection}\n${saveSection}`, sandbox);
  return {
    sandbox,
    aiDrawingEl,
    summaryAiArtEl,
    downloads,
    wasRawAiInnerHtmlAssigned: () => rawAiInnerHtmlAssigned,
    wasRawSummaryInnerHtmlAssigned: () => rawSummaryInnerHtmlAssigned,
  };
}

test('F04B-002: drawing-guess sanitizes AI SVG across showAiDrawing, renderSummaryList, and saveAiSvgFile', () => {
  const harness = createSvgDomHarness({ includeParser: true });
  const { sandbox, aiDrawingEl, summaryAiArtEl, downloads } = harness;

  const maliciousSvg = [
    '<svg viewBox="0 0 240 180" onload="alert(1)">',
    '<script>alert(1)</script>',
    '<foreignObject><body onload="alert(1)"></body></foreignObject>',
    '<image href="https://evil.test/x"/>',
    '<a xlink:href="javascript:alert(1)"><circle cx="10" cy="10" r="5" fill="#112233"/></a>',
    '<animate onbegin="alert(1)"/>',
    '<g transform="translate(2, 3)">',
    '<path d="M0 0 L10 10" onload="alert(1)" style="background:url(javascript:alert(1))" fill="url(javascript:alert(1))" stroke="#112233"/>',
    '<rect x="5" y="5" width="20" height="20" fill="none" stroke="#223344"/>',
    '</g>',
    '</svg>',
  ].join('');

  sandbox.showAiDrawing(maliciousSvg);
  assert.equal(harness.wasRawAiInnerHtmlAssigned(), false);
  const rendered = aiDrawingEl.innerHTML;
  assert.ok(rendered.includes('<path'));
  assert.ok(rendered.includes('d="M0 0 L10 10"'));
  assert.ok(rendered.includes('stroke="#112233"'));
  assert.ok(rendered.includes('<rect'));
  assert.ok(!rendered.includes('<script'));
  assert.ok(!rendered.includes('<foreignObject'));
  assert.ok(!rendered.includes('<image'));
  assert.ok(!rendered.includes('<animate'));
  assert.ok(!rendered.includes('onload'));
  assert.ok(!rendered.includes('onbegin'));
  assert.ok(!rendered.includes('javascript:'));
  assert.ok(!rendered.includes('style='));

  sandbox.renderSummaryList([{ round: 1, aiSvg: maliciousSvg }], false);
  assert.equal(harness.wasRawSummaryInnerHtmlAssigned(), false);
  const summaryRendered = summaryAiArtEl.innerHTML;
  assert.ok(summaryRendered.includes('<path'));
  assert.ok(!summaryRendered.includes('<script'));
  assert.ok(!summaryRendered.includes('onload'));

  sandbox.saveAiSvgFile(maliciousSvg, 'out.svg');
  assert.equal(downloads.length, 1);
  assert.ok(downloads[0].content.includes('<path'));
  assert.ok(!downloads[0].content.includes('<script'));
  assert.ok(!downloads[0].content.includes('javascript:'));

  // Malformed XML fails closed
  assert.equal(sandbox.sanitizeAiDrawingSvgElement('<svg><img src=x onerror=alert(1)></svg>'), null);
  assert.equal(sandbox.sanitizeAiDrawingSvgMarkup('<svg><unclosed>'), '');
});

test('F04B-002: sanitizer fails closed when DOMParser is unavailable', () => {
  const harness = createSvgDomHarness({ includeParser: false });
  assert.equal(harness.sandbox.sanitizeAiDrawingSvgElement('<svg><path d="M0 0"/></svg>'), null);
  harness.sandbox.showAiDrawing('<svg><script>alert(1)</script></svg>');
  assert.equal(harness.wasRawAiInnerHtmlAssigned(), false);
  assert.equal(harness.aiDrawingEl.children.length, 0);
});

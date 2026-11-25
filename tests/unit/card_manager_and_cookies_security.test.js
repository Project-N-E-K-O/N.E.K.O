const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../..');
const coreUploadSource = fs.readFileSync(
  path.join(repoRoot, 'static/js/character_card_manager/core-and-upload.js'),
  'utf8',
);
const subscriptionsSource = fs.readFileSync(
  path.join(repoRoot, 'static/js/character_card_manager/subscriptions-and-scan.js'),
  'utf8',
);
const cookiesLoginSource = fs.readFileSync(
  path.join(repoRoot, 'static/js/cookies_login.js'),
  'utf8',
);

function extractFunction(source, signature, nextAnchor) {
  const start = source.indexOf(signature);
  assert.notEqual(start, -1, `Could not find ${signature}`);
  const end = source.indexOf(nextAnchor, start + signature.length);
  assert.notEqual(end, -1, `Could not find ${nextAnchor} after ${signature}`);
  return source.slice(start, end);
}

function createFakeDom() {
  class FakeTextNode {
    constructor(text) {
      this.nodeType = 3;
      this.textContent = String(text);
      this.parentElement = null;
    }
  }

  class FakeElement {
    constructor(tagName) {
      this.nodeType = 1;
      this.tagName = tagName.toUpperCase();
      this.className = '';
      this.id = '';
      this.title = '';
      this.src = '';
      this.alt = '';
      this.attributes = Object.create(null);
      this.dataset = Object.create(null);
      this.children = [];
      this.listeners = Object.create(null);
      this.parentElement = null;
      this._textContent = '';
      this._innerHTML = '';
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === 'id') this.id = String(value);
      if (name === 'class') this.className = String(value);
    }
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    }
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    }
    append(...nodes) {
      for (const node of nodes) this.appendChild(node);
    }
    insertBefore(child, ref) {
      child.parentElement = this;
      const idx = this.children.indexOf(ref);
      if (idx === -1) this.children.push(child);
      else this.children.splice(idx, 0, child);
      return child;
    }
    replaceChildren(...nodes) {
      this.children = [];
      this._innerHTML = '';
      for (const node of nodes) this.appendChild(node);
    }
    remove() {
      if (!this.parentElement) return;
      const idx = this.parentElement.children.indexOf(this);
      if (idx !== -1) this.parentElement.children.splice(idx, 1);
      this.parentElement = null;
    }
    addEventListener(type, handler) {
      if (!this.listeners[type]) this.listeners[type] = [];
      this.listeners[type].push(handler);
    }
    click() {
      for (const handler of this.listeners.click || []) handler({ target: this });
      if (typeof this.onclick === 'function') this.onclick({ target: this });
    }
    set innerHTML(val) {
      this._innerHTML = String(val);
      this.children = [];
    }
    get innerHTML() {
      return this._innerHTML;
    }
    set textContent(val) {
      this._textContent = String(val);
      this.children = [];
    }
    get textContent() {
      if (this.children.length > 0) {
        return this.children.map((c) => c.textContent).join('');
      }
      return this._textContent;
    }
    querySelectorAll(selector) {
      const cls = selector.startsWith('.') ? selector.slice(1) : null;
      const results = [];
      const walk = (node) => {
        for (const child of node.children || []) {
          if (child.nodeType === 1) {
            if (cls && String(child.className).split(/\s+/).includes(cls)) results.push(child);
            if (!cls && child.tagName.toLowerCase() === selector.toLowerCase()) results.push(child);
            walk(child);
          }
        }
      };
      walk(this);
      return results;
    }
    get firstChild() {
      return this.children[0] || null;
    }
  }

  const byId = new Map();
  const document = {
    createElement(tag) {
      return new FakeElement(tag);
    },
    createElementNS(_ns, tag) {
      return new FakeElement(tag);
    },
    createTextNode(text) {
      return new FakeTextNode(text);
    },
    getElementById(id) {
      return byId.get(id) || null;
    },
    registerElement(id, el) {
      el.id = id;
      byId.set(id, el);
      return el;
    },
  };
  return { document, FakeElement, FakeTextNode };
}

test('F0051: addTag constructs DOM nodes safely without HTML/attribute injection', () => {
  const { document } = createFakeDom();
  const container = document.registerElement('tags-container', document.createElement('div'));
  const cardContainer = document.registerElement('character-card-tags-container', document.createElement('div'));
  const messages = [];

  const addTagSource = extractFunction(coreUploadSource, 'function addTag(', 'function removeTag(');
  const removeTagSource = extractFunction(coreUploadSource, 'function removeTag(', '\nfunction ');
  const context = {
    document,
    window: {
      t: (key) => (key === 'steam.customTemplateTagLocked' ? '" onmouseover="alert(1)' : key),
    },
    console,
    requestAnimationFrame: () => {},
    updateCharacterCardTagScrollControls: () => {},
    showMessage: (msg, type) => messages.push({ msg, type }),
  };
  vm.runInNewContext(`${addTagSource}\n${removeTagSource}`, context);

  // Payload length 22 <= 30 chars
  const xssTag = '<img src=x onerror=y>';
  context.addTag(xssTag);
  assert.equal(container.children.length, 1);
  const tagEl = container.children[0];
  assert.equal(tagEl.innerHTML, '');
  assert.equal(tagEl.querySelectorAll('img').length, 0);
  assert.equal(tagEl.textContent.replace('×', '').replace('🔒', '').trim(), xssTag);

  // Clicking the remove span removes the tag
  const removeBtn = tagEl.querySelectorAll('.tag-remove')[0];
  assert.ok(removeBtn);
  removeBtn.click();
  assert.equal(container.children.length, 0);

  // Character-card tag branch
  context.addTag(xssTag, 'character-card');
  assert.equal(cardContainer.children.length, 1);
  assert.equal(cardContainer.children[0].querySelectorAll('img').length, 0);
  assert.equal(cardContainer.children[0].textContent.replace('×', '').replace('🔒', '').trim(), xssTag);

  // Locked tag with quote-containing translation
  context.addTag('locked-tag', '', true);
  assert.equal(container.children.length, 1);
  const lockIcon = container.children[0].querySelectorAll('.tag-locked-icon')[0];
  assert.equal(lockIcon.title, '" onmouseover="alert(1)');
  assert.equal(container.children[0].innerHTML, '');
});

test('F0450: requestQR renders upstream error detail via DOM textContent without XSS', async () => {
  const { document } = createFakeDom();
  const qrLoginBox = document.registerElement('QRLogin', document.createElement('div'));

  const helpers = [
    extractFunction(cookiesLoginSource, 'const safeT = ', '\nfunction openCredentialGuide('),
    'const CJK_CHAR_RE = /[\\u3400-\\u9fff]/u;',
    extractFunction(cookiesLoginSource, 'const createLocalizedError =', '\nfunction shouldUseRawApiMessage('),
    extractFunction(cookiesLoginSource, 'function shouldUseRawApiMessage(', '\nfunction getLocalizedApiMessage('),
    extractFunction(cookiesLoginSource, 'function getLocalizedApiMessage(', '\nfunction getQrStatusMessage('),
    extractFunction(cookiesLoginSource, 'function renderQrSessionError(', '\nfunction sanitizeQrImageSrc('),
    extractFunction(cookiesLoginSource, 'function sanitizeQrImageSrc(', '\nasync function requestQR('),
    extractFunction(cookiesLoginSource, 'async function requestQR(', '\nfunction startQrPoll('),
  ].join('\n');

  let fetchMode = 'error';
  let requestQrCalls = 0;
  const context = {
    document,
    window: { location: { origin: 'http://localhost:48911' }, i18next: { language: 'en' } },
    URL,
    AbortController,
    console,
    getTranslator: () => null,
    currentPlatform: 'xiaohongshu',
    PLATFORM_CONFIG: { xiaohongshu: { name: 'Xiaohongshu' } },
    qrRefreshTimeout: null,
    qrEntryGeneration: 0,
    qrRequestGeneration: 0,
    qrRequestAbortController: null,
    currentQrKey: null,
    cancelQrRequest: () => {},
    stopQrPoll: () => {},
    showQRLogin: () => {},
    startQrPoll: () => {},
    clearTimeout,
    fetch: async () => {
      requestQrCalls += 1;
      if (fetchMode === 'error') {
        return {
          ok: false,
          json: async () => ({ detail: '<img src=x onerror=alert(1)>' }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: {
            qrcode_key: 'k1',
            qrcode_image: 'javascript:alert(1)',
            timeout: 120,
          },
        }),
      };
    },
  };
  vm.runInNewContext(helpers, context);

  await context.requestQR({ name: 'Xiaohongshu' }, 'xiaohongshu');
  assert.equal(qrLoginBox.innerHTML, '');
  assert.equal(qrLoginBox.querySelectorAll('img').length, 0);
  const errBox = qrLoginBox.querySelectorAll('.qr-session-error')[0];
  assert.ok(errBox);
  assert.ok(errBox.textContent.includes('<img src=x onerror=alert(1)>'));

  // Retry button triggers requestQR again; unsafe javascript: QR image URL is rejected
  fetchMode = 'unsafe_img';
  const retryBtn = errBox.querySelectorAll('.qr-session-retry')[0];
  assert.ok(retryBtn);
  retryBtn.click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(requestQrCalls, 2);
  const img = qrLoginBox.querySelectorAll('img')[0];
  assert.ok(img);
  assert.equal(img.src, '');

  // Safe data:image/png;base64 and https URLs are accepted
  assert.equal(context.sanitizeQrImageSrc('data:image/png;base64,iVBORw0KGgo='), 'data:image/png;base64,iVBORw0KGgo=');
  assert.equal(context.sanitizeQrImageSrc('https://example.com/qr.png'), 'https://example.com/qr.png');
  assert.equal(context.sanitizeQrImageSrc('javascript:alert(1)'), '');
});

test('R1111-P3-06: subscriptions-and-scan escapes errors, tags, status, and preview URLs', async () => {
  const { document } = createFakeDom();
  const listEl = document.registerElement('subscriptions-list', document.createElement('div'));
  const modalTitle = document.registerElement('modalTitle', document.createElement('h3'));
  const detailContent = document.registerElement('itemDetailContent', document.createElement('div'));

  const escapeHtmlSrc = extractFunction(coreUploadSource, 'function escapeHtml(', '\n\n// 加载状态管理器');
  const preamble = [
    extractFunction(subscriptionsSource, 'function normalizeSubscriptionText(', '\nfunction renderSubscriptionsEmptyState('),
    extractFunction(subscriptionsSource, 'function renderSubscriptionsEmptyState(', '\nfunction sanitizeWorkshopPreviewUrl('),
    extractFunction(subscriptionsSource, 'function sanitizeWorkshopPreviewUrl(', '\n// 加载订阅物品'),
    extractFunction(subscriptionsSource, 'function loadSubscriptions(', '\n// 渲染当前页的订阅物品'),
    extractFunction(subscriptionsSource, 'function renderSubscriptionsPage(', '\nfunction formatWorkshopCharacterNameList('),
    extractFunction(subscriptionsSource, 'function viewItemDetails(', '\n// 取消订阅功能'),
  ].join('\n');

  let fetchImpl = async () => ({
    ok: true,
    json: async () => ({ success: false, error: '<img src=x onerror=alert(1)>' }),
  });

  const context = {
    document,
    window: { t: null },
    URL,
    console,
    allSubscriptions: [],
    currentPage: 1,
    itemsPerPage: 10,
    totalPages: 1,
    showMessage: () => {},
    updatePagination: () => {},
    sortSubscriptions: () => {},
    formatFileSize: () => '1 KB',
    getStatusText: () => '<img src=x onerror=status>',
    openModal: () => {},
    fetch: (...args) => fetchImpl(...args),
  };
  vm.runInNewContext(`${escapeHtmlSrc}\n${preamble}`, context);

  context.loadSubscriptions();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(listEl.innerHTML, '');
  assert.equal(listEl.querySelectorAll('img').length, 0);
  assert.ok(listEl.textContent.includes('<img src=x onerror=alert(1)>'));

  // Rejected fetch path
  fetchImpl = async () => {
    throw new Error('<svg onload=alert(1)>');
  };
  context.loadSubscriptions();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(listEl.innerHTML, '');
  assert.ok(listEl.textContent.includes('<svg onload=alert(1)>'));

  // Preview URL sanitizer
  assert.equal(context.sanitizeWorkshopPreviewUrl('javascript:alert(1)'), '../static/icons/Steam_icon_logo.png');
  assert.equal(context.sanitizeWorkshopPreviewUrl('data:text/html,<script>alert(1)</script>'), '../static/icons/Steam_icon_logo.png');
  assert.equal(context.sanitizeWorkshopPreviewUrl('https://cdn.example.com/a b.png'), 'https://cdn.example.com/a%20b.png');

  // renderSubscriptionsPage escapes previewUrl, voiceClone data-item-id, and statusText
  context.allSubscriptions = [
    {
      publishedFileId: "123');alert(1);//",
      title: '" onmouseover="alert(1)',
      authorName: '<b onmouseover=alert(1)>Author</b>',
      previewUrl: 'javascript:alert(1)',
      voiceReferenceAvailable: true,
      voiceReference: { displayName: '" onclick="x' },
      state: { installed: true },
    },
  ];
  context.renderSubscriptionsPage();
  assert.ok(!listEl.innerHTML.includes('javascript:alert(1)'));
  assert.ok(!listEl.innerHTML.includes("openWorkshopVoiceClone('123'"));
  assert.ok(listEl.innerHTML.includes('openWorkshopVoiceClone(this.dataset.itemId)'));
  assert.ok(listEl.innerHTML.includes('../static/icons/Steam_icon_logo.png'));

  // viewItemDetails escapes tags, title alt, previewUrl, statusText
  fetchImpl = async () => ({
    ok: true,
    json: async () => ({
      success: true,
      item: {
        publishedFileId: 99,
        title: '" onclick="alert(1)',
        authorName: '<script>x</script>',
        previewUrl: 'javascript:alert(1)',
        timeAdded: 1700000000,
        timeUpdated: 1700000000,
        fileSize: 1024,
        tags: ['<img src=x onerror=alert(1)>'],
        state: { installed: true },
      },
    }),
  });
  context.viewItemDetails('99');
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(modalTitle.textContent, '" onclick="alert(1)');
  assert.ok(!detailContent.innerHTML.includes('javascript:alert(1)'));
  assert.ok(!detailContent.innerHTML.includes('<img src=x onerror=alert(1)>'));
  assert.ok(detailContent.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(detailContent.innerHTML.includes('&lt;img src=x onerror=status&gt;'));
  assert.ok(detailContent.innerHTML.includes('alt="&quot; onclick=&quot;alert(1)"'));

  // Non-string inputs to escapeHtml and non-string JSON array payloads
  assert.equal(context.escapeHtml(null), '');
  assert.equal(context.escapeHtml(undefined), '');
  assert.equal(context.escapeHtml(123), '123');
  assert.equal(context.escapeHtml(['<b>x</b>']), '&lt;b&gt;x&lt;/b&gt;');

  context.allSubscriptions = [
    {
      publishedFileId: 2,
      title: ['<img src=x onerror="window.__xss_title_arr=1">'],
      authorName: ['<b>Auth</b>'],
      description: ['<svg onload="window.__xss_desc_arr=1">'],
      voiceReferenceAvailable: true,
      voiceReference: { displayName: ['"><img src=x onerror="window.__xss=97">'] },
      state: { installed: true },
    },
  ];
  context.renderSubscriptionsPage();
  assert.ok(!listEl.innerHTML.includes('<img src=x onerror="window.__xss_title_arr=1">'));
  assert.ok(!listEl.innerHTML.includes('"><img src=x onerror="window.__xss=97">'));
  assert.ok(listEl.innerHTML.includes('&lt;img src=x onerror=&quot;window.__xss_title_arr=1&quot;&gt;'));
  assert.ok(listEl.innerHTML.includes('&quot;&gt;&lt;img src=x onerror=&quot;window.__xss=97&quot;&gt;'));
  assert.ok(listEl.innerHTML.includes('&lt;b&gt;Auth&lt;/b&gt;'));

  fetchImpl = async () => ({
    ok: true,
    json: async () => ({
      success: true,
      item: {
        publishedFileId: 9,
        title: ['<img src=x onerror="window.__xss_detail_title=1">'],
        description: ['<script>alert(1)</script>'],
        tags: [['<img src=x onerror="window.__xss_tag_arr=1">']],
        state: { installed: true },
      },
    }),
  });
  context.viewItemDetails('9');
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(!detailContent.innerHTML.includes('<img src=x onerror="window.__xss_tag_arr=1">'));
  assert.ok(detailContent.innerHTML.includes('&lt;img src=x onerror=&quot;window.__xss_tag_arr=1&quot;&gt;'));
  assert.ok(detailContent.innerHTML.includes('&lt;img src=x onerror=&quot;window.__xss_detail_title=1&quot;&gt;'));
  assert.ok(detailContent.innerHTML.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
});

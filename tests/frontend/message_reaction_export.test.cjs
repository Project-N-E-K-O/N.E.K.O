const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-chat-export.js'), 'utf8');
const body = '谢谢你一直陪着我';
const label = 'Neko reacted with ❤️';

function message(overrides = {}) {
  return { id: 'user-1', role: 'user', author: 'You', time: '10:00', status: 'sent',
    blocks: [{ type: 'text', text: body }], reaction: { emoji: '❤️', author: 'Neko' }, ...overrides };
}

function fixture(messages = [message()]) {
  const blobs = new Map();
  const downloads = [];
  const clipboard = [];
  const frames = [];
  const errors = [];
  let sequence = 0;
  const host = { messages };
  const url = Object.assign(class extends URL {}, { createObjectURL(blob) { const id = `blob:test-${++sequence}`; blobs.set(id, blob); return id; },
    revokeObjectURL() {} });
  const document = {
    readyState: 'complete', documentElement: { lang: 'en', getAttribute() { return 'light'; } },
    getElementById() { return null; }, querySelector() { return null; }, body: { appendChild() {} },
    createElement(tag) {
      if (tag === 'canvas') {
        const drawn = [];
        const context = new Proxy({
          measureText(text) { return { width: Array.from(String(text)).length * 8 }; },
          fillText(text) { drawn.push(String(text)); },
          createLinearGradient() { return { addColorStop() {} }; },
          createRadialGradient() { return { addColorStop() {} }; },
        }, { get(target, key) { return key in target ? target[key] : () => {}; } });
        return { getContext() { return context; }, toBlob(callback, type) {
          callback(new Blob([JSON.stringify(drawn)], { type }));
        } };
      }
      if (tag === 'a') return { style: {}, click() { downloads.push(blobs.get(this.href)); }, remove() {} };
      throw new Error(`Unexpected element: ${tag}`);
    },
  };
  const window = { document, URL: url, location: { href: 'http://localhost/chat' },
    reactChatWindowHost: { getState() { return host; } },
    matchMedia() { return { matches: false }; }, addEventListener() {} };
  class Image {
    constructor() { this.width = this.height = this.naturalWidth = this.naturalHeight = 32; }
    set src(value) { if (value) queueMicrotask(() => this.onload?.()); }
  }
  const context = vm.createContext({ window, document, Blob, URL: url, Image, AbortController,
    console: { error(...args) { errors.push(args); } },
    fetch: async () => { throw new Error('No network in export tests'); },
    navigator: { clipboard: { async writeText(value) { clipboard.push(value); },
      async write(items) { clipboard.push(items[0].data['image/png']); } } },
    ClipboardItem: class { constructor(data) { this.data = data; } },
    requestAnimationFrame(callback) { frames.push(callback); },
    setTimeout(callback, delay) { if (delay === 1000) return 0; return setTimeout(callback, delay); },
    clearTimeout,
  });
  // Expose private state only in the test VM; run the production exporter unchanged otherwise.
  vm.runInContext(source.replace('    window.appChatExport = {',
    '    window.testExport = { state, buildExportEntry, getSelectedEntries, getOrBuildPreviewPayload, '
      + 'setPreviewFunctions(open, modal) { openExportPreviewWindow = open; openPreviewModal = modal; } };\n'
      + '    window.appChatExport = {'), context);
  return { api: window.appChatExport, internal: window.testExport, host, blobs, downloads, clipboard, frames, errors };
}

test('normal Markdown preview, copy and download preserve body and reacting character', async () => {
  const f = fixture();
  const options = { messageIds: ['user-1'], format: 'markdown' };
  const preview = await f.api.buildCompactInlinePreview(options);
  assert.equal(preview.previewKind, 'document');
  assert.ok(preview.previewDocument.includes(body));
  assert.ok(preview.previewDocument.includes(label));
  await f.api.copyCompactInlineSelection(options);
  await f.api.downloadCompactInlineSelection(options);
  for (const content of [f.clipboard[0], await f.downloads[0].text()]) {
    assert.ok(content.includes(body));
    assert.ok(content.includes(`> ${label}`));
  }
  assert.equal(f.errors.length, 0);
});

for (const imageStyle of ['neko', 'original', 'poster', 'lyrics']) {
  test(`${imageStyle} normal image preview, copy and download draw the reaction`, async () => {
    const f = fixture();
    const options = { messageIds: ['user-1'], format: 'image', imageStyle, imageFormat: 'png' };
    const preview = await f.api.buildCompactInlinePreview(options);
    assert.equal(preview.previewKind, 'image');
    await f.api.copyCompactInlineSelection(options);
    await f.api.downloadCompactInlineSelection(options);
    for (const blob of [f.blobs.get(preview.previewUrl), f.clipboard[0], f.downloads[0]]) {
      const drawn = JSON.parse(await blob.text()).join('\n');
      assert.ok(drawn.includes(body), 'message body must remain intact');
      assert.ok(drawn.includes(label), 'image must draw the character and complete emoji');
    }
    assert.equal(f.errors.length, 0);
  });
}

for (const imageFormat of ['jpeg', 'webp']) {
  test(`${imageFormat} downloads retain the reaction and chosen format`, async () => {
    const f = fixture();
    await f.api.downloadCompactInlineSelection({ messageIds: ['user-1'], format: 'image', imageStyle: 'neko', imageFormat });
    assert.equal(f.errors.length, 0);
    assert.equal(f.downloads[0].type, `image/${imageFormat}`);
    assert.ok(JSON.parse(await f.downloads[0].text()).join('\n').includes(label));
  });
}

test('plain messages, non-user messages and unsuccessful user messages produce no reaction metadata', async () => {
  for (const overrides of [{ reaction: undefined }, { role: 'assistant' }, { status: 'sending' }, { status: 'failed' }]) {
    const f = fixture([message(overrides)]);
    const preview = await f.api.buildCompactInlinePreview({ messageIds: ['user-1'], format: 'markdown' });
    assert.ok(preview.previewDocument.includes(body));
    assert.equal(preview.previewDocument.includes(label), false);
    const image = await f.api.buildCompactInlinePreview({ messageIds: ['user-1'], format: 'image' });
    assert.equal(JSON.parse(await f.blobs.get(image.previewUrl).text()).join('\n').includes(label), false);
  }
});

test('reaction author is escaped in Markdown without modifying the original message', async () => {
  const f = fixture([message({ reaction: { emoji: '❤️', author: '*Neko* <img>' } })]);
  await f.api.copyCompactInlineSelection({ messageIds: ['user-1'], format: 'markdown' });
  assert.ok(f.clipboard[0].includes('> \\*Neko\\* \\<img\\> reacted with ❤️'));
  assert.ok(f.clipboard[0].includes(body));
});

for (const [author, rendered] of [
  ['*Neko*', '*Neko*'],
  ['`Neko`', '`Neko`'],
  ['[Neko](https://example.com)', '[Neko](https://example.com)'],
  ['<img src=x onerror=alert(1)>', '&lt;img src=x onerror=alert(1)&gt;'],
  ['\\Neko', '\\Neko'],
  ['**Neko** _friend_', '**Neko** _friend_'],
]) {
  test(`normal Markdown preview preserves the literal reaction author ${author}`, async () => {
    const f = fixture([message({ reaction: { emoji: '❤️', author } })]);
    const preview = await f.api.buildCompactInlinePreview({ messageIds: ['user-1'], format: 'markdown' });
    assert.ok(preview.previewDocument.includes(`<blockquote>${rendered} reacted with ❤️</blockquote>`));
    assert.ok(preview.previewDocument.includes(body));
  });
}

test('Markdown escapes stay literal inside normal formatting and do not become links or HTML', async () => {
  const f = fixture([message({ reaction: undefined, blocks: [{ type: 'text',
    text: '**bold \\* literal** and \\[link](https://example.com) and \\<script\\>' }] })]);
  const preview = await f.api.buildCompactInlinePreview({ messageIds: ['user-1'], format: 'markdown' });
  assert.ok(preview.previewDocument.includes('<strong>bold * literal</strong>'));
  assert.ok(preview.previewDocument.includes('[link](https://example.com)'));
  assert.ok(preview.previewDocument.includes('&lt;script&gt;'));
  assert.equal(preview.previewDocument.includes('<a href="https://example.com"'), false);
});

for (const [text, expected] of [
  ['`\\d+\\.\\d+`', String.raw`<code>\d+\.\d+</code>`],
  ['`print("a\\"b")`', String.raw`<code>print(&quot;a\&quot;b&quot;)</code>`],
  ['`C:\\_tmp`', String.raw`<code>C:\_tmp</code>`],
  ['``literal ` tick and \\_slash``', '<code>literal ` tick and \\_slash</code>'],
  ['```python\nprint("a\\\"b")\nC:\\_tmp\n```', '<pre><code>print(&quot;a\\&quot;b&quot;)\nC:\\_tmp</code></pre>'],
  ['~~~\n\\d+\\.\\d+ **literal** <script>\n~~~', '<pre><code>\\d+\\.\\d+ **literal** &lt;script&gt;</code></pre>'],
]) {
  test('Markdown code preserves literal backslashes: ' + text, async () => {
    const f = fixture([message({ blocks: [{ type: 'text', text }] })]);
    const options = { messageIds: ['user-1'], format: 'markdown' };
    const preview = await f.api.buildCompactInlinePreview(options);
    assert.ok(preview.previewDocument.includes(expected), expected);
    assert.ok(preview.previewDocument.includes(`<blockquote>${label}</blockquote>`));
    await f.api.copyCompactInlineSelection(options);
    assert.ok(f.clipboard[0].includes(text));
  });
}

for (const fence of ['```', '````', '~~~']) {
  test('unfinished ' + fence + ' fence is closed before reaction and the next message', async () => {
    const text = 'Here:\n' + fence + 'python\ndef f():\n\\d+\\.\\d+';
    const f = fixture([
      message({ id: 'first', blocks: [{ type: 'text', text }] }),
      message({ id: 'second', blocks: [{ type: 'text', text: 'thanks!' }] }),
    ]);
    const options = { messageIds: ['first', 'second'], format: 'markdown' };
    const preview = await f.api.buildCompactInlinePreview(options);
    assert.ok(preview.previewDocument.includes('<pre><code>def f():\n\\d+\\.\\d+</code></pre>'));
    assert.ok(preview.previewDocument.includes('<p>thanks!</p>'));
    assert.equal(preview.previewDocument.match(/<h2>/g).length, 2);
    assert.equal(preview.previewDocument.match(/<blockquote>/g).length, 2);
    await f.api.copyCompactInlineSelection(options);
    await f.api.downloadCompactInlineSelection(options);
    for (const markdown of [f.clipboard[0], await f.downloads[0].text()]) {
      assert.ok(markdown.includes(text + '\n' + fence + '\n\n> ' + label));
      assert.ok(markdown.includes('thanks!'));
    }
  });
}

test('already closed fences remain unchanged in downloaded Markdown', async () => {
  const text = '````python\n```\ncode\n````';
  const f = fixture([message({ blocks: [{ type: 'text', text }] })]);
  await f.api.copyCompactInlineSelection({ messageIds: ['user-1'], format: 'markdown' });
  assert.ok(f.clipboard[0].includes(text + '\n\n> ' + label));
});

test('opening the export window keeps a reaction received while the popup was loading', async () => {
  const f = fixture([message({ reaction: undefined })]);
  let finishOpening;
  const opened = new Promise(resolve => { finishOpening = resolve; });
  f.internal.state.allMessages = f.host.messages.slice();
  f.internal.setPreviewFunctions(() => opened, async () => {});
  const opening = f.api.open();
  f.host.messages = [message()];
  f.api.refreshMessageReaction('user-1');
  finishOpening({});
  await opening;
  assert.equal(f.internal.state.allMessages[0].reaction.emoji, '❤️');
  f.internal.state.selectedIds = new Set(['user-1']);
  const preview = await f.internal.getOrBuildPreviewPayload(f.internal.getSelectedEntries(), 'markdown');
  assert.ok(preview.previewDocument.includes(label));
  assert.equal(f.errors.length, 0);
});

for (const format of ['markdown', 'image']) {
test(`late reactions refresh selected ${format} snapshots and invalidate cached previews`, async () => {
  const f = fixture([message({ reaction: undefined, status: 'sending' })]);
  const { state, getSelectedEntries, getOrBuildPreviewPayload } = f.internal;
  state.allMessages = f.host.messages.slice();
  state.selectedIds = new Set(['user-1']);
  state.previewModal = { panel: { hidden: false } };
  const content = async payload => format === 'markdown' ? payload.previewDocument
    : JSON.parse(await f.blobs.get(payload.previewUrl).text()).join('\n');
  const before = await getOrBuildPreviewPayload(getSelectedEntries(), format);
  assert.equal((await content(before)).includes(label), false);
  const cached = await getOrBuildPreviewPayload(getSelectedEntries(), format);
  assert.equal(cached.fromCache, true);
  f.host.messages = [message()];
  f.api.refreshMessageReaction('user-1');
  assert.equal(f.frames.length, 1, 'open selected preview must be scheduled to refresh');
  const after = await getOrBuildPreviewPayload(getSelectedEntries(), format);
  assert.notEqual(after.cacheKey, before.cacheKey);
  assert.equal(after.fromCache, false);
  assert.ok((await content(after)).includes(label));
  assert.equal(state.allMessages[0].status, 'sent');
  assert.equal(state.allMessages[0].blocks[0].text, body);
  // The reacting character is part of the cache identity as well as the emoji.
  f.host.messages = [message({ reaction: { emoji: '❤️', author: 'Other' } })];
  f.api.refreshMessageReaction('user-1');
  const changed = await getOrBuildPreviewPayload(getSelectedEntries(), format);
  assert.notEqual(changed.cacheKey, after.cacheKey);
  assert.ok((await content(changed)).includes('Other reacted with ❤️'));
});
}

for (const destination of ['javascript\\:alert%281%29', 'JaVaScRiPt\\:alert%281%29',
  'java\tscript\\:alert%281%29', 'vbscript\\:msgbox%281%29']) {
  for (const prefix of ['', '!']) {
    test('Markdown preview rejects the final escaped URL ' + prefix + destination, async () => {
      const text = prefix + '[open](' + destination + ')';
      const f = fixture([message({ reaction: undefined, blocks: [{ type: 'text', text }] })]);
      const preview = await f.api.buildCompactInlinePreview({ messageIds: ['user-1'], format: 'markdown' });
      assert.equal(/(?:href|src)="(?:javascript|vbscript):/i.test(preview.previewDocument), false);
      assert.ok(preview.previewDocument.includes(prefix ? '<img src="" alt="open">' : '<p>open</p>'));
      await f.api.copyCompactInlineSelection({ messageIds: ['user-1'], format: 'markdown' });
      assert.ok(f.clipboard[0].includes(text), 'exported source must remain unchanged');
    });
  }
}

test('safe escaped destinations stay usable and cannot break HTML attributes', async () => {
  const f = fixture([message({ reaction: undefined, blocks: [{ type: 'text',
    text: '[safe](https\\://example.com/a?x=1&y=2) ![image](https\\://example.com/a.png) '
      + '[relative](.\\/notes) [quoted](https\\://example.com/\\"quoted\\")' }] })]);
  const preview = await f.api.buildCompactInlinePreview({ messageIds: ['user-1'], format: 'markdown' });
  for (const expected of ['href="https://example.com/a?x=1&amp;y=2"',
    'src="https://example.com/a.png"', 'href="./notes"', 'href="https://example.com/&quot;quoted&quot;"']) {
    assert.ok(preview.previewDocument.includes(expected), expected);
  }
});

for (const destination of ['https://example.com/`foo`', 'https://example.com/``foo``',
  'https://example.com/`a"b`', './`notes`']) {
  for (const prefix of ['', '!']) {
    test('code delimiters stay literal in URL: ' + prefix + destination, async () => {
      const text = prefix + '[open](' + destination + ') and `real code`';
      const f = fixture([message({ blocks: [{ type: 'text', text }] })]);
      const options = { messageIds: ['user-1'], format: 'markdown' };
      const preview = await f.api.buildCompactInlinePreview(options);
      const escaped = destination.replace(/"/g, '&quot;');
      assert.ok(preview.previewDocument.includes((prefix ? 'src' : 'href') + '="' + escaped + '"'));
      assert.ok(preview.previewDocument.includes('<code>real code</code>'));
      assert.equal(/(?:href|src)="[^"]*<code>/.test(preview.previewDocument), false);
      await f.api.copyCompactInlineSelection(options);
      assert.ok(f.clipboard[0].includes(text));
    });
  }
}

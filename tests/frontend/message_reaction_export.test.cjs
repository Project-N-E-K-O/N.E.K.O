const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const frontendRequire = createRequire(path.join(__dirname, '../../frontend/react-neko-chat/package.json'));

const source = process.env.CHAT_EXPORT_REVISION
  ? require('node:child_process').execFileSync('git', ['show', `${process.env.CHAT_EXPORT_REVISION}:static/app/app-chat-export.js`],
    { cwd: path.join(__dirname, '../..'), encoding: 'utf8' })
  : fs.readFileSync(path.join(__dirname, '../../static/app/app-chat-export.js'), 'utf8');
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
  const imageSources = [];
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
    set src(value) { imageSources.push(value); if (value) queueMicrotask(() => this.onload?.()); }
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
  return { api: window.appChatExport, internal: window.testExport, host, blobs, downloads, clipboard, frames, errors, imageSources };
}

function recommendation(kind, fields = {}) {
  const block = kind === 'music'
    ? { type: 'music_recommendation', recommendationId: '19f3bbca-a275-43d4-923d-1b2ceee91943', state: 'pending',
      track: { name: 'Evening Song', artist: 'Neko Quartet', url: 'https://example.test/song.mp3', cover: '' } }
    : { type: 'proactive_recommendation', recommendationId: '023bf0f3-77ac-433a-8138-5ba7dc6da15f', state: 'pending',
      category: 'news', content: { title: 'An interesting story', source: 'Example Journal', url: 'https://example.test/story' } };
  return message({ id: kind === 'music' ? 'music-recommendation-test' : 'proactive-recommendation-test',
    role: 'assistant', reaction: undefined, blocks: [{ ...block, ...fields }] });
}

let standardMarkdownRenderer;
async function renderStandardMarkdown(markdown) {
  if (!standardMarkdownRenderer) {
    standardMarkdownRenderer = import(pathToFileURL(frontendRequire.resolve('react-markdown')).href).then(({ default: Markdown }) => {
      const React = frontendRequire('react');
      const { renderToStaticMarkup } = frontendRequire('react-dom/server');
      return text => renderToStaticMarkup(React.createElement(Markdown, { children: text }));
    });
  }
  return (await standardMarkdownRenderer)(markdown);
}

function readExportHtml(html) {
  const { JSDOM } = frontendRequire('jsdom');
  const dom = new JSDOM(html);
  try {
    const body = dom.window.document.body;
    return { text: body.textContent,
      links: Array.from(body.querySelectorAll('a'), link => ({ text: link.textContent, href: link.getAttribute('href') })),
      images: Array.from(body.querySelectorAll('img'), image => ({ alt: image.getAttribute('alt'), src: image.getAttribute('src') })) };
  } finally { dom.window.close(); }
}

async function exportedRecommendation(message) {
  const before = JSON.stringify(message);
  const f = fixture([message]), options = { messageIds: [message.id], format: 'markdown' };
  const preview = await f.api.buildCompactInlinePreview(options);
  await f.api.copyCompactInlineSelection(options);
  await f.api.downloadCompactInlineSelection(options);
  const markdown = await f.downloads[0].text();
  const copiedMarkdown = f.clipboard[0];
  const comparable = [markdown, copiedMarkdown].map(text => {
    const lines = text.replace(/\r\n/g, '\n').split('\n');
    assert.equal(lines[0], '# Project N.E.K.O Conversation Export');
    assert.equal(lines[1], '');
    assert.match(lines[2], /^Exported At: \d{1,2}\/\d{1,2}\/\d{4}, \d{1,2}:\d{2}:\d{2}\s(?:AM|PM)$/);
    assert.equal(lines[3], '');
    // Each public action creates its own timestamp; only this known header may differ.
    lines[2] = 'Exported At: <generated>';
    return lines.join('\n');
  });
  assert.equal(comparable[0], comparable[1], 'copy and download must deliver the same Markdown body');
  assert.equal(JSON.stringify(message), before, 'serialization cannot change live recommendation content or URLs');
  assert.equal(f.errors.length, 0);
  return { markdown, rendered: [readExportHtml(preview.previewDocument),
    readExportHtml(await renderStandardMarkdown(copiedMarkdown)), readExportHtml(await renderStandardMarkdown(markdown))] };
}

function assertSameUrlMeaning(actual, expected) {
  const left = new URL(actual, 'http://localhost/chat'), right = new URL(expected, 'http://localhost/chat');
  assert.equal(left.origin, right.origin);
  assert.equal(decodeURI(left.pathname), decodeURI(right.pathname));
  assert.deepEqual(Array.from(left.searchParams), Array.from(right.searchParams), 'query separators and encoded values must retain their meaning');
  assert.equal(decodeURIComponent(left.hash), decodeURIComponent(right.hash));
}

for (const [name, title, sourceName, url] of [
  ['entities', 'Music &copy; 2026', 'A &amp; B', 'https://example.test/story?x=1&copy;=2#literal&reg;'],
  ['ordinary ampersands', 'Rock & Roll', 'A & B', 'https://example.test/story?x=1&copy=2#anchor'],
  ['literal autolinks', 'Read <https://example.test/inside> literally', 'The <b> tag is text', 'https://example.test/story'],
  ['encoded values', '100% original &#169;', 'Codes &#x26; and %26', 'https://example.test/doc%28one%29?literal=%26copy%3B&next=one%2526two#hash%26tail'],
  ['parentheses', 'A (parenthetical) [title]', 'Source &amp; reference', 'https://example.test/doc(1)[draft]?x=(left)&q=bracket[ok]#anchor(2)'],
  ['Unicode', '猫 &amp; 音乐 🌟', '日本語 &copy; 中文', 'https://例子.测试/音乐?q=猫&lang=中文#段落&copy;'],
]) {
  test(`recommendation Markdown preserves ${name} in external renderers and its own preview`, async () => {
    const result = await exportedRecommendation(recommendation('news', { content: { title, source: sourceName, url } }));
    for (const rendered of result.rendered) {
      assert.ok(rendered.text.includes(title));
      assert.ok(rendered.text.includes(sourceName));
      assert.equal(rendered.links.length, 1, 'literal text must not introduce another link');
      assert.equal(rendered.links[0].text, title);
      assertSameUrlMeaning(rendered.links[0].href, url);
      if (name === 'encoded values') assert.equal(rendered.links[0].href, new URL(url).href, 'existing percent escapes must not be encoded again');
      assert.equal(rendered.images.length, 0);
    }
  });
}

test('music Markdown preserves literal metadata and the cover URL without exporting its playback URL', async () => {
  const track = { name: 'Music &copy; <notes>', artist: 'A &amp; B', url: 'https://example.test/private-audio.mp3',
    cover: 'https://example.test/cover(1).png?literal=%26copy%3B&copy;=2#cover&reg;' };
  const result = await exportedRecommendation(recommendation('music', { track }));
  for (const rendered of result.rendered) {
    assert.ok(rendered.text.includes(track.name));
    assert.ok(rendered.text.includes(track.artist));
    assert.equal(rendered.links.length, 0);
    assert.equal(rendered.images.length, 1);
    assert.equal(rendered.images[0].alt, track.name);
    assertSameUrlMeaning(rendered.images[0].src, track.cover);
  }
  assert.equal(result.markdown.includes(track.url), false);
});

for (const state of ['pending', 'accepted']) {
  test(`meme Markdown keeps ${state} image consent while preserving literal text and URL semantics`, async () => {
    const content = { title: 'A cat &copy; <waving>', source: 'A &amp; B',
      url: 'https://example.test/cat(1).png?literal=%26copy%3B&copy;=2#cat&reg;' };
    const result = await exportedRecommendation(recommendation('meme', { category: 'meme', state, content }));
    for (const rendered of result.rendered) {
      assert.ok(rendered.text.includes(content.title));
      assert.ok(rendered.text.includes(content.source));
      assert.equal(rendered.links.length, 1);
      assertSameUrlMeaning(rendered.links[0].href, content.url);
      assert.equal(rendered.images.length, state === 'accepted' ? 1 : 0);
      if (state === 'accepted') {
        const proxy = new URL(rendered.images[0].src, 'http://localhost/chat');
        assert.equal(proxy.pathname, '/api/meme/proxy-image');
        assert.equal(proxy.searchParams.get('url'), content.url);
        assert.equal(rendered.images[0].alt, content.title);
      }
    }
  });
}

test('screen-observation Markdown remains literal text without adding links or images', async () => {
  const title = 'Read <https://example.test/inside> &copy; literally.';
  const result = await exportedRecommendation(recommendation('vision', { category: 'vision', content: { title, source: '', url: '' } }));
  for (const rendered of result.rendered) {
    assert.ok(rendered.text.includes(title));
    assert.equal(rendered.links.length, 0);
    assert.equal(rendered.images.length, 0);
  }
});

test('recommendation escaping leaves pre-existing text and link Markdown unchanged', async () => {
  const text = 'Existing **Markdown** &copy; <https://example.test>.';
  const title = 'Old &copy; link', url = 'https://example.test/story?x=1&copy;=2';
  const f = fixture([message({ reaction: undefined, blocks: [{ type: 'text', text }, { type: 'link', title, url }] })]);
  await f.api.copyCompactInlineSelection({ messageIds: ['user-1'], format: 'markdown' });
  assert.ok(f.clipboard[0].includes(text));
  assert.ok(f.clipboard[0].includes('[' + title + '](' + url + ')'));
});

for (const kind of ['music', 'news', 'vision']) {
  test(`${kind} recommendation metadata survives normal Markdown preview, copy and download`, async () => {
    const m = recommendation(kind, kind === 'vision'
      ? { category: 'vision', content: { title: 'You are tending a farm.', source: '', url: '' } } : {});
    const before = JSON.stringify(m);
    const expected = kind === 'music' ? ['Evening Song', 'Neko Quartet']
      : kind === 'vision' ? ['You are tending a farm'] : ['An interesting story', 'Example Journal', 'https://example.test/story'];
    const f = fixture([m]), options = { messageIds: [m.id], format: 'markdown' };
    const preview = await f.api.buildCompactInlinePreview(options);
    await f.api.copyCompactInlineSelection(options);
    await f.api.downloadCompactInlineSelection(options);
    for (const content of [preview.previewDocument, f.clipboard[0], await f.downloads[0].text()]) {
      for (const text of expected) assert.ok(content.includes(text), `missing ${text}`);
      if (kind === 'music') assert.equal(content.includes('https://example.test/song.mp3'), false, 'playback URLs stay out of music exports');
    }
    assert.equal(JSON.stringify(m), before, 'export projection must not mutate live recommendation blocks');
    assert.equal(f.errors.length, 0);
  });

  for (const imageStyle of ['neko', 'original', 'poster', 'lyrics']) {
    test(`${kind} recommendation draws its metadata in ${imageStyle} image preview, copy and download`, async () => {
      const m = recommendation(kind, kind === 'vision'
        ? { category: 'vision', content: { title: 'You are tending a farm.', source: '', url: '' } } : {});
      const expected = kind === 'music' ? ['Evening Song', 'Neko Quartet']
        : kind === 'vision' ? ['You are tending a farm.'] : ['An interesting story', 'Example Journal'];
      const f = fixture([m]), options = { messageIds: [m.id], format: 'image', imageStyle, imageFormat: 'png' };
      const preview = await f.api.buildCompactInlinePreview(options);
      await f.api.copyCompactInlineSelection(options);
      await f.api.downloadCompactInlineSelection(options);
      for (const blob of [f.blobs.get(preview.previewUrl), f.clipboard[0], f.downloads[0]]) {
        const drawn = JSON.parse(await blob.text()).join('\n');
        for (const text of expected) assert.ok(drawn.includes(text), `missing painted ${text}`);
      }
      assert.equal(f.errors.length, 0);
    });
  }
}

for (const [state, dismissed, imageExpected, directDelivery] of [['pending', false, false, false], ['skipped', false, false, false],
  ['accepted', false, true, false], ['accepted', true, false, false], ['pending', false, true, true], ['pending', true, false, true], ['skipped', false, false, true]]) {
  test(`meme export respects visible preview: ${state}, dismissed=${dismissed}, direct=${directDelivery}`, async () => {
    const m = recommendation('meme', { category: 'meme', state, dismissed, directDelivery,
      content: { title: 'A waving cat', source: 'Example', url: 'https://example.test/cat.png' } });
    const f = fixture([m]);
    const preview = await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'markdown' });
    assert.ok(preview.previewDocument.includes('A waving cat'));
    assert.equal(preview.previewDocument.includes('<img'), imageExpected);
    await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'image' });
    assert.equal(f.imageSources.some(url => url.includes('/api/meme/proxy-image?')), imageExpected);
    assert.equal(f.errors.length, 0);
  });
}

test('recommendation text stays literal and its URL cannot inject another Markdown image', async () => {
  const m = recommendation('news', { state: 'accepted', content: {
    title: 'Story ![secret](https://hidden.test/a.png)', source: 'Source\n![secret](https://hidden.test/b.png)',
    url: 'https://example.test/)[secret](https://hidden.test/asset)' } });
  const f = fixture([m]);
  const result = await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'markdown' });
  assert.equal(result.previewDocument.includes('<img'), false);
  assert.ok(result.previewDocument.includes('Story ![secret]'));
  assert.equal(f.imageSources.length, 0);
});

for (const url of ['javascript:alert(1)', 'data:image/png;base64,AA==', 'https://user:secret@example.test/image']) {
  test('recommendation exports refuse invisible or unsafe content ' + url, async () => {
    const m = recommendation('meme', { category: 'meme', state: 'accepted', content: { title: 'Hidden material', source: '', url } });
    const f = fixture([m]);
    const result = await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'markdown' });
    assert.equal(result.previewDocument.includes('Hidden material'), false);
    assert.equal(result.previewDocument.includes('<img'), false);
    await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'image' });
    assert.equal(f.imageSources.some(source => source.includes('/api/meme/proxy-image?') || source.includes('example.test/image')
      || source.startsWith('javascript:') || source.startsWith('data:image/png;base64,AA==')), false);
  });
}

for (const cover of ['https://example.test/cover(1).png', 'https://example.test/cover.png) ![hidden](https://hidden.test/pixel.png)',
  'https://user:secret@example.test/cover.png']) {
  test('music recommendation export keeps cover boundaries and rejects credentials: ' + cover, async () => {
    const m = recommendation('music'); m.blocks[0].track.cover = cover;
    m.blocks[0].track.name = 'Song [one] ![hidden](https://hidden.test/title.png)';
    const f = fixture([m]);
    const preview = await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'markdown' });
    assert.equal((preview.previewDocument.match(/<img /g) || []).length, 1);
    assert.equal(preview.previewDocument.includes('src="https://hidden.test/'), false);
    assert.equal(preview.previewDocument.includes('user:secret@'), false);
    if (cover.includes('cover(1)')) assert.ok(preview.previewDocument.includes('cover%281%29.png'));
    await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'image' });
    assert.equal(f.imageSources.some(source => source.includes('user:secret@') || source.startsWith('https://hidden.test/')), false);
  });
}

test('accepted meme parentheses and literal alt survive Markdown image projection', async () => {
  const m = recommendation('meme', { category: 'meme', state: 'accepted', content: {
    title: 'Cat [one] ![hidden](https://hidden.test/pixel.png)', source: '', url: 'https://example.test/cat(1).png' } });
  const f = fixture([m]);
  const preview = await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'markdown' });
  assert.equal((preview.previewDocument.match(/<img /g) || []).length, 1);
  assert.ok(preview.previewDocument.includes('src="/api/meme/proxy-image?url=https%3A%2F%2Fexample.test%2Fcat%281%29.png"'));
  assert.equal(preview.previewDocument.includes('src="https://hidden.test/'), false);
});

test('a screen observation containing tilde fences exports literal text, not a code block', async () => {
  const m = recommendation('vision', { category: 'vision', content: { title: '~~~\nkeep _literal_\n~~~', source: '', url: '' } });
  const f = fixture([m]);
  const preview = await f.api.buildCompactInlinePreview({ messageIds: [m.id], format: 'markdown' });
  assert.equal(preview.previewDocument.includes('<pre>'), false);
  assert.ok(preview.previewDocument.includes('~~~'));
  assert.ok(preview.previewDocument.includes('keep _literal_'));
});

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

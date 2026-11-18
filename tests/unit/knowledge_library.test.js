// Exercise the real page controller with delayed API results and a text-only DOM.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/knowledge_library.js'), 'utf8');

class Element {
    constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ''; this.hidden = false; this.open = false; this.disabled = false; this.textContent = ''; }
    set innerHTML(value) { throw new Error('Untrusted content must not reach innerHTML'); }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() {}
    click() { this.clicked = true; }
}

function harness(request = async () => ({ ok: true, jobs: [] })) {
    const elements = new Map();
    const timers = new Map();
    const storage = new Map();
    const created = []; const usedKeys = new Set();
    let counter = 0;
    const document = { getElementById: () => null, createElement: (tag) => { const element = new Element(tag); created.push(element); return element; }, querySelectorAll: () => [], addEventListener() {} };
    const host = {
        t: (key) => { usedKeys.add(key); return key; },
        crypto: webcrypto,
        localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
        addEventListener() {}, removeEventListener() {},
        setTimeout(fn) { timers.set(++counter, fn); return counter; },
        clearTimeout(id) { timers.delete(id); },
        fetch: async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }),
        nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'one' }), refreshToken: async () => {} },
        FormData: class { constructor() { this.values = []; } append(...args) { this.values.push(args); } },
    };
    vm.runInNewContext(source, { window: host, document, AbortController, Set, URL, console });
    document.getElementById = (id) => {
        if (!elements.has(id)) {
            const element = new Element();
            if (id === 'library-feedback') element.hidden = true; // Match the real template's initial state.
            elements.set(id, element);
        }
        return elements.get(id);
    };
    const calls = [];
    const api = { request: async (url, options) => { calls.push({ url, options }); return request(url, options); } };
    const page = host.NekoKnowledgeLibrary.createPage(host, document, api);
    return { host, document, elements, timers, calls, storage, created, usedKeys, page, text: (id) => document.getElementById(id).textContent };
}

function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
function library(id) { return { library_id: id, name: id, kind: 'local' }; }
function scope(artifactId) { return { artifact_id: artifactId, sha256: artifactId, name: 'Release', version: '2.0', source: { author: 'Author', license: 'CC0', url: '' }, documents: [{ document_id: 'one', name: 'selected-private-file.txt', version: 'frozen', characters: 23 }] }; }
function share(overrides = {}) { return { id: 31, title: '<img onerror=evil()>', kind: 'knowledge', status: 'published', revision: 7, filename: 'release.neko-knowledge-pack.zip', file_sha256: 'b'.repeat(64), preview: { format: 'neko.personal-knowledge-package', format_version: 1, package_version: '2.0' }, ...overrides }; }
function marketReady(h) { h.page.state.market = { authenticated: true, market_url: 'https://market.test' }; }

test('transport keeps multipart boundaries, same-origin scope, and CSRF header', async () => {
    const h = harness();
    let observed;
    h.host.fetch = async (url, options) => { observed = { url, options }; return { ok: true, status: 200, json: async () => ({ ok: true }) }; };
    const api = h.host.NekoKnowledgeLibrary.createApi(h.host);
    const form = new h.host.FormData();
    await api.request('/libraries/a/documents', { method: 'POST', file: form });
    assert.equal(observed.url, '/api/knowledge-library/libraries/a/documents');
    assert.equal(observed.options.headers['X-CSRF-Token'], 'one');
    assert.equal(observed.options.headers['Content-Type'], undefined);
    assert.equal(observed.options.mode, 'same-origin');
    assert.equal(observed.options.body, form);
    assert.equal(h.timers.size, 0);
});

test('transport retries a rejected CSRF once, but never replays business authorization failures', async () => {
    const h = harness(); let calls = 0; let refreshed = 0;
    h.host.nekoLocalMutationSecurity.refreshToken = async () => { refreshed += 1; };
    h.host.fetch = async () => ({ ok: ++calls > 1, status: calls === 1 ? 403 : 200, json: async () => calls === 1 ? { ok: false, reason: 'csrf_validation_failed' } : { ok: true } });
    await h.host.NekoKnowledgeLibrary.createApi(h.host).request('/libraries', { method: 'POST', body: { name: 'A' } });
    assert.equal(calls, 2); assert.equal(refreshed, 1);
    calls = 0;
    h.host.fetch = async () => { calls += 1; return { ok: false, status: 403, json: async () => ({ ok: false, reason: 'forbidden' }) }; };
    await assert.rejects(h.host.NekoKnowledgeLibrary.createApi(h.host).request('/libraries', { method: 'POST', body: {} }), /forbidden/);
    assert.equal(calls, 1);
});

test('late library A response cannot replace the newer selected library B', async () => {
    const first = deferred();
    const h = harness(async (url) => url === '/libraries/a' ? first.promise : { library: library('b'), documents: [], packages: [] });
    const old = h.page.selectLibrary('a'); await h.page.selectLibrary('b');
    first.resolve({ library: library('a'), documents: [{ document_id: 'secret', filename: 'A secret' }] }); await old;
    assert.equal(h.page.state.libraryId, 'b'); assert.equal(h.text('library-name'), 'b');
    assert.equal(h.page.state.documents.length, 0);
});

test('a search that finishes after a library switch cannot reveal old-library snippets', async () => {
    const pending = deferred();
    const h = harness(async (url) => url.endsWith('/search') ? pending.promise : { library: library('b'), documents: [], packages: [] });
    h.page.state.libraryId = 'a'; const old = h.page.search('secret');
    await h.page.selectLibrary('b');
    pending.resolve({ hits: [{ filename: 'secret', text: '<script>evil()</script>' }], semantic_state: 'ready' }); await old;
    assert.equal(h.document.getElementById('library-results').children.length, 0);
    assert.equal(h.text('library-semantic'), '');
});

test('search source labels localize real coordinates without exposing metadata or changing their source', async () => {
    const location = { page: 3, paragraph: 4, section: 'Chapter A', source_start: 0, source_end: 17, token_path: [2, 1], token_part: 1, window_part: 2, path: '/private/hidden' };
    const original = JSON.stringify(location);
    const h = harness(async () => ({ hits: [{ document_id: 'one', filename: 'notes.txt', text: 'Saved source', location }], semantic_state: 'ready' }));
    h.host.t = (key, options) => ({
        'knowledgeLibrary.sourcePage': 'Page ' + options?.page,
        'knowledgeLibrary.sourceParagraph': 'Paragraph ' + options?.paragraph,
        'knowledgeLibrary.sourceSection': 'Section: ' + options?.section,
    })[key] || key;
    h.page.state.libraryId = 'a';
    await h.page.search('source');
    const label = h.document.getElementById('library-results').children[0].children[1].textContent;
    assert.equal(label, 'Page 3 · Paragraph 4 · Section: Chapter A');
    assert.equal(JSON.stringify(location), original);
});

test('source labels omit unknown fields and invalid coordinates instead of inventing a location', async () => {
    const h = harness(async () => ({ hits: [
        { filename: 'unknown.txt', text: 'Text A', location: { path: '/private/source', part: 1, token_path: [1] } },
        { filename: 'invalid.txt', text: 'Text B', location: { page: -1, paragraph: 1.5, section: { path: 'hidden' } } },
        { filename: 'legacy.txt', text: 'Text C', location: '{"source_start":0,"path":"hidden"}' },
    ], semantic_state: 'unavailable' }));
    h.page.state.libraryId = 'a'; await h.page.search('source');
    for (const row of h.document.getElementById('library-results').children) {
        assert.equal(row.children.filter((child) => child.className === 'library-row-meta').length, 0);
    }
});

test('preview renders literal untrusted content and confirmation submits only its frozen hash', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    const h = harness(async (url) => url.startsWith('/previews/') ? { preview: { filename: evil, content: evil, sha256: 'sealed' } } : { jobs: [] });
    await h.page.showPreview('preview-one');
    const config = h.page.state.dialog;
    assert.equal(config.description, evil); assert.equal(config.content.textContent, evil);
    config.content.textContent = 'browser modified text';
    await config.submit();
    const confirm = h.calls.find((call) => call.url.endsWith('/confirm'));
    assert.deepEqual(JSON.parse(JSON.stringify(confirm.options.body)), { sha256: 'sealed' });
    assert.equal(h.document.getElementById('library-dialog').open, false);
});

test('preview response from a superseded selection does not reopen a dialog', async () => {
    const pending = deferred();
    const h = harness(async (url) => url.startsWith('/previews/') ? pending.promise : { library: library('b'), documents: [], packages: [] });
    const old = h.page.showPreview('one'); await h.page.selectLibrary('b');
    pending.resolve({ preview: { content: 'old', filename: 'old', sha256: 'old' } }); await old;
    assert.equal(h.page.state.dialog, null);
});

test('global restored jobs survive a concurrent selection and do not poll after disposal', async () => {
    const pending = deferred();
    const h = harness(async (url) => url === '/jobs' ? pending.promise : { library: library('b'), documents: [], packages: [] });
    const jobs = h.page.refreshJobs(); await h.page.selectLibrary('b');
    pending.resolve({ jobs: [{ job_id: 'restored', library_id: 'b', kind: 'preview', state: 'running' }] }); await jobs;
    assert.equal(h.page.state.jobs[0].job_id, 'restored'); assert.equal(h.timers.size, 1);
    h.page.dispose(); assert.equal(h.timers.size, 0);
});

test('cancel response is reconciled with server completion rather than fabricated cancellation', async () => {
    const completed = { job_id: 'j', state: 'completed', kind: 'index' };
    const h = harness(async (url) => url === '/jobs' ? { jobs: [completed] } : { job: completed });
    await h.page.action('cancel-job', 'j');
    assert.equal(h.page.state.jobs[0].state, 'completed');
    assert.equal(h.calls.filter((call) => call.url === '/jobs/j/cancel').length, 1);
});

test('upload preflight refuses oversize files and a selection changed under the file picker', async () => {
    const h = harness(); h.page.state.libraryId = 'a'; h.page.state.uploadLibraryId = 'a';
    await h.page.upload({ name: 'huge.pdf', size: 16 * 1024 * 1024 + 1 });
    assert.equal(h.calls.length, 0); assert.equal(h.text('library-feedback'), 'knowledgeLibrary.tooLarge');
    h.page.state.libraryId = 'b';
    await h.page.upload({ name: 'notes.md', size: 20 });
    assert.equal(h.calls.length, 0);
});

test('build freezes an explicit selection with source and version instead of including unchecked documents', async () => {
    const h = harness(async (url) => url === '/jobs' ? { jobs: [] } : { library: library('a'), documents: [{ document_id: 'one', filename: 'first', chunks_count: 1 }, { document_id: 'two', filename: 'second', chunks_count: 2 }], packages: [] });
    await h.page.selectLibrary('a');
    assert.equal(h.page.state.selected.size, 2);
    h.page.state.selected.delete('two');
    await h.page.action('build');
    const config = h.page.state.dialog;
    config.content.children[0].children[0].value = 'Author';
    config.content.children[1].children[0].value = 'CC BY 4.0';
    await config.submit('1.0');
    const built = h.calls.find((call) => call.url.endsWith('/packages') && call.options?.method === 'POST');
    assert.deepEqual(JSON.parse(JSON.stringify(built.options.body)), { version: '1.0', source: { author: 'Author', license: 'CC BY 4.0', url: '' }, document_ids: ['one'] });
});

test('package export uses a locally constructed endpoint and interrupted jobs do not poll forever', async () => {
    const h = harness(async (url) => url.endsWith('/preview') ? { package: scope('a/b') } : url === '/jobs' ? { jobs: [{ job_id: 'old', state: 'interrupted', kind: 'build', reason: 'process_interrupted' }] } : url === '/packages' ? { packages: [{ artifact_id: 'a/b', name: '<script>', version: 'v1', sha256: 'sealed', url: 'https://evil.test' }] } : { library: library('a'), documents: [] });
    await h.page.selectLibrary('a'); await h.page.refreshJobs();
    const control = h.document.getElementById('library-packages').children[0].children[0].children[1].children[0];
    assert.equal(control.dataset.action, 'package-export');
    assert.equal(h.created.some((element) => element.clicked && element.tagName === 'a'), false);
    await h.page.action('package-export', 'a/b');
    await h.page.state.dialog.submit();
    const link = h.created.find((element) => element.clicked && element.tagName === 'a');
    assert.equal(link.href, '/api/knowledge-library/packages/a%2Fb/download');
    assert.equal(h.timers.size, 0);
});

test('offline installation updates a library only after an explicit checkbox selection', async () => {
    const h = harness(async (url) => url === '/jobs' ? { jobs: [] } : url === '/libraries' ? { libraries: [] } : {});
    h.page.state.libraryId = 'installed-one'; h.page.state.library = { kind: 'installed' };
    await h.page.install({ name: 'release.neko-knowledge-pack.zip' });
    assert.equal(h.calls.length, 0);
    let config = h.page.state.dialog;
    config.content.children[0].children[0].checked = true;
    await config.submit();
    const update = h.calls.find((call) => call.url === '/packages/install');
    assert.deepEqual(update.options.file.values, [['file', { name: 'release.neko-knowledge-pack.zip' }], ['library_id', 'installed-one']]);
    h.calls.length = 0;
    h.page.state.libraryId = 'installed-one'; h.page.state.library = { kind: 'installed' };
    await h.page.install({ name: 'another.neko-knowledge-pack.zip' }); config = h.page.state.dialog;
    await config.submit();
    assert.equal(h.calls.find((call) => call.url === '/packages/install').options.file.values.length, 1);
});

test('a late preview cannot replace a newer creation dialog in the same library', async () => {
    const pending = deferred(); const h = harness(async () => pending.promise);
    const old = h.page.showPreview('old');
    await h.page.action('create'); const current = h.page.state.dialog;
    pending.resolve({ preview: { filename: 'old', content: 'old', sha256: 'old' } }); await old;
    assert.equal(h.page.state.dialog, current);
    assert.equal(h.text('library-dialog-title'), 'knowledgeLibrary.create');
});

test('market share inputs accept only a safe ID or configured-origin detail link', () => {
    const h = harness(); const parse = h.host.NekoKnowledgeLibrary.parseShareId;
    assert.equal(parse('31', 'https://market.test'), 31);
    assert.equal(parse('https://market.test/#/shares/31?kind=knowledge', 'https://market.test'), 31);
    assert.equal(parse('https://market.test/shares/31', 'https://market.test'), 31);
    for (const input of ['https://evil.test/#/shares/31', 'javascript:alert(1)', 'https://user:pass@market.test/shares/31', 'https://market.test/plugins/31', '9007199254740993', '0']) {
        assert.throws(() => parse(input, 'https://market.test'), /invalid_share_link/);
    }
});

test('abandoning a preview deletes its source while a confirmed import retains it', async () => {
    const h = harness(async (url) => url === '/jobs' ? { jobs: [] } : { preview: { filename: 'safe', content: 'source', sha256: 'sealed' } });
    await h.page.showPreview('first'); h.page.closeDialog(); await Promise.resolve();
    assert.equal(h.calls.filter((call) => call.url === '/previews/first' && call.options?.method === 'DELETE').length, 1);
    await h.page.showPreview('second'); await h.page.state.dialog.submit();
    assert.equal(h.calls.filter((call) => call.url === '/previews/second' && call.options?.method === 'DELETE').length, 0);
});

test('market installation requires a published package and freezes the displayed revision and hash', async () => {
    const publicShare = share();
    const h = harness(async (url) => url === '/market/shares/31' ? { share: publicShare } : { jobs: [] }); marketReady(h);
    h.page.marketInstall(); await h.page.state.dialog.submit('https://market.test/#/shares/31');
    const confirmation = h.page.state.dialog;
    assert.equal(confirmation.input, undefined);
    assert.equal(confirmation.content.children[0].textContent, publicShare.title);
    publicShare.revision = 9; publicShare.file_sha256 = 'c'.repeat(64);
    await confirmation.submit();
    const posted = h.calls.find((call) => call.url === '/market/install');
    assert.deepEqual(JSON.parse(JSON.stringify(posted.options.body)), { share_id: 31, revision: 7, file_sha256: 'b'.repeat(64) });
});

test('unpublished shares, old JSON, and arbitrary remote URLs never reach market installation', async () => {
    let result = share({ status: 'pending_review' });
    const h = harness(async () => ({ share: result })); marketReady(h);
    h.page.marketInstall(); await assert.rejects(h.page.state.dialog.submit('31'), /market_not_published/);
    result = share({ preview: { entries: [{ text: 'private' }] } });
    await assert.rejects(h.page.state.dialog.submit('31'), /market_not_knowledge_package/);
    const before = h.calls.length;
    await assert.rejects(h.page.state.dialog.submit('https://evil.test/shares/31'), /invalid_share_link/);
    assert.equal(h.calls.length, before);
    assert.equal(h.calls.some((call) => call.url === '/market/install'), false);
});

test('uncertain publication reuses the durable UUID and frozen metadata in the same dialog and after reload', async () => {
    const payloads = []; let failure = true;
    const request = async (url, options) => {
        if (url.endsWith('/preview')) return { package: scope('built') };
        if (url === '/market/publish') { payloads.push(JSON.parse(JSON.stringify(options.body))); if (failure) throw new Error('request_timeout'); return { job: { job_id: 'accepted' } }; }
        return { jobs: [] };
    };
    const h = harness(request); h.page.state.packages = [{ artifact_id: 'built', name: 'Release', version: '2.0' }];
    await h.page.publish('built'); const config = h.page.state.dialog;
    config.content.children[0].children[0].value = 'Description';
    await assert.rejects(config.submit('Reviewed title'), /request_timeout/);
    await assert.rejects(config.submit('Changed title'), /request_timeout/);
    assert.deepEqual(payloads[1], payloads[0]);
    assert.match(payloads[0].client_request_id, /^[a-f\d-]{36}$/);
    const reloaded = h.host.NekoKnowledgeLibrary.createPage(h.host, h.document, { request });
    reloaded.state.packages = h.page.state.packages;
    await reloaded.publish('built');
    assert.equal(reloaded.state.dialog.locked, true);
    failure = false; await reloaded.state.dialog.submit('Another title');
    assert.deepEqual(payloads[2], payloads[0]);
    assert.equal(JSON.parse([...h.storage.values()][0]).built.job_id, 'accepted');
});

test('share updates read the existing public revision and never ask the user to type it', async () => {
    const h = harness(async (url) => url.endsWith('/preview') ? { package: scope('built') } : url === '/market/shares/31' ? { share: share() } : url === '/market/publish' ? { job: { job_id: 'update' } } : { jobs: [] }); marketReady(h);
    h.page.state.packages = [{ artifact_id: 'built', name: 'Release', version: '2.0' }]; await h.page.publish('built');
    const config = h.page.state.dialog; const content = config.content;
    const update = content.children[1].children[0]; update.checked = true; update.listeners.change();
    await assert.rejects(config.submit('Updated title'), /read_share_first/);
    content.children[2].children[0].value = '31'; await content.children[3].listeners.click();
    await config.submit('Updated title');
    const body = h.calls.find((call) => call.url === '/market/publish').options.body;
    assert.equal(body.share_id, 31); assert.equal(body.revision, 7);
    assert.equal(body.title, 'Updated title');
});

test('retry delegates to the stored job and pending review is displayed as its real publication state', async () => {
    const pending = { job_id: 'sent', kind: 'market_publish', state: 'completed', result: { artifact_id: 'built', share: share({ status: 'pending_review' }) } };
    const h = harness(async (url) => url === '/jobs' ? { jobs: [pending] } : { job: { job_id: 'new', state: 'queued' } });
    h.page.state.jobs = [{ job_id: 'old', kind: 'build', state: 'interrupted' }];
    await h.page.action('retry-job', 'old');
    assert.equal(h.calls[0].url, '/jobs/old/retry');
    const row = h.document.getElementById('library-jobs').children[0];
    assert.match(row.children[1].textContent, /knowledgeLibrary.sharePendingReview/);
    assert.doesNotMatch(row.children[1].textContent, /knowledgeLibrary.sharePublished/);
});

test('a task already completed at its first poll refreshes imported documents and built artifacts', async () => {
    const h = harness(async (url) => url === '/jobs' ? { jobs: [{ job_id: 'fast', kind: 'index', library_id: 'a', state: 'completed' }, { job_id: 'build', kind: 'build', library_id: 'a', state: 'completed' }] } : url === '/libraries' ? { libraries: [library('a')] } : url === '/packages' ? { packages: [{ artifact_id: 'built', name: 'built', version: '1' }] } : { library: library('a'), documents: [{ document_id: 'fast-import', filename: 'small.txt' }] });
    h.page.state.libraryId = 'a'; await h.page.refreshJobs();
    assert.equal(h.page.state.documents[0].document_id, 'fast-import');
    assert.equal(h.page.state.packages[0].artifact_id, 'built');
    assert.equal(h.page.state.reconcileNeeded, false);
});

test('task completion during a dialog reconciles after closing without replacing the dialog', async () => {
    const h = harness(async (url) => url === '/jobs' ? { jobs: [{ job_id: 'fast', kind: 'index', library_id: 'a', state: 'completed' }] } : url === '/libraries' ? { libraries: [library('a')] } : url === '/packages' ? { packages: [] } : { library: library('a'), documents: [{ document_id: 'new', filename: 'small.txt' }] });
    h.page.state.libraryId = 'a'; await h.page.action('create'); const config = h.page.state.dialog;
    await h.page.refreshJobs(); assert.equal(h.page.state.dialog, config);
    assert.equal(h.page.state.reconcileNeeded, true); assert.equal(h.page.state.documents.length, 0);
    h.page.closeDialog(); await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.page.state.documents[0].document_id, 'new'); assert.equal(h.page.state.reconcileNeeded, false);
});

test('initial static buttons stay disabled until the controller has installed its handlers', async () => {
    const template = fs.readFileSync(path.resolve(__dirname, '../../templates/knowledge_library.html'), 'utf8');
    const buttons = template.match(/<button\b[^>]*>/g);
    assert.ok(buttons.length > 0); assert.ok(buttons.every((tag) => tag.includes('disabled data-library-startup')));
    const h = harness(async (url) => url === '/libraries' ? { libraries: [] } : url === '/market/status' ? { authenticated: false, market_url: 'https://market.test' } : { jobs: [] });
    const create = new Element('button'); create.dataset.action = 'create'; create.disabled = true;
    h.document.querySelectorAll = (selector) => selector === '[data-library-startup]' || selector === '[data-action]' ? [create] : [];
    await h.page.mount(); assert.equal(create.disabled, false);
    assert.equal(typeof h.document.getElementById('knowledge-library-app').listeners.click, 'function');
});

test('publication identity conflicts keep the original account and request semantics visible', async () => {
    let code = 'market_request_conflict';
    const h = harness(async () => { throw new Error(code); });
    await h.page.action('retry-job', 'original');
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.marketRequestConflict');
    code = 'market_publisher_changed';
    await h.page.action('retry-job', 'original');
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.marketAccountChanged');
});

test('failed preview release waits for server cleanup and hides actions only after its real discarded projection', async () => {
    const cleanup = deferred(); let discarded = false;
    const h = harness(async (url) => {
        if (url === '/previews/retained') { await cleanup.promise; discarded = true; return { ok: true }; }
        return { jobs: [{ job_id: 'failed', kind: 'preview', state: 'failed', preview_id: 'retained', input_discarded: discarded, result: null }] };
    });
    await h.page.refreshJobs();
    const actions = () => h.document.getElementById('library-jobs').children[0].children[0].children[1].children.map((element) => element.dataset.action);
    assert.deepEqual(actions(), ['retry-job', 'discard-preview']);
    const release = h.page.action('discard-preview', 'retained');
    assert.equal(h.page.state.jobs[0].input_discarded, false);
    assert.equal(h.calls.filter((call) => call.url === '/jobs').length, 1);
    cleanup.resolve(); await release;
    assert.equal(h.calls.find((call) => call.url === '/previews/retained').options.method, 'DELETE');
    assert.equal(h.page.state.jobs[0].input_discarded, true);
    assert.deepEqual(actions(), []);
});

test('consumed preview and indexing tasks do not expose stale preview or retry actions', async () => {
    const jobs = [
        { job_id: 'ready', kind: 'preview', state: 'completed', preview_id: 'ready-input', input_discarded: false, result: { preview_id: 'ready-input' } },
        { job_id: 'consumed', kind: 'preview', state: 'completed', preview_id: 'used-input', input_discarded: true, result: { preview_id: 'used-input' } },
        { job_id: 'released', kind: 'index', state: 'interrupted', preview_id: 'used-input', input_discarded: true },
    ];
    const h = harness(async () => ({ jobs })); await h.page.refreshJobs();
    const rows = h.document.getElementById('library-jobs').children;
    assert.equal(rows[0].children[0].children[1].children[0].dataset.action, 'preview');
    assert.equal(rows[1].children[0].children[1].children.length, 0);
    assert.equal(rows[2].children[0].children[1].children.length, 0);
    assert.equal(rows[2].children[1].textContent, 'knowledgeLibrary.previewDiscarded');
});

test('cleared preview and missing explicit update target produce actionable translated errors', async () => {
    let code = 'preview_input_discarded';
    const h = harness(async () => { throw new Error(code); });
    await h.page.action('retry-job', 'original');
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.previewInputDiscarded');
    code = 'package_update_requires_target';
    await h.page.action('retry-job', 'original');
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.updateRequiresTarget');
});

test('export cancellation and a closed confirmation cannot download the selected artifact', async () => {
    const h = harness(async () => ({ package: scope('artifact-a') }));
    await h.page.action('package-export', 'artifact-a');
    const confirmation = h.page.state.dialog;
    assert.equal(confirmation.description, 'knowledgeLibrary.exportScopeHint');
    const source = confirmation.content;
    assert.equal(source.children[3].children[0].textContent, 'selected-private-file.txt · knowledgeLibrary.scopeCharacters');
    h.page.closeDialog(); await confirmation.submit();
    assert.equal(h.created.some((element) => element.clicked && element.tagName === 'a'), false);
    assert.equal(h.calls.some((call) => call.url.startsWith('/market/')), false);
});

test('late artifact scope from library A cannot reopen after selecting library B', async () => {
    const pending = deferred();
    const h = harness(async (url) => url.endsWith('/preview') ? pending.promise : url === '/packages' ? { packages: [] } : { library: library('b'), documents: [] });
    const old = h.page.publish('artifact-a');
    await h.page.selectLibrary('b');
    pending.resolve({ package: scope('artifact-a') }); await old;
    assert.equal(h.page.state.dialog, null);
    assert.equal(h.calls.some((call) => call.url === '/market/publish'), false);
});

test('publication scope stays bound to artifact A and stores no private file scope in retry metadata', async () => {
    const h = harness(async (url) => url.endsWith('/preview') ? { package: scope('artifact-a') } : url === '/market/publish' ? { job: { job_id: 'sent' } } : { jobs: [] });
    await h.page.publish('artifact-a');
    const confirmation = h.page.state.dialog;
    // Draft selection and artifact catalogue updates cannot rewrite this review.
    h.page.state.libraryId = 'b'; h.page.state.selected = new Set(['not-selected-for-a']);
    h.page.state.packages = [{ artifact_id: 'artifact-b', name: 'Different package' }];
    assert.equal(confirmation.description.includes('knowledgeLibrary.publishScopeHint'), true);
    assert.equal(confirmation.content.children[5].children[3].children[0].textContent.startsWith('selected-private-file.txt'), true);
    await confirmation.submit('Public title');
    const published = h.calls.find((call) => call.url === '/market/publish');
    assert.equal(published.options.body.artifact_id, 'artifact-a');
    assert.equal(JSON.stringify([...h.storage.values()]).includes('selected-private-file.txt'), false);
    assert.equal(JSON.stringify([...h.storage.values()]).includes('not-selected-for-a'), false);
});

test('publication cancellation and mismatched artifact review never submit', async () => {
    const h = harness(async () => ({ package: scope('artifact-a') }));
    await h.page.publish('artifact-a'); const confirmation = h.page.state.dialog;
    h.page.closeDialog(); await confirmation.submit('Cancelled title');
    assert.equal(h.calls.some((call) => call.url === '/market/publish'), false);
    await assert.rejects(h.page.publish('artifact-b'), /package_artifact_hash_mismatch/);
    assert.equal(h.page.state.dialog, null);
});

test('actual indirect action and scope keys exist in every supported locale', async () => {
    const h = harness(async (url) => url.endsWith('/search') ? { hits: [{ filename: 'source.txt', text: 'Saved text', location: { page: 1, paragraph: 2, section: 'Introduction' } }], semantic_state: 'ready' } : url.endsWith('/preview') ? { package: scope('artifact-a') } : url === '/packages' ? { packages: [{ artifact_id: 'artifact-a', name: 'A', version: '1' }] } : { library: library('a'), documents: [{ document_id: 'one', filename: 'source.txt', chunks_count: 1, semantic_state: 'ready' }] });
    await h.page.selectLibrary('a');
    await h.page.search('saved');
    await h.page.action('package-export', 'artifact-a');
    await h.page.publish('artifact-a');
    for (const key of ['view', 'replace', 'sourcePage', 'sourceParagraph', 'sourceSection', 'exportScopeHint', 'publishScopeHint', 'packageContents', 'scopeDocuments', 'scopeCharacters']) {
        assert.equal(h.usedKeys.has('knowledgeLibrary.' + key), true, key);
    }
    for (const language of ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW']) {
        const locale = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../static/locales/' + language + '.json'), 'utf8'));
        for (const key of h.usedKeys) {
            assert.equal(typeof key.split('.').reduce((value, part) => value && value[part], locale), 'string', language + ': ' + key);
        }
    }
});

test('oversized persisted package data produces a translated actionable reason', async () => {
    let reason = 'metadata_too_large';
    const h = harness(async () => { throw new Error(reason); });
    await h.page.action('retry-job', 'original');
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.metadataTooLarge');
    reason = 'input_token_limit';
    await h.page.action('retry-job', 'original');
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.inputTokenLimit');
    for (const busy of ['embedding_busy', 'jobs_busy']) {
        reason = busy;
        await h.page.action('retry-job', 'original');
        assert.equal(h.text('library-feedback'), 'knowledgeLibrary.jobsBusy');
    }
});

test('completed operations show an unconfirmed sync warning without a duplicate retry', async () => {
    const job = { job_id: 'committed', kind: 'install', state: 'completed', reason: '', result: { warnings: ['storage_sync_unconfirmed'] } };
    const h = harness(async (url) => url === '/jobs' ? { jobs: [job] } : { libraries: [] });
    await h.page.refreshJobs();
    const row = h.document.getElementById('library-jobs').children[0];
    assert.equal(row.children[1].textContent, 'knowledgeLibrary.storageSyncUnconfirmed');
    assert.equal(row.children[0].children[1].children.length, 0);
    assert.equal(h.calls.some((call) => call.url.endsWith('/retry')), false);
});

test('create and rename preserve the committed storage warning after refreshing their library', async () => {
    for (const name of ['create', 'rename']) {
        const h = harness(async (url, options) => options?.method ? { library: { ...library('a'), warnings: ['storage_sync_unconfirmed'] } } : url === '/libraries' ? { libraries: [library('a')] } : url === '/packages' ? { packages: [] } : { library: library('a'), documents: [] });
        h.page.state.library = library('a'); h.page.state.libraryId = 'a';
        await h.page.action(name); await h.page.state.dialog.submit('Saved name');
        assert.equal(h.text('library-feedback'), 'knowledgeLibrary.storageSyncUnconfirmed', name);
        assert.equal(h.document.getElementById('library-feedback').dataset.tone, 'info');
        assert.equal(h.calls.filter((call) => call.options?.method).length, 1);
    }
});

test('repeated scope clicks share bounded UI admission instead of starting more validations', async () => {
    const pending = deferred();
    const h = harness(async () => pending.promise);
    const first = h.page.action('package-export', 'artifact-a');
    assert.equal(h.page.state.busy, true);
    await h.page.action('market-publish', 'artifact-a');
    await h.page.action('package-export', 'artifact-a');
    assert.equal(h.calls.length, 1);
    pending.resolve({ package: scope('artifact-a') }); await first;
    assert.equal(h.page.state.busy, false);
    assert.equal(h.page.state.dialog.title, 'export');
});

function jobFeedbackHarness() {
    const jobs = [{ job_id: 'owned', kind: 'preview', library_id: 'a', state: 'queued' }];
    const flags = { failPolling: false, failLibraries: false };
    const h = harness(async (url, options) => {
        // Each HTTP poll gets fresh records; mutating the server must not also
        // mutate the controller's previously observed state.
        if (url === '/jobs') { if (flags.failPolling) throw new Error('request_timeout'); return { jobs: jobs.map((job) => ({ ...job })) }; }
        if (url === '/jobs/other/retry') throw new Error('market_unavailable');
        if (url === '/libraries/a/documents') return { job: { ...jobs[0] } };
        if (options?.method === 'PATCH') return { library: { ...library('a'), warnings: ['storage_sync_unconfirmed'] } };
        if (url === '/libraries') { if (flags.failLibraries) throw new Error('request_timeout'); return { libraries: [library('a')] }; }
        if (url === '/packages') return { packages: [] };
        return { library: library(url.endsWith('/b') ? 'b' : 'a'), documents: [] };
    });
    h.page.state.libraryId = 'a'; h.page.state.uploadLibraryId = 'a'; h.page.state.library = library('a');
    return { h, jobs, flags, queue: () => h.page.upload({ name: 'source.txt', size: 23 }) };
}

test('the top task notice follows its actual job through running and completion', async () => {
    const { h, jobs, queue } = jobFeedbackHarness(); await queue();
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.queued');
    jobs[0].state = 'running'; await h.page.refreshJobs();
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.running');
    jobs[0].state = 'completed'; await h.page.refreshJobs();
    assert.equal(h.document.getElementById('library-feedback').hidden, true);
    assert.equal(h.text('library-feedback'), '');
});

test('only the owned job can replace its pending notice with a terminal result', async () => {
    for (const [state, reason, result, key] of [
        ['failed', 'metadata_too_large', undefined, 'metadataTooLarge'],
        ['interrupted', 'process_interrupted', undefined, 'interruptedHint'],
        ['cancelled', '', undefined, 'cancelled'],
        ['completed', '', { warnings: ['storage_sync_unconfirmed'] }, 'storageSyncUnconfirmed'],
    ]) {
        const { h, jobs, queue } = jobFeedbackHarness(); await queue();
        jobs.unshift({ job_id: 'another-library', kind: 'build', library_id: 'b', state: 'completed', result: { warnings: ['storage_sync_unconfirmed'] } });
        await h.page.refreshJobs();
        assert.equal(h.text('library-feedback'), 'knowledgeLibrary.queued');
        Object.assign(jobs[1], { state, reason, result }); await h.page.refreshJobs();
        assert.equal(h.text('library-feedback'), 'knowledgeLibrary.' + key, state);
    }
});

test('polling a completed old job preserves a newer operation error or storage warning', async () => {
    for (const warning of [false, true]) {
        const { h, jobs, queue } = jobFeedbackHarness(); await queue();
        if (warning) {
            await h.page.action('rename'); await h.page.state.dialog.submit('Updated');
        } else await h.page.action('retry-job', 'other');
        const expected = 'knowledgeLibrary.' + (warning ? 'storageSyncUnconfirmed' : 'marketUnavailable');
        assert.equal(h.text('library-feedback'), expected);
        jobs[0].state = 'completed'; await h.page.refreshJobs();
        assert.equal(h.text('library-feedback'), expected);
    }
});

test('switching libraries clears only the previous task notice and ignores a late submission', async () => {
    const { h, jobs, queue } = jobFeedbackHarness(); await queue();
    await h.page.selectLibrary('b');
    assert.equal(h.document.getElementById('library-feedback').hidden, true);
    jobs[0].state = 'completed'; await h.page.refreshJobs();
    assert.equal(h.document.getElementById('library-feedback').hidden, true);

    const pending = deferred();
    const late = harness(async (url) => url === '/libraries/a/documents' ? pending.promise : url === '/jobs' ? { jobs } : url === '/packages' ? { packages: [] } : { library: library('b'), documents: [] });
    late.page.state.libraryId = 'a'; late.page.state.uploadLibraryId = 'a';
    const uploading = late.page.upload({ name: 'source.txt', size: 23 });
    await late.page.selectLibrary('b');
    pending.resolve({ job: { job_id: 'owned', kind: 'preview', state: 'queued', library_id: 'a' } });
    await uploading;
    assert.equal(late.document.getElementById('library-feedback').hidden, true);
});

test('a failed background poll cannot replace a newer operation error or disk-sync warning', async () => {
    for (const warning of [false, true]) {
        const { h, flags, queue } = jobFeedbackHarness(); await queue();
        if (warning) {
            await h.page.action('rename'); await h.page.state.dialog.submit('Saved');
        } else await h.page.action('retry-job', 'other');
        const expected = h.text('library-feedback');
        flags.failPolling = true;
        [...h.timers.values()][0]();
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(h.text('library-feedback'), expected);
    }
});

test('deferred task reconciliation cannot replace its committed disk-sync warning', async () => {
    const { h, jobs, flags, queue } = jobFeedbackHarness(); await queue();
    await h.page.action('rename');
    Object.assign(jobs[0], { state: 'completed', result: { warnings: ['storage_sync_unconfirmed'] } });
    await h.page.refreshJobs();
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.storageSyncUnconfirmed');
    flags.failLibraries = true;
    h.page.closeDialog(); await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.text('library-feedback'), 'knowledgeLibrary.storageSyncUnconfirmed');
    assert.equal(h.page.state.reconcileNeeded, true);
});

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const loaderPath = path.join(projectRoot, 'static', 'js', 'spine_runtime_loader.js');
const loaderSource = fs.readFileSync(loaderPath, 'utf8');
const runtimePath = path.join(projectRoot, 'static', 'libs', 'spine', 'spine-webgl-4.3.13.min.js');
const licensePath = path.join(projectRoot, 'static', 'libs', 'spine', 'SPINE-RUNTIMES-LICENSE-4.3.13.txt');

function createContext({ onAppend } = {}) {
    const appended = [];
    const head = {
        appendChild(script) {
            appended.push(script);
            if (onAppend) onAppend(script, context.window);
        }
    };
    const context = {
        window: {
            document: {
                head,
                createElement() {
                    return { dataset: {} };
                }
            }
        },
        console,
        URL,
        Promise,
        setTimeout
    };
    context.window.window = context.window;
    vm.runInNewContext(loaderSource, context, { filename: loaderPath });
    return { context, appended };
}

function compatibleRuntime() {
    return {
        SkeletonJson() {},
        SkeletonBinary() {},
        TextureAtlas() {},
        AtlasAttachmentLoader() {},
        SpineCanvas() {}
    };
}

test('runtime detection is unavailable before explicit loading', () => {
    const { context, appended } = createContext();
    const status = context.window.NekoSpineRuntime.detect();

    assert.equal(status.available, false);
    assert.equal(status.loaded, false);
    assert.equal(status.version, null);
    assert.equal(appended.length, 0);
});

test('load inserts the pinned IIFE once and exposes a compatible status', async () => {
    const { context, appended } = createContext({
        onAppend(script, window) {
            window.spine = compatibleRuntime();
            script.onload();
        }
    });
    const runtime = context.window.NekoSpineRuntime;

    const first = await runtime.load();
    const second = await runtime.load();

    assert.equal(first.available, true);
    assert.equal(first.version, '4.3.13');
    assert.deepEqual(Array.from(first.missingSymbols), []);
    assert.equal(second.available, true);
    assert.equal(appended.length, 1);
    assert.equal(appended[0].src, '/static/libs/spine/spine-webgl-4.3.13.min.js');
    assert.equal(appended[0].dataset.nekoSpineRuntime, 'true');
    assert.equal(appended[0].dataset.nekoSpineVersion, '4.3.13');
});

test('load rejects a global that does not implement the required API', async () => {
    const { context } = createContext({
        onAppend(script, window) {
            window.spine = { SkeletonJson() {} };
            script.onload();
        }
    });

    await assert.rejects(
        context.window.NekoSpineRuntime.load(),
        /Spine runtime is missing:/
    );
    const status = context.window.NekoSpineRuntime.detect();
    assert.equal(status.available, false);
    assert.equal(status.apiCompatible, false);
});

test('the vendored IIFE and license match the recorded provenance', () => {
    const digest = crypto.createHash('sha256').update(fs.readFileSync(runtimePath)).digest('hex').toUpperCase();
    assert.equal(digest, 'FE05B399895CB4F5ACAF00CA91724CBB5F6DBA7972B53D4C7D5D9131FDE5B018');
    assert.match(fs.readFileSync(runtimePath, 'utf8'), /var spine=/);
    assert.match(fs.readFileSync(licensePath, 'utf8'), /Spine Runtimes License Agreement/);
    assert.match(fs.readFileSync(licensePath, 'utf8'), /each user of the Products must obtain their own\s+Spine Editor license/);
});

test('a synchronous insertion failure can be retried', async () => {
    let attempt = 0;
    const { context, appended } = createContext({onAppend(script, window) {
        if (++attempt === 1) throw new Error('insertion failed');
        window.spine = compatibleRuntime();
        script.onload();
    }});
    await assert.rejects(context.window.NekoSpineRuntime.load(), /insertion failed/);
    assert.equal((await context.window.NekoSpineRuntime.load()).available, true);
    assert.equal(appended.length, 2);
});

test('only the pinned same-origin bundle is accepted, including an absolute URL', async () => {
    const { context, appended } = createContext({onAppend(script, window) {
        window.spine = compatibleRuntime();
        script.onload();
    }});
    await assert.rejects(context.window.NekoSpineRuntime.load({src: 'https://foreign.test/runtime.js'}), /pinned local/);
    assert.equal(appended.length, 0);
    const result = await context.window.NekoSpineRuntime.load({src: 'http://localhost/static/libs/spine/spine-webgl-4.3.13.min.js'});
    assert.equal(result.available, true);
});

test('the emote maker page only references the lazy loader, not the runtime asset', () => {
    const html = fs.readFileSync(path.join(projectRoot, 'templates', 'emote_maker.html'), 'utf8');
    assert.match(html, /spine_runtime_loader\.js/);
    assert.doesNotMatch(html, /spine-webgl-4\.3\.13\.min\.js/);
});

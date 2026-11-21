// MMD / VRM animation lists and character config keep raw file names; the
// players escape them only for the request (MMDCore.toRequestUrl and
// VRMAnimation.toRequestUrl). Names with '#', '?', '%' used to be cut off as
// URL syntax, so those animations could not play. File names are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');

function loadContext(extraGlobals = {}) {
    const context = vm.createContext({
        console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
        URL,
        encodeURIComponent,
        decodeURIComponent,
        setTimeout,
        clearTimeout,
        addEventListener() {},
        removeEventListener() {},
        ...extraGlobals,
    });
    context.window = context;
    context.location = { href: 'http://neko.test/model_manager' };
    for (const file of ['static/mmd/mmd-core.js', 'static/mmd/mmd-animation.js', 'static/vrm/vrm-animation.js']) {
        vm.runInContext(fs.readFileSync(path.join(projectRoot, file), 'utf8'), context, { filename: file });
    }
    return context;
}

const ctx = loadContext();
const mmdToRequestUrl = url => vm.runInContext('MMDCore.toRequestUrl', ctx)(url);
const vrmToRequestUrl = url => vm.runInContext('VRMAnimation.toRequestUrl', ctx)(url);

// Raw names as the animation lists return them (static and user folders).
const RAW_NAMES = ['Dance #1', 'what?', '50%', 'two words', '全身展示', 'a+b', '比 V 手势#2?%'];
const CASES = [
    { label: 'MMD', toRequestUrl: mmdToRequestUrl, prefixes: ['/static/mmd/animation/', '/user_mmd/animation/'], ext: '.vmd' },
    { label: 'VRM', toRequestUrl: vrmToRequestUrl, prefixes: ['/static/vrm/animation/', '/user_vrm/animation/'], ext: '.vrma.gz' },
];

for (const { label, toRequestUrl, prefixes, ext } of CASES) {
    test(`${label}: raw animation names become requestable paths`, () => {
        for (const prefix of prefixes) {
            for (const name of RAW_NAMES) {
                const raw = `${prefix}${name}${ext}`;
                const requestUrl = toRequestUrl(raw);
                const parsed = new URL(requestUrl, 'http://neko.test/');
                // Nothing leaks into query or fragment, and the server decodes back to the raw path.
                assert.equal(parsed.search, '', raw);
                assert.equal(parsed.hash, '', raw);
                assert.equal(decodeURIComponent(parsed.pathname), raw);
                assert.equal(requestUrl, prefix + encodeURIComponent(name + ext));
            }
        }
    });

    test(`${label}: already-encoded paths are not encoded twice`, () => {
        const prefix = prefixes[1];
        for (const name of RAW_NAMES) {
            const encoded = prefix + encodeURIComponent(name + ext);
            assert.equal(toRequestUrl(encoded), encoded);
        }
    });

    test(`${label}: plain names and non-local URLs are unchanged`, () => {
        assert.equal(toRequestUrl(`${prefixes[0]}wait01${ext}`), `${prefixes[0]}wait01${ext}`);
        for (const url of [`http://neko.test${prefixes[1]}a#b${ext}`, `relative/a#b${ext}`, '', null]) {
            assert.equal(toRequestUrl(url), url);
        }
    });
}

test('VRM: configured VRM_PATHS prefixes are escaped too', () => {
    const custom = loadContext();
    custom.VRM_PATHS = { user_vrm: '/custom_vrm', static_vrm: '/static/vrm' };
    assert.equal(
        vm.runInContext('VRMAnimation.toRequestUrl', custom)('/custom_vrm/animation/a#b.vrma'),
        '/custom_vrm/animation/a%23b.vrma',
    );
});

test('MMD player hands the escaped URL to VMDLoader', async () => {
    const loaded = [];
    const local = loadContext({ THREE: {} });
    const MMDAnimation = vm.runInContext('MMDAnimation', local);
    const animation = new MMDAnimation({ currentModel: { mesh: {} } });
    animation._getMMDModule = async () => ({
        VMDLoader: class {
            load(url, onLoad, onProgress, onError) { loaded.push(url); onError(new Error('fixture stop')); }
        },
    });
    await assert.rejects(animation.loadAnimation('/user_mmd/animation/Dance #1.vmd'), /fixture stop/);
    assert.deepEqual(loaded, ['/user_mmd/animation/Dance%20%231.vmd']);
});

test('VRM player requests the escaped URL for .vrma and .vrma.gz', async () => {
    const fetched = [];
    const local = loadContext({
        fetch: async (url, init) => {
            fetched.push({ url, init });
            return { ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer };
        },
        document: { baseURI: 'http://neko.test/' },
    });
    const VRMAnimation = vm.runInContext('VRMAnimation', local);
    const animation = new VRMAnimation({ currentModel: null, core: {} });
    const calls = [];
    const loader = {
        loadAsync: async url => { calls.push(['loadAsync', url]); return {}; },
        parseAsync: async (data, resourcePath) => { calls.push(['parseAsync', resourcePath]); return {}; },
    };

    await animation._loadVRMAGltf(loader, '/user_vrm/animation/Wave #1?.vrma');
    await animation._loadVRMAGltf(loader, '/static/vrm/animation/比 V 手势#2.vrma.gz');

    assert.deepEqual(calls, [
        ['loadAsync', '/user_vrm/animation/Wave%20%231%3F.vrma'],
        ['parseAsync', 'http://neko.test/static/vrm/animation/'],
    ]);
    assert.deepEqual(fetched.map(entry => entry.url), [
        '/static/vrm/animation/%E6%AF%94%20V%20%E6%89%8B%E5%8A%BF%232.vrma.gz',
    ]);
});

// Jukebox song / action entries keep raw file names (uploads keep the original
// name). resolveJukeboxFileUrl escapes them for the request, so names with '#',
// '?', '%' are no longer cut off as URL syntax by the HEAD preflight (which then
// reported the song or action as missing) or by playback. File names are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');

function loadContext(files) {
    const context = vm.createContext({
        console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
        URL,
        encodeURIComponent,
        decodeURIComponent,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        addEventListener() {},
        removeEventListener() {},
    });
    context.window = context;
    context.location = { href: 'http://neko.test/', origin: 'http://neko.test' };
    context.Jukebox = {};
    context.t = (_key, fallback) => fallback;
    for (const file of files) {
        vm.runInContext(fs.readFileSync(path.join(projectRoot, file), 'utf8'), context, { filename: file });
    }
    return context;
}

const ctx = loadContext(['static/jukebox/jukebox/transport.js']);
const Jukebox = ctx.Jukebox;
const resolve = value => Jukebox.resolveJukeboxFileUrl(value);

const RAW_NAMES = ['Song #1', 'what?', '100%', 'two words', '夜に駆ける', '比 V 手势#2?%'];
// [stored value prefix, path the server should see]
const LOCATIONS = [
    ['songs/', '/api/jukebox/file/songs/'],
    ['actions/', '/api/jukebox/file/actions/'],
    ['/api/jukebox/file/actions/', '/api/jukebox/file/actions/'],
    ['static/jukebox/actions/', '/api/jukebox/file/actions/'],
    ['/static/jukebox/', '/api/jukebox/file/'],
    ['/user_vrm/animation/', '/user_vrm/animation/'],
    ['/user_mmd/animation/', '/user_mmd/animation/'],
];
const EXTENSIONS = ['.mp3', '.flac', '.vmd', '.vrma', '.vrma.gz'];

test('raw names with # ? % space and CJK become requestable paths', () => {
    for (const [stored, served] of LOCATIONS) {
        for (const name of RAW_NAMES) {
            for (const ext of EXTENSIONS) {
                const raw = `${stored}${name}${ext}`;
                const url = resolve(raw);
                const parsed = new URL(url, 'http://neko.test/');
                // Nothing leaks into query or fragment; the server decodes back to the raw name.
                assert.equal(parsed.search, '', raw);
                assert.equal(parsed.hash, '', raw);
                assert.equal(decodeURIComponent(parsed.pathname), `${served}${name}${ext}`, raw);
                // Resolving the result again changes nothing (no double escaping).
                assert.equal(resolve(url), url, raw);
            }
        }
    }
});

test('plain names and non-local URLs are unchanged', () => {
    const expected = {
        'songs/song_001.mp3': '/api/jukebox/file/songs/song_001.mp3',
        'actions/song_001.vrma': '/api/jukebox/file/actions/song_001.vrma',
        '/static/jukebox/actions/song_001.vrma': '/api/jukebox/file/actions/song_001.vrma',
        'static/jukebox/song_001.vrma': '/api/jukebox/file/song_001.vrma',
        '/api/jukebox/file/songs/song_001.mp3': '/api/jukebox/file/songs/song_001.mp3',
        '/user_vrm/animation/wait03.vrma': '/user_vrm/animation/wait03.vrma',
        '/static/vrm/animation/wait03.vrma.gz': '/static/vrm/animation/wait03.vrma.gz',
        '/api/other/endpoint?id=1#x': '/api/other/endpoint?id=1#x',
        'https://cdn.example.test/a b#c.mp3': 'https://cdn.example.test/a b#c.mp3',
        'blob:http://neko.test/1234': 'blob:http://neko.test/1234',
        'data:audio/mp3;base64,AAAA': 'data:audio/mp3;base64,AAAA',
        '': '',
        '   ': '',
    };
    for (const [raw, url] of Object.entries(expected)) {
        assert.equal(resolve(raw), url, raw);
    }
    assert.equal(resolve(null), '');
    assert.equal(resolve(undefined), '');
});

test('a query or fragment after the extension is kept as URL syntax', () => {
    assert.equal(resolve('actions/Dance #1.vrma?v=1'), '/api/jukebox/file/actions/Dance%20%231.vrma?v=1');
    assert.equal(resolve('songs/a b.MP3#t=5'), '/api/jukebox/file/songs/a%20b.MP3#t=5');
    assert.equal(resolve('/user_vrm/animation/x.vrma.gz?legacy=1'), '/user_vrm/animation/x.vrma.gz?legacy=1');
    // A value ending in the extension is a file name throughout.
    assert.equal(resolve('songs/what?.mp3'), '/api/jukebox/file/songs/what%3F.mp3');
});

test('output is left unchanged by MMDCore.toRequestUrl (no double escaping)', () => {
    const mmd = loadContext(['static/mmd/mmd-core.js']);
    const toRequestUrl = vm.runInContext('MMDCore.toRequestUrl', mmd);
    for (const name of RAW_NAMES) {
        const url = resolve(`/user_mmd/animation/${name}.vmd`);
        assert.equal(toRequestUrl(url), url, name);
    }
});

// Simulated backend: a file is found only when the request carries no query or
// fragment and the decoded path is an existing raw name.
function makeServer(rawPaths) {
    const files = new Set(rawPaths);
    const requests = [];
    const fetch = async (url, init = {}) => {
        requests.push({ url, method: init.method || 'GET' });
        const parsed = new URL(url, 'http://neko.test/');
        const ok = !parsed.search && !parsed.hash && files.has(decodeURIComponent(parsed.pathname));
        return { ok, status: ok ? 200 : 404 };
    };
    return { fetch, requests };
}

function stubPlayback(modelType) {
    const calls = { audio: [], vmd: [], vrma: [] };
    Object.assign(Jukebox, {
        State: {
            songs: [],
            playbackMode: 'order',
            randomQueueExitSongId: null,
            playCancelEpoch: 0,
            playRequestId: 0,
            audioOwnerRequestId: null,
            currentSong: null,
            boundPlayer: null,
        },
        getModelType: () => modelType,
        getPlayer: () => ({
            list: { clear() {}, add(items) { calls.audio.push(items[0].url); } },
            options: {},
            on() {},
            play() {},
        }),
        stopPlayback() {},
        confirmAudioStarted: async () => true,
        playVMD: async url => { calls.vmd.push(url); return true; },
        playVRMA: async url => { calls.vrma.push(url); return true; },
        updatePlayingStatus() {},
        updateCalibrationDisplay() {},
        settleIdleRestore() {},
        showError() {},
        formatControlSong: song => ({ id: song.id }),
    });
    return calls;
}

for (const [modelType, format, ext, callKey] of [['mmd', 'vmd', '.vmd', 'vmd'], ['vrm', 'vrma', '.vrma', 'vrma']]) {
    test(`${modelType}: preflight passes and playback requests the same URL`, async () => {
        const song = {
            id: 'song_special',
            name: 'Song #1?',
            audio: 'songs/Song #1? 100%.mp3',
            actions: { [format]: { id: 'act', name: 'Dance', format, file: `actions/Dance #1? 50% 全身${ext}` } },
        };
        const server = makeServer([
            '/api/jukebox/file/songs/Song #1? 100%.mp3',
            `/api/jukebox/file/actions/Dance #1? 50% 全身${ext}`,
        ]);
        ctx.fetch = server.fetch;
        const calls = stubPlayback(modelType);
        Jukebox.getActionForModel = s => s.actions[format];

        const preflight = await Jukebox.preflightSongPlayback(song);
        assert.equal(preflight.ok, true);
        assert.equal(preflight.actionAvailability.ok, true);
        assert.equal(preflight.actionAvailability.status, 'action_ready');

        const played = await Jukebox.playSong(song.id, {
            song,
            actionAvailability: preflight.actionAvailability,
            forceReplay: true,
        });
        assert.equal(played, song);

        const headUrls = server.requests.filter(r => r.method === 'HEAD').map(r => r.url);
        assert.deepEqual(headUrls, [calls.audio[0], calls[callKey][0]]);
        assert.equal(calls.audio.length, 1);
        assert.equal(calls[callKey].length, 1);
        // What playback loads is what the server can serve.
        for (const url of [...calls.audio, ...calls[callKey]]) {
            assert.equal((await server.fetch(url)).ok, true, url);
        }
    });
}

test('plain names still preflight and play as before', async () => {
    const song = {
        id: 'song_001',
        name: 'plain',
        audio: 'songs/song_001.mp3',
        actions: { vmd: { id: 'a1', name: 'a1', format: 'vmd', file: 'actions/song_001.vmd' } },
    };
    const server = makeServer(['/api/jukebox/file/songs/song_001.mp3', '/api/jukebox/file/actions/song_001.vmd']);
    ctx.fetch = server.fetch;
    const calls = stubPlayback('mmd');
    Jukebox.getActionForModel = s => s.actions.vmd;

    const preflight = await Jukebox.preflightSongPlayback(song);
    assert.equal(preflight.ok, true);
    assert.equal(preflight.actionAvailability.ok, true);
    await Jukebox.playSong(song.id, { song, actionAvailability: preflight.actionAvailability });
    assert.deepEqual(calls.audio, ['/api/jukebox/file/songs/song_001.mp3']);
    assert.deepEqual(calls.vmd, ['/api/jukebox/file/actions/song_001.vmd']);
});

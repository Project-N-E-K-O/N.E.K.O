const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const exporters = [
    {
        name: 'character manager',
        file: 'static/js/character_card_manager/character-data-and-transfer.js',
        start: 'async function _downloadBlobAs(',
        end: 'function _filenameFromContentDisposition(',
        functionName: '_downloadBlobAs',
        expectedReturnValue: true,
        parserStart: 'function _filenameFromContentDisposition(',
        parserEnd: 'async function exportCharacterCard(',
        parseFilename: (context, header) => context._filenameFromContentDisposition(header, '悠怡_角色卡.png'),
        filenames: ['YUI.png', '悠怡_设定.nekocfg'],
    },
    {
        name: 'card maker',
        file: 'static/js/card_maker.js',
        start: '    async function saveFile(',
        end: '    // ====== 图层列表面板 ======',
        functionName: 'saveFile',
        expectedReturnValue: undefined,
        parserStart: '    function parseFilename(',
        parserEnd: '    async function saveFile(',
        parseFilename: (context, header) => context.parseFilename({ headers: { get: () => header } }),
        filenames: ['悠怡_角色卡.png'],
    },
];

function loadExporter(exporter, hasPicker) {
    const source = fs.readFileSync(path.join(root, exporter.file), 'utf8');
    const start = source.indexOf(exporter.start);
    const end = source.indexOf(exporter.end, start);
    assert.ok(start >= 0 && end > start, 'download function exists');
    const downloads = [];
    const emptyFiles = [];
    const urls = new Map();
    const timers = [];
    const children = new Set();
    const window = {};
    if (hasPicker) {
        window.showSaveFilePicker = async ({ suggestedName }) => {
            // Electron can create the destination before rejecting the write.
            emptyFiles.push(suggestedName);
            return {
                async createWritable() {
                    throw new DOMException('Write permission denied', 'NotAllowedError');
                },
            };
        };
    }
    const body = {
        appendChild(anchor) {
            children.add(anchor);
            anchor.parentNode = body;
        },
        removeChild(anchor) {
            children.delete(anchor);
            anchor.parentNode = null;
        },
    };
    const context = vm.createContext({
        window,
        currentCharaName: '悠怡',
        document: {
            body,
            createElement(tag) {
                assert.equal(tag, 'a');
                return {
                    click() {
                        assert.ok(children.has(this), 'link is attached when clicked');
                        assert.ok(urls.has(this.href), 'download starts with a live Blob URL');
                        downloads.push({ filename: this.download, url: this.href, blob: urls.get(this.href) });
                    },
                    remove() { body.removeChild(this); },
                };
            },
        },
        URL: {
            createObjectURL(blob) {
                const url = `blob:export-${urls.size}`;
                urls.set(url, blob);
                return url;
            },
            revokeObjectURL(url) { urls.delete(url); },
        },
        setTimeout(callback, delay) { timers.push({ callback, delay }); },
    });
    vm.runInContext(source.slice(start, end), context, { filename: exporter.file });
    const parserStart = source.indexOf(exporter.parserStart);
    const parserEnd = source.indexOf(exporter.parserEnd, parserStart);
    assert.ok(parserStart >= 0 && parserEnd > parserStart, 'filename parser exists');
    vm.runInContext(source.slice(parserStart, parserEnd), context, { filename: exporter.file });
    return {
        save: context[exporter.functionName],
        parseFilename: header => exporter.parseFilename(context, header),
        downloads, emptyFiles, urls, timers, children,
    };
}

for (const exporter of exporters) {
    for (const hasPicker of [true, false]) {
        for (const filename of exporter.filenames) {
            test(`${exporter.name}: ${filename}, save picker ${hasPicker ? 'present' : 'absent'}`, async () => {
                const state = loadExporter(exporter, hasPicker);
                const bytes = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0, 255, 128]);
                const blob = new Blob([bytes], { type: 'application/octet-stream' });
                const saved = await state.save(blob, filename);

                assert.equal(saved, exporter.expectedReturnValue, 'download handoff result preserves the caller contract');
                assert.deepEqual(state.emptyFiles, [], 'no preliminary save creates an empty file');
                assert.equal(state.downloads.length, 1, 'exactly one download reaches the host');
                const download = state.downloads[0];
                assert.equal(download.filename, filename, 'Unicode name and extension are preserved');
                assert.deepEqual(new Uint8Array(await download.blob.arrayBuffer()), bytes, 'all binary bytes survive');
                assert.equal(state.children.size, 0, 'temporary link is removed');
                assert.ok(state.urls.has(download.url), 'URL survives asynchronous download handoff');
                assert.equal(state.timers.length, 1, 'URL cleanup is scheduled');
                assert.ok(state.timers[0].delay > 0, 'cleanup is delayed');
                state.timers[0].callback();
                assert.equal(state.urls.size, 0, 'Blob URL is eventually released');
            });
        }
    }
    for (const { name, header, expected } of [
        {
            name: 'UTF-8 filename takes precedence',
            header: `attachment; filename="fallback.png"; filename*=UTF-8''${encodeURIComponent('服务端_角色卡.png')}`,
            expected: '服务端_角色卡.png',
        },
        {
            name: 'quoted filename',
            header: 'attachment; filename="server-card.png"',
            expected: 'server-card.png',
        },
        {
            name: 'missing filename header',
            header: null,
            expected: '悠怡_角色卡.png',
        },
    ]) {
        test(`${exporter.name}: ${name}`, () => {
            const state = loadExporter(exporter, false);
            assert.equal(state.parseFilename(header), expected);
        });
    }
}

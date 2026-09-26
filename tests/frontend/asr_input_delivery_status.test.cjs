const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/app/app-websocket.js'), 'utf8');
const start = source.indexOf("                    if (statusCode === 'ASR_INPUT_CONNECTING'");
const end = source.indexOf("                    if (statusCode === 'ASR_LIFECYCLE_STATE')", start);
assert.ok(start >= 0 && end > start);
const handler = `(function(statusCode) { ${source.slice(start, end)} })`;

for (const locale of ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'pt', 'es']) {
    test(`${locale}: delivery states have distinct localized user messages`, () => {
        const messages = require(path.join(root, 'static/locales', `${locale}.json`)).microphone;
        const seen = [];
        const fn = vm.runInNewContext(handler, {window: {
            t: key => messages[key.split('.')[1]],
            showStatusToast: (text, duration) => seen.push({text, duration}),
        }});
        for (const code of ['ASR_INPUT_CONNECTING', 'ASR_INPUT_DELIVERY_FAILED', 'ASR_INPUT_DELIVERY_UNCERTAIN']) fn(code);
        assert.equal(new Set(seen.map(x => x.text)).size, 3);
        assert.ok(seen.every(x => typeof x.text === 'string' && x.text.length > 15));
        assert.equal(seen[0].duration, 3000);
        fn('ASR_READY');
        assert.equal(seen.length, 3);
    });
}

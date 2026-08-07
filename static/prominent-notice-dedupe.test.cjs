const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const browserNoticeSource = fs.readFileSync(
    path.join(repoRoot, 'static', 'app', 'app-ui', 'bootstrap-goodbye-and-toasts.js'),
    'utf8',
);
const desktopToastSource = fs.readFileSync(
    path.join(repoRoot, 'templates', 'toast.html'),
    'utf8',
);

function sourceBetween(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

function buildNoticeHarness({ source, stateStart, renderName, showName }) {
    const stateAndKey = sourceBetween(
        source,
        stateStart,
        `function ${renderName}(`,
    );
    const showFunction = sourceBetween(
        source,
        `function ${showName}(`,
        source === desktopToastSource ? '// ===== IPC' : 'I.mod.showProminentNotice',
    );
    const rendered = [];
    let dismissActive = null;
    const context = {
        rendered,
        Promise,
        String,
        JSON,
        setRenderCallback(callback) {
            dismissActive = callback;
        },
    };

    vm.runInNewContext(`
        ${stateAndKey}
        function ${renderName}(notice, onDismiss) {
            rendered.push(JSON.parse(JSON.stringify(notice)));
            setRenderCallback(onDismiss);
        }
        ${showFunction}
        globalThis.noticeHarness = {
            show: ${showName}
        };
    `, context);

    return {
        rendered,
        show: context.noticeHarness.show,
        dismiss() {
            assert.equal(typeof dismissActive, 'function', 'expected an active notice');
            const callback = dismissActive;
            dismissActive = null;
            callback();
        },
    };
}

test('browser prominent notices render one duplicate and advance after dismissal', async () => {
    const harness = buildNoticeHarness({
        source: browserNoticeSource,
        stateStart: 'const _prominentNoticeQueue = [];',
        renderName: '_renderProminentNotice',
        showName: 'showProminentNotice',
    });
    const first = { code: 'API_KEY_REJECTED', message: 'invalid key' };
    const second = { code: 'API_RATE_LIMIT', message: 'slow down' };

    const firstDone = harness.show(first);
    await harness.show({ ...first });
    const secondDone = harness.show(second);
    await harness.show({ ...second });

    assert.deepEqual(harness.rendered, [first]);
    harness.dismiss();
    await firstDone;
    assert.deepEqual(harness.rendered, [first, second]);
    harness.dismiss();
    await secondDone;
    const repeatedAfterDismissDone = harness.show({ ...second });
    assert.deepEqual(harness.rendered, [first, second, second]);
    harness.dismiss();
    await repeatedAfterDismissDone;
});

test('desktop prominent notices render one duplicate and advance after dismissal', () => {
    const harness = buildNoticeHarness({
        source: desktopToastSource,
        stateStart: 'var pnQueue = [];',
        renderName: 'renderProminentNotice',
        showName: 'showProminentNotice',
    });
    const first = { code: 'API_KEY_REJECTED', message: 'invalid key' };
    const second = { code: 'API_RATE_LIMIT', message: 'slow down' };

    harness.show(first);
    harness.show({ ...first });
    harness.show(second);
    harness.show({ ...second });

    assert.deepEqual(harness.rendered, [first]);
    harness.dismiss();
    assert.deepEqual(harness.rendered, [first, second]);
    harness.dismiss();
    harness.show({ ...second });
    assert.deepEqual(harness.rendered, [first, second, second]);
    harness.dismiss();
});

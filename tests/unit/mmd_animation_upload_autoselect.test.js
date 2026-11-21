// After a VMD upload the model manager should select the uploaded animation.
// The options are built by the real loadMMDAnimations() from a list payload
// shaped like GET /api/model/mmd/animations, and the upload result is shaped
// like POST /api/model/mmd/upload_animation, so a mismatch between what the
// list stores and what the upload returns fails here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const PAGE_CONTROLLER_JS = path.join(
    PROJECT_ROOT, 'static', 'js', 'model_manager', 'page-controller.js');
const source = fs.readFileSync(PAGE_CONTROLLER_JS, 'utf8');

class FakeOption {
    constructor() {
        this.value = '';
        this.textContent = '';
        this.attrs = {};
    }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
}

class FakeSelect {
    constructor() {
        this.options = [];
        this.disabled = true;
    }
    set innerHTML(_html) {
        // loadMMDAnimations() resets the select with a single placeholder option.
        const placeholder = new FakeOption();
        this.options = [placeholder];
    }
    appendChild(option) { this.options.push(option); }
}

function loadMMDAnimationHelpers(listPayload) {
    const start = source.indexOf('function findUploadedMMDAnimationOption(');
    const end = source.indexOf('function updateMMDAnimationDropdown(', start);
    assert.ok(start >= 0 && end > start, 'MMD animation upload-selection helper block not found');
    const select = new FakeSelect();
    const sandbox = {
        RequestHelper: { fetchJson: async () => listPayload },
        document: { createElement: () => new FakeOption() },
        t: (_key, fallback) => fallback,
        console,
        mmdAnimations: [],
        mmdAnimationSelect: select,
        mmdAnimationSelectBtn: null,
        updateMMDAnimationDropdown() {},
        updateMMDAnimationSelectButtonText() {},
    };
    vm.createContext(sandbox);
    vm.runInContext(source.slice(start, end) + `
        globalThis.helpers = { findUploadedMMDAnimationOption, loadMMDAnimations };`, sandbox);
    return { helpers: sandbox.helpers, select };
}

// Mirrors get_mmd_animations() / _handle_mmd_file_upload() in main_routers/mmd_router.py.
function listEntry(dirUrl, filename) {
    return {
        name: filename.replace(/\.vmd$/, ''),
        filename,
        url: `${dirUrl}/${filename}`,
        type: 'vmd',
        size: 1,
    };
}

function uploadResult(filename) {
    return {
        success: true,
        message: `VMD 动画 ${filename} 上传成功`,
        filename,
        file_path: `/user_mmd/animation/${filename}`,
    };
}

const NAMES = [
    'dance.vmd',
    'my dance.vmd',
    '待机动作.vmd',
    'take#2.vmd',
    '100%.vmd',
    '舞 #1 50%.vmd',
];

for (const filename of NAMES) {
    test(`uploaded VMD "${filename}" is selected after the list reloads`, async () => {
        const others = [listEntry('/user_mmd/animation', 'other.vmd')];
        const uploaded = listEntry('/user_mmd/animation', filename);
        const { helpers, select } = loadMMDAnimationHelpers({
            success: true,
            animations: [...others, uploaded],
        });
        await helpers.loadMMDAnimations();

        const match = helpers.findUploadedMMDAnimationOption(select.options, uploadResult(filename));
        assert.ok(match, `no option matched upload of ${filename}`);
        // The list keeps the raw URL; escaping only happens when the player loads it.
        assert.equal(match.value, `/user_mmd/animation/${filename}`);
    });
}

test('uploaded VMD wins over a built-in animation with the same file name', async () => {
    const filename = 'shared name.vmd';
    const { helpers, select } = loadMMDAnimationHelpers({
        success: true,
        animations: [
            listEntry('/static/mmd/animation', filename),
            listEntry('/user_mmd/animation', filename),
        ],
    });
    await helpers.loadMMDAnimations();

    const match = helpers.findUploadedMMDAnimationOption(select.options, uploadResult(filename));
    assert.ok(match);
    assert.equal(match.value, '/user_mmd/animation/shared name.vmd');
});

test('no match leaves the caller to restore the previous selection', async () => {
    const { helpers, select } = loadMMDAnimationHelpers({
        success: true,
        animations: [listEntry('/user_mmd/animation', 'other.vmd')],
    });
    await helpers.loadMMDAnimations();

    assert.equal(helpers.findUploadedMMDAnimationOption(select.options, uploadResult('missing.vmd')), null);
    assert.equal(helpers.findUploadedMMDAnimationOption(select.options, { success: true }), null);
    assert.equal(helpers.findUploadedMMDAnimationOption(select.options, null), null);
    // Placeholder / "no motion" options have values '' and '_no_motion_' and must never match.
    assert.equal(helpers.findUploadedMMDAnimationOption(select.options, { file_path: '' }), null);
});

test('the VMD upload handler selects through findUploadedMMDAnimationOption', () => {
    const start = source.indexOf("fetch('/api/model/mmd/upload_animation'");
    const end = source.indexOf('updateMMDAnimationSelectButtonText();', start);
    assert.ok(start >= 0 && end > start, 'VMD upload handler not found');
    const handler = source.slice(start, end);
    assert.match(handler, /findUploadedMMDAnimationOption\(\s*mmdAnimationSelect\.options\s*,\s*result\s*\)/);
    assert.doesNotMatch(handler, /data-filename/);
});

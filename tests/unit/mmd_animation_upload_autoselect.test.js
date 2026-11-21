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
        this.selectedIndex = -1;
    }
    set innerHTML(_html) {
        // loadMMDAnimations() resets the select with a single placeholder option.
        const placeholder = new FakeOption();
        this.options = [placeholder];
        this.selectedIndex = 0;
    }
    appendChild(option) { this.options.push(option); }
    // Like HTMLSelectElement: an unknown value selects nothing.
    get value() {
        const selected = this.options[this.selectedIndex];
        return selected ? selected.value : '';
    }
    set value(next) {
        this.selectedIndex = this.options.findIndex(opt => opt.value === String(next));
    }
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

// Drives the real VMD upload change handler: the list reload, the selection
// assignment and the button-text refresh all run from page-controller.js.
function loadVmdUploadHandler({ listBefore, listAfter, uploadResponse }) {
    const helpersStart = source.indexOf('function findUploadedMMDAnimationOption(');
    const helpersEnd = source.indexOf('function updateMMDAnimationDropdown(', helpersStart);
    const handlerStart = source.indexOf('    // MMD 动画文件上传\n    if (mmdAnimationFileUpload) {');
    const handlerEnd = source.indexOf('    // VMD动画上传按钮点击事件', handlerStart);
    assert.ok(helpersStart >= 0 && helpersEnd > helpersStart, 'MMD animation helper block not found');
    assert.ok(handlerStart >= 0 && handlerEnd > handlerStart, 'VMD upload handler block not found');

    const select = new FakeSelect();
    let listPayload = listBefore;
    let changeHandler = null;
    const buttonTextUpdates = [];
    const sandbox = {
        RequestHelper: { fetchJson: async () => listPayload },
        document: { createElement: () => new FakeOption() },
        t: (_key, fallback) => fallback,
        console,
        FormData: class { append() {} },
        fetch: async () => {
            listPayload = listAfter;
            return { json: async () => uploadResponse };
        },
        showStatus() {},
        setControlsDisabled() {},
        mmdAnimations: [],
        mmdAnimationSelect: select,
        mmdAnimationSelectBtn: null,
        uploadMmdAnimationBtn: null,
        isMmdAnimationUploading: false,
        isMmdAnimationPlaying: false,
        mmdAnimationFileUpload: {
            value: '',
            addEventListener(type, handler) {
                if (type === 'change') changeHandler = handler;
            },
        },
        updateMMDAnimationDropdown() {},
        updateMMDAnimationSelectButtonText() {
            buttonTextUpdates.push(select.value);
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        source.slice(helpersStart, helpersEnd) + source.slice(handlerStart, handlerEnd)
            + '\nglobalThis.helpers = { loadMMDAnimations };',
        sandbox,
    );
    assert.equal(typeof changeHandler, 'function', 'VMD upload handler was not registered');
    return {
        select,
        buttonTextUpdates,
        loadInitialList: () => sandbox.helpers.loadMMDAnimations(),
        upload: filename => changeHandler({ target: { files: [{ name: filename }] } }),
    };
}

for (const filename of NAMES) {
    test(`upload handler selects the uploaded VMD "${filename}"`, async () => {
        const previous = listEntry('/user_mmd/animation', 'previous.vmd');
        const uploaded = listEntry('/user_mmd/animation', filename);
        const page = loadVmdUploadHandler({
            listBefore: { success: true, animations: [previous] },
            listAfter: { success: true, animations: [previous, uploaded] },
            uploadResponse: uploadResult(filename),
        });
        await page.loadInitialList();
        page.select.value = previous.url;
        page.buttonTextUpdates.length = 0;

        await page.upload(filename);

        assert.equal(page.select.value, `/user_mmd/animation/${filename}`);
        assert.equal(page.buttonTextUpdates.at(-1), `/user_mmd/animation/${filename}`);
    });
}

test('upload handler restores the previous selection when the upload is not listed', async () => {
    const previous = listEntry('/user_mmd/animation', 'previous.vmd');
    const page = loadVmdUploadHandler({
        listBefore: { success: true, animations: [previous] },
        // e.g. an upload the list endpoint does not return
        listAfter: { success: true, animations: [previous] },
        uploadResponse: uploadResult('unlisted.vmd'),
    });
    await page.loadInitialList();
    page.select.value = previous.url;
    page.buttonTextUpdates.length = 0;

    await page.upload('unlisted.vmd');

    assert.equal(page.select.value, previous.url);
    assert.equal(page.buttonTextUpdates.at(-1), previous.url);
});

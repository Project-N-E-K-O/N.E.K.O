const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture() {
    class Element {
        constructor() {
            this.style = {}; this.events = new Map(); this.attrs = {}; this.hidden = false;
            this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
            this.offsetWidth = 50; this.offsetHeight = 100;
        }
        addEventListener(type, fn) { if (!this.events.has(type)) this.events.set(type, new Set()); this.events.get(type).add(fn); }
        removeEventListener(type, fn) { this.events.get(type)?.delete(fn); }
        setAttribute(key, value) { this.attrs[key] = value; }
        removeAttribute(key) { delete this.attrs[key]; }
        contains() { return false; }
        fire(type) { for (const fn of this.events.get(type) || []) fn({ target: this, preventDefault() {}, stopPropagation() {} }); }
    }
    const ids = new Map();
    for (const id of ['avatar-cropper-wrap', 'avatar-cropper-img', 'avatar-cropper-mask', 'avatar-cropper-box', 'avatar-cropper-retake', 'avatar-cropper-cancel', 'avatar-cropper-save']) ids.set(id, new Element());
    const popup = new Element(), controls = new Element(), document = new Element();
    popup.querySelector = () => controls;
    document.getElementById = id => ids.get(id);
    const frames = [];
    const window = {
        appState: { dom: { chatAvatarPreviewCard: popup } },
        innerWidth: 1024, innerHeight: 768, lanlan_config: { model_type: 'live2d' }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/app-chat-avatar.js'), 'utf8'), {
        window, document, requestAnimationFrame(fn) { frames.push(fn); }, getComputedStyle: () => ({ gap: '0' })
    });
    return { api: window.appChatAvatar, ids, controls, document, frames, flush() { while (frames.length) frames.shift()(); } };
}

test('crop rectangles remain inside tiny and extreme aspect sources', async () => {
    for (const [width, height] of [[32, 32], [4000, 100], [100, 4000], [4000, 1], [1, 4000], [1, 1]]) {
        const h = fixture(); const result = h.api.openUploadCropper({ url: 'blob:test', width, height }); h.flush();
        const box = h.ids.get('avatar-cropper-box').style;
        const wrap = h.ids.get('avatar-cropper-wrap').style;
        assert.ok(parseFloat(box.width) <= parseFloat(wrap.width));
        assert.ok(parseFloat(box.height) <= parseFloat(wrap.height));
        h.ids.get('avatar-cropper-save').fire('click'); const crop = (await result).cropRect;
        assert.ok(crop.size >= 1); assert.ok(crop.x >= 0); assert.ok(crop.y >= 0);
        assert.ok(crop.x + crop.size <= width); assert.ok(crop.y + crop.size <= height);
    }
});

test('new crop session retires only old listeners and old RAF cannot mutate current layout', async () => {
    const h = fixture(); const first = h.api.openUploadCropper({ url: 'blob:first', width: 32, height: 32 });
    const second = h.api.openUploadCropper({ url: 'blob:second', width: 100, height: 100 });
    assert.equal(await first, null);
    assert.equal(h.document.events.get('pointermove').size, 1);
    assert.equal(h.controls.events.get('click').size, 1);
    h.flush(); h.ids.get('avatar-cropper-cancel').fire('click'); assert.equal(await second, null);
    assert.equal(h.document.events.get('pointermove').size, 0);
    assert.equal(h.controls.events.get('click').size, 0);
});

test('close before crop layout frame does not resurrect cancelled session', async () => {
    const h = fixture(); const result = h.api.openUploadCropper({ url: 'blob:test', width: 32, height: 32 });
    h.api.closeUploadCropper(); assert.equal(await result, null); h.flush();
    assert.equal(h.ids.get('avatar-cropper-wrap').style.width, undefined);
    assert.equal(h.document.events.get('pointermove').size, 0);
});

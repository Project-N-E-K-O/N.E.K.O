'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../../static/theme-studio/materials.js'), 'utf8');

class Element extends EventTarget {
    constructor(tagName) {
        super();
        this.tagName = tagName;
        this.attrs = {};
        this.children = [];
        this.style = { setProperty() {}, removeProperty() {} };
        this.classList = { add() {}, remove() {}, toggle() {} };
    }
    set textContent(value) { this.text = value; this.children = []; }
    setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'value') this.value = value; }
    appendChild(child) { this.children.push(child); }
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
    find(predicate) { return predicate(this) ? this : this.children.map(child => child.find(predicate)).find(Boolean); }
    click() { this.dispatchEvent(new Event('click')); }
    focus() { this.focused = true; }
}

async function panel() {
    const root = new Element('main');
    root.attrs.id = 'materials-root';
    const get = (id) => root.find(node => node.attrs.id === id);
    const byClass = (name) => root.find(node => (node.className || '').split(' ').includes(name));
    const requests = [], notices = [], downloads = [];
    const document = new EventTarget();
    document.readyState = 'loading';
    document.createTextNode = () => new Element('#text');
    document.createElement = (tag) => {
        const node = new Element(tag);
        if (tag === 'input') document.lastInput = node;
        return node;
    };
    document.getElementById = get;
    const preset = {global: {opacity: 0.4}};
    const spec = {
        params: [{section:'global', key:'opacity', kind:'number', label:'Opacity', default:0.5, min:0, max:1}],
        quality: [{id:'balanced'},{id:'high'}], presets: {clear:{params:{}}},
        current: {global:{opacity:0.5}, dark:{global:{opacity:0.2}}, quality:'balanced',
            components:{card:{mode:'custom', opacity:0.7}}, custom_presets:{saved:preset}}
    };
    const window = {
        confirm: () => true,
        showNotice: (kind, message, action) => notices.push(action),
        downloadExportResult: result => downloads.push(result),
        NekoPanel: { toast() {}, call: async (id, args) => {
            requests.push({id, args});
            if (id === 'material_spec') return spec;
            if (id === 'export_material_preset') return {filename:'saved.json',data:'e30=',mime:'application/json'};
            if (id === 'import_material_preset') return {name:'added', materials:{custom_presets:{added:preset,saved:preset}}};
            if (id === 'delete_material_preset') return {removed:preset, materials:{custom_presets:{}}};
            if (id === 'save_material_preset') return {name:'saved', materials:{custom_presets:{saved:preset}}};
            throw new Error(id);
        } }
    };
    class FileReader {
        readAsDataURL() { this.result = 'data:application/json;base64,e30='; this.onload(); }
    }
    vm.runInNewContext(source, {window, document, CustomEvent, FileReader, setTimeout(){}, clearTimeout(){}});
    document.dispatchEvent(new Event('DOMContentLoaded'));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(get('mt-tab-overview'));
    return {api:window.NekoMaterials, root, get, byClass, requests, notices, downloads, document};
}

test('both-mode adjustments replace a dark override and survive an in-flight save readback', async () => {
    const {api,get} = await panel();
    const submitted = api.snapshot();
    get('mt-mode').value = 'dark';
    get('mt-mode').dispatchEvent(new Event('change'));
    assert.equal(api.patch([{key:'opacity',value:0.8}], 'both'), 1);
    assert.equal(api.snapshot().global.opacity, 0.8);
    assert.equal(api.snapshot().dark.global, undefined);
    assert.ok(api.setQuality('high'));
    assert.equal(api.setQuality('unknown'), false);
    api.sync({materials:submitted}, {submitted});
    assert.equal(api.getQuality(), 'high');
    assert.ok(api.isDirty());
    api.applyPreset('clear');
    assert.equal(Object.keys(api.snapshot().components).length, 0);
    assert.ok(api.lastPresetNote());
});

test('import and delete undo keep the current appearance and unapplied presets', async () => {
    const {api,get,byClass,requests,notices,downloads,document} = await panel();
    get('mt-tab-presets').click();
    get('mt-preset-name').value = 'draft';
    const enter = new Event('keydown');
    enter.key = 'Enter';
    get('mt-preset-name').dispatchEvent(enter);
    const first = api.snapshot();
    assert.equal(first.preset, 'draft');
    const exportButton = byClass('mt-preset-ops');
    assert.equal(exportButton.children[0].attrs.disabled, '');
    byClass('mt-preset-import').click();
    document.lastInput.files = [{name:'added.json',size:2}];
    document.lastInput.dispatchEvent(new Event('change'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(api.snapshot().preset, 'draft');
    assert.deepEqual(Object.keys(api.snapshot().custom_presets).sort(), ['added','draft','saved']);
    const card = get('mt-pane').find(node => node.className === 'mt-preset-wrap' &&
      node.children[0].children[0].text === 'saved');
    card.children[1].children[0].click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(downloads[0].filename, 'saved.json');
    card.children[1].children[1].click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(api.snapshot().custom_presets.saved, undefined);
    notices.at(-1).handler();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(Object.keys(api.snapshot().custom_presets).sort(), ['added','draft','saved']);
    assert.equal(api.snapshot().preset, 'draft');
    assert.equal(requests.at(-1).args.apply, false);
});

test('keyboard tabs, fine adjustments, section reset and palette-only preview use the live controls', async () => {
    const {api,get,byClass} = await panel();
    const right = new Event('keydown', {cancelable:true});
    right.key = 'ArrowRight';
    get('mt-tab-overview').dispatchEvent(right);
    assert.equal(get('mt-tab-base').attrs['aria-selected'], 'true');
    assert.equal(get('mt-tab-base').focused, true);
    assert.equal(get('mt-pane').attrs['aria-labelledby'], 'mt-tab-base');
    const number = byClass('mt-num');
    const up = new Event('keydown', {cancelable:true});
    up.key = 'ArrowUp';
    up.altKey = true;
    number.dispatchEvent(up);
    assert.equal(api.snapshot().global.opacity, 0.501);
    number.dispatchEvent(new Event('dblclick'));
    assert.equal(api.snapshot().global.opacity, 0.5);
    byClass('mt-sub-reset').click();
    assert.equal(api.snapshot().dark.global, undefined);
    get('mt-pv-plain').click();
    assert.equal(get('mt-pv').attrs['data-plain'], '1');
    assert.equal(get('mt-pv-plain').attrs['aria-pressed'], 'true');
});

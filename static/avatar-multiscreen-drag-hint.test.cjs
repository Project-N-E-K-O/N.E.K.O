const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'avatar-multiscreen-drag-hint.js'), 'utf8');

function createElement(tagName, ownerDocument) {
    const element = {
        tagName: tagName.toUpperCase(),
        children: [],
        className: '',
        id: '',
        style: {},
        textContent: '',
        attributes: {},
        classList: {
            values: new Set(),
            add(value) {
                this.values.add(value);
            },
            contains(value) {
                return this.values.has(value);
            }
        },
        appendChild(child) {
            this.children.push(child);
            child.parentNode = this;
            if (child.id) ownerDocument._elementsById.set(child.id, child);
            return child;
        },
        remove() {
            if (this.id) ownerDocument._elementsById.delete(this.id);
            if (this.parentNode) {
                this.parentNode.children = this.parentNode.children.filter(child => child !== this);
            }
        },
        setAttribute(name, value) {
            this.attributes[name] = value;
        },
        addEventListener() {}
    };
    return element;
}

function createDocument() {
    const document = {
        _elementsById: new Map(),
        createElement(tagName) {
            return createElement(tagName, document);
        },
        getElementById(id) {
            return document._elementsById.get(id) || null;
        }
    };
    document.head = createElement('head', document);
    document.body = createElement('body', document);
    return document;
}

function createContext({ displays }) {
    const document = createDocument();
    const storage = new Map();
    const window = {
        innerWidth: 1000,
        innerHeight: 800,
        localStorage: {
            getItem(key) {
                return storage.has(key) ? storage.get(key) : null;
            },
            setItem(key, value) {
                storage.set(key, String(value));
            }
        },
        electronScreen: {
            async getAllDisplays() {
                return displays;
            },
            async getCurrentDisplay() {
                return displays[0];
            },
            async moveWindowToDisplay() {}
        }
    };
    const context = vm.createContext({
        window,
        document,
        Date,
        JSON,
        Number,
        Promise,
        setTimeout() {},
        requestAnimationFrame(callback) {
            callback();
        }
    });
    vm.runInContext(source, context, { filename: 'avatar-multiscreen-drag-hint.js' });
    return { window, document };
}

const twoDisplays = [
    { id: 1, screenX: 0, screenY: 0, width: 1000, height: 800 },
    { id: 2, screenX: 1000, screenY: 0, width: 900, height: 800 }
];

test('pointer edge release intent shows the drag hint after repeated attempts', async () => {
    const { window, document } = createContext({ displays: twoDisplays });
    const pointer = {
        startScreenX: 820,
        startScreenY: 400,
        screenX: 998,
        screenY: 400
    };

    const first = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('vrm', pointer);
    const second = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('vrm', pointer);

    assert.equal(first, false);
    assert.equal(second, true);
    assert.ok(document.getElementById('avatar-multiscreen-drag-hint'));
});

test('pointer edge release intent ignores edge drags without an adjacent display', async () => {
    const { window, document } = createContext({ displays: [twoDisplays[0]] });

    const result = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('mmd', {
        startScreenX: 820,
        startScreenY: 400,
        screenX: 998,
        screenY: 400
    });

    assert.equal(result, false);
    assert.equal(document.getElementById('avatar-multiscreen-drag-hint'), null);
});

test('pointer edge release intent ignores movement away from the edge', async () => {
    const { window, document } = createContext({ displays: twoDisplays });

    const result = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('live2d', {
        startScreenX: 998,
        startScreenY: 400,
        screenX: 900,
        screenY: 400
    });

    assert.equal(result, false);
    assert.equal(document.getElementById('avatar-multiscreen-drag-hint'), null);
});

test('pointer edge release intent ignores releases outside adjacent display span', async () => {
    const { window, document } = createContext({
        displays: [
            twoDisplays[0],
            { id: 2, screenX: 1000, screenY: 600, width: 900, height: 800 }
        ]
    });

    const pointer = {
        startScreenX: 820,
        startScreenY: 100,
        screenX: 998,
        screenY: 100
    };
    const first = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('mmd', pointer);
    const second = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('mmd', pointer);

    assert.equal(first, false);
    assert.equal(second, false);
    assert.equal(document.getElementById('avatar-multiscreen-drag-hint'), null);
});

test('pointer edge release intent keeps secondary edge candidates at corners', async () => {
    const { window, document } = createContext({ displays: twoDisplays });
    const pointer = {
        startScreenX: 900,
        startScreenY: 600,
        screenX: 998,
        screenY: 799
    };

    const first = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('vrm', pointer);
    const second = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('vrm', pointer);

    assert.equal(first, false);
    assert.equal(second, true);
    assert.ok(document.getElementById('avatar-multiscreen-drag-hint'));
});

test('pointer edge release intent skips a miss already recorded during the same drag', async () => {
    const { window, document } = createContext({ displays: twoDisplays });
    const startedAt = Date.now();

    const existingMiss = await window.NekoAvatarMultiScreenDragHint.recordDisplaySwitchMiss('vrm');
    const edgeMiss = await window.NekoAvatarMultiScreenDragHint.recordPointerEdgeRelease('vrm', {
        startedAt,
        startScreenX: 820,
        startScreenY: 400,
        screenX: 998,
        screenY: 400
    });

    assert.equal(existingMiss, false);
    assert.equal(edgeMiss, false);
    assert.equal(document.getElementById('avatar-multiscreen-drag-hint'), null);
});

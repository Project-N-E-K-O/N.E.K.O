const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const geometrySource = fs.readFileSync(
    path.join(__dirname, 'app', 'app-react-chat-window', 'message-bundle-actions-and-prompts.js'),
    'utf8'
);
const apiSource = fs.readFileSync(
    path.join(__dirname, 'app', 'app-react-chat-window', 'resize-drag-and-api.js'),
    'utf8'
);

function assignmentBlock(name, nextName) {
    const start = geometrySource.indexOf(`I.${name} = function ${name}`);
    const end = geometrySource.indexOf(`\n    I.${nextName}`, start);
    assert.notEqual(start, -1, `missing ${name}`);
    assert.notEqual(end, -1, `missing ${nextName} boundary`);
    return geometrySource.slice(start, end);
}

function createContext(options = {}) {
    const shell = {
        style: {},
        getBoundingClientRect: () => ({
            left: 100,
            top: 300,
            width: 500,
            height: 400,
            ...(options.shellRect || {})
        })
    };
    let compactApplication = null;
    const context = {
        I: {
            minimized: false,
            COMPACT_SURFACE_DEFAULT_HEIGHT: 96,
            getShell: () => shell,
            getCurrentChatSurfaceMode: () => options.mode || 'full',
            getCurrentCompactSurfaceRect: () => options.currentCompactRect || null,
            getCompactSurfaceTarget: () => options.compactTarget || null,
            applyCompactSurfaceRect: (left, top, width, height, applyOptions) => {
                compactApplication = { left, top, width, height, options: applyOptions };
                return compactApplication;
            }
        },
        window: {
            innerWidth: 1000,
            innerHeight: options.viewportHeight || 600
        }
    };
    vm.runInNewContext(assignmentBlock('clampPosition', 'ensureChatSurfaceVisible'), context);
    vm.runInNewContext(assignmentBlock('ensureChatSurfaceVisible', 'applyPosition'), context);
    return { context, shell, getCompactApplication: () => compactApplication };
}

test('the default position clamp keeps the existing header-visible boundary', () => {
    const { context } = createContext({ viewportHeight: 300 });
    const position = context.I.clampPosition(100, 200);
    assert.equal(position.left, 100);
    assert.equal(position.top, 200);
});

test('full chat uses complete height only through the explicit visibility operation', () => {
    const { context, shell } = createContext({ mode: 'full', viewportHeight: 600 });

    assert.equal(context.I.ensureChatSurfaceVisible(), true);
    assert.equal(shell.style.left, '100px');
    assert.equal(shell.style.top, '200px');
    assert.equal(shell.style.transform, 'none');
});

test('dragged compact chat reapplies its mode-specific clamped target without persistence', () => {
    const { context, getCompactApplication } = createContext({
        mode: 'compact',
        currentCompactRect: { left: 100, top: 300, width: 430, height: 96 },
        compactTarget: { left: 100, top: 180, width: 430, height: 96 }
    });

    assert.equal(context.I.ensureChatSurfaceVisible(), true);
    const application = getCompactApplication();
    assert.equal(application.left, 100);
    assert.equal(application.top, 180);
    assert.equal(application.width, 430);
    assert.equal(application.height, 96);
    assert.equal(application.options.persist, false);
    assert.match(apiSource, /ensureChatSurfaceVisible: I\.ensureChatSurfaceVisible/);
});

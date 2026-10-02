const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { JSDOM } = createRequire(path.resolve(__dirname, '../../frontend/react-neko-chat/package.json'))('jsdom');
const labels = { tour: 'Tour', next: 'Next', skip: 'Skip', unavailable: 'Unavailable', nativeFallback: 'Click Next if blocked' };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

test('locking PNGTuber preserves guide controls while retaining normal hiding rules', () => {
    const { dom } = setup();
    const root = dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/pngtuber-core.js'), 'utf8'));
    const manager = Object.create(root.PNGTuberManager.prototype);
    manager.updateLockIconPosition = () => {};
    manager.updateFloatingButtonsPosition = () => {};
    manager._floatingButtonsContainer = root.document.createElement('div');
    manager._pngtuberFloatingControlsVisible = false;
    const toolbar = manager._floatingButtonsContainer;
    toolbar.dataset.inTutorial = 'true';
    manager.setLocked(true);
    assert.equal(toolbar.style.display, 'flex');
    delete toolbar.dataset.inTutorial;
    manager.setLocked(true);
    assert.equal(toolbar.style.display, 'none');
    manager.setLocked(false);
    assert.equal(toolbar.style.display, 'none', 'user-hidden controls remain hidden after unlocking');
    manager._pngtuberFloatingControlsVisible = true;
    manager.setLocked(false);
    assert.equal(toolbar.style.display, 'flex');
    dom.window.close();
});

function setup() {
    const dom = new JSDOM('<button id="target">Target</button><button id="outside">Outside</button>', { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
    dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/app/app-prompt-shared.js'), 'utf8'));
    for (const module of ['mask', 'highlight', 'target', 'advance', 'opened-window', 'runner']) {
        dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide', module + '.js'), 'utf8'));
    }
    const target = dom.window.document.querySelector('#target');
    target.getBoundingClientRect = () => ({ left: 100, top: 100, right: 140, bottom: 140, width: 40, height: 40 });
    return { dom, api: dom.window.NekoClickGuide, target, doc: dom.window.document };
}

test('opened inline panels guide the real close action before advancing', async () => {
    const { dom, api, target, doc } = setup();
    const close = doc.createElement('button'); close.id = 'close';
    close.getBoundingClientRect = target.getBoundingClientRect;
    target.onclick = () => doc.body.append(close);
    close.onclick = () => close.remove();
    const guide = api.createRunner({ labels, steps: [
        { title: 'Open', target: '#target', view: () => close.isConnected && {
            title: 'Close first', target: '#close', advanceOnClick: true, requireClick: true,
            ready: () => !close.isConnected,
        } }, { title: 'After close' },
    ] });
    await guide.start(); target.click(); await delay(35);
    assert.equal(guide.index, 0);
    assert.equal(doc.querySelector('h2').textContent, 'Close first');
    assert.equal(doc.querySelector('.click-guide-next').disabled, true);
    close.click(); await delay(80);
    assert.equal(guide.index, 1);
    await guide.stop(); dom.window.close();
});

test('preview returns to its parent panel without skipping the close lesson', async () => {
    const { dom, api, target, doc } = setup();
    let panel = 'preview';
    target.onclick = () => { panel = panel === 'preview' ? 'selection' : null; };
    const guide = api.createRunner({ labels, steps: [
        { title: 'Export', view: () => panel && {
            title: panel, target: '#target', advanceOnClick: true, requireClick: true,
            resume: panel === 'preview', ready: () => panel !== 'preview',
        } }, { title: 'Next tool' },
    ] });
    await guide.start(); target.click(); await delay(80);
    assert.equal(guide.index, 0);
    assert.equal(doc.querySelector('h2').textContent, 'selection');
    target.click(); await delay(80); assert.equal(guide.index, 1);
    await guide.stop(); dom.window.close();
});

test('browser child gets shared close highlight and returns only after closure', async () => {
    const { dom, api, target, doc } = setup();
    const popup = new JSDOM('<button data-neko-window-control="close">Close</button>', {
        url: 'http://localhost/settings', pretendToBeVisual: true,
    });
    const child = popup.window;
    const destroyChild = child.close.bind(child);
    child.closed = false;
    const close = child.document.querySelector('button');
    close.getBoundingClientRect = target.getBoundingClientRect;
    child.close = () => { child.closed = true; };
    close.onclick = child.close;
    const original = dom.window.open = () => child;
    target.onclick = () => dom.window.open('/settings', 'settings');
    const guide = api.createRunner({ labels, steps: [
        { title: 'Settings', target: '#target', windowGuide: {
            title: 'Return', body: 'Close this page', nextLabel: 'Close and continue',
            closeSelector: '[data-neko-window-control="close"]',
        } }, { title: 'After closing' },
    ] });
    await guide.start(); target.click(); await delay(125);
    assert.equal(guide.index, 0);
    assert.equal(doc.querySelector('h2').textContent, 'Return');
    assert.equal(child.document.querySelector('h2').textContent, 'Return');
    assert.equal(child.document.querySelector('.click-guide-highlight').style.left, '94px');
    const radius = child.document.querySelector('.click-guide-highlight').style.borderRadius;
    assert.equal(radius, child.document.querySelector('rect[fill="black"]').getAttribute('rx') + 'px');
    close.click(); await delay(140);
    assert.equal(guide.index, 1);
    assert.equal(dom.window.open, original);
    assert.equal(child.document.querySelector('.click-guide-layer'), null);
    await guide.stop(); dom.window.close(); destroyChild();
});

test('ending during child inspection removes guide UI without closing user page', async () => {
    const { dom, api, target } = setup();
    const childDom = new JSDOM('<p>Settings</p>', { url: 'http://localhost/settings' });
    const child = childDom.window;
    const destroyChild = child.close.bind(child);
    child.closed = false; let closes = 0; child.close = () => { closes++; child.closed = true; };
    const original = dom.window.open = () => child;
    target.onclick = () => dom.window.open('/settings');
    const guide = api.createRunner({ labels, steps: [{ target: '#target', windowGuide: {
        title: 'Return', body: 'Close first', nextLabel: 'Close', closeSelector: 'button',
    } }] });
    await guide.start(); target.click(); await delay(110);
    await guide.stop('skipped');
    assert.equal(closes, 0);
    assert.equal(child.document.querySelector('.click-guide-layer'), null);
    assert.equal(dom.window.open, original);
    dom.window.close(); destroyChild();
});

test('child close targets use their own viewport instead of the smaller opener', () => {
    const { dom, api } = setup();
    const popup = new JSDOM('<button id="close">Close</button>', { url: 'http://localhost/settings' });
    Object.defineProperties(dom.window, { innerWidth: { value: 400 }, innerHeight: { value: 300 } });
    Object.defineProperties(popup.window, { innerWidth: { value: 1000 }, innerHeight: { value: 800 } });
    const close = popup.window.document.querySelector('#close');
    close.getBoundingClientRect = () => ({ left: 940, top: 20, right: 980, bottom: 60, width: 40, height: 40 });
    try { assert.equal(api.resolveTarget('#close', popup.window.document), close); }
    finally { dom.window.close(); popup.window.close(); }
});

test('the real page close action exposes its confirmation without advancing prematurely', async () => {
    const { dom, api, target } = setup();
    const popup = new JSDOM('<button id="close">Close</button>', { url: 'http://localhost/settings' });
    const child = popup.window;
    const destroyChild = child.close.bind(child);
    child.closed = false;
    child.close = () => { child.closed = true; };
    const close = child.document.querySelector('#close');
    close.getBoundingClientRect = target.getBoundingClientRect;
    close.onclick = () => { child.document.body.insertAdjacentHTML('beforeend', '<div role="dialog">Unsaved changes</div>'); };
    dom.window.open = () => child;
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', windowGuide: { title: 'Return', body: 'Close first', nextLabel: 'Close',
            closePending: 'Finish the page confirmation', closeSelector: '#close' } }, { title: 'Next' },
    ] });
    try {
        await guide.start(); dom.window.open('/settings'); await delay(120);
        close.click(); await delay(120);
        assert.equal(guide.index, 0, 'a close request is not a completed close');
        assert.equal(child.document.querySelector('.click-guide-window-return p').textContent, 'Finish the page confirmation');
        assert.ok([...child.document.querySelectorAll('.click-guide-mask')].every(pane =>
            parseFloat(pane.style.width) === 0 || parseFloat(pane.style.height) === 0));
        child.close(); await delay(140);
        assert.equal(guide.index, 1);
    } finally { await guide.stop(); dom.window.close(); destroyChild(); }
});

for (const closeFrom of ['page', 'child card', 'parent card', 'skip']) {
test(`nested browser pages preserve their parent and clean up (${closeFrom})`, async () => {
    const { dom, api, target } = setup();
    const popups = ['/settings', '/details'].map(url => new JSDOM('<button id="close">Close</button>', {
        url: 'http://localhost' + url,
    }));
    const children = popups.map(popup => popup.window);
    const destroy = children.map(child => child.close.bind(child));
    for (const child of children) {
        child.closed = false; child.close = () => { child.closed = true; };
        const close = child.document.querySelector('#close');
        close.getBoundingClientRect = target.getBoundingClientRect;
        close.onclick = child.close;
    }
    const originalOpen = dom.window.open = () => children[0];
    const originalNestedOpen = children[0].open = () => children[1];
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', windowGuide: { title: 'Return', body: 'Close first', nextLabel: 'Close', closeSelector: '#close' } },
        { title: 'Next' },
    ] });
    try {
        await guide.start(); dom.window.open('/settings'); await delay(120);
        children[0].open('/details'); await delay(120);
        assert.ok(children[1].document.querySelector('.click-guide-window-return'));
        if (closeFrom === 'skip') {
            await guide.stop('skipped');
            assert.ok(children.every(child => !child.closed));
            assert.ok(children.every(child => !child.document.querySelector('.click-guide-layer')));
            assert.equal(dom.window.open, originalOpen);
            assert.equal(children[0].open, originalNestedOpen);
            return;
        }
        const closeDocument = closeFrom === 'parent card' ? dom.window.document : children[1].document;
        closeDocument.querySelector(closeFrom === 'page' ? '#close' : '.click-guide-next').click();
        await delay(140);
        assert.equal(guide.index, 0);
        assert.ok(children[0].document.querySelector('.click-guide-window-return'));
        children[0].document.querySelector('#close').click(); await delay(140);
        assert.equal(guide.index, 1);
        assert.equal(dom.window.open, originalOpen);
        assert.equal(children[0].open, originalNestedOpen);
    } finally { await guide.stop(); dom.window.close(); destroy.forEach(close => close()); }
});
}

test('page tutorials pause for inspection and resume the interrupted step after the guide leaves', () => {
    const { dom } = setup();
    const root = dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/page-tutorial-manager.js'), 'utf8'));
    const pageGuide = root.pageTutorialManager = new root.PageTutorialManager();
    pageGuide.currentPage = 'memory_browser';
    root.localStorage.setItem('neko_tutorial_memory_browser_manual_intent', 'true');
    root.__nekoClickGuideWindowInspection = true;
    pageGuide.checkAndStartTutorial();
    assert.equal(root.localStorage.getItem('neko_tutorial_memory_browser_manual_intent'), 'true');
    assert.equal(pageGuide.startTutorial(), false);
    pageGuide.isTutorialRunning = true;
    root.isInTutorial = true;
    pageGuide.driver = { currentStep: 2, destroy: () => pageGuide.handleTutorialEnd() };
    root.dispatchEvent(new root.CustomEvent('neko:click-guide-window-inspection'));
    assert.equal(pageGuide.isTutorialRunning, false);
    assert.equal(root.isInTutorial, false);
    assert.equal(root.localStorage.getItem('neko_tutorial_memory_browser'), null);
    assert.equal(root.localStorage.getItem('neko_tutorial_memory_browser_manual_intent'), 'true');
    assert.equal(pageGuide.shouldManageCurrentPage(), true);
    let resumedStep = -1;
    pageGuide.cachedValidSteps = [{}, {}, {}];
    pageGuide.startTutorial = () => {
        pageGuide.driver = { showStep: index => { resumedStep = index; } };
        pageGuide.isTutorialRunning = true;
        return true;
    };
    root.opener = { isNekoClickGuideActive: true };
    delete root.__nekoClickGuideWindowInspection;
    root.dispatchEvent(new root.CustomEvent('neko:click-guide-window-inspection'));
    assert.equal(resumedStep, -1, 'the opener is still guiding another window');
    root.opener.isNekoClickGuideActive = false;
    Object.defineProperty(root.document, 'visibilityState', { configurable: true, value: 'hidden' });
    root.document.dispatchEvent(new root.Event('visibilitychange'));
    assert.equal(resumedStep, -1, 'hidden pages must wait until visible');
    Object.defineProperty(root.document, 'visibilityState', { configurable: true, value: 'visible' });
    root.dispatchEvent(new root.Event('focus'));
    assert.equal(resumedStep, 2);
    dom.window.close();
});

test('inspection ending before Driver loads keeps manual page tutorial intent', () => {
    const { dom } = setup();
    const root = dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/page-tutorial-manager.js'), 'utf8'));
    const pageGuide = root.pageTutorialManager = new root.PageTutorialManager();
    pageGuide.currentPage = 'memory_browser';
    root.localStorage.setItem('neko_tutorial_memory_browser_manual_intent', 'true');
    root.dispatchEvent(new root.CustomEvent('neko:click-guide-window-inspection'));
    assert.equal(root.localStorage.getItem('neko_tutorial_memory_browser_manual_intent'), 'true');
    dom.window.close();
});

test('page tutorial starts after inspection ends without an interrupted step', () => {
    const { dom } = setup();
    const root = dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/page-tutorial-manager.js'), 'utf8'));
    const pageGuide = root.pageTutorialManager = new root.PageTutorialManager();
    pageGuide.currentPage = 'memory_browser';
    root.__nekoClickGuideWindowInspection = true;
    pageGuide.checkAndStartTutorial();
    assert.equal(pageGuide._clickGuideDeferredStart, true);
    let checks = 0;
    pageGuide.checkAndStartTutorial = () => { checks++; pageGuide._clickGuideDeferredStart = false; };
    root.dispatchEvent(new root.CustomEvent('neko:click-guide-window-inspection'));
    assert.equal(checks, 0);
    delete root.__nekoClickGuideWindowInspection;
    root.opener = { isNekoClickGuideActive: true };
    root.dispatchEvent(new root.CustomEvent('neko:click-guide-window-inspection'));
    assert.equal(checks, 0);
    root.opener.isNekoClickGuideActive = false;
    Object.defineProperty(root.document, 'visibilityState', { configurable: true, value: 'hidden' });
    root.document.dispatchEvent(new root.Event('visibilitychange'));
    assert.equal(checks, 0);
    Object.defineProperty(root.document, 'visibilityState', { configurable: true, value: 'visible' });
    root.dispatchEvent(new root.Event('focus'));
    assert.equal(checks, 1);
    root.localStorage.setItem('neko_tutorial_memory_browser_manual_intent', 'true');
    root.dispatchEvent(new root.Event('focus'));
    root.document.dispatchEvent(new root.Event('visibilitychange'));
    assert.equal(checks, 1, 'ordinary focus must not consume newly reset manual intent');
    assert.equal(root.localStorage.getItem('neko_tutorial_memory_browser_manual_intent'), 'true');
    dom.window.close();
});

test('deferred inspection startup survives busy and handoff barriers until it can check intent', () => {
    const { dom } = setup();
    const root = dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/page-tutorial-manager.js'), 'utf8'));
    const manager = root.pageTutorialManager = new root.PageTutorialManager();
    manager.currentPage = 'memory_browser';
    root.driver = {};
    manager.shouldManageCurrentPage = () => true;
    let handoff = true;
    manager.hasActiveYuiHandoff = () => handoff;
    manager.hasSeenTutorial = () => true;
    let intentChecks = 0;
    manager.consumeManualIntent = () => { intentChecks++; return false; };
    manager._clickGuideDeferredStart = true;
    root.isInTutorial = true;
    root.dispatchEvent(new root.Event('focus'));
    assert.equal(manager._clickGuideDeferredStart, true);
    root.isInTutorial = false;
    root.dispatchEvent(new root.Event('focus'));
    assert.equal(manager._clickGuideDeferredStart, true);
    assert.equal(intentChecks, 0);
    handoff = false;
    root.dispatchEvent(new root.Event('focus'));
    assert.equal(manager._clickGuideDeferredStart, false);
    assert.equal(intentChecks, 1);
    root.dispatchEvent(new root.Event('focus'));
    assert.equal(intentChecks, 1);
    dom.window.close();
});

test('manual page tutorial intent survives an inspection during delayed startup', async () => {
    const { dom } = setup();
    const root = dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/page-tutorial-manager.js'), 'utf8'));
    const pageGuide = root.pageTutorialManager = new root.PageTutorialManager();
    pageGuide.currentPage = 'memory_browser';
    root.i18nReady = true;
    root.__nekoClickGuideWindowInspection = true;
    pageGuide.startTutorialWhenI18nReady(0, 'manual');
    await delay(10);
    assert.equal(root.localStorage.getItem('neko_tutorial_memory_browser_manual_intent'), 'true');
    delete root.__nekoClickGuideWindowInspection;
    let checks = 0;
    pageGuide.checkAndStartTutorial = () => { checks++; };
    root.dispatchEvent(new root.CustomEvent('neko:click-guide-window-inspection'));
    assert.equal(checks, 1);
    dom.window.close();
});

test('only the actual target click advances, and only after the UI is ready', async () => {
    const { dom, api, target, doc } = setup();
    let ready = false;
    let cleanups = 0;
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', advanceOnClick: true, ready: () => ready, enter: () => () => cleanups++ },
        { title: 'Second' }, { title: 'Third' }
    ] });
    await guide.start();
    doc.querySelector('#outside').click();
    await delay(30);
    assert.equal(guide.index, 0);
    target.click();
    target.click();
    await delay(30);
    assert.equal(guide.index, 0);
    ready = true;
    await delay(70);
    assert.equal(guide.index, 1, 'double click must not skip a step');
    assert.equal(cleanups, 1);
    await guide.stop('skipped');
    assert.equal(doc.querySelector('.click-guide-layer'), null);
    dom.window.close();
});

test('skip aborts pending waits and removes listeners without later advancement', async () => {
    const { dom, api, target, doc } = setup();
    let ready = false;
    const endings = [];
    const guide = api.createRunner({ labels, onEnd: reason => endings.push(reason), steps: [
        { target: '#target', advanceOnClick: true, ready: () => ready }, { title: 'Next' }
    ] });
    await guide.start();
    target.click();
    await delay(20);
    await guide.stop('skipped');
    ready = true;
    target.click();
    await delay(80);
    assert.equal(guide.index, 0);
    assert.deepEqual(endings, ['skipped']);
    assert.equal(doc.querySelector('.click-guide-layer'), null);
    dom.window.close();
});

test('highlight tracks target movement and does not expose hidden ancestors', async () => {
    const { dom, api, target, doc } = setup();
    const guide = api.createRunner({ labels, steps: [{ target: '#target' }] });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-highlight').style.left, '94px');
    target.getBoundingClientRect = () => ({ left: 180, top: 150, right: 220, bottom: 190, width: 40, height: 40 });
    await delay(40);
    assert.equal(doc.querySelector('.click-guide-highlight').style.left, '174px');
    const wrapper = doc.createElement('div');
    target.replaceWith(wrapper);
    wrapper.append(target);
    wrapper.style.opacity = '0';
    assert.equal(api.resolveTarget('#target'), null);
    await guide.stop('skipped');
    dom.window.close();
});

test('the highlight uses seven-day rectangular and image circular frames with a precise circular aperture', async () => {
    const { dom, api, target, doc } = setup();
    Object.defineProperties(target, { offsetWidth: { value: 40 }, offsetHeight: { value: 40 } });
    target.style.borderTopLeftRadius = '50%';
    const guide = api.createRunner({ labels, steps: [{ target: '#target' }] });
    await guide.start();
    const frame = doc.querySelector('.click-guide-highlight');
    assert.ok(frame.classList.contains('yui-guide-spotlight-frame'));
    assert.ok(frame.querySelector('.yui-guide-spotlight-circle-skin'));
    assert.equal(frame.classList.contains('is-circle-image'), true);
    assert.equal(frame.classList.contains('has-cat-ears'), false);
    const visual = doc.querySelector('.click-guide-mask-visual');
    const circle = visual.querySelector('circle');
    const rectangle = visual.querySelector('rect[fill="black"]');
    assert.equal(circle.style.display, '');
    assert.equal(circle.getAttribute('r'), '26');
    assert.equal(rectangle.style.display, 'none');
    target.style.borderTopLeftRadius = '0px';
    await delay(30);
    assert.equal(frame.classList.contains('is-circle-image'), false);
    assert.ok(frame.querySelector('.yui-guide-spotlight-chrome'));
    assert.equal(circle.style.display, 'none');
    assert.equal(rectangle.style.display, '');
    assert.equal(frame.style.borderRadius, rectangle.getAttribute('rx') + 'px');
    await guide.stop('skipped');
    dom.window.close();
});

test('capsule overview fits its rounded border without a padded gap', async () => {
    const { dom, api, target, doc } = setup();
    target.style.borderTopLeftRadius = '999px';
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', shape: 'rect', padding: 0, radiusMode: 'target' }
    ] });
    await guide.start();
    const frame = doc.querySelector('.click-guide-highlight');
    assert.equal(frame.style.left, '100px');
    assert.equal(frame.style.top, '100px');
    assert.equal(frame.style.width, '40px');
    assert.equal(frame.style.borderRadius, '20px');
    assert.equal(doc.querySelector('.click-guide-mask-visual rect[fill="black"]').getAttribute('rx'), '20');
    await guide.stop('skipped');
    dom.window.close();
});

test('hover lesson advances after the control opens without requiring a click', async () => {
    const { dom, api, target, doc } = setup();
    let opened = false;
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', advanceOnHover: true, ready: () => opened }, { title: 'Next' }
    ] });
    await guide.start();
    target.click();
    await delay(25);
    assert.equal(guide.index, 0);
    target.dispatchEvent(new dom.window.Event('pointerover', { bubbles: true }));
    await delay(25);
    opened = true;
    await delay(65);
    assert.equal(guide.index, 1);
    await guide.stop('skipped');
    dom.window.close();
});

test('visual focus can follow a narrow mark without shrinking the real click target', async () => {
    const { dom, api, target, doc } = setup();
    const guide = api.createRunner({ labels, steps: [{ target: '#target', shape: 'rect', catEars: true,
        focusRect: element => {
            const button = element.getBoundingClientRect();
            return { left: button.left + 10, right: button.right - 10,
                top: button.top + 18, bottom: button.bottom - 18 };
        }, advanceOnClick: true }, { title: 'Clicked' }] });
    await guide.start();
    const frame = doc.querySelector('.click-guide-highlight');
    assert.equal(frame.style.width, '32px');
    assert.equal(frame.style.height, '16px');
    assert.equal(frame.style.borderRadius, '4px');
    assert.equal(doc.querySelector('.click-guide-mask-visual rect[fill="black"]').getAttribute('rx'), '4');
    assert.equal(frame.classList.contains('is-compact'), true);
    assert.equal(frame.classList.contains('has-cat-ears'), true);
    assert.equal(frame.classList.contains('is-circle-image'), false);
    assert.equal(doc.querySelectorAll('.click-guide-mask')[2].style.width, '104px');
    target.click();
    await delay(30);
    assert.equal(guide.index, 1);
    await guide.stop('skipped');
    dom.window.close();
});

test('history lessons focus the visible blue bar instead of its transparent button', () => {
    const { dom, api, target } = setup();
    const root = dom.window;
    root.t = key => key;
    const originalStyle = root.getComputedStyle.bind(root);
    let lineWidth = '44px';
    root.getComputedStyle = (element, pseudo) => pseudo === '::before'
        ? { width: lineWidth, height: '3px' } : originalStyle(element);
    target.className = 'compact-history-visibility-handle';
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const [, open, close] = api.chatSteps();
    for (const lesson of [open, close]) {
        assert.equal(lesson.catEars, true);
        assert.equal(lesson.shape, 'rect');
        const rect = lesson.focusRect(target);
        assert.equal(rect.right - rect.left, 44);
        assert.equal(rect.bottom - rect.top, 3);
    }
    lineWidth = '100%';
    const openRect = close.focusRect(target);
    assert.equal(openRect.right - openRect.left, 40);
    dom.window.close();
});

test('chat guide opens an initially unmounted host before waiting for its mount', async () => {
    const { dom, api } = setup();
    const root = dom.window;
    root.t = key => key;
    const overlay = root.document.createElement('div');
    overlay.id = 'react-chat-window-overlay';
    overlay.hidden = true;
    root.document.body.append(overlay);
    let mounted = false;
    const calls = [];
    root.reactChatWindowHost = {
        getState: () => ({ mounted, chatSurfaceMode: 'full', compactChatState: 'history', composerHidden: true }),
        openWindow: () => { calls.push('open'); root.setTimeout(() => { mounted = true; }, 0); },
        closeWindow: () => calls.push('close'),
        setChatSurfaceMode: mode => calls.push('surface:' + mode),
        setCompactChatState: mode => calls.push('chat:' + mode),
        setAvatarToolMenuOpen: () => {}, deactivateAvatarTool: () => {},
        setCompactToolFanOpen: () => {}, setCompactHistoryOpen: () => {},
    };
    api.waitUntil = async predicate => {
        if (predicate()) return;
        await delay(10);
        if (!predicate()) throw new Error('target_not_ready');
    };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    try {
        const restore = await api.prepareChat();
        assert.equal(mounted, true);
        assert.deepEqual(calls.slice(0, 3), ['open', 'surface:compact', 'chat:input']);
        await restore();
        assert.deepEqual(calls.slice(-3), ['surface:full', 'chat:history', 'close']);
    } finally { dom.window.close(); }
});

for (const nativeReady of [true, false]) {
test('chat guide restores a collapsed native window after preparation failure: ready=' + nativeReady, async () => {
    const { dom, api } = setup();
    const root = dom.window;
    root.t = key => key;
    let restores = 0;
    root.nekoChatWindow = {
        prepareExpandedForTutorial: async () => ({ ready: nativeReady, wasCollapsed: true }),
        restoreCollapsedAfterTutorial: async () => { restores++; },
    };
    root.reactChatWindowHost = {
        getState: () => ({ mounted: false }),
        openWindow: async () => {},
    };
    api.waitUntil = async predicate => {
        if (!predicate()) throw new Error('target_not_ready');
    };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    try {
        await assert.rejects(api.prepareChat(), nativeReady ? /target_not_ready/ : /native_chat_not_ready/);
        assert.equal(restores, 1);
    } finally { dom.window.close(); }
});
}

test('typing a greeting and pressing Enter submits once before opening history', async () => {
    const { dom, api, doc } = setup();
    const input = doc.createElement('textarea');
    input.className = 'composer-input';
    input.getBoundingClientRect = () => ({ left: 100, top: 100, right: 240, bottom: 140, width: 140, height: 40 });
    doc.body.append(input);
    let sent = '';
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.isComposing && input.value.trim()) {
            sent = input.value.trim();
            input.value = '';
        }
    });
    const guide = api.createRunner({ labels, steps: [
        { title: 'Capsule', target: '.composer-input', requireInput: true,
            advanceOnKey: 'Enter', keyTarget: '.composer-input',
            keyReady: element => element.value.trim() === '你好',
            ready: () => !input.value.trim() },
        { title: 'History' },
    ] });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-next').disabled, true);
    input.value = '';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await delay(30);
    assert.equal(guide.index, 0);
    assert.equal(sent, '', 'empty Enter cannot advance');
    input.value = '你好';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
    await delay(30);
    assert.equal(guide.index, 0, 'IME confirmation is not mistaken for sending');
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await delay(90);
    assert.equal(sent, '你好');
    assert.equal(guide.index, 1);
    await guide.stop(); dom.window.close();
});

test('the chat tour stays in one area and preserves a draft or attachment', () => {
    const { dom, api, doc } = setup();
    dom.window.t = key => key;
    let mode = 'compact';
    dom.window.reactChatWindowHost = { getChatSurfaceMode: () => mode, setCompactToolFanOpen() {} };
    const frame = doc.createElement('div');
    frame.className = 'compact-chat-surface-frame';
    frame.innerHTML = '<textarea class="composer-input"></textarea>';
    doc.body.append(frame);
    dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const steps = api.chatSteps();
    assert.deepEqual(Array.from(steps, step => step.id), [
        'chatOverview', 'history', 'historyClose', 'tools', 'screenshot', 'avatar', 'translate',
        'jukebox', 'import', 'export', 'galgame', 'minimize', 'restore'
    ]);
    assert.equal(steps[0].target, '.compact-chat-surface-frame');
    assert.equal(steps[0].shape, 'rect');
    assert.equal(steps[0].padding, 0);
    assert.equal(steps[0].radiusMode, 'target');
    assert.equal(steps[0].advanceOnKey, 'Enter');
    assert.equal(steps[0].requireInput, true);
    assert.equal(steps.find(step => step.id === 'tools').advanceOnHover, true);
    assert.equal(steps.find(step => step.id === 'tools').advanceOnClick, undefined);
    let reopened = '';
    dom.window.reactChatWindowHost.setCompactChatState = value => { reopened = value; };
    steps.find(step => step.id === 'tools').enter();
    assert.equal(reopened, 'input', 'the real send collapses the composer, so tools reopen it');
    const toolsStep = steps.find(step => step.id === 'tools');
    const avatarStep = steps.find(step => step.id === 'avatar');
    toolsStep.onAdvance({ by: 'next' });
    assert.equal(avatarStep.when(), false);
    toolsStep.onAdvance({ by: 'target' });
    assert.equal(avatarStep.when(), true, 'returning and hovering restores the tool lessons');
    assert.equal(steps.find(step => step.id === 'restore').when(), false);
    mode = 'minimized';
    assert.equal(steps.find(step => step.id === 'restore').when(), true);
    const attachment = doc.createElement('button');
    attachment.className = 'compact-input-tool-toggle';
    attachment.type = 'submit';
    doc.body.append(attachment);
    assert.equal(steps.find(step => step.id === 'avatar').when(), false);
    assert.equal(steps.find(step => step.id === 'tools').body(), 'clickGuide.toolsDraft.body');
    assert.equal(attachment.type, 'submit');
    const draftSteps = api.chatSteps();
    assert.equal(draftSteps[0].requireInput, false, 'an existing draft is never sent by the guide');
    assert.equal(draftSteps.find(step => step.id === 'tools').body(), 'clickGuide.toolsDraft.body');
    attachment.remove();
    dom.window.reactChatWindowHost.getState = () => ({ composerAttachments: [{ id: 'photo' }] });
    assert.equal(api.chatSteps()[0].requireInput, false, 'an existing attachment is never sent as a greeting');
    frame.remove();
    dom.window.reactChatWindowHost.getState = () => ({ composerAttachments: [] });
    assert.equal(api.chatSteps()[0].requireInput, false, 'a missing composer cannot trap the first lesson');
    dom.window.close();
});

test('history reveal covers the panel, then the close lesson focuses the bar', () => {
    const { dom, api, doc } = setup();
    dom.window.t = key => key;
    dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const handle = doc.createElement('button');
    handle.className = 'compact-history-visibility-handle';
    handle.getBoundingClientRect = () => ({ left: 200, right: 320, top: 500, bottom: 526, width: 120, height: 26 });
    const anchor = doc.createElement('section');
    anchor.className = 'compact-export-history-anchor';
    anchor.dataset.compactExportHistoryOpen = 'true';
    const panel = doc.createElement('div');
    panel.className = 'compact-export-history-panel';
    panel.getBoundingClientRect = () => ({ left: 80, right: 440, top: 120, bottom: 504, width: 360, height: 384 });
    anchor.append(panel); doc.body.append(handle, anchor);
    const [ , open, close] = api.chatSteps();
    const bar = open.focusRect(handle);
    handle.setAttribute('aria-expanded', 'true');
    const rect = open.focusRect(handle);
    const closeRect = close.focusRect(handle);
    assert.equal(close.target, '.compact-history-visibility-handle');
    assert.deepEqual({ ...rect }, { left: 80, top: 120, right: 440, bottom: bar.bottom });
    assert.deepEqual({ ...closeRect }, { ...bar });
    assert.equal(open.animateFocus(), true);
    assert.equal(close.animateFocus, true);
    dom.window.close();
});

test('history close step puts the ghost cursor on the blue bar after the panel reveal', async () => {
    const { dom, api, doc } = setup();
    const root = dom.window;
    root.t = key => key;
    const originalStyle = root.getComputedStyle.bind(root);
    root.getComputedStyle = (element, pseudo) => pseudo === '::before'
        ? { width: '44px', height: '3px' } : originalStyle(element);
    const handle = doc.createElement('button');
    handle.className = 'compact-history-visibility-handle';
    handle.setAttribute('aria-expanded', 'false');
    handle.getBoundingClientRect = () => ({ left: 100, right: 200, top: 500, bottom: 526, width: 100, height: 26 });
    handle.onclick = () => handle.setAttribute('aria-expanded', handle.getAttribute('aria-expanded') === 'true' ? 'false' : 'true');
    const anchor = doc.createElement('section');
    anchor.className = 'compact-export-history-anchor';
    anchor.dataset.compactExportHistoryOpen = 'true';
    const panel = doc.createElement('div');
    panel.className = 'compact-export-history-panel';
    panel.getBoundingClientRect = () => ({ left: 50, right: 250, top: 200, bottom: 510, width: 200, height: 310 });
    anchor.append(panel); doc.body.append(handle, anchor);
    root.reactChatWindowHost = { setCompactHistoryOpen() {} };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const [, open, close] = api.chatSteps();
    const nativeFrames = [];
    const presentation = { bind() {}, update(frame) { nativeFrames.push(frame); }, async close() {} };
    const guide = api.createRunner({ labels, steps: [open, close], presentation });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-highlight').classList.contains('is-click-step'), true,
        'the collapsed history bar initially has a click cursor');
    assert.equal(nativeFrames.at(-1).clickable, true);
    handle.click();
    await delay(90);
    assert.equal(guide.index, 0, 'the opened history remains visible for its reveal');
    const expandedRing = doc.querySelector('.click-guide-highlight');
    assert.equal(expandedRing.style.width, '212px');
    assert.equal(expandedRing.classList.contains('is-click-step'), false,
        'the expanded history panel has no click cursor during the reveal');
    assert.equal(nativeFrames.at(-1).clickable, false);
    await delay(600);
    assert.equal(guide.index, 1);
    const ring = doc.querySelector('.click-guide-highlight');
    assert.equal(ring.style.width, '56px');
    assert.equal(ring.classList.contains('is-click-step'), true);
    assert.equal(nativeFrames.at(-1).clickable, true);
    assert.equal(ring.classList.contains('has-cat-ears'), true);
    assert.equal(ring.classList.contains('cursor-on-target'), true);
    assert.equal(doc.querySelector('.click-guide-ghost-cursor').parentElement, ring);
    handle.click();
    await delay(75);
    assert.equal(guide.index, 2);
    dom.window.close();
});

test('hover lesson advances when the real wheel opens without a click event', async () => {
    const { dom, api, doc } = setup();
    let open = false;
    const guide = api.createRunner({ labels, steps: [
        { title: 'Wheel', target: '#target', advanceOnHover: true, ready: () => open },
        { title: 'First tool', target: '#target' },
    ] });
    await guide.start();
    await delay(75);
    assert.equal(guide.index, 0);
    open = true;
    await delay(100);
    assert.equal(guide.index, 1);
    assert.equal(doc.querySelector('h2').textContent, 'First tool');
    await guide.stop(); dom.window.close();
});

test('floating tour covers main buttons once and recalls only after actual goodbye click', () => {
    const { dom, api, doc } = setup();
    dom.window.t = key => key;
    dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const steps = api.floatingSteps();
    assert.deepEqual(Array.from(steps, step => step.id), [
        'floatingOverview', 'mic', 'agent', 'social', 'settings', 'goodbye', 'return', 'lock', 'finish'
    ]);
    assert.equal(steps[0].shape, 'rect');
    assert.equal(steps[0].advanceOnClick, true);
    assert.equal(steps[0].consumeTargetClick, true);
    const goodbye = steps.find(step => step.id === 'goodbye');
    const recall = doc.createElement('button');
    recall.className = 'neko-idle-return-btn';
    recall.getBoundingClientRect = () => ({ left: 100, top: 100, right: 140, bottom: 140, width: 40, height: 40 });
    doc.body.append(recall);
    goodbye.onAdvance({ by: 'next' });
    assert.equal(steps.find(step => step.id === 'return').when(), false);
    goodbye.onAdvance({ by: 'target' });
    assert.equal(steps.find(step => step.id === 'return').when(), true);
    dom.window.close();
});

test('floating overview bounds include the first and last actual buttons', () => {
    const { dom, api, doc } = setup();
    dom.window.t = key => key;
    dom.window.universalTutorialManager = { constructor: { detectModelPrefix: () => 'live2d' } };
    const group = doc.createElement('div');
    group.id = 'live2d-floating-buttons';
    group.getBoundingClientRect = () => ({ left: 100, top: 120, right: 160, bottom: 270, width: 60, height: 150 });
    for (const [id, top] of [['mic', 110], ['agent', 150], ['social', 180], ['settings', 210], ['goodbye', 250]]) {
        const button = doc.createElement('button');
        button.id = `live2d-btn-${id}`;
        button.getBoundingClientRect = () => ({ left: 112, top, right: 142, bottom: top + 30, width: 30, height: 30 });
        group.append(button);
    }
    doc.body.append(group);
    dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const overview = api.floatingSteps()[0];
    assert.equal(overview.target(), group);
    assert.deepEqual({ ...overview.focusRect(group) }, { left: 112, top: 110, right: 142, bottom: 280 });
    dom.window.close();
});

test('informational buttons do not advance on click or show the ghost cursor', async () => {
    const { dom, api, doc, target } = setup();
    const guide = api.createRunner({ labels, steps: [
        { title: 'Info', target: '#target' },
        { title: 'Click', target: '#target', advanceOnClick: true }
    ] });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-highlight').classList.contains('is-click-step'), false);
    target.click();
    await delay(25);
    assert.equal(guide.index, 0);
    doc.querySelector('.click-guide-next').click();
    await delay(30);
    assert.equal(doc.querySelector('.click-guide-highlight').classList.contains('is-click-step'), true);
    await guide.stop('skipped');
    dom.window.close();
});

test('conditional lessons can be skipped without touching the input draft', async () => {
    const { dom, api, doc } = setup();
    const input = doc.createElement('textarea');
    input.value = 'Keep my draft';
    doc.body.append(input);
    let entered = false;
    const guide = api.createRunner({ labels, steps: [
        { when: () => false, enter: () => { entered = true; } }, { title: 'Next' }
    ] });
    await guide.start();
    assert.equal(guide.index, 1);
    assert.equal(entered, false);
    assert.equal(input.value, 'Keep my draft');
    await guide.stop('skipped');
    dom.window.close();
});

test('chapter progress counts visited lessons without evaluating future conditions early', async () => {
    const { dom, api, doc } = setup();
    let checks = 0;
    const guide = api.createRunner({ labels: { ...labels, section: 'Chat capsule' }, steps: [
        { title: 'Overview' }, { when: () => { checks++; return false; } }, { title: 'Input' },
    ] });
    await guide.start();
    assert.equal(checks, 0);
    assert.equal(doc.querySelector('.click-guide-progress').textContent, 'Chat capsule · 1 / 3');
    doc.querySelector('.click-guide-next').click();
    await delay(35);
    assert.equal(checks, 1);
    assert.equal(guide.index, 2);
    assert.equal(doc.querySelector('.click-guide-progress').textContent, 'Chat capsule · 2 / 2');
    await guide.stop(); dom.window.close();
});

test('each highlighted chat tool advances without executing its action', async () => {
    const { dom, api, doc } = setup();
    dom.window.t = key => key;
    dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const ids = ['screenshot', 'avatar', 'translate', 'jukebox', 'import', 'export', 'galgame'];
    const actions = Object.fromEntries(ids.map(id => [id, 0]));
    dom.window.reactChatWindowHost = {
        setAvatarToolMenuOpen() {}, deactivateAvatarTool() {}, setCompactToolFanOpen() {},
        setCompactToolWheelIndex() {}, getState: () => ({ composerAttachments: [] })
    };
    const fan = doc.createElement('div');
    fan.className = 'compact-input-tool-fan';
    doc.body.append(fan);
    for (const id of ids) {
        const item = doc.createElement('div');
        item.className = `compact-input-tool-item-${id}`;
        const button = doc.createElement('button');
        button.textContent = id;
        button.onclick = () => actions[id]++;
        item.append(button); fan.append(item);
        item.getBoundingClientRect = button.getBoundingClientRect = () => ({
            left: 100, top: 100, right: 140, bottom: 140, width: 40, height: 40
        });
    }
    const steps = api.chatSteps().filter(step => ids.includes(step.id));
    assert.ok(steps.every(step => step.advanceOnClick && step.consumeTargetClick));
    assert.ok(steps.every(step => !step.view && !step.windowGuide));
    const guide = api.createRunner({ labels, steps });
    try {
        await guide.start();
        for (const [index, id] of ids.entries()) {
            const button = doc.querySelector(`.compact-input-tool-item-${id} button`);
            assert.equal(doc.querySelector('.click-guide-highlight').classList.contains('is-click-step'), true);
            assert.match(doc.querySelector('.click-guide-card p:not(.click-guide-status)').textContent, /toolContinueHint/);
            button.click();
            await delay(35);
            assert.equal(actions[id], 0, `${id} must not run during the guide`);
            assert.equal(guide.index, index + 1);
        }
        doc.querySelector('.compact-input-tool-item-screenshot button').click();
        assert.equal(actions.screenshot, 1, 'the original action works after the guide ends');
    } finally { await guide.stop(); dom.window.close(); }
});

test('pointerup restoration may change the target selector before click', async () => {
    const { dom, api, target } = setup();
    target.className = 'minimized';
    const guide = api.createRunner({ labels, steps: [
        { target: '.minimized', advanceOnClick: true, ready: () => target.className === '' },
        { title: 'Restored' }
    ] });
    await guide.start();
    target.dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true }));
    target.className = '';
    target.click();
    await delay(30);
    assert.equal(guide.index, 1);
    await guide.stop('skipped');
    dom.window.close();
});

test('a denied or unavailable action can be acknowledged after readiness times out', async () => {
    const { dom, api, target, doc } = setup();
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', advanceOnClick: true, requireClick: true, ready: () => false, readyTimeout: 20 },
        { title: 'Continued' }
    ] });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-next').disabled, true);
    target.click();
    await delay(100);
    assert.equal(doc.querySelector('.click-guide-next').disabled, false);
    doc.querySelector('.click-guide-next').click();
    await delay(25);
    assert.equal(guide.index, 1);
    await guide.stop('skipped');
    dom.window.close();
});

test('native presentation receives geometry and actions, and releases on exit', async () => {
    const { dom, api, doc } = setup();
    let actions, frame, closed = 0;
    const guide = api.createRunner({ labels, steps: [{ target: '#target' }, { title: 'Second' }], presentation: {
        bind(value) { actions = value; }, update(value) { frame = value; }, close() { closed++; }
    } });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-layer').style.visibility, 'hidden');
    assert.ok(doc.querySelector('.click-guide-layer').classList.contains('click-guide-native'));
    assert.equal(frame.rect.left, 94);
    actions.next();
    await delay(25);
    assert.equal(guide.index, 1);
    actions.skip();
    await delay(25);
    assert.equal(closed, 1);
    dom.window.close();
});

test('native card actions re-enable when a step settles without another animation frame', async () => {
    const { dom, api, target } = setup();
    dom.window.requestAnimationFrame = () => 1;
    dom.window.cancelAnimationFrame = () => {};
    const frames = [];
    const guide = api.createRunner({ labels, presentation: {
        bind() {}, update(value) { frames.push(value); }, close() {}
    }, steps: [
        { title: 'First', target: '#target', advanceOnClick: true },
        { title: 'Second', target: '#target' }
    ] });
    await guide.start();
    target.click();
    await delay(20);
    assert.equal(guide.index, 1);
    assert.equal(frames.at(-1).nextDisabled, false);
    assert.equal(frames.at(-1).backDisabled, false);
    await guide.stop('skipped');
    dom.window.close();
});

test('native target click waits for restored state before continuing', async () => {
    const { dom, api } = setup();
    let actions;
    let restored = false;
    const guide = api.createRunner({
        labels, presentation: { bind(value) { actions = value; }, update() {}, close() {} },
        steps: [
            { id: 'restore', target: '#missing', nativeTarget: 'minimizedBall',
                requireClick: true, advanceOnClick: true, ready: () => restored },
            { title: 'Restored' }
        ]
    });
    await guide.start();
    assert.equal(dom.window.document.querySelector('.click-guide-next').disabled, true);
    actions.target();
    await delay(30);
    assert.equal(guide.index, 0);
    restored = true;
    await delay(60);
    assert.equal(guide.index, 1);
    await guide.stop('skipped');
    dom.window.close();
});

test('a disappearing goodbye button does not center its card before the return button appears', async () => {
    const { dom, api, target, doc } = setup();
    let frame;
    const returning = doc.createElement('button');
    returning.id = 'return';
    returning.getBoundingClientRect = target.getBoundingClientRect;
    target.onclick = () => {
        target.remove();
        dom.window.setTimeout(() => doc.body.append(returning), 100);
    };
    const guide = api.createRunner({ labels, presentation: {
        bind() {}, update(value) { frame = value; }, close() {}
    }, steps: [
        { title: 'Goodbye', target: '#target', advanceOnClick: true,
            ready: () => returning.isConnected },
        { title: 'Return', target: '#return' }
    ] });
    await guide.start();
    target.click();
    await delay(45);
    assert.equal(frame.targetExpected, true);
    assert.equal(frame.rect, null);
    assert.equal(doc.querySelector('.click-guide-card').style.display, 'none');
    await delay(110);
    assert.equal(guide.index, 1);
    assert.equal(doc.querySelector('.click-guide-card').style.display, '');
    await guide.stop('skipped');
    dom.window.close();
});

test('native restore offers a visible continuation if the ball cannot be clicked', async () => {
    const { dom, api, doc } = setup();
    const original = dom.window.setTimeout.bind(dom.window);
    dom.window.setTimeout = (callback, ms, ...args) => original(callback, ms === 6000 ? 30 : ms, ...args);
    const guide = api.createRunner({ labels, presentation: { bind() {}, update() {}, close() {} }, steps: [
        { id: 'restore', target: '#missing', nativeTarget: 'minimizedBall',
            requireClick: true, advanceOnClick: true }, { title: 'Next' }
    ] });
    await guide.start();
    assert.equal(doc.querySelector('.click-guide-card').style.display, 'none');
    assert.equal(doc.querySelector('.click-guide-next').disabled, true);
    await delay(70);
    assert.equal(doc.querySelector('.click-guide-card').style.display, '', 'fallback card appears when the ball is unavailable');
    assert.equal(doc.querySelector('.click-guide-next').disabled, false);
    assert.equal(doc.querySelector('.click-guide-status').textContent, labels.nativeFallback);
    doc.querySelector('.click-guide-next').click();
    await delay(30);
    assert.equal(guide.index, 1);
    await guide.stop('skipped');
    dom.window.close();
});

test('native restore keeps the real ball click required while the target is present', async () => {
    const { dom, api, doc } = setup();
    const original = dom.window.setTimeout.bind(dom.window);
    dom.window.setTimeout = (callback, ms, ...args) => original(callback, ms === 6000 ? 30 : ms, ...args);
    const available = true;
    const guide = api.createRunner({ labels, presentation: {
        bind() {}, update() {}, close() {},
        nativeTargetAvailable: () => available,
    }, steps: [{ id: 'restore', nativeTarget: 'minimizedBall', requireClick: true,
        advanceOnClick: true }, { title: 'Next' }] });
    await guide.start();
    await delay(70);
    assert.equal(doc.querySelector('.click-guide-next').disabled, true);
    assert.equal(doc.querySelector('.click-guide-status').textContent, '');
    await guide.stop('skipped'); dom.window.close();
});

test('a late native ball removes the unavailable fallback', async () => {
    const { dom, api, doc } = setup();
    const original = dom.window.setTimeout.bind(dom.window);
    dom.window.setTimeout = (callback, ms, ...args) => original(callback, ms === 6000 ? 30 : ms, ...args);
    let actions;
    const guide = api.createRunner({ labels, presentation: {
        bind(value) { actions = value; }, update() {}, close() {},
        nativeTargetAvailable: () => false,
    }, steps: [{ id: 'restore', nativeTarget: 'minimizedBall', requireClick: true,
        advanceOnClick: true }, { title: 'Next' }] });
    await guide.start();
    await delay(70);
    assert.equal(doc.querySelector('.click-guide-next').disabled, false);
    actions.nativeTargetAvailability(true);
    assert.equal(doc.querySelector('.click-guide-next').disabled, true);
    assert.equal(doc.querySelector('.click-guide-status').textContent, '');
    await guide.stop('skipped'); dom.window.close();
});

test('browser-only restore explains when the desktop ball is absent', async () => {
    const { dom, api, doc } = setup();
    const original = dom.window.setTimeout.bind(dom.window);
    dom.window.setTimeout = (callback, ms, ...args) => original(callback, ms === 2000 ? 20 : ms, ...args);
    const guide = api.createRunner({ labels, steps: [
        { target: '#missing', nativeTarget: 'minimizedBall', requireClick: true, advanceOnClick: true }
    ] });
    await guide.start();
    await delay(50);
    assert.equal(doc.querySelector('.click-guide-next').disabled, false);
    assert.equal(doc.querySelector('.click-guide-status').textContent, labels.unavailable);
    await guide.stop('skipped');
    dom.window.close();
});

function startup(state, old = {}) {
    const context = setup();
    const root = context.dom.window;
    const calls = [];
    root.t = key => key;
    root.NekoSevenDayTutorialState = { loadState: () => old };
    root.NekoClickGuideState = {
        ready: async () => state, refresh: async () => state, get: () => state,
        update: async (action, values) => {
            calls.push(action);
            Object.assign(state, values);
            state.pending = action === 'choose' && values.choice === 'click';
            return state;
        }
    };
    const manager = root.universalTutorialManager = {
        currentPage: 'home', isI18nReady: () => true,
        setHomeTutorialPending: value => { root.isNekoHomeTutorialPending = value; },
        clearStartupGreetingRelease: () => { root.isNekoHomeTutorialPending = false; },
        dispatchStartupGreetingRelease: () => { calls.push('released'); }
    };
    Object.assign(context.api, {
        createNativePresentation: () => null, chatSteps: () => [], floatingSteps: () => [],
        prepareChat: async () => () => calls.push('chat-restored'),
        prepareFloating: async () => () => calls.push('floating-restored')
    });
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home.js'), 'utf8'));
    return { ...context, manager, calls };
}

test('authoritative seven-day reset wins before startup and refreshed replay', async () => {
    for (const direct of [false, true]) {
        const ctx = startup({ choice: 'click', pending: true, revision: 1 });
        let authoritative = false;
        ctx.dom.window.NekoSevenDayTutorialState.ready = async () => { authoritative = true; };
        ctx.dom.window.NekoClickGuideState.isSevenDayOverride = () => authoritative;
        ctx.api.prepareChat = async () => { assert.fail('new seven-day reset owns startup'); };
        assert.equal(await (direct ? ctx.api.startHome({ startup: true }) : ctx.api.handleStartup(ctx.manager)), false);
        assert.equal(authoritative, true);
        assert.ok(!ctx.calls.includes('finish'));
        ctx.dom.window.close();
    }
});

test('remote preparation heartbeats extend the lease but retain a total deadline', async () => {
    const ctx = startup({ choice: 'click', pending: true, revision: 1 });
    const root = ctx.dom.window;
    let now = 0;
    let nextId = 0;
    const timers = new Map();
    root.Date.now = () => now;
    root.setTimeout = (callback, ms) => { const id = ++nextId; timers.set(id, { callback, ms }); return id; };
    root.clearTimeout = id => timers.delete(id);
    root.setInterval = () => ++nextId;
    root.clearInterval = () => {};
    const emit = (type, runId, reason) => root.dispatchEvent(new root.CustomEvent('neko:tutorial-overlay-relay', {
        detail: { action: 'click_guide', type, runId, reason }
    }));
    root.nekoTutorialOverlay = { relayToChat(message) {
        if (message.type !== 'start') return;
        now = 14000;
        emit('heartbeat', message.runId);
        assert.deepEqual([...timers.values()].map(timer => timer.ms), [6000]);
        now = 29000;
        emit('heartbeat', message.runId);
        assert.deepEqual([...timers.values()].map(timer => timer.ms), [1000]);
        emit('done', message.runId, 'skipped');
    } };
    try {
        assert.equal(await ctx.api.startHome(), true);
        assert.equal(timers.size, 0);
    } finally { root.close(); }
});

test('Escape and IME Escape preserve the guide and reach menu handlers', async () => {
    const { dom, api, doc } = setup();
    let reason;
    let escapes = 0;
    doc.addEventListener('keydown', event => { if (event.key === 'Escape') escapes++; });
    const runner = api.createRunner({ labels, steps: [{ title: 'Menu' }], onEnd: value => { reason = value; } });
    await runner.start();
    for (const isComposing of [false, true]) {
        doc.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', isComposing, bubbles: true }));
    }
    await delay(20);
    assert.equal(reason, undefined);
    assert.equal(escapes, 2);
    assert.ok(doc.querySelector('.click-guide-layer'));
    await runner.stop(); dom.window.close();
});

test('startup state and i18n failures clear pending without releasing greetings before fallback', async () => {
    for (const failure of ['i18n', 'state', 'refresh']) {
        const ctx = startup({ choice: 'click', pending: true }, { completedRounds: [1] });
        ctx.manager.dispatchStartupGreetingRelease = () => {
            ctx.dom.window.isNekoHomeTutorialPending = false;
            ctx.calls.push('released');
        };
        ctx.dom.window.isNekoHomeTutorialPending = true;
        const fail = async () => { throw new Error('offline'); };
        if (failure === 'i18n') ctx.api.waitUntil = fail;
        if (failure === 'state') ctx.dom.window.NekoClickGuideState.ready = async () => null;
        if (failure === 'choose') ctx.dom.window.NekoClickGuideState.update = fail;
        if (failure === 'refresh') {
            ctx.dom.window.NekoClickGuideState.get().pending = true;
            ctx.dom.window.NekoClickGuideState.refresh = fail;
        }
        assert.equal(await ctx.api.handleStartup(ctx.manager), false, failure);
        assert.equal(ctx.dom.window.isNekoHomeTutorialPending, failure === 'state', failure);
        assert.ok(!ctx.calls.includes('released'), 'seven-day fallback owns greeting release');
        assert.equal(ctx.dom.window.isNekoClickGuideActive === true, false);
        ctx.dom.window.close();
    }
});

test('startup rechecks replay intent after waiting for i18n', async () => {
    for (const latest of [{ choice: 'seven-day', pending: false }, { choice: 'click', pending: false }]) {
        const ctx = startup({ choice: 'click', pending: true, revision: 1 });
        let languageReady = false;
        ctx.api.waitUntil = async () => { languageReady = true; };
        ctx.dom.window.NekoClickGuideState.refresh = async () => {
            assert.equal(languageReady, true);
            return latest;
        };
        ctx.api.prepareChat = async () => { assert.fail('superseded replay must not prepare chat'); };
        assert.equal(await ctx.api.handleStartup(ctx.manager), false);
        assert.equal(ctx.dom.window.isNekoClickGuideActive, false);
        assert.equal(ctx.dom.window.isNekoHomeTutorialPending, false);
        assert.ok(!ctx.calls.includes('finish'));
        assert.ok(!ctx.calls.includes('released'), 'seven-day startup owns greeting release');
        ctx.dom.window.close();
    }
});

test('failed chat preparation falls back for this session and retries the same choice next boot', async () => {
    const state = { choice: 'click', pending: true, revision: 1 };
    const ctx = startup(state);
    let notice;
    ctx.dom.window.showStatusToast = message => { notice = message; };
    ctx.api.prepareChat = async () => { throw new Error('host_timeout'); };
    assert.equal(await ctx.api.handleStartup(ctx.manager), false);
    assert.equal(notice, 'clickGuide.connection.body');
    assert.equal(state.pending, true);
    assert.equal(state.choice, 'click');
    assert.ok(!ctx.calls.includes('finish'), 'failed guide is never completed');
    assert.ok(!ctx.calls.includes('choose'), 'a transient failure never overwrites the choice');
    assert.ok(!ctx.calls.includes('released'), 'failure cannot release greetings before seven-day startup');
    ctx.api.prepareChat = async () => () => ctx.calls.push('chat-restored');
    assert.equal(await ctx.api.handleStartup(ctx.manager), true);
    assert.equal(state.choice, 'click');
    assert.ok(ctx.calls.includes('finish'));
    assert.ok(ctx.calls.includes('released'), 'successful completion still releases greetings');
    ctx.dom.window.close();
});

test('memory reactivation save failure keeps retry and cancel available', async () => {
    const ctx = startup({ choice: null, pending: false });
    ctx.dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/reactivation.js'), 'utf8'));
    const pending = ctx.dom.window.NekoTutorialReactivation.open(async () => { throw new Error('save_failed'); });
    ctx.doc.querySelector('.click-guide-choice button').click();
    await delay(20);
    const buttons = ctx.doc.querySelectorAll('.click-guide-choice button');
    assert.equal(buttons.length, 3);
    assert.equal(buttons[0].disabled, false);
    assert.equal(ctx.doc.querySelector('.click-guide-choice p').textContent, 'clickGuide.saveFailed');
    assert.equal(ctx.doc.querySelector('.click-guide-choice p').getAttribute('role'), 'alert');
    assert.equal(ctx.doc.activeElement, buttons[0]);
    buttons[2].click();
    assert.equal(await pending, null);
    assert.equal(ctx.doc.querySelector('.click-guide-choice'), null);
    assert.deepEqual(ctx.calls, []);
    ctx.dom.window.close();
});

test('busy startup defers greetings while an isolated manual failure still releases them', async () => {
    const ctx = startup({ choice: 'click', pending: true, revision: 1 });
    ctx.manager.isTutorialRunning = true;
    ctx.dom.window.isNekoHomeTutorialPending = true;
    assert.equal(await ctx.api.handleStartup(ctx.manager), false);
    assert.equal(ctx.dom.window.isNekoHomeTutorialPending, false);
    assert.ok(!ctx.calls.includes('released'));
    ctx.manager.isTutorialRunning = false;
    ctx.api.prepareChat = async () => { throw new Error('manual_host_timeout'); };
    assert.equal(await ctx.api.startHome(), false);
    assert.ok(ctx.calls.includes('released'), 'manual runs have no seven-day fallback to release greetings');
    ctx.dom.window.close();
});

test('model boot waits for click state before predicting either tutorial', async () => {
    const { dom } = setup();
    const root = dom.window;
    let choice = null;
    let settled = false;
    let release;
    const ready = new Promise(resolve => { release = resolve; });
    root.NekoClickGuideState = { get: () => choice, isReady: () => settled, ready: () => ready };
    root.NekoSevenDayTutorialState = {
        isReady: () => true, loadState: () => ({}), getNextAutoRound: () => 2,
        getTodayLocalDate: () => '2026-10-01', normalizeRound: value => value,
    };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/avatar-floating-boot-predictor.js'), 'utf8'));
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), false);
    let bootReady = false;
    const wait = root.NekoAvatarFloatingBoot.waitForAuthoritativeState().then(() => { bootReady = true; });
    await delay(10);
    assert.equal(bootReady, false);
    choice = { choice: null, pending: false };
    settled = true;
    release(choice);
    await wait;
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), true, 'new users preserve original seven-day role prediction');
    choice = { choice: 'click', pending: true };
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), false);
    choice = { choice: 'click', pending: false };
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), true, 'finished click replay preserves normal seven-day prediction');
    choice = { choice: 'seven-day', pending: false };
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), true);
    choice = null;
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), true, 'settled API failure falls back to seven-day prediction');
    for (const file of ['js/index.js', 'live2d/live2d-init.js', 'vrm/vrm-init.js', 'mmd/mmd-init.js']) {
        const source = fs.readFileSync(path.join(__dirname, '../../static', file), 'utf8');
        assert.match(source, /await window\.NekoAvatarFloatingBoot\?\.waitForAuthoritativeState\?\.\(\)/);
    }
    dom.window.close();
});

test('existing seven-day users and completed click users do not see the chooser', async () => {
    for (const choice of ['seven-day', 'click']) {
        const ctx = startup({ choice, status: 'completed', pending: false, revision: 1 });
        ctx.dom.window.isNekoHomeTutorialPending = true;
        const recovery = [];
        ctx.dom.window.NekoSevenDayTutorialState.ready = async () => { recovery.push('ready'); };
        ctx.dom.window.NekoClickGuideState.resumeSevenDay = () => { recovery.push('resume'); };
        ctx.dom.window.NekoSevenDayTutorialState.flush = async () => { recovery.push('flush'); };
        assert.equal(await ctx.api.handleStartup(ctx.manager), false);
        assert.equal(ctx.dom.window.isNekoHomeTutorialPending, choice !== 'click');
        assert.deepEqual(recovery, choice === 'click' ? ['ready', 'resume', 'flush'] : []);
        assert.ok(!ctx.calls.includes('released'), 'seven-day startup still owns greeting release');
        assert.equal(ctx.doc.querySelector('.click-guide-choice'), null);
        assert.ok(!ctx.calls.includes('choose'));
        ctx.dom.window.close();
    }
});

test('new users always hand startup to seven-day without a choice or click-state writes', async () => {
    for (const choice of [null, 'seven-day']) {
        const state = { choice, status: 'unseen', pending: false, revision: 0 };
        const ctx = startup(state);
        ctx.api.waitUntil = async () => { throw new Error('click i18n must not gate seven-day'); };
        assert.equal(await ctx.api.handleStartup(ctx.manager), false);
        assert.equal(ctx.doc.querySelector('.click-guide-choice'), null);
        assert.deepEqual(ctx.calls, []);
        assert.equal(state.revision, 0);
        ctx.dom.window.close();
    }
});

test('memory reactivation offers both flows and starts only an explicitly chosen click guide', async () => {
    for (const choice of ['seven-day', 'click']) {
        const old = { completedRounds: [1, 2] };
        const ctx = startup({ choice: 'seven-day', status: 'completed', pending: false, revision: 0 }, old);
        ctx.dom.window.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/reactivation.js'), 'utf8'));
        const result = ctx.dom.window.NekoTutorialReactivation.open(selected => ctx.dom.window.NekoClickGuideState.update('choose', { choice: selected }));
        const buttons = ctx.doc.querySelectorAll('.click-guide-choice button');
        buttons[choice === 'click' ? 0 : 1].click();
        assert.equal(await result, choice);
        assert.equal(await ctx.api.handleStartup(ctx.manager), choice === 'click');
        assert.deepEqual(old, { completedRounds: [1, 2] });
        assert.equal(ctx.calls.includes('finish'), choice === 'click');
        ctx.dom.window.close();
    }
});

test('memory browser reactivation saves the choice and resets only the selected progress', async () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/memory_browser.js'), 'utf8');
    const start = source.indexOf('    async function resetClickGuide()');
    const end = source.indexOf('    async function resetSelectedTutorial()', start);
    for (const choice of ['click', 'seven-day', null]) {
        const ctx = startup({ choice: 'seven-day', pending: false });
        const root = ctx.dom.window;
        root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/reactivation.js'), 'utf8'));
        const calls = [];
        root.translate = key => key;
        root.showTutorialResetNotice = async message => { calls.push(message); };
        root.getTutorialHomeAllResetSuccessMessage = () => 'seven-day-reset';
        root.AvatarFloatingGuideReset = {
            resetAllAvatarFloatingGuideDays: async () => { calls.push('reset-seven-day'); }
        };
        root.eval(source.slice(start, end) + '\nwindow.resetClickGuide = resetClickGuide;');
        const pending = root.resetClickGuide();
        const buttons = ctx.doc.querySelectorAll('.click-guide-choice button');
        buttons[choice === 'click' ? 0 : choice === 'seven-day' ? 1 : 2].click();
        await pending;
        assert.equal(calls.includes('reset-seven-day'), choice === 'seven-day');
        assert.equal(ctx.calls.includes('choose'), choice !== null);
        assert.equal(root.NekoClickGuideState.get().pending, choice === 'click');
        if (!choice) assert.deepEqual(calls, []);
        ctx.dom.window.close();
    }
});

test('explicit click reactivation takes precedence over stale seven-day manual intent', async () => {
    const ctx = startup({ choice: 'click', pending: true }, { manualResetRound: 1 });
    assert.equal(await ctx.api.handleStartup(ctx.manager), true);
    assert.ok(ctx.calls.includes('finish'));
    assert.equal(await ctx.api.handleStartup(ctx.manager), false, 'completed replay returns to daily scheduling');
    ctx.dom.window.close();
});

test('completed click replay clears only superseded manual intent and resumes daily prediction', async () => {
    const ctx = startup({ choice: 'click', pending: true });
    const root = ctx.dom.window;
    const sevenDay = require('../../static/tutorial/core/seven-day-state.js');
    const options = { storage: root.localStorage, syncServer: false };
    const reset = sevenDay.resetAll(options);
    const clickState = root.NekoClickGuideState.get();
    clickState.selectedAt = Date.parse(reset.resetHistory.at(-1).resetAt) + 1;
    root.NekoSevenDayTutorialState = {
        ...sevenDay,
        ready: async () => {}, isReady: () => true, flush: async () => {},
        loadState: () => sevenDay.loadState(options),
        saveState: state => sevenDay.saveState(state, options)
    };
    assert.equal(await ctx.api.handleStartup(ctx.manager), true);
    assert.equal(clickState.pending, false);
    root.fetch = async () => ({ ok: true, json: async () => clickState });
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/state.js'), 'utf8'));
    await root.NekoClickGuideState.ready();
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home.js'), 'utf8'));
    assert.equal(await ctx.api.handleStartup(ctx.manager), false);
    const resumed = root.NekoSevenDayTutorialState.loadState();
    assert.equal(resumed.manualResetRound, null);
    assert.equal(resumed.pendingRound, null);
    assert.deepEqual(resumed.completedRounds, reset.completedRounds);
    assert.deepEqual(resumed.resetHistory, reset.resetHistory);
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/avatar-floating-boot-predictor.js'), 'utf8'));
    assert.equal(root.NekoAvatarFloatingBoot.shouldSkipUserModelBoot(), true);
    const newer = sevenDay.resetRound(3, options);
    clickState.selectedAt = Date.parse(newer.resetHistory.at(-1).resetAt) - 1;
    root.NekoClickGuideState.resumeSevenDay();
    assert.equal(root.NekoSevenDayTutorialState.loadState().manualResetRound, 3);
    ctx.dom.window.close();
});

test('failed seven-day replay reset then cancel preserves the pending click choice', async () => {
    const state = { choice: 'click', pending: true, revision: 7 };
    const ctx = startup(state);
    const root = ctx.dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/reactivation.js'), 'utf8'));
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/memory_browser.js'), 'utf8');
    const start = source.indexOf('    async function resetClickGuide()');
    const end = source.indexOf('    async function resetSelectedTutorial()', start);
    root.translate = key => key;
    root.showTutorialResetNotice = async () => {};
    root.AvatarFloatingGuideReset = {
        resetAllAvatarFloatingGuideDays: async () => { throw new Error('reset failed'); }
    };
    root.eval(source.slice(start, end) + '\nwindow.resetClickGuide = resetClickGuide;');
    const result = root.resetClickGuide();
    ctx.doc.querySelectorAll('.click-guide-choice button')[1].click();
    await delay(20);
    assert.deepEqual(state, { choice: 'click', pending: true, revision: 7 });
    assert.ok(!ctx.calls.includes('choose'));
    ctx.doc.querySelectorAll('.click-guide-choice button')[2].click();
    await result;
    assert.deepEqual(state, { choice: 'click', pending: true, revision: 7 });
    ctx.dom.window.close();
});

test('seven-day reset continues after either click API failure but retains its own errors', async () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/memory_browser.js'), 'utf8');
    const start = source.indexOf('    async function performSelectedTutorialReset()');
    const end = source.indexOf('    async function resetClickGuide()', start);
    for (const failure of ['refresh', 'update', 'reset']) {
        const ctx = startup({ choice: 'click', pending: true });
        const root = ctx.dom.window;
        root.resolveSelectedTutorialReset = () => ({ type: 'home-day', day: 3 });
        const expected = new Error(failure);
        if (failure !== 'reset') root.NekoClickGuideState[failure] = async () => { throw expected; };
        let resets = 0;
        root.AvatarFloatingGuideReset = { resetAvatarFloatingGuideDay: async () => {
            resets++;
            if (failure === 'reset') throw expected;
        } };
        root.eval(source.slice(start, end) + '\nwindow.performReset = performSelectedTutorialReset;');
        if (failure === 'reset') await assert.rejects(root.performReset(), error => error === expected);
        else await root.performReset();
        assert.equal(resets, 1);
        if (failure === 'reset') {
            assert.equal(root.NekoClickGuideState.get().pending, true);
            assert.ok(!ctx.calls.includes('choose'));
        }
        ctx.dom.window.close();
    }
});

test('only a newer explicit seven-day reset overrides a click replay mode', async () => {
    const ctx = startup({ choice: 'click', pending: false });
    const root = ctx.dom.window;
    const selectedAt = Date.parse('2026-10-01T03:00:00Z');
    root.fetch = async () => ({ ok: true, json: async () => ({ choice: 'click', pending: false, selectedAt }) });
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/state.js'), 'utf8'));
    await root.NekoClickGuideState.ready();
    for (const [time, overrides] of [['02:59:00', false], ['03:01:00', true]]) {
        const sevenDay = { manualResetRound: 3, resetHistory: [{ day: 3, resetAt: `2026-10-01T${time}Z` }] };
        assert.equal(root.NekoClickGuideState.isSevenDayOverride(sevenDay), overrides);
        root.NekoSevenDayTutorialState.loadState = () => sevenDay;
        root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home.js'), 'utf8'));
        assert.equal(await ctx.api.handleStartup(ctx.manager), false);
    }
    const completedReset = { manualResetRound: null, completedRounds: [3],
        resetHistory: [{ day: 3, resetAt: '2026-10-01T04:00:00Z' }] };
    assert.equal(root.NekoClickGuideState.isSevenDayOverride(completedReset), true, 'completed reset remains the latest tutorial selection');
    root.NekoSevenDayTutorialState.loadState = () => completedReset;
    root.NekoClickGuideState.get().pending = true;
    assert.equal(await ctx.api.handleStartup(ctx.manager), false, 'completion cannot revive the old click choice');
    assert.equal(root.NekoClickGuideState.isSevenDayOverride({ resetHistory: [] }), false);
    ctx.dom.window.close();
});

test('committed seven-day replay remains successful when auxiliary choice save fails', async () => {
    const ctx = startup({ choice: 'click', pending: true });
    const root = ctx.dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/reactivation.js'), 'utf8'));
    const selectedAt = Date.parse('2026-10-01T03:00:00Z');
    root.fetch = async () => ({ ok: true, json: async () => ({ choice: 'click', pending: true, selectedAt }) });
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/state.js'), 'utf8'));
    await root.NekoClickGuideState.ready();
    const sevenDay = { completedRounds: [1, 2], manualResetRound: null };
    root.NekoSevenDayTutorialState.loadState = () => sevenDay;
    root.NekoClickGuideState.update = async () => { throw new Error('save conflict or offline'); };
    root.AvatarFloatingGuideReset = { resetAllAvatarFloatingGuideDays: async () => {
        Object.assign(sevenDay, { completedRounds: [], manualResetRound: 1,
            resetHistory: [{ day: 'all', resetAt: '2026-10-01T03:01:00Z' }] });
    } };
    const notices = [];
    root.translate = key => key;
    root.getTutorialHomeAllResetSuccessMessage = () => 'seven-day-reset-success';
    root.showTutorialResetNotice = async message => { notices.push(message); };
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/memory_browser.js'), 'utf8');
    root.eval(source.slice(source.indexOf('    async function resetClickGuide()'),
        source.indexOf('    async function resetSelectedTutorial()')) + '\nwindow.resetClickGuide = resetClickGuide;');
    const result = root.resetClickGuide();
    ctx.doc.querySelectorAll('.click-guide-choice button')[1].click();
    await result;
    assert.equal(ctx.doc.querySelector('.click-guide-choice'), null);
    assert.deepEqual(notices, ['seven-day-reset-success']);
    assert.equal(root.NekoClickGuideState.isSevenDayOverride(sevenDay), true);
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home.js'), 'utf8'));
    assert.equal(await ctx.api.handleStartup(ctx.manager), false, 'seven-day reset owns next startup');
    ctx.dom.window.close();
});

test('seven-day reactivation commits even when click refresh fails', async () => {
    const ctx = startup({ choice: 'click', pending: true });
    const root = ctx.dom.window;
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/reactivation.js'), 'utf8'));
    root.NekoClickGuideState.refresh = async () => { throw new Error('click state offline'); };
    let reset = false;
    root.AvatarFloatingGuideReset = { resetAllAvatarFloatingGuideDays: async () => { reset = true; } };
    const notices = [];
    root.translate = key => key;
    root.getTutorialHomeAllResetSuccessMessage = () => 'seven-day-success';
    root.showTutorialResetNotice = async message => notices.push(message);
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/memory_browser.js'), 'utf8');
    root.eval(source.slice(source.indexOf('    async function resetClickGuide()'),
        source.indexOf('    async function resetSelectedTutorial()')) + '\nwindow.resetClickGuide = resetClickGuide;');
    const result = root.resetClickGuide();
    ctx.doc.querySelectorAll('.click-guide-choice button')[1].click();
    await result;
    assert.equal(reset, true);
    assert.deepEqual(notices, ['seven-day-success']);
    assert.equal(ctx.doc.querySelector('.click-guide-choice'), null);
    assert.ok(!ctx.calls.includes('choose'));
    ctx.dom.window.close();
});

test('insecure HTTP without randomUUID can start a guide and native presentation', async () => {
    const ctx = startup({ choice: 'click', pending: true });
    const root = ctx.dom.window;
    Object.defineProperty(root.crypto, 'randomUUID', { value: undefined });
    assert.equal(await ctx.api.handleStartup(ctx.manager), true);
    assert.ok(ctx.calls.includes('finish'));
    let closedId;
    root.nekoTutorialOverlay = { clickGuideUpdate: async () => ({ ok: true }),
        clickGuideClose: async ({ runId }) => { closedId = runId; } };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/native.js'), 'utf8'));
    const presentation = ctx.api.createNativePresentation();
    await presentation.close();
    assert.match(closedId, /^click-view-.+/);
    ctx.dom.window.close();
});

test('pagehide interrupts a pending replay without recording finish or skip', async () => {
    const state = { choice: 'click', pending: true };
    const ctx = startup(state);
    let started = false;
    ctx.api.createRunner = ({ onEnd }) => ({ history: [], skipped: [],
        start: async () => { started = true; }, stop: async reason => onEnd(reason) });
    const result = ctx.api.handleStartup(ctx.manager);
    while (!started) await delay(1);
    ctx.dom.window.dispatchEvent(new ctx.dom.window.Event('pagehide'));
    assert.equal(await result, false);
    assert.equal(state.pending, true);
    assert.ok(!ctx.calls.includes('finish'));
    ctx.dom.window.close();
});

test('direct model prediction projects stale manual intent without saving unsynchronized state', async () => {
    const { dom } = setup();
    const root = dom.window;
    const progress = { manualResetRound: 3, pendingRound: 3, resetHistory: [] };
    root.fetch = async () => ({ ok: true, json: async () => ({ choice: 'click', pending: false, selectedAt: 10 }) });
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/state.js'), 'utf8'));
    await root.NekoClickGuideState.ready();
    root.NekoSevenDayTutorialState = { isReady: () => false, loadState: () => progress,
        saveState: () => { throw new Error('prediction must never save'); },
        getNextAutoRound: projected => projected.manualResetRound || 2,
        getTodayLocalDate: () => '2026-10-01', normalizeRound: value => value };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/avatar-floating-boot-predictor.js'), 'utf8'));
    assert.equal(root.NekoAvatarFloatingBoot.claimDirectTutorialBoot(), true);
    assert.equal(root.NekoAvatarFloatingBoot.getPredictedRound(), 2);
    assert.equal(progress.manualResetRound, 3);
    assert.equal(progress.pendingRound, 3);
    dom.window.close();
});

test('only compact chat receives the shared start while full chat stays passive', async () => {
    const contexts = ['/chat', '/chat_full'].map(chatPath => {
        const ctx = setup();
        const root = ctx.dom.window;
        root.history.replaceState(null, '', chatPath);
        root.t = key => key;
        root.NekoClickGuideState = {};
        let preparations = 0;
        ctx.api.prepareChat = async () => { preparations++; return () => {}; };
        ctx.api.chatSteps = () => [];
        ctx.api.createNativePresentation = () => null;
        ctx.api.createRunner = () => ({ start: async () => {}, stop: async () => {} });
        root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home.js'), 'utf8'));
        return { ...ctx, preparations: () => preparations };
    });
    try {
        for (const ctx of contexts) {
            const root = ctx.dom.window;
            root.dispatchEvent(new root.CustomEvent('neko:tutorial-overlay-relay', {
                detail: { action: 'click_guide', type: 'start', runId: 'shared-run' }
            }));
        }
        await delay(10);
        assert.equal(contexts[0].preparations(), 1);
        assert.equal(contexts[1].preparations(), 0);
        assert.equal(contexts[0].dom.window.isNekoClickGuideActive, true);
        assert.notEqual(contexts[1].dom.window.isNekoClickGuideActive, true);
    } finally { contexts.forEach(ctx => ctx.dom.window.close()); }
});

for (const chatPath of ['/chat', '/chat_full']) {
test('chat state module makes no startup request: ' + chatPath, async () => {
    const { dom } = setup();
    const root = dom.window;
    root.history.replaceState(null, '', chatPath);
    root.fetch = () => { throw new Error('chat must not read guide state'); };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/state.js'), 'utf8'));
    assert.equal(await root.NekoClickGuideState.ready(), null);
    assert.equal(root.NekoClickGuideState.isReady(), true);
    dom.window.close();
});
}

test('click mutations reuse CSRF token and retry a CSRF rejection exactly once', async () => {
    const { dom } = setup();
    const root = dom.window;
    root.pageConfigReady = Promise.resolve({ autostart_csrf_token: 'initial' });
    const saved = { choice: 'click', pending: true, revision: 1 };
    const tokens = [];
    let configReads = 0;
    root.fetch = async (url, options = {}) => {
        if (url === '/api/config/page_config') {
            configReads++;
            return { ok: true, json: async () => ({ autostart_csrf_token: 'refreshed' }) };
        }
        if (options.method !== 'POST') return { ok: true, json: async () => saved };
        tokens.push(options.headers['X-CSRF-Token']);
        if (tokens.length === 1) return { ok: false, status: 403,
            json: async () => ({ error_code: 'csrf_validation_failed' }) };
        return { ok: true, status: 200, json: async () => ({ state: saved }) };
    };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/state.js'), 'utf8'));
    await root.NekoClickGuideState.ready();
    await root.NekoClickGuideState.update('reset');
    await root.NekoClickGuideState.update('reset');
    assert.deepEqual(tokens, ['initial', 'refreshed', 'refreshed']);
    assert.equal(configReads, 1);
    dom.window.close();
});

test('the input lesson keeps focus on the composer instead of collapsing it', async () => {
    const { dom, api, target, doc } = setup();
    const input = doc.createElement('textarea');
    input.className = 'composer-input';
    input.getBoundingClientRect = target.getBoundingClientRect;
    input.addEventListener('blur', () => input.remove());
    doc.body.append(input);
    const guide = api.createRunner({ labels, steps: [{ target: '#target', keyTarget: '.composer-input',
        advanceOnKey: 'Enter', requireInput: true }] });
    try {
        await guide.start();
        assert.equal(doc.activeElement, input);
        await delay(40);
        assert.equal(input.isConnected, true);
        assert.equal(doc.querySelector('.click-guide-status').textContent, '');
    } finally { await guide.stop(); dom.window.close(); }
});

test('history close keeps the panel and the blue collapse bar lit separately', async () => {
    const { dom, api, doc } = setup();
    const root = dom.window;
    root.t = key => key;
    const originalStyle = root.getComputedStyle.bind(root);
    root.getComputedStyle = (element, pseudo) => pseudo === '::before'
        ? { width: '44px', height: '3px' } : originalStyle(element);
    const handle = doc.createElement('button');
    handle.className = 'compact-history-visibility-handle';
    handle.setAttribute('aria-expanded', 'true');
    handle.getBoundingClientRect = () => ({ left: 100, right: 200, top: 500, bottom: 526, width: 100, height: 26 });
    const anchor = doc.createElement('section');
    anchor.className = 'compact-export-history-anchor';
    anchor.dataset.compactExportHistoryOpen = 'true';
    const panel = doc.createElement('div');
    panel.className = 'compact-export-history-panel';
    panel.getBoundingClientRect = () => ({ left: 50, right: 250, top: 200, bottom: 490, width: 200, height: 290 });
    anchor.append(panel); doc.body.append(handle, anchor);
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const guide = api.createRunner({ labels, steps: [api.chatSteps()[2]] });
    try {
        await guide.start();
        const [barFrame, panelFrame] = doc.querySelectorAll('.click-guide-highlight');
        assert.equal(barFrame.style.width, '56px');
        assert.equal(panelFrame.style.width, '200px');
        assert.equal(barFrame.classList.contains('is-click-step'), true);
        assert.equal(panelFrame.classList.contains('is-click-step'), false);
        const apertures = doc.querySelectorAll('.click-guide-mask-visual rect[fill="black"]');
        assert.equal(apertures.length, 2);
        assert.ok([...apertures].every(rect => rect.style.display === ''));
    } finally { await guide.stop(); dom.window.close(); }
});

test('floating overview consumes only a real button click and keeps the cursor on a button', async () => {
    const { dom, api, doc } = setup();
    const root = dom.window;
    root.t = key => key;
    root.universalTutorialManager = { constructor: { detectModelPrefix: () => 'live2d' } };
    const group = doc.createElement('div');
    group.id = 'live2d-floating-buttons';
    group.getBoundingClientRect = () => ({ left: 100, right: 160, top: 100, bottom: 340, width: 60, height: 240 });
    const actions = {};
    for (const [i, name] of ['mic', 'agent', 'social', 'settings', 'goodbye'].entries()) {
        const button = doc.createElement('button');
        button.id = `live2d-btn-${name}`;
        button.getBoundingClientRect = () => ({ left: 110, right: 150,
            top: 110 + i * 45, bottom: 150 + i * 45, width: 40, height: 40 });
        actions[name] = 0;
        button.onclick = () => actions[name]++;
        group.append(button);
    }
    doc.body.append(group);
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    try {
        for (const name of Object.keys(actions)) {
            const guide = api.createRunner({ labels, steps: [api.floatingSteps()[0], { title: 'Mic' }] });
            try {
                await guide.start();
                group.click();
                await delay(5);
                assert.equal(guide.index, 0, 'empty space is not a button');
                assert.equal(doc.querySelector('.click-guide-ghost-cursor').style.left, '10px');
                group.querySelector(`#live2d-btn-${name}`).click();
                await delay(35);
                assert.equal(guide.index, 1);
                assert.equal(actions[name], 0, `${name} action must be blocked`);
            } finally { await guide.stop(); }
        }
        group.querySelector('#live2d-btn-mic').click();
        assert.equal(actions.mic, 1, 'normal clicking is restored after the guide');
    } finally { dom.window.close(); }
});

test('Back follows visited steps and does not count conditional skips twice', async () => {
    const { dom, api, doc } = setup();
    const guide = api.createRunner({ labels: { ...labels, back: 'Previous' }, steps: [
        { title: 'First' }, { title: 'Skipped', when: () => false }, { title: 'Third' }
    ] });
    try {
        await guide.start();
        assert.equal(doc.querySelector('.click-guide-back').disabled, true);
        doc.querySelector('.click-guide-next').click(); await delay(35);
        assert.equal(guide.index, 2);
        assert.equal(doc.querySelector('.click-guide-progress').textContent, '2 / 2');
        doc.querySelector('.click-guide-back').click(); await delay(35);
        assert.equal(guide.index, 0);
        assert.equal(doc.querySelector('.click-guide-progress').textContent, '1 / 2');
        doc.querySelector('.click-guide-next').click(); await delay(35);
        assert.equal(guide.index, 2);
        assert.equal(doc.querySelector('.click-guide-progress').textContent, '2 / 2');
    } finally { await guide.stop(); dom.window.close(); }
});

test('Back from floating 1/9 returns to the chat final step, then its prior step', async () => {
    const ctx = startup({ choice: 'click', status: 'unseen', pending: true, revision: 1 });
    const root = ctx.dom.window;
    let surface = 'compact';
    root.reactChatWindowHost = {
        setChatSurfaceMode(value) { surface = value; }, getChatSurfaceMode: () => surface
    };
    ctx.api.chatSteps = () => Array.from({ length: 13 }, (_, i) => ({
        id: i === 12 ? 'restore' : `chat-${i}`, title: `Chat ${i + 1}`
    }));
    ctx.api.floatingSteps = () => [{ title: 'Floating 1' }];
    const finished = ctx.api.startHome();
    try {
        await delay(30);
        for (let i = 0; i < 13; i++) {
            assert.equal(ctx.doc.querySelector('.click-guide-card h2')?.textContent, `Chat ${i + 1}`);
            ctx.doc.querySelector('.click-guide-next').click();
            await delay(25);
        }
        assert.equal(ctx.doc.querySelector('.click-guide-card h2')?.textContent, 'Floating 1');
        assert.equal(ctx.doc.querySelector('.click-guide-back').disabled, false);
        ctx.doc.querySelector('.click-guide-back').click();
        await delay(50);
        assert.equal(ctx.doc.querySelector('.click-guide-card h2')?.textContent, 'Chat 13');
        assert.equal(ctx.doc.querySelector('.click-guide-progress').textContent, 'clickGuide.sections.chat · 13 / 13');
        assert.equal(surface, 'minimized');
        ctx.doc.querySelector('.click-guide-back').click();
        await delay(35);
        assert.equal(ctx.doc.querySelector('.click-guide-card h2')?.textContent, 'Chat 12');
    } finally {
        ctx.doc.querySelector('.click-guide-actions button')?.click();
        await finished;
        ctx.dom.window.close();
    }
});

test('floating adaptation isolates the overlapping lock and restores presence on exit', async () => {
    const { dom, api, doc } = setup();
    const root = dom.window;
    doc.body.innerHTML = '<div id="live2d-floating-buttons" style="opacity:0"><button id="live2d-btn-settings"></button></div>'
        + '<div id="live2d-lock-icon" style="visibility:visible"></div><button class="neko-idle-return-btn"></button>';
    doc.querySelector('.neko-idle-return-btn').getBoundingClientRect = () => ({left: 20, top: 20, right: 60, bottom: 60, width: 40, height: 40});
    root.t = key => key;
    root.universalTutorialManager = { constructor: { detectModelPrefix: () => 'live2d' } };
    root.live2dManager = { _goodbyeClicked: false, closeAllPopups() {} };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const cleanup = await api.prepareFloating();
    const lock = doc.querySelector('#live2d-lock-icon');
    assert.ok(lock.classList.contains('click-guide-hidden-control'));
    api.floatingSteps().find(step => step.id === 'lock').enter();
    assert.equal(lock.classList.contains('click-guide-hidden-control'), false);
    root.live2dManager._goodbyeClicked = true;
    doc.querySelector('.neko-idle-return-btn').onclick = () => { root.live2dManager._goodbyeClicked = false; };
    await cleanup();
    assert.equal(root.live2dManager._goodbyeClicked, false);
    assert.equal(lock.style.visibility, 'visible');
    assert.equal(lock.style.display, '');
    assert.equal(doc.querySelector('#live2d-floating-buttons').style.opacity, '0');
    assert.equal(doc.querySelector('#live2d-floating-buttons').hasAttribute('data-in-tutorial'), false);
    dom.window.close();
});

test('an initially away character is recalled for the toolbar and returned afterwards', async () => {
    const { dom, api, doc } = setup();
    const root = dom.window;
    doc.body.innerHTML = '<div id="live2d-floating-buttons"><button id="live2d-btn-goodbye"></button></div><button class="neko-idle-return-btn"></button>';
    const recall = doc.querySelector('.neko-idle-return-btn');
    recall.getBoundingClientRect = () => ({left: 20, top: 20, right: 60, bottom: 60, width: 40, height: 40});
    root.t = key => key;
    root.universalTutorialManager = { constructor: { detectModelPrefix: () => 'live2d' } };
    root.live2dManager = { _goodbyeClicked: true, closeAllPopups() {} };
    recall.onclick = () => { root.live2dManager._goodbyeClicked = false; };
    doc.querySelector('#live2d-btn-goodbye').onclick = () => { root.live2dManager._goodbyeClicked = true; };
    root.eval(fs.readFileSync(path.join(__dirname, '../../static/tutorial/click-guide/home-steps.js'), 'utf8'));
    const cleanup = await api.prepareFloating();
    assert.equal(root.live2dManager._goodbyeClicked, false);
    await cleanup();
    assert.equal(root.live2dManager._goodbyeClicked, true);
    dom.window.close();
});

test('shared window proxies offer focus and wait for actual closure without pretending to close', async () => {
    const { dom, api, doc } = setup();
    let focuses = 0;
    let closes = 0;
    const shared = { closed: false, focus() { focuses++; }, close() { closes++; } };
    dom.window.openOrFocusWindow = () => shared;
    const shown = [];
    const guide = api.createRunner({ labels, onStep: (_step, index) => shown.push(index), steps: [
        { target: '#target', windowGuide: { title: 'Return', body: 'Close this page', nextLabel: 'Close',
            manualClose: 'Close the existing page, then return here', returnToPage: 'Go to the page', closeSelector: '#close' } },
        { title: 'Next' }, { title: 'Last' },
    ] });
    try {
        await guide.start(); dom.window.openOrFocusWindow('/settings'); await delay(120);
        assert.equal(doc.querySelector('.click-guide-card p').textContent, 'Close the existing page, then return here');
        assert.equal(doc.querySelector('.click-guide-next').textContent, 'Go to the page');
        doc.querySelector('.click-guide-next').click(); await delay(120);
        assert.equal(focuses, 1);
        assert.equal(closes, 0);
        assert.equal(guide.index, 0);
        shared.closed = true; await delay(240);
        assert.equal(guide.index, 1);
        assert.deepEqual(shown, [0, 1]);
    } finally { await guide.stop(); dom.window.close(); }
});

test('cross-origin real windows retain their native close fallback', async () => {
    const { dom, api, doc } = setup();
    let closes = 0;
    const child = { closed: false, close() { closes++; child.closed = true; } };
    child.window = child;
    Object.defineProperty(child, 'document', { get() { throw new Error('Cross-origin access denied'); } });
    dom.window.open = () => child;
    const guide = api.createRunner({ labels, steps: [
        { target: '#target', windowGuide: { title: 'Return', body: 'Close this page', nextLabel: 'Close',
            manualClose: 'Close the existing page', returnToPage: 'Go to the page', closeSelector: '#close' } },
        { title: 'Next' },
    ] });
    try {
        await guide.start(); dom.window.open('https://example.invalid'); await delay(120);
        assert.equal(doc.querySelector('.click-guide-next').textContent, 'Close');
        doc.querySelector('.click-guide-next').click(); await delay(140);
        assert.equal(closes, 1);
        assert.equal(guide.index, 1);
    } finally { await guide.stop(); dom.window.close(); }
});

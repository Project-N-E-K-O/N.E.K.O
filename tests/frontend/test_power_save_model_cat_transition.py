"""The pending form stays hidden while legacy canvas cleanup runs."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("model_type", ["live2d", "vrm", "mmd", "pngtuber"])
def test_instant_return_waits_for_window_without_canvas_fade(page, model_type):
    page.set_content(
        f'<div id="{model_type}-container"><canvas width="160" height="230"></canvas></div>'
    )
    page.add_style_tag(path=str(ROOT / "static/css/index.css"))
    page.add_style_tag(
        content=f"#{model_type}-container {{ display:block; visibility:visible; }}"
    )
    if model_type == "live2d":
        page.add_style_tag(path=str(ROOT / "static/css/yui-guide.css"))
        page.evaluate("""() => {
            document.body.classList.add('yui-guide-live2d-preparing', 'yui-guide-return-petal-fade');
            document.body.style.setProperty('--yui-guide-return-avatar-opacity', '0');
        }""")
    page.evaluate(
        """() => {
            window.appUi = {};
            window.__appUiParts = {};
            window.nekoPetPowerSave = {
                isEnabled: () => true,
                prepareFormChange: () => new Promise(resolve => { window.finishForm = resolve; })
            };
        }"""
    )
    page.add_script_tag(path=str(ROOT / "static/app/app-ui/return-transitions.js"))
    page.evaluate(
        """type => {
            const container = document.getElementById(type + '-container');
            window.__appUiParts.playModelReturnEnter(container, {left:400,top:200,width:160,height:230});
            const canvas = container.querySelector('canvas');
            // Model display sets these after playModelReturnEnter returns.
            canvas.style.transition = 'opacity 1.12s ease-out';
            canvas.style.opacity = '0.001';
            void canvas.offsetWidth;
            canvas.style.opacity = '1';
            // Legacy fade cleanup must not reveal a form whose IPC is still pending.
            container.style.removeProperty('opacity');
        }""",
        model_type,
    )
    container = page.locator(f"#{model_type}-container")
    if model_type == "live2d":
        page.add_script_tag(path=str(ROOT / "static/tutorial/core/universal-manager.js"))
        page.evaluate("UniversalTutorialManager.prototype.restoreTutorialLive2dDisplayState.call({}, 'pending-form-test')")
        assert not page.locator('body').evaluate("el => el.classList.contains('yui-guide-live2d-preparing')")
        assert not page.locator('body').evaluate("el => el.classList.contains('yui-guide-return-petal-fade')")
        assert page.locator('body').evaluate("el => el.style.getPropertyValue('--yui-guide-return-avatar-opacity')") == ""
    assert container.evaluate("el => getComputedStyle(el).opacity") == "0"
    assert container.locator("canvas").evaluate("el => getComputedStyle(el).opacity") == "1"
    assert container.locator("canvas").evaluate(
        "el => getComputedStyle(el).transitionDuration"
    ) == "0s"
    page.evaluate("window.finishForm(true)")
    page.wait_for_function("window._nekoModelReturnEnterPromise === null")
    assert container.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert container.locator("canvas").evaluate("el => getComputedStyle(el).opacity") == "1"
    assert container.get_attribute("data-neko-model-form-pending") is None
    assert container.evaluate("el => el.style.transition") == ""
    assert page.locator("#neko-model-cat-transition").count() == 0
    if model_type == "live2d":
        page.evaluate("UniversalTutorialManager.prototype.restoreTutorialLive2dDisplayState.call({}, 'form-ready-test')")
        assert container.evaluate("el => getComputedStyle(el).opacity") == "1"


def test_instant_cat_return_blocks_child_hits_until_explicit_recovery(page):
    page.set_content('''<div id="cat-container" style="position:fixed;left:20px;top:20px;width:80px;height:80px;pointer-events:auto">
        <button style="width:80px;height:80px;pointer-events:auto">Return</button></div>''')
    page.evaluate('''() => {
        window.appUi = {};
        window.__appUiParts = { nekoModelCatTransitionToken: 0 };
        window.nekoPetPowerSave = { isEnabled: () => true, prepareFormChange: async () => true };
    }''')
    page.add_script_tag(path=str(ROOT / "static/app/app-ui/return-transitions.js"))
    page.evaluate('''async () => {
        const I = window.__appUiParts;
        I.getPendingModelViewportRestoreBounds = () => null;
        I.isNativeReturnBallViewportSize = () => false;
        I.scheduleIdleReturnBallDesktopBridge = () => {};
        await I.playNekoModelCatTransition({ direction:'cat-to-model', container:document.getElementById('cat-container') });
    }''')
    assert page.evaluate("!document.elementFromPoint(50, 50).closest('#cat-container')")
    page.evaluate("window.__appUiParts.revealReturnBallContainer(document.getElementById('cat-container'), 'aborted-return')")
    assert page.evaluate("document.elementFromPoint(50, 50).tagName") == 'BUTTON'
    assert page.locator('#cat-container').evaluate("el => getComputedStyle(el).opacity") == '1'
    assert page.locator('#cat-container').evaluate("el => el.style.transition") == ''


@pytest.mark.parametrize("model_type,display_path", [
    ("live2d", "normal"), ("vrm", "normal"), ("mmd", "normal"), ("pngtuber", "normal"),
    ("live2d", "visible"), ("live2d", "preparing"), ("live2d", "corner"),
])
@pytest.mark.parametrize("power_save,request_form", [(True, True), (False, True), (True, False), (None, True)])
def test_model_display_prepares_a_requested_return_without_a_rect(page, model_type, display_path, power_save, request_form):
    page.set_content(
        f'<div id="{model_type}-container" class="hidden" style="display:none">'
        f'<canvas id="{model_type}-canvas" width="160" height="230"></canvas></div>'
    )
    page.add_style_tag(path=str(ROOT / "static/css/index.css"))
    page.evaluate("""({type, enabled, path}) => {
        window.appUi = {};
        window.__appUiParts = {mod: {}};
        window.lanlan_config = {lanlan_name: 'test', model_type: type};
        window.fetch = async () => ({ok: true, json: async () => ({'猫娘': {test: {model_type: type}}})});
        window.prepareTargets = [];
        window.nekoPetPowerSave = {
            isEnabled: () => enabled,
            prepareFormChange: target => {
                window.prepareTargets.push(target);
                return new Promise(resolve => { window.finishForm = resolve; });
            }
        };
        if (enabled === null) delete window.nekoPetPowerSave;
        window.nekoYuiGuideLive2dPreparing = path === 'preparing';
        window.nekoYuiGuideAvatarCornerPeekActive = path === 'corner';
        if (path === 'visible') {
            const container = document.getElementById(type + '-container');
            container.classList.remove('hidden');
            container.style.display = 'block';
        }
    }""", {"type": model_type, "enabled": power_save, "path": display_path})
    for name in ("return-transitions.js", "model-display.js"):
        page.add_script_tag(path=str(ROOT / "static/app/app-ui" / name))
    page.evaluate("""() => {
        const I = window.__appUiParts;
        I.ensureModelViewportReadyBeforeShowCurrentModel = async () => ({ready: true});
        I.ensureActiveModelOverlayControls = () => ({});
        I.keepAvatarRootContainerPassthrough = () => false;
    }""")
    page.evaluate("""prepareModelForm => {
        const I = window.__appUiParts;
        const sequence = I.modelReturnEnterSequence;
        window.displayFinished = false;
        window.displayResult = I.showCurrentModel({prepareModelForm}).then(async () => {
            if (prepareModelForm) await I.ensureModelReturnFormReady(sequence);
            window.displayFinished = true;
        });
    }""", request_form)
    container = page.locator(f"#{model_type}-container")
    if power_save and request_form:
        page.wait_for_function("window.prepareTargets.length === 1")
        assert page.evaluate("window.prepareTargets") == ["model"]
        assert container.get_attribute("data-neko-model-form-pending") == "true"
        assert container.evaluate("el => getComputedStyle(el).opacity") == "0"
        page.evaluate("window.finishForm(true)")
        page.wait_for_function("window.displayFinished")
        assert page.evaluate("window.prepareTargets") == ["model"]
        assert container.evaluate("el => getComputedStyle(el).opacity") == "1"
    else:
        page.wait_for_function("window.displayFinished")
        assert page.evaluate("window.prepareTargets") == []
        assert container.get_attribute("data-neko-model-form-pending") is None

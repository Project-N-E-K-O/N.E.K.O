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


@pytest.mark.parametrize("model_type", ["live2d", "vrm", "mmd", "pngtuber"])
@pytest.mark.parametrize("power_save,request_form", [(True, True), (False, True), (True, False), (None, True)])
def test_model_display_prepares_a_requested_return_without_a_rect(page, model_type, power_save, request_form):
    page.set_content(
        f'<div id="{model_type}-container" class="hidden" style="display:none">'
        f'<canvas id="{model_type}-canvas" width="160" height="230"></canvas></div>'
    )
    page.add_style_tag(path=str(ROOT / "static/css/index.css"))
    page.evaluate("""({type, enabled}) => {
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
    }""", {"type": model_type, "enabled": power_save})
    for name in ("return-transitions.js", "model-display.js"):
        page.add_script_tag(path=str(ROOT / "static/app/app-ui" / name))
    page.evaluate("""() => {
        const I = window.__appUiParts;
        I.ensureModelViewportReadyBeforeShowCurrentModel = async () => ({ready: true});
        I.ensureActiveModelOverlayControls = () => ({});
        I.keepAvatarRootContainerPassthrough = () => false;
    }""")
    page.evaluate("prepareModelForm => window.__appUiParts.showCurrentModel({prepareModelForm})", request_form)
    container = page.locator(f"#{model_type}-container")
    if power_save and request_form:
        assert page.evaluate("window.prepareTargets") == ["model"]
        assert container.get_attribute("data-neko-model-form-pending") == "true"
        assert container.evaluate("el => getComputedStyle(el).opacity") == "0"
        page.evaluate("window.finishForm(true)")
        page.wait_for_function("window._nekoModelReturnEnterPromise === null")
        assert container.evaluate("el => getComputedStyle(el).opacity") == "1"
    else:
        assert page.evaluate("window.prepareTargets") == []
        assert container.get_attribute("data-neko-model-form-pending") is None

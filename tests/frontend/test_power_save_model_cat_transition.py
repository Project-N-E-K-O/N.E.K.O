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

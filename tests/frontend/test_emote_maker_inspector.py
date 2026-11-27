"""Test inspector adjustment controls and the export drop hint.

Rebuilding slider DOM used to shorten the scroll container and clamp its scroll
position. Sliders also remained enabled while their default-value override was
disabled, although they could not affect the rendered result.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from playwright.sync_api import Page, expect


def _synthetic_template() -> bytes:
    from tests.emote_fixtures import spine_zip
    return spine_zip()


@pytest.fixture()
def adjust_page(page: Page, running_server: str) -> Page:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_function("() => Boolean(document.querySelector('#adjust-x'))", timeout=15000)
    page.set_input_files("#template-archive-file", {
        "name": "demo.zip",
        "mimeType": "application/zip",
        "buffer": _synthetic_template(),
    })
    page.wait_for_function(
        "() => document.body.classList.contains('editor-has-template')", timeout=25000)
    page.evaluate("() => document.querySelector('#inspector-tab-adjust').click()")
    return page



def test_dragging_a_slider_does_not_rebuild_the_panel(adjust_page: Page) -> None:
    """Update slider readings without replacing DOM nodes or resetting scroll position."""
    outcome = adjust_page.evaluate(r"""() => {
        const host = document.querySelector('#template-skin-controls');
        const keepAlive = host.querySelector('.template-compat-row');
        if (!keepAlive) throw new Error('Expected a real native skin control');
        let rebuilds = 0;
        const observer = new MutationObserver((records) => {
            records.forEach((r) => { rebuilds += r.addedNodes.length + r.removedNodes.length; });
        });
        observer.observe(host, { childList: true, subtree: true });
        const input = document.querySelector('#adjust-x');
        input.value = '40';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        observer.takeRecords().forEach((r) => { rebuilds += r.addedNodes.length + r.removedNodes.length; });
        observer.disconnect();
        return {
            rebuilds,
            readout: document.querySelector('#adjust-x-value').textContent,
            sameRow: keepAlive === host.querySelector('.template-compat-row'),
            inputValue: document.querySelector('#adjust-x').value,
        };
    }""")
    assert outcome["rebuilds"] == 0, f"拖动滑杆重建了滑块区（{outcome['rebuilds']} 个节点）：滚动会被夹回顶部"
    assert outcome["sameRow"] is True, "滑块行被换成了新节点"
    assert outcome["readout"] == "40", outcome
    assert outcome["inputValue"] == "40", "读数改了却把滑杆本身弹回去了"


def test_reset_is_only_offered_when_there_is_something_to_reset(adjust_page: Page) -> None:
    """Enable reset only while there is a changed value, then disable it after restoration."""
    adjust_page.evaluate("""() => {
        const input = document.querySelector('#adjust-x');
        input.value = '40';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }""")
    assert adjust_page.evaluate("() => document.querySelector('#reset-adjustments-btn').disabled") is False
    adjust_page.evaluate("() => document.querySelector('#reset-adjustments-btn').click()")
    assert adjust_page.evaluate("() => document.querySelector('#adjust-x-value').textContent") == "0"
    assert adjust_page.evaluate("() => document.querySelector('#reset-adjustments-btn').disabled") is True, \
        "已经全是默认值，重置还留着可点 —— 按下去什么都不动就是\"没作用\"的观感"


def test_a_range_drag_is_one_undo_step(adjust_page: Page) -> None:
    slider = adjust_page.locator("#adjust-x")
    slider.scroll_into_view_if_needed()
    bounds = slider.bounding_box()
    x = bounds["x"] + bounds["width"] / 2
    y = bounds["y"] + bounds["height"] / 2
    adjust_page.mouse.move(x, y)
    adjust_page.mouse.down()
    adjust_page.mouse.move(x + bounds["width"] / 4, y, steps=8)
    adjust_page.mouse.up()
    changed = slider.input_value()
    assert changed != "0"
    adjust_page.keyboard.press("Control+z")
    expect(slider).to_have_value("0")
    expect(adjust_page.locator("#undo-btn")).to_be_disabled()
    adjust_page.keyboard.press("Control+Shift+z")
    expect(slider).to_have_value(changed)
    expect(adjust_page.locator("#adjust-x-value")).to_have_text(changed)



def test_unchecking_overwrite_greys_out_the_inert_sliders(adjust_page: Page) -> None:
    """Disable all four transform sliders while their default-value override is off."""
    adjust_page.evaluate("""() => {
        const box = document.querySelector('#adjust-overwrite');
        box.checked = false;
        box.dispatchEvent(new Event('change', { bubbles: true }));
    }""")
    states = adjust_page.evaluate(
        "() => ['x','y','rotation','scale'].map(k => document.querySelector(`#adjust-${k}`).disabled)")
    assert states == [True, True, True, True], f"关掉覆盖默认值后滑杆还能拖: {states}"
    # 就算绕过禁用直接派发（Alt+滚轮就是这么改的），也不该偷偷改到值
    changed = adjust_page.evaluate("""() => {
        const input = document.querySelector('#adjust-x');
        input.value = '70';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        return { readout: document.querySelector('#adjust-x-value').textContent };
    }""")
    assert changed["readout"] == "0", f"开关关着时仍在改调整值: {changed}"


def test_drop_hint_ring_is_gone_once_the_breathe_finishes(page: Page, running_server: str) -> None:
    """Remove the static drop-hint border after its breathing animation.

        Inspect the effective CSSOM rule: synthetic templates cannot render the
        runtime-dependent pack panel, and later duplicate selectors can override
        earlier rules. A persistent border would stay visible until release.
        """
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    seen = page.evaluate(r"""() => {
        const alphaOf = (value) => {
            const parts = String(value || '').match(/[\d.]+/g);
            if (!parts) return null;
            return parts.length >= 4 ? Number(parts[3]) : 1;
        };
        let hint = null, base = null, keyframes = null;
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (_) { continue; }
            for (const rule of rules || []) {
                const sel = String(rule.selectorText || '');
                if (sel === '#pack-basic-rows.is-drop-hint') {
                    // 后写的同名规则才算数：取最后一条
                    hint = { border: rule.style.getPropertyValue('border-color')
                        || rule.style.getPropertyValue('border-top-color'),
                        name: rule.style.getPropertyValue('animation-name') };
                } else if (sel === '#pack-basic-rows') {
                    base = rule.style.getPropertyValue('border-color')
                        || rule.style.getPropertyValue('border-top-color');
                } else if (rule.type === CSSRule.KEYFRAMES_RULE && rule.name === 'pack-drop-hint-breathe') {
                    // 关键帧按声明顺序排，"0%, 100%" 会在第一条 —— 要找的是含 100% 的那条
                    const end = [...rule.cssRules]
                        .find((k) => k.keyText.split(',').map((t) => t.trim()).includes('100%'));
                    keyframes = { steps: rule.cssRules.length,
                        lastSelector: end ? end.keyText : '',
                        lastColor: end ? end.style.getPropertyValue('border-color') : '' };
                }
            }
        }
        return { hint, base, keyframes };
    }""")
    assert seen["hint"], "没找到 .is-drop-hint 规则，这条会空过"
    assert seen["hint"]["name"] == "pack-drop-hint-breathe", seen["hint"]
    assert seen["hint"]["border"] == "", \
        f"呼吸结束后静态 border-color 会让虚线留在原地: {seen['hint']}"
    assert seen["base"] and alpha_of_seen(seen["base"]) == 0, f"默认态就该透明: {seen['base']}"
    assert seen["keyframes"]["lastSelector"] in ("100%", "0%, 100%"), seen["keyframes"]
    assert alpha_of_seen(seen["keyframes"]["lastColor"]) == 0, \
        f"动画的收尾键不是透明，播完会留下可见的边框: {seen['keyframes']}"
    final_border = page.evaluate("""() => {
        const rows = document.querySelector('#pack-basic-rows');
        document.body.append(rows);
        rows.hidden = false;
        rows.style.display = 'grid';
        rows.classList.add('is-drop-hint');
        const animations = rows.getAnimations();
        if (!animations.length) throw new Error('Drop hint animation did not start');
        animations.forEach(animation => animation.finish());
        return getComputedStyle(rows).borderTopColor;
    }""")
    assert alpha_of_seen(final_border) == 0, final_border


def alpha_of_seen(value: str) -> float:
    parts = [p for p in __import__("re").findall(r"[\d.]+", str(value or ""))]
    if not parts:
        return -1
    return float(parts[3]) if len(parts) >= 4 else 1.0

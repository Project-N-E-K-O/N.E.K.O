"""Test export groups and the single animation list below the preview.

Assertions inspect rendered styles, visible choices and reachable controls.
Checking hidden attributes alone would miss CSS rules that override them.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from playwright.sync_api import Page, expect

SESSION_KEY = "neko-emote-maker-last-template"


def _template(display_name: str, slot: str, scenes: int = 2) -> bytes:
    from tests.emote_fixtures import spine_zip
    return spine_zip(model=slot, animations=scenes)


def _open_export_tab(page: Page) -> None:
    page.evaluate("() => document.querySelector('#inspector-tab-export').click()")


def _import(page: Page, file_name: str, display_name: str, slot: str, scenes: int = 2) -> None:
    page.set_input_files("#template-archive-file", {
        "name": file_name,
        "mimeType": "application/zip",
        "buffer": _template(display_name, slot, scenes),
    })
    page.wait_for_function("() => document.body.classList.contains('editor-has-template')",
                           timeout=25000)
    page.wait_for_function("name => JSON.parse(localStorage.getItem('neko-emote-maker-last-template') || '{}').packageName === name",
                           arg=file_name)
    _open_export_tab(page)
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('#group-section')).display !== 'none'",
        timeout=15000)


@pytest.fixture()
def group_page(page: Page, running_server: str) -> Page:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_function("() => Boolean(document.querySelector('#group-section'))", timeout=15000)
    _import(page, "first.zip", "第一个", "A", scenes=3)
    return page


def _shown(page: Page, selector: str) -> bool:
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            return Boolean(el) && getComputedStyle(el).display !== 'none';
        }""", selector)


def _options(page: Page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#batch-export-group-select option')]
            .map((o) => o.textContent.trim())""")


def test_dock_is_one_list_now_that_group_moved(page: Page, running_server: str) -> None:
    """Show one animation list instead of duplicate lists with identical names."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    _import(page, "first.zip", "第一个", "A", scenes=3)
    assert _shown(page, '.list-segment') is False, "场景/动画 两栏开关还在"
    assert _shown(page, '#anim-seg-scenes') is False
    assert page.evaluate("() => document.querySelector('#anim-dock-title').textContent.trim()") == "动作"
    # 组那一块不该在预览下方
    assert page.evaluate(
        """() => {
            const dock = document.querySelector('.animation-dock');
            return Boolean(dock.querySelector('.group-bar') || dock.querySelector('#new-group-btn'));
        }""") is False, "组的操作还留在预览下方"


def test_group_section_lives_in_export_settings(group_page: Page) -> None:
    section = group_page.locator("#group-section")
    expect(section).to_be_visible()
    expect(section).to_have_js_property("open", False)
    create = group_page.locator("#new-group-btn")
    expect(create).to_be_hidden()
    section.locator("summary").click()
    expect(create).to_be_visible()
    options = _options(group_page)
    # 下拉里没有"全部场景"这一项了：它不是组，只是以前的过滤器默认值
    assert "全部场景" not in options, options
    assert any("动作" in option for option in options), \
        f"导入模板的动作没有生成只读组: {options}"
    # 没选组时自动选上第一组：空值的下拉会渲染成一片空白
    selected = group_page.evaluate(
        "() => document.querySelector('#batch-export-group-select').value")
    assert selected.startswith("list:"), f"首组没有自动选中: {selected!r}"
    state = group_page.evaluate("""() => {
        const shown = (sel) => {
            const el = document.querySelector(sel);
            return Boolean(el) && getComputedStyle(el).display !== 'none';
        };
        return {
            // 导出入口已经搬到底部那排按钮里，组这一栏里不该再有一颗
            oldExport: Boolean(document.querySelector('#export-group-btn')),
            current: shown('#export-png-btn'),
            batch: shown('#export-group-open-btn'),
        };
    }""")
    # 还没选组时两个写动作照样给：按下去会顺手建一个组，比"先别按"少一步
    assert state["oldExport"] is False, "「导出本组」还留在批量导出里"
    assert state["current"] and state["batch"], "底部两颗导出按钮没并排出现"
    # 加号（顶替「加入本组」的那颗）只住在空组提示里，此刻不该有
    assert group_page.evaluate(
        "() => Boolean(document.querySelector('#add-scene-to-group-btn'))") is False

def test_current_framing_line_is_gone(group_page: Page) -> None:
    """Omit a current-frame label that repeats the selected animation name."""
    assert group_page.evaluate("() => Boolean(document.querySelector('#group-current'))") is False
    assert "当前画面" not in group_page.evaluate(
        "() => document.querySelector('#group-section').textContent")


def test_export_entry_is_two_buttons_on_one_row(group_page: Page) -> None:
    """Place current and batch export buttons side by side with matching dimensions."""
    box = group_page.evaluate("""() => {
        const a = document.querySelector('#export-png-btn').getBoundingClientRect();
        const b = document.querySelector('#export-group-open-btn').getBoundingClientRect();
        return { ay: Math.round(a.y), by: Math.round(b.y), aw: Math.round(a.width), bw: Math.round(b.width),
                 ah: Math.round(a.height), bh: Math.round(b.height),
                 label: document.querySelector('#export-png-btn span').textContent.trim(),
                 key: document.querySelector('#export-png-btn span').dataset.i18n,
                 batch: document.querySelector('#export-group-open-btn').textContent.trim() };
    }""")
    assert box["ay"] == box["by"], f"两颗按钮没在同一行: {box}"
    assert abs(box["aw"] - box["bw"]) <= 2, f"宽度不相当: {box}"
    assert abs(box["ah"] - box["bh"]) <= 2, f"高度不齐: {box}"
    assert box["batch"] == "批量导出", box
    # 导哪一张由这两颗说，出什么格式由上面的「格式」下拉说 —— 按钮上不再印 PNG/GIF。
    # 这份合成模板起不来渲染器，所以此刻按钮说的是"为什么不能导"，不是格式。
    assert box["key"] == "emoteMaker.exportCurrent", box
    assert box["label"] in ("导出当前", "预览暂不可用"), box
    assert "GIF" not in box["label"] and "PNG" not in box["label"], box


def test_batch_button_opens_a_group_picker_above_it(group_page: Page) -> None:
    """Open a group popover above batch export and dismiss it on outside clicks."""
    group_page.evaluate("() => document.querySelector('#export-group-open-btn').click()")
    opened = group_page.evaluate("""() => {
        const picker = document.querySelector('#group-export-picker');
        const trigger = document.querySelector('#export-group-open-btn').getBoundingClientRect();
        const box = picker.getBoundingClientRect();
        return {
            hidden: picker.hidden, display: getComputedStyle(picker).display,
            expanded: document.querySelector('#export-group-open-btn').getAttribute('aria-expanded'),
            above: box.bottom <= trigger.top + 1,
            rows: [...picker.querySelectorAll('.group-picker-item')].map((r) => r.textContent),
            title: picker.querySelector('.group-picker-title').textContent.trim(),
        };
    }""")
    assert opened["display"] != "none" and opened["hidden"] is False, f"气泡没开: {opened}"
    assert opened["expanded"] == "true", opened
    assert opened["above"] is True, f"气泡没长在按钮上方: {opened}"
    assert opened["title"] == "选择要导出的组", opened
    assert len(opened["rows"]) >= 1, f"气泡里没有组: {opened}"
    assert any("动作" in row for row in opened["rows"]), opened
    assert any("张" in row for row in opened["rows"]), f"每一行要说清几张: {opened}"

    group_page.mouse.click(200, 300)
    group_page.wait_for_function(
        "() => document.querySelector('#group-export-picker').hidden", timeout=10000)


def test_picker_rows_are_enabled_with_a_loaded_spine_renderer(group_page: Page) -> None:
    """Allow verified Spine models to be exported as groups."""
    expect(group_page.locator('#export-png-btn')).to_be_enabled()
    group_page.evaluate("() => document.querySelector('#export-group-open-btn').click()")
    state = group_page.evaluate("""() => {
        const picker = document.querySelector('#group-export-picker');
        const note = picker.querySelector('.group-picker-note');
        const shown = (el) => Boolean(el) && getComputedStyle(el).display !== 'none';
        return {
            allDisabled: [...picker.querySelectorAll('.group-picker-item')].every((r) => r.disabled),
            note: note ? note.textContent.trim() : '',
            noteShown: shown(note),
        };
    }""")
    assert state["allDisabled"] is False, state
    assert state["noteShown"] is False, state


@pytest.mark.parametrize('overrides', [True, False])
def test_create_group_from_current_keeps_the_setup(group_page: Page, overrides) -> None:
    """Create a group with the current frame, scale and rotation as its first entry."""
    # 先把缩放/旋转调开默认值，再看它们有没有被带走
    group_page.evaluate("""() => {
        const set = (key, value) => {
            const el = document.querySelector(`#adjust-${key}`);
            el.disabled = false;
            el.value = String(value);
            el.dispatchEvent(new Event('input', { bubbles: true }));
        };
        document.querySelector('#adjust-overwrite').checked = true;
        document.querySelector('#adjust-overwrite').dispatchEvent(new Event('change', { bubbles: true }));
        set('scale', 118);
        set('rotation', -12);
        set('x', 23);
        set('y', -9);
    }""")
    if not overrides:
        group_page.evaluate("""() => {
            const control = document.querySelector('#adjust-overwrite');
            control.checked = false;
            control.dispatchEvent(new Event('change', {bubbles:true}));
        }""")
    group_page.evaluate("() => document.querySelector('#new-group-btn').click()")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#group-members .group-member').length === 1",
        timeout=10000)
    row = group_page.evaluate("""() => {
        const el = document.querySelector('#group-members .group-member');
        return {
            kind: el.querySelector('.group-member-kind').textContent,
            removable: Boolean(el.querySelector('.group-member-remove')),
        };
    }""")
    assert row["kind"] == "取景", f"建组时没把当前画面存成第一条: {row}"
    assert row["removable"] is True

    stored = json.loads(group_page.evaluate("(k) => localStorage.getItem(k)", SESSION_KEY) or "{}")
    shot = (stored.get("shots") or [{}])[0]
    transform = (shot.get("look") or {}).get("transform") or {}
    expected = {'x':23, 'y':-9, 'rotation':-12, 'scale':1.18} if overrides else {
        'x':0, 'y':0, 'rotation':0, 'scale':1,
    }
    assert {key: transform[key] for key in expected} == expected
    own = next((item for item in (stored.get("groups") or []) if not str(item.get("id") or "").startswith("list:")), {})
    group_transform = ((own.get("look") or {}).get("transform") or {})
    assert {key: group_transform[key] for key in expected} == expected

    # 再加入当前场景：组里应该多一条"场景"成员。
    # （这条组已有一条取景，不是空组——加号只住在空组提示里，这里走右键菜单加。）
    group_page.evaluate("""() => {
        const card = document.querySelectorAll('#animation-list [data-animation-id]')[0];
        const rect = card.getBoundingClientRect();
        card.dispatchEvent(new MouseEvent('contextmenu', {
            bubbles: true, cancelable: true,
            clientX: Math.round(rect.left + 10), clientY: Math.round(rect.top + 10), button: 2,
        }));
    }""")
    group_page.evaluate("() => document.querySelector('#context-add-btn').click()")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#group-members .group-member').length === 2",
        timeout=10000)
    kinds = group_page.evaluate(
        "() => [...document.querySelectorAll('#group-members .group-member-kind')].map(e => e.textContent)")
    assert kinds == ["取景", "动作"], kinds

    # 移出第一条，剩下的那条还在（移的是行，不是整个组）
    group_page.evaluate("() => document.querySelector('.group-member-remove').click()")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#group-members .group-member').length === 1",
        timeout=10000)


def _open_group_menu(page: Page) -> None:
    page.evaluate("""() => {
        const menu = document.querySelector('#batch-export-group-select-menu');
        if (!menu || !menu.classList.contains('is-open')) {
            document.querySelector('#batch-export-group-select-trigger').click();
        }
    }""")
    page.wait_for_selector('#batch-export-group-select-menu.is-open', timeout=10000)


def _rename_group_in_menu(page: Page, current: str, name: str) -> None:
    _open_group_menu(page)
    page.evaluate(
        """(current) => {
            const row = [...document.querySelectorAll('#batch-export-group-select-menu .emote-select-option')]
                .find((item) => item.querySelector('.emote-select-option-label')?.textContent.trim() === current);
            row.querySelector('[data-action="rename"]').click();
        }""",
        current,
    )
    page.wait_for_selector('#batch-export-group-select-menu .emote-select-rename', timeout=10000)
    page.evaluate(
        """(name) => {
            const input = document.querySelector('#batch-export-group-select-menu .emote-select-rename');
            input.value = name;
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        }""",
        name,
    )


def test_a_typed_name_becomes_the_new_group_name(group_page: Page) -> None:
    """Fill the row with the selector and offer rename/delete only for user-created groups."""
    stacked = group_page.evaluate("""() => {
        const row = document.querySelector('.group-picker-row').getBoundingClientRect();
        const trigger = document.querySelector('.group-picker-row .emote-select-trigger').getBoundingClientRect();
        const actions = document.querySelector('.group-action-row').getBoundingClientRect();
        return {
            rowWidth: row.width, triggerWidth: trigger.width,
            triggerTop: trigger.top, actionTop: actions.top,
            nameGone: !document.querySelector('#group-name'),
            deleteGone: !document.querySelector('#delete-group-btn'),
        };
    }""")
    assert stacked["nameGone"] is True
    assert stacked["deleteGone"] is True
    assert stacked["triggerWidth"] >= stacked["rowWidth"] * 0.9, stacked
    assert stacked["actionTop"] >= stacked["triggerTop"] + 20, stacked
    _open_group_menu(group_page)
    imported = group_page.evaluate("""() => {
        const rows = [...document.querySelectorAll('#batch-export-group-select-menu .emote-select-option')];
        return rows.map((row) => row.querySelectorAll('[data-action]').length);
    }""")
    assert imported and all(count == 0 for count in imported), imported
    group_page.evaluate("() => document.querySelector('#new-group-btn').click()")
    group_page.wait_for_function(
        """() => [...document.querySelectorAll('#batch-export-group-select option')]
            .some((option) => option.textContent.trim() === '未命名组')""",
        timeout=10000)
    _open_group_menu(group_page)
    icons = group_page.evaluate("""() => {
        const row = [...document.querySelectorAll('#batch-export-group-select-menu .emote-select-option')]
            .find((item) => item.querySelector('.emote-select-option-label')?.textContent.trim() === '未命名组');
        const rename = row.querySelector('[data-action="rename"]');
        const remove = row.querySelector('[data-action="delete"]');
        return {
            renameLabel: rename.getAttribute('aria-label'),
            deleteLabel: remove.getAttribute('aria-label'),
            renameText: rename.textContent.trim(),
            deleteText: remove.textContent.trim(),
            svgs: row.querySelectorAll('svg').length,
        };
    }""")
    assert icons["renameLabel"] == "改名", icons
    assert icons["deleteLabel"] == "删除", icons
    assert icons["renameText"] == "" and icons["deleteText"] == "", icons
    assert icons["svgs"] == 2, icons
    _rename_group_in_menu(group_page, "未命名组", "开心组")
    group_page.wait_for_function(
        """() => [...document.querySelectorAll('#batch-export-group-select option')]
            .some((option) => option.textContent.trim() === '开心组')""",
        timeout=10000)
    _rename_group_in_menu(group_page, "开心组", "改过的组")
    group_page.wait_for_function(
        """() => [...document.querySelectorAll('#batch-export-group-select option')]
            .some((option) => option.textContent.trim() === '改过的组')""",
        timeout=10000)
    assert "开心组" not in _options(group_page)


def test_group_actions_are_keyboard_accessible_and_escape_restores_row_focus(group_page: Page) -> None:
    group_page.locator('#group-section > summary').click()
    group_page.locator('#new-group-btn').click()
    _open_group_menu(group_page)
    row = group_page.locator('#batch-export-group-select-menu .emote-select-option.has-actions')
    expect(row).to_have_count(1)
    value = row.get_attribute('data-value')
    row.focus()
    group_page.keyboard.press('F2')
    expect(group_page.locator('.emote-select-rename')).to_be_focused()
    group_page.keyboard.press('Escape')
    expect(group_page.locator(f'#batch-export-group-select-menu [data-value="{value}"]')).to_be_focused()
    group_page.keyboard.press('Delete')
    expect(group_page.locator('#emote-confirm')).to_be_visible()
    expect(group_page.locator('#emote-confirm-ok')).to_be_focused()
    group_page.keyboard.press('Tab')
    expect(group_page.locator('#emote-confirm-cancel')).to_be_focused()
    group_page.keyboard.press('Shift+Tab')
    expect(group_page.locator('#emote-confirm-ok')).to_be_focused()
    group_page.locator('#emote-confirm-cancel').click()
    expect(group_page.locator('#batch-export-group-select-trigger')).to_be_focused()


def test_a_created_group_can_be_deleted(group_page: Page) -> None:
    """Delete custom groups and their exclusive views only after confirmation; retain imported rows."""
    group_page.evaluate("() => document.querySelector('#new-group-btn').click()")
    group_page.wait_for_function(
        """() => [...document.querySelectorAll('#batch-export-group-select option')]
            .some((option) => option.textContent.trim() === '未命名组')""",
        timeout=10000)

    def delete_from_menu() -> None:
        _open_group_menu(group_page)
        group_page.evaluate("""() => {
            const row = [...document.querySelectorAll('#batch-export-group-select-menu .emote-select-option')]
                .find((item) => item.querySelector('.emote-select-option-label')?.textContent.trim() === '未命名组');
            row.querySelector('[data-action="delete"]').click();
        }""")

    delete_from_menu()
    group_page.wait_for_selector("#emote-confirm:not([hidden])", timeout=10000)
    assert "未命名组" in group_page.evaluate(
        "() => document.querySelector('#emote-confirm-text').textContent")
    group_page.evaluate("() => document.querySelector('#emote-confirm-cancel').click()")
    assert "未命名组" in _options(group_page)
    delete_from_menu()
    group_page.wait_for_selector("#emote-confirm:not([hidden])", timeout=10000)
    group_page.evaluate("() => document.querySelector('#emote-confirm-ok').click()")
    group_page.wait_for_function(
        """() => ![...document.querySelectorAll('#batch-export-group-select option')]
            .some((option) => option.textContent.trim() === '未命名组')""",
        timeout=10000)
    stored = json.loads(group_page.evaluate("(k) => localStorage.getItem(k)", SESSION_KEY) or "{}")
    assert not [group for group in (stored.get("groups") or []) if group.get("name") == "未命名组"]
    assert stored.get("shots") == [] or stored.get("shots") is None
    _open_group_menu(group_page)
    leftover = group_page.evaluate(
        "() => document.querySelectorAll('#batch-export-group-select-menu [data-action=\"delete\"]').length")
    assert leftover == 0


def test_group_shot_does_not_overwrite_the_editor_look(group_page: Page) -> None:
    """Keep saved group rotation separate from the editor slider used by the animation list."""
    group_page.evaluate("""() => {
        const input = document.querySelector('#adjust-rotation');
        input.value = '35';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        document.querySelector('#new-group-btn').click();
    }""")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#group-members .group-member').length === 1",
        timeout=10000)
    stored = json.loads(group_page.evaluate("(k) => localStorage.getItem(k)", SESSION_KEY) or "{}")
    shot = (stored.get("shots") or [{}])[-1]
    assert (shot.get("look") or {}).get("transform", {}).get("rotation") == 35, shot
    group_page.evaluate("""() => {
        const input = document.querySelector('#adjust-rotation');
        input.value = '0';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        document.querySelector('#group-members .group-member-pick').click();
        const card = document.querySelector('#animation-list .animation-card:not([disabled])');
        if (card) card.click();
    }""")
    assert group_page.evaluate(
        "() => document.querySelector('#adjust-rotation-value').textContent") == "0°"


def test_group_panel_points_at_the_action_list_context_menu(group_page: Page) -> None:
    """Add animations through the context menu and explain that action in the group panel."""
    hint = group_page.evaluate("""() => {
        const note = document.querySelector('#group-section .panel-note');
        return {
            gone: document.querySelector('#save-shot-btn') === null,
            text: note ? note.textContent.trim() : '',
            shown: Boolean(note) && getComputedStyle(note).display !== 'none',
        };
    }""")
    assert hint["gone"] is True
    assert hint["shown"] is True
    assert "右键中间的动作列表" in hint["text"], hint["text"]


def test_export_tips_show_once_per_page(group_page: Page) -> None:
    page = group_page
    section = page.locator("#group-section")
    summary = section.locator("summary")
    panel = page.locator("#inspector-panel-export")
    tip_host = page.locator("#emote-page-tips")
    tips = tip_host.locator(".emote-timed-tip")
    batch_tip = tips.filter(has_text="右键动作板块中的内容可快速多选和添加/移除当前组")
    pack_tip = tips.filter(has_text="长按动作板块中内容可拖动到对应位置快速应用")

    expect(tips).to_have_count(0)
    expect(page.locator("#pack-panel-basic")).not_to_contain_text("长按中间的动作")
    expect(section).to_have_js_property("open", False)
    summary.click()
    expect(batch_tip).to_be_visible()
    title_bounds = page.locator(".page-title-bar").bounding_box()
    tip_bounds = tip_host.bounding_box()
    assert tip_bounds["x"] == 12
    assert tip_bounds["y"] == title_bounds["y"] + title_bounds["height"] + 12
    assert batch_tip.evaluate("el => el.getAnimations()[0].effect.getTiming().duration") == 10000
    batch_tip.locator(".emote-timed-tip-close").click()
    expect(batch_tip).to_have_count(0)
    summary.click()
    summary.click()
    expect(section).to_have_js_property("open", True)
    expect(batch_tip).to_have_count(0)

    panel.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    expect(pack_tip).to_be_visible()
    pack_tip.evaluate("el => el.getAnimations().forEach(animation => animation.finish())")
    expect(pack_tip).to_have_count(0)
    panel.evaluate("el => { el.scrollTop = 0; }")
    page.locator("#inspector-tab-parts").click()
    _open_export_tab(page)
    panel.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    expect(pack_tip).to_have_count(0)

    page.reload(wait_until="networkidle")
    _import(page, "first.zip", "第一个", "A", scenes=3)
    expect(section).to_have_js_property("open", True)
    expect(batch_tip).to_have_count(0)
    summary.click()
    summary.click()
    expect(batch_tip).to_be_visible()
    panel.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    expect(pack_tip).to_be_visible()


def test_groups_are_scoped_to_the_template(group_page: Page) -> None:
    """Clear the previous template group and multiple selection when switching templates."""
    group_page.evaluate("() => document.querySelector('#new-group-btn').click()")
    group_page.wait_for_function(
        """() => {
            const stored = JSON.parse(localStorage.getItem('neko-emote-maker-last-template') || '{}');
            return (stored.groups || []).length === 1;
        }""", timeout=10000)
    group_page.evaluate("() => document.querySelector('#select-all-btn').click()")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#animation-list .animation-card.is-picked').length > 0",
        timeout=10000)
    _import(group_page, "second.zip", "第二个", "B", scenes=2)
    after = json.loads(group_page.evaluate("(k) => localStorage.getItem(k)", SESSION_KEY) or "{}")
    assert not after.get("groups"), f"新模板继承了旧模板的组: {after.get('groups')}"
    assert not any("未命名组" in option for option in _options(group_page))
    picks = group_page.evaluate("""() => ({
        picked: document.querySelectorAll('#animation-list .animation-card.is-picked').length,
        count: document.querySelector('#list-select-count').textContent,
    })""")
    assert picks["picked"] == 0 and picks["count"] == "", \
        f"旧模板的多选还留在新模板上: {picks}"


def test_group_controls_are_not_flush_against_the_clip_edge(group_page: Page) -> None:
    """Measure actual left spacing so the scroll container cannot clip the selector edge."""
    group_page.locator("#group-section summary").press("Enter")
    box = group_page.evaluate("""() => {
        const select = document.querySelector('#batch-export-group-select-trigger');
        const panel = document.querySelector('#inspector-panel-export');
        const s = select.getBoundingClientRect();
        const p = panel.getBoundingClientRect();
        select.focus();
        return {
            slack: +(s.left - p.left).toFixed(2),
            outlineOffset: getComputedStyle(select).outlineOffset,
            overflowX: panel.scrollWidth > panel.clientWidth,
            sizes: [panel.scrollWidth, panel.clientWidth],
            overflowChildren: [...panel.querySelectorAll('*')].filter(n => n.getBoundingClientRect().right > p.right + 1).map(n => n.id || n.className).slice(0, 8),
        };
    }""")
    assert box["slack"] >= 3, f"组下拉还贴着面板的裁剪边，左边那一条会被削掉: {box}"
    assert box["overflowChildren"] == [], f"组控件越过了面板的裁剪边: {box}"
    # 内侧环（负 offset）才不会被 overflow:auto 切掉
    assert box["outlineOffset"].startswith('-'), f"焦点环画在盒子外面，会被面板裁掉: {box}"


def test_help_bubble_anchors_above_the_control(group_page: Page) -> None:
    """Show an immediate anchored help bubble above its control rather than a native title tooltip."""
    group_page.locator("#group-section summary").click()
    anchor = group_page.evaluate("""() => {
        const el = document.querySelector('#new-group-help');
        const r = el.getBoundingClientRect();
        return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
                 tip: el.dataset.tip || '' };
    }""")
    assert "换装" in anchor["tip"], f"说明没挂到 ? 上: {anchor}"
    group_page.mouse.move(20, 20)
    group_page.mouse.move(anchor["x"], anchor["y"])
    shown = group_page.evaluate("""() => {
        const tip = document.querySelector('#emote-cursor-tip');
        const box = tip.getBoundingClientRect();
        return {
            display: getComputedStyle(tip).display, text: tip.textContent,
            top: box.top, left: box.left,
            viewH: document.documentElement.clientHeight,
            described: document.querySelector('#new-group-help').getAttribute('aria-describedby'),
        };
    }""")
    assert shown["display"] != "none" and shown["text"], f"悬停没出说明: {shown}"
    assert shown["top"] < shown["viewH"] - 120, f"说明跑到页面底部去了: {shown}"
    assert abs(shown["top"] - anchor["y"]) < 120, f"说明离控件太远: {shown}"
    # 钉在控件上方，不跟鼠标：气泡的位置只由控件决定
    group_page.mouse.move(anchor["x"] + 3, anchor["y"] + 2)
    again = group_page.evaluate("""() => {
        const box = document.querySelector('#emote-cursor-tip').getBoundingClientRect();
        return { top: box.top, left: box.left };
    }""")
    assert abs(again["top"] - shown["top"]) < 1 and abs(again["left"] - shown["left"]) < 1,         f"气泡跟着鼠标动了: {shown} → {again}"
    assert shown["described"] == "emote-cursor-tip", "读屏拿不到这条说明"
    # 点击不该再产生第二条 toast（眼睛在问号上，字却出现在脚边）
    group_page.evaluate("() => document.querySelector('#new-group-help').click()")
    group_page.wait_for_timeout(250)
    assert "换装" not in group_page.evaluate(
        "() => document.querySelector('#emote-toast').textContent"), \
        "点一下又把说明甩到页面底部了"


def test_collapse_caret_is_a_graphic_that_turns_in_place(group_page: Page) -> None:
    """Rotate a centered graphical chevron without the wobble caused by glyph metrics."""
    caret_style = """() => {
        const cs = getComputedStyle(document.querySelector('.group-summary'), '::before');
        return { content: cs.content, mask: cs.maskImage || cs.webkitMaskImage,
                 origin: cs.transformOrigin, w: cs.width, h: cs.height, transform: cs.transform };
    }"""
    # 收起/展开都在页面里等过渡落地再量。固定等 260ms 时，主线程一忙
    # 就会读到出发角度，收起态被误判成还转着 90°。
    def _settle(open_state: bool, ready: str) -> None:
        group_page.evaluate(
            """([openState, ready]) => new Promise((resolve, reject) => {
                const section = document.querySelector('#group-section');
                section.open = openState;
                const started = performance.now();
                const tick = () => {
                    const transform = getComputedStyle(
                        document.querySelector('.group-summary'), '::before').transform;
                    const identity = transform === 'none' || transform === 'matrix(1, 0, 0, 1, 0, 0)';
                    const ok = ready === 'closed' ? identity : !identity && transform.startsWith('matrix(');
                    if (ok) { resolve(transform); return; }
                    if (performance.now() - started > 2000) { reject(new Error(transform)); return; }
                    setTimeout(tick, 40);
                };
                tick();
            })""",
            [open_state, ready],
        )

    _settle(False, "closed")
    closed = group_page.evaluate(caret_style)
    _settle(True, "open")
    opened = group_page.evaluate(caret_style)

    caret = closed
    assert "url(" in caret["mask"], f"箭头不是图形，还是拿字符顶的: {caret}"
    assert caret["content"] in ('""', 'none'), f"字符还留在 content 里: {caret}"
    assert caret["w"] == caret["h"], f"包围盒不是正方形，绕中心转就会歪: {caret}"
    # Chromium 会把 center 解析成 px：钉的是"原点落在盒子正中"，不是写法
    origin = [float(v[:-2]) for v in caret["origin"].split()[:2]]
    assert origin == [float(caret["w"][:-2]) / 2, float(caret["h"][:-2]) / 2], \
        f"旋转原点不在箭头正中: {caret}"
    assert caret["transform"] == "none", caret
    matrix = opened["transform"]
    assert matrix.startswith("matrix("), f"展开没转过来: {matrix}"
    parts = [float(x) for x in matrix[matrix.index("(") + 1:matrix.index(")")].split(",")]
    assert abs(parts[4]) < 0.01 and abs(parts[5]) < 0.01, f"旋转时发生了位移（看起来在晃）: {matrix}"


def test_create_group_button_does_not_look_like_a_link(group_page: Page) -> None:
    """Give the create-group action button dimensions instead of link-only styling."""
    group_page.locator("#group-section summary").click()
    style = group_page.evaluate("""() => {
        const el = document.querySelector('#new-group-btn');
        const cs = getComputedStyle(el);
        return {
            borderWidth: cs.borderTopWidth, borderStyle: cs.borderTopStyle, dpr: window.devicePixelRatio,
            background: cs.backgroundColor, radius: cs.borderTopLeftRadius,
            height: Math.round(el.getBoundingClientRect().height),
        };
    }""")
    assert float(style["borderWidth"][:-2]) > 0 and style["borderStyle"] == "solid", style
    assert style["background"] not in ("rgba(0, 0, 0, 0)", "transparent"), \
        f"还是透明底，看着像链接: {style}"
    assert style["height"] >= 26, f"没有可点的体积: {style}"


def test_select_tools_share_the_heading_and_only_show_when_used(group_page: Page) -> None:
    """Keep select-all actions from permanently consuming an animation-list row."""
    place = group_page.evaluate("""() => {
        const tools = document.querySelector('#list-select-tools');
        const heading = document.querySelector('.quick-preview-heading');
        const hb = heading.getBoundingClientRect();
        const tb = tools.getBoundingClientRect();
        const list = document.querySelector('#animation-list').getBoundingClientRect();
        return {
            inHeading: heading.contains(tools),
            insideRow: tb.top >= hb.top - 1 && tb.bottom <= hb.bottom + 1,
            listBelowTools: list.top >= hb.bottom - 1,
            noneHidden: document.querySelector('#select-none-btn').hidden,
            toolsHidden: tools.hidden,
        };
    }""")
    assert place["inHeading"] and place["insideRow"], f"多选工具还自己占着一行: {place}"
    assert place["listBelowTools"], f"多选工具混进了列表区: {place}"
    assert place["toolsHidden"] is False, "有模板时不该整排藏掉"
    assert place["noneHidden"] is True, "一条都没选，就先别挂「取消全选」"

    group_page.evaluate("() => document.querySelector('#select-all-btn').click()")
    picked = group_page.evaluate("""() => {
        const tools = document.querySelector('#list-select-tools');
        return { visible: getComputedStyle(tools).display !== 'none',
                 noneHidden: document.querySelector('#select-none-btn').hidden,
                 count: document.querySelector('#list-select-count').textContent };
    }""")
    assert picked["visible"] is True and picked["noneHidden"] is False, picked
    assert "已选" in picked["count"], picked

    group_page.evaluate("() => document.querySelector('#select-none-btn').click()")
    group_page.wait_for_function(
        "() => document.querySelector('#select-none-btn').hidden", timeout=10000)
    assert group_page.evaluate(
        "() => document.querySelector('#list-select-count').textContent") == ""



def test_group_section_collapses_and_reopens(group_page: Page) -> None:
    """Hide collapsed content while retaining the active group and item count in its summary."""
    group_page.evaluate("() => document.querySelector('#new-group-btn').click()")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#group-members .group-member').length === 1",
        timeout=10000)
    summary = group_page.evaluate(
        "() => document.querySelector('#group-summary-state').textContent")
    assert "未命名组" in summary and "1" in summary, summary

    group_page.locator("#group-section summary").click()
    state = group_page.evaluate("""() => {
        const details = document.querySelector('#group-section');
        const openH = Math.round(details.getBoundingClientRect().height);
        details.open = false;
        const closedH = Math.round(details.getBoundingClientRect().height);
        details.open = true;
        return { openH, closedH };
    }""")
    # 看高度而不是子元素的 display：details 收起时后代的 computed display 并不会变成 none
    assert state["closedH"] < state["openH"] - 40, f"收起没让出位置: {state}"
    # 折叠状态要活到下次打开页面
    group_page.evaluate("""() => {
        const details = document.querySelector('#group-section');
        details.open = false;
        details.dispatchEvent(new Event('toggle'));
    }""")
    group_page.wait_for_timeout(200)
    saved = group_page.evaluate(
        "() => JSON.parse(localStorage.getItem('neko-emote-maker-layout') || '{}').groupPanelOpen")
    assert saved is False, f"收起状态没存下来: {saved}"


def _open_context_menu(page: Page, index: int) -> None:
    """Dispatch contextmenu directly for a disabled card without a Spine runtime.

        A real mouse context click on a renderable template still requires manual
        verification because browsers do not dispatch it to disabled controls.
        """
    page.evaluate(
        """(i) => {
            const card = document.querySelectorAll('#animation-list [data-animation-id]')[i];
            const rect = card.getBoundingClientRect();
            card.dispatchEvent(new MouseEvent('contextmenu', {
                bubbles: true, cancelable: true,
                clientX: Math.round(rect.left + 10), clientY: Math.round(rect.top + 10),
                button: 2,
            }));
        }""", index)




def test_gif_format_hides_1024_and_the_removed_notes_stay_gone(group_page: Page) -> None:
    """Hide the 1024 option for GIF export and keep retired hints absent."""
    group_page.evaluate("""() => {
        const format = document.querySelector('#export-format');
        format.value = 'gif';
        format.dispatchEvent(new Event('change', { bubbles: true }));
    }""")
    state = group_page.evaluate("""() => ({
        hidden1024: document.querySelector('#export-resolution-1024').hidden,
        warnGone: !document.querySelector('#export-gif-size-warn'),
        groupHintGone: !document.body.innerText.includes('要导出的那几张'),
        usageGone: !document.querySelector('.template-lib-usage'),
        mouthOffGone: !document.body.innerText.includes('不做口型交替'),
    })""")
    assert state["hidden1024"] is True, state
    assert state["warnGone"] and state["groupHintGone"] and state["usageGone"]         and state["mouthOffGone"], state


def test_export_options_keep_other_editor_controls_and_save_the_gif_limit(group_page: Page) -> None:
    result = group_page.evaluate("""() => {
        const card = document.querySelector('.template-lib-card');
        const stateRow = document.querySelector('#pack-basic-rows .pack-basic-row');
        const change = (id, value) => {
            const input = document.getElementById(id);
            input.value = value;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        };
        change('export-format', 'png');
        change('export-resolution', '1024');
        change('export-format', 'gif');
        change('export-fps', '30');
        const saved = JSON.parse(localStorage.getItem('neko-emote-maker-export'));
        return {
            sameCard: Boolean(card?.isConnected),
            sameStateRow: Boolean(stateRow?.isConnected),
            resolution: document.getElementById('export-resolution').value,
            savedResolution: saved.exportResolution,
            savedFps: saved.exportFps,
        };
    }""")
    assert result == {"sameCard": True, "sameStateRow": True, "resolution": "512", "savedResolution": 512, "savedFps": 30}


def test_right_click_menu_adds_and_removes_selected_actions(group_page: Page) -> None:
    """Toggle context-menu wording when an animation joins or leaves the current export group."""
    group_page.evaluate("() => document.querySelector('#new-group-btn').click()")
    group_page.wait_for_function(
        "() => document.querySelectorAll('#group-members .group-member').length === 1",
        timeout=10000)
    before = group_page.evaluate(
        "() => document.querySelectorAll('#group-members .group-member').length")

    _open_context_menu(group_page, 0)
    menu = group_page.evaluate("""() => {
        const m = document.querySelector('#group-context-menu');
        const shown = (sel) => {
            const el = document.querySelector(sel);
            return !el.hidden && getComputedStyle(el).display !== 'none';
        };
        return { open: !m.hidden, add: shown('#context-add-btn'), remove: shown('#context-remove-btn'),
                 groupLabel: document.querySelector('#context-group-name').textContent };
    }""")
    assert menu["open"] is True, "右键没弹出菜单"
    assert menu["add"] is True and menu["remove"] is False, f"不在组里时该只给「加入」: {menu}"
    assert "未命名组" in menu["groupLabel"], menu
    assert group_page.evaluate(
        "() => document.querySelectorAll('#animation-list .animation-card.is-picked').length") == 1

    # 再右键同一条：取消选择，菜单也收起来
    _open_context_menu(group_page, 0)
    toggled = group_page.evaluate("""() => ({
        picked: document.querySelectorAll('#animation-list .animation-card.is-picked').length,
        menuHidden: document.querySelector('#group-context-menu').hidden,
    })""")
    assert toggled["picked"] == 0 and toggled["menuHidden"] is True, f"再右键没有取消选择: {toggled}"

    _open_context_menu(group_page, 0)
    group_page.evaluate("() => document.querySelector('#context-add-btn').click()")
    group_page.wait_for_function(
        """(before) => document.querySelectorAll('#group-members .group-member').length > before""",
        arg=before, timeout=10000)
    kinds = group_page.evaluate(
        "() => [...document.querySelectorAll('#group-members .group-member-kind')].map(e => e.textContent)")
    expect(group_page.locator('#group-members .group-member')).to_have_count(before + 1)
    assert "动作" in kinds, f"右键加入的该是一个动作: {kinds}"

    # 全选只用来核对计数。要看「移出」得先清掉多选再右键：已选中时再右键是取消，不会开菜单。
    group_page.evaluate("() => document.querySelector('#select-all-btn').click()")
    counted = group_page.evaluate("""() => ({
        cards: document.querySelectorAll('#animation-list [data-animation-id]').length,
        picked: document.querySelectorAll('#animation-list .animation-card.is-picked').length,
        label: document.querySelector('#list-select-count').textContent,
    })""")
    assert counted["picked"] == counted["cards"] > 0, f"全选没把列表里的动作都选上: {counted}"
    assert str(counted["cards"]) in counted["label"], f"计数条没报出选了几条: {counted}"
    group_page.evaluate("() => document.querySelector('#select-none-btn').click()")
    _open_context_menu(group_page, 0)
    again = group_page.evaluate("""() => {
        const shown = (sel) => {
            const el = document.querySelector(sel);
            return !el.hidden && getComputedStyle(el).display !== 'none';
        };
        return { add: shown('#context-add-btn'), remove: shown('#context-remove-btn') };
    }""")
    assert again["remove"] is True, f"已在组里却还只给「加入」: {again}"
    group_page.evaluate("() => document.querySelector('#context-remove-btn').click()")
    after = group_page.evaluate(
        "() => document.querySelectorAll('#group-members .group-member').length")
    expect(group_page.locator('#group-members .group-member')).to_have_count(before)
    assert after == before, f"移出没生效: {before} → {after}"


def test_tip_help_buttons_sit_next_to_their_label_text(group_page: Page) -> None:
    """Keep help icons beside their labels instead of moving them to another grid row."""
    places = group_page.evaluate("""() => {
        const rows = ['#pack-name', '#pack-priority'].map((id) => {
            const control = document.querySelector(id);
            const label = control.closest('label');
            const wrap = label.querySelector('.select-row-label');
            const help = label.querySelector('.tip-help');
            // 量的是纯文字那层（data-i18n 的 span），wrapper 的右缘已经是问号右缘
            const text = wrap && wrap.querySelector('[data-i18n]');
            if (!text || !help) return { id, ok: false };
            const t = text.getBoundingClientRect();
            const b = help.getBoundingClientRect();
            // 同一行 + 紧跟在文字右侧（间距不超过一个字符位）
            return { id, ok: Math.abs(b.top - t.top) < 3 && (b.left - t.right) < 14
                     && (b.left - t.right) >= -2 };
        });
        return rows;
    }""")
    assert all(row["ok"] for row in places), f"问号没接在文字后面: {places}"

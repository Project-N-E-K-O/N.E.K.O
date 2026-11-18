"""The avatar-pack panel has exactly two surfaces: 基础 (two scene picks) and 进阶 (the node
editor overlay). The earlier card-tree is gone on purpose — it was both ugly and a second
source of truth, while export only ever reads a graph.

These tests pin the things most likely to rot: that basic mode compiles to a graph rather
than a parallel bake path, and that leaving the talking scene empty means no talking image
(which is what makes the runtime keep playing idle instead of flickering).
"""

from __future__ import annotations

from playwright.sync_api import Page

REVEAL = """() => {
    document.body.classList.remove('editor-empty');
    document.querySelector('.animation-dock').hidden = false;
    document.querySelector('.inspector-panel').hidden = false;
}"""

PANEL = """() => ({
    modes: [...document.querySelectorAll('[data-pack-mode]')].map(b => b.dataset.packMode),
    activeMode: [...document.querySelectorAll('[data-pack-mode]')]
        .filter(b => b.classList.contains('is-active')).map(b => b.dataset.packMode),
    basicVisible: !document.querySelector('#pack-panel-basic').hidden,
    graphVisible: !document.querySelector('#pack-panel-graph').hidden,
    rowIds: [...document.querySelectorAll('#pack-basic-rows select')].map(s => s.id),
    idleBlankLabel: document.querySelector('#pack-scene-idle')?.options[0]?.textContent || '',
    talkingBlankLabel: document.querySelector('#pack-scene-talking')?.options[0]?.textContent || '',
    summary: document.querySelector('#pack-basic-summary')?.textContent || '',
    summaryHidden: document.querySelector('#pack-basic-summary')?.hidden ?? true,
    editorButton: !!document.querySelector('#open-state-stage-btn'),
    // The deleted tree must stay deleted.
    leftoverCards: document.querySelectorAll('.pack-node').length,
    leftoverTreeHost: !!document.querySelector('#pack-states'),
})"""


def _open(page: Page, server: str) -> dict:
    page.goto(f"{server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(350)
    page.evaluate(REVEAL)
    page.wait_for_timeout(200)
    return page.evaluate(PANEL)


def test_panel_exposes_only_basic_and_advanced(page: Page, running_server: str) -> None:
    panel = _open(page, running_server)
    assert panel["modes"] == ["basic", "graph"], panel["modes"]
    assert panel["activeMode"] == ["basic"], "默认应当落在基础"
    assert panel["rowIds"] == [
        "pack-scene-idle", "pack-scene-talking", "pack-scene-drag", "pack-scene-click",
        "pack-scene-happy", "pack-scene-sad", "pack-scene-angry", "pack-scene-surprised",
    ], panel["rowIds"]
    assert panel["editorButton"], "进阶面板必须能打开状态表演台"


def test_the_old_card_tree_is_really_gone(page: Page, running_server: str) -> None:
    panel = _open(page, running_server)
    assert panel["leftoverCards"] == 0, "旧的树状卡片应当已删除"
    assert not panel["leftoverTreeHost"], "#pack-states 容器应当已移除"


def test_stage_open_button_is_flat(page: Page, running_server: str) -> None:
    """打开状态表演台按钮压平成单色：渐变+大投影在面板圆角里被裁出硬边
    （2026-10-19 用户抓的）。只看计算样式——这份 CSS 里同选择器有多个世代，
    只有最后一条算数。"""
    _open(page, running_server)
    style = page.evaluate("""() => {
        const btn = document.querySelector('#open-state-stage-btn');
        const cs = getComputedStyle(btn);
        return { image: cs.backgroundImage, shadow: cs.boxShadow };
    }""")
    assert style["image"] == "none", style
    assert style["shadow"] == "none", style


def test_input_focus_and_select_styling(page: Page, running_server: str) -> None:
    """输入框保持药丸焦点。下拉闭合控件是同款药丸，展开后是自绘菜单：
    点选项写回 select，隐藏项不进菜单，脚本改 value 时按钮文字跟着变。"""
    _open(page, running_server)
    page.evaluate("() => document.querySelector('#inspector-tab-export').click()")
    page.click("#pack-name")   # 真实交互聚焦；文本框任何聚焦方式都算 focus-visible
    page.wait_for_timeout(350)   # 等 0.2s 描边过渡落地再量
    state = page.evaluate("""() => {
        const name = document.querySelector('#pack-name');
        const cs = getComputedStyle(name);
        const trigger = document.querySelector('#export-format-trigger');
        const tcs = getComputedStyle(trigger);
        return { outlineStyle: cs.outlineStyle, shadow: cs.boxShadow,
            borderColor: cs.borderColor, radius: cs.borderRadius,
            selRadius: tcs.borderRadius, selBorder: tcs.borderTopColor,
            selWidth: tcs.borderTopWidth, caret: !!trigger.querySelector('.emote-select-caret') };
    }""")
    assert state["outlineStyle"] == "none", state       # 3px 蓝描边退役
    assert state["shadow"] == "none", state             # 药丸上不带柔光环
    assert state["borderColor"] == "rgb(32, 168, 239)", state  # accent（末代世代值）
    assert float(state["radius"].split("px")[0]) >= 20, state  # 药丸圆角
    assert float(state["selRadius"].split("px")[0]) >= 20, state
    assert state["selBorder"] == "rgb(179, 229, 252)", state  # 浅蓝药丸描边
    assert state["selWidth"] == "2px", state
    assert state["caret"], state

    page.click("#export-format-trigger")
    page.wait_for_selector("#export-format-menu.is-open")
    page.locator("#export-format-menu .emote-select-option", has_text="GIF").click()
    assert page.evaluate("() => document.querySelector('#export-format').value") == "gif"
    assert page.locator("#export-format-menu.is-open").count() == 0
    synced = page.evaluate("""() => {
        const select = document.querySelector('#export-format');
        select.value = 'png';
        const label = document.querySelector('#export-format-trigger .emote-select-value').textContent;
        const option = select.options[select.selectedIndex].textContent.trim();
        return label === option && select.value === 'png';
    }""")
    assert synced

    # 选完格式会 renderAll，空编辑器把检查器面板藏起来。菜单本身挂在 body 上，
    # 用脚本点开即可核对隐藏项，不必跟面板的空态抢可见性。
    page.evaluate("() => document.querySelector('#export-resolution-trigger').click()")
    page.wait_for_selector("#export-resolution-menu.is-open")
    labels = page.evaluate("""() => [...document.querySelectorAll('#export-resolution-menu .emote-select-option')].map(el => el.textContent)""")
    assert any("240" in text for text in labels), labels
    assert all("1024" not in text for text in labels), labels


def test_opening_batch_export_does_not_squeeze_the_panel(page: Page, running_server: str) -> None:
    """展开批量导出会把右侧面板撑出滚动条。滚动条若是临时冒出来的，
    内容宽度会少掉一截，格式药丸之类会一起变短。"""
    _open(page, running_server)
    page.evaluate("() => document.querySelector('#inspector-tab-export').click()")
    metrics = page.evaluate("""() => {
        const section = document.querySelector('#group-section');
        const panel = document.querySelector('#inspector-panel-export');
        const trigger = document.querySelector('#export-format-trigger');
        section.hidden = false;
        section.open = false;
        const before = trigger.getBoundingClientRect().width;
        section.open = true;
        const after = trigger.getBoundingClientRect().width;
        return {
            before, after,
            overflows: panel.scrollHeight > panel.clientHeight + 1,
            overflowX: panel.scrollWidth - panel.clientWidth,
            pageScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
    }""")
    assert metrics["overflows"], metrics
    assert abs(metrics["before"] - metrics["after"]) < 1, metrics
    assert metrics["overflowX"] <= 1, metrics
    assert metrics["pageScroll"] <= 1, metrics


def test_empty_talking_scene_stays_quiet_instead_of_lying(page: Page, running_server: str) -> None:
    """留空的说话场景不再解释"继续播待机"——那行说明已按要求退役，宁可不说也不含糊。
    用户能看出来的部分：摘要行收起（隐藏/空），别处也不许再出现那两句。"""
    panel = _open(page, running_server)
    # 留空的非必填槽位在运行时会回落，所以槽位标签仍要点明"跟随"
    assert "跟随" in panel["talkingBlankLabel"] or "回落" in panel["talkingBlankLabel"],         panel["talkingBlankLabel"]
    summary_hidden = panel.get("summaryHidden")
    if summary_hidden is None:
        # PANEL 没量 hidden 时退回看文本：空文本视为收起
        assert not panel["summary"], panel["summary"]
    else:
        assert summary_hidden or not panel["summary"], panel["summary"]
    page_text = page.evaluate("() => document.body.innerText")
    assert "不做口型交替" not in page_text and "继续播待机" not in page_text
def test_switching_to_advanced_reveals_the_graph_panel(page: Page, running_server: str) -> None:
    _open(page, running_server)
    page.evaluate("()=>document.querySelector('[data-pack-mode=\"graph\"]').click()")
    page.wait_for_timeout(250)
    panel = page.evaluate(PANEL)
    assert panel["activeMode"] == ["graph"], panel["activeMode"]
    # 没有导入模板时两个面板都应当隐藏：整个形象包区块在拿到 Spine 渲染器之前没有意义。
    assert not panel["basicVisible"], panel
    assert not panel["graphVisible"], panel
    # 选择应当被记住：这是视图偏好，存在 layout 键里
    assert "graph" in (page.evaluate(
        "localStorage.getItem('neko-emote-maker-layout')"
    ) or ""), "进阶/基础的选择应跨刷新保留"


def test_export_has_a_single_bake_path(page: Page, running_server: str) -> None:
    page.goto(f"{running_server}/emote_maker", wait_until="domcontentloaded")
    """结构保证：导出只认图。曾经存在过的 packAssignments / bakePngtuberStates /
    bakeNodeGraphStates 三条分支必须收敛成一条，否则基础和进阶会各自漂移。"""
    source = page.evaluate(
        """async () => (await fetch(new URL('/static/js/emote_maker.js', location.origin))).text()"""
    )
    assert "packAssignments" not in source, "旧的面板映射路径应当已删除"
    assert "bakePngtuberStatesFromGraph" not in source
    assert "bakeNodeGraphStates" not in source, "重复的第二条烘焙路径应当已合并"
    assert "bakeGraphStates(" not in source, "重复的第二条烘焙路径应当已合并"
    assert source.count("async function bakePackJobs(") == 1, "应当只剩一条采集路径"
    assert source.count("async function buildPngtuberPackage(") == 1
    assert "await bakePackJobs(jobs)" in source, "导出必须经由唯一的采集函数"
    assert "function basicGraph(" in source, "基础模式应编译成图"
    assert "currentBakeGraph()" in source, "两种编辑方式应汇流到同一张图"

def test_page_loads_every_emote_module_it_needs(page: Page, running_server: str) -> None:
    """回归防线：曾经有模块写好了、测试里靠 add_script_tag 注入才通过、但页面根本没加载它，
    于是真实页面上编辑器一点就报"未加载"。这里要求页面自身把依赖装齐。"""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    needed = {
        "NekoEmotePngtuberPack": "emote_pngtuber_pack.js",
        "NekoEmoteNodeGraph": "emote_node_graph.js",
        "NekoEmoteStateStage": "emote_state_stage.js",
        "NekoEmoteDragHold": "emote_drag_hold.js",
        "NekoEmotePngtuberLayered": "emote_pngtuber_layered.js",
        "NekoEmoteSheet": "emote_sheet.js",
        "NekoEmoteGifEncoder": "emote_gif_encoder.js",
    }
    loaded = page.evaluate(
        "(keys) => keys.map(k => Boolean(window[k]))", list(needed.keys()))
    missing = [name for name, ok in zip(needed.values(), loaded) if not ok]
    assert not missing, f"页面未加载这些脚本: {missing}"


def test_editor_opens_from_the_button_without_injection(page: Page, running_server: str) -> None:
    """上一条查的是脚本标签，这条查的是真点得动 —— 两者会各自失效。

    没有模板时"点开表演台"做不到，但那不是把按钮做死的理由：必须给出提示。
    """
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.evaluate(REVEAL)
    page.evaluate(
        """() => {
            document.querySelector('#pack-mode-graph')?.click();
            document.querySelector('#pack-panel-graph').hidden = false;
        }""")
    page.evaluate("() => document.querySelector('#open-state-stage-btn').click()")
    page.wait_for_timeout(500)
    answered = page.evaluate(
        """() => ({
            overlay: Boolean(document.querySelector('.st-overlay')),
            toast: (document.querySelector('.toast, [role=status]') || {}).textContent || '',
        })""")
    assert answered["overlay"] or answered["toast"].strip(), "点按钮之后既没打开也没解释，等于坏按钮"
    if not answered["overlay"]:
        assert "." not in answered["toast"], f"提示里出现了裸 key: {answered['toast']}"

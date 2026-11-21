"""Test the state stage as the single advanced export editor.

It selects animations, ranges and transforms while appearance remains in the
upstream tabs. Verify that range and position gestures reach the graph, talking
controls follow the package format, and untranslated keys stay off the page.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

REVEAL = """() => {
    document.body.classList.remove('editor-empty');
    document.querySelector('.animation-dock').hidden = false;
    document.querySelector('.inspector-panel').hidden = false;
}"""

# 在页内造帧：数据不走跨进程传输，同时 delayMs 带上真实采样率，
# 表演台的"秒↔帧"换算靠它反算，所以这里必须是可信的值。
BOOT = """(secondsPerFrame) => {
    const ZH = window.__STAGE_ZH || {};
    window.__SOURCE_CALLS = [];
    window.__DRAGS = [];
    window.__STAGE = window.NekoEmoteStateStage.create({
        model: window.NekoEmoteNodeGraph,
        graph: window.NekoEmoteNodeGraph.normalizeGraph({
            schemaVersion: 1,
            nodes: [
                { id: 'root', type: 'source.default', x: 0, y: 0, params: {} },
                { id: 'sc-idle', type: 'source.scene', x: 0, y: 120, params: { sceneId: 'anim-a' } },
                { id: 'out-idle', type: 'output.state', x: 0, y: 240,
                    params: { stateKey: 'idle_image', seconds: 0 } },
                { id: 'out-talk', type: 'output.state', x: 0, y: 360,
                    params: { stateKey: 'talking_image', seconds: 0 } },
            ],
            links: [
                { from: 'root', to: 'out-idle', fromSocket: 'look', toSocket: 'look' },
                { from: 'sc-idle', to: 'out-talk', fromSocket: 'look', toSocket: 'look' },
            ],
        }),
        t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
        fps: () => Math.round(1 / secondsPerFrame),
        format: () => (window.__FLAT ? 'flat' : 'layered'),
        animationsFor: () => [
            { value: 'anim-a', label: '走路' },
            { value: 'anim-b', label: '挥手' },
        ],
        thumbFor: (sceneId) => {
            window.__SOURCE_CALLS.push('thumb:' + sceneId);
            return '';
        },
        onPickerClose: () => { window.__SOURCE_CALLS.push('drop-thumbs'); },
        beginDrag: (payload) => { window.__DRAGS.push(payload); },
        framesFor: (source, quality) => {
            window.__SOURCE_CALLS.push(source + '|' + quality);
            const count = source === 'anim-b' ? 12
                : String(source).indexOf('talking') >= 0 ? 8 : 20;
            return Array.from({ length: count }, (unused, i) => {
                const canvas = document.createElement('canvas');
                canvas.width = 64;
                canvas.height = 64;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = String(source).indexOf('talking') >= 0 ? '#7a5cc0' : '#2f8ec8';
                ctx.fillRect(0, 0, 64, 64);
                ctx.fillStyle = '#ffffff';
                ctx.font = '22px sans-serif';
                ctx.fillText(String(i), 16, 40);
                const image = ctx.getImageData(0, 0, 64, 64);
                return { width: 64, height: 64, delayMs: secondsPerFrame * 1000,
                    data: Array.from(image.data) };
            });
        },
    });
    return true;
}"""

LOCALE = """async (path) => {
    const res = await fetch(path);
    const all = await res.json();
    window.__STAGE_ZH = all.emoteMaker;
    return true;
}"""

# 只在页内比较：文本值跨进程回传会被 Windows 控制台编码吃掉，
# 之前"标签是空串"的假故障就是这么来的。这里只回传布尔和长度。
TILES = """() => {
    const ZH = window.__STAGE_ZH || {};
    const cap = (key) => key.replace(/_image$/, '').replace(/^./, (c) => c.toUpperCase());
    return Array.from(document.querySelectorAll('.st-tile')).map((tile) => {
        const label = tile.querySelector('.st-tile-label').textContent.trim();
        const status = tile.querySelector('.st-tile-status').textContent.trim();
        const sub = tile.querySelector(".st-tile-sub").textContent.trim();
        return {
            state: tile.dataset.state,
            selected: tile.classList.contains('is-selected'),
            labelLen: label.length,
            labelRaw: label.indexOf('.') >= 0,
            labelOk: label === ZH['packState' + cap(tile.dataset.state)],
            followOk: sub === ZH.stageFollowTag,
            stillOk: status === ZH.stageStillTag,
            frameCount: (status.match(/^(\\d+)f$/) || [0, 0])[1] | 0,
        };
    });
}"""

TITLE_OK = """() => document.querySelector('.st-title').textContent.trim() === (window.__STAGE_ZH || {}).stageTitle"""

PANEL_TEXT = """() => ({
    title: document.querySelector('.st-title').textContent,
    span: document.querySelector('.st-span').textContent,
    reads: Array.from(document.querySelectorAll('.st-readout')).map((n) => n.textContent),
})"""


def _upstream_scene(page: Page, state_key: str):
    """Resolve the animation upstream of the requested state rather than counting global scenes."""
    return page.evaluate(
        """(key) => {
            const g = window.__STAGE.getGraph();
            const out = g.nodes.find((n) => n.params.stateKey === key);
            const link = g.links.find((l) => l.to === out.id);
            const from = g.nodes.find((n) => n.id === (link || {}).from);
            return { type: from ? from.type : null, sceneId: from ? from.params.sceneId : null };
        }""",
        state_key,
    )


def _open_library(page: Page) -> None:
    """Ensure the toggle is open instead of clicking an already open panel closed."""
    page.click(".st-anim")
    page.wait_for_timeout(250)
    if not page.evaluate("() => document.querySelector('.st-drawer').classList.contains('is-open')"):
        page.click(".st-anim")
        page.wait_for_timeout(250)


def _boot(page: Page, running_server: str, fps: int = 10, fmt: str = "layered", thumbnail: bool = False) -> None:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.evaluate(REVEAL)
    page.evaluate(LOCALE, "/static/locales/zh-CN.json")
    # goto 换掉了整个 document，所以标记必须在导航之后重新打
    page.evaluate("([flat]) => { window.__FLAT = flat; return 1; }", [fmt == "flat"])
    assert page.evaluate("() => typeof window.NekoEmoteStateStage === 'object'"), \
        "模板必须用和其他模块一致的 ?v= 形式载入 emote_state_stage.js"
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(getattr(exc, "stack", None) or str(exc)))
    script = BOOT.replace("return '';", "return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==';") if thumbnail else BOOT
    page.evaluate(script, 1.0 / fps)
    page.wait_for_timeout(400)
    # 覆盖层里的异常只会让界面静默变空，断言"没东西"永远查不出根因，
    # 所以在这里直接把 pageerror 拦下来。
    assert not errors, errors[:2]


def test_the_page_loads_the_stage_and_shows_eight_tiles(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    tiles = page.evaluate(TILES)
    assert [tile["state"] for tile in tiles] == [
        "idle_image", "talking_image", "drag_image", "click_image",
        "happy_image", "sad_image", "angry_image", "surprised_image",
    ], tiles
    # 没翻译的裸 key 直接印在脸上是最难发现的一类故障
    for tile in tiles:
        assert tile["labelLen"] > 0, tile
        assert not tile["labelRaw"], tile
        assert tile["labelOk"], tile
    assert page.evaluate(TITLE_OK), "标题没解析到本地化文案"


def test_unassigned_tiles_say_follow_default_and_assign_on_click(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    before = {tile["state"]: tile for tile in page.evaluate(TILES)}
    assert before["happy_image"]["followOk"], before["happy_image"]
    page.evaluate("() => document.querySelector('[data-state=\"happy_image\"]').click()")
    page.wait_for_timeout(250)
    after = {tile["state"]: tile for tile in page.evaluate(TILES)}
    assert after["happy_image"]["selected"], "点中的格子应当是唯一带蓝边的"
    assert not after["idle_image"]["selected"], "选中态不能同时有两格"
    graph = page.evaluate("() => window.__STAGE.getGraph()")
    keys = sorted(node["params"]["stateKey"] for node in graph["nodes"] if node["type"] == "output.state")
    assert keys == ["happy_image", "idle_image", "talking_image"], keys
    # 新格子必须接上根节点，否则求值链断掉、烘包时那一格会消失
    happy = [node for node in graph["nodes"] if node["params"].get("stateKey") == "happy_image"][0]
    assert any(link["to"] == happy["id"] for link in graph["links"]), graph["links"]


def test_dragging_the_strip_end_changes_the_seconds_stored(page: Page, running_server: str) -> None:
    """Persist range-handle seconds in the graph so UI selections agree with exported packages."""
    _boot(page, running_server, fps=10)
    box = page.locator(".st-strip-canvas").bounding_box()
    full = page.evaluate("() => window.__STAGE.getGraph().nodes.find(\
n => n.params.stateKey === 'idle_image').params.seconds")
    assert full == 0, "默认演完整段"
    # 从右侧往中间拖：终点手柄，20 帧 @10fps = 2s，拖到 60% 处约 1.2s
    page.mouse.move(box["x"] + box["width"] * 0.97, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.60, box["y"] + box["height"] / 2, steps=6)
    page.mouse.up()
    page.wait_for_timeout(250)
    params = page.evaluate("() => window.__STAGE.getGraph().nodes.find(\
n => n.params.stateKey === 'idle_image').params")
    assert 0.9 < params["seconds"] < 1.5, params
    assert params["startAt"] == 0, params
    span = page.evaluate(PANEL_TEXT)["span"]
    assert "1.20s" in span and "1.20s" in span.split("–")[-1], span


def test_the_start_handle_never_crosses_the_end(page: Page, running_server: str) -> None:
    _boot(page, running_server, fps=10)
    box = page.locator(".st-strip-canvas").bounding_box()
    # 先把范围收到 25%–50%
    for start, end in ((.98, .5), (.001, .25)):
        page.mouse.move(box["x"] + box["width"] * start, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * end, box["y"] + box["height"] / 2, steps=4)
        page.mouse.up()
    params = page.evaluate("() => window.__STAGE.getGraph().nodes.find(\
n => n.params.stateKey === 'idle_image').params")
    assert abs(params['startAt'] - .5) < .12, params
    assert abs(params['startAt'] + params['seconds'] - 1) < .12, params
    # 再把起点往最右拖：必须停在终点之前，不能拖出一个反向范围
    page.mouse.move(box["x"] + box["width"] * 0.25, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.95, box["y"] + box["height"] / 2, steps=6)
    page.mouse.up()
    page.wait_for_timeout(200)
    after = page.evaluate("() => window.__STAGE.getGraph().nodes.find(\
n => n.params.stateKey === 'idle_image').params")
    assert after['startAt'] > params['startAt'], after
    assert after['seconds'] >= .1 - 1e-6, after
    assert abs(after['startAt'] + after['seconds'] - params['startAt'] - params['seconds']) < .02, after
    assert page.evaluate("() => document.querySelector('.st-span').textContent").count("s") == 2


def test_dragging_the_figure_rotates_it(page: Page, running_server: str) -> None:
    """Rotate the frame directly instead of coupling horizontal rotation and vertical scaling."""
    _boot(page, running_server)
    box = page.locator(".st-stage").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    before = _pose(page)
    assert before == {"scalePercent": 100, "degrees": 0}, before
    # 抓住右边往上提：画面坐标 y 朝下，所以这在这个视觉上是逆时针＝负角度
    page.mouse.move(cx + 140, cy)
    page.mouse.down()
    page.mouse.move(cx + 100, cy - 100, steps=10)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = _pose(page)
    assert after["degrees"] < -25, after
    # 反过来往下按应该是正角度：方向不能是反的
    page.mouse.move(cx + 140, cy)
    page.mouse.down()
    page.mouse.move(cx + 100, cy + 100, steps=10)
    page.mouse.up()
    page.wait_for_timeout(250)
    assert _pose(page)["degrees"] > after["degrees"], _pose(page)
    assert after["scalePercent"] == 100, "旋转不该顺带改大小"
    reads = page.evaluate(PANEL_TEXT)["reads"]
    assert any("%" in text for text in reads) and any("°" in text for text in reads), reads


def test_the_wheel_scales_and_the_keyboard_snaps_back(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    box = page.locator(".st-stage").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.wheel(0, -300)          # 向上滚＝放大
    page.wait_for_timeout(250)
    assert _pose(page)["scalePercent"] > 100, _pose(page)
    assert _pose(page)["degrees"] == 0, "缩放不该顺带改角度"
    # 转到接近正的时候吸到 0，摆正不该靠手感
    page.locator(".st-stage").press("ArrowRight")
    page.wait_for_timeout(150)
    assert _pose(page)["degrees"] == 5, _pose(page)
    page.locator(".st-stage").press("0")
    page.wait_for_timeout(150)
    assert _pose(page) == {"scalePercent": 100, "degrees": 0}, _pose(page)


def test_pose_controls_are_doubtful_when_flat(page: Page, running_server: str) -> None:
    """Disable per-state transform gestures and readings for flat packs that cannot store them."""
    page.evaluate("() => { window.__FLAT = true; }")
    _boot(page, running_server, fmt="flat")
    box = page.locator(".st-stage").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx + 140, cy)
    page.mouse.down()
    page.mouse.move(cx + 60, cy - 90, steps=8)
    page.mouse.up()
    page.mouse.wheel(0, -240)
    page.wait_for_timeout(250)
    assert _pose(page) == {"scalePercent": 100, "degrees": 0}, _pose(page)
    assert page.evaluate("() => !document.querySelector('.st-notice').hidden"), "该说明为什么摆不动"


def test_flat_talking_tile_is_one_frozen_frame(page: Page, running_server: str) -> None:
    _boot(page, running_server, fmt="flat")
    tiles = {tile["state"]: tile for tile in page.evaluate(TILES)}
    assert tiles["talking_image"]["stillOk"], tiles["talking_image"]
    page.evaluate("() => document.querySelector('[data-state=\"talking_image\"]').click()")
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.querySelector('.st-btn-whole').hidden"), \
        "单帧那一格不该给「整段播放」"
    box = page.locator(".st-strip-canvas").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + box["height"] / 2, steps=5)
    page.mouse.up()
    page.wait_for_timeout(200)
    params = page.evaluate("() => window.__STAGE.getGraph().nodes.find(\
n => n.params.stateKey === 'talking_image').params")
    assert params["startAt"] > 0, "拖起点＝挑定格在哪一帧"
    assert params["seconds"] == 0, params


def test_layered_talking_tile_has_an_editable_animation_range(page: Page, running_server: str) -> None:
    _boot(page, running_server, fps=10, fmt="layered")
    talking = page.locator('.st-tile[data-state="talking_image"]')
    talking.click()
    status = talking.locator(".st-tile-status")
    span = page.locator(".st-span")
    expect(status).to_have_text("20f")
    expect(span).to_have_text("0.00s – 2.00s")

    box = page.locator(".st-strip-canvas").bounding_box()
    assert box is not None
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + box["width"] * 0.98, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.65, y, steps=5)
    page.mouse.up()
    expect(span).to_have_text("0.00s – 1.30s")

    page.mouse.move(box["x"] + 3, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.35, y, steps=5)
    page.mouse.up()
    expect(span).to_have_text("0.70s – 1.30s")
    expect(status).to_have_text("6f")
    params = page.evaluate("""() => window.__STAGE.getGraph().nodes.find(
        n => n.params.stateKey === 'talking_image').params""")
    assert params["startAt"] == 0.7 and params["seconds"] == 0.6, params

    page.locator(".st-btn-whole").click()
    expect(span).to_have_text("0.00s – 2.00s")


def _pose(page: Page) -> dict:
    return page.evaluate(
        "() => { const n = window.__STAGE.getGraph().nodes.find("
        "x => x.params.stateKey === 'idle_image'); "
        "return { scalePercent: n.params.scalePercent, degrees: n.params.degrees }; }"
    )


def _sources(page: Page):
    return page.evaluate("() => window.__SOURCE_CALLS.slice()")


def test_picking_an_animation_takes_effect_only_on_click(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    page.evaluate("""() => document.querySelector('[data-state="idle_image"]').click()""")
    page.wait_for_timeout(300)
    page.click(".st-anim")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.querySelector('.st-drawer').classList.contains('is-open')"), \
        "按钮点不开动作库"
    page.hover(".st-pick[data-scene=\"anim-b\"]")
    page.wait_for_timeout(400)
    # 悬停只是看一眼：图里不该多出任何东西
    assert _upstream_scene(page, "idle_image")["type"] == "source.default", _upstream_scene(page, "idle_image")
    assert "anim-b|poster" in _sources(page), _sources(page)
    page.click(".st-pick[data-scene=\"anim-b\"]")
    page.wait_for_timeout(400)
    assert _upstream_scene(page, "idle_image") == {"type": "source.scene", "sceneId": "anim-b"}
    # 挑完不收起：动作库是工作面板，批量挑时每次都要重新点开是负收益
    assert page.evaluate("() => document.querySelector('.st-drawer').classList.contains('is-open')"), \
        "选完不该把人踢出动作库"
    assert page.evaluate(
        """() => document.querySelector('.st-pick[data-scene="anim-b"]').getAttribute('aria-selected') === 'true'"""), \
        "选中的那条要立刻打上勾"
    # 选"跟随默认外观"要断掉那根线、接回根节点，而不是留一个空场景节点
    _open_library(page)
    page.click(".st-pick.is-default")
    page.wait_for_timeout(400)
    assert _upstream_scene(page, "idle_image")["type"] == "source.default", _upstream_scene(page, "idle_image")
    graph = page.evaluate("() => window.__STAGE.getGraph()")
    assert [n["params"]["sceneId"] for n in graph["nodes"] if n["type"] == "source.scene"] == ["anim-a"], graph["nodes"]


def test_one_animation_is_captured_once_for_two_states(page: Page, running_server: str) -> None:
    """Reuse the animation cache when another state requests the same animation."""
    _boot(page, running_server)
    for state in ("idle_image", "happy_image"):
        page.evaluate("""(key) => document.querySelector(`[data-state="${key}"]`).click()""", state)
        page.wait_for_timeout(200)
        _open_library(page)
        page.click(".st-pick[data-scene=\"anim-b\"]")
        page.wait_for_timeout(400)
    full = [c for c in _sources(page) if c == "anim-b|full"]
    assert len(full) == 1, _sources(page)


def test_the_drag_api_targets_one_tile(page: Page, running_server: str) -> None:
    """Keep the four public animation-drop methods scoped to the intended state."""
    _boot(page, running_server)
    result = page.evaluate(
        """async () => {
            const stage = window.__STAGE;
            const tile = document.querySelector('[data-state="angry_image"]');
            const box = tile.getBoundingClientRect();
            const mid = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
            const head = document.querySelector('.st-title').getBoundingClientRect();
            await stage.assignScene('angry_image', 'anim-b');
            const lit = Array.from(document.querySelectorAll('.st-tile.is-drop-target'))
                .map((el) => el.dataset.state);
            stage.setDropHighlight(null);
            const graph = stage.getGraph();
            const out = graph.nodes.find((n) => n.params.stateKey === 'angry_image');
            const link = graph.links.find((l) => l.to === out.id);
            const from = graph.nodes.find((n) => n.id === link.from);
            return {
                inside: stage.containsPoint(mid.x, mid.y),
                outside: stage.containsPoint(-50, -50),
                targeted: stage.stateAtPoint(mid.x, mid.y),
                nowhere: stage.stateAtPoint(head.left + 4, head.top + 4),
                lit,
                sceneId: from ? from.params.sceneId : null,
                selected: document.querySelector('[data-state="angry_image"]').classList.contains('is-selected'),
            };
        }"""
    )
    assert result["inside"] is True and result["outside"] is False, result
    assert result["targeted"] == "angry_image", result
    assert result["nowhere"] is None, "标题栏不是落点"
    assert result["sceneId"] == "anim-b", result
    assert result["selected"] is True, "拖入后应当把那一格点亮，让用户看见改的是谁"


def test_the_stage_canvas_backs_its_display_size(page: Page, running_server: str) -> None:
    """Match canvas backing pixels to CSS dimensions times devicePixelRatio.

        Stretching a 360-pixel backing canvas to roughly 500 CSS pixels caused
        blur regardless of the frame-capture resolution.
        """
    _boot(page, running_server)
    ratios = page.evaluate(
        """() => {
            const out = {};
            [['stage', '.st-stage'], ['strip', '.st-strip-canvas']].forEach(([name, selector]) => {
                const el = document.querySelector(selector);
                const box = el.getBoundingClientRect();
                const dpr = window.devicePixelRatio || 1;
                out[name] = {
                    ratio: el.width / Math.max(1, box.width),
                    needed: dpr,
                    heightRatio: el.height / Math.max(1, box.height),
                };
            });
            return out;
        }"""
    )
    for name, entry in ratios.items():
        assert entry["ratio"] >= entry["needed"] - 0.02, (name, entry)
        assert entry["heightRatio"] >= entry["needed"] - 0.02, (name, entry)

def test_opening_the_picker_captures_no_animation_whole(page: Page, running_server: str) -> None:
    """Capture only a poster thumbnail on open and defer full frames until hover."""
    _boot(page, running_server)
    page.evaluate("""() => document.querySelector('[data-state="idle_image"]').click()""")
    page.wait_for_timeout(300)
    page.evaluate("() => { window.__SOURCE_CALLS.length = 0; }")
    page.click(".st-anim")
    page.wait_for_timeout(600)
    calls = _sources(page)
    frames = [c for c in calls if "|" in c]
    assert frames == [], f"展开动作库不该采集任何整条动画：{calls}"
    thumbs = [c for c in calls if c.startswith("thumb:")]
    assert all(c.startswith("thumb:") for c in calls), calls
    assert set(thumbs) <= {"thumb:anim-a", "thumb:anim-b"}, calls


def test_hovering_the_library_previews_without_committing(page: Page, running_server: str) -> None:
    """Update the poster, inspection preview and timeline on hover without committing graph changes."""
    _boot(page, running_server)
    _open_library(page)
    page.evaluate("() => { window.__SOURCE_CALLS.length = 0; }")
    page.hover('.st-pick[data-scene="anim-b"]')
    page.wait_for_timeout(500)
    calls = _sources(page)
    assert "anim-b|poster" in calls, calls
    assert "anim-b|full" not in calls, f"只是悬停不该要清晰档：{calls}"
    assert page.evaluate("() => !document.querySelector('.st-lib-preview').hidden"), "没有放大预览"
    drawn = page.evaluate(
        """() => {
            const canvas = document.querySelector('.st-lib-preview-canvas');
            const data = canvas.getContext('2d').getImageData(
                Math.round(canvas.width / 2), Math.round(canvas.height / 2), 1, 1).data;
            return { alpha: data[3], r: data[0], g: data[1], b: data[2] };
        }"""
    )
    assert drawn["alpha"] > 200, drawn
    assert (drawn["r"], drawn["g"], drawn["b"]) != (255, 255, 255), "预览是空白的"
    # 看一眼不该改图
    assert _upstream_scene(page, "idle_image")["type"] == "source.default", _upstream_scene(page, "idle_image")


def test_the_library_rail_toggles_the_same_drawer(page: Page, running_server: str) -> None:
    """Open the same animation drawer from the left handle and the instrument-strip button."""
    _boot(page, running_server)
    opened = page.evaluate("() => { document.querySelector('.st-rail').click(); return 1; }")
    page.wait_for_timeout(300)
    assert opened and page.evaluate(
        "() => document.querySelector('.st-drawer').classList.contains('is-open')")
    assert page.evaluate("() => document.querySelector('.st-rail').getAttribute('aria-expanded')") == "true"
    page.evaluate("() => document.querySelector('.st-rail').click()")
    page.wait_for_timeout(250)
    assert not page.evaluate("() => document.querySelector('.st-drawer').classList.contains('is-open')")
    assert page.evaluate("() => document.querySelector('.st-anim').getAttribute('aria-expanded')") == "false"


def test_the_drawer_covers_the_stage_and_leaves_a_sliver(page: Page, running_server: str) -> None:
    """Cover the stage with the drawer while retaining usable state rows and timeline controls."""
    _boot(page, running_server)
    _open_library(page)
    geo = page.evaluate(
        """() => {
            const r = (sel) => {
                const b = document.querySelector(sel).getBoundingClientRect();
                return { l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width };
            };
            return { drawer: r('.st-drawer'), rail: r('.st-rail'),
                     shelf: r('.st-shelf'), strip: r('.st-strip-canvas'), wrap: r('.st-stage-wrap') };
        }"""
    )
    assert geo["drawer"]["w"] > geo["wrap"]["w"] * 0.8, geo
    assert geo["rail"]["r"] <= geo["wrap"]["r"] - 8, "右缘该留一条缝，抽屉不该顶到面板边"
    assert geo["rail"]["r"] <= geo["shelf"]["l"] + 1, geo
    assert geo["drawer"]["b"] <= geo["strip"]["t"] + 1, "时间轴不能被盖住，它要能拖手柄"


def test_the_stage_stops_painting_behind_the_drawer(page: Page, running_server: str) -> None:
    """Stop painting stage frames while the drawer completely covers the stage."""
    _boot(page, running_server)
    frame = "() => document.querySelector('.st-stage').toDataURL()"
    _open_library(page)
    first = page.evaluate(frame)
    page.wait_for_timeout(1200)
    assert page.evaluate(frame) == first, "抽屉开着时台面还在画，白花性能"
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    reopened = page.evaluate(frame)
    page.wait_for_timeout(1200)
    assert page.evaluate(frame) != reopened, "收起后必须恢复播放，否则台面就冻在遮罩撤掉的那一帧"


def test_the_preview_pane_lives_inside_the_drawer(page: Page, running_server: str) -> None:
    """Keep a populated inspection preview inside the drawer when it covers the stage."""
    _boot(page, running_server)
    _open_library(page)
    layout = page.evaluate(
        """() => {
            const pane = document.querySelector('.st-lib-preview');
            const drawer = document.querySelector('.st-drawer');
            const box = pane.getBoundingClientRect();
            const canvas = pane.querySelector('canvas');
            const data = canvas.getContext('2d')
                .getImageData(Math.round(canvas.width / 2), Math.round(canvas.height / 2), 1, 1).data;
            return {
                inside: drawer.contains(pane),
                drawn: data[3] > 200 && `${data[0]},${data[1]},${data[2]}` !== '255,255,255',
                name: pane.querySelector('.st-lib-preview-name').textContent,
                overlapsShelf: box.right > document.querySelector('.st-shelf').getBoundingClientRect().left,
            };
        }"""
    )
    assert layout["inside"] is True, layout
    assert layout["overlapsShelf"] is False, layout
    assert layout["drawn"] is True, "没悬停时检视窗该演这一格已选中的那条，不能空着"
    page.hover('.st-pick[data-scene="anim-b"]')
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.querySelector('.st-anim').classList.contains('is-previewing')")
    assert page.evaluate(
        "() => window.__STAGE.getGraph().nodes.some(n => n.type === 'source.scene' "
        "&& n.params.sceneId === 'anim-b')") is False, "悬停不许改图"
    # 指针离开列表＝"我就看看"：试演要收回去，仪表带不能停在虚线上
    page.mouse.move(20, 20)
    page.wait_for_timeout(500)
    assert not page.evaluate("() => document.querySelector('.st-anim').classList.contains('is-previewing')")


def test_long_press_hands_off_a_drag_without_applying(page: Page, running_server: str) -> None:
    """Drag an animation to another state without changing its source state."""
    _boot(page, running_server)
    page.click(".st-anim")
    page.wait_for_timeout(400)
    row = page.locator('.st-pick[data-scene="anim-b"]')
    box = row.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(520)
    page.mouse.up()
    page.wait_for_timeout(200)
    drags = page.evaluate("() => window.__DRAGS")
    assert drags == [{"kind": "scene", "sceneId": "anim-b", "label": "挥手"}], drags
    assert _upstream_scene(page, "idle_image")["type"] == "source.default", \
        "长按起拖不该顺手把当前格也改了"


def test_library_marks_which_states_already_use_an_animation(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    page.click(".st-anim")
    page.wait_for_timeout(400)
    usage = page.evaluate(
        """() => {
            const row = document.querySelector('.st-pick[data-scene="anim-a"]');
            const node = row.querySelector('.st-pick-usage');
            return node ? node.textContent : '';
        }"""
    )
    assert usage, usage
    assert page.evaluate(
        """() => {
            const zh = window.__STAGE_ZH;
            const row = document.querySelector('.st-pick[data-scene="anim-a"] .st-pick-usage');
            return row.textContent === zh.packStateTalking;
        }"""
    ), usage


def test_closing_the_picker_cancels_pending_thumbs(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    page.click(".st-anim")
    page.wait_for_timeout(200)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert "drop-thumbs" in _sources(page), _sources(page)
    assert page.evaluate(
        "() => !document.querySelector('.st-drawer').classList.contains('is-open')")
    assert page.evaluate("() => document.querySelectorAll('.st-overlay').length") == 1, \
        "关掉动作库不该把整个表演台一起关掉"


def test_playback_allocates_nothing_per_frame(page: Page, running_server: str) -> None:
    """Compose the filmstrip once and move its viewport and pointer during playback.

        Creating a new canvas for every frame on every tick caused unnecessary
        allocations and visible playback stalls.
        """
    _boot(page, running_server, fps=12)
    # 先让缩略图/胶片各合成一次，把一次性分配排除在测量之外
    page.evaluate("""() => {
        window.__CANVAS_MADE = 0;
        const real = document.createElement.bind(document);
        document.createElement = (tag) => {
            if (String(tag).toLowerCase() === 'canvas') window.__CANVAS_MADE += 1;
            return real(tag);
        };
    }""")
    page.wait_for_timeout(1200)
    made = page.evaluate("() => window.__CANVAS_MADE")
    assert page.evaluate("() => window.__STAGE.getGraph().nodes.length") > 0, "表演台没起来"
    assert made <= 1, f"稳定播放期间每帧都在分配画布（1.2 秒内新建了 {made} 张）"




def test_the_close_button_closes_only_the_stage(page: Page, running_server: str) -> None:
    """Close the overlay without resolving an unqualified close call to window.close()."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    url = page.url
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("(f) => { window.__FLAT = f; return 1; }", False)
    page.evaluate(BOOT, 1.0 / 12)
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.querySelectorAll('.st-overlay').length") == 1
    page.click(".st-close")
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.querySelectorAll('.st-overlay').length") == 0, "关闭没起作用"
    assert page.url == url, "关闭覆盖层不能把整个页面带走"
    # 页面本体还在（表情制作页是 window.open 打开的，误用 window.close 会整页带走）
    assert page.evaluate("() => Boolean(document.querySelector('#emote-maker-close'))") is True


def test_opening_the_library_only_thumbs_what_is_visible(page: Page, running_server: str) -> None:
    """Capture only visible drawer rows when opening a library with hundreds of animations."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("(f) => { window.__FLAT = f; return 1; }", False)
    page.evaluate(
        """(count) => {
            const ZH = window.__STAGE_ZH;
            const items = Array.from({ length: count }, (u, i) => ({ value: 'a' + i, label: '动作 ' + i }));
            window.__THUMBS = [];
            window.__STAGE = window.NekoEmoteStateStage.create({
                model: window.NekoEmoteNodeGraph,
                t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
                fps: () => 12, format: () => 'layered',
                animationsFor: () => items,
                thumbFor: (sceneId) => { window.__THUMBS.push(sceneId); return ''; },
                framesFor: () => [],
            });
            return true;
        }""", 400)
    page.wait_for_timeout(500)
    page.evaluate("() => { window.__THUMBS.length = 0; }")
    page.click(".st-rail")
    page.wait_for_timeout(600)
    rows = page.evaluate("() => document.querySelectorAll('.st-pick').length")
    thumbs = page.evaluate("() => window.__THUMBS.length")
    assert rows == 401, rows
    assert thumbs < 40, f"展开时给 {thumbs} 条动作排了采集，应当只取可视区域附近"


def test_the_library_can_be_filtered(page: Page, running_server: str) -> None:
    _boot(page, running_server)
    _open_library(page)
    page.fill(".st-library-search", "挥手")
    page.wait_for_timeout(300)
    visible = page.evaluate("() => Array.from(document.querySelectorAll('.st-pick-label')).map(n => n.textContent)")
    assert visible == ['跟随默认外观', '挥手'], visible
    assert page.evaluate("() => document.querySelector('.st-drawer-count').textContent") == "1 / 2"
    page.fill(".st-library-search", "不存在的名字")
    page.wait_for_timeout(300)
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('.st-pick-label')).map(n => n.textContent)"
    ) == ['跟随默认外观'], "筛空了也要留一个能用的出口"


def test_filtering_jumps_back_to_the_top(page: Page, running_server: str) -> None:
    """Reset filtered scroll positions so the first result stays below the sticky search field."""
    _boot(page, running_server)
    _open_library(page)
    page.evaluate("() => { document.querySelector('.st-library-scroll').scrollTop = 400; }")
    page.fill(".st-library-search", "挥手")
    page.wait_for_timeout(300)
    check = page.evaluate("""() => {
        const scroller = document.querySelector('.st-library-scroll');
        const first = document.querySelector('.st-pick');
        return { scrolled: scroller.scrollTop,
                 firstIsDefault: first.classList.contains('is-default'),
                 belowBar: first.getBoundingClientRect().top
                     < document.querySelector('.st-library-search').getBoundingClientRect().bottom };
    }""")
    assert check["scrolled"] == 0, check
    assert check["firstIsDefault"] is True, check
    assert check["belowBar"] is False, check


def test_the_backdrop_freezes_at_the_frame_the_drawer_opened(page: Page, running_server: str) -> None:
    """Keep the decorative background fixed while hovering different animation previews."""
    _boot(page, running_server)
    page.wait_for_timeout(200)
    shot = "() => document.querySelector('.st-stage').toDataURL()"
    page.click(".st-rail")
    page.wait_for_timeout(400)
    # 基线要在抽屉稳定之后取：开合本身会补画一帧，那是应该的
    frozen = page.evaluate(shot)
    page.wait_for_timeout(300)
    hover = page.evaluate("""() => {
        const scroller = document.querySelector('.st-library-scroll');
        const row = Array.from(document.querySelectorAll('.st-pick'))
            .find((el) => el.dataset.scene === 'anim-b' || el.dataset.scene === 'anim-a');
        const b = row.getBoundingClientRect();
        const box = scroller.getBoundingClientRect();
        if (b.top < box.top || b.bottom > box.bottom) {
            row.scrollIntoView({ block: 'center' });
        }
        const r = row.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }""")
    page.mouse.move(hover["x"], hover["y"])
    page.wait_for_timeout(700)
    assert page.evaluate(shot) == frozen, "抽屉里的悬停把背景预览也带着变了"
    # 检视窗自己要在动——冻的是背景，不是全部
    pane = "() => document.querySelector('.st-lib-preview-canvas').toDataURL()"
    first = page.evaluate(pane)
    page.wait_for_timeout(600)
    assert page.evaluate(pane) != first, "检视窗被一起冻住了"
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    after_close = page.evaluate(shot)
    page.wait_for_timeout(700)
    assert page.evaluate(shot) != after_close, "收起后背景必须恢复播放"


def test_leaving_the_library_keeps_the_scroll_position(page: Page, running_server: str) -> None:
    """Preserve the list scroll position when the pointer crosses animation entries."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate(
        """(count) => {
            const ZH = window.__STAGE_ZH;
            window.__STAGE = window.NekoEmoteStateStage.create({
                model: window.NekoEmoteNodeGraph,
                t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
                fps: () => 12, format: () => 'layered',
                animationsFor: () => Array.from({ length: count },
                    (u, i) => ({ value: 'a' + i, label: '动作 ' + i })),
                framesFor: () => [],
            });
            return true;
        }""", 300)
    page.wait_for_timeout(500)
    page.click(".st-rail")
    page.wait_for_timeout(400)
    moved = page.evaluate("""() => {
        const scroller = document.querySelector('.st-library-scroll');
        scroller.scrollTop = 1200;
        return scroller.scrollTop;
    }""")
    assert moved >= 600, moved
    # 用坐标悬停：page.hover 会把目标行滚进视野，那本身就会改 scrollTop
    target = page.evaluate("""() => {
        const scroller = document.querySelector('.st-library-scroll');
        const box = scroller.getBoundingClientRect();
        const row = Array.from(document.querySelectorAll('.st-pick')).find((el) => {
            const r = el.getBoundingClientRect();
            return r.top > box.top + 20 && r.bottom < box.bottom - 20;
        });
        const r = row.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2,
                 top: Math.round(r.top - box.top) };
    }""")
    page.mouse.move(target["x"], target["y"])
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.querySelector('.st-library-scroll').scrollTop") == moved,         "悬停可见行不该移动列表"
    page.mouse.move(1150, 220)          # 离开列表，进检视窗
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.querySelector('.st-library-scroll').scrollTop") == moved, \
        "鼠标离开列表把滚动位置弹回了顶部"
    # 拖时间轴也不该重建/归零列表
    box = page.locator(".st-strip-canvas").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.97, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2, steps=6)
    page.mouse.up()
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.querySelector('.st-library-scroll').scrollTop") == moved, \
        "拖动起止手柄时列表被重建了"


def test_the_pane_shows_the_frame_the_still_state_froze_on(page: Page, running_server: str) -> None:
    """Update the flat talking inspection preview when range handles choose a different still frame."""
    _boot(page, running_server, fmt="flat")
    page.evaluate("""() => document.querySelector('[data-state="talking_image"]').click()""")
    page.wait_for_timeout(500)
    page.click(".st-rail")
    page.wait_for_timeout(400)
    pane = "() => document.querySelector('.st-lib-preview-canvas').toDataURL()"
    strip = page.locator(".st-strip-canvas").bounding_box()
    before = page.evaluate(pane)
    # 拖起点：定格换到后面那一帧
    page.mouse.move(strip["x"] + strip["width"] * 0.04, strip["y"] + strip["height"] / 2)
    page.mouse.down()
    page.mouse.move(strip["x"] + strip["width"] * 0.6, strip["y"] + strip["height"] / 2, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)
    moved = page.evaluate(pane)
    assert moved != before, "换了定格帧，检视窗还停在原来那一帧"
    assert page.evaluate(
        "() => window.__STAGE.getGraph().nodes.find("
        "n => n.params.stateKey === 'talking_image').params.startAt") > 0
    # 再拖一次，应当跟着再变（不是简单地"和第一次不同"）
    page.mouse.move(strip["x"] + strip["width"] * 0.62, strip["y"] + strip["height"] / 2)
    page.mouse.down()
    page.mouse.move(strip["x"] + strip["width"] * 0.9, strip["y"] + strip["height"] / 2, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)
    assert page.evaluate(pane) not in (before, moved), "检视窗只在第一次变化，之后又卡住了"


def test_an_animation_behind_a_modifier_still_reads_as_assigned(page: Page, running_server: str) -> None:
    """Resolve animation bindings through an intervening skin node instead of reporting defaults."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('source.scene', { sceneId: 'walk' }, 'sc'),
                    mk('mod.skin', { groups: [] }, 'mod'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'out'),
                ],
                links: [
                    { from: 'sc', to: 'mod', fromSocket: 'look', toSocket: 'look' },
                    { from: 'mod', to: 'out', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [{ value: 'walk', label: '散步' }],
            framesFor: () => [],
        });
        return 1;
    }""")
    page.wait_for_timeout(500)
    page.click(".st-rail")
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.querySelector('.st-anim').textContent.includes('散步')"), \
        page.evaluate("() => document.querySelector('.st-anim').textContent")
    assert page.evaluate("""() => {
        const row = document.querySelector('.st-pick[data-scene="walk"]');
        return row.getAttribute('aria-selected') === 'true'
            && !document.querySelector('.st-pick.is-default').getAttribute('aria-selected')
                .startsWith('t');
    }"""), "选择器把这条标成未选中，等于说它没设置"
    # 状态行也不能报"跟随默认"
    assert page.evaluate("() => document.querySelector('[data-state=\"idle_image\"] .st-tile-sub').textContent") == "散步"


def test_tile_and_library_share_one_thumb_cache(page: Page, running_server: str) -> None:
    """Share identical thumbnail requests between state rows and the animation library."""
    _boot(page, running_server, thumbnail=True)
    page.click(".st-rail")
    page.wait_for_timeout(600)
    page.click(".st-rail")
    page.wait_for_timeout(200)
    page.click(".st-rail")
    page.wait_for_timeout(600)
    # 抽屉开了用户图片区之后，行的纵向位置变了：停在轨道上的指针可能正好
    # 压住某一行，触发一次合法的悬停预览（poster 档）。这条用例只管缓存共享，
    # 悬停预览归另一条——把指针停到台面中央再读调用清单。
    page.mouse.move(700, 400)
    calls = _sources(page)
    # 收起再展开也不该重取：缓存按动画存，和状态行共用同一份
    for scene in ("anim-a", "anim-b"):
        assert calls.count("thumb:" + scene) == 1, calls
    # 每条动画的缩略图各采一次（状态行与动作库共享同一份缓存）；
    # 开合抽屉时指针扫过行会带出一次海报档悬停预览——合法，至多一次；
    # 昂贵的 full 档只属于 root（状态行真渲染），场景动画不许进 full。
    for scene in ("anim-a", "anim-b"):
        assert calls.count("thumb:" + scene) == 1, calls
        assert calls.count(scene + "|poster") <= 1, calls
        assert scene + "|full" not in calls, calls

def test_only_a_state_row_accepts_a_dropped_animation(page: Page, running_server: str) -> None:
    """Ignore drops on the stage, filmstrip and empty space instead of modifying the selected state."""
    _boot(page, running_server)
    result = page.evaluate(
        """() => {
            const stage = window.__STAGE;
            const at = (sel, fx, fy) => {
                const b = document.querySelector(sel).getBoundingClientRect();
                return stage.stateAtPoint(b.left + b.width * fx, b.top + b.height * fy);
            };
            return {
                onRow: at('[data-state="sad_image"]', 0.5, 0.5),
                stageCentre: at('.st-stage', 0.5, 0.5),
                strip: at('.st-strip-canvas', 0.5, 0.5),
                band: at('.st-band', 0.5, 0.5),
            };
        }"""
    )
    assert result["onRow"] == "sad_image", result
    assert result["stageCentre"] is None, f"台面上松手仍会应用: {result}"
    assert result["strip"] is None, result
    assert result["band"] is None, result


def test_assigned_rows_show_a_thumb_without_being_clicked(page: Page, running_server: str) -> None:
    """Prioritize state-row thumbnails above the drawer backlog so bound states do not remain blank."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        const png = (() => {
            const c = document.createElement('canvas'); c.width = 24; c.height = 24;
            const x = c.getContext('2d'); x.fillStyle = '#7a3fa0'; x.fillRect(0, 0, 24, 24);
            return c.toDataURL('image/png');
        })();
        window.__THUMB_CALLS = [];
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('source.scene', { sceneId: 'walk' }, 's-a'),
                    mk('source.scene', { sceneId: 'wave' }, 's-b'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle'),
                    mk('output.state', { stateKey: 'happy_image', seconds: 0 }, 'o-happy'),
                    mk('output.state', { stateKey: 'sad_image', seconds: 0 }, 'o-sad'),
                ],
                links: [
                    { from: 's-a', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
                    { from: 's-b', to: 'o-happy', fromSocket: 'look', toSocket: 'look' },
                    { from: 'root', to: 'o-sad', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [{ value: 'walk', label: '散步' }, { value: 'wave', label: '挥手' }],
            // 抽屉那 400 条先排队：状态行的请求必须能插到它们前面，否则永远空白
            thumbFor: (sceneId, hot) => {
                window.__THUMB_CALLS.push([sceneId, hot === true]);
                return Promise.resolve(png);
            },
            framesFor: () => [],
        });
        return true;
    }""")
    page.wait_for_timeout(700)
    state = page.evaluate(
        """() => {
            const rows = {};
            document.querySelectorAll('.st-tile').forEach((tile) => {
                rows[tile.dataset.state] = Boolean(tile.querySelector('.st-tile-thumb img'));
            });
            return { rows, calls: window.__THUMB_CALLS };
        }"""
    )
    assert state["rows"]["happy_image"] is True, state   # 接了动画的行要有图
    assert state["rows"]["sad_image"] is False, state    # 跟随默认的行不该要帧源
    hot = [c for c in state["calls"] if c[1]]
    assert "happy_image" not in hot and "sad_image" not in hot, state
    assert ["walk", "wave"] == [c[0] for c in state["calls"] if c[1]], state


def test_image_bound_rows_show_a_thumb_without_being_clicked(page: Page, running_server: str) -> None:
    """Render user-image state thumbnails without a click, including root-prefixed committed scenes."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        const png = (() => {
            const c = document.createElement('canvas'); c.width = 24; c.height = 24;
            const x = c.getContext('2d'); x.fillStyle = '#7a3fa0'; x.fillRect(0, 0, 24, 24);
            return c.toDataURL('image/png');
        })();
        window.__THUMB_CALLS = [];
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('source.image', { imageId: 'img-x' }, 'img-node'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle'),
                    mk('output.state', { stateKey: 'happy_image', seconds: 0 }, 'o-happy'),
                ],
                links: [
                    { from: 'img-node', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
                    { from: 'root', to: 'o-happy', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [{ value: 'walk', label: '散步' }],
            userImages: () => [{ id: 'img-x', label: '图片.png', thumb: '' }],
            thumbFor: (sceneId, hot) => {
                window.__THUMB_CALLS.push([sceneId, hot === true]);
                return Promise.resolve(png);
            },
            framesFor: () => [],
        });
        return true;
    }""")
    page.wait_for_timeout(700)
    state = page.evaluate(
        """() => {
            const rows = {};
            document.querySelectorAll('.st-tile').forEach((tile) => {
                rows[tile.dataset.state] = Boolean(tile.querySelector('.st-tile-thumb img'));
            });
            const idleSub = document.querySelector(
                '.st-tile[data-state="idle_image"] .st-tile-sub')?.textContent || '';
            return { rows, calls: window.__THUMB_CALLS, idleSub };
        }"""
    )
    assert state["rows"]["idle_image"] is True, state    # 绑图行要有图
    assert state["rows"]["happy_image"] is False, state  # 跟随默认的行照旧不要
    assert ["image:img-x", True] in state["calls"], state
    assert state["idleSub"] == "图片.png", state


def test_late_image_list_refresh_repaints_tiles(page: Page, running_server: str) -> None:
    """Refresh image-bound state labels and thumbnails when library restoration finishes after rendering."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        const png = (() => {
            const c = document.createElement('canvas'); c.width = 24; c.height = 24;
            const x = c.getContext('2d'); x.fillStyle = '#7a3fa0'; x.fillRect(0, 0, 24, 24);
            return c.toDataURL('image/png');
        })();
        window.__THUMB_CALLS = [];
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('source.image', { imageId: 'img-x' }, 'img-node'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle'),
                    mk('output.state', { stateKey: 'happy_image', seconds: 0 }, 'o-happy'),
                ],
                links: [
                    { from: 'img-node', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
                    { from: 'root', to: 'o-happy', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [{ value: 'walk', label: '散步' }],
            thumbFor: (sceneId, hot) => {
                window.__THUMB_CALLS.push([sceneId, hot === true]);
                return Promise.resolve(png);
            },
            framesFor: () => [],
        });
        return true;
    }""")
    page.wait_for_timeout(400)
    before = page.evaluate(
        "() => document.querySelector('.st-tile[data-state=\"idle_image\"] .st-tile-sub')?.textContent || ''")
    assert before == "跟随默认", before   # 清单还没喂进来，只能报跟随默认
    page.evaluate("""() => {
        window.NekoEmoteStateStage.setUserImages([{ id: 'img-x', label: '晚到.png', thumb: '' }]);
        window.__STAGE.refreshUserImages();
    }""")
    page.wait_for_function(
        "() => document.querySelector('.st-tile[data-state=\"idle_image\"] .st-tile-sub')?.textContent === '晚到.png'",
        timeout=5000)


def test_export_zone_box_marks_the_content_bounds(page: Page, running_server: str) -> None:
    """Outline and grid the export region while leaving its surrounding transparent area clear."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        const frame = (() => {
            const c = document.createElement('canvas'); c.width = 64; c.height = 64;
            const x = c.getContext('2d');
            x.fillStyle = '#e04040'; x.fillRect(16, 16, 32, 32);   // 中间实心、四周透明
            return { width: 64, height: 64, delayMs: 100,
                data: x.getImageData(0, 0, 64, 64).data };
        })();
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: { schemaVersion: 1,
                nodes: [mk('source.default', {}, 'root'),
                        mk('source.scene', { sceneId: 'anim-a' }, 's-a'),
                        mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle')],
                links: [{ from: 's-a', to: 'o-idle', fromSocket: 'look', toSocket: 'look' }] },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [{ value: 'anim-a', label: '动作A' }],
            thumbFor: () => Promise.resolve(''),
            framesFor: () => [frame],
        });
    }""")
    found = False
    dump = {}
    for _ in range(40):
        dump = page.evaluate("""() => {
            const c = document.querySelector('.st-stage');
            const fit = Math.min(c.width / 64, c.height / 64) * 0.88;
            const half = 32 * fit;
            const cx = c.width / 2, cy = c.height / 2;
            const row = [];
            for (let x = 0; x < c.width; x += 1) {
                row.push(c.getContext('2d').getImageData(
                    x, Math.round(cy - half), 1, 1).data[3]);
            }
            const maxTop = Math.max(...row);
            const argTop = row.indexOf(maxTop);
            return { w: c.width, h: c.height, dpr: window.devicePixelRatio,
                maxTop, argTop, half, maxRowData:
                Array.from(c.getContext('2d').getImageData(
                    Math.max(0, argTop - 2), Math.round(cy - half), 5, 1).data) };
        }""")
        if dump.get("maxTop", 0) > 60:
            found = True
            break
        page.wait_for_timeout(200)
    print("EZ-DUMP:", dump)
    assert found is True, f"顶边整行扫描没有找到虚线描边: {dump}"
    # 网格线竖线贯穿到顶边行：轮询找到的亮像素 x 就是一条竖线，向下采中部验证
    state = page.evaluate("""(argTop) => {
        const c = document.querySelector('.st-stage');
        const cy = c.height / 2;
        const maxAlpha = (x, y) => Math.max(...Array.from(
            c.getContext('2d').getImageData(Math.round(x - 2), Math.round(y), 5, 1).data
                .filter((_, i) => i % 4 === 3)));
        return { midColumn: maxAlpha(argTop, cy),
            outside: Array.from(c.getContext('2d').getImageData(10, Math.round(cy), 1, 1).data) };
    }""", dump["argTop"])
    assert state["midColumn"] > 60, state         # 竖线贯穿（网格线连续）
    assert state["outside"][3] == 0, state        # 框外保持全透明


def test_stage_close_button_sits_at_the_right_edge(page: Page, running_server: str) -> None:
    """Keep the close button at the header right edge after removal of the former hint element."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: g.defaultGraph(),
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [], thumbFor: () => Promise.resolve(''),
            framesFor: () => [],
        });
    }""")
    page.wait_for_timeout(300)
    state = page.evaluate("""() => {
        const title = document.querySelector('.st-title').getBoundingClientRect();
        const close = document.querySelector('.st-close').getBoundingClientRect();
        const panel = document.querySelector('.st-panel').getBoundingClientRect();
        return { gap: close.left - title.right,
                 closeToPanelRight: panel.right - close.right,
                 sameRow: Math.abs(close.top - title.top) < 8 };
    }""")
    assert state["gap"] > 100, f"关闭按钮贴着标题，没有被推到右侧: {state}"
    assert state["closeToPanelRight"] < 40, f"关闭按钮没贴住面板右缘: {state}"
    assert state["sameRow"] is True, state


def test_image_carousel_appends_and_removes(page: Page, running_server: str) -> None:
    """Append images to a state carousel through assignImage and allow individual or full removal."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle'),
                ],
                links: [
                    { from: 'root', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key, params) => {
                let text = ZH[key.split('.').slice(1).join('.')] || key;
                if (params) {
                    for (const [name, value] of Object.entries(params)) {
                        text = text.split(`{{${name}}}`).join(String(value));
                    }
                }
                return text;
            },
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [],
            userImages: () => [
                { id: 'img-a', label: '图片A', thumb: '' },
                { id: 'img-b', label: '图片B', thumb: '' },
            ],
            thumbFor: () => Promise.resolve(''),
            framesFor: () => [],
        });
        return true;
    }""")
    page.wait_for_timeout(300)
    page.evaluate("""async () => {
        await window.__STAGE.assignImage('idle_image', 'img-a');
        await window.__STAGE.assignImage('idle_image', 'img-b');
    }""")
    page.wait_for_function(
        "() => document.querySelector('.st-tile[data-state=\"idle_image\"] .st-tile-sub')?.textContent === '图片A 等 2 张'",
        timeout=5000)
    state = page.evaluate("""() => ({
        ids: window.__STAGE.getGraph().nodes.find((n) => n.type === 'source.image')?.params.imageIds,
        chips: document.querySelectorAll('.st-bound-chip').length,
        rowHidden: document.querySelector('.st-bound')?.hidden,
    })""")
    assert state["ids"] == ["img-a", "img-b"], state
    assert state["chips"] == 2 and state["rowHidden"] is False, state

    # 摘第一张：列表变 [img-b]，副标题跟名
    page.evaluate("() => document.querySelector('.st-bound-chip .st-bound-chip-x').click()")
    page.wait_for_function(
        "() => document.querySelector('.st-tile[data-state=\"idle_image\"] .st-tile-sub')?.textContent === '图片B'",
        timeout=5000)
    ids = page.evaluate(
        "() => window.__STAGE.getGraph().nodes.find((n) => n.type === 'source.image')?.params.imageIds")
    assert ids == ["img-b"], ids

    # 清空：拆掉图片源，回"跟随默认"
    page.evaluate("() => document.querySelector('.st-bound-clear').click()")
    page.wait_for_function(
        "() => document.querySelector('.st-tile[data-state=\"idle_image\"] .st-tile-sub')?.textContent === '跟随默认'",
        timeout=5000)
    state = page.evaluate("""() => ({
        hasImageNode: window.__STAGE.getGraph().nodes.some((n) => n.type === 'source.image'),
        rowHidden: document.querySelector('.st-bound').hidden,
    })""")
    assert state["hasImageNode"] is False and state["rowHidden"] is True, state


def test_state_tiles_capture_thumbs_at_their_start_frame(page: Page, running_server: str) -> None:
    """Capture each state thumbnail at its own start handle and key the cache by that start time."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        const png = (() => {
            const c = document.createElement('canvas'); c.width = 24; c.height = 24;
            const x = c.getContext('2d'); x.fillStyle = '#7a3fa0'; x.fillRect(0, 0, 24, 24);
            return c.toDataURL('image/png');
        })();
        window.__THUMB_CALLS = [];
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('source.scene', { sceneId: 'walk' }, 's-a'),
                    mk('source.scene', { sceneId: 'wave' }, 's-b'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle'),
                    mk('output.state', { stateKey: 'happy_image', seconds: 0, startAt: 2.5 }, 'o-happy'),
                    mk('output.state', { stateKey: 'sad_image', seconds: 0, startAt: 4 }, 'o-sad'),
                    mk('output.state', { stateKey: 'talking_image', seconds: 0 }, 'o-talk'),
                ],
                links: [
                    { from: 's-a', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
                    { from: 's-b', to: 'o-happy', fromSocket: 'look', toSocket: 'look' },
                    { from: 's-a', to: 'o-sad', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [
                { value: 'walk', label: '散步' },
                { value: 'wave', label: '挥手' },
            ],
            thumbFor: (sceneId, hot, opts) => {
                window.__THUMB_CALLS.push([sceneId, hot === true, opts || null]);
                return Promise.resolve(png);
            },
            framesFor: () => [],
        });
        return true;
    }""")
    page.wait_for_timeout(500)
    calls = page.evaluate("() => window.__THUMB_CALLS")
    flat = [tuple(call) for call in calls]
    assert ("wave", True, {"startAt": 2.5}) in flat, flat
    assert ("walk", True, {"startAt": 4}) in flat, flat
    assert ("walk", True, None) in flat, flat   # 没动过起点的格子照旧不带窗口


def test_rebinding_a_tile_does_not_stack_the_previous_preview(page: Page, running_server: str) -> None:
    """Clear reused state canvases before compositing transparent frames from a new binding."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.evaluate("() => document.body.classList.remove('editor-empty')")
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate("""() => {
        const ZH = window.__STAGE_ZH;
        const g = window.NekoEmoteNodeGraph;
        const mk = (t, p, id) => g.makeNode(t, 0, 0, p, id);
        const buildFrame = (r, g2, b, x, y) => {
            const c = document.createElement('canvas'); c.width = 8; c.height = 8;
            const x2 = c.getContext('2d');
            x2.fillStyle = `rgba(${r},${g2},${b},1)`;
            x2.fillRect(x, y, 2, 2);
            return { width: 8, height: 8, delayMs: 100,
                data: x2.getImageData(0, 0, 8, 8).data };
        };
        window.__STAGE = window.NekoEmoteStateStage.create({
            model: g,
            graph: {
                schemaVersion: 1,
                nodes: [
                    mk('source.default', {}, 'root'),
                    mk('source.scene', { sceneId: 'anim-a' }, 's-a'),
                    mk('source.scene', { sceneId: 'anim-b' }, 's-b'),
                    mk('output.state', { stateKey: 'idle_image', seconds: 0 }, 'o-idle'),
                ],
                links: [
                    { from: 's-a', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
                ],
            },
            t: (key) => ZH[key.split('.').slice(1).join('.')] || key,
            fps: () => 12, format: () => 'layered',
            animationsFor: () => [
                { value: 'anim-a', label: '动作A' },
                { value: 'anim-b', label: '动作B' },
            ],
            thumbFor: () => Promise.resolve(''),
            framesFor: (source) => {
                if (String(source) === 'anim-a') {
                    return [buildFrame(255, 40, 40, 0, 0)];
                }
                return [buildFrame(40, 40, 255, 6, 6)];
            },
        });
        return true;
    }""")
    page.evaluate("() => document.querySelector('.st-tile[data-state=\"idle_image\"]').click()")
    page.wait_for_function("""() => {
        const c = document.querySelector('.st-tile[data-state="idle_image"] .st-tile-thumb canvas');
        if (!c) return false;
        const d = c.getContext('2d').getImageData(2, 2, 1, 1).data;
        return d[0] > 200 && d[3] > 200;   // 红块左上角
    }""", timeout=8000)
    page.evaluate("""() => window.__STAGE.assignScene('idle_image', 'anim-b')""")
    page.wait_for_function("""() => {
        const c = document.querySelector('.st-tile[data-state="idle_image"] .st-tile-thumb canvas');
        if (!c) return false;
        const blue = c.getContext('2d').getImageData(40, 40, 1, 1).data;
        const red = c.getContext('2d').getImageData(2, 2, 1, 1).data;
        return blue[2] > 200 && blue[3] > 200 && red[3] === 0;   // 蓝在、红没了
    }""", timeout=8000)


def test_a_long_hover_drag_on_a_library_row_does_not_commit(page: Page, running_server: str) -> None:
    """Suppress the click after releasing a long drawer drag even when the old timeout has elapsed."""
    _boot(page, running_server)
    _open_library(page)
    page.wait_for_timeout(200)
    row = page.locator('.st-pick[data-scene="anim-b"]')
    box = row.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(500)                    # 长按起拖
    page.wait_for_timeout(900)                    # 停在原地超过旧的 400ms 窗口
    page.mouse.up()
    page.wait_for_timeout(120)
    committed = page.evaluate(
        """() => window.__STAGE.getGraph().nodes.some(
            (n) => n.type === 'source.scene' && n.params.sceneId === 'anim-b')"""
    )
    assert committed is False, "悬停久了再松手，被当成一次点击写进图里了"

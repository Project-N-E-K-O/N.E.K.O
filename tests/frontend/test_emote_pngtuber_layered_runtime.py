"""Acceptance gate for the layered avatar package we generate ourselves.

Everything in the layered-plan depends on one fact this test pins: pngtuber-core.js loads a
manifest authored by *us* (not by the PNGTuber Plus / Remix reverse-engineering importers),
reports the right state count, switches states by name, and honours the timed return.

If this ever goes red, the layered export must not ship — a package that loads but draws
nothing is worse than the flat one it replaced.

Uses the repo's `running_server` fixture, so uploads land in a throwaway user-data dir.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

MODULES = (
    "/static/js/emote_pngtuber_pack.js",
    "/static/js/emote_pngtuber_layered.js",
)

FOLDER = "neko-layered-acceptance"

BUILD_AND_UPLOAD = r"""async ([folder]) => {
    const layered = window.NekoEmotePngtuberLayered;
    const pack = window.NekoEmotePngtuberPack;
    const FW = 128, FH = 128;

    async function sheet(frames, tint) {
        const canvas = document.createElement('canvas');
        canvas.width = FW * frames; canvas.height = FH;
        const ctx = canvas.getContext('2d');
        for (let f = 0; f < frames; f += 1) {
            ctx.fillStyle = tint;
            ctx.fillRect(f * FW, 0, FW, FH);
            ctx.fillStyle = '#000000';
            ctx.fillRect(f * FW + 10 + f * 8, 40, 20, 40);
        }
        const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
        return new Uint8Array(await blob.arrayBuffer());
    }
    async function firstFrame(png, frames) {
        const img = new Image();
        img.src = URL.createObjectURL(new Blob([png], { type: 'image/png' }));
        await img.decode();
        const canvas = document.createElement('canvas');
        canvas.width = FW; canvas.height = FH;
        canvas.getContext('2d').drawImage(img, 0, 0, FW * frames, FH, 0, 0, FW, FH);
        URL.revokeObjectURL(img.src);
        const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
        return new Uint8Array(await blob.arrayBuffer());
    }

    const idlePng = await sheet(6, '#ff6b6b');
    const talkPng = await sheet(1, '#6bd0ff');
    const happyPng = await sheet(4, '#ffd166');

    const built = layered.buildLayeredPackage({
        canvasWidth: FW, canvasHeight: FH,
        states: [
            { stateKey: 'idle_image', frames: 6, width: FW, height: FH, sheetWidth: FW * 6, fps: 12, pngBytes: idlePng },
            { stateKey: 'talking_image', frames: 1, width: FW, height: FH, sheetWidth: FW, fps: 1, pngBytes: talkPng },
            { stateKey: 'happy_image', frames: 4, width: FW, height: FH, sheetWidth: FW * 4, fps: 12, pngBytes: happyPng },
        ],
    });

    const modelJson = {
        name: folder,
        description: 'layered acceptance package',
        model_type: 'pngtuber',
        pngtuber: {
            // 服务端 _validate_model_package 要求 idle_image 非空，所以分层包
            // 也必须带一张平铺回落图；只发 metadata.json 会被判为无效包。
            idle_image: 'idle.png',
            adapter: built.adapter,
            layered_metadata: built.metadataPath,
            scale: 1, offset_x: 0, offset_y: 0, mirror: false,
        },
    };

    const files = [
        { path: `${folder}/model.json`, data: new TextEncoder().encode(JSON.stringify(modelJson, null, 2)) },
        { path: `${folder}/metadata.json`,
          data: built.files.find((f) => f.path === 'metadata.json').data },
        { path: `${folder}/idle.png`, data: await firstFrame(idlePng, 6) },
    ].concat(built.files.filter((f) => f.path.startsWith('layers/'))
        .map((f) => ({ path: `${folder}/${f.path}`, data: f.data })));

    const form = new FormData();
    files.forEach((f) => form.append('files', new Blob([f.data]), f.path));
    const res = await fetch('/api/model/pngtuber/upload_model', { method: 'POST', body: form });
    const json = await res.json().catch(() => ({}));
    return { ok: Boolean(json.success), json, stateCount: built.metadata.state_count };
}"""

LOAD_IN_RUNTIME = r"""async ([folder]) => {
    const container = document.createElement('div');
    container.id = 'acceptance-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:600px';
    document.body.appendChild(container);
    container.appendChild(Object.assign(document.createElement('img'), { id: 'acceptance-image' }));

    const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
    const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
    config.layered_metadata = `${root}/metadata.json`;
    config.idle_image = `${root}/idle.png`;

    const manager = new window.PNGTuberManager('acceptance-container');
    await manager.load(config);

    const paintedPixels = () => {
        const canvas = manager.canvasElement;
        if (!canvas) return 0;
        const ctx = canvas.getContext('2d');
        if (!ctx) return 0;
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let count = 0;
        for (let i = 3; i < data.length; i += 4) if (data[i] > 0) count += 1;
        return count;
    };

    const result = {
        layeredActive: manager.isLayeredActive(),
        stateCount: manager.getLayeredStateCount(),
        catalog: (manager.layeredStateCatalog() || []).map((s) => s.name),
        hasCanvas: Boolean(manager.canvasElement),
    };
    manager.drawLayeredState('idle_image', performance.now());
    result.idlePainted = paintedPixels() > 0;

    result.playHappy = manager.playLayeredAnimation('happy_image', {});
    result.indexAfterHappy = manager.layeredStateIndex;
    manager.drawLayeredState('happy_image', performance.now());
    result.happyPainted = paintedPixels() > 0;

    result.playTalk = manager.playLayeredAnimation('talking_image', { returnToDefaultAfterMs: 120 });
    result.indexAfterTalk = manager.layeredStateIndex;
    await new Promise((r) => setTimeout(r, 340));
    result.indexAfterReturn = manager.layeredStateIndex;

    result.byOrdinal = manager.playLayeredAnimation('2', {});
    result.indexByOrdinal = manager.layeredStateIndex;
    manager.dispose();
    return result;
}"""


@pytest.fixture()
def uploaded(page: Page, running_server: str) -> dict:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    for src in MODULES:
        page.add_script_tag(url=src)
    page.wait_for_function(
        "() => Boolean(window.NekoEmotePngtuberLayered && window.NekoEmotePngtuberPack)",
        timeout=15000,
    )
    result = page.evaluate(BUILD_AND_UPLOAD, [FOLDER])
    assert result["ok"], f"分层包未被服务端接受: {result['json']}"
    yield result
    page.evaluate(
        """async ([f]) => fetch('/api/model/pngtuber/model', {
            method: 'DELETE', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ folder: f }) })""", [result["json"]["folder"]])


def _runtime_page(page: Page, running_server: str) -> None:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.add_script_tag(url="/static/pngtuber-core.js")
    page.wait_for_function("() => Boolean(window.PNGTuberManager)", timeout=15000)


def test_server_accepts_our_layered_package(uploaded: dict) -> None:
    assert uploaded["json"]["folder"]
    assert uploaded["stateCount"] == 3


def test_runtime_loads_it_as_layered_and_paints(uploaded: dict, page: Page,
                                                running_server: str) -> None:
    _runtime_page(page, running_server)
    result = page.evaluate(LOAD_IN_RUNTIME, [uploaded["json"]["folder"]])
    assert result["layeredActive"] is True, "运行时没有把它识别为分层包"
    assert result["stateCount"] == 3, f"状态数应为 3，实际 {result['stateCount']}"
    assert result["catalog"] == ["idle_image", "talking_image", "happy_image"]
    assert result["hasCanvas"] is True
    assert result["idlePainted"] is True, "分层画布是空的 —— 包能加载但画不出东西"


def test_states_are_addressable_by_name_and_ordinal(uploaded: dict, page: Page,
                                                    running_server: str) -> None:
    _runtime_page(page, running_server)
    result = page.evaluate(LOAD_IN_RUNTIME, [uploaded["json"]["folder"]])
    assert result["playHappy"] is True and result["indexAfterHappy"] == 2
    assert result["happyPainted"] is True, "切状态后画布变空"
    assert result["byOrdinal"] is True and result["indexByOrdinal"] == 1, \
        "state_catalog 的序号别名解析失败"


def test_timed_return_goes_back_to_state_zero(uploaded: dict, page: Page,
                                              running_server: str) -> None:
    _runtime_page(page, running_server)
    result = page.evaluate(LOAD_IN_RUNTIME, [uploaded["json"]["folder"]])
    assert result["playTalk"] is True and result["indexAfterTalk"] == 1
    assert result["indexAfterReturn"] == 0, "returnToDefaultAfterMs 没有回到默认状态"


def test_upload_and_delete_leaves_no_residue(uploaded: dict, page: Page,
                                             running_server: str) -> None:
    """Remove the imported acceptance-test package to avoid contaminating later library tests."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    listed = page.evaluate(
        "async () => (await (await fetch('/api/model/pngtuber/models')).json()).models.map(m => m.folder)")
    assert FOLDER in listed, f"上传后应当能列出 {FOLDER}，实际 {listed}"

    status = page.evaluate(
        """async ([f]) => (await fetch('/api/model/pngtuber/model', {
            method: 'DELETE', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ folder: f }) })).status""", [FOLDER])
    assert status == 200, f"删除返回 {status}"

    after = page.evaluate(
        "async () => (await (await fetch('/api/model/pngtuber/models')).json()).models.map(m => m.folder)")
    assert FOLDER not in after, f"删除后仍在列表里: {after}"

PRIORITY_PROBE = r"""async ([folder, priority]) => {
    const container = document.createElement('div');
    container.id = 'prio-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:400px;height:400px';
    document.body.appendChild(container);
    container.appendChild(Object.assign(document.createElement('img'), { id: 'prio-image' }));
    const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
    const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
    config.layered_metadata = `${root}/metadata.json`;
    config.idle_image = `${root}/idle.png`;
    config.state_priority = priority;

    const manager = new window.PNGTuberManager('prio-container');
    await manager.load(config);
    const out = { readBack: manager.statePriority() };

    manager.setState('idle');
    out.indexAtStart = manager.layeredStateIndex;

    // 正在说话时来了情绪（走真实入口：说话本身会占住 talking 那一格）
    manager.setSpeaking(true);
    out.accepted = manager.setEmotion('happy');
    out.indexWhileSpeaking = manager.layeredStateIndex;
    out.deferred = manager.pendingEmotion
        ? manager.layeredEmotionTarget(manager.pendingEmotion.name) : null;

    // 话说完：情绪应当自己补上（延后，不是丢弃）
    manager.setSpeaking(false);
    await new Promise((r) => setTimeout(r, 400));
    out.indexAfterSpeech = manager.layeredStateIndex;

    // 平铺路径的仲裁（分层的说话不占状态号，所以这一半在平铺里才有区别）
    // 平铺那份 config 必须带着 state_priority，否则读成默认值、测的是个假开关
    const flat = {
        idle_image: 'i.gif', talking_image: 't.gif', happy_image: 'h.gif',
        state_priority: priority,
    };
    const asFlat = Object.assign(manager, { config: flat, layeredMetadata: null });
    asFlat.emotionImage = 'h.gif';
    out.flatTalkingSrc = asFlat.stateToSrc('talking');
    manager.dispose();
    return out;
}"""


def _priority(uploaded, page, running_server, priority):
    _runtime_page(page, running_server)
    return page.evaluate(PRIORITY_PROBE, [uploaded["json"]["folder"], priority])


def test_speech_first_defers_the_emotion_until_speech_ends(uploaded, page, running_server):
    """Queue emotion during talking priority and play it after speech completes."""
    result = _priority(uploaded, page, running_server, "talking")
    assert result["readBack"] == "talking", result
    assert result["indexAtStart"] == 0, result
    assert result["accepted"] is True, "setEmotion 不该拒绝，只是延后"
    # 说话期间占着的是"说话"那一格，绝不是刚来的情绪
    assert result["indexWhileSpeaking"] == 1, \
        f"说话中被插进了情绪状态 {result['indexWhileSpeaking']}"
    assert result["deferred"] == 2, result
    assert result["indexAfterSpeech"] == 2, "话说完情绪没补上＝它被悄悄丢掉了"
    assert result["flatTalkingSrc"] == "t.gif", result


def test_emotion_first_lets_the_emotion_show_while_speaking(uploaded, page, running_server):
    """Keep a talking frame from overriding the active emotion in an emotion-first flat pack."""
    result = _priority(uploaded, page, running_server, "emotion")
    assert result["readBack"] == "emotion", result
    assert result["flatTalkingSrc"] == "h.gif", \
        f"情绪优先时 talking 仍在显示口型图: {result}"


# ── 换行雪碧图：导出用的帧数一多，表就会折成多行，网格必须照实写 ──────────────
# 这组断言对应的真实故障：60 帧 @512px 的表折成 8x8 后，metadata 仍写"一行 60 格"，
# 运行时按 sx=(frame%60)*512 取格 —— 第 9 帧起全部取到图像外，形象每循环闪一次空白。
# 只测 builder 挡不住它：谎报的是"给运行时看的那份数字"，所以要拿运行时的取格函数
# 去对真实解码出来的贴图。

WRAPPED_FOLDER = "neko-layered-wrapped"

# 20 帧 @256px：一行只放得下 floor(4096/256)=16 格，所以必然换行（16x2=32 格，12 格是空的）
WRAP_BUILD_AND_UPLOAD = r"""async ([folder]) => {
    const sheet = window.NekoEmoteSheet;
    const layered = window.NekoEmotePngtuberLayered;
    const FW = 256, COUNT = 20;
    const colour = (f) => [20 + f * 10, 180, 60];

    function frame(f) {
        const data = new Uint8ClampedArray(FW * FW * 4);
        const [r, g, b] = colour(f);
        for (let i = 0; i < data.length; i += 4) {
            data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = 255;
        }
        // 每帧画一个位置不同的黑记号：证明取到的格子真的是这一帧，而不是一片同色底
        for (let y = 30; y < 60; y += 1) {
            for (let x = 30 + f * 4; x < 50 + f * 4; x += 1) {
                const p = (y * FW + x) * 4;
                data[p] = 0; data[p + 1] = 0; data[p + 2] = 0; data[p + 3] = 255;
            }
        }
        return { width: FW, height: FW, data };
    }

    const frames = [];
    for (let f = 0; f < COUNT; f += 1) frames.push(frame(f));
    // 混尺寸的批次：格子必须按最大的那帧定。两帧（16 红、20 蓝）的表应当是 2x1 格、
    // 每格 20x20 → 画布 40x20，蓝帧铺满第二格。按"第一帧"定格子时画布只有 32x16，
    // 蓝帧右边和下边各被裁掉 4px —— 采样 (39,19) 就落到画布外（全透明）。
    const solid = (w, h, rgb) => {
        const d = new Uint8ClampedArray(w * h * 4);
        for (let i = 0; i < d.length; i += 4) {
            d[i] = rgb[0]; d[i + 1] = rgb[1]; d[i + 2] = rgb[2]; d[i + 3] = 255;
        }
        return d;
    };
    const mixed = sheet.composeSheetCanvas(
        [{ width: 16, height: 16, data: solid(16, 16, [255, 0, 0]) },
            { width: 20, height: 20, data: solid(20, 20, [0, 0, 255]) }],
        (w, h) => Object.assign(document.createElement('canvas'), { width: w, height: h }),
    );
    const mixedCorner = [
        ...mixed.canvas.getContext('2d').getImageData(39, 19, 1, 1).data,
    ];
    // 走产品自己的拼表函数：网格由它决定，测试不自己发明布局
    const composed = sheet.composeSheetCanvas(frames, (w, h) => {
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        return c;
    });
    const pngBytes = await sheet.canvasToPngBytes(composed.canvas);

    const built = layered.buildLayeredPackage({
        canvasWidth: FW, canvasHeight: FW,
        states: [
            { stateKey: 'idle_image', frames: composed.hframes * composed.vframes,
              frameCount: frames.length,
              width: FW, height: FW,
              sheetWidth: composed.canvas.width, sheetHeight: composed.canvas.height,
              fps: 12, pngBytes },
        ],
    });
    const own = built.metadata.layers[0].states[0];

    const modelJson = {
        name: folder, description: 'wrapped sheet acceptance', model_type: 'pngtuber',
        pngtuber: {
            idle_image: 'idle.png', adapter: built.adapter,
            layered_metadata: built.metadataPath,
            scale: 1, offset_x: 0, offset_y: 0, mirror: false,
        },
    };
    const first = sheet.firstFrameCanvas(frames[0]);
    const files = [
        { path: `${folder}/model.json`, data: new TextEncoder().encode(JSON.stringify(modelJson)) },
        { path: `${folder}/metadata.json`,
          data: built.files.find((f) => f.path === 'metadata.json').data },
        { path: `${folder}/idle.png`, data: await sheet.canvasToPngBytes(first) },
    ].concat(built.files.filter((f) => f.path.startsWith('layers/'))
        .map((f) => ({ path: `${folder}/${f.path}`, data: f.data })));

    const form = new FormData();
    files.forEach((f) => form.append('files', new Blob([f.data]), f.path));
    const res = await fetch('/api/model/pngtuber/upload_model', { method: 'POST', body: form });
    const json = await res.json().catch(() => ({}));
    return { ok: Boolean(json.success), json, grid: {
        hframes: own.hframes, vframes: own.vframes, frames: own.frames,
        realFrames: frames.length,
        // 合成方自己说"我折了几行"，与 builder 写出的数字无关：用它当前提检查，
        // 才能让"builder 谎报单行"这种故障落到实质断言上，而不是把测试挡在门口
        composedRows: composed.vframes, composedCols: composed.hframes,
        composedFrame: [composed.frameWidth, composed.frameHeight],
        cells: composed.hframes * composed.vframes,
        imageWidth: composed.canvas.width, imageHeight: composed.canvas.height },
        mixed: { frame: [mixed.frameWidth, mixed.frameHeight],
                 size: [mixed.canvas.width, mixed.canvas.height],
                 corner: mixedCorner } };
}"""

WRAPPED_PROBE = r"""async ([folder]) => {
    const container = document.createElement('div');
    container.id = 'wrapped-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:600px';
    document.body.appendChild(container);
    container.appendChild(Object.assign(document.createElement('img'), { id: 'wrapped-image' }));
    const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
    const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
    config.layered_metadata = `${root}/metadata.json`;
    config.idle_image = `${root}/idle.png`;

    const manager = new window.PNGTuberManager('wrapped-container');
    await manager.load(config);
    const active = manager.isLayeredActive();

    const layer = manager.layeredMetadata.layers[0];
    const layerState = manager.layerStateForCurrentIndex(layer);
    const img = manager.layeredImages.get(layer._imageIndex);
    const scratch = document.createElement('canvas');
    scratch.width = img.naturalWidth; scratch.height = img.naturalHeight;
    scratch.getContext('2d').drawImage(img, 0, 0);

    // 直接问运行时"第 f 帧该取哪一块"，再看那一块的真实像素
    const at = (f) => {
        const rect = manager.stateFrameInfo(layer, layerState, img, performance.now(), f);
        const d = scratch.getContext('2d').getImageData(rect.sx, rect.sy, 1, 1).data;
        return { sx: rect.sx, sy: rect.sy, sw: rect.sw, sh: rect.sh,
                 chosen: rect.frame, inside: rect.sx + rect.sw <= img.naturalWidth
                     && rect.sy + rect.sh <= img.naturalHeight,
                 rgb: [d[0], d[1], d[2]], alpha: d[3] };
    };
    const visited = [];
    for (let f = 0; f < layerState.frames; f += 1) visited.push(at(f));
    const last = at(layerState.frames + 4);  // 越过真实帧数：运行时该夹住，不该走到空格
    manager.dispose();
    return {
        active,
        grid: { hframes: layerState.hframes, vframes: layerState.vframes,
                frames: layerState.frames, fw: layerState.frame_width,
                fh: layerState.frame_height,
                animate: manager.stateHasFrameAnimation(layerState) },
        imgSize: [img.naturalWidth, img.naturalHeight],
        visited, last,
    };
}"""


@pytest.fixture()
def wrapped(page: Page, running_server: str) -> dict:
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    for src in ("/static/js/emote_sheet.js",) + MODULES:
        page.add_script_tag(url=src)
    page.wait_for_function(
        "() => Boolean(window.NekoEmoteSheet && window.NekoEmotePngtuberLayered"
        " && window.NekoEmotePngtuberPack)",
        timeout=15000,
    )
    result = page.evaluate(WRAP_BUILD_AND_UPLOAD, [WRAPPED_FOLDER])
    assert result["ok"], f"换行的分层包未被服务端接受: {result['json']}"
    yield result
    page.evaluate(
        """async ([f]) => fetch('/api/model/pngtuber/model', {
            method: 'DELETE', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ folder: f }) })""", [result["json"]["folder"]])


def test_mixed_frame_sizes_do_not_get_clipped_or_overlap(wrapped):
    """Enlarge sprite-sheet cells rather than clipping a later, larger frame."""
    mixed = wrapped["mixed"]
    assert mixed["frame"] == [20, 20], f"格子尺寸跟着第一帧走了: {mixed}"
    assert mixed["size"] == [40, 20], f"画布按第一帧开，最后一帧被裁掉了: {mixed}"
    assert mixed["corner"][3] == 255, f"最后一帧的右下角没有内容（被裁掉）: {mixed}"
    assert mixed["corner"][:3] == [0, 0, 255], f"最后一帧的位置取到了别人的格子: {mixed}"


def test_wrapped_sheet_addresses_every_real_frame(wrapped, page, running_server):
    """Keep wrapped frame cells in bounds and separate from padding cells and adjacent frames."""
    grid = wrapped["grid"]
    assert grid["composedRows"] > 1, f"这组帧数没测到换行: {grid}"
    assert grid["hframes"] == grid["composedCols"] and grid["vframes"] == grid["composedRows"], \
        f"metadata 的网格和随包的贴图不一致: {grid}"
    assert grid["frames"] == grid["realFrames"], \
        f"metadata 的帧数写成了格子总数 {grid}：补齐的空格会被当成动画帧播出来"

    _runtime_page(page, running_server)
    out = page.evaluate(WRAPPED_PROBE, [wrapped["json"]["folder"]])
    assert out["active"] is True
    assert out["grid"]["frames"] == grid["realFrames"], out["grid"]
    assert out["grid"]["animate"] is True, "换行的表被判定为无动画，形象会定格"

    colour = lambda f: [20 + f * 10, 180, 60]  # noqa: E731
    for f, entry in enumerate(out["visited"]):
        assert entry['chosen'] == f, entry
        assert entry["inside"] is True, f"第 {f} 帧取到图像外: {entry} / 图 {out['imgSize']}"
        assert entry["alpha"] == 255, f"第 {f} 帧取到空白格（补齐的格子被播出来了）: {entry}"
        assert entry["rgb"] == colour(f), f"第 {f} 帧取到了别的帧的格子: {entry}"

    # 越界请求由运行时夹回最后一帧，夹到的必须还是有内容的那一帧
    assert out["last"]["chosen"] == grid["realFrames"] - 1, out["last"]
    assert out["last"]["rgb"] == colour(grid["realFrames"] - 1), \
        f"夹回的那一帧是空的: {out['last']}"


# ── 分层包的瞬时状态槽：说话 / 拖动 / 点击 ─────────────────────────────────────
# 平铺包里这三个动作靠换 <img> 的 src 表现，分层模式没有 src 可换 —— 渲染只认
# layeredStateIndex。改动前它们调用了 drawLayeredState('talking') 之类，但名字被
# 完全忽略，所以用户在表演台上绑到说话/拖动/点击的动作一条都演不出来。
SLOT_FOLDER = "neko-layered-slots"
SLOT_EMO_FOLDER = "neko-layered-slot-emo"
COSTUME_FOLDER = "neko-layered-costume"

SLOT_BUILD_AND_UPLOAD = r"""async ([folder, states, priority]) => {
    const layered = window.NekoEmotePngtuberLayered;
    const pack = window.NekoEmotePngtuberPack;
    const FW = 128;

    async function solidSheet(rgb, frames) {
        const canvas = document.createElement('canvas');
        canvas.width = FW * frames; canvas.height = FW;
        const ctx = canvas.getContext('2d');
        for (let f = 0; f < frames; f += 1) {
            // 两帧不同亮度：这样"动画有没有被反复从头开始"在别的断言里也能看出来
            const k = f === 0 ? 1 : 0.8;
            ctx.fillStyle = `rgb(${Math.round(rgb[0] * k)}, ${Math.round(rgb[1] * k)}, ${Math.round(rgb[2] * k)})`;
            ctx.fillRect(f * FW, 0, FW, FW);
        }
        const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
        return new Uint8Array(await blob.arrayBuffer());
    }

    const built = layered.buildLayeredPackage({
        canvasWidth: FW, canvasHeight: FW,
        states: await Promise.all(states.map(async (item) => ({
            stateKey: item.key,
            frames: 2,
            frameCount: 2,
            width: FW, height: FW,
            sheetWidth: FW * 2, sheetHeight: FW,
            fps: 12,
            pngBytes: await solidSheet(item.rgb, 2),
        }))),
    });

    const modelJson = {
        name: folder, description: 'slot acceptance', model_type: 'pngtuber',
        pngtuber: {
            idle_image: 'idle.png', adapter: built.adapter,
            layered_metadata: built.metadataPath, state_priority: priority,
            scale: 1, offset_x: 0, offset_y: 0, mirror: false,
        },
    };
    // 平铺回落图：用第一个状态的第 0 帧（服务端要求 idle_image 真实存在）
    const fallback = document.createElement('canvas');
    fallback.width = FW; fallback.height = FW;
    const first = states[0];
    fallback.getContext('2d').fillStyle = `rgb(${first.rgb[0]}, ${first.rgb[1]}, ${first.rgb[2]})`;
    fallback.getContext('2d').fillRect(0, 0, FW, FW);

    const files = [
        { path: `${folder}/model.json`, data: new TextEncoder().encode(JSON.stringify(modelJson)) },
        { path: `${folder}/metadata.json`,
          data: built.files.find((f) => f.path === 'metadata.json').data },
        { path: `${folder}/idle.png`,
          data: new Uint8Array(await (await new Promise((r) => fallback.toBlob(r, 'image/png'))).arrayBuffer()) },
    ].concat(built.files.filter((f) => f.path.startsWith('layers/'))
        .map((f) => ({ path: `${folder}/${f.path}`, data: f.data })));

    const form = new FormData();
    files.forEach((f) => form.append('files', new Blob([f.data]), f.path));
    const res = await fetch('/api/model/pngtuber/upload_model', { method: 'POST', body: form });
    const json = await res.json().catch(() => ({}));
    return { ok: Boolean(json.success), json, catalog: built.metadata.state_catalog.map((s) => s.name) };
}"""

SLOT_PROBE = r"""async ([folder]) => {
    const container = document.createElement('div');
    container.id = 'slot-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:600px';
    document.body.appendChild(container);
    container.appendChild(Object.assign(document.createElement('img'), { id: 'slot-image' }));
    const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
    const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
    config.layered_metadata = `${root}/metadata.json`;
    config.idle_image = `${root}/idle.png`;

    const manager = new window.PNGTuberManager('slot-container');
    await manager.load(config);

    // 每个状态是一种确定的纯色，所以"现在演的是哪一格"可以直接从画布读出来
    const nearest = () => {
        const canvas = manager.canvasElement;
        const ctx = canvas.getContext('2d');
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let best = null, bestScore = Infinity, painted = 0;
        for (let i = 0; i < data.length; i += 4) {
            if (data[i + 3] < 200) continue;
            painted += 1;
            for (const [name, c] of Object.entries(window.__slotColours)) {
                // 两帧亮度差 20%，取较近的一档
                for (const k of [1, 0.8]) {
                    const score = Math.abs(data[i] - c[0] * k) + Math.abs(data[i + 1] - c[1] * k)
                        + Math.abs(data[i + 2] - c[2] * k);
                    if (score < bestScore) { bestScore = score; best = name; }
                }
            }
        }
        return painted > 0 ? best : null;
    };
    const snap = (label) => ({ label, index: manager.layeredStateIndex, shown: nearest() });

    const out = { steps: [], priority: manager.statePriority() };
    manager.drawLayeredState('idle');
    out.steps.push(snap('open'));

    // 1) 说话：应占住 talking 那一格
    manager.startSpeakingMouthAnimation();
    out.steps.push(snap('speak'));
    // 口型节拍每 ~150ms 切一次 talking/idle：状态槽不该跟着来回跳，
    // 也不该每次都被重启（重启会让这一格的动画永远停在开头）
    const frameLog = [];
    const realSet = manager.setLayeredStateIndex.bind(manager);
    manager.setLayeredStateIndex = (i, o) => { frameLog.push([i, (o && o.source) || '']); return realSet(i, o); };
    for (let i = 0; i < 6; i += 1) {
        manager.setState(i % 2 ? 'idle' : 'talking', { restartLayeredAnimation: false });
    }
    out.steps.push(snap('after-mouth-flaps'));
    out.mouthSwitches = frameLog.length;

    // 2) 拖动：盖在说话上；松手回到说话（话还没说完）
    manager.showDragImage();
    out.steps.push(snap('drag'));
    manager.restoreStateImage();
    out.steps.push(snap('drag-end'));

    // 3) 话说完：回到默认那一格
    manager.setSpeaking(false);
    await new Promise((r) => setTimeout(r, 260));
    manager.drawLayeredState('idle');
    out.steps.push(snap('speech-end'));

    // 4) 点击只播一轮。即使返回定时器晚执行，动画也不会回卷到第一帧。
    window.lanlan_config = { ...window.lanlan_config, model_type: 'pngtuber' };
    manager.handleClick(new MouseEvent('click'));
    out.steps.push(snap('click'));
    const clickLayer = manager.layeredMetadata.layers.find(layer => layer.name === 'click_image');
    const clickState = clickLayer.states[3];
    const clickImage = manager.layeredImages.get(clickLayer._imageIndex);
    out.clickFrames = [0, 100, 170].map(ms => manager.stateFrameInfo(
        clickLayer, clickState, clickImage, manager.layeredAnimationStart + ms,
    ).frame);
    await new Promise((r) => setTimeout(r, 280));
    out.steps.push(snap('click-released'));

    // 5) 点击还挂着时说话插进来：setSpeaking 会取消那个 600ms 定时器，
    //    点击那一格必须跟着放开，否则会一直停在点击上不回话
    manager.showClickImage();
    manager.clickTimer = setTimeout(() => {}, 600);
    manager.setSpeaking(true);
    out.steps.push(snap('click-cancelled-by-speech'));
    out.clickTimerCleared = manager.clickTimer === null;
    manager.setSpeaking(false);
    await new Promise((r) => setTimeout(r, 260));
    out.steps.push(snap('cleanup'));

    manager.setLayeredStateIndex = realSet;
    manager.dispose();
    return out;
}"""

EMOTION_SLOT_PROBE = r"""async ([folder]) => {
    const container = document.createElement('div');
    container.id = 'emo-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:600px';
    document.body.appendChild(container);
    const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
    const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
    config.layered_metadata = `${root}/metadata.json`;
    config.idle_image = `${root}/idle.png`;
    const manager = new window.PNGTuberManager('emo-container');
    await manager.load(config);
    const out = { priority: manager.statePriority(), talking: null, happy: null };
    out.talking = manager.layeredSlotStateIndex('talking');
    out.happy = manager.layeredSlotStateIndex('happy');
    out.absent = manager.layeredSlotStateIndex('surprised');

    // 情绪优先：情绪正在演的时候，说话不许把状态槽抢走
    manager.setEmotion('happy');
    out.afterEmotion = manager.layeredStateIndex;
    manager.startSpeakingMouthAnimation();
    out.duringSpeech = manager.layeredStateIndex;
    // 情绪到点撤走，话还没说完：这一格该交还给说话，而不是掉回待机
    manager.clearEmotion();
    out.afterEmotionGone = manager.layeredStateIndex;
    manager.setSpeaking(false);
    await new Promise((r) => setTimeout(r, 320));
    out.afterSpeech = manager.layeredStateIndex;
    manager.dispose();
    return out;
}"""

COSTUME_PROBE = r"""async ([folder]) => {
    const container = document.createElement('div');
    container.id = 'costume-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:600px';
    document.body.appendChild(container);
    const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
    const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
    config.layered_metadata = `${root}/metadata.json`;
    config.idle_image = `${root}/idle.png`;
    const manager = new window.PNGTuberManager('costume-container');
    await manager.load(config);
    const out = { indices: [] };
    // 目录里没有 talking/drag/click 这些名字（PNGTuber Plus / Remix 换装模型）：
    // 说话与拖动一律不许动状态号，否则用户挑的服装会被抢走
    manager.playLayeredAnimation('2', {});
    out.indices.push({ label: 'costume-picked', index: manager.layeredStateIndex });
    manager.startSpeakingMouthAnimation();
    out.indices.push({ label: 'speak', index: manager.layeredStateIndex });
    manager.showDragImage();
    out.indices.push({ label: 'drag', index: manager.layeredStateIndex });
    manager.restoreStateImage();
    out.indices.push({ label: 'drag-end', index: manager.layeredStateIndex });
    manager.setSpeaking(false);
    await new Promise((r) => setTimeout(r, 300));
    out.indices.push({ label: 'speech-end', index: manager.layeredStateIndex });
    manager.dispose();
    return out;
}"""

SLOT_STATES = [
    {"key": "idle_image", "rgb": [255, 0, 0]},
    {"key": "talking_image", "rgb": [0, 255, 0]},
    {"key": "drag_image", "rgb": [0, 0, 255]},
    {"key": "click_image", "rgb": [255, 255, 0]},
    {"key": "happy_image", "rgb": [255, 0, 255]},
]

COSTUME_STATES = [
    {"key": "idle_image", "rgb": [255, 0, 0]},
    {"key": "outfit_blue", "rgb": [0, 255, 0]},
    {"key": "outfit_gold", "rgb": [0, 0, 255]},
]


def _upload_states(page, running_server, folder, states, priority="talking"):
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    for src in MODULES:
        page.add_script_tag(url=src)
    page.wait_for_function(
        "() => Boolean(window.NekoEmotePngtuberLayered && window.NekoEmotePngtuberPack)",
        timeout=15000,
    )
    result = page.evaluate(SLOT_BUILD_AND_UPLOAD, [folder, states, priority])
    assert result["ok"], f"分层包未被服务端接受: {result['json']}"
    return result


def _step(out, label):
    hit = [s for s in out["steps"] if s["label"] == label]
    assert hit, f"缺少步骤 {label}: {out['steps']}"
    return hit[0]


def test_speech_drag_click_take_the_state_slot(page, running_server):
    """Use each layered state with priority drag above talking above default."""
    _upload_states(page, running_server, SLOT_FOLDER, SLOT_STATES)
    _runtime_page(page, running_server)
    page.evaluate(
        """(map) => { window.__slotColours = map; }""",
        {s["key"][:4]: s["rgb"] for s in SLOT_STATES})
    out = page.evaluate(SLOT_PROBE, [SLOT_FOLDER])
    try:
        assert out["priority"] == "talking", out["priority"]
        assert _step(out, "open")["shown"] == "idle", out["steps"]
        assert _step(out, "speak")["index"] == 1, "说话没有切到 talking 那一格"
        assert _step(out, "speak")["shown"] == "talk", out["steps"]
        # 口型节拍：6 次 setState 不该产生任何一次状态切换
        assert out["mouthSwitches"] == 0, f"口型节拍在来回抢状态槽（{out['mouthSwitches']} 次）：动画会被反复重启"
        assert _step(out, "after-mouth-flaps")["index"] == 1, out["steps"]
        assert _step(out, "drag")["index"] == 2, "拖动没有切到 drag 那一格"
        assert _step(out, "drag-end")["index"] == 1, "松手后该回到还没说完的话"
        assert _step(out, "speech-end")["index"] == 0, "话说完该回到默认状态"
        assert _step(out, "speech-end")["shown"] == "idle", out["steps"]
        assert _step(out, "click")["index"] == 3, "点击没有切到 click 那一格"
        assert out["clickFrames"] == [0, 1, 1], out["clickFrames"]
        assert _step(out, "click-released")["index"] == 0, "点击放开后没有回到该在的那一格"
        # 点击的 600ms 定时器被说话取消时，那一格必须一起放开，否则会卡在点击上
        assert _step(out, "click-cancelled-by-speech")["index"] == 1, out["steps"]
        assert out["clickTimerCleared"] is True, out
        assert _step(out, "cleanup")["index"] == 0, out["steps"]
    finally:
        page.evaluate(
            """async ([f]) => fetch('/api/model/pngtuber/model', {
                method: 'DELETE', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ folder: f }) })""", [SLOT_FOLDER])


def test_emotion_first_owns_the_slot_over_speech(page, running_server):
    """Restore ongoing talking after an emotion-first animation releases the state slot."""
    _upload_states(page, running_server, SLOT_EMO_FOLDER, SLOT_STATES, "emotion")
    _runtime_page(page, running_server)
    out = page.evaluate(EMOTION_SLOT_PROBE, [SLOT_EMO_FOLDER])
    try:
        assert out["priority"] == "emotion", out["priority"]
        assert out["talking"] == 1 and out["happy"] == 4, out
        assert out["absent"] is None, "包里没有的状态被当成了 0（会悄悄跳回待机）"
        assert out["afterEmotion"] == 4, out
        assert out["duringSpeech"] == 4, f"情绪优先时说话抢了状态槽: {out}"
        assert out["afterEmotionGone"] == 1, f"情绪撤走后没把说话接回来: {out}"
        assert out["afterSpeech"] == 0, f"话说完没回到默认状态: {out}"
    finally:
        page.evaluate(
            """async ([f]) => fetch('/api/model/pngtuber/model', {
                method: 'DELETE', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ folder: f }) })""", [SLOT_EMO_FOLDER])


def test_costume_packs_are_not_disturbed(page, running_server):
    """Keep talking and dragging from changing state indices in packs without those state names."""
    _upload_states(page, running_server, COSTUME_FOLDER, COSTUME_STATES)
    _runtime_page(page, running_server)
    out = page.evaluate(COSTUME_PROBE, [COSTUME_FOLDER])
    try:
        # 用户挑中的第 2 套服装，从头到尾都得是第 2 套
        assert [item["index"] for item in out["indices"]] == [1, 1, 1, 1, 1], out["indices"]
    finally:
        page.evaluate(
            """async ([f]) => fetch('/api/model/pngtuber/model', {
                method: 'DELETE', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ folder: f }) })""", [COSTUME_FOLDER])


# ── 平铺包：说话优先时，情绪不得从口型节拍里漏出来 ─────────────────────────────
# 报上来的现象是"就算说话优先，说话中还是会夹情绪动作"。成因在平铺这边：
# 口型每 ~150ms 在 talking/idle 之间切一次 src，而 stateToSrc('idle') 返回
# emotionImage —— 情绪优先的判断只管住了 'talking' 那一支，'idle' 那一支照原样
# 把情绪图放了出来。所以画面变成 talking → 情绪 → talking → 情绪 的闪。
FLAT_HOLD_PROBE = r"""async () => {
    const container = document.createElement('div');
    container.id = 'flat-container';
    container.style.cssText = 'position:fixed;left:0;top:0;width:300px;height:300px';
    document.body.appendChild(container);
    const manager = new window.PNGTuberManager('flat-container');
    // 平铺 config：文件名即状态，断言读的就是 <img> 上真实的 src
    manager.config = {
        idle_image: 'i.gif', talking_image: 't.gif', happy_image: 'h.gif',
        sad_image: 's.gif', state_priority: 'talking',
    };
    manager.ensureContainer();
    manager.setState('idle');
    const src = () => manager.imageElement.getAttribute('src');
    const seen = new Set();
    const out = { priority: manager.statePriority() };
    out.beforeSpeech = src();

    manager.setEmotion('happy');
    out.emotionShown = src();

    manager.setSpeaking(true);
    // 让真实的口型节拍跑一会儿：这里记录每一拍实际显示的图
    for (let i = 0; i < 14; i += 1) {
        await new Promise((r) => setTimeout(r, 60));
        seen.add(src());
    }
    out.duringSpeech = [...seen].sort();
    manager.setEmotion('sad');            // 说话中途又来了一个情绪
    seen.clear();
    for (let i = 0; i < 10; i += 1) {
        await new Promise((r) => setTimeout(r, 60));
        seen.add(src());
    }
    out.afterSecondEmotion = [...seen].sort();
    out.deferredName = manager.pendingEmotion ? manager.pendingEmotion.name : null;

    manager.setSpeaking(false);
    await new Promise((r) => setTimeout(r, 500));
    out.afterSpeech = src();
    manager.dispose();
    return out;
}"""


def test_speech_first_blocks_the_emotion_from_flat_mouth_beats(page, running_server):
    """Keep emotion frames out of the image element during flat-pack speech and defer them until afterward."""
    _runtime_page(page, running_server)
    out = page.evaluate(FLAT_HOLD_PROBE)
    assert out["priority"] == "talking", out["priority"]
    assert out["beforeSpeech"] == "i.gif", out
    assert out["emotionShown"] == "h.gif", "没说话时情绪本该正常显示"
    assert "h.gif" not in out["duringSpeech"], \
        f"说话中夹了情绪动作（口型节拍漏出 emotionImage）: {out['duringSpeech']}"
    assert set(out["duringSpeech"]) <= {"i.gif", "t.gif"}, out["duringSpeech"]
    assert "h.gif" not in out["afterSecondEmotion"] and "s.gif" not in out["afterSecondEmotion"], \
        f"说话中途来的情绪直接上了画面: {out['afterSecondEmotion']}"
    assert out["deferredName"] == "sad", f"延后的情绪没被记下来（等于被丢掉了）: {out}"
    assert out["afterSpeech"] == "s.gif", \
        f"话说完没把攒下的情绪补上: {out['afterSpeech']}"


@pytest.mark.parametrize("mode", ["legacy-emotion", "drag-before-speech", "paused-end", "paused-no-emotion", "hidden-end"])
def test_layered_speech_priority_lifecycle(page, running_server, mode):
    folder = "review-speech-" + mode
    states = [SLOT_STATES[0], SLOT_STATES[4]] if mode == "legacy-emotion" else SLOT_STATES
    _upload_states(page, running_server, folder, states)
    _runtime_page(page, running_server)
    try:
        result = page.evaluate("""async ([folder, mode]) => {
            const div = document.createElement('div');
            div.id = 'review-speech-container';
            document.body.append(div);
            const root = `/user_pngtuber/${encodeURIComponent(folder)}`;
            const config = (await (await fetch(`${root}/model.json`)).json()).pngtuber;
            config.layered_metadata = `${root}/metadata.json`;
            config.idle_image = `${root}/idle.png`;
            const manager = new PNGTuberManager(div.id);
            await manager.load(config);
            try {
                if (mode === 'drag-before-speech') {
                    manager.showDragImage();
                    manager.startSpeakingMouthAnimation();
                    const during = manager.layeredStateIndex;
                    manager.restoreStateImage();
                    return {during, afterRelease: manager.layeredStateIndex};
                }
                manager.startSpeakingMouthAnimation();
                if (mode === 'paused-no-emotion') {
                    manager.pauseRendering();
                    manager.setSpeaking(false);
                    manager.resumeRendering();
                    return {index:manager.layeredStateIndex,pending:manager.pendingEmotion,speaking:manager.isSpeaking};
                }
                manager.setEmotion('happy', {durationMs: 0});
                if (mode === 'legacy-emotion') {
                    return {index: manager.layeredStateIndex, pending: manager.pendingEmotion};
                }
                const wasPending = !!manager.pendingEmotion;
                if (mode === 'paused-end') {
                    manager._renderingPaused = true;
                    manager.setSpeaking(false);
                } else manager.hide();
                return {wasPending, pending: manager.pendingEmotion, emotion: manager.currentEmotion,
                    index: manager.layeredStateIndex};
            } finally { manager.dispose(); }
        }""", [folder, mode])
        if mode == "legacy-emotion":
            assert result == {"index": 1, "pending": None}
        elif mode == "drag-before-speech":
            assert result == {"during": 2, "afterRelease": 1}
        elif mode == 'paused-no-emotion':
            assert result == {'index':0,'pending':None,'speaking':False}
        else:
            assert result["wasPending"]
            assert result["pending"] is None
            if mode == "paused-end":
                assert result["emotion"] == "happy"
                assert result["index"] == 4
            else:
                assert not result["emotion"]
    finally:
        page.evaluate("""folder => fetch('/api/model/pngtuber/model', {
            method: 'DELETE', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({folder})})""", folder)

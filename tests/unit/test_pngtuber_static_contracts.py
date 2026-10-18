import json
import shutil
from pathlib import Path

import pytest

from tests.static_app_parts import read_js_parts
from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PNGTUBER_CORE_PATH = PROJECT_ROOT / "static" / "pngtuber-core.js"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"
APP_AUDIO_PLAYBACK_PATH = PROJECT_ROOT / "static" / "app" / "app-audio-playback.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def test_pngtuber_mobile_web_detection_uses_canonical_width_predicate():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    block = source[
        source.index("function isPngtuberMobileWebPage()"):
        source.index("function canInteractWithAvatar()")
    ]

    assert "if (isModelManagerPage()) return false;" in block
    assert "document.body?.classList.contains('electron-chat-window')" in block
    assert "if (window.__LANLAN_IS_ELECTRON_PET__) return false;" in block
    assert "typeof window.isMobileWidth === 'function'" in block
    assert "return window.innerWidth <= 768;" in block


def test_pngtuber_config_keeps_separate_mobile_placement_fields():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    normalize_block = source[
        source.index("function normalizeConfig(config)"):
        source.index("class PNGTuberManager")
    ]

    assert "normalized.scale = clampNumber(source.scale, SCALE_MIN, SCALE_MAX, 1);" in normalize_block
    assert "normalized.offset_x = Number.isFinite(Number(source.offset_x)) ? Number(source.offset_x) : 0;" in normalize_block
    assert "normalized.offset_y = Number.isFinite(Number(source.offset_y)) ? Number(source.offset_y) : 0;" in normalize_block
    assert "normalized.mobile_scale = clampNumber(source.mobile_scale, SCALE_MIN, SCALE_MAX, Math.min(normalized.scale, 1));" in normalize_block
    assert "normalized.mobile_offset_x = Number.isFinite(Number(source.mobile_offset_x)) ? Number(source.mobile_offset_x) : 0;" in normalize_block
    assert "normalized.mobile_offset_y = Number.isFinite(Number(source.mobile_offset_y)) ? Number(source.mobile_offset_y) : 0;" in normalize_block
    assert "normalized.position_anchor = (sourceAnchor === 'center' || sourceAnchor === 'bottom_right')" in normalize_block
    assert "? 'bottom_right' : 'center'" in normalize_block
    assert "centerPreview ? 0" not in normalize_block


def test_pngtuber_transition_guard_predicate_covers_known_transitions():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    block = source[
        source.index("isTransitionBlockingPlacementSave() {"):
        source.index("async resetModelPosition()")
    ]

    # 谓词必须覆盖全部已知转场信号；新转场在此补信号而不是再造散落守卫
    assert "window.appState.isSwitchingCatgirl" in block
    assert "window._modelReloadInFlight" in block
    assert "isMainUIHiddenByModelManager" in block


def test_pngtuber_reset_model_position_restores_default_placement():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    block = source[
        source.index("async resetModelPosition()"):
        source.index("async checkAndSwitchDisplayAfterDrag(state)")
    ]

    assert "if (this._dragState || this._touchZoomState) return;" in block
    # 转场守卫收敛为共享谓词：复位入口与保存出请求前用同一套判定
    # （角色切换 / 同角色重载 / 模型管理页持有 UI——卡面弹窗期间重载未置位）
    assert "if (this.isTransitionBlockingPlacementSave()) return;" in block
    assert block.index("if (this.isTransitionBlockingPlacementSave()) return;") < block.index(
        "if (this._dragState || this._touchZoomState) return;"
    )
    assert "this._dragSequence += 1;" in block
    assert "this.cancelEdgeSnapAnimation();" in block
    assert "this.setActiveOffsets(0, 0);" in block
    assert "this.setActiveScale(1);" in block
    assert "this.applyTransform();" in block
    assert "this.syncGlobalConfig();" in block
    # 复位强制持久化：默认键与上次完成保存相同时不得被去重早退吞掉
    assert "this._lastSavedPositionKey = '';" in block
    assert block.index("this._lastSavedPositionKey = '';") < block.index(
        "await this.saveOrStageCurrentConfig();"
    )
    assert "await this.saveOrStageCurrentConfig();" in block
    # position_anchor 是摆放偏好（居中/右下），不属于被拖走的位置，复位不改动
    assert "position_anchor" not in block


def test_pngtuber_load_invalidates_save_dedup_key_for_new_model():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    load_head = source[
        source.index("        async load(config, options = {}) {"):
        source.index("pngtuber-model-loading")
    ]

    # 去重键只记摆放字段不记角色，管理器单例跨角色复用：换模型必须清键，
    # 否则上个角色末次保存的键会挡掉新角色摆放相同的保存（复位恰是规范化摆放）
    assert "this._lastSavedPositionKey = '';" in load_head
    assert load_head.index("this._lastSavedPositionKey = '';") < load_head.index(
        "this.config = normalizedConfig;"
    )


def test_pngtuber_save_dedup_key_write_binds_to_load_generation():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    save_block = source[
        source.index("        async saveCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]

    # 在途保存横跨 load() 完成时不得恢复已清空的键（Codex P2 / CodeRabbit
    # Data Integrity）：键写回必须被加载世代守卫包住
    assert "const saveGeneration = this._loadGeneration;" in save_block
    assert "if (this._loadGeneration === saveGeneration) {" in save_block
    assert save_block.index("const saveGeneration = this._loadGeneration;") < save_block.index(
        "if (this._loadGeneration === saveGeneration) {"
    )
    # 世代校验必须在 fetch 之前拒绝过期任务（Codex P1）：同角色重载后旧快照
    # 整对象替换会还原刚选的新配置，不能只在键写回处兜底
    assert "if (this._loadGeneration !== saveGeneration) {" in save_block
    assert save_block.index("if (this._loadGeneration !== saveGeneration) {") < save_block.index(
        "const response = await fetch("
    )


def test_pngtuber_save_binds_identity_and_payload_at_enqueue():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    save_block = source[
        source.index("        async saveCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]

    # 身份与载荷必须入队时捕获、执行时复审（Codex P1）：跨类型热切换只
    # hide() 不清保存链，惰性读名会把 A 的 pngtuber 配置 PUT 到当前角色
    # 记录上（后端照写 model_type 保留字段）
    assert "const queuedName = this.resolveDirectLanlanName();" in save_block
    assert "const queuedPayload = {" in save_block
    # 身份与载荷必须在同步段捕获、链槽位同步预留（append 先于任何 await）：
    # 兜底配置的名字解析乱序完成时，先 await 后入队会让保存顺序颠倒
    assert save_block.index("const queuedPayload = {") < save_block.index(
        "const queuedName = this.resolveDirectLanlanName();"
    )
    assert save_block.index("const queuedName = this.resolveDirectLanlanName();") < save_block.index(
        "const runSave = async () => {"
    )
    assert save_block.index("const runSave = async () => {") < save_block.index(
        "if (this.resolveDirectLanlanName() !== queuedName)"
    )
    # 直读名不可用（需 /api/config 兜底）的配置在入队前就拒绝：身份无法安全
    # 绑定就不发送（CodeRabbit Major）
    assert "if (!queuedName) {" in save_block
    assert save_block.index("if (!queuedName) {") < save_block.index(
        "const runSave = async () => {"
    )
    run_save_block = save_block[save_block.index("const runSave = async () => {"):]
    # runSave 从检查到 PUT 全同步（无兜底解析 await）：无重排窗口、无检查后
    # 漂移窗口；身份在入队时刻绑定，PUT 身份即入队名
    assert "resolveCurrentLanlanName" not in run_save_block
    pre_fetch = run_save_block[:run_save_block.index("const response = await fetch(")]
    assert "await " not in pre_fetch
    assert "encodeURIComponent(queuedName)" in run_save_block
    assert "JSON.stringify(queuedPayload)" in run_save_block
    assert "(window.lanlan_config?.model_type || '').toLowerCase() !== 'pngtuber'" in run_save_block
    assert "this.resolveDirectLanlanName() !== queuedName" in run_save_block
    # 出请求前复核转场状态：转场开始于入队之后（如复位排队期间打开模型
    # 管理页并保存新包）也必须取消该笔保存
    assert "this.isTransitionBlockingPlacementSave()" in run_save_block
    assert run_save_block.index("this.isTransitionBlockingPlacementSave()") < run_save_block.index(
        "const response = await fetch("
    )
    # 执行路径不得再惰性读 config 或以重新解析的名字作为 PUT 身份
    assert "Object.assign({}, this.config)" not in run_save_block


def test_pngtuber_save_pipeline_timing_guards():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber save timing tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const source = __SOURCE_JSON__;

// 完成哨兵：未完成的 Promise 不 удерж node 事件循环，进程会以退出码 0
// 提前退出，让尾部场景的断言静默变成死代码（Codex P2 / CodeRabbit）。
// 场景全部跑完后才置位，提前退出在此大声失败。
let finished = false;
process.on('beforeExit', () => {
  if (!finished) {
    console.error('COMPLETION_SENTINEL: script exited before all scenarios finished');
    process.exitCode = 1;
  }
});

class StubElement {
  constructor(tag) {
    this.tagName = tag;
    this.style = {};
    this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    this.children = [];
  }
  appendChild(child) { this.children.push(child); return child; }
  remove() {}
  addEventListener() {}
  removeEventListener() {}
  setAttribute() {}
  removeAttribute() {}
  getAttribute() { return null; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  contains() { return false; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; }
}

function makeContext() {
  const state = { fetches: [], configApiName: 'neko' };
  let releaseGate;
  const gate = new Promise((resolve) => { releaseGate = resolve; });
  let fetchCallCount = 0;
  const window = {
    location: { pathname: '/' },
    innerWidth: 1000,
    innerHeight: 800,
    appState: { isSwitchingCatgirl: false },
    lanlan_config: { model_type: 'pngtuber', lanlan_name: 'neko' },
  };
  const document = {
    body: Object.assign(new StubElement('body'), { classList: { contains() { return false; }, toggle() {}, add() {}, remove() {} } }),
    head: new StubElement('head'),
    documentElement: new StubElement('html'),
    getElementById() { return null; },
    querySelectorAll() { return []; },
    createElement: (tag) => new StubElement(tag),
    addEventListener() {},
    removeEventListener() {},
  };
  const context = {
    console,
    performance: { now: () => 0 },
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
    Image: class { constructor() { this.style = {}; } },
    Audio: class { constructor() {} addEventListener() {} },
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts && opts.detail; } },
    fetch: async (url, options) => {
      if (String(url) === '/api/config') {
        // 名字兜底解析：受同一计数门控制（首笔 fetch 仍被拦），不计入 PUT 记录
        fetchCallCount += 1;
        if (fetchCallCount === 1) {
          await gate;
        }
        return { ok: true, json: async () => ({ lanlan_name: state.configApiName }) };
      }
      fetchCallCount += 1;
      if (fetchCallCount === 1) {
        await gate;
      }
      state.fetches.push({ url, body: options ? JSON.parse(options.body) : null });
      return { ok: true, json: async () => ({ success: true }) };
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    window,
    document,
  };
  window.addEventListener = context.addEventListener;
  window.removeEventListener = context.removeEventListener;
  window.dispatchEvent = context.dispatchEvent;
  window.CustomEvent = context.CustomEvent;
  window.Image = context.Image;
  window.setTimeout = context.setTimeout;
  window.clearTimeout = context.clearTimeout;
  document.addEventListener = context.addEventListener;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'pngtuber-core.js' });
  return { context, state, releaseGate, window };
}

const placement = (offsetX) => ({
  offset_x: offsetX,
  offset_y: 0,
  scale: 1,
  mobile_scale: 1,
  mobile_offset_x: 0,
  mobile_offset_y: 0,
  position_anchor: 'center',
  mirror: false,
});

(async () => {
  // 跨类型热切换：排队的保存不得落到切换后的 Live2D 角色记录上
  {
    const { context, state, releaseGate, window } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    const save1 = manager.saveCurrentConfig();
    await new Promise((resolve) => setTimeout(resolve, 0));
    manager.config = placement(-5);
    const save2 = manager.saveCurrentConfig();
    window.lanlan_config.model_type = 'live2d';
    window.lanlan_config.lanlan_name = 'miku';
    releaseGate();
    await save1;
    await save2;
    assert.equal(state.fetches.length, 1, 'only the in-flight A save PUTs');
    assert.ok(state.fetches[0].url.includes('/l2d/neko'), 'in-flight save goes to A');
    assert.ok(!state.fetches.some((f) => f.url.includes('miku')),
      'queued save must not reach the switched-to live2d character');
  }

  // 同类型换角色：排队的保存同样中止
  {
    const { context, state, releaseGate, window } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    const save1 = manager.saveCurrentConfig();
    await new Promise((resolve) => setTimeout(resolve, 0));
    manager.config = placement(-5);
    const save2 = manager.saveCurrentConfig();
    window.lanlan_config.lanlan_name = 'bocchi';
    releaseGate();
    await save1;
    await save2;
    assert.equal(state.fetches.length, 1, 'only the in-flight A save PUTs');
    assert.ok(!state.fetches.some((f) => f.url.includes('bocchi')),
      'queued save must not reach the switched-to pngtuber character');
  }

  // 同角色重载推进世代：排队保存的旧快照不得整对象替换还原新配置
  {
    const { context, state, releaseGate } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    const save1 = manager.saveCurrentConfig();
    await new Promise((resolve) => setTimeout(resolve, 0));
    manager.config = placement(-5);
    const save2 = manager.saveCurrentConfig();
    // 模型管理页保存新包后 handleModelReload → load()：世代推进、配置换新
    const loaded = await manager.load(Object.assign(placement(0), {
      idle_image: 'assets/neko-new-package.png',
      talking_image: 'assets/neko-new-package.png',
    }), {});
    assert.equal(loaded, true, 'same-character reload succeeded');
    releaseGate();
    await save1;
    await save2;
    assert.equal(state.fetches.length, 1, 'stale queued save must not PUT after reload');
    assert.equal(manager._lastSavedPositionKey, '',
      'stale completion must not record its pre-reload key');
    console.log('timing guards: cross-type / cross-character / stale-generation OK');
  }

  // 无切换的正常排队：两笔快照都按入队时刻落盘
  {
    const { context, state, releaseGate } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    const save1 = manager.saveCurrentConfig();
    await new Promise((resolve) => setTimeout(resolve, 0));
    manager.config = placement(-5);
    const save2 = manager.saveCurrentConfig();
    releaseGate();
    await save1;
    await save2;
    assert.equal(state.fetches.length, 2, 'both queued saves persist for the same character');
    assert.equal(state.fetches[0].body.pngtuber.offset_x, 0, 'first save snapshot');
    assert.equal(state.fetches[1].body.pngtuber.offset_x, -5, 'second save snapshot');
  }

  // 摆放相同的两个角色包：load 清键 + 复位后仍要持久化（去重键不得跨 load 泄漏）
  {
    const { context, state, releaseGate } = makeContext();
    releaseGate(); // 本场景首笔 fetch 即受门控制，先放行再等待
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    await manager.saveCurrentConfig();
    assert.equal(state.fetches.length, 1, 'A save issued');
    const loaded = await manager.load(Object.assign(placement(0), {
      idle_image: 'assets/character-b.png',
      talking_image: 'assets/character-b-talk.png',
    }), {});
    assert.equal(loaded, true, 'B loaded');
    await manager.resetModelPosition();
    assert.equal(state.fetches.length, 2,
      'B reset must persist even though saveKey matches the previous package');
    assert.equal(state.fetches[1].body.pngtuber.idle_image, 'assets/character-b.png',
      'B reset payload carries the loaded package');
  }

  // 切换进行中（isSwitchingCatgirl）：托盘复位整体跳过，不改动配置也不出请求
  {
    const { context, state, releaseGate, window } = makeContext();
    releaseGate(); // 场景末段的复位会发首笔 fetch，先放行
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(-3200);
    window.appState.isSwitchingCatgirl = true;
    await manager.resetModelPosition();
    assert.equal(manager.config.offset_x, -3200, 'switching: config untouched');
    assert.equal(state.fetches.length, 0, 'switching: no save issued');
    window.appState.isSwitchingCatgirl = false;
    await manager.resetModelPosition();
    assert.equal(manager.config.offset_x, 0, 'after switch completes: reset works');
    assert.equal(state.fetches.length, 1, 'after switch completes: save issued');
  }

  // 同角色重载进行中（_modelReloadInFlight）：托盘复位跳过，不改配置不出请求
  {
    const { context, state, window } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(-3200);
    window._modelReloadInFlight = true;
    await manager.resetModelPosition();
    assert.equal(manager.config.offset_x, -3200, 'reload window: config untouched');
    assert.equal(state.fetches.length, 0, 'reload window: no save issued');
  }

  // 复位遇在途拖拽保存：默认键与上次完成保存相同时不得被去重早退吞掉
  {
    const { context, state, releaseGate } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    const resetBaseline = manager.saveCurrentConfig();
    releaseGate(); // 基线保存的首笔 fetch 放行，得到 key = 默认摆放
    await resetBaseline;
    manager.config = placement(-5);
    const dragSave = manager.saveCurrentConfig(); // 拖拽保存排队/在途
    await manager.resetModelPosition();           // 同步段内完成去重判定
    await dragSave;
    assert.equal(state.fetches.length, 3, 'reset must persist despite matching dedup key');
    assert.equal(state.fetches[1].body.pngtuber.offset_x, -5, 'drag placement was queued');
    assert.equal(state.fetches[2].body.pngtuber.offset_x, 0, 'reset placement persisted last');
    assert.equal(manager.config.offset_x, 0, 'ui stays reset');
  }

  // 兜底配置（无直读名）：身份无法安全绑定，保存不入队（CodeRabbit Major）
  {
    const { context, state, window } = makeContext();
    window.lanlan_config = { model_type: 'pngtuber' }; // 无直读名 → 旧实现走 /api/config 兜底解析
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(0);
    const refused = await manager.saveCurrentConfig();
    assert.equal(refused, false, 'fallback-mode save refuses to enqueue');
    assert.equal(state.fetches.length, 0, 'unbindable identity must not reach any endpoint');
  }

  // 模型管理页持有 UI（保存后卡面决定弹窗期间，重载未置位）：托盘复位跳过
  {
    const { context, state, releaseGate, window } = makeContext();
    releaseGate(); // 场景末段管理页关闭后的复位会发首笔 fetch，先放行
    const manager = new context.window.PNGTuberManager();
    manager.config = placement(-3200);
    window.isMainUIHiddenByModelManager = () => true;
    await manager.resetModelPosition();
    assert.equal(manager.config.offset_x, -3200, 'manager owns ui: config untouched');
    assert.equal(state.fetches.length, 0, 'manager owns ui: no save issued');
    window.isMainUIHiddenByModelManager = () => false;
    await manager.resetModelPosition();
    assert.equal(manager.config.offset_x, 0, 'after manager closes: reset works');
    assert.equal(state.fetches.length, 1, 'after manager closes: save issued');
  }

  // 复位入队后、执行前模型管理页取得界面并保存新包：出请求前复核转场，取消排队保存
  {
    const { context, state, releaseGate, window } = makeContext();
    const manager = new context.window.PNGTuberManager();
    manager.config = Object.assign(placement(-5), { idle_image: 'assets/old-package.png' });
    const priorSave = manager.saveCurrentConfig(); // 在途保存（首笔 fetch 被门拦住）
    manager.config = Object.assign(placement(-5), { idle_image: 'assets/old-package.png' });
    const queuedReset = manager.resetModelPosition(); // 复位保存入队，排在在途保存后
    // 让出事件循环：在途保存已通过全部检查、挂到首笔 fetch 上——真实场景里
    // 转场发生在它发起之后
    await new Promise((resolve) => setTimeout(resolve, 0));
    // 复位入队与执行之间：模型管理页取得界面并保存了新包（持有 UI，
    // 卡面弹窗期间 _modelReloadInFlight 未置位）
    window.isMainUIHiddenByModelManager = () => true;
    releaseGate();
    await priorSave;
    await queuedReset;
    assert.equal(state.fetches.length, 1, 'queued reset must not PUT once the manager owns the ui');
    assert.ok(state.fetches[0].body.pngtuber.idle_image === 'assets/old-package.png',
      'only the pre-reset drag save reaches the endpoint');
    finished = true;
    console.log('timing guards: queued-reset transition recheck OK — all scenarios finished');
  }
})().catch((error) => {
  console.error('RUNTIME_CHECK_FAILED', error);
  process.exit(1);
});
"""
    script = script.replace("__SOURCE_JSON__", json.dumps(source))
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_transform_and_interactions_use_active_layout_fields():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    transform_block = source[
        source.index("applyTransform()"):
        source.index("applyScale(nextScale)")
    ]
    drag_block = source[
        source.index("        startDrag(event) {"):
        source.index("        handleClick(event) {")
    ]
    wheel_block = source[
        source.index("handleWheelZoom(event)"):
        source.index("getTouchDistance(touch1, touch2)")
    ]
    touch_block = source[
        source.index("startTouchZoom(event)"):
        source.index("async endTouchZoom()")
    ]
    save_block = source[
        source.index("async saveOrStageCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]
    runtime_save_block = source[
        source.index("async saveCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]

    assert "getActiveLayoutFields()" in transform_block
    assert "getActivePlacement()" in transform_block
    assert "const renderPlacement = this.getRenderPlacement(placement);" in transform_block
    assert "const centerAnchored = modelManagerPage || this.config.position_anchor === 'center';" in transform_block
    assert "left: '50%'" in transform_block
    assert "top: '50%'" in transform_block
    assert "left: 'calc(100% - 48px)'" in transform_block
    assert "top: 'calc(100% - 18px)'" in transform_block
    assert "'translate(-50%, -50%)'" in transform_block
    assert "'translate(-100%, -100%)'" in transform_block
    assert "renderPlacement.scale" in transform_block
    assert "renderPlacement.offsetX" in transform_block
    assert "renderPlacement.offsetY + bounce.y" in transform_block
    assert "this.config.offset_x}px" not in transform_block
    assert "this.config.scale * bounce" not in transform_block

    assert "const placement = this.getActivePlacement();" in drag_block
    assert "startOffsetX: placement.offsetX" in drag_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in drag_block
    assert "const currentScale = this.getActivePlacement().scale;" in wheel_block
    assert "this.beginModelManagerPositionEditing();" in wheel_block
    assert "window.stageModelManagerPNGTuberPlacement(this.config);" in wheel_block
    assert "initialScale: placement.scale" in touch_block
    assert "this.beginModelManagerPositionEditing();" in touch_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in touch_block
    assert "window.stageModelManagerPNGTuberPlacement(this.config);" in save_block
    assert "this.config.mobile_offset_x" in runtime_save_block
    assert "this.config.mobile_offset_y" in runtime_save_block
    assert "this.config.mobile_scale" in runtime_save_block
    assert "this.config.position_anchor" in runtime_save_block
    assert "apply_runtime: false" in runtime_save_block


def test_pngtuber_drag_uses_the_shared_multiscreen_transfer_contract():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    drag_block = source[
        source.index("        setModelDraggingState(active, moved = false) {"):
        source.index("        handleClick(event) {")
    ]

    assert "this._isDraggingModel = dragging;" in drag_block
    assert "this.image.setAttribute('data-dragging', moved ? 'true' : 'pending');" in drag_block
    assert "this.rememberDragScreenPoint(this._dragState, event, { start: true });" in drag_block
    assert "this.rememberDragScreenPoint(state, event);" in drag_block
    assert "void this.recordDragHintPointerEdgeApproach(state);" in drag_block
    assert "const displaySwitched = await this.checkAndSwitchDisplayAfterDrag(state);" in drag_block
    assert "await this.recordDragHintPointerEdgeRelease(state);" in drag_block
    assert "bridge.getAllDisplays()" in drag_block
    assert "bridge.getCurrentDisplay()" in drag_block
    assert "bridge.getDesktopCoordinateSnapshot()" in drag_block
    assert "coordinateSnapshot?.renderer?.screenOrigin" in drag_block
    assert "state?.dragHintApproachPending" in drag_block
    assert "state.dragHintApproachPending = true;" in drag_block
    assert "state.dragHintApproachPending = false;" in drag_block
    assert "this.isDragCompletionCurrent(state)" in drag_block
    assert "this.beginModelManagerPositionEditing();" in drag_block
    assert "bridge.moveWindowToDisplay(switchScreenX, switchScreenY)" in drag_block
    assert "result.windowBounds" in drag_block
    assert "this.moveModelCenterToWindowPoint(desiredCenterX, desiredCenterY);" in drag_block
    assert "helper.markDisplaySwitchSuccess('pngtuber');" in drag_block
    assert "await this.snapModelIntoScreen({ animate: true });" in drag_block
    # endDrag 内部的顺序契约单独切片锚定，避免被块内其他方法的同名调用干扰
    end_drag_block = source[
        source.index("        async endDrag(event) {"):
        source.index("        handleClick(event) {")
    ]
    assert "await this.recordDragHintPointerEdgeRelease(state);" in end_drag_block
    assert end_drag_block.index("await this.recordDragHintPointerEdgeRelease(state);") < end_drag_block.index(
        "await this.snapModelIntoScreen({ animate: true });"
    )
    assert end_drag_block.index("await this.snapModelIntoScreen({ animate: true });") < end_drag_block.index(
        "await this.saveOrStageCurrentConfig();"
    )
    touch_end_block = source[
        source.index("        async endTouchZoom() {"):
        source.index("        setupHTMLLockIcon()")
    ]
    assert "await this.snapModelIntoScreen({ animate: true });" in touch_end_block
    assert "if (!this.isDragCompletionCurrent(state)) return;" in touch_end_block
    assert touch_end_block.index("if (!this.isDragCompletionCurrent(state)) return;") < touch_end_block.index(
        "await this.snapModelIntoScreen({ animate: true });"
    )
    assert touch_end_block.index("await this.snapModelIntoScreen({ animate: true });") < touch_end_block.index(
        "await this.saveOrStageCurrentConfig();"
    )


def test_pngtuber_drag_snaps_back_when_less_than_200_pixels_remain_visible():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber edge snap tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

let nextFrameId = 0;
const frames = new Map();
const requestAnimationFrame = (callback) => {{
  const frameId = ++nextFrameId;
  frames.set(frameId, callback);
  return frameId;
}};
const cancelAnimationFrame = (frameId) => {{ frames.delete(frameId); }};
const runNextFrame = (timestamp) => {{
  const next = frames.entries().next();
  assert.equal(next.done, false, 'expected a pending animation frame');
  const [frameId, callback] = next.value;
  frames.delete(frameId);
  callback(timestamp);
}};
let stagedPlacement = null;
let modelWidth = 400;
const window = {{
  location: {{ pathname: '/' }},
  innerWidth: 1000,
  innerHeight: 800,
  lanlan_config: {{ model_type: 'pngtuber' }},
  stageModelManagerPNGTuberPlacement(config) {{ stagedPlacement = {{ ...config }}; }},
}};
const document = {{
  body: {{ classList: {{ contains() {{ return false; }}, toggle() {{}} }} }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
const context = {{
  cancelAnimationFrame,
  console,
  document,
  performance: {{ now: () => 0 }},
  requestAnimationFrame,
  window,
}};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

const manager = new window.PNGTuberManager();
manager.config = {{
  scale: 1,
  offset_x: 0,
  offset_y: 0,
  mobile_scale: 1,
  mobile_offset_x: 0,
  mobile_offset_y: 0,
  position_anchor: 'center',
  mirror: false,
}};
manager.image = {{
  classList: {{ toggle() {{}} }},
  closest() {{ return null; }},
  setAttribute() {{}},
  removeAttribute() {{}},
  setPointerCapture() {{}},
  getBoundingClientRect() {{
    const placement = manager.getActivePlacement();
    const width = modelWidth * placement.scale;
    const height = 400 * placement.scale;
    const centerX = window.innerWidth / 2 + placement.offsetX;
    const centerY = window.innerHeight / 2 + placement.offsetY;
    return {{ left: centerX - width / 2, top: centerY - height / 2, width, height }};
  }},
}};
manager.applyTransform = () => {{}};
manager.isLayeredActive = () => false;
manager.resetLayeredDragVelocity = () => {{}};
manager.showDragImage = () => {{}};
manager.restoreStateImage = () => {{}};
manager.restartLayeredAnimationLoop = () => {{}};
manager.syncGlobalConfig = () => {{}};
manager.updateFloatingButtonsPosition = () => {{}};
manager.updateLockIconPosition = () => {{}};
let scheduledSaves = 0;
manager.scheduleSaveCurrentConfig = () => {{ scheduledSaves += 1; }};

(async () => {{
  manager.config.offset_x = -750;
  let target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetX, -500);
  assert.equal(target.offsetY, 0);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_x, -500);

  // Exactly 200 px remains visible, so ordinary edge placement is preserved.
  assert.equal(manager.getEdgeSnapTarget(), null);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), false);

  manager.config.offset_x = 750;
  target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetX, 500);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_x, 500);

  manager.config.offset_x = 0;
  manager.config.offset_y = -650;
  target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetY, -400);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_y, -400);

  manager.config.offset_y = 650;
  target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetY, 400);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_y, 400);

  manager.config.offset_x = -750;
  manager.config.offset_y = 0;
  const animatedSnap = manager.snapModelIntoScreen();
  assert.equal(frames.size, 1);
  runNextFrame(0);
  runNextFrame(130);
  assert.ok(manager.config.offset_x > -500, 'easeOutBack should briefly overshoot the target');
  runNextFrame(260);
  assert.equal(await animatedSnap, true);
  assert.equal(manager.config.offset_x, -500);
  assert.equal(manager.config.offset_y, 0);

  // Layered-canvas padding is transparent and does not count as visible avatar content.
  manager.isLayeredActive = () => true;
  manager.layeredCanvasPadding = 100;
  manager.layeredCanvasLogicalWidth = 600;
  manager.layeredCanvasLogicalHeight = 600;
  manager.config.offset_x = -700;
  const layeredTarget = manager.getEdgeSnapTarget();
  assert.ok(Math.abs(layeredTarget.offsetX - (-433.3333333333333)) < 0.001);
  manager.isLayeredActive = () => false;

  // Content smaller than 200 px stops once it is fully visible.
  modelWidth = 100;
  manager.config.offset_x = -480;
  const smallTarget = manager.getEdgeSnapTarget();
  assert.equal(smallTarget.offsetX, -450);
  modelWidth = 400;

  // State image geometry changes retarget an in-flight rebound.
  manager.config.offset_x = -750;
  modelWidth = 400;
  const resizedImageSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  modelWidth = 200;
  runNextFrame(130);
  runNextFrame(260);
  assert.equal(await resizedImageSnap, true);
  assert.equal(manager.config.offset_x, -400);

  // A size change during ease-out-back overshoot still replaces the stale target.
  manager.config.offset_x = -750;
  modelWidth = 400;
  const overshootResizeSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  runNextFrame(130);
  modelWidth = 370;
  runNextFrame(260);
  assert.equal(await overshootResizeSnap, true);
  assert.ok(Math.abs(manager.config.offset_x - (-485)) < 0.001);
  modelWidth = 400;

  // Growing content cannot reverse the snap target past its starting offset.
  manager.config.offset_x = -750;
  const growingImageSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  modelWidth = 2000;
  runNextFrame(130);
  runNextFrame(260);
  assert.equal(await growingImageSnap, true);
  assert.equal(manager.config.offset_x, -500);
  modelWidth = 400;

  // Crossing the responsive breakpoint restarts against the new layout fields.
  manager.config.offset_x = -750;
  manager.config.mobile_offset_x = -500;
  const responsiveSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  window.innerWidth = 600;
  runNextFrame(130);
  runNextFrame(130);
  runNextFrame(260);
  assert.equal(await responsiveSnap, true);
  assert.equal(manager.config.offset_x, -750);
  assert.equal(manager.config.mobile_offset_x, -300);
  window.innerWidth = 1000;
  manager.config.mobile_offset_x = 0;

  // Wheel input clamped at the scale boundary leaves the active rebound untouched.
  manager.config.scale = 0.1;
  manager.config.offset_x = -750;
  const minScaleWheelSnap = manager.snapModelIntoScreen();
  const sequenceBeforeClampedWheel = manager._dragSequence;
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager._dragSequence, sequenceBeforeClampedWheel);
  assert.equal(frames.size, 1, 'clamped wheel input must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await minScaleWheelSnap, true);
  manager.config.scale = 1;

  // Wheel zoom cancels the old rebound and targets the resized model geometry.
  manager.config.offset_x = -750;
  const preZoomSnap = manager.snapModelIntoScreen();
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(await preZoomSnap, false);
  assert.equal(frames.size, 1);
  runNextFrame(0);
  runNextFrame(260);
  await Promise.resolve();
  const resizedBounds = manager.image.getBoundingClientRect();
  const resizedRight = resizedBounds.left + resizedBounds.width;
  assert.ok(Math.abs(resizedRight - 200) < 0.001);
  assert.equal(scheduledSaves, 1);

  // An unchanged two-finger gesture leaves the active rebound untouched.
  manager.config.scale = 1;
  manager.config.offset_x = -750;
  const untouchedSnap = manager.snapModelIntoScreen();
  manager.startTouchZoom({{
    touches: [{{ clientX: 100, clientY: 100 }}, {{ clientX: 200, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(frames.size, 1);
  await manager.endTouchZoom();
  assert.equal(frames.size, 1, 'unchanged touch must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await untouchedSnap, true);
  assert.equal(manager.config.offset_x, -500);

  // A pinch clamped at the scale boundary with no pan also preserves the rebound.
  manager.config.scale = 5;
  manager.config.offset_x = -3000;
  const maxScaleTouchSnap = manager.snapModelIntoScreen();
  const sequenceBeforeClampedTouch = manager._dragSequence;
  manager.startTouchZoom({{
    touches: [{{ clientX: 100, clientY: 100 }}, {{ clientX: 200, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  manager.moveTouchZoom({{
    touches: [{{ clientX: 90, clientY: 100 }}, {{ clientX: 210, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager._touchZoomState.changed, false);
  assert.equal(manager._dragSequence, sequenceBeforeClampedTouch);
  await manager.endTouchZoom();
  assert.equal(frames.size, 1, 'clamped touch scale must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await maxScaleTouchSnap, true);
  manager.config.scale = 1;

  // A rebound that finishes during a pending click refreshes the first drag grab offset.
  manager.config.offset_x = -750;
  const completedPendingSnap = manager.snapModelIntoScreen();
  manager.startDrag({{
    target: manager.image,
    button: 0,
    pointerId: 8,
    clientX: 100,
    clientY: 100,
    screenX: 100,
    screenY: 100,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await completedPendingSnap, true);
  manager.moveDrag({{
    pointerId: 8,
    clientX: 110,
    clientY: 100,
    screenX: 110,
    screenY: 100,
    preventDefault() {{}},
  }});
  const centerAfterPendingCompletionMove = manager.getModelCenterInWindow();
  assert.equal(manager._dragState.modelCenterPointerOffset.x, centerAfterPendingCompletionMove.x - 110);
  assert.equal(manager._dragState.modelCenterPointerOffset.y, centerAfterPendingCompletionMove.y - 100);
  manager._dragState = null;

  // Wheel input cannot steal ownership from an active two-finger interaction.
  manager.config.scale = 1;
  manager.config.offset_x = 0;
  manager.config.offset_y = 0;
  manager.startTouchZoom({{
    touches: [{{ clientX: 100, clientY: 100 }}, {{ clientX: 200, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  manager.moveTouchZoom({{
    touches: [{{ clientX: 110, clientY: 100 }}, {{ clientX: 210, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  const activeTouchSequence = manager._dragSequence;
  const activeTouchState = manager._touchZoomState;
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager._dragSequence, activeTouchSequence);
  assert.equal(manager._touchZoomState, activeTouchState);
  assert.equal(manager.config.scale, 1);
  manager.moveTouchZoom({{
    touches: [{{ clientX: 120, clientY: 100 }}, {{ clientX: 220, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager.config.offset_x, 20);
  await manager.endTouchZoom();
  assert.equal(manager._touchZoomState, null);

  // Model-manager wheel replacement stages the final snapped placement.
  window.location.pathname = '/model_manager';
  manager._modelManagerUseCurrentPlacement = true;
  manager.config.offset_x = -750;
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  runNextFrame(0);
  runNextFrame(260);
  await Promise.resolve();
  assert.ok(stagedPlacement);
  assert.equal(stagedPlacement.offset_x, manager.config.offset_x);
  assert.equal(scheduledSaves, 1, 'model-manager placement must be staged instead of auto-saved');
  window.location.pathname = '/';

  manager.config.offset_x = -750;
  const cancelledSnap = manager.snapModelIntoScreen();
  assert.equal(frames.size, 1);
  manager.startDrag({{
    target: manager.image,
    button: 0,
    pointerId: 9,
    clientX: 100,
    clientY: 100,
    screenX: 100,
    screenY: 100,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(frames.size, 1, 'pointerdown alone must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(130);
  manager.moveDrag({{
    pointerId: 9,
    clientX: 110,
    clientY: 100,
    screenX: 110,
    screenY: 100,
    preventDefault() {{}},
  }});
  const centerAfterTakeover = manager.getModelCenterInWindow();
  assert.equal(manager._dragState.modelCenterPointerOffset.x, centerAfterTakeover.x - 110);
  assert.equal(manager._dragState.modelCenterPointerOffset.y, centerAfterTakeover.y - 100);
  manager.setActiveOffsets(-123, 45);
  assert.equal(await cancelledSnap, false);
  assert.equal(frames.size, 0);
  assert.equal(manager.config.offset_x, -123);
  assert.equal(manager.config.offset_y, 45);
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_drag_hint_edge_approach_allows_only_one_in_flight_call():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber drag hint tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

let approachCalls = 0;
let resolveApproach;
const window = {{
  location: {{ pathname: '/' }},
  lanlan_config: {{ model_type: 'pngtuber' }},
  NekoAvatarMultiScreenDragHint: {{
    recordPointerEdgeApproach() {{
      approachCalls += 1;
      return new Promise((resolve) => {{ resolveApproach = resolve; }});
    }},
  }},
}};
const document = {{
  body: {{ classList: {{ contains() {{ return false; }} }} }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
const context = {{ console, document, window }};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

(async () => {{
  const manager = new window.PNGTuberManager();
  const state = {{
    dragHintStartPointer: {{ x: 10, y: 20, startedAt: 1 }},
    dragHintLastPointer: {{ x: 30, y: 40 }},
    dragHintApproachShown: false,
    dragHintApproachPending: false,
  }};
  const first = manager.recordDragHintPointerEdgeApproach(state);
  const second = manager.recordDragHintPointerEdgeApproach(state);

  assert.equal(approachCalls, 1);
  assert.equal(await second, false);
  resolveApproach(true);
  assert.equal(await first, true);
  assert.equal(state.dragHintApproachPending, false);
  assert.equal(state.dragHintApproachShown, true);
  assert.equal(await manager.recordDragHintPointerEdgeApproach(state), false);
  assert.equal(approachCalls, 1);
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_drag_and_pinch_share_the_animation_clock():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber animation tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = "const source = " + json.dumps(source) + ";\n" + r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
let nextId = 0;
const frames = new Map(), timers = new Map();
const window = { location: { pathname: '/' }, innerWidth: 1000,
  lanlan_config: { model_type: 'pngtuber' } };
const document = { body: { classList: { contains: () => false } }, getElementById: () => null };
vm.runInNewContext(source, { window, document, console, performance: { now: () => 100 },
  requestAnimationFrame: fn => { frames.set(++nextId, fn); return nextId; },
  cancelAnimationFrame: id => frames.delete(id),
  setTimeout: fn => { timers.set(++nextId, fn); return nextId; },
  clearTimeout: id => timers.delete(id),
});
const tick = () => {
  const [id, fn] = frames.entries().next().value;
  frames.delete(id);
  fn(100);
};
const pointer = (x, y, pointerType) => ({ pointerId: 1, button: 0, pointerType,
  clientX: x, clientY: y, preventDefault() {}, stopPropagation() {} });
const pinch = (x, distance) => ({ touches: [
  { clientX: x - distance / 2, clientY: 200 },
  { clientX: x + distance / 2, clientY: 200 },
], preventDefault() {}, stopPropagation() {} });

(async () => {
  for (const gesture of ['mouse', 'touch', 'pinch']) {
    const manager = new window.PNGTuberManager();
    manager.config = { scale: 1, offset_x: 20, offset_y: 30 };
    for (const name of ['applyTransform', 'rememberDragScreenPoint', 'rememberModelCenterPointerOffset',
      'setModelDraggingState', 'resetLayeredDragVelocity', 'startLayeredBreathingLoop',
      'showDragImage', 'restoreStateImage', 'updateLockIconPosition']) manager[name] = () => {};
    for (const name of ['layeredPointerNeedsFrame', 'layeredPhysicsNeedsFrame',
      '_layeredStateHasFastSheetAnimation', 'checkAndSwitchDisplayAfterDrag']) manager[name] = () => false;
    for (const name of ['recordDragHintPointerEdgeApproach', 'recordDragHintPointerEdgeRelease',
      'snapModelIntoScreen']) manager[name] = async () => {};
    let saved;
    manager.saveOrStageCurrentConfig = async () => { saved = { ...manager.config }; };
    let layered = true, motion = true;
    manager.isLayeredActive = () => layered;
    manager.hasMotionLayersForCurrentState = () => motion;
    const drawn = [];
    manager.drawLayeredState = () => drawn.push({ ...manager.config });

    // Begin with an existing idle timer, then wake it once for an input burst.
    manager.startLayeredAnimationLoop();
    tick();
    assert.equal(timers.size, 1);
    const timeline = manager.layeredAnimationStart;
    drawn.length = 0;
    if (gesture === 'pinch') manager.startTouchZoom(pinch(100, 100));
    else manager.startDrag(pointer(100, 200, gesture));
    for (let i = 1; i <= 100; i++) {
      if (gesture === 'pinch') manager.moveTouchZoom(pinch(100 + i, 100 + i));
      else manager.moveDrag(pointer(100 + i, 200 + i, gesture));
    }
    assert.equal(manager.config.offset_x, 120, 'placement updates before the next frame');
    assert.equal(manager.config.offset_y, gesture === 'pinch' ? 30 : 130);
    assert.equal(manager.config.scale, gesture === 'pinch' ? 2 : 1);
    assert.equal(drawn.length, 0, 'input bursts must not draw synchronously');
    assert.equal(timers.size, 0, 'dragging upgrades the idle timer to rAF');
    assert.equal(frames.size, 1, 'all moves share the existing animation loop');
    assert.equal(manager.layeredAnimationStart, timeline);
    tick();
    assert.equal(drawn.length, 1);
    assert.equal(drawn[0].offset_x, 120);
    assert.equal(frames.size, 1);

    // Releasing before the next frame must still save the final position/scale.
    if (gesture === 'pinch') {
      manager.moveTouchZoom(pinch(210, 210));
      await manager.endTouchZoom();
    } else {
      manager.moveDrag(pointer(210, 310, gesture));
      await manager.endDrag(pointer(210, 310, gesture));
    }
    assert.equal(saved.offset_x, 130);
    assert.equal(saved.scale, gesture === 'pinch' ? 2.1 : 1);
    assert.equal(frames.size, 1, 'release leaves only one animation loop');
    manager.stopLayeredAnimationLoop();

    // Static layered canvases and ordinary PNGs move via their CSS transform.
    motion = false;
    for (layered of [true, false]) {
      manager.startDrag(pointer(100, 200, 'mouse'));
      manager.moveDrag(pointer(120, 220, 'mouse'));
      assert.equal(frames.size, 0);
      await manager.endDrag(pointer(120, 220, 'mouse'));
    }
    assert.equal(timers.size, 0);
  }
})().catch(error => { console.error(error); process.exit(1); });
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_drag_switches_to_the_pointer_display_without_losing_the_grab_point():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber multiscreen drag tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const bodyClasses = new Set();
const bodyClassList = {{
  contains(name) {{ return bodyClasses.has(name); }},
  add(name) {{ bodyClasses.add(name); }},
  remove(name) {{ bodyClasses.delete(name); }},
  toggle(name, active) {{
    if (active) bodyClasses.add(name);
    else bodyClasses.delete(name);
  }},
}};
const document = {{
  body: {{ classList: bodyClassList }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};

const primary = {{ id: 'primary', screenX: 0, screenY: 0, width: 1707, height: 1067 }};
const secondary = {{ id: 'secondary', screenX: -2560, screenY: 0, width: 2560, height: 1440 }};
const primaryWindowBounds = {{ x: 1, y: 1, width: 1706, height: 1066 }};
const secondaryWindowBounds = {{ x: -2559, y: 1, width: 2559, height: 1439 }};
let currentDisplay = primary;
let currentWindowBounds = {{ ...primaryWindowBounds }};
let movedPoint = null;
let saves = 0;
let snapshotCalls = 0;
let deferredFrames = null;
const markedSwitches = [];

const requestAnimationFrame = (callback) => {{
  if (deferredFrames) {{
    deferredFrames.push(callback);
    return deferredFrames.length;
  }}
  callback(0);
  return 1;
}};
const window = {{
  location: {{ pathname: '/' }},
  innerWidth: primaryWindowBounds.width,
  innerHeight: primaryWindowBounds.height,
  __LANLAN_IS_ELECTRON_PET__: true,
  lanlan_config: {{ model_type: 'pngtuber' }},
  requestAnimationFrame,
  electronScreen: {{
    async getAllDisplays() {{ return [primary, secondary]; }},
    async getCurrentDisplay() {{ return currentDisplay; }},
    async getDesktopCoordinateSnapshot() {{
      snapshotCalls += 1;
      return {{
        version: 2,
        window: {{ actualBounds: {{ ...currentWindowBounds }} }},
        renderer: {{ screenOrigin: {{ x: currentWindowBounds.x, y: currentWindowBounds.y }} }},
      }};
    }},
    async moveWindowToDisplay(x, y) {{
      movedPoint = {{ x, y }};
      currentDisplay = secondary;
      currentWindowBounds = {{ ...secondaryWindowBounds }};
      window.innerWidth = currentWindowBounds.width;
      window.innerHeight = currentWindowBounds.height;
      return {{
        success: true,
        sameDisplay: false,
        windowBounds: {{ ...currentWindowBounds }},
      }};
    }},
  }},
  NekoAvatarMultiScreenDragHint: {{
    async recordPointerEdgeApproach() {{ return false; }},
    async recordPointerEdgeRelease() {{ return false; }},
    markDisplaySwitchSuccess(source) {{ markedSwitches.push(source); }},
  }},
}};

const context = {{
  console,
  document,
  window,
  performance: {{ now: () => 0 }},
  requestAnimationFrame,
}};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

let manager = new window.PNGTuberManager();
const attributes = new Map();
const imageClasses = new Set();
const image = {{
  style: {{}},
  classList: {{
    toggle(name, active) {{
      if (active) imageClasses.add(name);
      else imageClasses.delete(name);
    }},
  }},
  closest() {{ return null; }},
  setAttribute(name, value) {{ attributes.set(name, String(value)); }},
  removeAttribute(name) {{ attributes.delete(name); }},
  setPointerCapture() {{}},
  releasePointerCapture() {{}},
  getBoundingClientRect() {{
    const centerX = window.innerWidth / 2 + manager.config.offset_x;
    const centerY = window.innerHeight / 2 + manager.config.offset_y;
    return {{ left: centerX - 100, top: centerY - 100, width: 200, height: 200 }};
  }},
}};
manager.image = image;
manager.container = {{ style: {{}} }};
manager.config = {{
  scale: 1,
  offset_x: -700,
  offset_y: 0,
  mobile_scale: 1,
  mobile_offset_x: 0,
  mobile_offset_y: 0,
  position_anchor: 'center',
  mirror: false,
}};
manager.resetLayeredDragVelocity = () => {{}};
manager.isLayeredActive = () => false;
manager.showDragImage = () => {{}};
manager.restoreStateImage = () => {{}};
manager.restartLayeredAnimationLoop = () => {{}};
manager.applyTransform = () => {{}};
manager.syncGlobalConfig = () => {{}};
manager.updateFloatingButtonsPosition = () => {{}};
manager.updateLockIconPosition = () => {{}};
manager.saveCurrentConfig = async () => {{ saves += 1; return true; }};

function pointer(type, clientX, clientY, screenX, screenY, pointerId = 7) {{
  return {{
    type,
    target: image,
    button: 0,
    pointerId,
    clientX,
    clientY,
    screenX,
    screenY,
    preventDefault() {{}},
    stopPropagation() {{}},
  }};
}}

(async () => {{
  manager.startDrag(pointer('pointerdown', 154, 534, 155, 535));
  assert.equal(manager._isDraggingModel, true);
  assert.equal(attributes.get('data-dragging'), 'pending');

  manager.moveDrag(pointer('pointermove', -201, 534, -200, 535));
  assert.equal(attributes.get('data-dragging'), 'true');

  await manager.endDrag(pointer('pointerup', -201, 534, -200, 535));

  assert.deepEqual(movedPoint, {{ x: -200, y: 535 }});
  assert.equal(currentDisplay.id, 'secondary');
  // -200 - secondary origin(-2559) + grab offset(-1) = 2358.
  assert.equal(manager.getModelCenterInWindow().x, 2358);
  // 535 - secondary origin(1) + grab offset(-1) = 533.
  assert.equal(manager.getModelCenterInWindow().y, 533);
  // 2358 - secondary window center(2559 / 2) = 1078.5.
  assert.equal(manager.config.offset_x, 1078.5);
  // 533 - secondary window center(1439 / 2) = -186.5.
  assert.equal(manager.config.offset_y, -186.5);
  assert.equal(saves, 1);
  assert.ok(snapshotCalls >= 1);
  assert.deepEqual(markedSwitches, ['pngtuber']);
  assert.equal(manager._isDraggingModel, false);
  assert.equal(attributes.has('data-dragging'), false);
  assert.equal(bodyClasses.has('neko-model-dragging'), false);

  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = currentWindowBounds.width;
  window.innerHeight = currentWindowBounds.height;
  manager.config.offset_x = -855;
  manager.config.offset_y = 0;
  movedPoint = null;
  manager._dragSequence += 1;
  const modelOnlyState = {{
    dragSequence: manager._dragSequence,
    lastScreenPoint: null,
    modelCenterPointerOffset: {{ x: 0, y: 0 }},
  }};
  assert.equal(await manager.checkAndSwitchDisplayAfterDrag(modelOnlyState), true);
  // Primary actual origin(1) + local model center(-2) = screen x(-1).
  assert.deepEqual(movedPoint, {{ x: -1, y: 534 }});

  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = Number.NaN;
  window.innerHeight = currentWindowBounds.height;
  movedPoint = null;
  const getModelCenterInWindow = manager.getModelCenterInWindow.bind(manager);
  manager.getModelCenterInWindow = () => ({{ x: 100, y: 100 }});
  manager._dragSequence += 1;
  const invalidWindowState = {{
    dragSequence: manager._dragSequence,
    lastScreenPoint: {{ x: -200, y: 100 }},
    modelCenterPointerOffset: {{ x: 0, y: 0 }},
  }};
  assert.equal(await manager.checkAndSwitchDisplayAfterDrag(invalidWindowState), false);
  assert.equal(movedPoint, null);
  manager.getModelCenterInWindow = getModelCenterInWindow;

  // A second pointerdown without movement must not invalidate the prior completion.
  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = currentWindowBounds.width;
  window.innerHeight = currentWindowBounds.height;
  manager.config.offset_x = -700;
  manager.config.offset_y = 0;
  movedPoint = null;
  saves = 0;
  deferredFrames = [];
  manager.startDrag(pointer('pointerdown', 154, 534, 155, 535));
  manager.moveDrag(pointer('pointermove', -201, 534, -200, 535));
  const pendingClickEnd = manager.endDrag(pointer('pointerup', -201, 534, -200, 535));
  while (deferredFrames.length === 0) await new Promise(setImmediate);
  manager.startDrag(pointer('pointerdown', 100, 100, -2459, 101, 8));
  deferredFrames.shift()(0);
  await new Promise(setImmediate);
  deferredFrames.shift()(0);
  await pendingClickEnd;
  assert.equal(manager._dragState.pointerId, 8);
  assert.equal(manager._dragState.dragSequence, null);
  assert.equal(saves, 1);
  await manager.endDrag(pointer('pointerup', 100, 100, -2459, 101, 8));

  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = currentWindowBounds.width;
  window.innerHeight = currentWindowBounds.height;
  manager.config.offset_x = -700;
  manager.config.offset_y = 0;
  movedPoint = null;
  saves = 0;
  deferredFrames = [];
  manager.startDrag(pointer('pointerdown', 154, 534, 155, 535));
  manager.moveDrag(pointer('pointermove', -201, 534, -200, 535));
  const staleEnd = manager.endDrag(pointer('pointerup', -201, 534, -200, 535));
  while (deferredFrames.length === 0) await new Promise(setImmediate);

  manager.startDrag(pointer('pointerdown', 100, 100, -2459, 101, 8));
  manager.moveDrag(pointer('pointermove', 120, 100, -2439, 101, 8));
  const activeDragOffsets = {{
    x: manager.config.offset_x,
    y: manager.config.offset_y,
  }};
  deferredFrames.shift()(0);
  await new Promise(setImmediate);
  deferredFrames.shift()(0);
  await staleEnd;

  assert.equal(manager._dragState.pointerId, 8);
  assert.equal(manager.config.offset_x, activeDragOffsets.x);
  assert.equal(manager.config.offset_y, activeDragOffsets.y);
  assert.equal(saves, 0);
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_model_manager_preview_centering_does_not_mutate_saved_offsets():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    render_block = source[
        source.index("getRenderPlacement(placement) {"):
        source.index("        setActiveScale(nextScale)")
    ]

    assert "isModelManagerPage()" in render_block
    assert "!this.config.preserve_model_manager_position" in render_block
    assert "offsetX: 0" in render_block
    assert "offsetY: 0" in render_block
    assert "this.config.offset_x = 0" not in source
    assert "this.config.offset_y = 0" not in source
    assert "this.config.mobile_offset_x = 0" not in source
    assert "this.config.mobile_offset_y = 0" not in source


def test_pngtuber_container_pointer_events_stay_passthrough_outside_model_manager():
    core_source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    interpage_source = read_js_parts(APP_INTERPAGE_PATH)
    app_ui_source = read_js_parts(APP_UI_PATH)
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    css_container_block = css_source[
        css_source.index("#pngtuber-container {"):
        css_source.index("#pngtuber-container.minimized")
    ]
    css_image_block = css_source[
        css_source.index("#pngtuber-container .pngtuber-image {"):
        css_source.index("#pngtuber-container .pngtuber-image.is-dragging")
    ]
    assert "pointer-events: none;" in css_container_block
    assert "pointer-events: auto;" in css_image_block
    assert "this.container.style.pointerEvents = modelManagerPage ? 'auto' : 'none';" in core_source
    assert "this.container.style.pointerEvents = isModelManagerPage() ? 'auto' : 'none';" in core_source

    assert "restoredPngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "restoredPngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');" in app_ui_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'auto', 'important');" not in app_ui_source


def test_pngtuber_mouth_flap_does_not_restart_layered_motion_timeline():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    set_state_block = source[
        source.index("setState(state"):
        source.index("        currentRemixStateSettings()")
    ]
    animation_loop_block = source[
        source.index("startLayeredAnimationLoop(options = {})"):
        source.index("        motionValue(")
    ]
    schedule_block = source[
        source.index("scheduleSpeakingMouthFrame()"):
        source.index("        startSpeakingMouthAnimation()")
    ]
    start_block = source[
        source.index("        startSpeakingMouthAnimation() {"):
        source.index("        stopSpeakingMouthAnimation()")
    ]

    assert "restartLayeredAnimation !== false" in set_state_block
    assert source.count("this.layeredAnimationStart = performance.now();") == 1
    assert "this.layeredAnimationStart = performance.now();" in animation_loop_block
    assert "if (!options.preserveTimeline || !this.layeredAnimationStart)" in animation_loop_block
    assert "layeredAnimationStart = performance.now()" not in set_state_block
    assert "layeredAnimationStart = performance.now()" not in schedule_block
    assert "layeredAnimationStart = performance.now()" not in start_block
    assert set_state_block.count("this.restartLayeredAnimationLoop();") == 1
    assert (
        "if (options.restartLayeredAnimation !== false) {\n"
        "                    this.restartLayeredAnimationLoop();\n"
        "                } else if (!this.layeredAnimationFrame && this.hasMotionLayersForCurrentState()) {\n"
        "                    this.startLayeredAnimationLoop({ preserveTimeline: true });\n"
        "                }"
    ) in set_state_block
    assert "this.restartLayeredAnimationLoop();" not in schedule_block
    assert "this.restartLayeredAnimationLoop();" not in start_block
    assert "this.setState(this.speakingMouthOpen ? 'talking' : 'idle', { restartLayeredAnimation: false });" in schedule_block
    assert "this.setState('talking', { restartLayeredAnimation: false });" in start_block


def test_layered_pngtuber_speaking_bounce_does_not_transform_whole_canvas():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    bounce_config_block = source[
        source.index("speakingBounceConfig()"):
        source.index("        currentSpeakingBounceTransform(")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]

    assert "if (this.isLayeredActive()) return null;" in bounce_config_block
    assert "const bounce = this.currentSpeakingBounceTransform();" in apply_transform_block
    assert "this.image.style.transform" in apply_transform_block


def test_layered_pngtuber_motion_requires_explicit_runtime_feature_flags():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    feature_block = source[
        source.index("layeredRuntimeFeatureEnabled("):
        source.index("        stateHasMotion(")
    ]
    state_motion_block = source[
        source.index("stateHasMotion(layerState)"):
        source.index("        stateFrameInfo(")
    ]
    current_motion_block = source[
        source.index("hasMotionLayersForCurrentState("):
        source.index("        startLayeredAnimationLoop(")
    ]
    frame_block = source[
        source.index("stateFrameInfo(layer, layerState, img"):
        source.index("        stateHasFrameAnimation(")
    ]
    draw_block = source[
        source.index("drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "return features[featureName] === true;" in feature_block
    assert "hasMotionLayersForCurrentState(stateName = this.state || 'idle')" in current_motion_block
    assert "this.shouldRenderLayer(layer, stateName)" in current_motion_block
    assert "this.layeredRuntimeFeatureEnabled('layer_motion')" in state_motion_block
    assert "this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')" in state_motion_block
    assert "this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')" in frame_block
    assert "const layerMotionEnabled = this.layeredRuntimeFeatureEnabled('layer_motion');" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.xAmp, layerState.xFrq" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.yAmp, layerState.yFrq" in draw_block
    assert "const wiggleDegrees = layerMotionEnabled" in draw_block
    assert "this.motionValue(layerState.wiggle_amp, layerState.wiggle_freq || layerState.rot_frq" in draw_block


def test_layered_pngtuber_caps_render_resolution_without_changing_logical_coordinates():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    setup_block = source[
        source.index("        async setupLayeredAdapter(options = {})"):
        source.index("        hasBlinkLayers()")
    ]
    pointer_block = source[
        source.index("        layeredPointerForLayer("):
        source.index("        layeredPointerNeedsFrame(")
    ]
    draw_block = source[
        source.index("        drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "const PNGTUBER_LAYERED_CANVAS_MAX_RENDER_EDGE = 1024;" in source
    assert "PNGTUBER_LAYERED_CANVAS_MAX_RENDER_EDGE / Math.max(logicalWidth, logicalHeight)" in setup_block
    assert "this.layeredCanvasLogicalWidth = logicalWidth;" in setup_block
    assert "this.layeredCanvasLogicalHeight = logicalHeight;" in setup_block
    assert "this.layeredCanvasScaleX = renderWidth / logicalWidth;" in setup_block
    assert "this.layeredCanvasScaleY = renderHeight / logicalHeight;" in setup_block
    assert "const heightLimitedWidthVh = (maxHeightVh * logicalWidth) / logicalHeight;" in setup_block
    assert "canvas.style.width = `min(${logicalWidth}px, ${viewportWidthLimits})`;" in setup_block
    assert "canvas.style.height = 'auto';" in setup_block
    assert "Number(this.layeredCanvasLogicalWidth)" in pointer_block
    assert "Number(this.layeredCanvasLogicalHeight)" in pointer_block
    assert "ctx.clearRect(0, 0, canvas.width, canvas.height);" in draw_block
    assert "ctx.setTransform(renderScaleX, 0, 0, renderScaleY, 0, 0);" in draw_block


def test_layered_pngtuber_can_render_full_resolution_snapshot_without_resizing_runtime_canvas():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    snapshot_block = source[
        source.index("        renderLayeredSnapshotCanvas("):
        source.index("        drawLayeredState(stateName")
    ]
    draw_block = source[
        source.index("        drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "document.createElement('canvas')" in snapshot_block
    assert "Number(this.layeredCanvasLogicalWidth)" in snapshot_block
    assert "Number(this.layeredCanvasLogicalHeight)" in snapshot_block
    assert "Number(options.maxEdge)" in snapshot_block
    assert "maxEdge / Math.max(logicalWidth, logicalHeight)" in snapshot_block
    assert "this.drawLayeredState(stateName, timestamp, {" in snapshot_block
    assert "scaleX: canvas.width / logicalWidth" in snapshot_block
    assert "scaleY: canvas.height / logicalHeight" in snapshot_block
    assert "return drawn ? canvas : null;" in snapshot_block
    assert "this.canvasElement =" not in snapshot_block
    assert "renderTarget?.canvas || this.canvasElement" in draw_block
    assert "renderTarget?.scaleX ?? this.layeredCanvasScaleX" in draw_block
    assert "renderTarget?.scaleY ?? this.layeredCanvasScaleY" in draw_block


def test_pngtuber_load_announces_identity_change_before_async_setup():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    load_block = source[
        source.index("        async load(config, options = {}) {"):
        source.index("        stateToSrc(state)")
    ]

    config_assignment = "this.config = normalizedConfig;"
    loading_event = "window.dispatchEvent(new CustomEvent('pngtuber-model-loading', {"
    async_setup = "await this.setupLayeredAdapter({ config: normalizedConfig, isCurrentLoad });"
    assert load_block.index(config_assignment) < load_block.index(loading_event)
    assert load_block.index(loading_event) < load_block.index(async_setup)


def test_pngtuber_loader_finishes_loading_state_on_every_exit():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    loader_block = source[
        source.index("    async function loadPNGTuberAvatar(config) {"):
        source.index("    function playPNGTuberAnimation")
    ]

    assert "try {" in loader_block
    assert "} finally {" in loader_block
    assert "window.dispatchEvent(new CustomEvent('pngtuber-model-load-finished', {" in loader_block


def test_pngtuber_loader_binds_lifecycle_events_to_one_load_token():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    load_block = source[
        source.index("        async load(config, options = {}) {"):
        source.index("        stateToSrc(state)")
    ]
    loader_block = source[
        source.index("    async function loadPNGTuberAvatar(config) {"):
        source.index("    function playPNGTuberAnimation")
    ]

    assert "let pngtuberLoadSequence = 0;" in source
    assert "const loadToken = ++pngtuberLoadSequence;" in loader_block
    assert "await window.pngtuberManager.load(config || {}, { loadToken });" in loader_block
    assert "const loadToken = Number(options.loadToken) || 0;" in load_block
    assert "if (!isCurrentLoad()) return false;" in load_block
    assert loader_block.count("if (loadToken !== pngtuberLoadSequence)") == 3
    assert "if (!loaded || loadToken !== pngtuberLoadSequence)" in loader_block
    assert load_block.count("detail: { loadToken }") == 1
    assert loader_block.count("detail: { loadToken }") == 3


def test_pngtuber_remote_images_enable_anonymous_cors_before_loading():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    assign_block = source[
        source.index("    function assignImageSource(image, src) {"):
        source.index("    function isPNGTuberPlusLayerVisible")
    ]

    assert "/^(?:https?:)?\\/\\//i.test" in assign_block
    assert "image.crossOrigin = 'anonymous';" in assign_block
    assert assign_block.index("image.crossOrigin = 'anonymous';") < assign_block.index("image.src = src;")
    assert "assignImageSource(img, src);" in source
    assert "assignImageSource(this.image, nextSrc);" in source


def test_pngtuber_older_overlapping_load_cannot_resume_over_latest_model():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber overlapping load tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const events = [];
const window = {{
  location: {{ pathname: '/' }},
  innerWidth: 1280,
  innerHeight: 720,
  lanlan_config: {{ model_type: 'pngtuber' }},
  dispatchEvent(event) {{ events.push(event); }},
}};
const document = {{
  body: {{ classList: {{ contains() {{ return false; }} }} }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
class CustomEvent {{
  constructor(type, options = {{}}) {{ this.type = type; this.detail = options.detail; }}
}}
const context = {{ console, CustomEvent, document, window }};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

const manager = new window.PNGTuberManager();
const pendingSetups = [];
manager.detachDragListeners = () => {{}};
manager.clearEmotion = () => {{}};
manager.setupLayeredAdapter = (options) => new Promise((resolve) => {{
  pendingSetups.push({{ options, resolve }});
}});
manager.ensureContainer = () => {{}};
manager.preloadImages = () => {{}};
manager.attachSpeechListeners = () => {{}};
manager.attachDragListeners = () => {{}};
manager.setState = () => {{}};
manager.applyTransform = () => {{}};
manager.syncGlobalConfig = () => {{}};
manager.setupHTMLLockIcon = () => {{}};

(async () => {{
  const older = manager.load({{ idle_image: 'older.png' }}, {{ loadToken: 1 }});
  const newer = manager.load({{ idle_image: 'newer.png' }}, {{ loadToken: 2 }});
  assert.equal(pendingSetups.length, 2);

  pendingSetups[1].resolve(false);
  assert.equal(await newer, true);
  assert.equal(manager.config.idle_image, 'newer.png');

  pendingSetups[0].resolve(false);
  assert.equal(await older, false);
  assert.equal(manager.config.idle_image, 'newer.png');
  assert.equal(events.filter((event) => event.type === 'pngtuber-model-loading').length, 2);
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""

    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_layered_pngtuber_alt_one_cycles_states_without_imported_hotkeys():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    attach_block = source[
        source.index("attachLayeredHotkeys()"):
        source.index("        detachLayeredHotkeys()")
    ]
    handler_block = source[
        source.index("        handleLayeredHotkey(event) {"):
        source.index("        async setupLayeredAdapter(options = {})")
    ]
    cycle_hotkey_block = source[
        source.index("        isLayeredCycleHotkey(event) {"):
        source.index("        cycleLayeredState()")
    ]
    cycle_block = source[
        source.index("        cycleLayeredState() {"):
        source.index("        handleLayeredHotkey(event)")
    ]

    assert "this.getLayeredStateCount() <= 1" in attach_block
    assert "this.layeredMetadata.hotkeys" not in attach_block
    assert "isLayeredCycleHotkey(event)" in handler_block
    assert "cycleLayeredState()" in handler_block
    assert "event.preventDefault();" in handler_block
    assert "event.stopPropagation();" in handler_block
    assert "hotkeyMatchesEvent" not in handler_block
    assert "this.layeredMetadata.hotkeys" not in handler_block
    assert "setLayeredStateIndex(Number(matched.state_index)" not in handler_block
    assert "event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey" in cycle_hotkey_block
    assert "event.key === '1' || event.code === 'Digit1' || event.keyCode === 49" in cycle_hotkey_block
    assert "this.getLayeredStateCount() <= 1" in cycle_block
    assert "const stateCount = this.getLayeredStateCount();" in cycle_block
    assert "this.setLayeredStateIndex((this.layeredStateIndex + 1) % stateCount" in cycle_block
    assert "source: 'alt-one-cycle-hotkey'" in cycle_block


def test_pngtuber_plus_imported_toggles_ignore_shift_modified_events():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    toggle_block = source[
        source.index("layeredToggleEntriesForEvent(event) {"):
        source.index("        initializeLayeredToggleState(layers)")
    ]

    assert "if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return [];" in toggle_block


def test_layered_pngtuber_alt_two_toggles_imported_asset_action():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    attach_block = source[
        source.index("attachLayeredHotkeys()"):
        source.index("        detachLayeredHotkeys()")
    ]
    handler_block = source[
        source.index("        handleLayeredHotkey(event) {"):
        source.index("        async setupLayeredAdapter(options = {})")
    ]
    asset_hotkey_block = source[
        source.index("        isLayeredAssetActionHotkey(event) {"):
        source.index("        hasLayeredAssetActions()")
    ]
    asset_toggle_block = source[
        source.index("        togglePrimaryLayeredAssetAction() {"):
        source.index("        handleLayeredHotkey(event)")
    ]
    render_block = source[
        source.index("        shouldRenderLayer(layer, stateName) {"):
        source.index("        layerStateForCurrentIndex(layer)")
    ]

    assert "hasLayeredAssetActions()" in attach_block
    assert "isLayeredAssetActionHotkey(event)" in handler_block
    assert "togglePrimaryLayeredAssetAction()" in handler_block
    assert "event.key === '2' || event.code === 'Digit2' || event.keyCode === 50" in asset_hotkey_block
    assert "this.layeredAssetVisibility.set(String(spriteId), true);" in asset_toggle_block
    assert "this.layeredAssetVisibility.set(String(spriteId), false);" in asset_toggle_block
    assert "this.restartLayeredAnimationLoop();" in asset_toggle_block
    assert "source: 'alt-two-asset-hotkey'" in asset_toggle_block
    assert "const assetVisibility = this.layeredAssetVisibility.get(String(layer.sprite_id));" in render_block
    assert "const assetForcedVisible = assetVisibility === true;" in render_block
    assert "if (assetVisibility === false) return false;" in render_block
    assert "if (layer.inactive_asset_ancestor && !assetForcedVisible) return false;" in render_block
    assert "if (layerState.visible === false && !assetForcedVisible) return false;" in render_block


def test_layered_pngtuber_uses_default_mouth_state_under_emotions():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    state_block = source[
        source.index("layerStateForRender(layer, stateName = this.state || 'idle')"):
        source.index("        isLayeredRemixModel()")
    ]
    render_block = source[
        source.index("        shouldRenderLayer(layer, stateName) {"):
        source.index("        layerStateForCurrentIndex(layer)")
    ]

    assert "const layerState = this.layerStateForRender(layer, stateName);" in render_block
    assert "this.isLayeredPlusModel() || this.layerStateHasTalkingMouth(currentState)" in state_block
    assert "const defaultState = states[0] || layer.state || {};" in state_block
    assert "return this.layerStateHasTalkingMouth(defaultState) ? defaultState : currentState;" in state_block


def test_layered_pngtuber_draw_order_uses_imported_effective_z_index():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    helper_block = source[
        source.index("        layerDrawZIndex(layer, layerState = null) {"):
        source.index("        drawLayeredState(stateName")
    ]
    draw_block = source[
        source.index("        drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]
    debug_block = source[
        source.index("        renderedLayerDebugInfo(stateName)"):
        source.index("        getDebugState()")
    ]

    assert "layerState.effective_z_index" in helper_block
    assert "layer.effective_zindex" in helper_block
    assert "layerState.z_index" in helper_block
    assert "layer.zindex" in helper_block
    assert "this.fallbackLayerDrawZIndex(layer, layerState)" in helper_block
    assert "fallbackLayerDrawZIndex(layer, layerState = null)" in helper_block
    assert "_fallbackLayersBySpriteIdSource !== layers" in helper_block
    assert "const layersBySpriteId = this._fallbackLayersBySpriteId;" in helper_block
    assert "const layersBySpriteId = new Map();" not in helper_block
    assert "currentState.z_as_relative ?? current.z_as_relative" in helper_block
    assert "this.compareLayerRenderOrder(a, b, stateName)" in draw_block
    assert "this.compareLayerRenderOrder(a, b, stateName)" in debug_block


def test_layered_pngtuber_keeps_stable_breathing_without_raw_layer_motion():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    timers_block = source[
        source.index("clearLayeredTimers()"):
        source.index("        attachLayeredHotkeys()")
    ]
    restart_block = source[
        source.index("restartLayeredAnimationLoop()"):
        source.index("        motionValue(")
    ]
    animation_loop_block = source[
        source.index("startLayeredAnimationLoop(options = {})"):
        source.index("        layeredBreathingEnabled()")
    ]
    breathing_enabled_block = source[
        source.index("layeredBreathingEnabled()"):
        source.index("        currentLayeredBreathingTransform(")
    ]
    breathing_transform_block = source[
        source.index("currentLayeredBreathingTransform("):
        source.index("        startLayeredBreathingLoop(")
    ]
    breathing_loop_block = source[
        source.index("        startLayeredBreathingLoop("):
        source.index("        motionValue(")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]

    assert "this.layeredBreathingFrame = null;" in constructor_block
    assert "this.layeredBreathingStart = 0;" in constructor_block
    assert "this.stopLayeredBreathingLoop();" in timers_block
    assert "this.startLayeredAnimationLoop();" in restart_block
    assert "this.startLayeredBreathingLoop();" in animation_loop_block
    assert "features.layered_breathing === false" in breathing_enabled_block
    assert "return this.isLayeredActive();" in breathing_enabled_block
    assert "if (!this.layeredBreathingStart) return { y: 0, scaleX: 1, scaleY: 1 };" in breathing_transform_block
    assert "this.layeredBreathingStart = timestamp;" not in breathing_transform_block
    assert "scaleY" in breathing_transform_block
    assert "scaleX" in breathing_transform_block
    assert "this.applyAnimationTransform(timestamp);" in breathing_loop_block
    assert "const breathing = this.currentLayeredBreathingTransform(timestamp);" in apply_transform_block
    assert "bounce.y + breathing.y" in apply_transform_block
    assert "bounce.scaleX * breathing.scaleX" in apply_transform_block
    assert "bounce.scaleY * breathing.scaleY" in apply_transform_block


def test_audio_playback_routes_lip_sync_to_pngtuber_when_active():
    source = APP_AUDIO_PLAYBACK_PATH.read_text(encoding="utf-8")
    model_type_block = source[
        source.index("function getActiveAvatarModelType()"):
        source.index("    function clearPendingAudioMetaStallTimer()")
    ]
    stop_block = source[
        source.index("function stopActiveLipSync()"):
        source.index("    function maybeFinalizeAssistantSpeech(")
    ]
    schedule_block = source[
        source.index("function scheduleAudioChunks()"):
        source.index("                    var scheduledStartTime = S.nextChunkTime;")
    ]

    assert "pngtuber-container" in model_type_block
    assert "modelType === 'pngtuber'" in model_type_block
    assert "return 'pngtuber';" in model_type_block
    assert "activeModelType === 'pngtuber'" in schedule_block
    assert "typeof window.pngtuberManager.startLipSync === 'function'" in schedule_block
    assert "window.pngtuberManager.startLipSync(S.globalAnalyser)" in schedule_block
    assert "S.lipSyncActive = true;" in schedule_block
    assert "activeModelType === 'pngtuber'" in stop_block
    assert "typeof window.pngtuberManager.stopLipSync === 'function'" in stop_block
    assert "window.pngtuberManager.stopLipSync()" in stop_block
    assert "S.lipSyncActive = false;" in stop_block


def test_pngtuber_analyser_lip_sync_uses_hysteresis_and_timer_mutex():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    lip_sync_block = source[
        source.index("startLipSync(analyser)"):
        source.index("        stopLipSync()")
    ]
    stop_lip_sync_block = source[
        source.index("stopLipSync()"):
        source.index("        scheduleSpeakingMouthFrame()")
    ]
    schedule_block = source[
        source.index("scheduleSpeakingMouthFrame()"):
        source.index("        startSpeakingMouthAnimation()")
    ]

    assert "this.lipSyncFrame = null;" in constructor_block
    assert "this.lipSyncMouthOpen = 0;" in constructor_block
    assert "this.lipSyncMouthState = false;" in constructor_block
    assert "this.lipSyncLastStateChangeAt = 0;" in constructor_block
    assert "this.lipSyncNextPulseAt = 0;" in constructor_block
    assert "this.lipSyncPulseCloseAt = 0;" in constructor_block
    assert "clearTimeout(this.speakingMouthTimer);" in lip_sync_block
    assert "this.startSpeakingMouthAnimation();" in lip_sync_block
    assert "const sampleSize = Math.max(32, Number(analyser.fftSize) || 2048);" in lip_sync_block
    assert "frequencyBinCount" not in lip_sync_block
    assert "analyser.getByteTimeDomainData(dataArray);" in lip_sync_block
    assert "Math.sqrt(sum / dataArray.length)" in lip_sync_block
    assert "const activeThreshold = 0.16;" in lip_sync_block
    assert "const quietThreshold = 0.07;" in lip_sync_block
    assert "const pulseOpenMs = Math.max(42, Math.min(72, 42 + this.lipSyncMouthOpen * 34));" in lip_sync_block
    assert "const pulseGapMs = Math.max(45, Math.min(135, 135 - this.lipSyncMouthOpen * 90));" in lip_sync_block
    assert "timestamp >= this.lipSyncPulseCloseAt" in lip_sync_block
    assert "timestamp >= this.lipSyncNextPulseAt" in lip_sync_block
    assert "this.lipSyncPulseCloseAt = timestamp + pulseOpenMs;" in lip_sync_block
    assert "this.lipSyncNextPulseAt = timestamp + pulseGapMs;" in lip_sync_block
    assert "this.applyLipSyncMouthState(true);" in lip_sync_block
    assert "this.applyLipSyncMouthState(false);" in lip_sync_block
    assert "this.lipSyncFrame = requestAnimationFrame(tick);" in lip_sync_block
    assert "cancelAnimationFrame(this.lipSyncFrame);" in stop_lip_sync_block
    assert "this.lipSyncFrame = null;" in stop_lip_sync_block
    assert "this.lipSyncMouthOpen = 0;" in stop_lip_sync_block
    assert "this.lipSyncNextPulseAt = 0;" in stop_lip_sync_block
    assert "this.lipSyncPulseCloseAt = 0;" in stop_lip_sync_block
    assert "if (this.lipSyncFrame) return;" in schedule_block
    assert "if (!this.isSpeaking || this.lipSyncFrame) return;" in schedule_block


def test_pngtuber_lip_sync_state_changes_use_layered_safe_mouth_pulses():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    helper_block = source[
        source.index("applyLipSyncMouthState(open)"):
        source.index("        startLipSync(analyser)")
    ]

    assert "if (this.lipSyncMouthState === open && this.speakingMouthOpen === open) return;" in helper_block
    assert "this.lipSyncMouthState = open;" in helper_block
    assert "this.speakingMouthOpen = open;" in helper_block
    assert "this.startSpeakingBounceAnimation();" in helper_block
    assert "this.setState(open ? 'talking' : 'idle', { restartLayeredAnimation: false });" in helper_block


def test_pngtuber_debug_state_exposes_lip_sync_timer():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    debug_block = source[
        source.index("getDebugState()"):
        source.index("        setSpeaking(isSpeaking)")
    ]

    assert "lipSyncFrame: !!this.lipSyncFrame" in debug_block


def test_pngtuber_talking_hop_moves_whole_avatar_while_speaking():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    hop_transform_block = source[
        source.index("currentTalkingHopTransform("):
        source.index("        startTalkingHopAnimation(")
    ]
    hop_loop_block = source[
        source.index("startTalkingHopAnimation("):
        source.index("        applyLipSyncMouthState(open)")
    ]
    stop_block = source[
        source.index("stopTalkingHopAnimation()"):
        source.index("        applyLipSyncMouthState(open)")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]
    start_block = source[
        source.index("        startSpeakingMouthAnimation() {"):
        source.index("        stopSpeakingMouthAnimation()")
    ]
    lip_sync_block = source[
        source.index("startLipSync(analyser)"):
        source.index("        stopLipSync()")
    ]
    stop_speaking_block = source[
        source.index("stopSpeakingMouthAnimation()"):
        source.index("        renderedLayerCountForState(")
    ]
    debug_block = source[
        source.index("getDebugState()"):
        source.index("        setSpeaking(isSpeaking)")
    ]

    assert "this.talkingHopFrame = null;" in constructor_block
    assert "this.talkingHopStart = 0;" in constructor_block
    assert "this.talkingHopAmplitude = 0;" in constructor_block
    assert "this.talkingHopPeriodMs = 0;" in constructor_block
    assert "return { y: 0, scaleX: 1, scaleY: 1 };" in hop_transform_block
    assert "const wave = Math.sin(progress * Math.PI);" in hop_transform_block
    assert "y: -this.talkingHopAmplitude * wave" in hop_transform_block
    assert "scaleY: 1 + 0.004 * wave" in hop_transform_block
    assert "if (this.talkingHopFrame || !this.isSpeaking || !this.isLayeredActive()) return;" in hop_loop_block
    assert "this.talkingHopAmplitude = 4.5;" in hop_loop_block
    assert "this.talkingHopPeriodMs = 260;" in hop_loop_block
    assert "this.applyAnimationTransform(timestamp);" in hop_loop_block
    assert "this.talkingHopFrame = requestAnimationFrame(tick);" in hop_loop_block
    assert "cancelAnimationFrame(this.talkingHopFrame);" in stop_block
    assert "this.talkingHopFrame = null;" in stop_block
    assert "this.talkingHopStart = 0;" in stop_block
    assert "const talkingHop = this.currentTalkingHopTransform(timestamp);" in apply_transform_block
    assert "bounce.y + breathing.y + talkingHop.y" in apply_transform_block
    assert "bounce.scaleX * breathing.scaleX * talkingHop.scaleX" in apply_transform_block
    assert "bounce.scaleY * breathing.scaleY * talkingHop.scaleY" in apply_transform_block
    assert "this.startTalkingHopAnimation();" in start_block
    assert "this.startTalkingHopAnimation();" in lip_sync_block
    assert "this.stopTalkingHopAnimation();" in stop_speaking_block
    assert "talkingHopFrame: !!this.talkingHopFrame" in debug_block


def test_pngtuber_animation_loops_throttle_overlay_position_updates():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    helper_block = source[
        source.index("updateOverlayPositionsForAnimation("):
        source.index("        currentLayeredBreathingTransform(")
    ]
    breathing_loop_block = source[
        source.index("        startLayeredBreathingLoop() {"):
        source.index("        stopLayeredBreathingLoop()")
    ]
    bounce_loop_block = source[
        source.index("        startSpeakingBounceAnimation() {"):
        source.index("        currentTalkingHopTransform(")
    ]
    hop_loop_block = source[
        source.index("        startTalkingHopAnimation() {"):
        source.index("        stopTalkingHopAnimation()")
    ]

    assert "this.lastOverlayPositionUpdateAt = 0;" in constructor_block
    assert "this.lastAnimationTransformAt = 0;" in constructor_block
    assert "const minIntervalMs = 120;" in helper_block
    assert "timestamp - this.lastOverlayPositionUpdateAt < minIntervalMs" in helper_block
    assert "this.updateLockIconPosition();" in helper_block
    assert "this.updateFloatingButtonsPosition();" not in helper_block
    assert "applyAnimationTransform(timestamp = performance.now())" in helper_block
    assert "if (this.lastAnimationTransformAt === timestamp) return;" in helper_block
    assert "this.lastAnimationTransformAt = timestamp;" in helper_block
    assert "this.applyTransform(timestamp);" in helper_block
    assert "this.applyAnimationTransform(timestamp);" in breathing_loop_block
    assert "this.applyAnimationTransform(timestamp);" in bounce_loop_block
    assert "this.applyAnimationTransform(timestamp);" in hop_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in breathing_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in bounce_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in hop_loop_block
    assert "this.updateLockIconPosition();" not in breathing_loop_block
    assert "this.updateLockIconPosition();" not in bounce_loop_block
    assert "this.updateLockIconPosition();" not in hop_loop_block


def test_pngtuber_floating_controls_auto_hide_like_live2d_without_touching_other_models():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    setup_block = source[
        source.index("PNGTuberManager.prototype.setupFloatingButtons = function()"):
        source.index("            window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));")
    ]
    lock_block = source[
        source.index("        updateLockIconPosition()"):
        source.index("        async resolveCurrentLanlanName()")
    ]

    assert "this._pngtuberFloatingControlsVisible = true;" in setup_block
    assert "const hideFloatingControls = () => {" in setup_block
    assert "const showFloatingControls = () => {" in setup_block
    assert "const startHideTimer = (delay = 1000) => {" in setup_block
    assert "const schedulePointerEvaluation = () => {" in setup_block
    assert "this._pngtuberPointerEvaluateFrame = requestAnimationFrame(() => {" in setup_block
    assert "if (window.isInTutorial === true) return;" in setup_block
    assert "buttonsContainer.addEventListener('mouseenter', markControlsHover);" in setup_block
    assert "buttonsContainer.addEventListener('mouseleave', unmarkControlsHover);" in setup_block
    assert "lockIcon.addEventListener('mouseenter', markControlsHover);" in setup_block
    assert "lockIcon.addEventListener('mouseleave', unmarkControlsHover);" in setup_block
    assert "window.addEventListener('pointermove', handlePointerMove, { passive: true });" in setup_block
    assert "window.addEventListener('focus', handleWindowFocus);" in setup_block
    assert "window.addEventListener('blur', handleWindowBlur);" in setup_block
    assert "document.addEventListener('mouseenter', handleDocumentMouseEnter, true);" in setup_block
    assert "document.addEventListener('mouseleave', handleDocumentMouseLeave, true);" in setup_block
    assert "this.image.addEventListener('pointerenter', handleImagePointerEnter);" in setup_block
    assert "this.image.addEventListener('pointerleave', handleImagePointerLeave);" in setup_block
    assert "this.image.addEventListener('mouseover', handleImagePointerEnter);" in setup_block
    assert "this._lastPngtuberPointerX = null;" in setup_block
    handle_pointer_block = setup_block[
        setup_block.index("const handlePointerMove = (event) => {"):
        setup_block.index("const handleImagePointerEnter = () => showFloatingControls();")
    ]
    assert "schedulePointerEvaluation();" in handle_pointer_block
    assert "shouldKeepFloatingControlsVisible()" not in handle_pointer_block
    assert "showFloatingControls();" not in handle_pointer_block
    assert "startHideTimer();" not in handle_pointer_block
    assert "this._pngtuberFloatingControlsVisible === false" in lock_block
    assert "'live2d-lock-icon'" not in setup_block
    assert "'vrm-lock-icon'" not in setup_block
    assert "'mmd-lock-icon'" not in setup_block
    # document 级 capture 事件必须过滤为窗口边界事件，否则相邻按钮边缘
    # 移动会反复触发隐藏/显示（闪烁）
    assert "const isWindowBoundaryMouseEvent = (event) => {" in setup_block
    assert "return isDocTarget && !event.relatedTarget;" in setup_block
    assert "if (!isWindowBoundaryMouseEvent(event)) return;" in setup_block
    assert "if (isWindowBoundaryMouseEvent(event)) {" in setup_block
    # unmarkControlsHover 必须校验 relatedTarget 仍在控件区域内再取消悬停标记
    assert "const related = event && event.relatedTarget;" in setup_block
    assert "buttonsContainer.contains(related)" in setup_block


def test_pngtuber_get_stable_anchor_rect_strips_animation_transforms():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber anchor tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = "const source = " + json.dumps(source) + ";\n" + r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const window = { location: { pathname: '/' }, innerWidth: 1000, innerHeight: 800,
  lanlan_config: { model_type: 'pngtuber' } };
const document = { body: { classList: { contains: () => false } }, getElementById: () => null };
vm.runInNewContext(source, { window, document, console, performance: { now: () => 0 },
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {} });

const manager = new window.PNGTuberManager();
manager.config = {
  scale: 1, offset_x: 0, offset_y: 0,
  mobile_scale: 1, mobile_offset_x: 0, mobile_offset_y: 0,
  position_anchor: 'bottom_right', mirror: false,
};
let currentRect = null;
manager.image = {
  style: {},
  getBoundingClientRect: () => ({ ...currentRect }),
};
manager.updateLockIconPosition = () => {};
// 受控动画量：说话弹跳 Y=-10、挤压 scaleX=1.2/scaleY=0.9，呼吸 Y=-2
manager.currentSpeakingBounceTransform = () => ({ y: -10, scaleX: 1.2, scaleY: 0.9 });
manager.currentLayeredBreathingTransform = () => ({ y: -2, scaleX: 1, scaleY: 1 });
manager.currentTalkingHopTransform = () => ({ y: 0, scaleX: 1, scaleY: 1 });
manager.applyTransform(0);
assert.equal(manager._appliedAnimOffsetY, -12);
assert.equal(manager._appliedAnimScaleX, 1.2);
assert.equal(manager._appliedAnimScaleY, 0.9);
assert.equal(manager._appliedAnimCenterAnchored, false);
assert.equal(manager._appliedAnimMirrored, false);

// bottom_right 非镜像：缩放围绕右下角，右/底边界固定
currentRect = { left: 100, top: 200, width: 240, height: 180, right: 340, bottom: 380 };
let stable = manager.getStableAnchorRect();
assert.equal(stable.width, 200);   // 240 / 1.2
assert.equal(stable.height, 200);  // 180 / 0.9
assert.equal(stable.right, 340);
assert.equal(stable.bottom, 392);  // 380 - (-12)
assert.equal(stable.left, 140);
assert.equal(stable.top, 192);

// bottom_right 镜像：finalScaleX 为负，右 bottom 原点固定的是可见矩形左边界
manager.config.mirror = true;
manager.applyTransform(0);
assert.equal(manager._appliedAnimMirrored, true);
stable = manager.getStableAnchorRect();
assert.equal(stable.left, 100);
assert.equal(stable.right, 300);   // 100 + 200
assert.equal(stable.bottom, 392);
assert.equal(stable.top, 192);

// center 锚点：缩放围绕中心，镜像不影响结果
manager.config.mirror = false;
manager.config.position_anchor = 'center';
manager.applyTransform(0);
assert.equal(manager._appliedAnimCenterAnchored, true);
stable = manager.getStableAnchorRect();
assert.equal(stable.left, 120);    // centerX 220 - 100
assert.equal(stable.right, 320);
assert.equal(stable.top, 202);     // centerY 290 - (-12) - 100
assert.equal(stable.bottom, 402);

// 动画量归零时稳定矩形等于当前矩形
manager.currentSpeakingBounceTransform = () => ({ y: 0, scaleX: 1, scaleY: 1 });
manager.currentLayeredBreathingTransform = () => ({ y: 0, scaleX: 1, scaleY: 1 });
manager.applyTransform(0);
stable = manager.getStableAnchorRect();
assert.equal(stable.left, 100);
assert.equal(stable.top, 200);
assert.equal(stable.width, 240);
assert.equal(stable.height, 180);
console.log('stable anchor rect OK');
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_locked_hover_fade_ignores_touch_input():
    # Touch has no hover: a tap would start the 1s stationary timer and leave the
    # avatar faded with nothing to undo it. Both the touch pointermove and the
    # compat mousemove derived from it must bail out before touching fade state.
    # The compat mousemove is trusted in every browser, so all trusted mousemoves
    # are skipped (pointermove already covers real mouse movement); Electron's
    # synthetic mousemove is untrusted and still gets through.
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    setup_block = source[
        source.index("setupLockedHoverFade() {"):
        source.index("setModelDraggingState(active, moved = false) {")
    ]
    handler = setup_block[
        setup_block.index("const onPointerMove = (event) => {"):
        setup_block.index("const onKeyDown = (event) => {")
    ]

    touch_guard = handler.index("event.pointerType === 'touch'")
    assert "return;" in handler[touch_guard:handler.index("}", touch_guard)]
    assert touch_guard < handler.index("(event.type === 'mousemove' && event.isTrusted)")
    assert touch_guard < handler.index("this._fadePointerX = event.clientX;")
    assert touch_guard < handler.index("requestAnimationFrame(evaluate)")


def test_pngtuber_locked_hover_fade_revalidates_and_clears_on_leave():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    setup_block = source[
        source.index("setupLockedHoverFade() {"):
        source.index("setModelDraggingState(active, moved = false) {")
    ]

    # The 1s timer re-runs evaluate() instead of fading blindly: the viewport may
    # have moved the model away or crossed the mobile cutoff in the meantime.
    timer_body = setup_block[
        setup_block.index("stationaryFadeTimer = setTimeout(() => {"):
        setup_block.index("}, STATIONARY_FADE_DELAY);")
    ]
    assert "evaluate();" in timer_body
    assert "applyFade();" not in timer_body
    assert "{ target: window, event: 'resize', handler: onResize }" in setup_block

    # Leaving the window (or a non-hover pen lifting) emits no further move, so the
    # fade must be cleared there and the stale coordinates dropped before any
    # pending frame / resize can re-fade from them. Electron Pet is excluded.
    leave = setup_block[
        setup_block.index("const onPointerOut = (event) => {"):
        setup_block.index("this._pngtuberFadeListeners = [")
    ]
    guard = leave[leave.index("if (event.pointerType === 'touch'"):leave.index("return;")]
    assert "|| event.relatedTarget" in guard
    assert "|| window.__LANLAN_IS_ELECTRON_PET__)" in guard
    assert leave.index("this._fadePointerX = NaN;") < leave.index("clearFadeActiveState();")
    assert leave.index("cancelAnimationFrame(pendingFrame);") < leave.index("clearFadeActiveState();")
    assert "{ target: window, event: 'pointerout', handler: onPointerOut }" in setup_block


def test_apply_emotion_prefers_pngtuber_runtime_when_active():
    source = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    apply_block = source[
        source.index("mod.applyEmotion = function applyEmotion(emotion)"):
        source.index("    window.applyEmotion = mod.applyEmotion;")
    ]

    assert "window.lanlan_config && window.lanlan_config.model_type" in apply_block
    assert "modelType === 'pngtuber'" in apply_block
    assert "window.pngtuberManager.setEmotion(emotion)" in apply_block
    assert "window.LanLan1.setEmotion(emotion)" in apply_block

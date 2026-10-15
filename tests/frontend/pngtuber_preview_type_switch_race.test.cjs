const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const PAGE_CONTROLLER_JS = path.join(
    PROJECT_ROOT, 'static', 'js', 'model_manager', 'page-controller.js');
const source = fs.readFileSync(PAGE_CONTROLLER_JS, 'utf8');
const CORE_JS = path.join(PROJECT_ROOT, 'static', 'pngtuber-core.js');
const coreSource = fs.readFileSync(CORE_JS, 'utf8');

function extractSlice(startMarker, endMarker, src = source) {
    const start = src.indexOf(startMarker);
    assert.ok(start >= 0, `找不到区块起点: ${startMarker}`);
    const end = src.indexOf(endMarker, start);
    assert.ok(end > start, `找不到区块终点: ${endMarker}`);
    return src.slice(start, end);
}

const previewSlice = extractSlice(
    'async function previewPNGTuberConfig(',
    'async function loadSelectedPNGTuberOption(');
const previewControlsSlice = extractSlice(
    'async function loadPNGTuberPreviewControls(',
    // 注意：函数体内也有 `if (pngtuberTalkPreviewBtn) {`，endMarker 必须锚定到
    // 函数结束后紧跟的 listener 注册，否则会把函数拦腰截断
    `if (pngtuberTalkPreviewBtn) {
        pngtuberTalkPreviewBtn.addEventListener(`);

function makeContainerStub() {
    const classCalls = [];
    return {
        style: {},
        classList: {
            add(name) { classCalls.push(['add', name]); },
            remove(name) { classCalls.push(['remove', name]); },
        },
        classCalls,
    };
}

// initialType：调用 previewPNGTuberConfig 时的 currentModelType；
// typeDuringLoad：PNG 异步加载完成前用户切到的类型（模拟加载中途切换模型类型）。
function makeSandbox({ initialType = 'pngtuber', typeDuringLoad = 'pngtuber' } = {}) {
    const statusMessages = [];
    const records = { clearCalls: 0, renderCalls: [], talkButtonTextCalls: 0 };
    const sandbox = {
        console: { ...console, error() {}, warn() {}, log() {} },
        currentModelType: initialType,
        currentLive3dSubType: '',
        currentModelInfo: null,
        pendingPNGTuberPreview: null,
        unfinalizedPNGTuberCommit: null,
        savePositionBtn: null,
        pngtuberPreviewGeneration: 0,
        t: (key, fallback) => fallback,
        showStatus: (message) => statusMessages.push(message),
        markModelChangedForCardFacePrompt: () => {},
        live2dContainer: makeContainerStub(),
        vrmContainer: makeContainerStub(),
        mmdContainer: makeContainerStub(),
        pngtuberContainer: makeContainerStub(),
        // 状态预览控件的真实函数会被提取执行，这里桩掉它的外部依赖
        clearPNGTuberPreviewControls: () => { records.clearCalls += 1; },
        renderPNGTuberStatePreviewDropdown: (metadata) => { records.renderCalls.push(metadata); },
        updatePNGTuberTalkPreviewButtonText: () => { records.talkButtonTextCalls += 1; },
        fetchPNGTuberLayeredMetadata: async () => null,
        pngtuberPreviewGroup: { style: {} },
        pngtuberBasicPreviewSection: { style: {} },
        pngtuberTalkPreviewBtn: { disabled: true },
        statusMessages,
        records,
        avatarLoadCalls: 0,
        window: {
            hasUnsavedChanges: false,
            loadPNGTuberAvatar: async () => {
                sandbox.avatarLoadCalls += 1;
                // 加载进行中用户在模型类型下拉里切到了别的类型：
                // switchModelDisplay 会同步改写 currentModelType。
                sandbox.currentModelType = typeDuringLoad;
            },
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(previewControlsSlice, sandbox, { filename: 'loadPNGTuberPreviewControls' });
    vm.runInContext(previewSlice, sandbox, { filename: 'previewPNGTuberConfig' });
    return sandbox;
}

function runPreview(sandbox, { name = 'demo', idle = '/user_pngtuber/demo/idle.png', markDirty = true } = {}) {
    const talking = idle.replace('idle.png', 'talking.png');
    return vm.runInContext(
        `previewPNGTuberConfig(
            { idle_image: ${JSON.stringify(idle)}, talking_image: ${JSON.stringify(talking)} },
            { name: ${JSON.stringify(name)}, label: ${JSON.stringify(name)}, folder: ${JSON.stringify(name)} },
            { markDirty: ${JSON.stringify(markDirty)} }
        )`,
        sandbox);
}

test('对照组：全程停留在 pngtuber 时正常显示 PNG 容器', async () => {
    const sandbox = makeSandbox({ typeDuringLoad: 'pngtuber' });
    const result = await runPreview(sandbox);

    assert.equal(result, true);
    assert.equal(sandbox.avatarLoadCalls, 1);
    assert.equal(sandbox.pngtuberContainer.style.display, 'block');
    assert.deepEqual(sandbox.pngtuberContainer.classCalls, [['remove', 'hidden']]);
    assert.equal(sandbox.live2dContainer.style.display, 'none');
    assert.equal(sandbox.vrmContainer.style.display, 'none');
    assert.equal(sandbox.mmdContainer.style.display, 'none');
    assert.equal(sandbox.window.hasUnsavedChanges, true);
    assert.equal(sandbox.statusMessages.length, 1);
    assert.match(sandbox.statusMessages[0], /已加载PNGTuber模型/);
    // 提交成功后 currentModelInfo 才落上 pngtuber 条目
    assert.equal(sandbox.currentModelInfo.type, 'pngtuber');
    assert.equal(sandbox.currentModelInfo.name, 'demo');
    assert.equal(sandbox.currentModelInfo.pngtuber.idle_image, '/user_pngtuber/demo/idle.png');
});

test('加载中途切到 live2d：迟到的续体不得重新显示 PNG 容器/隐藏 live2d 容器', async () => {
    const sandbox = makeSandbox({ typeDuringLoad: 'live2d' });
    const result = await runPreview(sandbox);

    assert.equal(result, false);
    // 核心回归：PNG 容器不能被迟到的续体重新显示
    assert.equal(sandbox.pngtuberContainer.style.display, undefined);
    assert.deepEqual(sandbox.pngtuberContainer.classCalls, []);
    // live2d/vrm/mmd 容器不能被迟到的续体隐藏
    assert.equal(sandbox.live2dContainer.style.display, undefined);
    assert.deepEqual(sandbox.live2dContainer.classCalls, []);
    assert.equal(sandbox.vrmContainer.style.display, undefined);
    assert.equal(sandbox.mmdContainer.style.display, undefined);
    // 不应误报“已加载PNGTuber模型”，也不应把页面标记为有未保存更改
    assert.deepEqual(sandbox.statusMessages, []);
    assert.equal(sandbox.window.hasUnsavedChanges, false);
    // 取消的预览不得把 pngtuber 条目留在 currentModelInfo 上：
    // showStatus 定时器 / reloadCurrentLive2DModelInModelManager / 保存流程都会读它
    assert.equal(sandbox.currentModelInfo, null);
    // 切走后连状态预览控件都不应再加载
    assert.deepEqual(sandbox.records.renderCalls, []);
    // 取消后 pending 记录必须清空，删除防护恢复到已提交模型
    assert.equal(sandbox.pendingPNGTuberPreview, null);
});

test('加载中途切到 live3d：迟到的续体同样不得接管显示', async () => {
    const sandbox = makeSandbox({ typeDuringLoad: 'live3d' });
    const result = await runPreview(sandbox);

    assert.equal(result, false);
    assert.equal(sandbox.pngtuberContainer.style.display, undefined);
    assert.deepEqual(sandbox.pngtuberContainer.classCalls, []);
    assert.equal(sandbox.vrmContainer.style.display, undefined);
    assert.equal(sandbox.mmdContainer.style.display, undefined);
    assert.deepEqual(sandbox.statusMessages, []);
    assert.equal(sandbox.currentModelInfo, null);
});

test('入口即已切走（角色配置加载链被打断）：不启动过期预览、不覆盖 currentModelInfo', async () => {
    const sandbox = makeSandbox({ initialType: 'live2d', typeDuringLoad: 'live2d' });
    const result = await runPreview(sandbox);

    assert.equal(result, false);
    assert.equal(sandbox.avatarLoadCalls, 0);
    assert.equal(sandbox.currentModelInfo, null);
    assert.equal(sandbox.pngtuberContainer.style.display, undefined);
    assert.deepEqual(sandbox.statusMessages, []);
    // 过期入口调用不得自增世代号（否则会作废仍在进行的合法预览）
    assert.equal(sandbox.pngtuberPreviewGeneration, 0);
    // 也不得登记 pending 记录（否则会错误保护与新预览无关的模型）
    assert.equal(sandbox.pendingPNGTuberPreview, null);
});

test('同类型重叠预览：慢的旧预览 A 不得覆盖先完成的新预览 B', async () => {
    const sandbox = makeSandbox();
    // 受控双闸门：A 先进入加载但最后完成，B 后发起先完成
    let resolveA;
    let resolveB;
    const gateA = new Promise((resolve) => { resolveA = resolve; });
    const gateB = new Promise((resolve) => { resolveB = resolve; });
    sandbox.window.loadPNGTuberAvatar = async () => {
        sandbox.avatarLoadCalls += 1;
        await (sandbox.avatarLoadCalls === 1 ? gateA : gateB);
    };

    const previewA = runPreview(sandbox, { name: 'A', idle: '/user_pngtuber/a/idle.png' });
    const previewB = runPreview(sandbox, { name: 'B', idle: '/user_pngtuber/b/idle.png' });
    resolveB();
    const resultB = await previewB;
    resolveA();
    const resultA = await previewA;

    assert.equal(resultB, true);
    assert.equal(resultA, false);
    // 最终提交的是最新选择 B，不是更晚完成的 A
    assert.equal(sandbox.currentModelInfo.name, 'B');
    assert.equal(sandbox.currentModelInfo.pngtuber.idle_image, '/user_pngtuber/b/idle.png');
    // 只有 B 报成功提示；A 迟到后不得再发「已加载PNGTuber模型: A」
    assert.equal(sandbox.statusMessages.length, 1);
    assert.match(sandbox.statusMessages[0], /已加载PNGTuber模型: B/);
    // A 在中间守卫处被拦下，未加载自己的状态预览控件
    assert.deepEqual(sandbox.records.renderCalls, [null]);
});

test('旧预览的状态下拉不得在被取代后渲染（metadata fetch 竞态）', async () => {
    const sandbox = makeSandbox();
    sandbox.window.loadPNGTuberAvatar = async () => { sandbox.avatarLoadCalls += 1; };
    // A 的 metadata fetch 挂起；B 全程快速完成
    let resolveFetchA;
    const gateFetchA = new Promise((resolve) => { resolveFetchA = resolve; });
    const metadataA = { state_count: 2, states: [{ name: 'A1' }, { name: 'A2' }] };
    const metadataB = { state_count: 2, states: [{ name: 'B1' }, { name: 'B2' }] };
    sandbox.fetchPNGTuberLayeredMetadata = async (config) => {
        if (String(config.idle_image).includes('/a/')) {
            await gateFetchA;
            return metadataA;
        }
        return metadataB;
    };

    const previewA = runPreview(sandbox, { name: 'A', idle: '/user_pngtuber/a/idle.png' });
    // 让 A 走到 fetch 挂起点（loadPNGTuberAvatar 与中间守卫均为微任务）
    await new Promise((resolve) => setImmediate(resolve));
    const previewB = runPreview(sandbox, { name: 'B', idle: '/user_pngtuber/b/idle.png' });
    const resultB = await previewB;
    resolveFetchA();
    const resultA = await previewA;

    assert.equal(resultB, true);
    assert.equal(resultA, false);
    assert.equal(sandbox.currentModelInfo.name, 'B');
    // 只渲染了 B 的状态列表；A 的迟到 metadata 被世代号拦下
    assert.deepEqual(sandbox.records.renderCalls, [metadataB]);
});

const tick = () => new Promise((resolve) => setImmediate(resolve));

test('重叠预览乱序退出：旧预览退出不得清掉新预览的 pending 记录', async () => {
    const sandbox = makeSandbox();
    let resolveA;
    let resolveB;
    const gateA = new Promise((resolve) => { resolveA = resolve; });
    const gateB = new Promise((resolve) => { resolveB = resolve; });
    sandbox.window.loadPNGTuberAvatar = async () => {
        sandbox.avatarLoadCalls += 1;
        await (sandbox.avatarLoadCalls === 1 ? gateA : gateB);
    };

    const previewA = runPreview(sandbox, { name: 'A', idle: '/user_pngtuber/a/idle.png' });
    const previewB = runPreview(sandbox, { name: 'B', idle: '/user_pngtuber/b/idle.png' });
    // pending 记录被最新预览 B 覆盖（删除防护跟着最新选择走）
    assert.equal(sandbox.pendingPNGTuberPreview.folder, 'B');
    assert.equal(sandbox.pendingPNGTuberPreview.generation, 2);

    // 被取代的 A 先结束：finally 按世代号判定，不得误清 B 的 pending
    resolveA();
    const resultA = await previewA;
    assert.equal(resultA, false);
    assert.equal(sandbox.pendingPNGTuberPreview.folder, 'B');
    assert.equal(sandbox.currentModelInfo, null);

    // B 随后正常完成：提交并清理自己的 pending
    resolveB();
    const resultB = await previewB;
    assert.equal(resultB, true);
    assert.equal(sandbox.pendingPNGTuberPreview, null);
    assert.equal(sandbox.currentModelInfo.name, 'B');
});

test('头像加载被接受后立即提交模型信息，metadata fetch 期间不再空窗', async () => {
    const sandbox = makeSandbox();
    let resolveAvatar;
    let resolveFetch;
    const gateAvatar = new Promise((resolve) => { resolveAvatar = resolve; });
    const gateFetch = new Promise((resolve) => { resolveFetch = resolve; });
    sandbox.window.loadPNGTuberAvatar = async () => {
        sandbox.avatarLoadCalls += 1;
        await gateAvatar;
    };
    sandbox.fetchPNGTuberLayeredMetadata = async () => {
        await gateFetch;
        return null;
    };

    const preview = runPreview(sandbox);
    await tick();
    // 头像仍在加载：currentModelInfo 未提交，但 pending 记录已就位，
    // deleteSelectedModels 的安全检查据此仍能拦住「删除加载中的模型」
    assert.equal(sandbox.currentModelInfo, null);
    assert.equal(sandbox.pendingPNGTuberPreview.folder, 'demo');
    assert.equal(sandbox.pendingPNGTuberPreview.generation, 1);

    resolveAvatar();
    await tick();
    // 核心断言（Codex P2）：头像已被运行时接受并显示，模型信息在 metadata fetch
    // 之前提交——期间拖拽/缩放 PNG 时 stageModelManagerPNGTuberPlacement
    // 不再因 !currentModelInfo 拒绝暂存摆放
    assert.equal(sandbox.currentModelInfo.name, 'demo');
    assert.equal(sandbox.currentModelInfo.type, 'pngtuber');
    assert.equal(sandbox.currentModelInfo.pngtuber.idle_image, '/user_pngtuber/demo/idle.png');
    // pending 的防护使命随提交结束、不陪跑 metadata fetch（Codex P2 第二轮）：
    // 该 fetch 是无超时的裸请求，若挂起则 finally 永不执行，悬置的 pending
    // 会让该模型被删除安全检查误拦为「绑定中」
    assert.equal(sandbox.pendingPNGTuberPreview, null);
    // 已提交未定稿的条目引用已登记：若 fetch 从此挂起，离开块按它代行撤销
    // （Codex P2@1495：只靠预览出口的撤销在挂起时不可达）
    assert.equal(sandbox.unfinalizedPNGTuberCommit, sandbox.currentModelInfo);

    resolveFetch();
    const result = await preview;
    assert.equal(result, true);
    assert.equal(sandbox.pendingPNGTuberPreview, null);
    assert.equal(sandbox.unfinalizedPNGTuberCommit, null, '成功收尾后登记必须清除');
    assert.equal(sandbox.statusMessages.length, 1);
    assert.match(sandbox.statusMessages[0], /已加载PNGTuber模型: demo/);
});

test('离开 pngtuber：先作废在途预览（世代号+运行时 loadToken），再释放 pending 防护', () => {
    const start = source.indexOf('async function switchModelDisplay(');
    assert.ok(start >= 0, 'switchModelDisplay 不存在');
    const end = source.indexOf('const sidebar =', start);
    assert.ok(end > start, 'switchModelDisplay 序块不存在');
    const block = source.slice(start, end);
    assert.ok(block.includes("if (previousModelType === 'pngtuber' && type !== 'pngtuber') {"));
    assert.ok(block.includes('pendingPNGTuberPreview = null;'));
    // 只清防护不作废的话，「切走→删除在途模型→切回」后旧加载完成会复活已删除模型：
    // 必须同时自增页面世代号（拦截检查点提交）并作废运行时 loadToken（拦截 show()）
    assert.ok(block.includes('pngtuberPreviewGeneration += 1;'));
    assert.ok(block.includes('window.cancelPNGTuberAvatarLoads'));
    // 顺序：两个作废都必须先于释放删除防护
    const clearIdx = block.indexOf('pendingPNGTuberPreview = null;');
    assert.ok(block.indexOf('pngtuberPreviewGeneration += 1;') < clearIdx);
    assert.ok(block.indexOf('window.cancelPNGTuberAvatarLoads') < clearIdx);
    // 作废判定依据 previousModelType，必须先于 currentModelType 改写
    assert.ok(block.indexOf("previousModelType === 'pngtuber'") < block.indexOf('currentModelType = type;'));
    // 已在检查点提交、预览未走完的条目：离开时按条目对象同一性代行撤销
    // （metadata fetch 挂起时预览自己的 finally 永不可达——Codex P2@1495）
    assert.ok(block.includes('if (unfinalizedPNGTuberCommit && currentModelInfo === unfinalizedPNGTuberCommit) {'));
    assert.ok(block.includes('unfinalizedPNGTuberCommit = null;'));

    // 跨文件契约：pngtuber-core 必须提供并导出 cancelPNGTuberAvatarLoads，
    // 先自增序列号（拦截外层 loadPNGTuberAvatar 的 show()），再穿透到管理器
    // 内部作废在途 load()——isCurrentLoad 只看 _loadGeneration/_latestLifecycleLoadToken，
    // 外层序列号对它不可见，缺了内部作废则挂起的加载解析后仍会 setState 写旧图
    assert.ok(coreSource.includes('function cancelPNGTuberAvatarLoads() {'));
    assert.ok(coreSource.includes('window.cancelPNGTuberAvatarLoads = cancelPNGTuberAvatarLoads;'));
    const cancelFn = extractSlice(
        'function cancelPNGTuberAvatarLoads() {',
        'window.PNGTuberManager =',
        coreSource);
    assert.ok(cancelFn.includes('pngtuberLoadSequence += 1;'));
    assert.ok(cancelFn.includes('manager.cancelInFlightLoad()'));
    assert.ok(cancelFn.indexOf('pngtuberLoadSequence += 1;') < cancelFn.indexOf('manager.cancelInFlightLoad()'));
    // load() 的 isCurrentLoad 检查点必须先于 setState('idle')——作废生效的守卫位置
    const loadStart = coreSource.indexOf('async load(config, options = {}) {');
    const guardIdx = coreSource.indexOf('if (!isCurrentLoad()) return false;', loadStart);
    const setStateIdx = coreSource.indexOf("this.setState('idle');", loadStart);
    assert.ok(loadStart >= 0 && guardIdx > loadStart && setStateIdx > guardIdx);
    // 在途标记的生命周期（wehos 第 5 轮：load 只清自己这一代的标记——A 在途中
    // B 开始加载后，A 结束不得清掉 B 的标记）：进入即登记本代，唯一挂起点返回后
    // 按世代匹配清除，且清除先于 isCurrentLoad 检查点
    const markerSetIdx = coreSource.indexOf('this._inFlightLoadGeneration = loadGeneration;', loadStart);
    const markerClearIdx = coreSource.indexOf('if (this._inFlightLoadGeneration === loadGeneration) this._inFlightLoadGeneration = 0;', loadStart);
    const adapterIdx = coreSource.indexOf('await this.setupLayeredAdapter(', loadStart);
    assert.ok(markerSetIdx > loadStart && markerSetIdx < adapterIdx, '在途标记应在挂起点前登记');
    assert.ok(markerClearIdx > adapterIdx && markerClearIdx < guardIdx, '标记清除应在挂起点后、检查点前，且按世代匹配');
});

test('切换链世代号：旧链的列表 await 之后不得再发起预览（Codex P1 链级竞态）', () => {
    // 场景：角色 A 配置的 switchModelDisplay('pngtuber') 还挂在 loadPNGTuberModels()，
    // 用户切走→切回→选了 B；A 的列表请求随后返回，旧链若继续为 preferredConfig=A
    // 发起预览，会给 A 分配比 B 更新的预览世代号、反向顶掉用户的新选择。
    // 预览世代号在预览发起时才分配，识别不了链级过期——必须在链入口捕获世代号、
    // 发起预览前复查，并把链有效性返回给调用方（角色配置路径在 switchModelDisplay
    // 返回后还会自己发起预览，函数内部的复查拦不到它——wehos 第 6 轮 🔴）。
    const start = source.indexOf('async function switchModelDisplay(');
    assert.ok(start >= 0, 'switchModelDisplay 不存在');
    const captureIdx = source.indexOf('const switchGeneration = ++modelDisplaySwitchGeneration;', start);
    assert.ok(captureIdx > start, '链世代号必须在 switchModelDisplay 入口捕获');
    // 捕获必须先于函数内任何 await：入口到捕获点之间不得出现 await
    // （此前用 indexOf('await ', captureIdx) 断言恒真，没有验证力——wehos 指出）
    assert.ok(!source.slice(start, captureIdx).includes('await '), '捕获前不得有 await');

    const pngBranchStart = source.indexOf('await loadPNGTuberModels({ isStale: chainStale });', captureIdx);
    assert.ok(pngBranchStart > captureIdx, 'pngtuber 分支列表加载不存在');
    const previewCallIdx = source.indexOf('await selectAndPreviewFirstPNGTuberModelAfterModeSwitch(', pngBranchStart);
    assert.ok(previewCallIdx > pngBranchStart, 'pngtuber 分支预览调用不存在');
    // 复查必须落在「列表 await 之后、发起预览之前」，且过期时返回 false（链无效）
    const recheckIdx = source.indexOf('if (chainStale()) {', pngBranchStart);
    assert.ok(recheckIdx > pngBranchStart && recheckIdx < previewCallIdx, '链过期复查缺失或位置错误');
    // indexOf 找不到时返回 -1 同样满足 <，必须先断言存在（wehos 第 7 轮指出的恒真断言）
    const staleReturnIdx = source.indexOf('return false;', recheckIdx);
    assert.ok(staleReturnIdx !== -1 && staleReturnIdx < previewCallIdx, '过期链必须返回 false');

    // 函数末尾把链有效性作为返回值传出（isStale 并入由链世代号专门用例断言）
    const fnEndIdx = source.indexOf('_dispatchTutorialEvent();', previewCallIdx);
    assert.ok(fnEndIdx > previewCallIdx);
    const fnTail = source.slice(previewCallIdx, fnEndIdx);
    assert.ok(fnTail.includes('return switchGeneration === modelDisplaySwitchGeneration'));

    // 角色配置加载路径必须消费该返回值：只查 currentModelType 拦不住
    // 「切走又切回」的场景（届时类型复查会通过）
    const charPathIdx = source.indexOf("const switchChainValid = await switchModelDisplay('pngtuber'");
    assert.ok(charPathIdx > 0, '角色配置路径未消费链有效性');
    const charBlock = source.slice(charPathIdx, source.indexOf('const matchedOption = findPNGTuberOptionByConfig(', charPathIdx));
    // 守卫现含三条件（链有效性 / 类型 / 手动选择世代号——后者由专门用例钉住）
    assert.ok(charBlock.includes("if (!switchChainValid || currentModelType !== 'pngtuber'"));

    // 列表加载器必须在 DOM 写入前复查过期（Codex P2@2251）：过期链的列表返回
    // 不得把共享 modelSelect 换成 PNGTuber 选项（用户可能已切到 live2d）
    const loaderStart = source.indexOf('async function loadPNGTuberModels(options = {}) {');
    assert.ok(loaderStart > 0, 'loadPNGTuberModels 未接收 options');
    const loaderEnd = source.indexOf('function clearPNGTuberPreviewControls(', loaderStart);
    const loaderBlock = source.slice(loaderStart, loaderEnd);
    const staleIdx = loaderBlock.indexOf('if (isStale()) return false;');
    const domIdx = loaderBlock.indexOf("modelSelect.innerHTML = '';");
    assert.ok(staleIdx > 0 && domIdx > staleIdx, '加载器的过期复查必须先于 DOM 写入');
    // catch 路径同样不得写 DOM（catchIdx 先断言存在，避免 -1 让位置比较恒真）
    const catchIdx = loaderBlock.indexOf('} catch (error) {');
    assert.ok(catchIdx > 0, 'loader catch 区块不存在');
    const catchStaleIdx = loaderBlock.indexOf('if (isStale()) return false;', catchIdx);
    assert.ok(catchStaleIdx > catchIdx, 'catch 路径缺少过期复查');
    // 类型复查内置于加载器：上传后/删除后刷新等不传 isStale 的调用方也受保护
    // （wehos 第 7 轮可选项 2）
    assert.ok(loaderBlock.includes("currentModelType !== 'pngtuber'"), '加载器缺少内置类型复查');
});

test('控件加载抛异常且预览已被作废：finally 兜底撤销已提交条目', async () => {
    const sandbox = makeSandbox();
    // 头像立即被接受（检查点已提交），随后 metadata fetch 期间切走并抛错
    sandbox.fetchPNGTuberLayeredMetadata = async () => {
        sandbox.currentModelType = 'live2d';
        sandbox.pngtuberPreviewGeneration += 1;
        throw new Error('render boom');
    };
    const result = await runPreview(sandbox);

    assert.equal(result, false);
    // catch 出口也必须撤销（撤销只在 try 之后的出口时，异常路径会把过期条目
    // 留在 live2d 下——wehos 第 6 轮可选项 2）
    assert.equal(sandbox.currentModelInfo, null);
    // 被作废预览的失败静默
    assert.deepEqual(sandbox.statusMessages, []);
    assert.equal(sandbox.pendingPNGTuberPreview, null);
    assert.equal(sandbox.unfinalizedPNGTuberCommit, null);
});

test('cancelInFlightLoad：在途加载作废且丢 config；已完成加载的 config 必须保留', () => {
    // 提取真实的 cancelInFlightLoad 方法执行
    const slice = extractSlice(
        'cancelInFlightLoad() {',
        'async load(config, options = {}) {',
        coreSource);
    // pngtuberLoadSequence 取 cancelPNGTuberAvatarLoads 自增后的值（先序列号后内部作废）
    const sandbox = { pngtuberLoadSequence: 9 };
    vm.createContext(sandbox);
    vm.runInContext(`globalThis.manager = {\n${slice}\n};`, sandbox, { filename: 'cancelInFlightLoad' });
    const manager = sandbox.manager;

    // —— 场景一：在途 load()（loadToken=8 已捕获 gen=6，正挂在 setupLayeredAdapter）——
    manager._loadGeneration = 6;
    manager._latestLifecycleLoadToken = 8;
    manager._inFlightLoadGeneration = 6;
    manager.config = { idle_image: '/user_pngtuber/deleted/idle.png' };
    const captured = { gen: 6, token: 8 };
    // load() 内部 isCurrentLoad 的语义复刻
    const isCurrentLoad = () => (
        captured.gen === manager._loadGeneration
        && (!captured.token || captured.token === manager._latestLifecycleLoadToken)
    );
    assert.equal(isCurrentLoad(), true, '取消前在途加载应有效');

    manager.cancelInFlightLoad();

    // 取消后 isCurrentLoad 双条件均不满足：挂起的 setupLayeredAdapter 解析后
    // 命中 load() 的 return false，不再 setState('idle') 把旧图片写进已可见容器，
    // 也不再挂拖拽监听/悬浮按钮/锁标
    assert.equal(isCurrentLoad(), false, '取消后在途加载必须失效');
    assert.equal(manager._loadGeneration, 7);
    assert.ok(manager._latestLifecycleLoadToken >= 9);
    // 被取消（可能已被删除）模型的路径不得再作为 runtime 配置被合并进 Save；
    // 空对象经保存合并链后无 idle_image，走「配置无效」拦截而非泄漏占位图
    // （vm 跨 realm 对象原型不同一，用键集断言代替 deepEqual）
    assert.equal(Object.keys(manager.config).length, 0);
    assert.equal(manager.config.idle_image, undefined);
    assert.equal(manager._inFlightLoadGeneration, 0, '取消后在途标记必须清零');
    // 下一次新加载（token=10 > 序列号）不受入口检查影响
    assert.equal(10 < manager._latestLifecycleLoadToken, false);

    // —— 场景二：已完成加载（无在途标记）——config 是切回 pngtuber 后
    // 拖拽/状态/保存的数据来源，取消不得清空（Codex P2：切走再切回 +
    // 列表接口慢/失败时，容器先被重新显示，空 config 会让头像退回默认
    // 摆放、状态回退占位图、编辑落到空配置）
    const completedConfig = { idle_image: '/user_pngtuber/m/idle.png', scale: 1.4 };
    manager.config = completedConfig;
    manager._inFlightLoadGeneration = 0;
    manager.cancelInFlightLoad();
    assert.equal(manager.config, completedConfig, '已完成加载的 config 必须原样保留');
    assert.equal(manager._loadGeneration, 8, '世代号仍应推进（作废潜在悬挂闭包）');
});

test('预览失败且仍在 pngtuber 类型：照常报错提示', async () => {
    const sandbox = makeSandbox();
    sandbox.window.loadPNGTuberAvatar = async () => { throw new Error('boom'); };
    const result = await runPreview(sandbox);

    assert.equal(result, false);
    assert.equal(sandbox.statusMessages.length, 1);
    assert.match(sandbox.statusMessages[0], /PNGTuber 模型加载失败: boom/);
    assert.equal(sandbox.currentModelInfo, null);
    assert.equal(sandbox.pendingPNGTuberPreview, null);
});

test('切走后旧预览加载失败（如文件已删 404）：静默丢弃，不弹无关报错', async () => {
    const sandbox = makeSandbox();
    sandbox.window.loadPNGTuberAvatar = async () => {
        // 模拟切走：类型改写 + switchModelDisplay 入口的世代号作废
        sandbox.currentModelType = 'live2d';
        sandbox.pngtuberPreviewGeneration += 1;
        throw new Error('404 Not Found');
    };
    const result = await runPreview(sandbox);

    assert.equal(result, false);
    assert.deepEqual(sandbox.statusMessages, []);
    assert.equal(sandbox.currentModelInfo, null);
    assert.equal(sandbox.pendingPNGTuberPreview, null);
});

test('控件加载期间切走类型：撤销本预览已提交的条目，不把 pngtuber 信息留在 live2d 下', async () => {
    const sandbox = makeSandbox();
    let resolveFetch;
    const gateFetch = new Promise((resolve) => { resolveFetch = resolve; });
    sandbox.fetchPNGTuberLayeredMetadata = async () => {
        await gateFetch;
        return null;
    };

    const preview = runPreview(sandbox);
    await tick();
    // 头像立即被接受（默认桩），检查点已提交本预览的 pngtuber 条目
    assert.equal(sandbox.currentModelInfo.name, 'demo');
    // 模拟摆放暂存：stageModelManagerPNGTuberPlacement 会把 .pngtuber 原地替换成
    // 新的合并对象——撤销判定必须按「条目对象同一性」而非嵌套 .pngtuber 同一性，
    // 否则暂存过的预览被取消时撤销失配，过期条目留在 live2d 下（Codex P2）
    sandbox.currentModelInfo.pngtuber = { idle_image: '/merged/by-placement.png' };
    // 用户在 metadata fetch 期间切到 live2d
    sandbox.currentModelType = 'live2d';
    resolveFetch();
    const result = await preview;

    assert.equal(result, false);
    // 按条目对象同一性撤销：清理的只能是本预览自己提交的条目；
    // 若新流程已写入更新信息（对象不同一），不会被触碰（由重叠预览用例覆盖）
    assert.equal(sandbox.currentModelInfo, null);
    assert.deepEqual(sandbox.statusMessages, []);
    assert.equal(sandbox.pngtuberContainer.style.display, undefined);
    assert.equal(sandbox.pendingPNGTuberPreview, null);
});

test('删除防护：已提交模型与加载中预览必须同时护住（Codex P1 单槽 || 回归）', () => {
    // 提取纯函数 helper 与槽位函数验证行为
    const slice = extractSlice(
        'function isBoundPNGTuberDeleteKey(',
        'async function deleteSelectedModels(');
    const sandbox = { currentModelInfo: null, pendingPNGTuberPreview: null };
    vm.createContext(sandbox);
    vm.runInContext(slice + '\n;globalThis.api = { isBoundPNGTuberDeleteKey, getBoundPNGTuberDeleteKeys };', sandbox, {
        filename: 'isBoundPNGTuberDeleteKey',
    });
    const { isBoundPNGTuberDeleteKey, getBoundPNGTuberDeleteKeys } = sandbox.api;

    // A 已提交显示、B 加载中：两个都必须拦，第三者放行
    assert.equal(isBoundPNGTuberDeleteKey('A', 'A', 'B'), true);
    assert.equal(isBoundPNGTuberDeleteKey('B', 'A', 'B'), true);
    assert.equal(isBoundPNGTuberDeleteKey('C', 'A', 'B'), false);
    // 只有已提交 / 只有 pending
    assert.equal(isBoundPNGTuberDeleteKey('A', 'A', ''), true);
    assert.equal(isBoundPNGTuberDeleteKey('B', '', 'B'), true);
    // 空 key / 空 folder 不参与匹配，避免误拦
    assert.equal(isBoundPNGTuberDeleteKey('', '', ''), false);
    assert.equal(isBoundPNGTuberDeleteKey('', 'A', 'B'), false);
    assert.equal(isBoundPNGTuberDeleteKey('A', '', ''), false);

    // getBoundPNGTuberDeleteKeys：committed 槽按类型门控、folder 缺失回退 name、双槽独立
    let keys = getBoundPNGTuberDeleteKeys();
    assert.equal(keys.committed, '');
    assert.equal(keys.pending, '');
    // live2d 条目不得冒充 pngtuber 绑定槽（跨类型同名误伤）
    sandbox.currentModelInfo = { type: 'live2d', folder: 'shared', name: 'shared' };
    keys = getBoundPNGTuberDeleteKeys();
    assert.equal(keys.committed, '');
    sandbox.currentModelInfo = { type: 'pngtuber', folder: 'A', name: 'a-name' };
    keys = getBoundPNGTuberDeleteKeys();
    assert.equal(keys.committed, 'A');
    sandbox.currentModelInfo = { type: 'pngtuber', folder: '', name: 'a-name' };
    keys = getBoundPNGTuberDeleteKeys();
    assert.equal(keys.committed, 'a-name');
    sandbox.pendingPNGTuberPreview = { folder: 'B', generation: 3 };
    keys = getBoundPNGTuberDeleteKeys();
    assert.equal(keys.committed, 'a-name');
    assert.equal(keys.pending, 'B');

    // 接线断言：deleteSelectedModels 的绑定判定必须封装为逐项实时读取的
    // isDeleteBoundModel（内部经共享槽位函数取双槽），不得回到 || 单槽折叠
    const start = source.indexOf('async function deleteSelectedModels(');
    const confirmIdx = source.indexOf('const confirmDelete = await showConfirm(', start);
    assert.ok(start >= 0 && confirmIdx > start, 'deleteSelectedModels 区块不存在');
    const preCheckBlock = source.slice(start, confirmIdx);
    assert.ok(preCheckBlock.includes('const isDeleteBoundModel = (type, key) => {'));
    assert.ok(preCheckBlock.includes('const boundPNGTuberKeys = getBoundPNGTuberDeleteKeys();'));
    assert.ok(preCheckBlock.includes('isBoundPNGTuberDeleteKey(key, boundPNGTuberKeys.committed, boundPNGTuberKeys.pending)'));
    assert.ok(preCheckBlock.includes('if (isDeleteBoundModel(type, key)) {'));

    // TOCTOU 复查：确认框 await 期间异步流程（角色配置重载/跨窗口切换）可能登记新
    // pending 或提交新模型，删除循环必须在每个 DELETE 前用最新状态复查，
    // 不得沿用确认前的旧快照
    const loopEndIdx = source.indexOf('await loadUserModels();', confirmIdx);
    assert.ok(loopEndIdx > confirmIdx, '删除循环区块不存在');
    const deleteLoopBlock = source.slice(confirmIdx, loopEndIdx);
    assert.ok(deleteLoopBlock.includes('if (isDeleteBoundModel(type, key)) {'));
    assert.ok(deleteLoopBlock.includes('skippedBoundCount'));
    // 删除循环必须「遍历快照 + 逐项 has()」：删除期间弹窗仍可交互——
    // 中途取消/取消勾选（hideDeleteModelModal 会 clear()）要跳过未访问项（wehos 第 3 轮 🔴），
    // 中途新勾选的项没经过确认框、不得被访问并删除（wehos 第 4 轮更正）。
    // 纯活遍历会删掉新勾选项，纯副本会无视取消，两者都不合格。
    assert.ok(deleteLoopBlock.includes('for (const modelId of [...selectedDeleteModels]) {'));
    assert.ok(deleteLoopBlock.includes('if (!selectedDeleteModels.has(modelId)) continue;'));
    // 结果弹窗必带跳过数量后，循环后的 2 秒状态条属于重复反馈，不得保留
    // （确认前过滤循环的同款提示在 confirmIdx 之前，不在本区块内）
    assert.ok(!deleteLoopBlock.includes("showStatus(t('live2d.cannotDeleteBoundModel'"));

    // 结果弹窗：有跳过时成功/失败弹窗附数量；全部被拦时弹跳过说明而非「失败 0 个」误报
    const fnEndIdx = source.indexOf('if (deleteModelBtn) {', loopEndIdx);
    const tailBlock = source.slice(loopEndIdx, fnEndIdx);
    assert.ok(tailBlock.includes('} else if (failCount > 0) {'));
    assert.ok(tailBlock.includes('} else if (skippedMessage) {'));
    // 文案只计算一次，全拦下分支复用同一段（wehos 第 5 轮可选项）
    assert.equal(tailBlock.split("t('live2d.deleteSkippedBound'").length - 1, 1);
    assert.ok(tailBlock.includes('count: skippedBoundCount'));
    assert.equal(tailBlock.split('const skippedMessage =').length - 1, 1);
    assert.equal(tailBlock.split('const skippedPart =').length - 1, 1);

    // 删除弹窗 UI 必须复用同一 helper 与同一槽位来源：被禁用的即会被拦截的。
    // 截取起点取 UI 函数的槽位声明之前（wehos 第 2 轮：起点过晚会漏掉
    // 「弹窗槽位来自共享函数」这一接线）
    const uiStart = source.indexOf("userModelList.innerHTML = '';");
    assert.ok(uiStart > 0, '删除弹窗渲染区块不存在');
    const uiBlock = source.slice(uiStart, source.indexOf('const checkbox = document.createElement', uiStart));
    assert.ok(uiBlock.includes('const boundPNGTuberKeys = getBoundPNGTuberDeleteKeys();'));
    assert.ok(uiBlock.includes('isBound = isBoundPNGTuberDeleteKey('));
    assert.ok(uiBlock.includes('boundPNGTuberKeys.committed'));
    assert.ok(uiBlock.includes('boundPNGTuberKeys.pending'));
});

test('live2d.deleteSkippedBound 在全部 8 个语言文件中就位', () => {
    const localesDir = path.join(PROJECT_ROOT, 'static', 'locales');
    const expected = ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'];
    for (const lang of expected) {
        const data = JSON.parse(fs.readFileSync(path.join(localesDir, `${lang}.json`), 'utf8'));
        assert.ok(data.live2d, `${lang}: 缺少 live2d 段`);
        const value = data.live2d.deleteSkippedBound;
        assert.equal(typeof value, 'string', `${lang}: 缺少 live2d.deleteSkippedBound`);
        assert.ok(value.includes('{{count}}'), `${lang}: deleteSkippedBound 缺少 {{count}} 插值`);
    }
});

test('hideOther：removeModel 挂起期间被取消/切走后，不得隐藏刚显示的 live2d（CodeRabbit Minor）', async () => {
    const slice = extractSlice(
        'async function hideOtherAvatarRuntimesForPNGTuber(options = {}) {',
        'async function loadPNGTuberAvatar(config) {',
        coreSource);

    function makeHelperSandbox() {
        const containers = {};
        const containerStub = () => ({ style: {}, classList: { add() {}, remove() {} } });
        let resolveRemove;
        const removeGate = new Promise((resolve) => { resolveRemove = resolve; });
        const sandbox = {
            console: { warn() {}, error() {}, log() {} },
            pngtuberLoadSequence: 5,
            document: {
                body: { classList: { contains: (name) => name === 'model-manager-page' } },
                getElementById: (id) => (containers[id] = containers[id] || containerStub()),
                querySelectorAll: () => [],
            },
            window: {
                _modelManagerCurrentAvatarType: 'pngtuber',
                live2dManager: {
                    _activeLoadToken: 0,
                    removeModel: async () => { await removeGate; },
                },
            },
            containers,
            resolveRemove,
        };
        vm.createContext(sandbox);
        vm.runInContext(slice + '\n;globalThis.api = { hideOtherAvatarRuntimesForPNGTuber };', sandbox, {
            filename: 'hideOtherAvatarRuntimesForPNGTuber',
        });
        return sandbox;
    }

    const tick = () => new Promise((resolve) => setImmediate(resolve));

    // 场景一：removeModel 挂起期间用户切到 live2d（离开块已作废本次 token、
    // live2d 分支已显示容器与画布）——过期调用归来不得再写隐藏
    const sb = makeHelperSandbox();
    const staleCall = sb.api.hideOtherAvatarRuntimesForPNGTuber({ loadToken: 5 });
    await tick(); // 进入 removeModel 挂起点
    sb.pngtuberLoadSequence = 6; // cancelPNGTuberAvatarLoads 已自增序列号
    sb.window._modelManagerCurrentAvatarType = 'live2d';
    const liveShown = sb.document.getElementById('live2d-container');
    liveShown.style.display = 'block';
    const canvasShown = sb.document.getElementById('live2d-canvas');
    canvasShown.style.visibility = 'visible';
    sb.resolveRemove();
    await staleCall;
    assert.equal(liveShown.style.display, 'block', '过期调用不得隐藏 live2d 容器');
    assert.equal(canvasShown.style.visibility, 'visible', '过期调用不得隐藏 live2d 画布');

    // 场景二：新鲜调用（token 最新、类型仍为 pngtuber）照常执行隐藏
    const sb2 = makeHelperSandbox();
    const freshCall = sb2.api.hideOtherAvatarRuntimesForPNGTuber({ loadToken: 5 });
    await tick();
    sb2.resolveRemove();
    await freshCall;
    assert.equal(sb2.document.getElementById('live2d-container').style.display, 'none');
    assert.equal(sb2.document.getElementById('vrm-container').style.display, 'none');

    // 场景三：主 app 语义不变——不传 loadToken 时不做 token 复查
    const sb3 = makeHelperSandbox();
    sb3.pngtuberLoadSequence = 99; // 即便序列号前进也不影响无 token 调用
    const mainAppCall = sb3.api.hideOtherAvatarRuntimesForPNGTuber();
    await tick();
    sb3.resolveRemove();
    await mainAppCall;
    assert.equal(sb3.document.getElementById('live2d-container').style.display, 'none');
});

test('角色自动加载链携带手动选择世代号：同模式手选使自动加载让位（Codex P1）', () => {
    // 场景：记忆模式已是 pngtuber、初始化已填充并启用下拉，loadCurrentCharacterModel
    // 挂在 /api/characters；用户此时选了 B（只推进预览世代号，不触发 switchModelDisplay，
    // 链世代号无感知）；请求返回后角色链若继续预览角色模型 A，会给 A 领到更新的
    // 预览世代号、反向顶掉 B。
    assert.ok(source.includes('let userModelSelectionGeneration = 0;'), '缺少手动选择世代号声明');

    // 自增点：真实用户（非 suppress）的模型下拉选择，且只在「选择确实被接受」后计数
    const modelSelHandler = source.indexOf("modelSelect.addEventListener('change', async (e) => {");
    assert.ok(modelSelHandler > 0);
    const pngBranchIdx = source.indexOf("if (currentModelType === 'pngtuber') {", modelSelHandler);
    assert.ok(pngBranchIdx > modelSelHandler);
    // 入口到 pngtuber 分支之间不得有自增（Codex P2@6698：语音模式拒绝路径之前
    // 自增，会让被挡回的选择误废角色自动加载链）
    assert.ok(!source.slice(modelSelHandler, pngBranchIdx).includes('userModelSelectionGeneration += 1;'),
        '自增不得位于选择被接受之前');
    // pngtuber 分支（无拒绝路径，选择即接受）内自增，且以非 suppress 为门槛
    const pngLoadIdx = source.indexOf('await loadSelectedPNGTuberOption(', pngBranchIdx);
    const pngBranchBlock = source.slice(pngBranchIdx, pngLoadIdx);
    assert.ok(pngBranchBlock.includes('userModelSelectionGeneration += 1;'));
    assert.ok(pngBranchBlock.includes('!isSuppressedModelManagerChangeEvent(e)'));
    // 自增必须以「选项确为 pngtuber 模型」为门槛：类型切换过渡窗口里下拉可能仍是
    // 旧 live2d 选项，选中会被 loadSelectedPNGTuberOption 按 dataset 拒绝，
    // 被拒绝的选择不得作废角色自动加载链（Codex P2@6724）
    assert.ok(pngBranchBlock.includes("selectedOption.dataset.modelType === 'pngtuber'"),
        'pngtuber 分支自增必须先验证选项类型');
    // 配置有效性门槛：后端按 model_type 标记列目录、不做包校验，坏包（idle_image
    // 为空）会进下拉并被 previewPNGTuberConfig 入口拒绝，同样不得计数（Codex P2@6748）
    assert.ok(pngBranchBlock.includes("JSON.parse(selectedOption.getAttribute('data-pngtuber')"),
        'pngtuber 分支自增前必须解析选项配置');
    const idleGateIdx = pngBranchBlock.indexOf('if (optionHasIdleImage) {');
    const bumpIdxInBranch = pngBranchBlock.indexOf('userModelSelectionGeneration += 1;');
    assert.ok(idleGateIdx > 0 && bumpIdxInBranch > idleGateIdx,
        '自增必须位于 idle_image 有效性门槛之内');
    // live2d 路径：自增必须在语音检查之后、且选择成功匹配到有效 Live2D 模型之后
    // （Codex P2@6770 / wehos 第 12 轮可选1：过渡期下拉残留的其他类型旧选项
    // 会被 findLive2DModelBySelection 匹配为 null，这类被拒绝的点选不得计数）
    const voiceIdx = source.indexOf('const voiceStatus = await checkVoiceModeStatus();', pngLoadIdx);
    const live2dCommitIdx = source.indexOf('currentModelInfo = findLive2DModelBySelection(', voiceIdx);
    const live2dNullCheckIdx = source.indexOf('if (!currentModelInfo) return;', live2dCommitIdx);
    const live2dBumpIdx = source.indexOf('userModelSelectionGeneration += 1;', live2dCommitIdx);
    assert.ok(voiceIdx > pngLoadIdx && live2dCommitIdx > voiceIdx, 'live2d 路径结构变化');
    assert.ok(live2dNullCheckIdx > live2dCommitIdx, 'live2d 空匹配检查不存在');
    assert.ok(live2dBumpIdx > live2dNullCheckIdx,
        'live2d 自增必须落在语音检查与空匹配拒绝之后');
    // vrmModelSelect 同理：自增在语音检查之后，且以非 suppress 为门槛
    const vrmHandler = source.indexOf("vrmModelSelect.addEventListener('change', async (e) => {");
    assert.ok(vrmHandler > 0);
    const vrmVoiceIdx = source.indexOf('const voiceStatus = await checkVoiceModeStatus();', vrmHandler);
    const vrmBumpIdx = source.indexOf('userModelSelectionGeneration += 1;', vrmHandler);
    assert.ok(vrmVoiceIdx > vrmHandler, 'vrm 语音检查不存在');
    assert.ok(vrmBumpIdx > vrmVoiceIdx, 'vrmModelSelect 自增必须在语音检查之后');
    assert.ok(source.slice(vrmVoiceIdx, vrmBumpIdx).includes('!isSuppressedModelManagerChangeEvent(e)'),
        'vrm 自增必须以非 suppress 为门槛');

    // 角色加载链：捕获先于链内第一个 await；/api/characters 返回后复查；
    // pngtuber 路径守卫并入同一复查（覆盖 switchModelDisplay 期间的手选）
    const fnStart = source.indexOf('async function loadCurrentCharacterModel() {');
    assert.ok(fnStart > 0, 'loadCurrentCharacterModel 不存在');
    const captureIdx = source.indexOf('const selectionGenerationAtStart = userModelSelectionGeneration;', fnStart);
    const firstAwaitIdx = source.indexOf('await getLanlanName();', fnStart);
    const fetchIdx = source.indexOf("await RequestHelper.fetchJson('/api/characters');", fnStart);
    assert.ok(captureIdx > fnStart && captureIdx < firstAwaitIdx, '捕获必须先于链内第一个 await');
    // 世代复查必须在 catgirlConfig 提取之后，且让位 return 前补记休眠 Live2D 绑定
    // （wehos 🔴 / Codex P2@9354：提前 return 吞掉 rememberDormant 会让之后切
    // live2d 读到跨角色的旧选择；live2d 模式下不补记，避免覆盖手选刚写入的同一全局槽）
    const configExtractIdx = source.indexOf("const catgirlConfig = charactersData['猫娘']?.[lanlanName];", fetchIdx);
    const postFetchCheck = source.indexOf('if (selectionGenerationAtStart !== userModelSelectionGeneration) {', configExtractIdx);
    assert.ok(configExtractIdx > fetchIdx, 'catgirlConfig 提取必须在 fetch 之后');
    assert.ok(postFetchCheck > configExtractIdx, '世代复查必须在 catgirlConfig 提取之后');
    const staleReturnIdx = source.indexOf('return;', postFetchCheck);
    const staleBlock = source.slice(postFetchCheck, staleReturnIdx);
    assert.ok(staleBlock.includes("if (currentModelType !== 'live2d') {"),
        '让位分支的休眠绑定补记必须带 live2d 模式门槛');
    assert.ok(staleBlock.includes('rememberDormantLive2DModelFromCharacterConfig(catgirlConfig, lanlanName);'),
        '让位分支必须补记休眠 Live2D 绑定');
    const pngGuardIdx = source.indexOf('|| selectionGenerationAtStart !== userModelSelectionGeneration) return;', fetchIdx);
    assert.ok(pngGuardIdx > postFetchCheck, 'pngtuber 路径守卫必须并入手动选择世代号复查');

    // 手动选择世代号必须随 options.isStale 进入切换链内部（wehos 🔴 / Codex P1@2274：
    // 链内 selectAndPreview 会在调用方复查之前先为角色模型发起预览）
    const charSwitchIdx = source.indexOf("const switchChainValid = await switchModelDisplay('pngtuber'", fnStart);
    assert.ok(charSwitchIdx > fnStart);
    const charSwitchBlock = source.slice(charSwitchIdx, source.indexOf('});', charSwitchIdx));
    assert.ok(charSwitchBlock.includes('isStale: () => selectionGenerationAtStart !== userModelSelectionGeneration'),
        '角色配置路径必须把手动选择世代号复查注入切换链');
    // 链内合并判定：chainStale = 链世代号 || 调用方 isStale
    const chainStaleIdx = source.indexOf('const chainStale = () => switchGeneration !== modelDisplaySwitchGeneration');
    assert.ok(chainStaleIdx > 0, 'chainStale 定义不存在');
    const chainStaleBlock = source.slice(chainStaleIdx, source.indexOf('await loadPNGTuberModels({ isStale: chainStale });', chainStaleIdx));
    assert.ok(chainStaleBlock.includes('options.isStale'), 'chainStale 必须并入调用方注入的 isStale');
    // 末尾返回值同样计入 isStale
    const tailReturnIdx = source.indexOf('return switchGeneration === modelDisplaySwitchGeneration', chainStaleIdx);
    assert.ok(tailReturnIdx > chainStaleIdx);
    assert.ok(source.slice(tailReturnIdx, tailReturnIdx + 300).includes('options.isStale'),
        '链有效性返回值必须并入调用方 isStale');
});

test('A 已提交被 B 取代但 B 未提交：A 条目保留，双槽防护完整（Codex P2@1497）', async () => {
    const sandbox = makeSandbox();
    let resolveFetchA;
    let resolveAvatarB;
    const gateFetchA = new Promise((resolve) => { resolveFetchA = resolve; });
    const gateAvatarB = new Promise((resolve) => { resolveAvatarB = resolve; });
    let avatarCalls = 0;
    sandbox.window.loadPNGTuberAvatar = async () => {
        avatarCalls += 1;
        if (avatarCalls === 2) await gateAvatarB; // B 的头像加载很慢
    };
    sandbox.fetchPNGTuberLayeredMetadata = async (config) => {
        if (String(config.idle_image).includes('/a/')) {
            await gateFetchA;
        }
        return null;
    };

    const previewA = runPreview(sandbox, { name: 'A', idle: '/user_pngtuber/a/idle.png' });
    await tick();
    // A 已过检查点提交，挂在 metadata fetch
    assert.equal(sandbox.currentModelInfo.name, 'A');

    // 用户选 B：B 登记 pending、挂在头像加载（尚未提交）
    const previewB = runPreview(sandbox, { name: 'B', idle: '/user_pngtuber/b/idle.png' });
    assert.equal(sandbox.pendingPNGTuberPreview.folder, 'B');

    // A 的 fetch 归来：A 被取代退出，但已提交的条目必须保留——屏上头像仍是 A，
    // 「committed=A + pending=B」正是删除防护双槽的设计状态；若置 null，
    // B 挂起/失败时页面没有可保存/可暂存的记录，A 的 folder 也失去删除防护
    resolveFetchA();
    const resultA = await previewA;
    assert.equal(resultA, false);
    assert.equal(sandbox.currentModelInfo.name, 'A', '被取代 ≠ 被撤销：B 提交前 A 条目保留');
    assert.equal(sandbox.pendingPNGTuberPreview.folder, 'B');
    // 登记必须保留（wehos 🔴）：A 条目仍是 currentModelInfo 且本预览未成功收尾——
    // 若此时清掉登记，B 提交前用户切走类型，离开块查不到登记、撤销不到 A，
    // A 的 pngtuber 条目会滞留在新类型下
    assert.equal(sandbox.unfinalizedPNGTuberCommit, sandbox.currentModelInfo,
        'B 提交前登记必须保留，供离开块代行撤销');

    // B 提交后覆盖 A
    resolveAvatarB();
    const resultB = await previewB;
    assert.equal(resultB, true);
    assert.equal(sandbox.currentModelInfo.name, 'B');
    assert.equal(sandbox.unfinalizedPNGTuberCommit, null, 'B 成功收尾后登记应清除');
});

test('世代号声明位置：必须先于初始化首个 switchModelDisplay 调用（TDZ）', () => {
    // 整个控制器在 DOMContentLoaded 的 async 回调里自上而下顺序执行：函数声明提升
    // 可达，但 let 绑定在声明语句执行前处于 TDZ——初始化在声明之前调用
    // switchModelDisplay 会抛 ReferenceError，被 catch 吞成「切换显示模式失败」，
    // 持久化的模型类型恢复静默失效（Codex P1@2132，wehos 确认为阻塞级）
    const firstCallIdx = source.indexOf('await switchModelDisplay(savedModelType, savedSubType);');
    assert.ok(firstCallIdx > 0, '初始化调用点不存在');
    const declarations = [
        'let modelDisplaySwitchGeneration = 0;',
        'let userModelSelectionGeneration = 0;',
        'let pngtuberPreviewGeneration = 0;',
        'let pendingPNGTuberPreview = null;',
        'let unfinalizedPNGTuberCommit = null;',
    ];
    for (const decl of declarations) {
        const idx = source.indexOf(decl);
        assert.ok(idx > 0, `缺少声明: ${decl}`);
        assert.ok(idx < firstCallIdx, `声明必须先于初始化首个 switchModelDisplay 调用: ${decl}`);
    }
});

test('上传流程：列表刷新过期时不得继续选中/兜底直载（Codex P2@3193）', () => {
    const uploadIdx = source.indexOf("await fetch('/api/model/pngtuber/upload_model'");
    assert.ok(uploadIdx > 0, '上传流程不存在');
    const uploadBlock = source.slice(uploadIdx, source.indexOf('uploadBtn.disabled = false;', uploadIdx));
    // 刷新结果必须被消费：过期（false）时跳过「选中新模型/兜底 loadPNGTuberAvatar」，
    // 否则会后台替换运行时 config，切回时凭空出现导入头像甚至被保存
    assert.ok(uploadBlock.includes('const refreshed = await loadPNGTuberModels();'));
    assert.ok(uploadBlock.includes('if (refreshed !== false && result.folder && modelSelect) {'));
});

test('previewPNGTuberConfig 的时效判定收敛为单一 isCurrentPreview 闭包（wehos 第 12 轮可选3）', () => {
    const start = source.indexOf('async function previewPNGTuberConfig(');
    const end = source.indexOf('async function loadSelectedPNGTuberOption(', start);
    assert.ok(start >= 0 && end > start, 'previewPNGTuberConfig 区块不存在');
    const block = source.slice(start, end);
    // 单一闭包定义（语义为「未被取消/未过期」，四处检查点共用）
    assert.ok(block.includes('const isCurrentPreview = () => previewGeneration === pngtuberPreviewGeneration'));
    // 散落谓词只允许存在于闭包内部：gen 比对（=== 形态，闭包 1 处、无 !== 变体）；
    // 类型比对在入口守卫与闭包各 1 次
    assert.equal(block.split('previewGeneration === pngtuberPreviewGeneration').length - 1, 1);
    assert.equal(block.split('previewGeneration !== pngtuberPreviewGeneration').length - 1, 0);
    assert.equal(block.split("currentModelType !== 'pngtuber'").length - 1, 2);
    // 三处消费点都在
    assert.ok(block.includes('if (!isCurrentPreview()) {'));
    assert.ok(block.includes('if (isCurrentPreview()) {'));
    assert.ok(block.includes('if (isCurrentPreview() || currentModelInfo !== committedInfo) {'));
});

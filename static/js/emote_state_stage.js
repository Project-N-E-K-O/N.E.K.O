/*
 * 状态表演台 — the single advanced editor for what an exported avatar pack does.
 *
 * Scope discipline: the tabs upstream already decide skins / slot tints.
 * This surface owns only the three things that are genuinely about the avatar itself:
 *   1. which animation each of the eight states uses,
 *   2. which part of it plays (the whole loop, or a range picked on the film strip),
 *   3. how big and how tilted that state sits on the desktop.
 * Anything else that lived here would be a second place to set one thing.
 *
 * The data edited is the node graph in emote_node_graph.js — a tile is an output.state
 * node. The graph stays the model and the thing that gets persisted; this is its face.
 * normalizeGraph() rebuilds every node from scratch, so no render-time cache may be hung
 * on a node: it would vanish on the next edit, and pixel data would leak into the saved JSON.
 *
 * Design: an animator's light table. One accent (走带蓝) for selection and the playhead,
 * rules drawn only where they carry information, and motion that only answers the user.
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmoteStateStage = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const TILE_STATES = [
        'idle_image', 'talking_image', 'drag_image', 'click_image',
        'happy_image', 'sad_image', 'angry_image', 'surprised_image',
    ];
    const MIN_SPAN_SECONDS = 0.05;
    const HANDLE_HIT_PX = 9;
    const SNAP_DEGREES = [0, 90, -90, 180, -180];
    const SNAP_WINDOW = 2.5;
    let stripW = 900;   // 逻辑宽度＝CSS 宽度；画布后备存储另按 dpr 放大，见 sizeCanvases()
    const STRIP_HEIGHT = 84;   // 画面 56 + 秒尺 18 + 区间括号 10
    // 没有上游场景节点＝跟随默认外观。它也是一帧源，所以给它一个和 sceneId 同族的键。
    const ROOT_SCENE = '__default__';
    // 没有上游动画时，帧源是"默认外观 + 这一格自己的 look"——每格都不同，
    // 所以缓存键不能共用 ROOT_SCENE，否则待机借用了开心的画面。
    const ROOT_PREFIX = 'root:';

    function dpr() {
        return Math.max(1, Math.min(3, Number(globalThis.devicePixelRatio) || 1));
    }

    function clamp(value, min, max) {
        const number = Number(value);
        if (!Number.isFinite(number)) return min;
        return Math.min(max, Math.max(min, number));
    }

    function secondsLabel(value) {
        return `${clamp(value, 0, 600).toFixed(2)}s`;
    }

    function stateLabel(stateKey) {
        const camel = stateKey.replace(/_image$/, '').replace(/^./, (c) => c.toUpperCase());
        return `emoteMaker.packState${camel}`;
    }

    // 用户图片清单的模块级持有者：setUserImages 注入，create 之后也能换/刷
    let userImagesProvider = () => [];

    function create(options) {
        const model = options.model;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const onChange = typeof options.onChange === 'function' ? options.onChange : () => {};
        const onCommit = typeof options.onCommit === 'function' ? options.onCommit : () => {};

        const emptyList = () => [];
        // framesFor(sceneId, quality) -> frame[] | Promise<frame[]>
        // frame = {width,height,data,delayMs}。键是**动画**而不是状态，同一条动画被
        // 多个状态用到时只取一次；quality 让悬停能用便宜的档位先动起来。
        const framesFor = typeof options.framesFor === 'function' ? options.framesFor : emptyList;
        const onPickerClose = typeof options.onPickerClose === 'function' ? options.onPickerClose : () => {};
        // 起拖后 swallow 掉紧随其后的点击（从松手那一刻计时，不是从起拖）
        const rowClickGuard = typeof window.NekoEmoteDragHold?.createClickGuard === 'function'
            ? window.NekoEmoteDragHold.createClickGuard() : null;
        // 长按某条动作 → 交给调用方的拖拽机制（落点是右侧状态行，那里只有它认得）
        const beginDrag = typeof options.beginDrag === 'function' ? options.beginDrag : () => {};
        const UPGRADE_AFTER_MS = 240;
        const PREVIEW_CARD_W = 150;    // 检视窗画布：台面被遮住后唯一的"看一眼"地方
        const PREVIEW_CARD_H = 176;
        const animationsFor = typeof options.animationsFor === 'function' ? options.animationsFor : emptyList;
        // 用户图片：清单（{id, label, thumb}）走模块级 setUserImages 注入
        // （导出对象是 frozen 的，create 之后没法从外面换 options 上的函数）
        const userImages = typeof options.userImages === 'function' ? options.userImages : () => userImagesProvider();
        const onImportImages = typeof options.onImportImages === 'function' ? options.onImportImages : () => {};
        const fpsOf = () => Math.max(1, Number(
            typeof options.fps === 'function' ? options.fps() : options.fps,
        ) || 12);
        const formatIsFlat = () => (
            typeof options.format === 'function' ? options.format() : options.format
        ) === 'flat';
        const stateUsesStillFrame = (stateKey) => stateKey === 'talking_image' && formatIsFlat();

        let stageSize = 460;     // 舞台的逻辑边长（正方形）

        let canvasRatio = 0;

        function sizeCanvases() {
            const ratio = dpr();
            const stripBox = stripCanvas.getBoundingClientRect();
            const stageBox = stage.getBoundingClientRect();
            const wantedW = Math.max(320, Math.round(stripBox.width || stripW));
            const wantedStage = Math.max(160, Math.round(stageBox.width || stageSize));
            if (wantedW === stripW && wantedStage === stageSize && ratio === canvasRatio) {
                return false;
            }
            stripW = wantedW;
            stageSize = wantedStage;
            canvasRatio = ratio;
            stripCanvas.width = Math.round(stripW * ratio);
            stripCanvas.height = Math.round(STRIP_HEIGHT * ratio);
            stage.width = Math.round(stageSize * ratio);
            stage.height = Math.round(stageSize * ratio);
            filmCache.canvas = null;   // 胶片图要按新分辨率重画
            return true;
        }

        function context2d(canvas) {
            const ctx = canvas.getContext('2d');
            if (!ctx) return null;
            // 一切绘制都用逻辑坐标，缩放交给这一次 transform：字号和 1px 细线
            // 才不会随 dpr 变粗变细。
            ctx.setTransform(dpr(), 0, 0, dpr(), 0, 0);
            if ('imageSmoothingQuality' in ctx) ctx.imageSmoothingQuality = 'high';
            ctx.globalAlpha = 1;
            return ctx;
        }

        let graph = model.normalizeGraph(options.graph || model.defaultGraph());
        // 选中的那一格：id 而不是 stateKey，因为一个状态可能有多条输出（旧的图里也合法）
        let selected = (graph.nodes.find((node) => model.typeOf(node)?.category === 'output') || {}).id || null;
        const framesBySource = new Map();  // `${scene}|${quality}` -> 该动画的帧
        let framesRevision = 0;
        let imageFramesRevision = 0;
        const thumbsByScene = new Map();   // scene -> dataURL（状态行和动作库共用）
        const sliceByNode = new Map();     // nodeId  -> 落在起止范围内的帧
        let previewScene = null;           // 悬停试演的那条动画，不落进图里
        let previewQuality = 'poster';
        let upgradeTimer = 0;
        let thumbWatcher = null;
        let playhead = 0;
        let playTimer = 0;
        let stripDrag = null;
        let stripBefore = null;
        let stageDrag = null;
        let disposed = false;
        let selectionRequest = 0;

        // ── shell ────────────────────────────────────────────────────────────
        const overlay = document.createElement('div');
        overlay.className = 'st-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', t('emoteMaker.stageTitle'));

        const head = document.createElement('header');
        head.className = 'st-head';
        const title = document.createElement('h2');
        title.className = 'st-title';
        title.textContent = t('emoteMaker.stageTitle');
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'st-close';
        closeBtn.textContent = t('emoteMaker.stageClose');
        head.append(title, closeBtn);

        const body = document.createElement('div');
        body.className = 'st-body';

        const stageWrap = document.createElement('div');
        stageWrap.className = 'st-stage-wrap';
        const stage = document.createElement('canvas');
        stage.className = 'st-stage';
        stage.width = 360;
        stage.height = 360;
        stage.setAttribute('aria-label', t('emoteMaker.stageCanvasAria'));
        const stageGestureHint = document.createElement('p');
        stageGestureHint.className = 'st-gesture';
        stageGestureHint.textContent = t('emoteMaker.stageGestureHint');
        const stageNotice = document.createElement('p');
        stageNotice.className = 'st-notice';
        stageNotice.textContent = t('emoteMaker.stagePoseNeedsLayered');
        stageNotice.hidden = true;
        // 动作库抽屉：从台面左缘滑出，盖住舞台（舞台在毛玻璃后隐约可见，
        // 这样"我在给哪个位置挑动作"始终看得见）。把手骑在抽屉边缘一起移动，
        // 所以开着关着都只有一处可点。
        const dock = document.createElement('div');
        dock.className = 'st-dock';
        dock.setAttribute('aria-hidden', 'false');

        const rail = document.createElement('button');
        rail.type = 'button';
        rail.className = 'st-rail';
        rail.setAttribute('aria-controls', 'st-drawer');
        rail.setAttribute('aria-expanded', 'false');
        rail.setAttribute('aria-label', t('emoteMaker.stageLibrary'));
        const railChevron = document.createElement('span');
        railChevron.className = 'st-rail-chevron';
        railChevron.textContent = '›';   // ›  展开时翻转成 ‹
        const railLabel = document.createElement('span');
        railLabel.className = 'st-rail-text';
        railLabel.textContent = t('emoteMaker.stageLibrary');
        rail.append(railChevron, railLabel);

        const drawer = document.createElement('section');
        drawer.className = 'st-drawer';
        drawer.inert = true;
        drawer.id = 'st-drawer';
        drawer.setAttribute('aria-label', t('emoteMaker.stageLibrary'));
        const drawerHead = document.createElement('div');
        drawerHead.className = 'st-drawer-head';
        // 胶囊切换：动作库 / 用户图片——两个库并列，不再一个区压在另一个区头上
        const seg = document.createElement('div');
        seg.className = 'list-segment';
        seg.setAttribute('role', 'tablist');
        const segScene = document.createElement('button');
        segScene.type = 'button';
        segScene.className = 'list-segment-btn is-active';
        segScene.setAttribute('role', 'tab');
        segScene.setAttribute('aria-selected', 'true');
        segScene.textContent = t('emoteMaker.stageLibrary');
        const segImage = document.createElement('button');
        segImage.type = 'button';
        segImage.className = 'list-segment-btn';
        segImage.setAttribute('role', 'tab');
        segImage.setAttribute('aria-selected', 'false');
        segImage.textContent = t('emoteMaker.stageUserImages');
        seg.append(segScene, segImage);
        const drawerCount = document.createElement('span');
        drawerCount.className = 'st-drawer-count';
        drawerHead.append(seg, drawerCount);
        // 一个动作列表在几十条时能靠滚动，到几百条就只能靠筛
        const search = document.createElement('input');
        search.type = 'search';
        search.className = 'st-library-search';
        search.setAttribute('aria-label', t('emoteMaker.stageSearch'));
        search.placeholder = t('emoteMaker.stageSearch');
        let libraryQuery = '';
        const library = document.createElement('div');
        library.className = 'st-library';
        library.setAttribute('role', 'listbox');
        library.setAttribute('aria-label', t('emoteMaker.stageAnimation'));
        const listScroll = document.createElement('div');
        listScroll.className = 'st-library-scroll';
        listScroll.append(library);
        search.addEventListener('input', () => {
            libraryQuery = String(search.value || '').trim().toLowerCase();
            renderLibrary();
            // 只有改筛选条件才回顶部；划过列表、拖时间轴都不该动滚动位置
            listScroll.scrollTop = 0;
        });
        let imageTab = false;
        const applyTab = () => {
            segScene.classList.toggle('is-active', !imageTab);
            segScene.setAttribute('aria-selected', String(!imageTab));
            segImage.classList.toggle('is-active', imageTab);
            segImage.setAttribute('aria-selected', String(imageTab));
            search.hidden = imageTab;
            library.style.display = imageTab ? 'none' : '';
            imagePanel.hidden = !imageTab;
            renderUserImages();
            const total = imageTab ? userImages().length : animationsFor().length;
            drawerCount.textContent = String(total);
            listScroll.scrollTop = 0;
        };
        segScene.addEventListener('click', () => {
            if (imageTab) { imageTab = false; applyTab(); }
        });
        segImage.addEventListener('click', () => {
            if (!imageTab) { imageTab = true; applyTab(); }
        });

        // 用户图片面板：导入按钮 + 缩略图格 + 空态，切到「用户图片」时才显示
        const imagePanel = document.createElement('div');
        imagePanel.className = 'st-userimg-panel';
        imagePanel.hidden = true;
        const imageImport = document.createElement('button');
        imageImport.type = 'button';
        imageImport.className = 'st-userimg-import';
        imageImport.textContent = t('emoteMaker.stageImportImages');
        imageImport.addEventListener('click', () => onImportImages());
        const userImageGrid = document.createElement('div');
        userImageGrid.className = 'st-userimg-grid';
        userImageGrid.setAttribute('role', 'listbox');
        userImageGrid.setAttribute('aria-label', t('emoteMaker.stageUserImages'));
        const userImageEmpty = document.createElement('p');
        userImageEmpty.className = 'st-userimg-empty';
        userImageEmpty.textContent = t('emoteMaker.stageNoUserImages');
        imagePanel.append(imageImport, userImageGrid, userImageEmpty);

        // 检视窗：悬停哪条就演哪条；没悬停时演这一格真正选中的那条。
        // 台面被抽屉完全遮住后它代替舞台成为"看一眼"的地方。
        const previewCard = document.createElement('div');
        previewCard.className = 'st-lib-preview';
        const previewCanvas = document.createElement('canvas');
        previewCanvas.className = 'st-lib-preview-canvas';
        const previewName = document.createElement('span');
        previewName.className = 'st-lib-preview-name';
        previewCard.append(previewCanvas, previewName);
        const drawerMain = document.createElement('div');
        drawerMain.className = 'st-drawer-main';
        // 搜索框放在列表列里：它筛的是这一列，不是整块抽屉
        listScroll.append(search, library, imagePanel);
        drawerMain.append(listScroll, previewCard);
        drawer.append(drawerHead, drawerMain);

        dock.append(drawer, rail);
        stageWrap.append(stage, dock, stageGestureHint, stageNotice);

        const shelf = document.createElement('div');
        shelf.className = 'st-shelf';
        shelf.setAttribute('role', 'listbox');
        shelf.setAttribute('aria-label', t('emoteMaker.stageShelfAria'));

        body.append(stageWrap, shelf);

        const strip = document.createElement('div');
        strip.className = 'st-strip';
        // 仪表带：这一格"用什么动画、演哪一段、多大多角度"一行读完，
        // 数字用等宽，位置固定，拖动时只有数字在跳。
        const band = document.createElement('div');
        band.className = 'st-band';
        const animButton = document.createElement('button');
        animButton.type = 'button';
        animButton.className = 'st-anim';
        animButton.setAttribute('aria-haspopup', 'listbox');
        animButton.setAttribute('aria-expanded', 'false');
        animButton.setAttribute('aria-label', t('emoteMaker.stageAnimation'));
        const animButtonText = document.createElement('span');
        const animCaret = document.createElement('span');
        animCaret.className = 'st-anim-caret';
        animCaret.textContent = '\u203a';   // \u7528 aria-expanded \u51b3\u5b9a\u671d\u5411\uff0c\u4e0d\u5728 JS \u91cc\u6362\u5b57\u7b26
        animButton.append(animButtonText, animCaret);
        animButton.setAttribute('aria-controls', 'st-drawer');
        const stateFlag = document.createElement('span');
        stateFlag.className = 'st-band-state';
        const spanRead = document.createElement('span');
        spanRead.className = 'st-span';
        const scaleRead = document.createElement('span');
        const angleRead = document.createElement('span');
        scaleRead.className = 'st-readout';
        angleRead.className = 'st-readout';
        const bandRight = document.createElement('span');
        bandRight.className = 'st-band-right';
        const fullBtn = document.createElement('button');
        fullBtn.type = 'button';
        fullBtn.className = 'st-link st-btn-whole';
        fullBtn.textContent = t('emoteMaker.stagePlayWhole');
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'st-link st-btn-reset';
        resetBtn.textContent = t('emoteMaker.stageResetPose');
        bandRight.append(fullBtn, resetBtn);
        band.append(animButton, stateFlag, spanRead, scaleRead, angleRead, bandRight);
        const stripCanvas = document.createElement('canvas');
        stripCanvas.className = 'st-strip-canvas';
        stripCanvas.width = stripW;
        stripCanvas.height = STRIP_HEIGHT;
        stripCanvas.setAttribute('aria-label', t('emoteMaker.stageStripAria'));
        strip.append(band, stripCanvas);

        // 已绑图片行：只有选中格绑了用户图片才出现。轮播的删减入口就在这——
        // 追加靠拖拽/点击，摘除得有个地方放 ×。
        const boundRow = document.createElement('div');
        boundRow.className = 'st-bound';
        boundRow.hidden = true;
        const boundTitle = document.createElement('span');
        boundTitle.className = 'st-bound-title';
        const boundChips = document.createElement('span');
        boundChips.className = 'st-bound-chips';
        const boundClear = document.createElement('button');
        boundClear.type = 'button';
        boundClear.className = 'st-link st-bound-clear';
        boundRow.append(boundTitle, boundChips, boundClear);

        const panel = document.createElement('section');
        panel.className = 'st-panel';
        panel.append(head, body, strip, boundRow);
        overlay.appendChild(panel);
        (options.host || document.body).appendChild(overlay);

        // ── graph access ─────────────────────────────────────────────────────
        function outputNodes() {
            return graph.nodes.filter((node) => model.typeOf(node)?.category === 'output');
        }

        function nodeById(id) {
            return graph.nodes.find((node) => node.id === id) || null;
        }

        function nodeForState(stateKey) {
            return outputNodes().find((node) => node.params.stateKey === stateKey) || null;
        }

        function rootNode() {
            return graph.nodes.find((node) => node.type === 'source.default') || null;
        }

        function sceneNodeFor(node) {
            if (!node) return null;
            // 走完整条上游：中间可能有外观节点，只看直接上游会把
            // "已经接了动画"读成"跟随默认外观"，选择器就会显示成错的。
            const source = model.findSource(graph, node.id, 'source');
            return source && source.type === 'source.scene' ? source : null;
        }

        function selectedNode() {
            return nodeById(selected) || null;
        }

        function ensureNodeForState(stateKey) {
            const existing = nodeForState(stateKey);
            if (existing) return existing;
            const created = model.makeNode('output.state', 60, 60, { stateKey, seconds: 0 });
            graph = { ...graph, nodes: graph.nodes.concat([created]) };
            // 没有上游来源的输出＝"跟随默认外观"，补一根线让求值链保持完整
            const root = rootNode();
            if (root) graph = model.addLink(graph, root.id, model.LOOK, created.id, model.LOOK);
            return created;
        }

        function updateParams(patch) {
            const node = selectedNode();
            if (!node) return null;
            node.params = model.normalizeParams(node.type, { ...node.params, ...patch });
            return node;
        }

        function snapshotGraph() {
            return model.normalizeGraph(graph);
        }

        function editGraph(edit) {
            const before = snapshotGraph();
            edit();
            onCommit(before, snapshotGraph());
        }

        // ── frames and ranges ────────────────────────────────────────────────
        async function framesOf(sceneId, quality) {
            const tier = quality || 'full';
            const key = `${sceneId}|${tier}`;
            if (framesBySource.has(key)) return framesBySource.get(key);
            const revision = framesRevision;
            const imageRevision = imageFramesRevision;
            const result = await framesFor(sceneId, tier);
            if (revision !== framesRevision
                    || (String(sceneId).startsWith('images:') && imageRevision !== imageFramesRevision)) return [];
            const list = Array.isArray(result) ? result : [];
            // 空结果不进缓存：那条动画可能只是还没采完，缓存住就永远空着
            if (list.length) framesBySource.set(key, list);
            return list;
        }

        function activeSource(node) {
            return previewScene || imageSourceKey(node) || committedScene(node);
        }

        /** 图片源的 sourceKey（image:<id>），供 framesOf 走图片绘制路径。 */
        function imageSourceKey(node) {
            if (!node) return '';
            const source = model.findSource(graph, node.id, 'source');
            if (!source || source.type !== 'source.image') return '';
            const ids = boundImageIds(source);
            return ids.length ? 'images:' + ids.join('|') : '';
        }

        /** 这一格上游图片源里的 id 列表（旧单值图也归一成一项）。 */
        function boundImageIds(source) {
            const params = source && source.params || {};
            const list = Array.isArray(params.imageIds) && params.imageIds.length
                ? params.imageIds
                : (String(params.imageId || '') ? [params.imageId] : []);
            return list.map((id) => String(id)).filter(Boolean);
        }

        /** 这一格上游绑的用户图片条目（轮播顺序）；查不到清单项的 id 跳过。 */
        function boundImagesFor(node) {
            const source = node ? model.findSource(graph, node.id, 'source') : null;
            if (!source || source.type !== 'source.image') return [];
            const known = userImages();
            return boundImageIds(source)
                .map((id) => known.find((entry) => entry.id === id) || null)
                .filter(Boolean);
        }

        function activeQuality() {
            return previewScene ? previewQuality : 'full';
        }

        function clearPreview() {
            if (upgradeTimer) window.clearTimeout(upgradeTimer);
            upgradeTimer = 0;
            if (!previewScene) return;
            previewScene = null;
            previewQuality = 'poster';
        }

        /** 这一格的帧源：接了动画就是那条动画，没接就是"默认外观"（每格不同）。*/
        function committedScene(node) {
            const scene = sceneNodeFor(node);
            const id = scene ? String(scene.params.sceneId || '') : '';
            return id || (ROOT_PREFIX + (node ? node.params.stateKey : ''));
        }

        function activeFrames(node) {
            return framesBySource.get(`${activeSource(node)}|${activeQuality()}`) || [];
        }

        /**
         * 这条动画实际的采样率：预览用的 fps 和导出用的 fps 不一样，长动画还会被
         * 帧数上限压稀。用帧自带的 delayMs 反算，秒↔帧的换算才和屏幕上看到的一致。
         */
        function rateOf(node) {
            const delay = Number((activeFrames(node) || [])[0]?.delayMs);
            if (Number.isFinite(delay) && delay > 0) return 1000 / delay;
            return fpsOf();
        }

        /**
         * 起止范围落在胶片带的哪几帧、哪几个像素上。
         * seconds 0 = 演到结尾，与导出的采集规则一致，
         * 所以手柄显示的就是会被烘进包里的东西。
         */
        function rangeOf(frames, node) {
            const rate = rateOf(node);
            const previewing = Boolean(previewScene);
            const total = frames.length;
            const params = node?.params || {};
            // 平铺包的说话位用一帧和待机图交替，分层包按选中的区间播放说话动作。
            const still = !previewing && stateUsesStillFrame(params.stateKey);
            const fullSeconds = total / rate;
            const startAt = previewing ? 0 : clamp(params.startAt, 0, Math.max(0, fullSeconds));
            const wanted = still ? 1 / rate
                : (previewing ? 0 : clamp(params.seconds, 0, Math.max(0, fullSeconds)));
            const firstFrame = total ? Math.min(total - 1, Math.round(startAt * rate)) : 0;
            const endFrame = wanted > 0 ? Math.round((startAt + wanted) * rate) - 1 : total - 1;
            const lastFrame = total ? Math.max(firstFrame, Math.min(total - 1, endFrame)) : 0;
            const cell = total ? stripW / total : 0;
            return {
                total,
                rate,
                still,
                previewing,
                firstFrame,
                lastFrame,
                slice: frames.slice(firstFrame, lastFrame + 1),
                startPx: firstFrame * cell,
                endPx: (lastFrame + 1) * cell,
                cell,
                startSeconds: firstFrame / rate,
                endSeconds: (lastFrame + 1) / rate,
            };
        }

        let scratch = null;
        const imageDataByFrame = new WeakMap();

        function frameImageData(frame) {
            // ImageData 包的是那个 typed array 本身（不复制），所以缓存它
            // = 每帧只付一次包装代价，而不是每次重绘都重新拷一整张像素。
            let image = imageDataByFrame.get(frame);
            if (image) return image;
            const width = Math.max(1, frame.width | 0);
            const height = Math.max(1, frame.height | 0);
            const data = frame.data instanceof Uint8ClampedArray
                ? frame.data : new Uint8ClampedArray(frame.data);
            try {
                image = new ImageData(data, width, height);
            } catch (error) {
                return null;
            }
            imageDataByFrame.set(frame, image);
            return image;
        }

        function putFrame(ctx, frame, x, y, w, h) {
            if (!ctx || !frame || !frame.data) return;
            const image = frameImageData(frame);
            if (!image) return;
            if (!scratch) scratch = document.createElement('canvas');
            if (scratch.width !== image.width || scratch.height !== image.height) {
                scratch.width = image.width;
                scratch.height = image.height;
            }
            const octx = scratch.getContext('2d');
            if (!octx) return;
            octx.putImageData(image, 0, 0);
            ctx.drawImage(scratch, x, y, w, h);
        }

        const FRAME_BAND = 56;      // 画面区高度
        const RULER_TOP = 64;       // 秒尺基线

        const filmCache = { key: '', canvas: null };

        /**
         * 整条片子（所有帧 + 分隔线 + 秒尺）合成到一张离屏画布上。
         * 之前每个播放 tick 都把几十帧逐帧 putImage+drawImage 一遍，
         * 而那几十帧里根本没有一样东西在动——动的只有窗口和指针。
         */
        function filmBitmap(frames, range) {
            const source = activeSource(selectedNode());
            const key = `${source}|${frames.length}|${range.rate}|${stripW}`;
            if (filmCache.canvas && filmCache.key === key) return filmCache.canvas;
            const ratio = canvasRatio || dpr();
            const canvas = document.createElement('canvas');
            canvas.width = Math.round(stripW * ratio);
            canvas.height = Math.round(STRIP_HEIGHT * ratio);
            const ctx = canvas.getContext('2d');
            if (!ctx) return canvas;
            ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            ctx.clearRect(0, 0, stripW, STRIP_HEIGHT);
            ctx.globalAlpha = 1;
            frames.forEach((frame, index) => {
                putFrame(ctx, frame, index * range.cell, 0, range.cell - 1, FRAME_BAND);
            });
            // 帧分隔细线：只在画面区，密度本身就是"多少帧"的信息
            ctx.strokeStyle = 'rgba(199, 220, 235, 0.55)';
            ctx.lineWidth = 1;
            for (let x = range.cell; x < stripW; x += range.cell) {
                ctx.beginPath();
                ctx.moveTo(Math.round(x) + 0.5, 0);
                ctx.lineTo(Math.round(x) + 0.5, FRAME_BAND);
                ctx.stroke();
            }
            drawRuler(ctx, range);
            filmCache.key = key;
            filmCache.canvas = canvas;
            return canvas;
        }

        function drawStrip() {
            const node = selectedNode();
            const frames = activeFrames(node);
            const range = rangeOf(frames, node);
            const ctx = context2d(stripCanvas);
            if (!ctx) return;
            ctx.clearRect(0, 0, stripW, STRIP_HEIGHT);

            if (!range.total) {
                spanRead.textContent = '';
                scaleRead.textContent = '';
                angleRead.textContent = '';
                fullBtn.hidden = true;
                drawEmptyStrip(ctx);
                return;
            }
            // 已经是整段时不给"整段播放"：一个什么都不做的链接比灰掉的按钮更糟
            const wholeRange = range.firstFrame === 0 && range.lastFrame === range.total - 1;
            fullBtn.hidden = range.still || wholeRange;
            const film = filmBitmap(frames, range);
            // 范围外淡掉而不是涂黑：在光台上"缺席"应该退到背景。
            // 淡的部分和清晰的部分是同一张合成图，只是被裁开贴了两次。
            ctx.save();
            ctx.globalAlpha = 0.16;
            ctx.beginPath();
            ctx.rect(0, 0, range.startPx, STRIP_HEIGHT);
            ctx.rect(range.endPx, 0, Math.max(0, stripW - range.endPx), STRIP_HEIGHT);
            ctx.clip();
            ctx.drawImage(film, 0, 0, stripW, STRIP_HEIGHT);
            ctx.restore();
            // 清晰的那一遍也必须裁在窗口内：整条重贴会把刚淡掉的又涂回来
            ctx.save();
            ctx.beginPath();
            ctx.rect(range.startPx, 0, Math.max(0, range.endPx - range.startPx), STRIP_HEIGHT);
            ctx.clip();
            ctx.drawImage(film, 0, 0, stripW, STRIP_HEIGHT);
            ctx.restore();

            drawRangeBracket(ctx, range);
            // 走带指针：细线＋顶端小三角，只标在会播的区间里
            const at = range.startPx + (playhead % range.slice.length) * range.cell;
            ctx.fillStyle = '#0F6FD1';
            ctx.fillRect(at, 0, 1, FRAME_BAND);
            ctx.beginPath();
            ctx.moveTo(at - 3.5, FRAME_BAND + 1);
            ctx.lineTo(at + 3.5, FRAME_BAND + 1);
            ctx.lineTo(at, FRAME_BAND - 3.5);
            ctx.closePath();
            ctx.fill();
            spanRead.textContent = `${secondsLabel(range.startSeconds)} – ${secondsLabel(range.endSeconds)}`;
        }

        function drawEmptyStrip(ctx) {
            // 空态要说"下一步做什么"，而且要说在帧本来该出现的位置上
            ctx.fillStyle = '#63809C';
            ctx.font = '10.5px ui-sans-serif, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(t('emoteMaker.stageNoFrames'), stripW / 2, 28);
            ctx.textAlign = 'start';
            ctx.textBaseline = 'alphabetic';
        }

        function drawRuler(ctx, range) {
            const totalSeconds = range.total / range.rate;
            const step = totalSeconds > 16 ? Math.ceil(totalSeconds / 8)
                : totalSeconds > 8 ? 2 : totalSeconds > 4 ? 1 : totalSeconds > 1.6 ? 0.5 : 0.25;
            ctx.strokeStyle = '#C7DCEB';
            ctx.fillStyle = '#63809C';
            ctx.lineWidth = 1;
            ctx.font = '9px ui-sans-serif, system-ui, sans-serif';
            ctx.textBaseline = 'top';
            for (let second = 0; second <= totalSeconds + 1e-6; second += step) {
                const x = Math.round(second * range.rate * range.cell);
                const major = Math.abs(second - Math.round(second)) < 1e-6;
                ctx.beginPath();
                ctx.moveTo(x + 0.5, RULER_TOP);
                ctx.lineTo(x + 0.5, RULER_TOP + (major ? 8 : 4));
                ctx.stroke();
                if (major || step < 1) {
                    // 截到真正走得到的步长，不做四舍五入：0.25 标成 0.3 是把尺读错
                    const digits = step >= 1 ? 0 : step >= 0.5 ? 1 : 2;
                    ctx.fillText(`${second.toFixed(digits)}s`, x + 3, RULER_TOP + 1);
                }
            }
        }

        function drawRangeBracket(ctx, range) {
            const y = STRIP_HEIGHT - 9;
            ctx.strokeStyle = '#0F6FD1';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(range.startPx, y - 4);
            ctx.lineTo(range.startPx, y);
            ctx.lineTo(range.endPx, y);
            ctx.lineTo(range.endPx, y - 4);
            ctx.stroke();
            // 两端圆把手：圆形才读得出"这是可以抓住拖的东西"，方条读不出
            [{ x: range.startPx, edge: true }, { x: range.endPx, edge: false }].forEach((handle) => {
                const x = clamp(handle.x, 5, stripW - 5);
                ctx.beginPath();
                ctx.arc(x, y, 5, 0, Math.PI * 2);
                ctx.fillStyle = '#0F6FD1';
                ctx.fill();
                ctx.beginPath();
                ctx.arc(x, y, 1.8, 0, Math.PI * 2);
                ctx.fillStyle = '#FFFFFF';
                ctx.fill();
            });
        }

        function updatePoseReadouts(node) {
            if (!node) { scaleRead.textContent = ''; angleRead.textContent = ''; return; }
            paintPoseReadouts(
                clamp((Number(node.params.scalePercent) || 100) / 100, 0.1, 5) * 100,
                clamp(Number(node.params.degrees) || 0, -180, 180));
        }

        function paintPoseReadouts(scalePercent, degrees) {
            scaleRead.textContent = `${t('emoteMaker.stageScale')} ${Math.round(scalePercent)}%`;
            angleRead.textContent = `${t('emoteMaker.stageAngle')} ${Math.round(degrees)}°`;
            scaleRead.classList.toggle('is-neutral', Math.round(scalePercent) === 100);
            angleRead.classList.toggle('is-neutral', Math.round(degrees) === 0);
        }

        function drawPivot(ctx) {
            // 大小和角度都绕台面正中缩放/旋转。不给标记的话，用户拖出来的
            // 到底是不是"以脚为轴"这种问题只能靠猜。
            const cx = stageSize / 2;
            const cy = stageSize / 2;
            ctx.strokeStyle = 'rgba(199, 220, 235, 0.9)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(cx - 9, cy + 0.5); ctx.lineTo(cx + 9, cy + 0.5);
            ctx.moveTo(cx + 0.5, cy - 9); ctx.lineTo(cx + 0.5, cy + 9);
            ctx.stroke();
        }

        function drawTransformBox(ctx, frame, fit) {
            const w = frame.width * fit;
            const h = frame.height * fit;
            ctx.strokeStyle = 'rgba(15, 111, 209, 0.55)';
            ctx.setLineDash([3, 3]);
            ctx.lineWidth = 1;
            ctx.strokeRect(stageSize / 2 - w / 2, stageSize / 2 - h / 2, w, h);
            ctx.setLineDash([]);
        }

        function nodeHasTweaks(node) {
            const p = node.params;
            return (Number(p.startAt) || 0) > 0 || (Number(p.seconds) || 0) > 0
                || !nodePoseIsNeutral(node);
        }

        function nodePoseIsNeutral(node) {
            return (Number(node.params.scalePercent) || 100) === 100
                && (Number(node.params.degrees) || 0) === 0;
        }

        /**
         * 抽屉展开期间台面保持"展开那一刻的那一帧"：背景是氛围，不是第二个
         * 预览窗，跟着列表上的鼠标变会让整块面板看起来在抖。
         * force 只给尺寸变化后用 —— 改 canvas.width 会清空画布，得补一帧。
         */
        function drawStage(force) {
            const node = selectedNode();
            const ctx = context2d(stage);
            if (!ctx) return;
            if (drawer.classList.contains('is-open') && !force) {
                updatePoseReadouts(node);   // 画面冻住，数字仍要诚实
                return;
            }
            ctx.clearRect(0, 0, stageSize, stageSize);
            if (node) stateFlag.textContent = t(stateLabel(node.params.stateKey));
            const slice = node ? sliceByNode.get(node.id) : null;
            if (!slice?.length) {
                scaleRead.textContent = '';
                angleRead.textContent = '';
                return;
            }
            const frame = slice[playhead % slice.length];
            const scale = clamp((Number(node.params.scalePercent) || 100) / 100, 0.1, 5);
            const degrees = clamp(Number(node.params.degrees) || 0, -180, 180);
            const fit = Math.min(stageSize / frame.width, stageSize / frame.height) * 0.88;
            drawPivot(ctx);
            if (stageDrag) drawTransformBox(ctx, frame, fit);
            // 导出可见区提示：淡蓝虚线框 + 网格线标出导出方的内容边界。
            // 框是"导出的那一个方"，固定不转不缩——你在台上调的大小/角度
            // 套在内容上（分层格式由运行时应用），方本身不动。
            const px = 1 / dpr();                   // 线宽恒为 1 设备像素
            const halfW = (frame.width * fit) / 2;
            const halfH = (frame.height * fit) / 2;
            ctx.strokeStyle = 'rgba(79, 140, 255, 0.45)';
            ctx.lineWidth = px;
            for (let i = 1; i < 6; i += 1) {        // 网格线填充（不含边框）
                const gx = stageSize / 2 - halfW + (frame.width * fit * i) / 6;
                const gy = stageSize / 2 - halfH + (frame.height * fit * i) / 6;
                ctx.beginPath();
                ctx.moveTo(gx, stageSize / 2 - halfH); ctx.lineTo(gx, stageSize / 2 + halfH);
                ctx.moveTo(stageSize / 2 - halfW, gy); ctx.lineTo(stageSize / 2 + halfW, gy);
                ctx.stroke();
            }
            ctx.setLineDash([6 * px, 5 * px]);
            ctx.strokeRect(stageSize / 2 - halfW, stageSize / 2 - halfH,
                frame.width * fit, frame.height * fit);
            ctx.setLineDash([]);
            ctx.save();
            ctx.translate(stageSize / 2, stageSize / 2);
            ctx.rotate(degrees * Math.PI / 180);
            ctx.scale(fit * scale, fit * scale);
            putFrame(ctx, frame, -frame.width / 2, -frame.height / 2, frame.width, frame.height);
            ctx.restore();
            paintPoseReadouts(scale * 100, degrees);
        }

        // 八行的结构只建一次。以前每次 paint 都 replaceChildren 重建：
        // 拖一下起止手柄就是几十次"拆了再建"，而其中真正变的只有几个字。
        const tileRefs = new Map();   // stateKey -> { row, thumb, sub, status }
        let labelsCache = null;

        function animationLabels() {
            if (!labelsCache) {
                labelsCache = {};
                animationsFor().forEach((item) => { labelsCache[item.value] = item.label; });
            }
            return labelsCache;
        }

        function paintTileCanvas(host, frame) {
            const box = host.querySelector('canvas') || (() => {
                const created = document.createElement('canvas');
                created.width = 44;
                created.height = 44;
                host.replaceChildren(created);
                return created;
            })();
            const ctx = box.getContext('2d');
            // putFrame 是 drawImage 合成、不清底：画布又是复用的，换绑动作/图片后
            // 旧帧会整个压在新帧下面，透明背景的源叠成多重曝光。重画前必须清掉。
            ctx.clearRect(0, 0, box.width, box.height);
            const k = Math.min(box.width / frame.width, box.height / frame.height);
            putFrame(ctx, frame,
                (box.width - frame.width * k) / 2, (box.height - frame.height * k) / 2,
                frame.width * k, frame.height * k);
        }

        function buildTiles() {
            shelf.replaceChildren();
            tileRefs.clear();
            TILE_STATES.forEach((stateKey) => {
                const row = document.createElement('button');
                row.type = 'button';
                row.className = 'st-tile';
                row.dataset.state = stateKey;
                row.setAttribute('role', 'option');
                const thumb = document.createElement('span');
                thumb.className = 'st-tile-thumb';
                const text = document.createElement('span');
                text.className = 'st-tile-text';
                const label = document.createElement('span');
                label.className = 'st-tile-label';
                label.textContent = t(stateLabel(stateKey));
                const sub = document.createElement('span');
                sub.className = 'st-tile-sub';
                text.append(label, sub);
                const status = document.createElement('span');
                status.className = 'st-tile-status';
                row.append(thumb, text, status);
                row.addEventListener('click', () => {
                    // 每次点都重新解析：结构复用后，闭包里不能攥着旧的 node 引用
                    const node = nodeForState(stateKey) || ensureNodeForState(stateKey);
                    selected = node.id;
                    // 换格子＝换帧源：只重画不去取帧的话，舞台上还在播上一格的动画
                    loadSelected();
                });
                shelf.appendChild(row);
                tileRefs.set(stateKey, { row, thumb, sub, status });
            });
        }

        function drawTiles() {
            if (!tileRefs.size) buildTiles();
            const labels = animationLabels();
            TILE_STATES.forEach((stateKey) => {
                const refs = tileRefs.get(stateKey);
                if (!refs) return;
                const node = nodeForState(stateKey);
                const slice = node ? sliceByNode.get(node.id) : null;
                const chosen = Boolean(node) && node.id === selected;
                refs.row.classList.toggle('is-selected', chosen);
                refs.row.setAttribute('aria-selected', String(chosen));

                // 先前只给"正在编辑的那一格"取帧，其余七行一直是空白，要点一下才亮
                const scene = node ? sceneNodeFor(node) : null;
                const bound = node ? boundImagesFor(node) : null;
                const sourceScene = node ? committedScene(node) : '';
                if (slice?.length) paintTileCanvas(refs.thumb, slice[0]);
                else if (refs.thumb.firstChild === null && node && bound?.length) {
                    // 绑了用户图片的格子缩略图走图片源；committedScene 给的是
                    // root: 前缀，照旧路走会把这格永远哑成空白。多图轮播也只
                    // 摆第一张：格子里要的是"这格是图"，不是预告片。
                    requestTileThumb(refs.thumb, `image:${bound[0].id}`);
                } else if (refs.thumb.firstChild === null && node && sourceScene
                    && sourceScene.indexOf(ROOT_PREFIX) !== 0) {
                    // 格子是"这个状态长什么样"：起始手柄指到哪，缩略图就采哪一帧，
                    // 同一条动画不同起点的两格各采各的（缩略图缓存按起点拆开）
                    const startAt = Math.max(0, Number(node.params.startAt) || 0);
                    requestTileThumb(refs.thumb, sourceScene, startAt > 0 ? { startAt } : undefined);
                }

                const sub = bound?.length
                    ? imageListLabel(bound)
                    : scene ? (labels[scene.params.sceneId] || '—')
                    : t('emoteMaker.stageFollowTag');
                if (refs.sub.textContent !== sub) refs.sub.textContent = sub;

                let status = '';
                let tweaked = false;
                if (node && stateUsesStillFrame(stateKey)) {
                    status = t('emoteMaker.stageStillTag');
                } else if (chosen) {
                    // 只有正在编辑的那一格报帧数——它是舞台上正在演的那个范围
                    status = slice?.length ? `${slice.length}f` : '—';
                } else if (node && nodeHasTweaks(node)) {
                    // 一个点就够：告诉我这格被改过，不用八格全铺满文字
                    status = '●';
                    tweaked = true;
                }
                if (refs.status.textContent !== status) refs.status.textContent = status;
                refs.status.classList.toggle('is-tweaked', tweaked);
                refs.status.classList.toggle('is-previewing', Boolean(previewScene) && chosen);
            });
        }

        function publishSlice() {
            const node = selectedNode();
            if (!node) return;
            const slice = rangeOf(activeFrames(node), node).slice;
            sliceByNode.set(node.id, slice);
            if (playhead >= slice.length) playhead = 0;
        }

        function paint(forceStage) {
            publishSlice();
            drawTiles();
            renderBoundChips();
            drawStrip();
            drawStage(forceStage);
            // 台面定格后这里就是"当前帧"的显示口，漏画就会一直停在旧帧
            if (!forceStage) drawLibraryPreview();

            const flat = formatIsFlat();
            const node = selectedNode();
            stageNotice.hidden = !flat;
            stageGestureHint.hidden = flat || !node || !(activeFrames(node).length);
            // 一个只在"有得撤销"时出现的链接，比一个永远灰着的按钮好读
            resetBtn.hidden = flat || !node || nodePoseIsNeutral(node);
            scaleRead.hidden = !node;
            angleRead.hidden = !node;
            onChange(model.normalizeGraph(graph));
        }

        function range0Preview() {
            return Boolean(previewScene);
        }

        async function loadSelected() {
            const node = selectedNode();
            if (!node) return;
            const request = ++selectionRequest;
            const source = activeSource(node);
            await framesOf(source, activeQuality());
            if (disposed || request !== selectionRequest || selectedNode()?.id !== node.id
                || activeSource(selectedNode()) !== source) return;
            paint();
            syncAnimButton();
        }

        // ── film strip: drag either end ──────────────────────────────────────
        function frameIndexAt(clientX, total) {
            const box = stripCanvas.getBoundingClientRect();
            if (!box.width || !total) return 0;
            return clamp(Math.round((clientX - box.left) / box.width * total), 0, total);
        }

        function applyStripDrag(clientX) {
            const node = selectedNode();
            const frames = activeFrames(node);
            if (!node || !frames.length || range0Preview()) return;
            const range = rangeOf(frames, node);
            const index = frameIndexAt(clientX, frames.length);
            if (stripDrag === 'start') {
                // 定格那一格的终点本来就贴着起点，只有整条胶片带才是它的可选范围
                const first = clamp(index, 0, range.still ? frames.length - 1 : range.lastFrame);
                if (first === range.firstFrame) return;
                const startAt = first / range.rate;
                // 裁短过的片段按时长存储。移动起点时重算时长，让终点留在原处。
                const seconds = Number(node.params.seconds) || 0;
                updateParams({
                    startAt,
                    seconds: range.still || seconds <= 0 ? seconds : (range.lastFrame - first + 1) / range.rate,
                });
            } else {
                const last = clamp(index - 1, range.firstFrame, frames.length - 1);
                const span = Math.max(MIN_SPAN_SECONDS, (last - range.firstFrame + 1) / range.rate);
                if (Math.abs(span - (Number(node.params.seconds) || 0)) < 0.001) return;
                updateParams({ seconds: span });
            }
            playhead = 0;
            paint();
        }

        stripCanvas.addEventListener('pointerdown', (event) => {
            if (event.button !== 0 || stripDrag || stageDrag) return;
            const frames = activeFrames(selectedNode());
            if (!frames.length || previewScene) return;
            const range = rangeOf(frames, selectedNode());
            const box = stripCanvas.getBoundingClientRect();
            const x = (event.clientX - box.left) / box.width * stripW;
            // 单帧那一格只有起点有意义：终点手柄就挨在旁边，抓住哪个都该挪起点
            stripDrag = range.still || Math.abs(x - range.startPx) <= HANDLE_HIT_PX ? 'start' : 'end';
            stripBefore = snapshotGraph();
            stripCanvas.setPointerCapture?.(event.pointerId);
            applyStripDrag(event.clientX);
        });
        stripCanvas.addEventListener('pointermove', (event) => {
            if (stripDrag) applyStripDrag(event.clientX);
        });
        const endStripDrag = () => {
            if (!stripDrag) return;
            stripDrag = null;
            onCommit(stripBefore, snapshotGraph());
            stripBefore = null;
        };
        stripCanvas.addEventListener('pointerup', endStripDrag);
        stripCanvas.addEventListener('pointercancel', endStripDrag);
        stripCanvas.addEventListener('lostpointercapture', endStripDrag);

        fullBtn.addEventListener('click', () => {
            editGraph(() => updateParams({ startAt: 0, seconds: 0 }));
            playhead = 0;
            paint();
        });

        // ── 台面手势 ────────────────────────────────────────────────────────
        // 横拖=角度、竖拖=大小 试过了：一次动两个量，手不知道自己在改什么。
        // 改成"抓住转"（指针绕中心的角位移就是角度）＋"滚轮调大小"，
        // 两件事各有一只手，且和"把纸转一下"的直觉一致。
        function stageCenter() {
            const box = stage.getBoundingClientRect();
            return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
        }

        function pointerAngle(event, center) {
            return Math.atan2(event.clientY - center.y, event.clientX - center.x) * 180 / Math.PI;
        }

        function snapAngle(degrees) {
            let best = degrees;
            let bestGap = SNAP_WINDOW + 1;
            SNAP_DEGREES.forEach((anchor) => {
                const wrapped = ((degrees - anchor + 540) % 360) - 180;
                if (Math.abs(wrapped) < bestGap) {
                    bestGap = Math.abs(wrapped);
                    best = anchor;
                }
            });
            return bestGap <= SNAP_WINDOW ? best : degrees;
        }

        function setPose(degrees, scalePercent) {
            const node = selectedNode();
            if (!node) return;
            updateParams({
                degrees: Math.round(clamp(degrees, -180, 180)),
                scalePercent: Math.round(clamp(scalePercent, 10, 500)),
            });
            paint();
        }

        stage.addEventListener('pointerdown', (event) => {
            const node = selectedNode();
            if (!node || formatIsFlat() || event.button !== 0 || stageDrag || stripDrag) return;
            const center = stageCenter();
            stageDrag = {
                angle: pointerAngle(event, center),
                degrees: Number(node.params.degrees) || 0,
                before: snapshotGraph(),
            };
            stage.setPointerCapture?.(event.pointerId);
            stage.classList.add('is-transforming');
            event.preventDefault();
        });
        stage.addEventListener('pointermove', (event) => {
            if (!stageDrag) return;
            const now = pointerAngle(event, stageCenter());
            let degrees = stageDrag.degrees + (now - stageDrag.angle);
            // 越过 ±180 要绕回去，否则转到头会突然反向
            degrees = ((degrees + 180 + 360 * 40) % 360) - 180;
            setPose(snapAngle(degrees), Number(selectedNode()?.params.scalePercent) || 100);
        });
        const endStageDrag = () => {
            if (!stageDrag) return;
            const { before } = stageDrag;
            stageDrag = null;
            stage.classList.remove('is-transforming');
            onCommit(before, snapshotGraph());
            paint();
        };
        stage.addEventListener('pointerup', endStageDrag);
        stage.addEventListener('pointercancel', endStageDrag);
        stage.addEventListener('lostpointercapture', endStageDrag);

        stage.addEventListener('wheel', (event) => {
            if (formatIsFlat() || stageDrag || stripDrag) return;
            const node = selectedNode();
            if (!node) return;
            event.preventDefault();
            const step = event.shiftKey ? 25 : 5;
            const direction = event.deltaY < 0 ? 1 : -1;
            editGraph(() => setPose(Number(node.params.degrees) || 0,
                (Number(node.params.scalePercent) || 100) + direction * step));
        }, { passive: false });

        // 键盘也得能摆正：这是唯一的精确输入口
        stage.setAttribute('tabindex', '0');
        stage.addEventListener('keydown', (event) => {
            const node = selectedNode();
            if (!node || formatIsFlat() || stageDrag || stripDrag) return;
            const degrees = Number(node.params.degrees) || 0;
            const scale = Number(node.params.scalePercent) || 100;
            const fine = event.shiftKey ? 1 : 5;
            const moves = {
                ArrowLeft: () => setPose(degrees - fine, scale),
                ArrowRight: () => setPose(degrees + fine, scale),
                ArrowUp: () => setPose(degrees, scale + fine),
                ArrowDown: () => setPose(degrees, scale - fine),
                '+': () => setPose(degrees, scale + 5),
                '=': () => setPose(degrees, scale + 5),
                '-': () => setPose(degrees, scale - 5),
                '0': () => setPose(0, 100),
            };
            const move = moves[event.key];
            if (!move) return;
            event.preventDefault();
            editGraph(move);
        });

        resetBtn.addEventListener('click', () => {
            editGraph(() => updateParams({ scalePercent: 100, degrees: 0 }));
            paint();
        });
        stage.addEventListener('dblclick', () => {
            editGraph(() => updateParams({ scalePercent: 100, degrees: 0 }));
            paint();
        });

        // ── which animation this state uses ──────────────────────────────────
        function isDefaultSource(sceneId) {
            return !sceneId || sceneId === ROOT_SCENE || String(sceneId).indexOf(ROOT_PREFIX) === 0;
        }

        /**
         * 把某一格的帧源换成某条动画（''＝跟随默认外观）。
         * 只动图上那一根线：别的状态用同一条动画时不该被牵连。
         */
        function replaceSourceFor(node, type, params) {
            const previous = graph.links.find((link) => link.to === node.id);
            const modifiers = [];
            const visited = new Set();
            let cursor = previous?.from;
            while (cursor && !visited.has(cursor)) {
                visited.add(cursor);
                const upstream = graph.nodes.find((entry) => entry.id === cursor);
                if (!upstream || model.typeOf(upstream)?.category !== 'modify') break;
                modifiers.push(upstream);
                cursor = graph.links.find((link) => link.to === cursor)?.from;
            }
            let source = rootNode();
            if (type) {
                source = model.makeNode(type, 60, 60, params);
                graph = { ...graph, nodes: graph.nodes.concat([source]) };
            }
            // A stored graph may share its modifiers as well as its source.
            // Copy that path for this output so its appearance is preserved.
            for (const modifier of modifiers.reverse()) {
                const copy = model.makeNode(modifier.type, modifier.x, modifier.y, { ...modifier.params });
                graph = { ...graph, nodes: graph.nodes.concat([copy]) };
                if (source) graph = model.addLink(graph, source.id, model.LOOK, copy.id, model.LOOK);
                source = copy;
            }
            if (source) graph = model.addLink(graph, source.id, model.LOOK, node.id, model.LOOK);
            else graph = { ...graph, links: graph.links.filter((link) => link.to !== node.id) };
            let orphan = previous?.from;
            while (orphan && !graph.links.some((link) => link.from === orphan)) {
                const unused = graph.nodes.find((entry) => entry.id === orphan);
                if (!unused || !(unused.type === 'source.scene' || unused.type === 'source.image'
                    || model.typeOf(unused)?.category === 'modify')) break;
                const parent = graph.links.find((link) => link.to === orphan)?.from;
                graph = model.removeNode(graph, orphan);
                orphan = parent;
            }
        }

        function applySceneTo(node, sceneId) {
            if (!node) return;
            replaceSourceFor(node, sceneId ? 'source.scene' : null, { sceneId });
            sliceByNode.delete(node.id);
            playhead = 0;
        }

        /**
         * 把一张用户图片**追加**进某一格的轮播列表（已有同一张就不重复）。
         * 与 applySceneTo 同一套纪律：只动图上那一根线；没有图片源就建一个新节点接上。
         */
        function applyImageTo(node, imageId) {
            if (!node || !imageId) return;
            const id = String(imageId);
            const upstream = model.findSource(graph, node.id, 'source');
            if (upstream && upstream.type === 'source.image') {
                const ids = [...boundImageIds(upstream)];
                if (ids.includes(id)) return;
                ids.push(id);
                replaceSourceFor(node, 'source.image', {
                    ...upstream.params, imageIds: ids, imageId: ids[0],
                });
                return;
            }
            replaceSourceFor(node, 'source.image', {
                imageIds: [id], imageId: id,
            });
        }

        /** 从某一格的轮播列表里摘掉一张；摘空 = 拆掉图片源回"跟随默认外观"。 */
        function removeBoundImage(node, imageId) {
            if (!node) return;
            const source = model.findSource(graph, node.id, 'source');
            if (!source || source.type !== 'source.image') return;
            const ids = boundImageIds(source).filter((id) => id !== String(imageId));
            if (ids.length) {
                replaceSourceFor(node, 'source.image', {
                    ...source.params, imageIds: ids, imageId: ids[0],
                });
                return;
            }
            replaceSourceFor(node, null, {});
        }

        // 本模块拿到的 t 只做翻译不插值（页面约定插值走 interpolate）；
        // 这里就地补一个同款，模板串的 {{x}} 有值就换。
        function fillParams(text, values) {
            return String(text || '').replace(/\{\{\s*(\w+)\s*\}\}/g,
                (_, key) => String(values?.[key] ?? ''));
        }

        /** 绑图格子的副标题/按钮文案：一张直呼其名，多张报"第一张 等 N 张"。 */
        function imageListLabel(entries) {
            if (!entries || !entries.length) return '—';
            if (entries.length === 1) return entries[0].label || '—';
            return fillParams(t('emoteMaker.stageImageMulti'),
                { first: entries[0].label || '', count: entries.length });
        }

        // 已绑图片行的重绘：paint 每帧都跑，这里用签名挡住重复重建，
        // 只有选中格或名单真的变了才动 DOM。
        let boundChipsKey = null;
        function renderBoundChips() {
            const node = selectedNode();
            const entries = node ? boundImagesFor(node) : [];
            const source = node ? model.findSource(graph, node.id, 'source') : null;
            const ids = source?.type === 'source.image' ? boundImageIds(source) : [];
            const key = `${node ? node.id : ''}|${ids.join(',')}|${entries.map((entry) => entry.id).join(',')}`;
            if (key === boundChipsKey) return;
            boundChipsKey = key;
            if (!ids.length) {
                boundRow.hidden = true;
                boundChips.replaceChildren();
                return;
            }
            boundRow.hidden = false;
            boundTitle.textContent = t('emoteMaker.stageBoundTitle');
            boundClear.textContent = t('emoteMaker.stageBoundClear');
            const rebuild = () => {
                const current = selectedNode();
                if (!current) return;
                sliceByNode.delete(current.id);
                playhead = 0;
                boundChipsKey = null;   // 名单变了：下一轮 paint 强制重建
                loadSelected();          // 重取帧并 paint（onChange 随 paint 发出）
            };
            const chips = entries.map((entry) => {
                const chip = document.createElement('span');
                chip.className = 'st-bound-chip';
                const thumb = document.createElement('img');
                thumb.alt = '';
                if (entry.thumb) thumb.src = entry.thumb;
                const label = document.createElement('span');
                label.className = 'st-bound-chip-label';
                label.textContent = entry.label;
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'st-bound-chip-x';
                remove.textContent = '×';
                remove.setAttribute('aria-label', entry.label);
                remove.addEventListener('click', () => {
                    editGraph(() => removeBoundImage(selectedNode(), entry.id));
                    rebuild();
                });
                chip.append(thumb, label, remove);
                return chip;
            });
            boundChips.replaceChildren(...chips);
            boundClear.onclick = () => {
                editGraph(() => applySceneTo(selectedNode(), ''));
                rebuild();
            };
        }

        function animationLabel(sceneId) {
            if (isDefaultSource(sceneId)) return t('emoteMaker.stageUseDefaults');
            const key = String(sceneId || '');
            if (key.indexOf('images:') === 0) {
                const ids = key.slice('images:'.length).split('|').filter(Boolean);
                const known = userImages();
                return imageListLabel(
                    ids.map((id) => known.find((entry) => entry.id === id)).filter(Boolean));
            }
            if (key.indexOf('image:') === 0) {
                const entry = userImages().find(
                    (item) => item.id === key.slice('image:'.length));
                return entry ? (entry.label || '—') : '—';
            }
            const found = animationsFor().find((item) => String(item.value) === String(sceneId));
            return found ? found.label : String(sceneId);
        }

        function syncAnimButton() {
            const scene = activeSource(selectedNode());
            const label = animationLabel(scene);
            animButtonText.textContent = label;
            previewName.textContent = label;   // 检视窗没悬停时也要有名字，不能空着
            animButton.dataset.scene = scene;
            animButton.classList.toggle('is-previewing', Boolean(previewScene));
        }

        function requestTileThumb(host, scene, opts) {
            const startAt = Math.max(0, Number(opts && opts.startAt) || 0);
            const cacheKey = startAt > 0 ? `${scene}@${startAt}` : scene;
            host.dataset.thumbRequest = cacheKey;
            const paintThumb = (url) => {
                if (!url || disposed || !host.isConnected || host.dataset.thumbRequest !== cacheKey) return;
                const image = document.createElement('img');
                image.alt = '';
                image.src = url;
                host.replaceChildren(image);
            };
            if (thumbsByScene.has(cacheKey)) {
                paintThumb(thumbsByScene.get(cacheKey));
                return;
            }
            if (typeof options.thumbFor !== 'function') return;
            // hot=true：状态行只有八条，必须排在动作库那几百条缩略图前面
            Promise.resolve(options.thumbFor(scene, true, startAt > 0 ? { startAt } : undefined))
                .then((url) => {
                    if (url) thumbsByScene.set(cacheKey, url);
                    paintThumb(url);
                }, () => {});
        }

        function statesUsing(sceneId) {
            const users = [];
            outputNodes().forEach((entry) => {
                if (entry === selectedNode()) return;
                if (committedScene(entry) === sceneId) users.push(entry.params.stateKey);
            });
            return users;
        }

        function renderUserImages() {
            const entries = userImages();
            userImageGrid.replaceChildren(...entries.map((entry) => {
                const tile = document.createElement('div');
                tile.className = 'st-pick st-userimg';
                tile.setAttribute('role', 'option');
                tile.setAttribute('tabindex', '0');
                tile.dataset.imageId = entry.id;
                tile.setAttribute('role', 'option');
                const thumb = document.createElement('span');
                thumb.className = 'st-pick-thumb';
                if (entry.thumb) {
                    const image = document.createElement('img');
                    image.alt = '';
                    image.src = entry.thumb;
                    thumb.replaceChildren(image);
                }
                const text = document.createElement('span');
                text.className = 'st-pick-text';
                const label = document.createElement('span');
                label.className = 'st-pick-label';
                label.textContent = entry.label;
                text.appendChild(label);
                // 删除钮：点 ≠ 赋格，只从清单里移除
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'st-userimg-remove';
                remove.textContent = '×';
                remove.setAttribute('aria-label', t('emoteMaker.stageUserImages')
                    + ' · ' + entry.label);
                remove.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    if (typeof options.onRemoveImage !== 'function') return;
                    remove.disabled = true;
                    try {
                        await options.onRemoveImage(entry.id);
                    } finally {
                        remove.disabled = false;
                    }
                });
                tile.append(thumb, text, remove);
                // 点击 = 赋给当前选中的状态格
                tile.addEventListener('click', (event) => {
                    if (rowClickGuard ? rowClickGuard.shouldSwallow(event) : tile.dataset.justDragged) return;
                    clearPreview();
                    editGraph(() => applyImageTo(selectedNode(), entry.id));
                    loadSelected().then(renderUserImages);
                });
                holdToDrag(tile, { kind: 'image', id: entry.id, label: entry.label });
                return tile;
            }));
            userImageEmpty.hidden = entries.length > 0;
            imageImport.hidden = false;
        }

        function renderLibrary() {
            labelsCache = null;   // 动画清单可能被换掉了
            library.replaceChildren();
            thumbWatcher?.disconnect();
            const node = selectedNode();
            const committed = committedScene(node);
            const all = animationsFor();
            const shown = libraryQuery
                ? all.filter((item) => String(item.label || '').toLowerCase().includes(libraryQuery)
                    || String(item.value || '').toLowerCase().includes(libraryQuery))
                : all;
            const entries = [{
                value: ROOT_PREFIX + (node ? node.params.stateKey : ''),
                label: t('emoteMaker.stageUseDefaults'),
                isDefault: true,
            }].concat(shown.map((item) => ({ value: item.value, label: item.label })));
            entries.forEach((entry) => library.appendChild(libraryRow(entry, committed)));
            // 展开一次就排 407 个采集会把主线程压死：只取滚动条附近看得见的
            if (!thumbWatcher && typeof IntersectionObserver === 'function') {
                thumbWatcher = new IntersectionObserver((records) => {
                    records.forEach((record) => {
                        if (!record.isIntersecting) return;
                        loadThumb(record.target);
                        thumbWatcher.unobserve(record.target);
                    });
                }, { root: listScroll, rootMargin: '160px' });
            }
            library.querySelectorAll('.st-pick-thumb.is-loading').forEach((thumb) => {
                if (thumbWatcher) thumbWatcher.observe(thumb); else loadThumb(thumb);
            });
            if (!imageTab) {
                const total = all.length;
                drawerCount.textContent = shown.length === total
                    ? String(total) : `${shown.length} / ${total}`;
            }
        }

        /**
         * 只改已有行的选中态和"谁在用"，不重建行。
         * 重建会抹掉滚动位置，也会在每次拖动时白做几百个 DOM 写入。
         */
        function refreshLibraryMarks() {
            if (!drawer.classList.contains('is-open')) return;
            const committed = committedScene(selectedNode());
            const defaulted = !sceneNodeFor(selectedNode());
            library.querySelectorAll('.st-pick').forEach((row) => {
                const scene = row.dataset.scene;
                const isDefault = isDefaultSource(scene);
                row.setAttribute('aria-selected', String(
                    isDefault ? defaulted : scene === committed,
                ));
                const text = row.querySelector('.st-pick-text');
                if (!text) return;
                const existing = text.querySelector('.st-pick-usage');
                const joined = isDefault ? '' : statesUsing(scene)
                    .map((key) => t(stateLabel(key))).join(' · ');
                if (!joined) { existing?.remove(); return; }
                if (existing) { existing.textContent = joined; existing.title = joined; return; }
                const usage = document.createElement('span');
                usage.className = 'st-pick-usage';
                usage.textContent = joined;
                usage.title = joined;
                text.appendChild(usage);
            });
        }

        function loadThumb(thumb) {
            const sceneId = thumb.parentElement?.dataset.scene;
            if (!sceneId || typeof options.thumbFor !== 'function') {
                thumb.classList.remove('is-loading');
                return;
            }
            if (thumbsByScene.has(sceneId)) {
                const cached = thumbsByScene.get(sceneId);
                thumb.classList.remove('is-loading');
                if (cached && thumb.isConnected) {
                    const image = document.createElement('img');
                    image.alt = '';
                    image.src = cached;
                    thumb.replaceChildren(image);
                }
                return;
            }
            Promise.resolve(options.thumbFor(sceneId)).then((url) => {
                if (url) thumbsByScene.set(sceneId, url);
                thumb.classList.remove('is-loading');
                if (!url || disposed || !thumb.isConnected) return;
                const image = document.createElement('img');
                image.alt = '';
                image.src = url;
                thumb.replaceChildren(image);
            }, () => thumb.classList.remove('is-loading'));
        }

        function libraryRow(entry, committed) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'st-pick';
            row.dataset.scene = entry.value;
            row.setAttribute('role', 'option');
            const chosen = entry.isDefault ? !sceneNodeFor(selectedNode()) : entry.value === committed;
            row.setAttribute('aria-selected', String(chosen));
            if (entry.isDefault) row.classList.add('is-default');

            const thumb = document.createElement('span');
            thumb.className = 'st-pick-thumb';
            if (entry.isDefault) {
                thumb.classList.add('is-blank');
            } else if (typeof options.thumbFor === 'function') {
                // 只标记"待取"，真正要帧由 IntersectionObserver 决定
                thumb.classList.add('is-loading');
            }

            const text = document.createElement('span');
            text.className = 'st-pick-text';
            const label = document.createElement('span');
            label.className = 'st-pick-label';
            label.textContent = entry.label;
            text.appendChild(label);
            const users = entry.isDefault ? [] : statesUsing(entry.value);
            if (users.length) {
                const usage = document.createElement('span');
                usage.className = 'st-pick-usage';
                const names = users.map((key) => t(stateLabel(key)));
                usage.textContent = names.join(' · ');
                usage.title = names.join(' · ');
                text.appendChild(usage);
            }

            row.append(thumb, text);
            row.addEventListener('mouseenter', () => previewLibrary(entry));
            row.addEventListener('focus', () => previewLibrary(entry));
            row.addEventListener('click', (event) => {
                // 刚刚长按拖过 → 这一跳 click 不是"想选它"
                if (rowClickGuard ? rowClickGuard.shouldSwallow(event) : row.dataset.justDragged) return;
                clearPreview();
                editGraph(() => applySceneTo(selectedNode(), entry.isDefault ? '' : entry.value));
                loadSelected().then(refreshLibraryMarks);
            });
            holdToDrag(row, entry);
            return row;
        }

        /**
         * 长按才起拖：短按是"用这条"，长按是"拖到右边某一格"。
         * 和动画列表里的手势一致，用户不必记两套规则。
         */
        /**
         * 长按才起拖：短按是"用这条"，长按是"拖到右边某一格"。
         * 计时、光标圆环、位移容差都由 NekoEmoteDragHold 给（和动画列表共用一套），
         * 起拖之后把这一行本身交出去，ghost 才能长成这张选项卡的样子。
         */
        function holdToDrag(row, entry) {
            row.addEventListener('pointerdown', (event) => {
                const payload = entry.kind === 'image'
                    ? { kind: 'image', imageId: entry.id, label: entry.label }
                    : { kind: 'scene', sceneId: entry.value, label: entry.label };
                const fire = (point) => {
                    if (rowClickGuard) rowClickGuard.arm(row, event.pointerId);
                    else {
                        row.dataset.justDragged = '1';
                        window.setTimeout(() => { delete row.dataset.justDragged; }, 400);
                    }
                    hideLibraryPreview();
                    beginDrag(payload, point, row);
                };
                const hold = window.NekoEmoteDragHold;
                if (!hold) { fire(event); return; }
                hold.armHold(event, { onComplete: fire });
            });
        }

        function previewLibrary(entry) {
            previewName.textContent = entry.isDefault ? t('emoteMaker.stageUseDefaults') : entry.label;
            if (entry.isDefault) {
                // "跟随默认外观"没有单条动画可演，恢复成这一格现在的样子
                if (previewScene) { clearPreview(); loadSelected(); }
                return;
            }
            setPreview(entry.value, false);
        }

        function hideLibraryPreview() {
            if (!previewScene) return;
            // 指针离开列表＝"我就看看"：把试演收回去，别让仪表带停在虚线上
            clearPreview();
            playhead = 0;
            loadSelected();
        }

        function drawLibraryPreview() {
            if (!drawer.classList.contains('is-open')) return;
            const node = selectedNode();
            // 用真正会被烘进去的那一段，而不是整条动画：
            // 抽屉展开时台面是定格的，检视窗是唯一能看见"选了哪一帧"的地方。
            const slice = node ? sliceByNode.get(node.id) : null;
            const frames = slice?.length ? slice : activeFrames(node);
            const ctx = context2d(previewCanvas);
            if (!ctx) return;
            const box = previewCanvas.getBoundingClientRect();
            const width = Math.max(80, Math.round(box.width || PREVIEW_CARD_W));
            const height = Math.max(80, Math.round(box.height || PREVIEW_CARD_H));
            // 尺寸还没量出来（宽 0）时不写后备存储，避免先画在错误尺寸上再跳一次
            if (!box.width) return;
            if (previewCanvas.width !== Math.round(width * (canvasRatio || dpr()))
                || previewCanvas.height !== Math.round(height * (canvasRatio || dpr()))) {
                previewCanvas.width = Math.round(width * (canvasRatio || dpr()));
                previewCanvas.height = Math.round(height * (canvasRatio || dpr()));
            }
            ctx.clearRect(0, 0, width, height);
            if (!frames.length) return;
            const frame = frames[playhead % frames.length];
            const k = Math.min(width / frame.width, height / frame.height) * 0.9;
            putFrame(ctx, frame,
                (width - frame.width * k) / 2, (height - frame.height * k) / 2,
                frame.width * k, frame.height * k);
        }

        /** 悬停只出海报档，不做清晰档升级：抽屉里的放大预览就是给"看一眼"用的。*/
        async function setPreview(sceneId, upgrade) {
            if (previewScene === sceneId && previewQuality === 'poster') return;
            if (upgradeTimer) window.clearTimeout(upgradeTimer);
            upgradeTimer = 0;
            previewScene = sceneId;
            previewQuality = 'poster';
            playhead = 0;
            await framesOf(sceneId, 'poster');
            if (disposed || previewScene !== sceneId) return;
            publishSlice();
            drawStrip();
            drawStage();
            drawLibraryPreview();
            syncAnimButton();
            if (!upgrade) return;
            upgradeTimer = window.setTimeout(async () => {
                upgradeTimer = 0;
                if (disposed || previewScene !== sceneId) return;
                previewQuality = 'full';
                await framesOf(sceneId, 'full');
                if (disposed || previewScene !== sceneId) return;
                playhead = 0;
                publishSlice();
                drawStrip();
                drawStage();
            }, UPGRADE_AFTER_MS);
        }

        function openLibrary() {
            drawer.inert = false;
            if (!drawer.classList.contains('is-open')) {
                renderLibrary();
                applyTab();   // 恢复上次的页签（动作库/用户图片）并刷计数
                drawer.classList.add('is-open');
                dock.classList.add('is-open');
                // 先按最终尺寸量一次再画：否则首帧用画布默认的 300x150 排版，
                // 图形会偏上，等一帧才回到中间。
                drawLibraryPreview();
            }
            animButton.setAttribute('aria-expanded', 'true');
            rail.setAttribute('aria-expanded', 'true');
        }

        function closeLibrary() {
            if (drawer.contains(document.activeElement)) rail.focus();
            drawer.inert = true;
            const wasOpen = drawer.classList.contains('is-open');
            drawer.classList.remove('is-open');
            dock.classList.remove('is-open');
            if (wasOpen) paint();   // 暂停期间没画，这里补回当前帧
            animButton.setAttribute('aria-expanded', 'false');
            rail.setAttribute('aria-expanded', 'false');
            hideLibraryPreview();
            // 还没排上的缩略图直接丢掉，别继续占着渲染器
            onPickerClose();
            if (previewScene) {
                clearPreview();
                playhead = 0;
                loadSelected();
            }
        }

        function toggleLibrary() {
            if (drawer.classList.contains('is-open')) closeLibrary(); else openLibrary();
        }

        animButton.addEventListener('click', toggleLibrary);
        rail.addEventListener('click', toggleLibrary);
        // 指针离开列表就收起放大预览；试演留在时间轴上，
        // 因为"这条多长、有多少帧"正是底部那条要回答的问题。
        library.addEventListener('mouseleave', hideLibraryPreview);

        // 关闭是覆盖层自己的责任：调用方忘了接 onClose 也不能把人锁在一个关不掉的模态里。
        // 必须显式声明——写成裸 close 会静默落到 window.close() 上，
        // 而表情制作页是 window.open 打开的，那等于把整个页面关掉。
        function close() {
            disposed = true;
            onPickerClose();
            if (playTimer) cancelAnimationFrame(playTimer);
            playTimer = 0;
            if (upgradeTimer) window.clearTimeout(upgradeTimer);
            upgradeTimer = 0;
            document.removeEventListener('keydown', onKey);
            window.removeEventListener('resize', onResize);
            overlay.remove();
            if (typeof options.onClose === 'function') options.onClose();
        }

        closeBtn.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            close();
        });

        const onResize = () => {
            // 改后备存储会清空画布，所以隔着抽屉也要补画一帧
            if (sizeCanvases()) paint(true);
        };
        window.addEventListener('resize', onResize);

        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            // 抽屉开着时 Esc 是"收起动作库"，不该把整个表演台关掉。
            // 但抽屉是常驻工作面板，点外面不自动关——挑动作时经常要点到台上。
            if (drawer.classList.contains('is-open')) {
                event.stopPropagation();
                closeLibrary();
            } else {
                close();
            }
        };
        document.addEventListener('keydown', onKey);

        // ── playback ─────────────────────────────────────────────────────────
        // 自动循环是"表演"，不是装饰：尊重系统的减少动效偏好时停在所选的那一帧，
        // 起止与大小角度照常可编辑（拖动手柄本身会重画）。
        const reduceMotion = typeof matchMedia === 'function'
            && matchMedia('(prefers-reduced-motion: reduce)').matches;
        let last = performance.now();
        function tick(now) {
            if (disposed) return;
            playTimer = requestAnimationFrame(tick);
            if (reduceMotion) return;
            const node = selectedNode();
            const slice = node ? sliceByNode.get(node.id) : null;
            if (!slice?.length || stripDrag || stageDrag) return;
            if ((now - last) / 1000 < 1 / rateOf(selectedNode())) return;
            last = now;
            playhead = (playhead + 1) % slice.length;
            drawStrip();
            // 台面被抽屉铺满遮住时不画它：省掉每 tick 一次整幅缩放合成
            if (drawer.classList.contains('is-open')) drawLibraryPreview();
            else drawStage();
        }

        (async function boot() {
            // paint() 里不再每次量尺寸，首次绘制前必须量一次
            sizeCanvases();
            if (!outputNodes().length) ensureNodeForState('idle_image');
            if (!nodeById(selected)) selected = outputNodes()[0]?.id || null;
            await loadSelected();
            playTimer = requestAnimationFrame(tick);
        }());

        return {
            element: overlay,
            // 动画列表可能被改名或增删：外部改动后请我重取帧源与选项
            refresh: async () => {
                labelsCache = null;
                framesRevision += 1;
                framesBySource.clear();
                sliceByNode.clear();
                await loadSelected();
            },
            getGraph: snapshotGraph,
            isEditing: () => Boolean(stripDrag || stageDrag),
            setGraph(next) {
                const stateKey = selectedNode()?.params.stateKey;
                graph = model.normalizeGraph(next);
                const node = stateKey ? ensureNodeForState(stateKey) : outputNodes()[0];
                selected = node?.id || null;
                clearPreview();
                playhead = 0;
                framesRevision += 1;
                framesBySource.clear();
                sliceByNode.clear();
                paint();
                refreshLibraryMarks();
                void loadSelected();
            },

            // 长按动画列表里的动画拖过来 → 落到哪一格
            containsPoint(x, y) {
                const box = panel.getBoundingClientRect();
                return x >= box.left && x <= box.right && y >= box.top && y <= box.bottom;
            },
            stateAtPoint(x, y) {
                // 只有压在某一行状态上才算落点。松在台面/胶片带/空白处不改任何东西 ——
                // "拖出去一松手就写进当前选中格"会把人手里那点试错空间吃掉。
                const target = document.elementFromPoint(x, y);
                const tile = target && target.closest ? target.closest('.st-tile') : null;
                return tile ? tile.dataset.state : null;
            },
            setDropHighlight(stateKey) {
                shelf.querySelectorAll('.st-tile').forEach((tile) => {
                    tile.classList.toggle('is-drop-target', tile.dataset.state === stateKey);
                });
            },
            async assignImage(stateKey, imageId) {
                clearPreview();
                editGraph(() => {
                    const node = ensureNodeForState(stateKey);
                    selected = node.id;
                    applyImageTo(node, imageId);
                });
                await loadSelected();
                renderUserImages();
            },

            refreshUserImages: async () => {
                if (disposed) return;
                imageFramesRevision += 1;
                for (const key of framesBySource.keys()) {
                    if (key.startsWith('images:')) framesBySource.delete(key);
                }
                renderUserImages();
                // 清单是外部喂进来的：绑图格子的副标题/缩略图也吃这份名单，
                // 只重绘网格的话，异步恢复完成后那八行会一直停在"跟随默认"
                paint();
                await loadSelected();
            },

            async assignScene(stateKey, sceneId) {
                clearPreview();
                editGraph(() => {
                    const node = ensureNodeForState(stateKey);
                    selected = node.id;
                    applySceneTo(node, sceneId);
                });
                await loadSelected();
                // 刚拖完一条就想知道它占了哪几格；只改标记，不动滚动位置
                refreshLibraryMarks();
            },

            dispose: () => {
                disposed = true;
                thumbWatcher?.disconnect();
                window.removeEventListener('resize', onResize);
                if (upgradeTimer) window.clearTimeout(upgradeTimer);
                if (playTimer) cancelAnimationFrame(playTimer);
                playTimer = 0;
                document.removeEventListener('keydown', onKey);
                overlay.remove();
            },
        };
    }

    return Object.freeze({
        create, TILE_STATES, secondsLabel, clamp, stateLabel,
        setUserImages(list) { userImagesProvider = () => (Array.isArray(list) ? list : []); },
    });
}));

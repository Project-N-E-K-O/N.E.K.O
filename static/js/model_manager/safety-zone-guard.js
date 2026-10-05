/**
 * 模型管理器页「安全区」防护。
 *
 * 背景：模型位置是全局持久化的，当模型停在屏幕左侧、落入 #sidebar 的水平覆盖范围时，
 * 进入 /model_manager 后模型会被侧边栏完全盖住，且由于指针命中的是 sidebar DOM，
 * 无法把模型拖出来。
 *
 * 本模块只在本页（body.model-manager-page）生效，主界面行为完全不变：
 *   1. 每次模型加载完成后，把模型中心移到「安全区几何中心」（临时位移，不写库）；
 *      注意本页一次进入可能加载多次模型（switchModelDisplay 先载一个，随后
 *      loadCurrentCharacterModel 再载角色真正的模型），后一次会把该模型自己的存档
 *      位置写回去，所以这里必须「每次就绪都居中」，否则最终位置会退回主页面那套。
 *   2. 离开页面时把模型移回进入前的屏幕中心（同样不写库）；
 *   3. 拖拽结束后，若模型中心水平落入遮挡区，则把它推回安全区（仅水平方向）。
 *      本页有两条拖拽路径——「空白背景拖动」（background-model-drag.js）和
 *      「直接抓住模型拖动」（各运行时自己的拖拽代码）。两者都要覆盖，因此这里
 *      统一在全局 pointerup/pointercancel 后做多次幂等的 clamp，跨过各运行时
 *      260~300ms 的回弹/保存动画再复核。
 *   4. 本页不写位置：管理页上任何位置持久化都会被「替换成后端已经存着的那个位置」，
 *      所以管理页的临时摆位绝不会影响主页面；缩放/旋转/参数/相机等仍照原样保存。
 *      （管理页的位置与主页面位置互不影响。）
 *      改写的入口是运行时里的判断：saveUserPreferences 开头看本模块在不在，在就调用
 *      rewritePositionWrite() 拿改写后的 position/display/viewport。本模块只在管理页
 *      加载，主页面等其它页面分支不执行，因此不需要包装运行时函数。
 *
 * 所有模型类型统一用「屏幕 CSS 像素」做中间层，只用两个原语：
 *   getModelScreenCenter()  取模型中心的屏幕坐标
 *   moveModelScreenBy(dx,dy) 按屏幕像素平移模型
 * 居中 / 恢复 / clamp 全部复用它们，避免各类型私有坐标换算重复出错。
 */
(function installModelManagerSafetyZoneGuard() {
    'use strict';

    const SIDEBAR_MARGIN_PX = 24;
    const FALLBACK_HALF_WIDTH_PX = 150;
    const READINESS_POLL_INTERVAL_MS = 200;
    const READINESS_TIMEOUT_MS = 30000;
    const DRAGGING_BODY_CLASS = 'model-manager-background-dragging';
    // 居中后按这些时间点复核：一次进入可能触发多次模型加载，且各运行时的位置写回
    // （存档位置套用、边界回弹、视口归一化）不一定发生在 ready 事件之前。
    const CENTERING_REASSERT_DELAYS_MS = [0, 60, 160, 320, 600, 1000];
    // 松手后补做安全区约束的时间点：立即一次给出反馈，其余几次用于跨过各运行时
    // 的边界回弹（VRM/MMD/PNGTuber 260ms、Live2D 300ms 防抖吸附）之后的位置写回。
    const POST_INTERACTION_CLAMP_DELAYS_MS = [0, 120, 320, 700];
    // 各运行时的「模型已加载完成」事件；本页只认当前激活类型的就绪状态。
    const MODEL_READY_EVENTS = [
        'neko-live2d-model-ready',
        'live2d-model-ready',
        'vrm-model-loaded',
        'mmd-model-loaded',
        'pngtuber-model-loaded'
    ];
    // switchModelDisplay 在切换模型类型时派发，用于「还没就绪就先等着」。
    const MODE_SET_EVENT = 'neko-model-manager-mode-set';
    // ── 「本页不写位置」相关 ──
    const PREFERENCES_ENDPOINT = '/api/config/preferences';
    // 排查用：控制台执行 localStorage.setItem('nekoSafetyZoneDebug','1') 后，每次改写都会打印明细
    const DEBUG_FLAG_KEY = 'nekoSafetyZoneDebug';

    let savedCenter = null;
    let readyListenersBound = false;
    let pollTimerId = null;
    let readinessDeadline = 0;
    let centeringTimers = [];
    let clampTimers = [];
    let pointerDown = false;
    let stopped = false;
    let storedPositionsPromise = null;
    let storedPositionByPath = null;
    let storedPreferencesList = null;
    let loadSnapshotByType = new Map();

    function isMmPage() {
        return !!(document.body && document.body.classList.contains('model-manager-page'));
    }

    function isDragging() {
        return !!(document.body && document.body.classList.contains(DRAGGING_BODY_CLASS));
    }

    function dragController() {
        return window.ModelManagerBackgroundDragController || null;
    }

    // 复用 background-model-drag.js 的类型判定，避免两套判断漂移。
    function getActiveModelType() {
        const controller = dragController();
        if (controller && typeof controller.getActiveModelType === 'function') {
            return controller.getActiveModelType();
        }
        return 'live2d';
    }

    function getLive3DSubType() {
        const controller = dragController();
        if (controller && typeof controller.getLive3DSubType === 'function') {
            return controller.getLive3DSubType();
        }
        return 'vrm';
    }

    function live3dManager() {
        return getLive3DSubType() === 'mmd' ? window.mmdManager : window.vrmManager;
    }

    function sidebarRect() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar || typeof sidebar.getBoundingClientRect !== 'function') return null;
        const rect = sidebar.getBoundingClientRect();
        if (!rect || !Number.isFinite(rect.right)) return null;
        return rect;
    }

    function isLive2DReady(manager) {
        return !!(manager && manager.currentModel && !manager.currentModel.destroyed &&
            manager._isModelReadyForInteraction === true &&
            manager.pixi_app && manager.pixi_app.view && manager.pixi_app.renderer);
    }

    // Live2D 画布换算上下文：渲染逻辑坐标 <-> CSS 像素（与 background-model-drag.js 一致）。
    function live2dContext() {
        const manager = window.live2dManager;
        if (!isLive2DReady(manager)) return null;
        const canvas = manager.pixi_app.view;
        const screen = manager.pixi_app.renderer.screen;
        if (!canvas || typeof canvas.getBoundingClientRect !== 'function' || !screen) return null;
        const rect = canvas.getBoundingClientRect();
        if (!(rect.width > 0) || !(rect.height > 0)) return null;
        const scaleX = (Number(screen.width) || rect.width) / rect.width;
        const scaleY = (Number(screen.height) || rect.height) / rect.height;
        if (!(scaleX > 0) || !(scaleY > 0)) return null;
        return { manager, model: manager.currentModel, rect, scaleX, scaleY };
    }

    function boundsCenter(bounds) {
        if (!bounds) return null;
        const left = Number.isFinite(bounds.left) ? bounds.left : bounds.x;
        const top = Number.isFinite(bounds.top) ? bounds.top : bounds.y;
        const right = Number.isFinite(bounds.right) ? bounds.right : left + bounds.width;
        const bottom = Number.isFinite(bounds.bottom) ? bounds.bottom : top + bounds.height;
        if (![left, top, right, bottom].every(Number.isFinite)) return null;
        return { x: (left + right) / 2, y: (top + bottom) / 2 };
    }

    function boundsWidth(bounds) {
        if (!bounds) return NaN;
        if (Number.isFinite(bounds.left) && Number.isFinite(bounds.right)) {
            return bounds.right - bounds.left;
        }
        return Number(bounds.width);
    }

    function getModelScreenCenter() {
        const type = getActiveModelType();

        if (type === 'live2d') {
            const ctx = live2dContext();
            if (!ctx || typeof ctx.model.getBounds !== 'function') return null;
            let center;
            try {
                center = boundsCenter(ctx.model.getBounds());
            } catch (_) {
                return null;
            }
            if (!center) return null;
            return {
                x: ctx.rect.left + center.x / ctx.scaleX,
                y: ctx.rect.top + center.y / ctx.scaleY
            };
        }

        if (type === 'pngtuber') {
            const manager = window.pngtuberManager;
            if (!manager) return null;
            if (typeof manager.getModelCenterInWindow === 'function') {
                const center = manager.getModelCenterInWindow();
                if (center && Number.isFinite(center.x) && Number.isFinite(center.y)) {
                    return { x: center.x, y: center.y };
                }
                return null;
            }
            const container = manager.container;
            if (container && typeof container.getBoundingClientRect === 'function') {
                const rect = container.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                }
            }
            return null;
        }

        const manager = live3dManager();
        const interaction = manager && manager.interaction;
        if (!interaction || typeof interaction._getProjectedModelCenterInWindow !== 'function') return null;
        const center = interaction._getProjectedModelCenterInWindow();
        if (!center || !Number.isFinite(center.x) || !Number.isFinite(center.y)) return null;
        return { x: center.x, y: center.y };
    }

    // 运行时自己正在拖拽/回弹/自动移动时不介入，避免两边抢位置。
    function isRuntimeBusy() {
        const type = getActiveModelType();

        if (type === 'live2d') {
            const manager = window.live2dManager;
            return !!(manager && (manager._isSnapping || manager._isDraggingModel || manager.isDragging));
        }

        if (type === 'pngtuber') {
            const manager = window.pngtuberManager;
            return !!(manager && (manager._dragState || manager._isDraggingModel ||
                manager.isDragging || manager._edgeSnapAnimationFrame));
        }

        const manager = live3dManager();
        const interaction = manager && manager.interaction;
        return !!(interaction && (interaction.isDragging || interaction._isSnappingModel));
    }

    // VRM 的 _moveModelCenterToWindowPoint 内部会 _cancelGuidedMovement({invalidateInteraction:true})，
    // 把 movementToken 自增。若这次调用撞上拖拽收尾（_endDrag）里 stillOwnsInteraction() 的检查，
    // 收尾会提前 return，导致边界回弹和位置保存被跳过。所以：
    //   - 有自动移动/转向在跑时直接不动（宁可少约束，也不打断运行时的流程）；
    //   - 否则调用前后保存/还原 movementToken，使这次位移对收尾流程完全透明。
    function isVrmGuidedMovementActive(interaction) {
        const isSet = (value) => value !== null && value !== undefined;
        return !!(interaction.isMoving || interaction._movementAction ||
            isSet(interaction._movementOwnerToken) || isSet(interaction._movementFinishingToken) ||
            isSet(interaction._smoothFacingFrame));
    }

    function moveModelScreenBy(dx, dy) {
        if (!Number.isFinite(dx) || !Number.isFinite(dy)) return false;
        if (dx === 0 && dy === 0) return true;

        const type = getActiveModelType();

        if (type === 'live2d') {
            const ctx = live2dContext();
            if (!ctx) return false;
            ctx.model.x += dx * ctx.scaleX;
            ctx.model.y += dy * ctx.scaleY;
            ctx.manager.isFocusing = false;
            return true;
        }

        if (type === 'pngtuber') {
            const manager = window.pngtuberManager;
            if (!manager || typeof manager.moveModelCenterToWindowPoint !== 'function' ||
                typeof manager.getModelCenterInWindow !== 'function') {
                return false;
            }
            // 本页默认忽略已存偏移渲染（getRenderPlacement 归零），必须先进入编辑态，
            // 否则改 offset 不会产生任何视觉位移。
            if (typeof manager.beginModelManagerPositionEditing === 'function') {
                manager.beginModelManagerPositionEditing();
            }
            const center = manager.getModelCenterInWindow();
            if (!center) return false;
            return manager.moveModelCenterToWindowPoint(center.x + dx, center.y + dy) === true;
        }

        const manager = live3dManager();
        const interaction = manager && manager.interaction;
        if (!interaction ||
            typeof interaction._getProjectedModelCenterInWindow !== 'function' ||
            typeof interaction._moveModelCenterToWindowPoint !== 'function') {
            return false;
        }
        const isVrm = getLive3DSubType() !== 'mmd';
        if (isVrm && isVrmGuidedMovementActive(interaction)) return false;
        const center = interaction._getProjectedModelCenterInWindow();
        if (!center) return false;
        const token = isVrm ? interaction.movementToken : undefined;
        const moved = interaction._moveModelCenterToWindowPoint(center.x + dx, center.y + dy) === true;
        if (isVrm) interaction.movementToken = token;
        return moved;
    }

    function isModelReadyForActiveType() {
        const type = getActiveModelType();

        if (type === 'live2d') {
            return !!live2dContext();
        }

        if (type === 'pngtuber') {
            const manager = window.pngtuberManager;
            return !!(manager && manager.image &&
                typeof manager.getModelCenterInWindow === 'function' &&
                manager.getModelCenterInWindow());
        }

        const manager = live3dManager();
        return !!(manager && manager._isModelReadyForInteraction === true &&
            manager.interaction && getModelScreenCenter());
    }

    function computeTargetCenter() {
        const bar = sidebarRect();
        const right = bar && Number.isFinite(bar.right) ? bar.right : 0;
        return {
            x: (right + window.innerWidth) / 2,
            y: window.innerHeight / 2
        };
    }

    function applyCentering() {
        const current = getModelScreenCenter();
        if (!current) return false;
        const target = computeTargetCenter();
        return moveModelScreenBy(target.x - current.x, target.y - current.y);
    }

    // 用户此刻没在亲手操作（按着指针 / 正在拖背景）时，才允许自动摆位。
    function canTakeOverModel() {
        return !stopped && isMmPage() && !pointerDown && !isDragging();
    }

    // 更严格一档：运行时自己也在动（回弹/吸附/自动移动）时不插手，避免两边抢位置。
    function canAutoAdjust() {
        return canTakeOverModel() && !isRuntimeBusy();
    }

    function cancelPendingCentering() {
        while (centeringTimers.length) {
            window.clearTimeout(centeringTimers.pop());
        }
    }

    // 居中后按时间点复核几次：晚到的存档位置写回会被再次纠正，最终一定落在安全区中心。
    function scheduleCenteringReassert() {
        cancelPendingCentering();
        CENTERING_REASSERT_DELAYS_MS.forEach((delay) => {
            centeringTimers.push(window.setTimeout(() => {
                if (!canAutoAdjust()) return;
                applyCentering();
            }, delay));
        });
    }

    function cancelPendingClamps() {
        while (clampTimers.length) {
            window.clearTimeout(clampTimers.pop());
        }
    }

    function runClampPass() {
        if (!canAutoAdjust()) return;
        clampAfterDrag();
    }

    // 松手后补做几次（幂等）：第一次立即纠正，后面几次跨过运行时的回弹动画再复核。
    function scheduleClampPasses() {
        cancelPendingClamps();
        if (stopped || !isMmPage()) return;
        POST_INTERACTION_CLAMP_DELAYS_MS.forEach((delay) => {
            clampTimers.push(window.setTimeout(runClampPass, delay));
        });
    }

    // 只在「模型已就绪」时真正居中；否则交给轮询等它就绪。
    // 用户此刻正按着指针就不动（等他松手后由轮询补上），避免跟他抢模型。
    function tryCenter() {
        if (!canTakeOverModel() || !isModelReadyForActiveType()) return false;
        const current = getModelScreenCenter();
        if (!current) return false;
        // 只记第一次：这是「进入页面之前」的位置，供离开时恢复。
        if (!savedCenter) savedCenter = { cx: current.x, cy: current.y };
        applyCentering();
        scheduleCenteringReassert();
        return true;
    }

    function stopPolling() {
        if (pollTimerId !== null) {
            window.clearInterval(pollTimerId);
            pollTimerId = null;
        }
    }

    function stopReadinessWatch() {
        stopPolling();
        if (!readyListenersBound) return;
        readyListenersBound = false;
        MODEL_READY_EVENTS.forEach((name) => window.removeEventListener(name, onModelReadySignal));
        window.removeEventListener(MODE_SET_EVENT, onModeSet);
    }

    // 每次「模型加载完成」或「切换模型类型」都重新摆位一次。
    function armCentering() {
        if (!isMmPage() || stopped) return;
        // 必须在任何搬动之前记录「加载时位置」，它是本页唯一允许被写回去的位置。
        captureLoadSnapshot();
        readinessDeadline = Date.now() + READINESS_TIMEOUT_MS;
        if (tryCenter()) {
            stopPolling();
            return;
        }
        if (pollTimerId !== null) return;
        pollTimerId = window.setInterval(() => {
            if (stopped || !isMmPage()) {
                stopPolling();
                return;
            }
            if (tryCenter()) {
                stopPolling();
                return;
            }
            if (Date.now() > readinessDeadline) stopPolling();
        }, READINESS_POLL_INTERVAL_MS);
    }

    function onModelReadySignal() {
        if (!isMmPage()) {
            stopReadinessWatch();
            return;
        }
        cancelPendingCentering();
        armCentering();
    }

    function onModeSet() {
        if (!isMmPage() || stopped) return;
        // 刚切换类型，新模型多半还没就绪：作废旧计划，重新等待并居中。
        cancelPendingCentering();
        armCentering();
    }

    function startReadinessWatch() {
        if (readyListenersBound) return;
        readyListenersBound = true;
        MODEL_READY_EVENTS.forEach((name) => window.addEventListener(name, onModelReadySignal));
        window.addEventListener(MODE_SET_EVENT, onModeSet);
        armCentering();
    }

    function restoreOnLeave() {
        const saved = savedCenter;
        if (!saved) return;
        savedCenter = null;
        if (!isMmPage()) return;
        const current = getModelScreenCenter();
        if (!current) return;
        moveModelScreenBy(saved.cx - current.x, saved.cy - current.y);
    }

    // clamp 只是软约束：夹在 [140, 25% 窗口宽] 之间，避免过小挡不住、过大把模型顶到屏外。
    function boundHalfWidth(value) {
        const measured = Number.isFinite(value) && value > 0 ? value : FALLBACK_HALF_WIDTH_PX;
        const lower = 140;
        const upper = Math.max(lower, window.innerWidth * 0.25);
        return Math.max(lower, Math.min(measured, upper));
    }

    function estimateModelHalfWidth() {
        const type = getActiveModelType();

        if (type === 'live2d') {
            const ctx = live2dContext();
            if (ctx && typeof ctx.model.getBounds === 'function') {
                try {
                    const width = boundsWidth(ctx.model.getBounds());
                    if (Number.isFinite(width) && width > 0) {
                        return boundHalfWidth(width / 2 / ctx.scaleX);
                    }
                } catch (_) { /* 退回保守估计 */ }
            }
            return boundHalfWidth(NaN);
        }

        if (type === 'pngtuber') {
            const manager = window.pngtuberManager;
            const image = manager && manager.image;
            if (image && typeof image.getBoundingClientRect === 'function') {
                const rect = image.getBoundingClientRect();
                if (rect.width > 0) return boundHalfWidth(rect.width / 2);
            }
            return boundHalfWidth(NaN);
        }

        const manager = live3dManager();
        const canvas = manager && manager.renderer && manager.renderer.domElement;
        if (canvas && typeof canvas.getBoundingClientRect === 'function') {
            const rect = canvas.getBoundingClientRect();
            if (rect.width > 0) return boundHalfWidth(rect.width * 0.15);
        }
        return boundHalfWidth(NaN);
    }

    function clampAfterDrag() {
        if (!isMmPage() || isRuntimeBusy()) return false;
        const bar = sidebarRect();
        if (!bar) return false;
        const current = getModelScreenCenter();
        if (!current) return false;
        const minX = bar.right + estimateModelHalfWidth() + SIDEBAR_MARGIN_PX;
        if (current.x < minX) {
            return moveModelScreenBy(minX - current.x, 0);
        }
        return true;
    }

    function bindWindowHooks() {
        // 返回主页的两条路径（window.close / location.href='/'）都会触发卸载事件，
        // 因此不需要挂在按钮 click 上，避免用户取消「未保存确认」时把模型位置弄乱。
        window.addEventListener('beforeunload', restoreOnLeave);
        window.addEventListener('pagehide', restoreOnLeave);
        // 用户一旦按下指针，就说明他要自己摆，立刻停掉所有自动摆位。
        window.addEventListener('pointerdown', () => {
            pointerDown = true;
            cancelPendingCentering();
            cancelPendingClamps();
        }, true);
        window.addEventListener('pointerup', () => {
            pointerDown = false;
            scheduleClampPasses();
        }, true);
        window.addEventListener('pointercancel', () => {
            pointerDown = false;
            scheduleClampPasses();
        }, true);
        window.addEventListener('unload', () => {
            stopped = true;
            pointerDown = false;
            stopReadinessWatch();
            cancelPendingCentering();
            cancelPendingClamps();
        });
    }

    // ═══════════════════ 本页不写位置 ═══════════════════
    // 管理页会把模型临时摆到安全区中心，所以任何位置持久化都必须被替换成
    // 「后端已经存着的那个位置」，否则下一次自动保存 / 点保存设置就会把居中位置
    // 写回全局偏好，主页面也跟着变。位置连同解释它的 viewport/display 一起替换
    // （否则「旧位置 + 新归一化基准」会让主页面渲染到别处）；缩放、旋转、参数、
    // 相机等一律照原样保存。
    //
    // 调用方式是「运行时里判断」：Live2D / VRM / MMD 的 saveUserPreferences 开头会看
    // 本模块在不在，在就问它要一份改写后的参数；PNGTuber 那处直接在 page-controller
    // 里摘掉位置字段。本模块只在模型管理页加载，所以其它页面这些分支根本不会执行
    // （主页面行为零变化），也就不需要「等运行时出现再补装」这类机制。

    function isUsablePosition(position, shape) {
        if (!position || typeof position !== 'object') return false;
        if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) return false;
        // 调用方给的是三维位置时，替换值也必须是三维，否则会被下游校验整单拒掉。
        const needsZ = !!(shape && typeof shape === 'object' && Number.isFinite(shape.z));
        return !needsZ || Number.isFinite(position.z);
    }

    // 偏好里的模型路径形式不一定和调用方一致（历史原因），所以按运行时自己的策略匹配：
    // 精确 → 去掉 query/hash 归一化 → 文件名（小写）相同。（对齐 live2d-init.js / vrm-core.js）
    function normalizePreferencePath(value) {
        const raw = value && typeof value === 'object' ? (value.url || value.path || '') : value;
        if (typeof raw !== 'string') return '';
        return raw.split('#')[0].split('?')[0].trim().replace(/\\/g, '/');
    }

    function preferenceFilename(path) {
        const parts = String(path).split('/').filter(Boolean);
        return parts.length ? parts[parts.length - 1].toLowerCase() : '';
    }

    function preferencePathMatches(candidate, target) {
        const left = normalizePreferencePath(candidate);
        const right = normalizePreferencePath(target);
        if (!left || !right) return true;   // 路径未知时不否决
        if (left === right) return true;
        const leftName = preferenceFilename(left);
        return !!leftName && leftName === preferenceFilename(right);
    }

    async function fetchStoredPositions() {
        try {
            const response = await window.fetch(PREFERENCES_ENDPOINT, { credentials: 'same-origin' });
            if (!response || !response.ok) return null;
            const data = await response.json();
            const list = Array.isArray(data)
                ? data
                : (data && Array.isArray(data.preferences) ? data.preferences : null);
            if (!list) return null;
            const entries = list.filter((entry) => entry && typeof entry === 'object');
            const map = new Map();
            entries.forEach((entry) => {
                const key = normalizePreferencePath(entry.model_path || entry.modelPath);
                if (key) map.set(key, entry);
            });
            return { map, entries };
        } catch (_) {
            return null;
        }
    }

    function ensureStoredPositions() {
        if (!storedPositionsPromise) {
            storedPositionsPromise = fetchStoredPositions().then((result) => {
                if (result) {
                    storedPositionByPath = result.map;
                    storedPreferencesList = result.entries;
                }
                return result;
            });
        }
        return storedPositionsPromise;
    }

    function lookupStoredEntry(modelPath) {
        const key = normalizePreferencePath(modelPath);
        if (!key) return null;
        if (storedPositionByPath) {
            const normalizedExact = storedPositionByPath.get(key);
            if (normalizedExact) return normalizedExact;
        }
        if (!Array.isArray(storedPreferencesList)) return null;
        // 再兜一层：文件名相同就认为是同一个模型（与运行时的匹配策略一致）
        const name = preferenceFilename(key);
        if (!name) return null;
        return storedPreferencesList.find((entry) => (
            preferenceFilename(normalizePreferencePath(entry.model_path || entry.modelPath)) === name
        )) || null;
    }

    // 模型加载完成、且本模块还没搬动它之前的位置。它要么等于后端存的位置（有偏好记录），
    // 要么等于默认布局（没有记录），两种情况都正好是主页面会用的值。
    function captureLoadSnapshot() {
        const type = getActiveModelType();
        let record = null;

        if (type === 'live2d') {
            const manager = window.live2dManager;
            const model = manager && manager.currentModel;
            if (model && !model.destroyed && isUsablePosition({ x: model.x, y: model.y })) {
                record = {
                    path: String(manager._lastLoadedModelPath || ''),
                    position: { x: model.x, y: model.y }
                };
            }
        } else if (type === 'live3d') {
            const manager = live3dManager();
            const model = manager && manager.currentModel;
            const node = model && (getLive3DSubType() === 'mmd' ? model.mesh : model.scene);
            if (node && node.position && isUsablePosition(node.position) && Number.isFinite(node.position.z)) {
                record = {
                    path: String(model.url || ''),
                    position: { x: node.position.x, y: node.position.y, z: node.position.z }
                };
            }
        }

        if (record) loadSnapshotByType.set(type, record);
    }

    // 返回 null 表示「这次不替换」。
    function resolvePositionSubstitute(modelPath, incomingPosition) {
        const key = modelPath === undefined || modelPath === null ? '' : String(modelPath);

        if (key) {
            const entry = lookupStoredEntry(modelPath);
            if (entry && isUsablePosition(entry.position, incomingPosition)) {
                return {
                    position: Object.assign({}, entry.position),
                    display: entry.display,
                    viewport: entry.viewport,
                    replaceMeta: true
                };
            }
        }

        // 后端没有这条记录（或记录里没有可用位置）时，用「模型加载完、本模块还没搬动它」
        // 那一刻的位置兜底 —— 那正好等于默认布局，也就是主页面会用的值。
        for (const record of loadSnapshotByType.values()) {
            if (!record || !isUsablePosition(record.position, incomingPosition)) continue;
            if (!preferencePathMatches(record.path, key)) continue;   // 不是同一个模型宁可不换
            return { position: Object.assign({}, record.position), replaceMeta: false };
        }

        return null;
    }

    // 排查用开关：默认关闭，开启后每次位置写入都会打印明细。
    function isDebugEnabled() {
        try {
            return !!(window.localStorage && window.localStorage.getItem(DEBUG_FLAG_KEY) === '1');
        } catch (_) {
            return false;
        }
    }

    // 供运行时的 saveUserPreferences 调用：把一次位置写入改写成「后端原值」。
    // 本函数保证不抛异常——出任何意外都原样返回入参，绝不让保存因为它而失败。
    async function rewritePositionWrite(modelPath, position, display, viewport) {
        const fallback = { position, display, viewport };
        try {
            if (!isMmPage() || stopped) return fallback;
            await ensureStoredPositions();
            const substitute = resolvePositionSubstitute(modelPath, position);
            if (isDebugEnabled()) {
                console.log('[安全区] 位置写入改写', {
                    模型路径: modelPath,
                    本次要写的位置: position,
                    改写后: substitute ? substitute.position : '(未改写，按原值)',
                    来源: substitute ? (substitute.replaceMeta ? '后端已存记录' : '加载时快照') : '无',
                    底账条数: storedPositionByPath ? storedPositionByPath.size : -1,
                    快照: Array.from(loadSnapshotByType.values()).map((r) => r.path)
                });
            }
            if (!substitute) return fallback;
            return {
                position: substitute.position,
                display: substitute.replaceMeta ? substitute.display : display,
                viewport: substitute.replaceMeta ? substitute.viewport : viewport
            };
        } catch (error) {
            console.warn('[模型管理] 位置写入改写失败，改按原值保存:', error);
            return fallback;
        }
    }

    window.ModelManagerSafetyZone = {
        getModelScreenCenter,
        isModelReadyForActiveType,
        moveModelScreenBy,
        recenter: tryCenter,
        restoreOnLeave,
        clampAfterDrag,
        rewritePositionWrite
    };

    function install() {
        if (!isMmPage() || stopped) return;
        bindWindowHooks();
        // 先把「后端已存位置」的底账拉到内存，之后运行时的改写调用就是同步查表。
        ensureStoredPositions();
        startReadinessWatch();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', install, { once: true });
    } else {
        install();
    }
})();

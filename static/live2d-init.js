/**
 * Live2D Init - 全局导出和自动初始化
 * 功能:
 *  - 导出 Live2DManager 类到全局作用域
 *  - 创建全局 Live2D 管理器实例
 *  - 监听模型加载事件，自动更新全局引用（修复口型同步失效问题）
 */

// 创建全局 Live2D 管理器实例
window.live2dManager = new Live2DManager();

// 监听模型加载事件，自动更新全局引用（修复口型同步失效问题）
window.live2dManager.onModelLoaded = (model) => {
    if (!window.LanLan1) {
        console.warn('[Live2D Init] LanLan1 尚未初始化，跳过全局引用更新');
        return;
    }
    window.LanLan1.live2dModel = model;
    window.LanLan1.currentModel = model;
    window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();
    console.log('[Live2D Init] 全局模型引用已更新');
};

// 兼容性：保持原有的全局变量，但增加 VRM/Live2D 双模态调度逻辑
window.LanLan1 = window.LanLan1 || {};

// ── 自愈基建：解决"重新加载后模型加载不出来、只剩毛线球" ──
// 根因：initLive2DModel 是一次性的——cubism4Model 为空（page_config 取失败 / 资产解析为空）
// 时永久 early-return 不重试；且它 await 的 storageLocation 哨兵 / pageConfigReady 一旦卡死，
// 初始化会永远挂起。下面提供：① 重入保护，② 模型路径缺失时有界重取配置并重试初始化，
// ③ 加载完成后的可见性看门狗（模型已加载却被卡在 minimized/透明，或干脆没加载时兜底自愈）。
let _nekoLive2DInitInFlight = false;
let _nekoLive2DModelLoadedOnce = false;
let _nekoLive2DConfigRetryCount = 0;
// 同一时刻只允许一个重取在排队/进行中：去重多个看门狗 + 空路径分支堆叠的计时器，
// 并在 reloadPageConfig await 窗口里挡住并发 timer 双消耗预算。
let _nekoLive2DRetryPending = false;
const NEKO_LIVE2D_CONFIG_RETRY_MAX = 6;
// 启动等待（storageLocation 哨兵 / pageConfigReady）一旦永不 resolve，会让
// _initLive2DModelInner 永远卡在 await、_nekoLive2DInitInFlight 永远为 true，
// 进而令看门狗重试每次都被在途守卫挡掉、无法自愈（见 PR #1920 review）。
// 用超时兜底把这两个等待变为"有界"：超时即继续，后续空路径分支会触发重取自愈。
const NEKO_LIVE2D_AWAIT_TIMEOUT_MS = 5000;
function _nekoAwaitWithTimeout(thenable, ms) {
    return Promise.race([
        Promise.resolve(thenable).catch(() => {}),
        new Promise((resolve) => { setTimeout(resolve, ms); }),
    ]);
}

// 仅对"本应显示 Live2D"的会话自愈；pngtuber/vrm/mmd 的空 cubism4Model 是正常态。
function _nekoShouldSelfHealLive2D() {
    try {
        if (window.NekoAvatarFloatingBoot && typeof window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot === 'function'
            && window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot()) {
            return false;
        }
        if (window.location && String(window.location.pathname || '').includes('model_manager')) return false;
        const mt = (window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
        const sub = (window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
        if (mt === 'pngtuber' || mt === 'vrm') return false;
        if (mt === 'live3d' && sub === 'mmd') return false;
        if ((window.vrmManager && window.vrmManager.currentModel) || (window.mmdManager && window.mmdManager.currentModel)) return false;
        return true;
    } catch (_) {
        return true;
    }
}

// 模型路径缺失时：有界地重新拉取 page_config 并重试初始化（解决瞬时后端未就绪 / 配置迟到）。
function scheduleLive2DConfigRetry(reason) {
    if (_nekoLive2DModelLoadedOnce) return;
    if (!_nekoShouldSelfHealLive2D()) return;
    // 去重：已有重取在排队/进行中就不再叠加新计时器（修复多个看门狗 + 空路径分支堆叠 timer，
    // 以及在下方 reloadPageConfig await 窗口内被并发 timer 双消耗预算的问题，PR #1920 review）。
    if (_nekoLive2DRetryPending) return;
    if (_nekoLive2DConfigRetryCount >= NEKO_LIVE2D_CONFIG_RETRY_MAX) {
        console.warn('[Live2D Init] 模型路径重试已达上限，停止自愈:', reason);
        return;
    }
    _nekoLive2DRetryPending = true;
    // 退避按"已消耗预算 + 1"估算；但预算只在真正执行重取时扣减（见下方 setTimeout 内），
    // 避免首次正常慢加载期间被空转的看门狗轮次提前耗尽真正的重试次数。
    const delayMs = Math.min(4000, 600 * (_nekoLive2DConfigRetryCount + 1));
    console.log('[Live2D Init] 模型路径缺失，安排配置重取自愈，原因:', reason);
    setTimeout(async () => {
        try {
            if (_nekoLive2DModelLoadedOnce || !_nekoShouldSelfHealLive2D()) return;
            // 正在初始化中（可能是首次正常慢加载）：本轮不消耗预算、不打扰；
            // 若该次初始化最终因空路径失败，会从 early-return 分支再次安排重试。
            if (_nekoLive2DInitInFlight) return;
            if (_nekoLive2DConfigRetryCount >= NEKO_LIVE2D_CONFIG_RETRY_MAX) return;
            // 重取前等存储位置哨兵：reloadPageConfig 直接 fetch、不经 startPageConfigLoad 的等待，
            // 不在此 await 的话，自愈会在存储弹窗仍开着时抢跑、用错/未批准的存储位置（PR #1920 review P1）。
            // 预算也只在哨兵解析、确实要重取时才扣减，避免长时间等待用户决定期间空耗预算。
            if (window.__nekoStorageLocationStartupBarrier && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
                try {
                    await window.__nekoStorageLocationStartupBarrier;
                } catch (_) {
                    // 哨兵被拒绝（存储未批准/取消）：不放行、不重取、不加载，与 startPageConfigLoad 一致。
                    return;
                }
            }
            // 哨兵可能等待很久：解析后重新校验是否仍应自愈（其间可能已切到 vrm/pngtuber/模型管理等）。
            if (_nekoLive2DModelLoadedOnce || _nekoLive2DInitInFlight || !_nekoShouldSelfHealLive2D()) return;
            _nekoLive2DConfigRetryCount += 1;
            try {
                if (typeof window.reloadPageConfig === 'function') {
                    await window.reloadPageConfig();
                }
            } catch (error) {
                console.warn('[Live2D Init] 自愈重取配置失败:', error);
            }
            if (_nekoLive2DModelLoadedOnce || _nekoLive2DInitInFlight) return;
            initLive2DModel();
        } finally {
            // 计时器跑完即解除去重位；若初始化仍失败，会从空路径分支再排下一轮（受预算上限约束）。
            _nekoLive2DRetryPending = false;
        }
    }, delayMs);
}

function _nekoIsLive2DContainerHidden() {
    const container = document.getElementById('live2d-container');
    const canvas = document.getElementById('live2d-canvas');
    if (!container || !canvas) return false;
    if (container.classList.contains('minimized') || container.classList.contains('hidden')) return true;
    try {
        const cs = window.getComputedStyle ? window.getComputedStyle(container) : null;
        if (cs && (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0')) return true;
        const canvasCs = window.getComputedStyle ? window.getComputedStyle(canvas) : null;
        if (canvasCs && (canvasCs.visibility === 'hidden' || canvasCs.opacity === '0')) return true;
    } catch (_) {}
    return false;
}

// 可见性看门狗：加载完仍不可见（yui-guide 残留 / 卡在 minimized），或根本没加载出来 → 兜底自愈。
function ensureLive2DVisibleOnce(reason) {
    try {
        if (window.nekoYuiGuideAvatarCornerPeekActive === true) return;
        if (!_nekoShouldSelfHealLive2D()) return;
        // goodbye / 切换中属于合法隐藏，交给各自链路，不打扰。
        if (window.live2dManager && window.live2dManager._goodbyeClicked) return;
        if (window.appState && (window.appState.isSwitchingCatgirl || window.appState.isSwitchingMode)) return;

        const model = window.live2dManager && (typeof window.live2dManager.getCurrentModel === 'function'
            ? window.live2dManager.getCurrentModel()
            : window.live2dManager.currentModel);
        if (!model || model.destroyed) {
            scheduleLive2DConfigRetry('watchdog-no-model:' + reason);
            return;
        }
        if (_nekoIsLive2DContainerHidden() && typeof window.showLive2d === 'function') {
            console.warn('[Live2D Init] 看门狗检测到模型已加载但不可见，自愈显示，原因:', reason);
            try { window.showLive2d(); } catch (_) {}
        }
    } catch (_) {}
}

function ensureLive2DVisibleSoon(reason) {
    [1500, 4000, 8000].forEach((delayMs) => {
        setTimeout(() => ensureLive2DVisibleOnce(`${reason || 'startup'}:${delayMs}`), delayMs);
    });
}

function revealInitialLive2DModelWhenUiReady(reason) {
    let revealed = false;
    const reveal = () => {
        if (revealed) {
            return true;
        }
        if (typeof window.showLive2d !== 'function') {
            return false;
        }
        try {
            window.showLive2d();
        } catch (error) {
            console.warn('[Live2D Init] showLive2d reveal failed, will retry:', error);
            return false;
        }
        revealed = true;
        return true;
    };

    if (reveal()) {
        return;
    }

    [0, 50, 150, 300, 600, 1000].forEach((delayMs) => {
        setTimeout(() => {
            reveal();
        }, delayMs);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', reveal, { once: true });
    }
    window.addEventListener('load', reveal, { once: true });
    console.log('[Live2D Init] waiting for showLive2d to reveal initial model:', reason || 'initial-load');
}

// 根据 lanlan_config 判断当前活跃的模型类型
function _getActiveModelType() {
    const cfg = window.lanlan_config;
    if (!cfg) return 'live2d';
    const modelType = (cfg.model_type || '').toLowerCase();
    if (modelType === 'live3d') {
        const sub = (cfg.live3d_sub_type || '').toLowerCase();
        return sub === 'mmd' ? 'mmd' : sub === 'vrm' ? 'vrm' : 'live2d';
    }
    if (modelType === 'vrm') return 'vrm';
    if (modelType === 'pngtuber') return 'pngtuber';
    return 'live2d';
}

// 1. 表情控制 (setEmotion / playExpression)
window.LanLan1.setEmotion = function(emotion) {
    const activeType = _getActiveModelType();
    if (activeType === 'mmd') {
        if (window.mmdManager && window.mmdManager.expression) {
            window.mmdManager.expression.setEmotion(emotion);
        }
        return;
    }
    if (activeType === 'vrm') {
        if (window.vrmManager && window.vrmManager.expression) {
            window.vrmManager.expression.setMood(emotion);
        }
        return;
    }
    if (activeType === 'pngtuber') return;
    // Live2D 模式
    if (window.live2dManager && window.live2dManager.currentModel) {
        window.live2dManager.setEmotion(emotion);
    }
};

// 兼容旧接口 playExpression，逻辑同 setEmotion
window.LanLan1.playExpression = window.LanLan1.setEmotion;

// 2. 动作控制 (playMotion)
window.LanLan1.playMotion = function(group, no, priority) {
    const activeType = _getActiveModelType();
    // MMD/VRM 模式下忽略 Live2D 的动作指令
    if (activeType === 'mmd' || activeType === 'vrm' || activeType === 'pngtuber') return;

    // Live2D 模式
    if (window.live2dManager && window.live2dManager.currentModel) {
        window.live2dManager.playMotion(group, no, priority);
    }
};

// 3. 清除表情/特效
window.LanLan1.clearEmotionEffects = function() {
    const activeType = _getActiveModelType();
    if (activeType === 'mmd') {
        if (window.mmdManager && window.mmdManager.expression) window.mmdManager.expression.resetAllMorphs();
        return;
    }
    if (activeType === 'vrm') {
        if (window.vrmManager && window.vrmManager.expression) window.vrmManager.expression.setMood('neutral');
        return;
    }
    if (activeType === 'pngtuber') return;
    if (window.live2dManager) window.live2dManager.clearEmotionEffects();
};

window.LanLan1.clearExpression = function() {
    const activeType = _getActiveModelType();
    if (activeType === 'mmd' || activeType === 'vrm' || activeType === 'pngtuber') return;
    if (window.live2dManager) window.live2dManager.clearExpression();
};

// 4. 嘴型控制
window.LanLan1.setMouth = function(value) {
    const activeType = _getActiveModelType();
    // MMD 嘴型：通过 morph target 控制
    if (activeType === 'mmd') {
        if (window.mmdManager && window.mmdManager.expression) {
            window.mmdManager.expression.setMorphWeight('あ', value);
        }
        return;
    }
    if (activeType === 'pngtuber') return;
    // VRM 的嘴型通常由 Audio 分析自动控制 (vrm-animation.js)，这里主要服务 Live2D
    if (window.live2dManager && window.live2dManager.currentModel) {
        window.live2dManager.setMouth(value);
    }
};

/**
 * 清理 VRM 资源（抽取为独立函数以提高可读性）
 * 处理初始化中的竞态条件、双重释放等问题
 */
async function cleanupVRMResources() {
    if (!window.vrmManager) return;
    
    try {
        // 如果 VRM 正在初始化，等待其完成或通过 dispose() 取消
        // 不要直接设置 _isVRMInitializing = false，避免竞态条件
        let hasDisposed = false;
        if (window._isVRMInitializing) {
            let waitCount = 0;
            const maxWait = 50; // 最多等待 5 秒 (50 * 100ms)
            while (window._isVRMInitializing && waitCount < maxWait) {
                await new Promise(resolve => setTimeout(resolve, 100));
                waitCount++;
            }
            if (window._isVRMInitializing) {
                console.warn('[Live2D Init] VRM 初始化超时，通过 dispose() 取消初始化');
                // 通过 dispose() 取消初始化（确保资源正确清理，由 initVRMModel 的 finally 块设置 _isVRMInitializing = false）
                if (typeof window.vrmManager.dispose === 'function') {
                    try {
                        await window.vrmManager.dispose();
                        hasDisposed = true;
                    } catch (disposeError) {
                        console.warn('[Live2D Init] 调用 dispose() 取消初始化时出错:', disposeError);
                    }
                }
            }
        }
        
        // 使用 dispose() 作为主要清理路径（确保资源正确清理，包括取消正在进行的初始化）；如果已调用过则不再重复
        if (!hasDisposed && typeof window.vrmManager.dispose === 'function') {
            await window.vrmManager.dispose();
            console.log('[Live2D Init] 已清理VRM管理器');
            
            // 只有在确认 dispose() 完成且初始化标志已清除时才清理引用（由 initVRMModel 的 finally 块处理 _isVRMInitializing）
            if (window.vrmManager && !window._isVRMInitializing) {
                if (window.vrmManager.currentModel) {
                    window.vrmManager.currentModel = null;
                }
                if (window.vrmManager.renderer) {
                    window.vrmManager.renderer = null;
                }
                if (window.vrmManager.scene) {
                    window.vrmManager.scene = null;
                }
            }
        } else {
            // 降级方案：如果 dispose 不存在，手动清理（避免双重释放）；只有在确认初始化已完成时才清理
            if (!window._isVRMInitializing) {
                if (window.vrmManager.renderer) {
                    window.vrmManager.renderer.dispose();
                    window.vrmManager.renderer = null;
                    console.log('[Live2D Init] 已清理Three.js渲染器（降级方案）');
                }
                if (window.vrmManager.scene) {
                    window.vrmManager.scene.clear();
                    window.vrmManager.scene = null;
                    console.log('[Live2D Init] 已清理Three.js场景（降级方案）');
                }
                if (window.vrmManager) {
                    window.vrmManager.currentModel = null;
                }
            } else {
                console.warn('[Live2D Init] VRM 正在初始化中，跳过手动清理（等待 dispose 或初始化完成）');
            }
        }
    } catch (cleanupError) {
        console.warn('[Live2D Init] VRM清理时出现警告:', cleanupError);
        // 如果 dispose 抛出错误，尝试降级清理；只有在确认初始化已完成时才清理
        try {
            if (!window._isVRMInitializing) {
                // 只有在初始化已完成时才清理
                if (window.vrmManager && !window.vrmManager.renderer && !window.vrmManager.scene) {
                    // dispose 可能已经部分清理，只清理剩余引用
                    if (window.vrmManager.currentModel) {
                        window.vrmManager.currentModel = null;
                    }
                } else {
                    // dispose 可能完全失败，尝试手动清理
                    if (window.vrmManager?.renderer) {
                        try {
                            window.vrmManager.renderer.dispose();
                        } catch (e) {
                            // 忽略 dispose 错误
                        }
                        window.vrmManager.renderer = null;
                    }
                    if (window.vrmManager?.scene) {
                        try {
                            window.vrmManager.scene.clear();
                        } catch (e) {
                            // 忽略 clear 错误
                        }
                        window.vrmManager.scene = null;
                    }
                    if (window.vrmManager) {
                        window.vrmManager.currentModel = null;
                    }
                }
            } else {
                console.warn('[Live2D Init] VRM 正在初始化中，跳过降级清理（等待初始化完成）');
            }
        } catch (fallbackError) {
            console.error('[Live2D Init] 降级清理也失败:', fallbackError);
            // 不要直接设置 _isVRMInitializing = false，这应该由 initVRMModel 的 finally 块处理
        }
    }
}

// 自动初始化函数（重入保护包装；真正逻辑在 _initLive2DModelInner）
async function initLive2DModel() {
    if (_nekoLive2DInitInFlight) {
        console.log('[Live2D Init] 初始化进行中，跳过本次重入调用');
        return;
    }
    _nekoLive2DInitInFlight = true;
    try {
        await _initLive2DModelInner();
    } finally {
        _nekoLive2DInitInFlight = false;
    }
}

window.initLive2DModel = initLive2DModel;

// 自动初始化函数（延迟执行，等待 cubism4Model 设置）
async function _initLive2DModelInner() {
    const _preInitModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));
    // 存储位置启动哨兵必须始终等待——不设超时、不因已知路径而跳过：它是用户存储/迁移决定的门，
    // 抢跑会让头像/主界面用错或未批准的存储位置（PR #1920 review P1）。已解析则瞬时返回；
    // 若用户迟迟不决定（或哨兵真卡死），保持等待与 startPageConfigLoad 一致，也比抢跑安全。
    // 哨兵被拒绝（存储未批准/取消）不能当成放行：直接中止本次初始化，绝不对未批准存储加载（PR #1920 review）。
    if (window.__nekoStorageLocationStartupBarrier && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
        try {
            await window.__nekoStorageLocationStartupBarrier;
        } catch (_) {
            console.warn('[Live2D Init] 存储位置哨兵被拒绝，中止本次 Live2D 初始化');
            return;
        }
    }

    if (window.NekoAvatarFloatingBoot && typeof window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot === 'function'
        && window.NekoAvatarFloatingBoot.shouldSkipUserModelBoot()) {
        if (typeof window.NekoAvatarFloatingBoot.markUserModelBootSkipped === 'function') {
            window.NekoAvatarFloatingBoot.markUserModelBootSkipped('live2d-init');
        }
        console.log('[Live2D Init] 新手教程启动预测命中，跳过用户 Live2D 模型加载');
        return;
    }

    // 检查是否在 VRM/MMD 模式下，如果是则跳过 Live2D 初始化
    const isVRMMode = window.vrmManager && window.vrmManager.currentModel;
    const isMMDMode = window.mmdManager && window.mmdManager.currentModel;
    if (isVRMMode || isMMDMode) {
        console.log('[Live2D Init] 当前为 VRM/MMD 模式，跳过 Live2D 初始化');
        return;
    }

    // 检查是否在 model_manager 页面且当前选择的是 VRM 模型
    const isModelManagerPage = window.location.pathname.includes('model_manager');
    if (isModelManagerPage) {
        // 兼容 model_manager.html：当前使用的是 <select id="model-type-select"> (live2d/vrm)
        const modelTypeSelect = document.getElementById('model-type-select');
        const activeModelType = modelTypeSelect?.value || localStorage.getItem('modelType');
        if (activeModelType === 'vrm' || activeModelType === 'live3d') {
            console.log('[Live2D Init] 模型管理页面当前选择的是 VRM/Live3D 模型，跳过 Live2D 初始化');
            return;
        }

        // 回退方案：检查选择器状态（防御性编程，处理边界情况）
        // 注意：model_manager 页面实际 ID 分别为 #vrm-model-select 与 #model-select
        const vrmModelSelect = document.getElementById('vrm-model-select');
        const live2dModelSelect = document.getElementById('model-select');
        if (vrmModelSelect && vrmModelSelect.value && (!live2dModelSelect || !live2dModelSelect.value)) {
            console.log('[Live2D Init] 模型管理页面当前选择的是 VRM 模型（通过选择器状态），跳过 Live2D 初始化');
            return;
        }
    }

    // 等待配置加载完成（如果存在）；自愈重入若已拿到模型路径，则不再等待，规避 pageConfigReady 卡死。
    // 有界等待：pageConfigReady 卡死时超时即继续，空路径分支随后会触发重取自愈。
    if (!_preInitModelPath && window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await _nekoAwaitWithTimeout(window.pageConfigReady, NEKO_LIVE2D_AWAIT_TIMEOUT_MS);
    }

    // 获取模型路径
    const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));

    // 如果当前为 Live3D+MMD 模式，跳过 Live2D 初始化
    const modelManagerAvatarType = window.location.pathname.includes('model_manager')
        ? String(window._modelManagerCurrentAvatarType || '').toLowerCase()
        : '';
    if (
        modelManagerAvatarType === 'pngtuber' ||
        (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber' ||
        ((window.lanlan_config?.model_type || '').toLowerCase() === 'live3d' &&
        (window.lanlan_config?.live3d_sub_type || '').toLowerCase() === 'mmd')
    ) {
        console.log('[Live2D Init] 非 Live2D 模式，跳过 Live2D 初始化');
        return;
    }

    if (!targetModelPath && !isModelManagerPage) {
        console.log('未设置模型路径，且不在模型管理页面，跳过Live2D初始化');
        // 一次性 early-return 会导致"重新加载后只剩毛线球、模型永不出现"。这里有界重取配置并重试，
        // 让瞬时后端未就绪 / page_config 迟到 / 取空的情况能自愈。pngtuber/vrm/mmd 由 _nekoShouldSelfHealLive2D 排除。
        scheduleLive2DConfigRetry('empty-model-path');
        return;
    }

    try {
        console.log('开始初始化Live2D模型，路径:', targetModelPath);

        // 在初始化Live2D前，清理VRM相关资源（UI 切换逻辑 - 智能视觉切换）
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) vrmContainer.style.display = 'none';

        // 清理VRM的浮动按钮
        const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
        if (vrmFloatingButtons) {
            vrmFloatingButtons.remove();
            console.log('[Live2D Init] 已清理VRM浮动按钮');
        }

        const vrmReturnBtn = document.getElementById('vrm-return-button-container');
        if (vrmReturnBtn) {
            vrmReturnBtn.remove();
            console.log('[Live2D Init] 已清理VRM回来按钮');
        }

        // 清理VRM管理器和Three.js场景（使用抽取的清理函数）
        await cleanupVRMResources();

        if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber') {
            console.log('[Live2D Init] 当前为 PNGTuber 模式，取消 Live2D 显示与初始化');
            const live2dContainerForPngtuber = document.getElementById('live2d-container');
            if (live2dContainerForPngtuber) {
                live2dContainerForPngtuber.style.display = 'none';
                live2dContainerForPngtuber.classList.add('hidden');
            }
            const live2dCanvasForPngtuber = document.getElementById('live2d-canvas');
            if (live2dCanvasForPngtuber) {
                live2dCanvasForPngtuber.style.visibility = 'hidden';
                live2dCanvasForPngtuber.style.pointerEvents = 'none';
            }
            return;
        }

        // 确保Live2D容器可见
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) live2dContainer.style.display = 'block';
        if ((window.lanlan_config?.model_type || '').toLowerCase() !== 'pngtuber') {
            if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
                window.pngtuberManager.hide();
            }
            if (window.cleanupPNGTuberOverlayUI && typeof window.cleanupPNGTuberOverlayUI === 'function') {
                window.cleanupPNGTuberOverlayUI();
            }
            const pngtuberContainer = document.getElementById('pngtuber-container');
            if (pngtuberContainer) {
                pngtuberContainer.style.display = 'none';
                pngtuberContainer.classList.add('hidden');
            }
        }

        // 初始化 PIXI 应用；再次检查是否在 VRM/MMD 模式下（防止在异步操作期间切换）
        if ((window.vrmManager && window.vrmManager.currentModel) || (window.mmdManager && window.mmdManager.currentModel)) {
            console.log('[Live2D Init] 检测到 VRM/MMD 模式，取消 Live2D 初始化');
            return;
        }

        if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber') {
            console.log('[Live2D Init] 当前已切换到 PNGTuber 模式，跳过 PIXI 初始化');
            if (live2dContainer) {
                live2dContainer.style.display = 'none';
                live2dContainer.classList.add('hidden');
            }
            return;
        }

        // 检查 canvas 元素是否存在
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (!live2dCanvas) {
            console.log('[Live2D Init] 未找到 live2d-canvas 元素，可能当前为 VRM 模式，跳过初始化');
            return;
        }

        await window.live2dManager.ensurePIXIReady('live2d-canvas', 'live2d-container');
        let modelPreferences = null;
        // 如果不在模型管理界面且有模型路径，才继续加载模型
        if (!isModelManagerPage && targetModelPath) {
            console.log('开始初始化Live2D模型，路径:', targetModelPath);

            // 加载用户偏好
            const preferences = await window.live2dManager.loadUserPreferences();
            console.log('加载到的偏好设置数量:', preferences.length);

            // 根据模型路径找到对应的偏好设置（使用多种匹配方式）
            if (preferences && preferences.length > 0) {
                console.log('所有偏好设置的路径:', preferences.map(p => p?.model_path).filter(Boolean));

                // 【优化】预先计算路径相关变量，避免重复计算
                const targetFileName = targetModelPath.split('/').pop() || '';
                const targetPathParts = targetModelPath.split('/').filter(p => p);

                // 首先尝试精确匹配
                modelPreferences = preferences.find(p => p && p.model_path === targetModelPath);

                // 如果精确匹配失败，尝试文件名匹配
                if (!modelPreferences) {
                    console.log('尝试文件名匹配，目标文件名:', targetFileName);
                    modelPreferences = preferences.find(p => {
                        if (!p || !p.model_path) return false;
                        const prefFileName = p.model_path.split('/').pop() || '';
                        if (targetFileName && prefFileName && targetFileName === prefFileName) {
                            console.log('文件名匹配成功:', p.model_path);
                            return true;
                        }
                        return false;
                    });
                }

                // 如果还是没找到，尝试部分匹配（通过模型名称）
                if (!modelPreferences) {
                    const modelName = targetPathParts[targetPathParts.length - 2] || targetPathParts[targetPathParts.length - 1]?.replace('.model3.json', '');
                    console.log('尝试模型名称匹配，模型名称:', modelName);
                    if (modelName) {
                        modelPreferences = preferences.find(p => {
                            if (!p || !p.model_path) return false;
                            
                            // 分割路径（支持 '/' 和 '\\'）
                            const pathSegments = p.model_path.split(/[/\\]/).filter(seg => seg);
                            
                            // 检查是否有任何完整段等于 modelName（精确匹配，不是子字符串）
                            const hasExactSegmentMatch = pathSegments.some(seg => seg === modelName);
                            if (hasExactSegmentMatch) {
                                console.log('模型名称匹配成功（完整段匹配）:', p.model_path);
                                return true;
                            }
                            
                            // 获取最后一个路径段的 basename（去掉扩展名）
                            if (pathSegments.length > 0) {
                                const lastSegment = pathSegments[pathSegments.length - 1];
                                // 去掉常见扩展名（.model3.json, .model.json, .json 等）
                                const basename = lastSegment.replace(/\.(model3\.json|model\.json|json)$/i, '');
                                if (basename === modelName) {
                                    console.log('模型名称匹配成功（basename匹配）:', p.model_path);
                                    return true;
                                }
                            }
                            
                            return false;
                        });
                    }
                }

                // 如果还是没找到，尝试部分路径匹配
                if (!modelPreferences) {
                    console.log('尝试部分路径匹配...');
                    modelPreferences = preferences.find(p => {
                        if (!p || !p.model_path) return false;
                        const prefPathParts = p.model_path.split('/').filter(part => part);
                        
                        // 获取文件名（最后一个路径段）
                        const targetFilename = targetPathParts[targetPathParts.length - 1];
                        const prefFilename = prefPathParts[prefPathParts.length - 1];
                        
                        // 主要条件：文件名必须匹配
                        if (targetFilename && prefFilename && targetFilename === prefFilename) {
                            console.log('部分路径匹配成功（文件名匹配）:', p.model_path);
                            return true;
                        }
                        
                        // 次要条件：如果文件名不匹配，需要更严格的路径匹配
                        const commonParts = targetPathParts.filter(part => prefPathParts.includes(part));
                        
                        // 检查最后两个路径段是否匹配
                        const targetLastTwo = targetPathParts.slice(-2);
                        const prefLastTwo = prefPathParts.slice(-2);
                        const lastTwoMatch = targetLastTwo.length === 2 && prefLastTwo.length === 2 &&
                            targetLastTwo[0] === prefLastTwo[0] && targetLastTwo[1] === prefLastTwo[1];
                        
                        // 如果最后两个路径段匹配，或者共同部分 >= 3，则允许匹配
                        if (lastTwoMatch || commonParts.length >= 3) {
                            console.log('部分路径匹配成功（严格匹配）:', p.model_path, '共同部分:', commonParts);
                            return true;
                        }
                        
                        return false;
                    });
                }

                if (modelPreferences && modelPreferences.parameters) {
                    console.log('找到模型偏好设置，参数数量:', Object.keys(modelPreferences.parameters).length);
                }

                // 检查是否有保存的显示器信息（多屏幕位置恢复）
                if (modelPreferences && modelPreferences.display &&
                    window.electronScreen && window.electronScreen.moveWindowToDisplay) {
                    const savedDisplay = modelPreferences.display;
                    if (Number.isFinite(savedDisplay.screenX) && Number.isFinite(savedDisplay.screenY)) {
                        console.log('恢复窗口到保存的显示器位置:', savedDisplay);
                        try {
                            const result = await window.electronScreen.moveWindowToDisplay(
                                savedDisplay.screenX + 10,  // 在保存的屏幕坐标中心点附近
                                savedDisplay.screenY + 10
                            );
                            if (result && result.success) {
                                console.log('窗口位置恢复成功:', result);
                            } else if (result && result.sameDisplay) {
                                console.log('窗口已在正确的显示器上');
                            } else {
                                console.warn('窗口移动失败:', result);
                            }
                        } catch (error) {
                            console.warn('恢复窗口位置失败:', error);
                        }
                    }
                }
            }
        }

        // 只有在非模型管理界面且有模型路径时才自动加载模型
        if (!isModelManagerPage && targetModelPath) {
            // 加载模型（使用事件驱动方式，在常驻表情应用完成后应用参数）
            await window.live2dManager.loadModel(targetModelPath, {
                preferences: modelPreferences,
                isMobile: typeof window.isMobileWidth === 'function' ? window.isMobileWidth() : (window.innerWidth <= 768),
                // 在常驻表情应用完成后应用参数（事件驱动，替代不可靠的 setTimeout）
                onResidentExpressionApplied: (model) => {
                    if (modelPreferences && modelPreferences.parameters &&
                        model && model.internalModel && model.internalModel.coreModel) {
                        window.live2dManager.applyModelParameters(model, modelPreferences.parameters);
                        console.log('[Live2D Init] 在常驻表情应用后已重新应用用户偏好参数');
                    }
                },
                // 模型完全就绪后恢复待机动作（延迟 500ms 确保模型完全稳定）
                // 触发 restoreLive2DIdleAnimationOnMainPage() 从 characters.json 读取保存的动作
                onModelReady: (model) => {
                    setTimeout(() => {
                        // 防竞态：确保 500ms 后当前存活的模型仍然是触发这个回调的模型
                        if (window.live2dManager && (window.live2dManager.getCurrentModel() !== model || model.destroyed)) {
                            console.log('[Live2D Init] 模型已在 500ms 延迟期间被切换或销毁，跳过待机动作恢复');
                            return;
                        }

                        if (typeof window.restoreLive2DIdleAnimationOnMainPage === 'function') {
                            window.restoreLive2DIdleAnimationOnMainPage();
                        }
                    }, 500);
                }
            });

        // 模型已成功加载：关闭路径缺失自愈，避免后续重试与正常态打架。
        _nekoLive2DModelLoadedOnce = true;

        // 设置全局引用（兼容性）
        window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
        window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
        window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();

        // 设置页面卸载时的自动清理（确保资源正确释放）
        revealInitialLive2DModelWhenUiReady('initial-live2d-load');
        window.live2dManager.setupUnloadCleanup();
        // 加载成功后再用看门狗兜底"加载完仍不可见"（yui-guide 残留 / 卡在 minimized）。
        ensureLive2DVisibleSoon('post-load');

            console.log('✓ Live2D 管理器自动初始化完成');
        } else if (isModelManagerPage) {
            console.log('✓ Live2D 管理器在模型管理界面初始化完成（等待手动加载模型）');
        }
        
    } catch (error) {
        console.error('Live2D 管理器自动初始化失败:', error);
        console.error('错误堆栈:', error.stack);
    }
}

// 自动初始化（如果存在 cubism4Model 变量）；如果 pageConfigReady 存在，等待它完成；否则立即执行
if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
    window.pageConfigReady.then(() => {
        initLive2DModel();
    }).catch(() => {
        // 即使配置加载失败，也尝试初始化（可能使用默认模型）
        initLive2DModel();
    });
} else {
    // 如果没有 pageConfigReady，检查 cubism4Model 是否已设置
    const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));
    if (targetModelPath) {
        initLive2DModel();
    } else {
        // 如果还没有设置，等待一下再检查
        setTimeout(() => {
            initLive2DModel();
        }, 1000);
    }
}

// 启动看门狗：独立于上面的 await 链路运行——即便 initLive2DModel 卡在哨兵 / pageConfigReady，
// 也能在模型迟迟不出现时触发有界配置重取自愈，保证"重新加载后不会只剩毛线球"。
ensureLive2DVisibleSoon('startup');

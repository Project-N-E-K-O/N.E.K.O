/**
 * app-character.js — Character (猫娘) switching module
 *
 * Handles VRM <-> Live2D model hot-switching, resource cleanup,
 * container visibility toggling, and achievement unlocking.
 *
 * Depends on: app-state.js (window.appState / window.appConst)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    // const C = window.appConst;  // available if needed

    // ======================================================================
    // Internal state (not shared — only used within this module)
    // ======================================================================
    // isSwitchingCatgirl lives on S so other modules (e.g. WS reconnect
    // guard in app.js) can read it.

    // ======================================================================
    // Helpers — thin wrappers that delegate to functions still in app.js
    // These will be called via window globals exported by app.js.
    // ======================================================================

    /** Show a status toast (exported by app.js as window.showStatusToast) */
    function showStatusToast(message, duration) {
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, duration);
        }
    }

    /** Stop current recording session */
    function stopRecording() {
        if (typeof window.stopRecording === 'function') {
            window.stopRecording();
        }
    }

    /** Sync floating mic button visual state */
    function syncFloatingMicButtonState(isActive) {
        if (typeof window.syncFloatingMicButtonState === 'function') {
            window.syncFloatingMicButtonState(isActive);
        }
    }

    /** Sync floating screen button visual state */
    function syncFloatingScreenButtonState(isActive) {
        if (typeof window.syncFloatingScreenButtonState === 'function') {
            window.syncFloatingScreenButtonState(isActive);
        }
    }

    /** Clear audio playback queue */
    async function clearAudioQueue() {
        if (typeof window.clearAudioQueue === 'function') {
            await window.clearAudioQueue();
        }
    }

    /** Reconnect WebSocket */
    function connectWebSocket() {
        if (typeof window.connectWebSocket === 'function') {
            window.connectWebSocket();
        }
    }

    /** Show the Live2D container with proper animation */
    function showLive2d() {
        if (typeof window.showLive2d === 'function') {
            window.showLive2d();
        }
    }

    function supportsLocalModelRuntime() {
        return !/^\/chat(?:\/|$)/.test(window.location.pathname || '');
    }

    function emitAssistantSpeechCancel(source) {
        var turnId = S.assistantTurnId || S.assistantSpeechActiveTurnId || null;
        S.assistantTurnId = null;
        S.assistantPendingTurnServerId = null;
        S.assistantTurnAwaitingBubble = false;
        S.assistantSpeechActiveTurnId = null;
        window.dispatchEvent(new CustomEvent('neko-assistant-speech-cancel', {
            detail: {
                turnId: turnId ? String(turnId) : null,
                source: source || 'character_switch',
                timestamp: Date.now()
            }
        }));
    }

    function markMMDCanvasLoadingSession(canvas, loadingSessionId) {
        if (!canvas) return;
        canvas.dataset.mmdLoadingSessionId = String(loadingSessionId);
        canvas.style.display = 'block';
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
    }

    function restoreMMDCanvasForLoadingSession(canvas, loadingSessionId) {
        if (!canvas) return false;
        if (canvas.dataset.mmdLoadingSessionId !== String(loadingSessionId)) {
            return false;
        }
        delete canvas.dataset.mmdLoadingSessionId;
        canvas.style.display = 'block';
        canvas.style.visibility = 'visible';
        canvas.style.pointerEvents = 'auto';
        return true;
    }

    function clearMMDCanvasLoadingSession(canvas) {
        if (!canvas) return;
        delete canvas.dataset.mmdLoadingSessionId;
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
    }

    // ======================================================================
    // handleCatgirlSwitch — main character switching logic
    // ======================================================================

    /**
     * Handle character (猫娘) switching triggered via WebSocket push.
     * Supports VRM and Live2D dual-model hot-switching.
     *
     * @param {string} newCatgirl - Name of the new character
     * @param {string} oldCatgirl - Name of the previous character
     */
    async function handleCatgirlSwitch(newCatgirl, oldCatgirl) {
        console.log('[猫娘切换] ========== 开始切换 ==========');
        console.log('[猫娘切换] 从', oldCatgirl, '切换到', newCatgirl);
        console.log('[猫娘切换] isSwitchingCatgirl:', S.isSwitchingCatgirl);
        let mmdLoadingSessionId = '';
        // 保存旧连接引用，finally 中确保关闭（防止 try 中途 throw 导致永久泄露）
        let _switchOldSocket = null;
        let _switchOldHeartbeat = null;
        // MMD→非 MMD 切换时延后 dispose 旧 mmdManager 用的引用：
        // 清理阶段无条件 dispose 会把旧 MMD 实例销毁，但失败回滚仅重启 Live2D ticker /
        // VRM animation——MMD 没有恢复路径，从 MMD 切出去时任一步失败都留下空白模型区。
        // 暂存到 commit 之后 dispose；rollback 时跳过 dispose + 重显容器/按钮。
        let _mmdDeferredDispose = null;

        if (S.isSwitchingCatgirl) {
            console.log('[猫娘切换] 正在切换中，忽略本次请求');
            return;
        }
        if (!newCatgirl) {
            console.log('[猫娘切换] newCatgirl为空，返回');
            return;
        }
        if (newCatgirl === oldCatgirl) {
            console.log('[猫娘切换] 新旧角色相同，跳过切换');
            return;
        }
        // 已是当前角色：BroadcastChannel 兜底 + WebSocket 后端通知会让同一次切换在两条
        // 通道各到一份。BC 先到的话 handleCatgirlSwitch 跑完 lanlan_name 已是 newCatgirl，
        // 接踵而至的 WS 事件参数还是 (newCatgirl, 上一次的 oldCatgirl)，原本只 newCatgirl
        // !== oldCatgirl 的 dedupe 拦不住，会重新清 chat / 关 socket / 重连 / 重加载模型。
        // app-interpage.js 的 BC handler 已加同样守护作为外层防御，这里在源头再加一道，
        // 让所有调用方（不光 BC/WS handler）自动受益。
        if (window.lanlan_config && window.lanlan_config.lanlan_name === newCatgirl) {
            console.log('[猫娘切换] 已经是当前角色', newCatgirl, '，跳过重复事件（BC + WS 双通道收到同一切换）');
            return;
        }
        // 确认切换到不同角色后，清空上一任的搜歌任务
        window.invalidatePendingMusicSearch();
        S.isSwitchingCatgirl = true;
        _switchOldSocket = S.socket;
        _switchOldHeartbeat = S.heartbeatInterval;
        // 保存切换前的 lanlan_config 三件套用于失败回滚。三个字段都在 fallible await 之前
        // 被乐观写入：lanlan_name 在 line 489（connectWebSocket 之前），model_type / live3d_sub_type
        // 在 line ~234（VRM 模型类型分支判定之前）。任何路径上失败/超时都得整套回滚，否则：
        //   - lanlan_name 不回滚 → 重试同名被入口 dedupe 拦掉
        //   - model_type / live3d_sub_type 不回滚 → 全局类型跟实际仍跑的旧模型不一致，
        //     后续分支走偏（preload 穿透逻辑读这两个字段）
        const previousLanlanConfig = {
            lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
            model_type: (window.lanlan_config && window.lanlan_config.model_type) || '',
            live3d_sub_type: (window.lanlan_config && window.lanlan_config.live3d_sub_type) || '',
        };
        const restorePreviousLanlanConfig = () => {
            if (!window.lanlan_config) return;
            window.lanlan_config.lanlan_name = previousLanlanConfig.lanlan_name;
            window.lanlan_config.model_type = previousLanlanConfig.model_type;
            window.lanlan_config.live3d_sub_type = previousLanlanConfig.live3d_sub_type;
        };
        // 给延迟 UI 回调用的角色归属守护：handleCatgirlSwitch 完成后挂着的 setTimeout
        // (line 964/1187/1357 那些 300ms ensure-visible 回调）触发时如果用户已切到别的角色，
        // 旧回调会污染新角色的 UI（比如调 setupFloatingButtons 重建错类型按钮）。
        //
        // 双重守护：
        //   - lanlan_name === newCatgirl：cover 新 attempt 已经把 lanlan_name 改成它的目标
        //   - S._currentSwitchAttemptId === myAttemptId：cover 新 attempt 已接管 attempt id
        //     但还没跑到 line 612 改 lanlan_name 的窗口（line 203 attempt id 接管 vs line 612
        //     lanlan_name 写入之间的几百行代码 + 长 await）。这段窗口里只查 lanlan_name 会
        //     误判"自己还是当前角色"放行旧 setTimeout 的 UI mutation。
        const isStillActiveSwitchTarget = () =>
            S._currentSwitchAttemptId === myAttemptId
            && !!(window.lanlan_config && window.lanlan_config.lanlan_name === newCatgirl);
        console.log('[猫娘切换] 设置 isSwitchingCatgirl = true');

        // Watchdog: 切换路径上有大量没有 timeout 兜底的 await（rAF 循环、wasm reset、模型加载、
        // fetch /api/characters 等）。任何一处永挂都会让 finally 永不执行、isSwitchingCatgirl
        // 永卡 true，后续 broadcast 触发的切换全在前面 isSwitchingCatgirl 早退分支被吞掉，
        // 且 ws 重连也救不回来——只能刷页面。墙钟兜底强制重置标志，让用户能再次触发切换自愈。
        // 45s 留足真实大模型加载空间（VRM/MMD 偶尔 20-30s），又不至于让用户等到放弃。
        //
        // attempt id 归属：watchdog 触发后用户重试新一轮切换时，老 attempt 的 promise 链
        // 如果苏醒走到 finally，会把新 attempt 的标志也一并清掉、放第三次切换并发进来。
        // 给每次切换一个 id，watchdog 和 finally 都只在 id 匹配时才动 isSwitchingCatgirl，
        // 老 attempt 的副作用就被锁在自己 attempt 内。
        const myAttemptId = (S._switchAttemptCounter = (S._switchAttemptCounter || 0) + 1);
        S._currentSwitchAttemptId = myAttemptId;
        // 显式切换完成 flag：lanlan_name 在 line 612 就赋值，远早于 model load / render
        // stabilization 等长 await。如果 watchdog 用 `lanlan_name === newCatgirl` 判 "已完成"，
        // 长 await 永挂超过 45s 时会误判跳过回滚 + toast，但实际 model/UI 没切完——
        // 用户重试同名被 dedupe 拦死卡半切换。改用显式 flag：line 1546 "已切换到"toast 之前
        // 才 set true（这点说明模型/socket/UI/lanlan_config 都到位、只剩 unlockAchievement
        // 等末尾副作用 await），watchdog 检测这个 flag 精准。
        let switchHasCommitted = false;
        // try 体里 line 486 / 489 / 493 那一段是关键全局副作用区段：S.socket.close()、
        // lanlan_config.lanlan_name = newCatgirl、connectWebSocket()。老 attempt 卡在
        // removeModel/clearAudioQueue 这类无 timeout 的 await 上苏醒后会接着跑这一段，
        // 把新 attempt 刚连的 socket 关掉、把 lanlan_name 覆盖回老目标、用老 lanlan_name
        // 重连——直接把新切换状态打回老角色。在每个关键 await 之后调 throwIfStale 让老
        // attempt 苏醒第一时间被赶进 catch 走 finally，attempt id 校验决定不动门闩。
        const throwIfStale = () => {
            if (S._currentSwitchAttemptId !== myAttemptId) {
                const err = new Error('[猫娘切换] attempt ' + myAttemptId + ' superseded（已被新一轮切换取代或 watchdog 超时），中止后续 mutation');
                err.isStaleSwitchAttempt = true;
                throw err;
            }
        };
        const switchWatchdogId = setTimeout(() => {
            if (S._currentSwitchAttemptId !== myAttemptId) return;
            if (S.isSwitchingCatgirl) {
                // 切换可能已实际完成、只是末尾 unlockAchievement 等无关副作用 await 还在跑：
                // line 1481 toast 已 emit + connectWebSocket 已跑 + model 已加载 + lanlan_config
                // 已写入 newCatgirl。此时 watchdog 无脑 restorePreviousLanlanConfig 会把
                // lanlan_config 回滚成旧、socket 收尾把新 ws 关掉，但实际 model/server 都在新——
                // 严重 desync。检测：lanlan_name 已是 newCatgirl 说明已切完，跳过回滚 + socket
                // 收尾，只清门闩 + 收 overlay。
                // 用显式 commit flag 而非 lanlan_name 判定——后者在 line 612 就赋值，远早于
                // model load 等长 await 完成。switchHasCommitted 在 line ~1546 toast 之前才
                // set true，那时模型/socket/UI 都已到位。
                const switchAlreadyCompleted = switchHasCommitted;
                if (switchAlreadyCompleted) {
                    console.warn('[猫娘切换] watchdog 超时 45s，但切换已实际完成（卡在 unlockAchievement 等末尾副作用 await），跳过回滚仅清门闩');
                } else {
                    console.error('[猫娘切换] watchdog 超时 45s，强制重置 isSwitchingCatgirl；上一次切换很可能挂在某个无 timeout 的 await（如 _waitForModelVisualStability / oggOpusDecoder.reset / model load）');
                    // 整套回滚 lanlan_config（lanlan_name + model_type + live3d_sub_type），否则
                    // 用户重试同名角色被 dedupe 拦掉、且 model_type 跟实际仍跑的旧模型不一致让
                    // 后续分支走偏。
                    restorePreviousLanlanConfig();
                    // socket 收尾：line 529 早期 retire 把 S.socket 置 null，卡死场景下 catch/finally
                    // 永远不到，_switchOldSocket 既不会被关也不会被恢复——orphan 连接到 server，
                    // 同时当前页 S.socket=null 是断联态。复用 catch+finally 的 socket 处理：
                    //   - S.socket=null 且旧 ws 还活着 → 恢复 S.socket = _switchOldSocket（让用户继续旧角色）
                    //   - 新 connectWebSocket 已跑且 S.socket 是新 ws → 直接关旧 _switchOldSocket
                    try {
                        if (_switchOldSocket
                            && _switchOldSocket.readyState !== WebSocket.CLOSED
                            && _switchOldSocket.readyState !== WebSocket.CLOSING) {
                            if (S.socket === null) {
                                S.socket = _switchOldSocket;
                                console.log('[猫娘切换] watchdog 触发，恢复 S.socket 引用到旧连接');
                            } else if (S.socket !== _switchOldSocket) {
                                _switchOldSocket.close();
                            }
                        }
                        if (_switchOldHeartbeat && S.heartbeatInterval !== _switchOldHeartbeat) {
                            clearInterval(_switchOldHeartbeat);
                        }
                    } catch (_e) { /* ignore socket cleanup failures */ }
                }
                // 收掉可能还挂着的 MMD loading overlay：watchdog 触发说明某 await 永挂，
                // catch 永远不进，里面 stale 分支的 MMDLoadingOverlay.fail 永远不执行 →
                // overlay 留着 block UI，用户看到 toast 也没法点。这里主动 fail 它。
                // overlay 收尾不区分"已完成 vs 未完成"——已完成路径下 overlay 应该已经被 end 掉，
                // mmdLoadingSessionId 一般已为空；这里只是 defensive 兜底。
                if (mmdLoadingSessionId) {
                    try {
                        window.MMDLoadingOverlay?.fail(mmdLoadingSessionId, { detail: 'switch watchdog timeout' });
                        clearMMDCanvasLoadingSession(document.getElementById('mmd-canvas'));
                    } catch (_e) { /* ignore overlay failures */ }
                    mmdLoadingSessionId = '';
                }
                S.isSwitchingCatgirl = false;
                S._currentSwitchAttemptId = null;
                if (!switchAlreadyCompleted) {
                    try {
                        showStatusToast((window.t && window.t('app.switchCatgirlWatchdog')) || '上次切换似乎卡住了，请再点一次切换', 5000);
                    } catch (_e) { /* ignore toast failures */ }
                }
                // 已完成路径下不弹"卡住了"toast——line 1481 已 emit 的"已切换到 newCatgirl"是
                // 用户唯一应该看到的状态反馈。
            }
        }, 45000);

        try {
            emitAssistantSpeechCancel('character_switch');
            // VRM applyLighting retry ownership token：无论新角色是 VRM/Live2D/MMD 都先刷新。
            // 之前只在 VRM 分支刷的话，VRM→Live2D/MMD 时旧 VRM retry 仍持有相同 token →
            // 继续 mutate vrmManager.scene 灯光（即便 VRM 已隐藏，scene 状态被污染、下次切回
            // VRM 时残留）。这里无条件刷成 attempt 级 token，旧 retry 比对 !== 立刻自杀。
            // 提到 try 顶部还覆盖了"非 VRM 分支也参与 ownership 流转"的语义。
            const currentSwitchId = Symbol();
            window._currentCatgirlSwitchId = currentSwitchId;

            // 0. 紧急制动：立即停止所有渲染循环
            // 停止 Live2D Ticker
            if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.ticker) {
                window.live2dManager.pixi_app.ticker.stop();
            }

            // 停止 VRM 渲染循环
            if (window.vrmManager && window.vrmManager._animationFrameId) {
                cancelAnimationFrame(window.vrmManager._animationFrameId);
                window.vrmManager._animationFrameId = null;
            }

            // 1. 获取新角色的配置（包括 model_type）
            const charResponse = await fetch('/api/characters');
            throwIfStale();
            if (!charResponse.ok) {
                throw new Error('无法获取角色配置');
            }
            const charactersData = await charResponse.json();
            throwIfStale();
            const catgirlConfig = charactersData['猫娘']?.[newCatgirl];

            if (!catgirlConfig) {
                throw new Error(`未找到角色 ${newCatgirl} 的配置`);
            }

            const modelType = catgirlConfig.model_type || (catgirlConfig.vrm ? 'vrm' : 'live2d');

            // 检测 live3d 子类型：优先使用 live3d_sub_type（后端权威来源）
            const _sanitize = (v) => {
                if (v === undefined || v === null) return '';
                const s = String(v).trim();
                const lower = s.toLowerCase();
                if (!s || lower === 'undefined' || lower === 'null') return '';
                return s;
            };
            // mmdPath/vrmPath 在所有 modelType 路径上统一计算（不只 live3d），让下游 VRM/MMD
            // 加载分支可以直接复用一份已 _sanitize 的路径来源（顶层 + _reserved）：
            // - 规范化的 live3d+vrm/mmd 卡：权威路径在 _reserved.avatar.{vrm,mmd}.model_path
            // - legacy 卡（model_type='vrm'，无 live3d_sub_type）：路径在顶层 catgirlConfig.vrm
            // 之前只在 live3d 分支算 vrmPath，legacy VRM 卡走 VRM 分支时 vrmPath='' 让真实
            // 模型路径被吃掉静默 fallback 默认模型，回归 PR 之前的行为，bug 由 review 抓出。
            const mmdPath = _sanitize(catgirlConfig.mmd)
                || _sanitize(catgirlConfig._reserved?.avatar?.mmd?.model_path)
                || '';
            const vrmPath = _sanitize(catgirlConfig.vrm)
                || _sanitize(catgirlConfig._reserved?.avatar?.vrm?.model_path)
                || '';
            let effectiveModelType = modelType;
            if (modelType === 'live3d') {
                const subType = (
                    catgirlConfig._reserved?.avatar?.live3d_sub_type
                    || catgirlConfig.live3d_sub_type
                    || ''
                ).toString().trim().toLowerCase();

                if (subType === 'vrm') {
                    effectiveModelType = 'vrm';
                } else if (subType === 'mmd') {
                    effectiveModelType = 'mmd';
                } else {
                    // sub_type 缺失时根据路径探测
                    if (mmdPath && !vrmPath) {
                        effectiveModelType = 'mmd';
                    } else if (vrmPath) {
                        effectiveModelType = 'vrm';
                    } else {
                        effectiveModelType = 'live2d';
                    }
                }
                console.log('[猫娘切换] live3d 子类型检测:', effectiveModelType, '(subType:', subType, 'mmd:', !!mmdPath, 'vrm:', !!vrmPath, ')');
            }
            console.log('[猫娘切换] effectiveModelType:', effectiveModelType);

            // ⭐ 立即更新 model_type，让 preload 穿透逻辑使用正确的分支
            if (window.lanlan_config) {
                if (effectiveModelType === 'mmd' || effectiveModelType === 'vrm') {
                    window.lanlan_config.model_type = 'live3d';
                    window.lanlan_config.live3d_sub_type = effectiveModelType;
                } else {
                    window.lanlan_config.model_type = 'live2d';
                    window.lanlan_config.live3d_sub_type = '';
                }
                console.log('[猫娘切换] 已更新 lanlan_config.model_type =', window.lanlan_config.model_type, 'sub_type =', window.lanlan_config.live3d_sub_type);
            }

            // 2. 清理旧模型资源（温和清理，保留基础设施）

            // 清理 VRM 资源（参考 index.html 的清理逻辑）
            try {

                // 隐藏容器
                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'none';
                    vrmContainer.classList.add('hidden');
                }

                // 【关键修复】调用 cleanupUI 来完全清理 VRM UI 资源（包括浮动按钮、锁图标和"请她回来"按钮）
                if (window.vrmManager && typeof window.vrmManager.cleanupUI === 'function') {
                    window.vrmManager.cleanupUI();
                }

                if (window.vrmManager) {
                    // 1. 停止动画循环
                    if (window.vrmManager._animationFrameId) {
                        cancelAnimationFrame(window.vrmManager._animationFrameId);
                        window.vrmManager._animationFrameId = null;
                    }

                    // 2. 停止VRM动画并立即清理状态（用于角色切换）
                    if (window.vrmManager.animation) {
                        // 立即重置动画状态，不等待淡出完成
                        if (typeof window.vrmManager.animation.reset === 'function') {
                            window.vrmManager.animation.reset();
                        } else {
                            window.vrmManager.animation.stopVRMAAnimation();
                        }
                    }

                    // 3. 清理模型（从场景中移除，但不销毁scene）
                    if (window.vrmManager.currentModel && window.vrmManager.currentModel.vrm) {
                        const vrm = window.vrmManager.currentModel.vrm;
                        if (vrm.scene) {
                            vrm.scene.visible = false;
                            if (window.vrmManager.scene) {
                                window.vrmManager.scene.remove(vrm.scene);
                            }
                        }
                    }

                    // 4. 清理动画混合器
                    if (window.vrmManager.animationMixer) {
                        window.vrmManager.animationMixer.stopAllAction();
                        window.vrmManager.animationMixer = null;
                    }

                    // 5. 清理场景中剩余的模型对象（但保留光照、相机和控制器）
                    // 注意：vrm.scene 已经在上面（步骤3）从场景中移除了
                    // 这里只需要清理可能残留的其他模型对象
                    if (window.vrmManager.scene) {
                        const childrenToRemove = [];
                        window.vrmManager.scene.children.forEach((child) => {
                            // 只移除模型相关的对象，保留光照、相机和控制器
                            if (!child.isLight && !child.isCamera) {
                                // 检查是否是VRM模型场景（通过检查是否有 SkinnedMesh）
                                if (child.type === 'Group' || child.type === 'Object3D') {
                                    let hasMesh = false;
                                    child.traverse((obj) => {
                                        if (obj.isSkinnedMesh || obj.isMesh) {
                                            hasMesh = true;
                                        }
                                    });
                                    if (hasMesh) {
                                        childrenToRemove.push(child);
                                    }
                                }
                            }
                        });
                        // 移除模型对象
                        childrenToRemove.forEach(child => {
                            window.vrmManager.scene.remove(child);
                        });
                    }

                    // 6. 隐藏渲染器（但不销毁）
                    if (window.vrmManager.renderer && window.vrmManager.renderer.domElement) {
                        window.vrmManager.renderer.domElement.style.display = 'none';
                    }

                    // 7. 重置模型引用
                    window.vrmManager.currentModel = null;
                    // 不在这里设置 _goodbyeClicked = true，因为这会永久短路 showCurrentModel
                    // 标志会在 finally 块中统一重置，或在加载新模型时清除
                }

            } catch (e) {
                console.warn('[猫娘切换] VRM 清理出错:', e);
            }

            // 清理 Live2D 资源（参考 index.html 的清理逻辑）
            try {

                // 隐藏容器
                const live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'none';
                    live2dContainer.classList.add('hidden');
                }

                // 【关键修复】手动清理 Live2D UI 资源（Live2D没有cleanupUI方法）
                // 只有在切换到非Live2D模型时才清理UI
                if (effectiveModelType !== 'live2d') {
                    // 移除浮动按钮
                    const live2dButtons = document.getElementById('live2d-floating-buttons');
                    if (live2dButtons) {
                        if (window._removeNekoFloatingButtonsElement) {
                            window._removeNekoFloatingButtonsElement(live2dButtons);
                        } else {
                            live2dButtons.remove();
                        }
                    }

                    // 移除"请她回来"按钮
                    const live2dReturnBtn = document.getElementById('live2d-return-button-container');
                    if (live2dReturnBtn) live2dReturnBtn.remove();

                    // 清理所有可能残留的 Live2D 锁图标
                    document.querySelectorAll('#live2d-lock-icon').forEach(el => el.remove());
                }

                if (window.live2dManager) {
                    // 1. 使用 Live2DManager 的统一清理入口。
                    // 只 destroy currentModel 会绕过 removeModel() 中的 ticker 回调、鼠标跟踪、
                    // idle motion 定时器、浮动按钮 ticker 等清理逻辑；这些残留会在 PIXI ticker
                    // 每帧继续运行，角色卡切换几次后表现为持续低 FPS。
                    if (typeof window.live2dManager.removeModel === 'function') {
                        await window.live2dManager.removeModel({ skipCloseWindows: true });
                    } else if (window.live2dManager.currentModel) {
                        // 兼容兜底：旧版本没有 removeModel 时仍尽量销毁模型。
                        if (typeof window.live2dManager.currentModel.destroy === 'function') {
                            window.live2dManager.currentModel.destroy({ children: true });
                        }
                        window.live2dManager.currentModel = null;
                    }

                    // 2. 停止 ticker（但保留 pixi_app，以便 Live2D 分支加载完成后重启）。
                    // removeModel() 为了让空舞台保持可恢复会重启 ticker；切到非 Live2D 时必须再次停掉。
                    if (window.live2dManager.pixi_app && window.live2dManager.pixi_app.ticker) {
                        if (effectiveModelType !== 'live2d') {
                            window.live2dManager.pixi_app.ticker.stop();
                        }
                    }

                    // 3. 清理舞台残留（但不销毁 pixi_app）
                    if (window.live2dManager.pixi_app && window.live2dManager.pixi_app.stage) {
                        window.live2dManager.pixi_app.stage.removeChildren();
                    }
                }

            } catch (e) {
                if (e?.isStaleSwitchAttempt) throw e;
                console.warn('[猫娘切换] Live2D 清理出错:', e);
            }
            // 内层 try 之外做 stale 检查：line 413 await live2dManager.removeModel 是这一段唯一的
            // long await，stale attempt 可能卡在它的 ticker 回调清理里。在 try 外检查，避免
            // throwIfStale 抛错被上面的 catch 吞成 warn 让 stale attempt 继续往下跑。
            throwIfStale();

            // 清理 MMD 资源
            try {
                // 隐藏容器
                const mmdContainer = document.getElementById('mmd-container');
                if (mmdContainer) {
                    mmdContainer.style.display = 'none';
                    mmdContainer.classList.add('hidden');
                }
                const mmdCanvas = document.getElementById('mmd-canvas');
                if (mmdCanvas) {
                    clearMMDCanvasLoadingSession(mmdCanvas);
                }

                // 清理 MMD UI 资源（浮动按钮、锁图标等）
                // MMD→MMD 切换时需要完全销毁旧实例，避免新旧 GPU/物理 world 实例并存；
                // MMD→非 MMD 切换时延后到 commit 之后销毁，让中途失败 rollback 还能让旧
                // MMD 模型重新可见——否则用户看到空白模型区只能再切一次或刷新。
                if (window.mmdManager && typeof window.mmdManager.dispose === 'function') {
                    if (effectiveModelType === 'mmd') {
                        console.log('[猫娘切换] 完全销毁旧 MMD 管理器实例（MMD→MMD）');
                        window.mmdManager.dispose();
                        window.mmdManager = null;
                    } else {
                        _mmdDeferredDispose = window.mmdManager;
                        console.log('[猫娘切换] 延后旧 MMD 实例 dispose 到 commit（MMD→', effectiveModelType, '）');
                    }
                }
                if (effectiveModelType !== 'mmd') {
                    document.querySelectorAll('#mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container')
                        .forEach(el => {
                            if (window._removeNekoFloatingButtonsElement) {
                                window._removeNekoFloatingButtonsElement(el);
                            } else {
                                el.remove();
                            }
                        });
                }
            } catch (e) {
                console.warn('[猫娘切换] MMD 清理出错:', e);
            }

            // 3. 准备新环境
            showStatusToast(window.t ? window.t('app.switchingCatgirl', { name: newCatgirl }) : `正在切换到 ${newCatgirl}...`, 3000);

            // 先退役旧 socket，让它后续的 onmessage / onclose 在清空会话期间直接走 stale guard。
            if (_switchOldSocket && S.socket === _switchOldSocket) {
                S.socket = null;
            }

            // 清空聊天记录和相关全局状态
            const chatContainer = document.getElementById('chatContainer');
            if (chatContainer) {
                chatContainer.innerHTML = '';
            }
            if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearMessages === 'function') {
                window.reactChatWindowHost.clearMessages();
            }
            if (typeof window._resetReactChatSwitchState === 'function') {
                window._resetReactChatSwitchState();
            }
            // 重置聊天相关的全局状态
            window.currentGeminiMessage = null;
            window._geminiTurnFullText = '';
            window.currentTurnGeminiBubbles = [];
            window.currentTurnGeminiAttachments = [];
            // 清空realistic synthesis队列和缓冲区，防止旧角色的语音继续播放
            window._realisticGeminiQueue = [];
            window._realisticGeminiBuffer = '';
            window._pendingMusicCommand = '';
            window._realisticGeminiTimestamp = null;
            window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
            window._isProcessingRealisticQueue = false;
            window.realisticGeminiCurrentTurnId = null;
            // 重置语音模式用户转录合并追踪
            S.lastVoiceUserMessage = null;
            S.lastVoiceUserMessageTime = 0;
            // 丢弃切换前已经入站但尚未消费的旧 TTS 数据
            S.incomingAudioEpoch += 1;
            S.incomingAudioBlobQueue = [];
            S.pendingAudioChunkMetaQueue = [];

            // 清理连接与状态
            if (S.autoReconnectTimeoutId) clearTimeout(S.autoReconnectTimeoutId);
            if (S.isRecording) {
                stopRecording();
                syncFloatingMicButtonState(false);
                syncFloatingScreenButtonState(false);
            }
            //  等待清空音频队列完成，避免竞态条件
            await clearAudioQueue();
            throwIfStale();
            if (S.isTextSessionActive) S.isTextSessionActive = false;

            // 关键全局副作用区段开始：S.socket / lanlan_config / connectWebSocket。
            // 老 attempt 苏醒到这里若已 stale，绝对不能再动这些——会把新 attempt 的
            // socket 关掉、lanlan_name 覆盖回老目标、用老 lanlan_name 重连。
            throwIfStale();
            if (S.socket) S.socket.close();
            if (S.heartbeatInterval) clearInterval(S.heartbeatInterval);

            window.lanlan_config.lanlan_name = newCatgirl;

            await new Promise(resolve => setTimeout(resolve, 100));
            throwIfStale();
            S._pendingGreetingSwitch = true;  // 标记为切换连接，onopen 时发送 greeting_check
            connectWebSocket();
            document.title = `${newCatgirl} Terminal - Project N.E.K.O.`;

            // 4. 根据模型类型加载相应的模型
            console.log('[猫娘切换] 检测到模型类型:', modelType, '有效类型:', effectiveModelType);
            if (!supportsLocalModelRuntime()) {
                console.log('[猫娘切换] 当前页面不加载本地模型，跳过模型热切换');
                // chat.html 不走模型分支，在此显式恢复 composer（兜底 onclose 路径）
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }
            } else if (effectiveModelType === 'vrm') {
                // 加载 VRM 模型（currentSwitchId 在 try 顶部已无条件刷过，VRM 分支直接复用）
                console.log('[猫娘切换] 进入VRM加载分支');

                // VRM 模型路径解析：复用前面已 _sanitize 的 vrmPath（涵盖 _reserved.avatar
                // .vrm.model_path 和顶层 catgirlConfig.vrm 两条来源，无论 modelType=='live3d'
                // 还是 legacy 'vrm' 都会被填充），与 MMD 分支 `mmdModelPath = mmdPath || ...`
                // 对偶。
                // 不再在 VRM 分支自己重新解析顶层 catgirlConfig.vrm——规范化为 live3d+vrm
                // 子类型的角色卡没有顶层 vrm 字段（其权威来源是 _reserved.avatar.vrm.model_path），
                // 原本只看顶层会把它误判成"未配置"，触发 fallback 默认模型 + auto-repair PUT
                // 写 model_type='vrm' + 顶层 vrm 字段把规范化卡反向反规范化、污染后端配置。
                let vrmModelPath = vrmPath || '';

                // 仅 legacy（catgirlConfig.model_type === 'vrm'）格式参与 auto-repair：规范化
                // live3d+vrm 卡走到 fallback 时不应触发 PUT，否则反向反规范化把 model_type
                // 改回 'vrm'、写入顶层 vrm 字段，破坏 PR #510 后采用的 live3d 规范化格式。
                const isLegacyVrmCard = catgirlConfig.model_type === 'vrm';
                const hasVrmField = Object.prototype.hasOwnProperty.call(catgirlConfig, 'vrm');
                const vrmValue = catgirlConfig.vrm;
                // 检查顶层 vrm 字段是否是字符串 "undefined"/"null" 等无效字面值（仅用于决定
                // 是否触发 auto-repair 警告/PUT，不影响 vrmModelPath 解析——后者已由前置
                // _sanitize 处理）
                let isVrmValueInvalid = false;
                if (hasVrmField && vrmValue !== undefined && vrmValue !== null) {
                    const rawStr = typeof vrmValue === 'string' ? vrmValue : String(vrmValue);
                    const trimmed = rawStr.trim();
                    const lowerTrimmed = trimmed.toLowerCase();
                    isVrmValueInvalid = trimmed === ''
                        || lowerTrimmed === 'undefined'
                        || lowerTrimmed === 'null'
                        || lowerTrimmed.includes('undefined')
                        || lowerTrimmed.includes('null');
                }

                // 如果路径仍无效，使用默认模型
                if (!vrmModelPath) {
                    // effectiveModelType === 'vrm' 才进入这一分支：legacy（model_type='vrm'）
                    // 和规范化（live3d+vrm 子类型）两条路径都走默认模型 fallback。
                    // 改用 effectiveModelType 而非 catgirlConfig.model_type：规范化卡 model_type
                    // 是 'live3d'，原条件会让它走错误抛出分支拒载；现在统一 fallback 到默认。
                    vrmModelPath = '/static/vrm/sister1.0.vrm';

                    if (hasVrmField && vrmValue !== undefined && vrmValue !== null && !isVrmValueInvalid) {
                        // 走到这一分支说明顶层 vrm 字段值看起来合法但 _sanitize 没让它进 vrmPath
                        // ——理论上不该发生，留着诊断
                        const vrmValueStr = typeof vrmValue === 'string' ? `"${vrmValue}"` : String(vrmValue);
                        console.warn(`[猫娘切换] VRM 模型路径无效 (${vrmValueStr})，使用默认模型`);
                    } else {
                        console.info('[猫娘切换] VRM 模型路径未配置或无效，使用默认模型');

                        // auto-repair：仅 legacy 卡 + 顶层 vrm 字段是 "undefined" 字面值时触发。
                        // 规范化卡（live3d+vrm 子类型）跳过：PUT 写 model_type='vrm' + 顶层 vrm
                        // 会反向反规范化，污染后端配置；规范化卡的权威来源是 _reserved.avatar.vrm
                        // .model_path，正常路径下根本不该走到 fallback（vrmPath 已经覆盖）。
                        if (isLegacyVrmCard && hasVrmField && isVrmValueInvalid && typeof vrmValue === 'string') {
                            try {
                                // VRM 自动修复 PUT 是对后端配置的持久化写入。如果切换在
                                // PUT/json 两个 await 之间被新 attempt 顶掉，旧 attempt 仍替
                                // 过期目标 newCatgirl 写后端默认 VRM 路径，污染后端配置。
                                // 在每个 await 后立刻 stale check，stale 时让 catch 走 stale
                                // 分支静默退出（catch 已加 isStaleSwitchAttempt rethrow 兜底）。
                                const fixResponse = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(newCatgirl)}`, {
                                    method: 'PUT',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                        model_type: 'vrm',
                                        vrm: vrmModelPath  // 使用默认模型路径
                                    })
                                });
                                throwIfStale();
                                if (fixResponse.ok) {
                                    const fixResult = await fixResponse.json();
                                    throwIfStale();
                                    if (fixResult.success) {
                                        console.log(`[猫娘切换] 已自动修复角色 ${newCatgirl} 的 VRM 模型路径配置（从 "undefined" 修复为默认模型）`);
                                    }
                                }
                            } catch (fixError) {
                                if (fixError?.isStaleSwitchAttempt) throw fixError;
                                console.warn('[猫娘切换] 自动修复配置时出错:', fixError);
                            }
                        }
                    }
                    console.info('[猫娘切换] 使用默认 VRM 模型:', vrmModelPath);
                }

                // 确保 VRM 管理器已初始化
                console.log('[猫娘切换] 检查VRM管理器 - 存在:', !!window.vrmManager, '已初始化:', window.vrmManager?._isInitialized);

                // 等待 VRM 模块加载（双保险：事件 + 轮询）
                // VRMManager 与 VRMCore 由 vrm-init.js 并行加载，加载顺序不确定；
                // 只检查 VRMManager 会在 VRMCore 未就绪时放行，导致 initThreeJS
                // 抛 "VRMCore 尚未加载"。因此就绪条件需同时覆盖两者。
                const isVRMRuntimeReady = () =>
                    typeof window.VRMManager !== 'undefined' &&
                    typeof window.VRMCore !== 'undefined';

                if (!isVRMRuntimeReady()) {
                    await new Promise((resolve, reject) => {
                        // 先检查是否已经就绪（事件可能已经发出）
                        if (isVRMRuntimeReady()) {
                            return resolve();
                        }

                        let resolved = false;
                        const timeoutId = setTimeout(() => {
                            if (!resolved) {
                                resolved = true;
                                reject(new Error('VRM 模块加载超时'));
                            }
                        }, 5000);

                        // 方法1：监听事件
                        const eventHandler = () => {
                            if (!resolved && isVRMRuntimeReady()) {
                                resolved = true;
                                clearTimeout(timeoutId);
                                window.removeEventListener('vrm-modules-ready', eventHandler);
                                resolve();
                            }
                        };
                        window.addEventListener('vrm-modules-ready', eventHandler, { once: true });

                        // 方法2：轮询检查（双保险）
                        const pollInterval = setInterval(() => {
                            if (isVRMRuntimeReady()) {
                                if (!resolved) {
                                    resolved = true;
                                    clearTimeout(timeoutId);
                                    clearInterval(pollInterval);
                                    window.removeEventListener('vrm-modules-ready', eventHandler);
                                    resolve();
                                }
                            }
                        }, 100); // 每100ms检查一次

                        // 清理轮询（在超时或成功时）
                        const originalResolve = resolve;
                        const originalReject = reject;
                        resolve = (...args) => {
                            clearInterval(pollInterval);
                            originalResolve(...args);
                        };
                        reject = (...args) => {
                            clearInterval(pollInterval);
                            originalReject(...args);
                        };
                    });
                }
                // VRMRuntimeReady wait（5s timeout）resolve 后立刻做 stale 检查：stale attempt
                // 苏醒后接下来的 vrmManager 创建 / canvas 创建 / initThreeJS 都会 mutate 共享
                // three.js 状态，必须在调任何 mutation 之前拦下，否则即便 line 723 的 throwIfStale
                // 抛错，污染已经发生了。
                throwIfStale();

                if (!window.vrmManager) {
                    window.vrmManager = new window.VRMManager();
                }
                // 每次都清除 goodbyeClicked 标志，确保新模型可以正常显示
                window.vrmManager._goodbyeClicked = false;

                // 确保容器和 canvas 存在，并初始化 Three.js 场景。
                // 即使 vrmManager 已初始化也要调用 initThreeJS：在已初始化时是幂等的，
                // 但会无条件恢复容器/canvas 可见性——修复在 Live2D/VRM 反复切换后，
                // 容器/canvas 仍保持 display:none 导致 VRM 模型加载不出来的问题。
                {
                    const vrmContainerEl = document.getElementById('vrm-container');
                    if (vrmContainerEl && !vrmContainerEl.querySelector('canvas')) {
                        const canvas = document.createElement('canvas');
                        canvas.id = 'vrm-canvas';
                        vrmContainerEl.appendChild(canvas);
                    }
                }
                const lightingConfig = catgirlConfig.lighting || null;
                await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', lightingConfig);
                throwIfStale();

                // 转换路径为 URL（基本格式处理，vrm-core.js 会处理备用路径）
                // 再次验证 vrmModelPath 的有效性
                if (!vrmModelPath ||
                    vrmModelPath === 'undefined' ||
                    vrmModelPath === 'null' ||
                    (typeof vrmModelPath === 'string' && (vrmModelPath.trim() === '' || vrmModelPath.includes('undefined')))) {
                    console.error('[猫娘切换] vrmModelPath 在路径转换前无效，使用默认模型:', vrmModelPath);
                    vrmModelPath = '/static/vrm/sister1.0.vrm';
                }

                let modelUrl = vrmModelPath;

                // 确保 modelUrl 是有效的字符串
                if (typeof modelUrl !== 'string' || !modelUrl) {
                    console.error('[猫娘切换] modelUrl 不是有效字符串，使用默认模型:', modelUrl);
                    modelUrl = '/static/vrm/sister1.0.vrm';
                }

                // 处理 Windows 路径：提取文件名并转换为 Web 路径
                if (modelUrl.includes('\\') || modelUrl.includes(':')) {
                    const filename = modelUrl.split(/[\\/]/).pop();
                    if (filename && filename !== 'undefined' && filename !== 'null' && !filename.includes('undefined')) {
                        modelUrl = `/user_vrm/${filename}`;
                    } else {
                        console.error('[猫娘切换] Windows 路径提取的文件名无效，使用默认模型:', filename);
                        modelUrl = '/static/vrm/sister1.0.vrm';
                    }
                } else if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
                    // 相对路径，添加 /user_vrm/ 前缀
                    // 再次验证 modelUrl 的有效性
                    if (modelUrl !== 'undefined' && modelUrl !== 'null' && !modelUrl.includes('undefined')) {
                        modelUrl = `/user_vrm/${modelUrl}`;
                    } else {
                        console.error('[猫娘切换] 相对路径无效，使用默认模型:', modelUrl);
                        modelUrl = '/static/vrm/sister1.0.vrm';
                    }
                } else {
                    // 确保路径格式正确（统一使用正斜杠）
                    modelUrl = modelUrl.replace(/\\/g, '/');
                }

                // 最终验证：确保 modelUrl 不包含 "undefined" 或 "null"
                if (typeof modelUrl !== 'string' ||
                    modelUrl.includes('undefined') ||
                    modelUrl.includes('null') ||
                    modelUrl.trim() === '') {
                    console.error('[猫娘切换] 路径转换后仍包含无效值，使用默认模型:', modelUrl);
                    modelUrl = '/static/vrm/sister1.0.vrm';
                }

                // 加载 VRM 模型（vrm-core.js 内部已实现备用路径机制，会自动尝试 /user_vrm/ 和 /static/vrm/）
                console.log('[猫娘切换] 开始加载VRM模型:', modelUrl);
                await window.vrmManager.loadModel(modelUrl);
                throwIfStale();
                console.log('[猫娘切换] VRM模型加载完成');

                // 【关键修复】确保VRM渲染循环已启动（loadModel内部会调用startAnimation，但为了保险再次确认）
                if (!window.vrmManager._animationFrameId) {
                    console.log('[猫娘切换] VRM渲染循环未启动，手动启动');
                    if (typeof window.vrmManager.startAnimation === 'function') {
                        window.vrmManager.startAnimation();
                    }
                } else {
                    console.log('[猫娘切换] VRM渲染循环已启动，ID:', window.vrmManager._animationFrameId);
                }

                // 应用角色的光照配置（currentSwitchId 在 VRM 分支顶部已刷新）
                if (catgirlConfig.lighting && window.vrmManager) {
                    const lighting = catgirlConfig.lighting;

                    // 确保光照已初始化，如果没有则等待（添加最大重试次数和切换取消条件）
                    let applyLightingRetryCount = 0;
                    const MAX_RETRY_COUNT = 50; // 最多重试50次（5秒）
                    let applyLightingTimerId = null;

                    const applyLighting = () => {
                        // 检查是否切换已被取消（新的切换已开始）
                        if (window._currentCatgirlSwitchId !== currentSwitchId) {
                            if (applyLightingTimerId) {
                                clearTimeout(applyLightingTimerId);
                                applyLightingTimerId = null;
                            }
                            return;
                        }

                        if (window.vrmManager?.ambientLight && window.vrmManager?.mainLight &&
                            window.vrmManager?.fillLight && window.vrmManager?.rimLight) {
                            // 引用全局唯一默认值（定义于 vrm-core.js）
                            const defaultLighting = window.VRM_DEFAULT_LIGHTING || {
                                ambient: 0.83, main: 1.91, fill: 0.0,
                                rim: 0.0, top: 0.0, bottom: 0.0
                            };

                            if (window.vrmManager.ambientLight) {
                                window.vrmManager.ambientLight.intensity = lighting.ambient ?? defaultLighting.ambient;
                            }
                            if (window.vrmManager.mainLight) {
                                window.vrmManager.mainLight.intensity = lighting.main ?? defaultLighting.main;
                            }
                            if (window.vrmManager.fillLight) {
                                window.vrmManager.fillLight.intensity = lighting.fill ?? defaultLighting.fill;
                            }
                            if (window.vrmManager.rimLight) {
                                window.vrmManager.rimLight.intensity = lighting.rim ?? defaultLighting.rim;
                            }
                            if (window.vrmManager.topLight) {
                                window.vrmManager.topLight.intensity = lighting.top ?? defaultLighting.top;
                            }
                            if (window.vrmManager.bottomLight) {
                                window.vrmManager.bottomLight.intensity = lighting.bottom ?? defaultLighting.bottom;
                            }

                            // 应用描边粗细设置
                            if (lighting.outlineWidthScale !== undefined && typeof applyVRMOutlineWidth === 'function') {
                                applyVRMOutlineWidth(lighting.outlineWidthScale, window.vrmManager);
                            }

                            // 强制渲染一次，确保光照立即生效
                            if (window.vrmManager.renderer && window.vrmManager.scene && window.vrmManager.camera) {
                                window.vrmManager.renderer.render(window.vrmManager.scene, window.vrmManager.camera);
                            }

                            // 成功应用，清理定时器
                            if (applyLightingTimerId) {
                                clearTimeout(applyLightingTimerId);
                                applyLightingTimerId = null;
                            }
                        } else {
                            // 光照未初始化，延迟重试（但限制重试次数）
                            applyLightingRetryCount++;
                            if (applyLightingRetryCount < MAX_RETRY_COUNT) {
                                applyLightingTimerId = setTimeout(applyLighting, 100);
                            } else {
                                console.warn('[猫娘切换] 光照应用失败：已达到最大重试次数');
                                if (applyLightingTimerId) {
                                    clearTimeout(applyLightingTimerId);
                                    applyLightingTimerId = null;
                                }
                            }
                        }
                    };

                    applyLighting();
                }

                if (window.LanLan1) {
                    window.LanLan1.live2dModel = null;
                    window.LanLan1.currentModel = null;
                }

                // 显示 VRM 容器

                const vrmContainer = document.getElementById('vrm-container');
                const live2dContainer = document.getElementById('live2d-container');

                console.log('[猫娘切换] 显示VRM容器 - vrmContainer存在:', !!vrmContainer, 'live2dContainer存在:', !!live2dContainer);

                if (vrmContainer) {
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.style.display = 'block';
                    vrmContainer.style.visibility = 'visible';
                    vrmContainer.style.pointerEvents = 'auto';
                    console.log('[猫娘切换] VRM容器已设置为可见');

                    // 检查容器的实际状态
                    const computedStyle = window.getComputedStyle(vrmContainer);
                    console.log('[猫娘切换] VRM容器状态 - display:', computedStyle.display, 'visibility:', computedStyle.visibility, 'opacity:', computedStyle.opacity, 'zIndex:', computedStyle.zIndex);
                    console.log('[猫娘切换] VRM容器子元素数量:', vrmContainer.children.length);
                }

                if (live2dContainer) {
                    live2dContainer.style.display = 'none';
                    live2dContainer.classList.add('hidden');
                }

                // 隐藏 MMD 容器
                const mmdContainerVrm = document.getElementById('mmd-container');
                if (mmdContainerVrm) {
                    mmdContainerVrm.style.display = 'none';
                    mmdContainerVrm.classList.add('hidden');
                }
                const mmdCanvasVrm = document.getElementById('mmd-canvas');
                if (mmdCanvasVrm) {
                    clearMMDCanvasLoadingSession(mmdCanvasVrm);
                }

                // 确保 VRM 渲染器可见
                if (window.vrmManager && window.vrmManager.renderer && window.vrmManager.renderer.domElement) {
                    window.vrmManager.renderer.domElement.style.display = 'block';
                    window.vrmManager.renderer.domElement.style.visibility = 'visible';
                    window.vrmManager.renderer.domElement.style.opacity = '1';
                    console.log('[猫娘切换] VRM渲染器已设置为可见');

                    // 恢复 VRM canvas 的指针事件
                    const vrmCanvasEl = document.getElementById('vrm-canvas');
                    if (vrmCanvasEl) {
                        vrmCanvasEl.style.pointerEvents = 'auto';
                    }

                    // 检查canvas的实际状态
                    const canvas = window.vrmManager.renderer.domElement;
                    const computedStyle = window.getComputedStyle(canvas);
                    console.log('[猫娘切换] VRM Canvas状态 - display:', computedStyle.display, 'visibility:', computedStyle.visibility, 'opacity:', computedStyle.opacity, 'zIndex:', computedStyle.zIndex);
                } else {
                    console.warn('[猫娘切换] VRM渲染器不存在或未初始化');
                }

                const chatContainerVrm = document.getElementById('chat-container');
                const textInputArea = document.getElementById('text-input-area');
                console.log('[猫娘切换] VRM - 恢复对话框 - chatContainer存在:', !!chatContainerVrm, '当前类:', chatContainerVrm ? chatContainerVrm.className : 'N/A');
                if (chatContainerVrm) chatContainerVrm.classList.remove('minimized');
                if (textInputArea) textInputArea.classList.remove('hidden');
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }
                console.log('[猫娘切换] VRM - 对话框已恢复，当前类:', chatContainerVrm ? chatContainerVrm.className : 'N/A');

                // 确保 VRM 按钮和锁图标可见
                setTimeout(() => {
                    // fire-and-forget 延迟回调：用户在 300ms 内切到别角色时旧回调会重建错类型按钮
                    if (!isStillActiveSwitchTarget()) return;
                    const vrmButtons = document.getElementById('vrm-floating-buttons');
                    console.log('[猫娘切换] VRM按钮检查 - 存在:', !!vrmButtons);
                    if (vrmButtons) {
                        vrmButtons.style.removeProperty('display');
                        vrmButtons.style.removeProperty('visibility');
                        vrmButtons.style.removeProperty('opacity');
                        console.log('[猫娘切换] VRM按钮已设置为可见');
                    } else {
                        console.warn('[猫娘切换] VRM浮动按钮不存在，尝试重新创建');
                        if (window.vrmManager && typeof window.vrmManager.setupFloatingButtons === 'function') {
                            window.vrmManager.setupFloatingButtons();
                            const newVrmButtons = document.getElementById('vrm-floating-buttons');
                            console.log('[猫娘切换] 重新创建后VRM按钮存在:', !!newVrmButtons);
                        }
                    }

                    // 【关键】显示 VRM 锁图标
                    const vrmLockIcon = document.getElementById('vrm-lock-icon');
                    if (vrmLockIcon) {
                        vrmLockIcon.style.removeProperty('display');
                        vrmLockIcon.style.removeProperty('visibility');
                        vrmLockIcon.style.removeProperty('opacity');
                    }
                }, 300);

            } else if (effectiveModelType === 'mmd') {
                // 加载 MMD 模型
                console.log('[猫娘切换] 进入MMD加载分支');

                // 获取 MMD 模型路径（复用前面检测阶段已净化的 mmdPath）
                let mmdModelPath = mmdPath
                    || catgirlConfig.mmd
                    || catgirlConfig._reserved?.avatar?.mmd?.model_path
                    || '';

                if (!mmdModelPath || mmdModelPath === 'undefined' || mmdModelPath === 'null') {
                    mmdModelPath = '/static/mmd/Miku/Miku.pmx';
                    console.warn('[猫娘切换] MMD 模型路径未配置或无效，使用默认模型:', mmdModelPath);
                } else {
                    console.log('[猫娘切换] MMD 模型路径:', mmdModelPath);
                }

                // 处理路径格式
                let mmdModelUrl = mmdModelPath;
                if (mmdModelUrl.startsWith('http://') || mmdModelUrl.startsWith('https://')) {
                    // 保留 HTTP(S) URL 不做修改
                } else if (/^[A-Za-z]:[\\/]/.test(mmdModelUrl) || mmdModelUrl.includes('\\')) {
                    // Windows 绝对路径——取文件名映射到 /user_mmd/
                    const filename = mmdModelUrl.split(/[\\/]/).pop();
                    if (filename) {
                        mmdModelUrl = `/user_mmd/${filename}`;
                    }
                } else if (!mmdModelUrl.startsWith('/')) {
                    mmdModelUrl = `/user_mmd/${mmdModelUrl}`;
                } else {
                    mmdModelUrl = mmdModelUrl.replace(/\\/g, '/');
                }

                // 隐藏 Live2D 容器
                const live2dContainerMmd = document.getElementById('live2d-container');
                if (live2dContainerMmd) {
                    live2dContainerMmd.style.display = 'none';
                    live2dContainerMmd.classList.add('hidden');
                }

                // 隐藏 VRM 容器
                const vrmContainerMmd = document.getElementById('vrm-container');
                if (vrmContainerMmd) {
                    vrmContainerMmd.style.display = 'none';
                    vrmContainerMmd.classList.add('hidden');
                }
                const vrmCanvasMmd = document.getElementById('vrm-canvas');
                if (vrmCanvasMmd) {
                    vrmCanvasMmd.style.visibility = 'hidden';
                    vrmCanvasMmd.style.pointerEvents = 'none';
                }
                // 【修复】清除 VRM canvas 缓存帧，防止在模型切换窗口期透穿显示旧 VRM 模型
                if (window.vrmManager && window.vrmManager.renderer) {
                    try { window.vrmManager.renderer.clear(); } catch (_) { /* ignore */ }
                }

                // 显示 MMD 容器
                const mmdContainerShow = document.getElementById('mmd-container');
                if (mmdContainerShow) {
                    mmdContainerShow.classList.remove('hidden');
                    mmdContainerShow.style.display = 'block';
                    mmdContainerShow.style.visibility = 'visible';
                    mmdContainerShow.style.removeProperty('pointer-events');
                }
                const mmdCanvasShow = document.getElementById('mmd-canvas');
                mmdLoadingSessionId = window._createMMDLoadingSessionId
                    ? window._createMMDLoadingSessionId('mmd-character')
                    : `mmd-character-${Date.now()}`;
                if (mmdCanvasShow) {
                    // 先隐藏 canvas，避免旧帧或新模型首帧在半透明 loading overlay 后面透出。
                    markMMDCanvasLoadingSession(mmdCanvasShow, mmdLoadingSessionId);
                }
                window.MMDLoadingOverlay?.begin(mmdLoadingSessionId, { stage: 'engine' });

                // 初始化 MMD 管理器
                // 【优化】如果 MMD 管理器已存在且场景有效，复用现有 renderer/scene，
                // 仅清理旧模型并加载新模型，避免 dispose+重建导致的画布透明窗口期。
                console.log('[猫娘切换] 初始化/复用 MMD 管理器');
                if (window.mmdManager && window.mmdManager.scene && window.mmdManager.renderer && !window.mmdManager._isDisposed) {
                    // 复用现有场景：清理旧模型（core._clearModel），保留 renderer/scene/camera
                    if (window.mmdManager.core) {
                        try { window.mmdManager.core._clearModel(); } catch (e) { console.warn('[猫娘切换] _clearModel 失败:', e); }
                    }
                    // 重置动画状态
                    if (window.mmdManager.animationModule) {
                        try { window.mmdManager.animationModule.dispose(); } catch (_) {}
                        // 重新创建动画模块
                        if (typeof MMDAnimation !== 'undefined') {
                            window.mmdManager.animationModule = new MMDAnimation(window.mmdManager);
                        }
                    }
                    console.log('[猫娘切换] MMD 管理器已复用');
                } else {
                    // 首次初始化或管理器已销毁，执行完整初始化
                    let initializedManager = null;
                    if (typeof window.initMMDModel === 'function') {
                        initializedManager = await window.initMMDModel();
                    } else if (typeof initMMDModel === 'function') {
                        initializedManager = await initMMDModel();
                    }
                    throwIfStale();
                    if (!initializedManager || !window.mmdManager || window.mmdManager._isDisposed) {
                        console.error('[猫娘切换] MMD 管理器初始化失败');
                        window.MMDLoadingOverlay?.fail(mmdLoadingSessionId, {
                            detail: (window.t && window.t('mmd.managerInitFailed')) || 'MMD 管理器初始化失败'
                        });
                        mmdLoadingSessionId = '';
                        // 改 throw 而不 return：原本静默 return 会绕过外层 catch 的失败 toast +
                        // lanlan_config 回滚，配合本 PR 入口加的 dedupe (lanlan_name === newCatgirl)
                        // 会让用户重试同名角色被拦死、且 lanlan_config 残留半切换状态。
                        throw new Error('MMD 管理器初始化失败');
                    }
                }

                // 加载 MMD 模型
                if (window.mmdManager) {
                    // 重置 goodbyeClicked 标志
                    window.mmdManager._goodbyeClicked = false;
                    // 提前获取设置并预置物理开关
                    let savedSettings = null;
                    try {
                        window.MMDLoadingOverlay?.update(mmdLoadingSessionId, { stage: 'settings' });
                        const settingsRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(newCatgirl) + '/mmd_settings');
                        throwIfStale();
                        const settingsData = await settingsRes.json();
                        throwIfStale();
                        if (settingsData.success && settingsData.settings) {
                            savedSettings = settingsData.settings;
                            if (savedSettings.physics?.enabled != null) {
                                // mutation 之前再一次 stale 检查：上面两道 throwIfStale 已能拦下
                                // stale 苏醒在 fetch / json await 之后的场景，但 catch 已加 stale
                                // rethrow 兜底，这道是局部防御性双保险——将来 try 里多加一个 await
                                // 时不至于漏。
                                throwIfStale();
                                window.mmdManager.enablePhysics = !!savedSettings.physics.enabled;
                            }
                        }
                    } catch (e) {
                        if (e?.isStaleSwitchAttempt) throw e;
                        /* ignore - will use current enablePhysics */
                    }
                    // settings 段的 catch 故意 swallow 用户态错误，但要把 stale rethrow 出去；
                    // 同时再做一次显式 stale 检查兜底，避免 stale attempt 在 settings fetch
                    // 里苏醒后接着调 mmdManager.loadModel 跟 B 并发跑共享的 loadModel。
                    throwIfStale();
                    window.MMDLoadingOverlay?.update(mmdLoadingSessionId, { stage: 'model' });
                    await window.mmdManager.loadModel(mmdModelUrl, { loadingSessionId: mmdLoadingSessionId });
                    throwIfStale();
                    console.log('[猫娘切换] MMD 模型加载完成');

                    // 应用完整设置（光照、渲染、物理、鼠标跟踪）
                    if (savedSettings) {
                        window.mmdManager.applySettings(savedSettings);
                    }

                    // 播放待机动作（使用已获取的 catgirlConfig，无需重复请求）
                    const mmdIdleAnimation = catgirlConfig?.mmd_idle_animation;
                    if (mmdIdleAnimation) {
                        try {
                            window.MMDLoadingOverlay?.update(mmdLoadingSessionId, { stage: 'idle' });
                            await window.mmdManager.loadAnimation(mmdIdleAnimation);
                            // playAnimation 之前补 stale 检查：A 卡在 loadAnimation 上苏醒后会
                            // 直接对 B 接管的 mmdManager 播放 A 的待机动作，串台。catch 已 rethrow
                            // stale 不会被吞。
                            throwIfStale();
                            window.mmdManager.playAnimation();
                            console.log('[猫娘切换] 已播放待机动作:', mmdIdleAnimation);
                        } catch (idleErr) {
                            if (idleErr?.isStaleSwitchAttempt) throw idleErr;
                            console.warn('[猫娘切换] 播放待机动作失败:', idleErr);
                        }
                        throwIfStale();
                    }
                    window.MMDLoadingOverlay?.update(mmdLoadingSessionId, { stage: 'done' });
                    if (window._waitForMMDRenderFrame) {
                        await window._waitForMMDRenderFrame(window.mmdManager);
                        throwIfStale();
                    }
                    window.MMDLoadingOverlay?.end(mmdLoadingSessionId);
                    const mmdCanvasReady = document.getElementById('mmd-canvas');
                    restoreMMDCanvasForLoadingSession(mmdCanvasReady, mmdLoadingSessionId);
                    mmdLoadingSessionId = '';
                } else {
                    console.error('[猫娘切换] MMD 管理器初始化失败');
                    window.MMDLoadingOverlay?.fail(mmdLoadingSessionId, {
                        detail: (window.t && window.t('mmd.managerInitFailed')) || 'MMD 管理器初始化失败'
                    });
                    mmdLoadingSessionId = '';
                }

                if (window.LanLan1) {
                    window.LanLan1.live2dModel = null;
                    window.LanLan1.currentModel = null;
                }

                const chatContainerMmd = document.getElementById('chat-container');
                const textInputAreaMmd = document.getElementById('text-input-area');
                if (chatContainerMmd) chatContainerMmd.classList.remove('minimized');
                if (textInputAreaMmd) textInputAreaMmd.classList.remove('hidden');
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }

                // 延时显示 MMD 浮动按钮和锁图标
                setTimeout(() => {
                    if (!isStillActiveSwitchTarget()) return;
                    const mmdButtons = document.getElementById('mmd-floating-buttons');
                    if (mmdButtons) {
                        mmdButtons.style.removeProperty('display');
                        mmdButtons.style.removeProperty('visibility');
                        mmdButtons.style.removeProperty('opacity');
                    } else if (window.mmdManager && typeof window.mmdManager.setupFloatingButtons === 'function') {
                        window.mmdManager.setupFloatingButtons();
                    }

                    const mmdLockIcon = document.getElementById('mmd-lock-icon');
                    if (mmdLockIcon) {
                        mmdLockIcon.style.removeProperty('display');
                        mmdLockIcon.style.removeProperty('visibility');
                        mmdLockIcon.style.removeProperty('opacity');
                    }
                }, 300);

            } else {
                // 加载 Live2D 模型

                // 重置goodbyeClicked标志（包括 VRM 的，避免快速切换时遗留）
                if (window.live2dManager) {
                    window.live2dManager._goodbyeClicked = false;
                }
                if (window.vrmManager) {
                    window.vrmManager._goodbyeClicked = false;
                }

                const modelResponse = await fetch(`/api/characters/current_live2d_model?catgirl_name=${encodeURIComponent(newCatgirl)}`);
                throwIfStale();
                if (!modelResponse.ok) {
                    // 原本失败也走成功路径：configRes.ok 失败时跳进 fallback yui-origin，
                    // 但 modelResponse 本身失败时 modelData.success/model_info 缺失，下面
                    // `if (modelData.success && modelData.model_info)` 直接跳过整个加载块，
                    // 用户看到的是空白容器但弹"已切换到 xxx"——配合本 PR 入口 dedupe，
                    // 重试同名被拦死。明确抛错让外层 catch 走完整失败路径。
                    throw new Error(`无法获取 Live2D 模型配置 (HTTP ${modelResponse.status})`);
                }
                const modelData = await modelResponse.json();
                throwIfStale();
                if (!modelData.success || !modelData.model_info?.path) {
                    throw new Error(modelData.error || 'Live2D 模型配置无效（缺 success 或 model_info.path）');
                }

                // 确保 Manager 存在
                if (!window.live2dManager && typeof window.Live2DManager === 'function') {
                    window.live2dManager = new window.Live2DManager();
                }

                if (!window.live2dManager) {
                    console.error('[猫娘切换] Live2DManager 不可用，无法加载模型');
                    throw new Error('Live2DManager unavailable');
                }

                // 初始化或重用 PIXI
                if (!window.live2dManager.pixi_app || !window.live2dManager.pixi_app.renderer) {
                    await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
                    throwIfStale();
                }

                // 加载新模型
                if (modelData.success && modelData.model_info) {
                    const modelConfigRes = await fetch(modelData.model_info.path);
                    throwIfStale();
                    if (modelConfigRes.ok) {
                        const modelConfig = await modelConfigRes.json();
                        throwIfStale();
                        modelConfig.url = modelData.model_info.path;

                        const preferences = await window.live2dManager.loadUserPreferences();
                        throwIfStale();
                        const modelPreferences = preferences ? preferences.find(p => p.model_path === modelConfig.url) : null;

                        await window.live2dManager.loadModel(modelConfig, {
                            preferences: modelPreferences,
                            isMobile: window.innerWidth <= 768
                        });
                        throwIfStale();

                        if (window.LanLan1) {
                            window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                            window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                        }

                        // 确保所有 VRM 锁图标已完全移除（loadModel 内部会调用 setupHTMLLockIcon）
                        // 清理所有可能残留的 VRM 锁图标
                        document.querySelectorAll('#vrm-lock-icon, #vrm-lock-icon-hidden').forEach(el => el.remove());

                        // 【关键修复】确保 PIXI ticker 在模型加载完成后立即启动
                        if (window.live2dManager?.pixi_app?.ticker) {
                            try {
                                if (!window.live2dManager.pixi_app.ticker.started) {
                                    window.live2dManager.pixi_app.ticker.start();
                                    console.log('[猫娘切换] Live2D ticker 已启动');
                                }
                                // 强制触发一次更新以确保模型正常渲染
                                const currentModel = window.live2dManager.getCurrentModel();
                                if (currentModel && currentModel.internalModel && currentModel.internalModel.coreModel) {
                                    window.live2dManager.pixi_app.ticker.update();
                                }
                            } catch (tickerError) {
                                console.error('[猫娘切换] Ticker 启动失败:', tickerError);
                            }
                        }
                    } else {
                        // 模型配置获取失败（可能因 CFA/反勒索防护导致路径不可用），回退到默认模型
                        console.warn(`[猫娘切换] 模型配置获取失败 (HTTP ${modelConfigRes.status}: ${modelData.model_info.path}), 回退到默认模型 yui-origin`);
                        try {
                            const defaultPath = '/static/yui-origin/yui-origin.model3.json';
                            const defaultRes = await fetch(defaultPath);
                            throwIfStale();
                            if (defaultRes.ok) {
                                const defaultConfig = await defaultRes.json();
                                throwIfStale();
                                defaultConfig.url = defaultPath;
                                await window.live2dManager.loadModel(defaultConfig, {
                                    isMobile: window.innerWidth <= 768
                                });
                                throwIfStale();
                                if (window.LanLan1) {
                                    window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                                    window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                                }
                                // 确保 ticker 启动
                                if (window.live2dManager?.pixi_app?.ticker && !window.live2dManager.pixi_app.ticker.started) {
                                    window.live2dManager.pixi_app.ticker.start();
                                }
                                console.log('[猫娘切换] 已回退加载默认模型 yui-origin');
                            } else {
                                // throw 而非只 log：原本静默继续会让 showLive2d() + "已切换到 xxx"
                                // toast 都跑，但实际模型没载起来。配合 dedupe 让用户看空白点不动。
                                throw new Error(`默认 Live2D 模型加载失败 (HTTP ${defaultRes.status})`);
                            }
                        } catch (fallbackErr) {
                            if (fallbackErr?.isStaleSwitchAttempt) throw fallbackErr;
                            console.error('[猫娘切换] 默认模型加载失败:', fallbackErr);
                            // rethrow 让外层 catch 走完整失败路径（toast + lanlan_config 回滚）。
                            // 不 rethrow 的话同问题：UI 当成功但模型没载起来。
                            throw fallbackErr;
                        }
                        // 内层 try 之外补 stale 检查（fallback try 内部多个 await 含 loadModel，
                        // stale 抛错会被上面 catch 吞成 console.error 让 stale attempt 继续）
                        throwIfStale();
                    }
                }

                // 显示 Live2D 容器

                showLive2d();
                // Fallback if showLive2d is not available
                if (typeof window.showLive2d !== 'function') {
                    const l2dContainer = document.getElementById('live2d-container');
                    if (l2dContainer) {
                        l2dContainer.classList.remove('minimized');
                        l2dContainer.classList.remove('hidden');
                        l2dContainer.style.display = 'block';
                        l2dContainer.style.visibility = 'visible';
                    }
                }

                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'none';
                    vrmContainer.classList.add('hidden');
                }

                // 隐藏 MMD 容器
                const mmdContainerL2d = document.getElementById('mmd-container');
                if (mmdContainerL2d) {
                    mmdContainerL2d.style.display = 'none';
                    mmdContainerL2d.classList.add('hidden');
                }
                const mmdCanvasL2d = document.getElementById('mmd-canvas');
                if (mmdCanvasL2d) {
                    clearMMDCanvasLoadingSession(mmdCanvasL2d);
                }

                const chatContainerL2d = document.getElementById('chat-container');
                const textInputAreaL2d = document.getElementById('text-input-area');
                if (chatContainerL2d) chatContainerL2d.classList.remove('minimized');
                if (textInputAreaL2d) textInputAreaL2d.classList.remove('hidden');
                if (typeof window.syncVoiceChatComposerHidden === 'function') {
                    window.syncVoiceChatComposerHidden(false);
                }

                // 延时重启 Ticker 和显示按钮（双重保险）
                setTimeout(() => {
                    if (!isStillActiveSwitchTarget()) return;

                    window.dispatchEvent(new Event('resize'));

                    // 确保 PIXI ticker 正确启动（双重保险）
                    if (window.live2dManager?.pixi_app?.ticker) {
                        // 强制启动 ticker（即使已经启动也重新启动以确保正常）
                        try {
                            if (!window.live2dManager.pixi_app.ticker.started) {
                                window.live2dManager.pixi_app.ticker.start();
                                console.log('[猫娘切换] Live2D ticker 延迟启动（双重保险）');
                            }
                            // 确保模型更新循环正在运行
                            const currentModel = window.live2dManager.getCurrentModel();
                            if (currentModel && currentModel.internalModel && currentModel.internalModel.coreModel) {
                                // 强制触发一次更新以确保模型正常渲染
                                if (window.live2dManager.pixi_app.ticker) {
                                    window.live2dManager.pixi_app.ticker.update();
                                }
                            } else {
                                console.warn('[猫娘切换] Live2D 模型未完全加载，ticker 可能无法正常工作');
                            }
                        } catch (tickerError) {
                            console.error('[猫娘切换] Ticker 启动失败:', tickerError);
                        }
                    } else {
                        console.warn('[猫娘切换] Live2D pixi_app 或 ticker 不存在');
                    }

                    const l2dCanvas = document.getElementById('live2d-canvas');
                    if (l2dCanvas) l2dCanvas.style.pointerEvents = 'auto';

                    const l2dButtons = document.getElementById('live2d-floating-buttons');
                    if (l2dButtons) {
                        l2dButtons.style.setProperty('display', 'flex', 'important');
                        l2dButtons.style.visibility = 'visible';
                        l2dButtons.style.opacity = '1';
                    }

                    // 【关键】显示 Live2D 锁图标（loadModel 内部已调用 setupHTMLLockIcon）
                    const live2dLockIcon = document.getElementById('live2d-lock-icon');
                    if (live2dLockIcon) {
                        //  使用 setProperty 移除之前的 !important 样式，确保能够正常显示
                        live2dLockIcon.style.removeProperty('display');
                        live2dLockIcon.style.removeProperty('visibility');
                        live2dLockIcon.style.setProperty('display', 'block', 'important');
                        live2dLockIcon.style.setProperty('visibility', 'visible', 'important');
                        live2dLockIcon.style.setProperty('opacity', '1', 'important');
                    } else {
                        // 如果锁图标不存在，尝试重新创建
                        // 这可能发生在快速切换模型类型时，锁图标创建被阻止的情况
                        const currentModel = window.live2dManager?.getCurrentModel();
                        if (currentModel && window.live2dManager?.setupHTMLLockIcon) {
                            console.log('[锁图标] 锁图标不存在，尝试重新创建');
                            window.live2dManager.setupHTMLLockIcon(currentModel);
                            // 再次尝试显示
                            const newLockIcon = document.getElementById('live2d-lock-icon');
                            if (newLockIcon) {
                                newLockIcon.style.removeProperty('display');
                                newLockIcon.style.removeProperty('visibility');
                                newLockIcon.style.setProperty('display', 'block', 'important');
                                newLockIcon.style.setProperty('visibility', 'visible', 'important');
                                newLockIcon.style.setProperty('opacity', '1', 'important');
                            }
                        }
                    }
                }, 300);
            }

            // 切换完成 commit 点：模型加载完、socket 连上、UI 收尾完成、lanlan_config 已是
            // newCatgirl，剩下的只是 unlockAchievement 等无关副作用 await。从这里开始 watchdog
            // 触发应识别为"已完成"跳过回滚（避免破坏成功状态）。
            switchHasCommitted = true;
            // commit 之后再 dispose 延后保留的旧 MMD 实例（MMD→非 MMD 路径）。
            // commit 前 dispose 会让中途失败的 rollback 没法恢复旧 MMD（容器没渲染 + 实例已销毁）。
            if (_mmdDeferredDispose) {
                try {
                    if (typeof _mmdDeferredDispose.dispose === 'function') {
                        _mmdDeferredDispose.dispose();
                    }
                } catch (disposeErr) {
                    console.warn('[猫娘切换] 延后 dispose 旧 MMD 出错:', disposeErr);
                }
                if (window.mmdManager === _mmdDeferredDispose) {
                    window.mmdManager = null;
                }
                _mmdDeferredDispose = null;
            }
            showStatusToast(window.t ? window.t('app.switchedCatgirl', { name: newCatgirl }) : `已切换到 ${newCatgirl}`, 3000);

            // 【成就】解锁换肤成就
            // 注：内层 try/catch 会吞 throwIfStale 的抛错，需要先做 stale 检查再进 try
            throwIfStale();
            if (window.unlockAchievement) {
                try {
                    await window.unlockAchievement('ACH_CHANGE_SKIN');
                } catch (err) {
                    if (err?.isStaleSwitchAttempt) throw err;
                    console.error('解锁换肤成就失败:', err);
                }
                // unlockAchievement 是 try 体内最后一个 await。watchdog 在它期间触发会清门闩
                // + 回滚 lanlan_config，attempt 苏醒后如果不 stale check 会"成功"落到 finally：
                // line 1481 已经 emit 的"已切换"toast 留下来 + watchdog 后续 emit 的"卡住了"
                // toast 矛盾，且状态半截（lanlan_config 被回滚但 ws/model 已切）。补一道让
                // catch 走 stale 分支静默退场（watchdog 已弹的 toast 是用户唯一看到的）。
                throwIfStale();
            }

        } catch (error) {
            // stale attempt 主动退出：老 attempt 苏醒发现自己已被新一轮取代/watchdog 超时，
            // 静默退场。不弹 toast（不是用户当前操作的失败）、不回滚（新 attempt 持有 socket）。
            // 但 mmdLoadingSessionId 是 stale attempt 自己持有的 overlay session token，
            // 如果 stale 在 MMD 分支已 begin 了 overlay 后才被取代、且新 attempt 是 non-MMD，
            // 新 attempt 不会 end/fail 这个 session → 老 overlay 一直挂着 block UI。
            // 必须由 stale 自己 fail 它持有的 session 才能让 overlay 退出。
            //
            // stale 判定：除了 throwIfStale 抛的带 isStaleSwitchAttempt 标记的 error 外，
            // 还要看 attempt ownership——某些路径上 await 真实 reject（如 network error）发生
            // 在 watchdog 超时/新 attempt 接管之后，error 没 stale 标记但 attempt 已 stale，
            // 走 else 分支会弹"切换失败"toast 给已被取代的 attempt，干扰新 attempt 的 UI。
            const isStaleAttempt = !!error?.isStaleSwitchAttempt
                || S._currentSwitchAttemptId !== myAttemptId;
            if (isStaleAttempt) {
                console.log('[猫娘切换] attempt', myAttemptId, '已 stale，主动退出（不影响新一轮切换状态）；error:', error?.message || error);
                if (mmdLoadingSessionId) {
                    // 跟 watchdog 分支一样用 try/catch 包：overlay.fail 自己抛错会让后续
                    // toast / lanlan_config 回滚 / S.socket 恢复全跳过，用户卡半切换状态。
                    try {
                        window.MMDLoadingOverlay?.fail(mmdLoadingSessionId, { detail: 'switch superseded' });
                    } catch (overlayErr) { console.warn('[猫娘切换] MMDLoadingOverlay.fail 报错:', overlayErr); }
                }
            } else {
                console.error('[猫娘切换] 失败:', error);
                if (mmdLoadingSessionId) {
                    try {
                        window.MMDLoadingOverlay?.fail(mmdLoadingSessionId, { detail: error?.message || String(error) });
                    } catch (overlayErr) { console.warn('[猫娘切换] MMDLoadingOverlay.fail 报错:', overlayErr); }
                }
                const errorMessage = error?.message || String(error);
                showStatusToast((window.t && window.t('app.switchCatgirlError', { error: errorMessage })) || `切换失败: ${errorMessage}`, 4000);
                // 失败时整套回滚 lanlan_config（lanlan_name + model_type + live3d_sub_type）：
                // 三个字段都在 fallible await 之前被乐观写入，不回滚的话——name 让重试被入口
                // dedupe 拦死、type 让全局类型跟实际旧模型不一致让后续分支走偏。
                // attempt id 守护：只在自己仍是 currentAttempt 时回滚（与 isStaleAttempt 计算
                // 等价，但显式列出避免与上面 stale 分支耦合），避免 stale attempt 苏醒走 else
                // 分支后用自己看到的 snapshot 覆盖新 attempt 的成功状态。
                if (S._currentSwitchAttemptId === myAttemptId) {
                    restorePreviousLanlanConfig();
                    console.log('[猫娘切换] 切换失败，已恢复切换前的 lanlan_config（', previousLanlanConfig.lanlan_name, '/', previousLanlanConfig.model_type, '/', previousLanlanConfig.live3d_sub_type, '），允许用户重试同一目标');
                    // 恢复 S.socket 引用：line 530 早期 retire 把 S.socket 设成 null（让 stale
                    // onmessage/onclose 在清空会话期间走 stale guard）。如果新 connectWebSocket
                    // 还没替换 S.socket（即 S.socket 仍是 null），下面的 ticker/vrm rollback
                    // 检查 S.socket === _switchOldSocket 永远 false → rollback 进不去，且 finally
                    // 会把 _switchOldSocket 关掉 → "切换失败 + 旧角色掉线"。如果新 attempt 已建
                    // 新 socket（S.socket 是别的对象），则它已接管，不动。
                    if (_switchOldSocket && S.socket === null
                        && _switchOldSocket.readyState !== WebSocket.CLOSED
                        && _switchOldSocket.readyState !== WebSocket.CLOSING) {
                        S.socket = _switchOldSocket;
                        console.log('[猫娘切换] 切换失败，恢复 S.socket 引用到旧连接，避免角色掉线');
                    }
                }
            }

            // 早期失败回滚：若新 socket 未接管（如 /api/characters fetch 抛错），
            // try 开头的"紧急制动"已停掉 L2D ticker 和 VRM 动画，此处重启回旧模型的渲染循环，
            // 否则用户看到的是切换失败 toast + 冻结画面。
            // stale attempt 必须排除：watchdog 触发后 B 已启动但还没跑到 line 486 关 socket 时,
            // S.socket 仍是 _switchOldSocket，A stale 苏醒进 catch 会满足条件错误地启动旧模型，
            // 跟 B 后续清理/加载并发打架。用 isStaleAttempt 而非 error.isStaleSwitchAttempt：
            // 同样覆盖 attempt ownership 检测出的隐式 stale（无标记的真实 reject）。
            // 注：上面 restorePreviousLanlanConfig 那段也会把 S.socket 从 null 恢复成
            // _switchOldSocket，所以这里的 === _switchOldSocket 检查能命中。
            if (!isStaleAttempt && _switchOldSocket && S.socket === _switchOldSocket) {
                try {
                    const ticker = window.live2dManager?.pixi_app?.ticker;
                    if (ticker && !ticker.started) ticker.start();
                } catch (_e) { /* ignore */ }
                try {
                    if (window.vrmManager
                        && !window.vrmManager._animationFrameId
                        && typeof window.vrmManager.startAnimation === 'function') {
                        window.vrmManager.startAnimation();
                    }
                } catch (_e) { /* ignore */ }
                // MMD rollback 恢复：清理阶段为防 MMD→非 MMD 切换中途失败让模型区空白，
                // 把旧 mmdManager.dispose 延后到了 commit 之后。这里 commit 没到，旧实例
                // 还活着；恢复 mmd-container + canvas 可见性 + 浮动按钮，让用户看到旧模型
                // 而不是空白。注意 canvas 这里要显式恢复 visibility/display/pointerEvents
                // 而非用 clearMMDCanvasLoadingSession——后者只清 dataset 但反过来把 canvas
                // 设成 visibility:hidden + pointerEvents:none（line 117-119），会立刻把刚
                // 恢复的容器再藏回去，rollback 等于没做。
                if (_mmdDeferredDispose && window.mmdManager === _mmdDeferredDispose) {
                    try {
                        const mmdContainer = document.getElementById('mmd-container');
                        if (mmdContainer) {
                            mmdContainer.style.removeProperty('display');
                            mmdContainer.classList.remove('hidden');
                        }
                        const mmdCanvas = document.getElementById('mmd-canvas');
                        if (mmdCanvas) {
                            delete mmdCanvas.dataset.mmdLoadingSessionId;
                            mmdCanvas.style.display = 'block';
                            mmdCanvas.style.visibility = 'visible';
                            mmdCanvas.style.pointerEvents = 'auto';
                        }
                        if (typeof _mmdDeferredDispose.setupFloatingButtons === 'function') {
                            _mmdDeferredDispose.setupFloatingButtons();
                        }
                    } catch (recoveryErr) {
                        console.warn('[猫娘切换] MMD rollback 恢复出错:', recoveryErr);
                    }
                    // 旧实例继续作为 active mmdManager；不再 commit 时 dispose。
                    _mmdDeferredDispose = null;
                }
            }
        } finally {
            clearTimeout(switchWatchdogId);
            // attempt id 归属：watchdog 触发后老 attempt 苏醒走到这里时不能动新 attempt 的标志。
            // 注意 socket / 模型清理仍要做（否则老 attempt 持有的旧资源会泄漏），只有
            // isSwitchingCatgirl / _currentSwitchAttemptId 这两个全局门闩需要 attempt 隔离。
            const isCurrentAttempt = (S._currentSwitchAttemptId === myAttemptId);

            // 双重保障：若新 socket 已接管，确保旧连接被关闭（即使 try 中途 throw）。
            // 真正的 stale-onclose guard 在 app-websocket.js 的 onclose 早退
            // (S.socket !== _thisSocket)，本层是第二道防线。
            // 门闸 S.socket !== _switchOldSocket：切换早期失败（如 /api/characters fetch 抛错）时
            // 新 socket 尚未建立，此时保留旧 socket 让用户继续与当前猫娘对话。
            try {
                if (_switchOldSocket
                    && S.socket !== _switchOldSocket
                    && _switchOldSocket.readyState !== WebSocket.CLOSED
                    && _switchOldSocket.readyState !== WebSocket.CLOSING) {
                    _switchOldSocket.close();
                }
                if (_switchOldHeartbeat && S.heartbeatInterval !== _switchOldHeartbeat) {
                    clearInterval(_switchOldHeartbeat);
                }
            } catch (_e) { /* ignore */ }

            // 兜底 dispose 延后保留的旧 MMD 实例：commit 成功 / rollback 恢复 / 抢占退出
            // 三条路径都已把 _mmdDeferredDispose 置 null。这里只剩残余 case：catch 进了
            // stale 分支或 guard 没匹配（如 S.socket !== _switchOldSocket）——orphan 引用
            // 既不是 active mmdManager 也不会被 commit 路径清理，dispose 释放 GPU 资源。
            if (_mmdDeferredDispose && window.mmdManager !== _mmdDeferredDispose) {
                try {
                    if (typeof _mmdDeferredDispose.dispose === 'function') {
                        _mmdDeferredDispose.dispose();
                    }
                } catch (_e) { /* ignore */ }
                _mmdDeferredDispose = null;
            }

            if (isCurrentAttempt) {
                S.isSwitchingCatgirl = false;
                S._currentSwitchAttemptId = null;
                // window._currentCatgirlSwitchId 故意不清——它是 VRM applyLighting retry loop
                // 的 ownership token（line 794-800 用 Symbol() 标识 attempt，retry 比对
                // !== currentSwitchId 自我退出）。retry 是 setTimeout 链，可能跨 finally 还在跑；
                // 慢设备上首轮检查可能晚于 finally → null !== Symbol 判 true → retry 自杀，
                // 灯光偶发不生效。留给下次切换 line 796 的新 Symbol() 自然覆盖；老 retry 比对
                // 新 Symbol 仍然 !==，自然退出。stale attempt 苏醒走 else 分支也不动这个 token。
            } else {
                console.warn('[猫娘切换] attempt', myAttemptId, '苏醒走到 finally，但当前 attempt 已经是', S._currentSwitchAttemptId, '——保留新一轮的 isSwitchingCatgirl 不动');
            }

            // 重置 goodbyeClicked 标志，确保 showCurrentModel 可以正常运行
            if (window.live2dManager) {
                window.live2dManager._goodbyeClicked = false;
            }
            if (window.vrmManager) {
                window.vrmManager._goodbyeClicked = false;
            }
            if (window.mmdManager) {
                window.mmdManager._goodbyeClicked = false;
            }
        }
    }

    // ======================================================================
    // Public API
    // ======================================================================
    mod.handleCatgirlSwitch = handleCatgirlSwitch;

    // Backward-compatible window global so app.js call-sites work unchanged
    window.handleCatgirlSwitch = handleCatgirlSwitch;

    window.appCharacter = mod;

    // ======================================================================
    // Cloudsave force-terminate: clear chat UI after character reloaded
    // ======================================================================
    window.addEventListener('neko-cloudsave-character-reloaded', function (event) {
        var character_name = (event.detail || {}).character_name || '';

        var chatContainer = document.getElementById('chatContainer');
        if (chatContainer) {
            chatContainer.innerHTML = '';
        }
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.clearMessages === 'function') {
            window.reactChatWindowHost.clearMessages();
        }
        if (typeof window._resetReactChatSwitchState === 'function') {
            window._resetReactChatSwitchState();
        }

        window.currentGeminiMessage = null;
        window._geminiTurnFullText = '';
        window.currentTurnGeminiBubbles = [];
        window.currentTurnGeminiAttachments = [];
        window._realisticGeminiQueue = [];
        window._realisticGeminiBuffer = '';
        window._pendingMusicCommand = '';
        window._realisticGeminiTimestamp = null;
        window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
        window._isProcessingRealisticQueue = false;
        window.realisticGeminiCurrentTurnId = null;

        console.info('[cloudsave] 角色 ' + character_name + ' 数据已从云端重新加载，聊天 UI 已刷新');
    });
})();

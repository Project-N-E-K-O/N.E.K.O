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
        // 确认切换到不同角色后，清空上一任的搜歌任务
        window.invalidatePendingMusicSearch();
        S.isSwitchingCatgirl = true;
        console.log('[猫娘切换] 设置 isSwitchingCatgirl = true');

        try {
            emitAssistantSpeechCancel('character_switch');
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
            if (!charResponse.ok) {
                throw new Error('无法获取角色配置');
            }
            const charactersData = await charResponse.json();
            const catgirlConfig = charactersData['猫娘']?.[newCatgirl];

            if (!catgirlConfig) {
                throw new Error(`未找到角色 ${newCatgirl} 的配置`);
            }

            const modelType = catgirlConfig.model_type || (catgirlConfig.vrm ? 'vrm' : 'live2d');

            // 检测 live3d 子类型（优先检查 MMD，与后端 _get_live3d_sub_type 保持一致）
            const _sanitize = v => (typeof v === 'string' && v.trim() && v !== 'undefined' && v !== 'null') ? v : '';
            let mmdPath = '';
            let vrmPath = '';
            let effectiveModelType = modelType;
            if (modelType === 'live3d') {
                mmdPath = _sanitize(catgirlConfig.mmd)
                    || _sanitize(catgirlConfig._reserved?.avatar?.mmd?.model_path)
                    || '';
                vrmPath = _sanitize(catgirlConfig.vrm)
                    || _sanitize(catgirlConfig._reserved?.avatar?.vrm?.model_path)
                    || '';
                if (mmdPath) {
                    effectiveModelType = 'mmd';
                } else if (vrmPath) {
                    effectiveModelType = 'vrm';
                } else {
                    effectiveModelType = 'live2d'; // fallback
                }
                console.log('[猫娘切换] live3d 子类型检测:', effectiveModelType, '(mmd:', !!mmdPath, 'vrm:', !!vrmPath, ')');
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
                    if (live2dButtons) live2dButtons.remove();

                    // 移除"请她回来"按钮
                    const live2dReturnBtn = document.getElementById('live2d-return-button-container');
                    if (live2dReturnBtn) live2dReturnBtn.remove();

                    // 清理所有可能残留的 Live2D 锁图标
                    document.querySelectorAll('#live2d-lock-icon').forEach(el => el.remove());
                }

                if (window.live2dManager) {
                    // 1. 清理模型
                    if (window.live2dManager.currentModel) {
                        if (typeof window.live2dManager.currentModel.destroy === 'function') {
                            window.live2dManager.currentModel.destroy();
                        }
                        window.live2dManager.currentModel = null;
                    }

                    // 2. 停止ticker（但保留 pixi_app，以便后续重启）
                    if (window.live2dManager.pixi_app && window.live2dManager.pixi_app.ticker) {
                        // 只有在切换到非 Live2D 模型时才停止 ticker
                        // 如果切换到 Live2D，ticker 会在加载新模型后重启
                        if (effectiveModelType !== 'live2d') {
                            window.live2dManager.pixi_app.ticker.stop();
                        }
                    }

                    // 3. 清理舞台（但不销毁pixi_app）
                    if (window.live2dManager.pixi_app && window.live2dManager.pixi_app.stage) {
                        window.live2dManager.pixi_app.stage.removeChildren();
                    }
                }

            } catch (e) {
                console.warn('[猫娘切换] Live2D 清理出错:', e);
            }

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
                    mmdCanvas.style.visibility = 'hidden';
                    mmdCanvas.style.pointerEvents = 'none';
                }

                // 清理 MMD UI 资源（浮动按钮、锁图标等）
                // MMD→MMD 切换时需要完全销毁旧实例，避免状态残留导致的问题
                if (window.mmdManager && typeof window.mmdManager.dispose === 'function') {
                    console.log('[猫娘切换] 完全销毁旧 MMD 管理器实例');
                    window.mmdManager.dispose();
                    window.mmdManager = null;
                }
                if (effectiveModelType !== 'mmd') {
                    document.querySelectorAll('#mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container')
                        .forEach(el => el.remove());
                }
            } catch (e) {
                console.warn('[猫娘切换] MMD 清理出错:', e);
            }

            // 3. 准备新环境
            showStatusToast(window.t ? window.t('app.switchingCatgirl', { name: newCatgirl }) : `正在切换到 ${newCatgirl}...`, 3000);

            // 清空聊天记录和相关全局状态
            const chatContainer = document.getElementById('chatContainer');
            if (chatContainer) {
                chatContainer.innerHTML = '';
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
            // 重置语音模式用户转录合并追踪
            S.lastVoiceUserMessage = null;
            S.lastVoiceUserMessageTime = 0;

            // 清理连接与状态
            if (S.autoReconnectTimeoutId) clearTimeout(S.autoReconnectTimeoutId);
            if (S.isRecording) {
                stopRecording();
                syncFloatingMicButtonState(false);
                syncFloatingScreenButtonState(false);
            }
            //  等待清空音频队列完成，避免竞态条件
            await clearAudioQueue();
            if (S.isTextSessionActive) S.isTextSessionActive = false;

            if (S.socket) S.socket.close();
            if (S.heartbeatInterval) clearInterval(S.heartbeatInterval);

            window.lanlan_config.lanlan_name = newCatgirl;

            await new Promise(resolve => setTimeout(resolve, 100));
            S._pendingGreetingSwitch = true;  // 标记为切换连接，onopen 时发送 greeting_check
            connectWebSocket();
            document.title = `${newCatgirl} Terminal - Project N.E.K.O.`;

            // 4. 根据模型类型加载相应的模型
            console.log('[猫娘切换] 检测到模型类型:', modelType, '有效类型:', effectiveModelType);
            if (effectiveModelType === 'vrm') {
                // 加载 VRM 模型
                console.log('[猫娘切换] 进入VRM加载分支');

                // 安全获取 VRM 模型路径，处理各种边界情况
                let vrmModelPath = null;
                // 检查 vrm 字段是否存在且有效
                const hasVrmField = catgirlConfig.hasOwnProperty('vrm');
                const vrmValue = catgirlConfig.vrm;

                // 检查 vrmValue 是否是有效的值（排除字符串 "undefined" 和 "null"）
                let isVrmValueInvalid = false;
                if (hasVrmField && vrmValue !== undefined && vrmValue !== null) {
                    const rawValue = vrmValue;
                    if (typeof rawValue === 'string') {
                        const trimmed = rawValue.trim();
                        const lowerTrimmed = trimmed.toLowerCase();
                        // 检查是否是无效的字符串值（包括 "undefined", "null" 等）
                        isVrmValueInvalid = trimmed === '' ||
                            lowerTrimmed === 'undefined' ||
                            lowerTrimmed === 'null' ||
                            lowerTrimmed.includes('undefined') ||
                            lowerTrimmed.includes('null');
                        if (!isVrmValueInvalid) {
                            vrmModelPath = trimmed;
                        }
                    } else {
                        // 非字符串类型，转换为字符串后也要验证
                        const strValue = String(rawValue);
                        const lowerStr = strValue.toLowerCase();
                        isVrmValueInvalid = lowerStr === 'undefined' || lowerStr === 'null' || lowerStr.includes('undefined');
                        if (!isVrmValueInvalid) {
                            vrmModelPath = strValue;
                        }
                    }
                }

                // 如果路径无效，使用默认模型或抛出错误
                if (!vrmModelPath) {
                    // 如果配置中明确指定了 model_type 为 'vrm'，静默使用默认模型
                    if (catgirlConfig.model_type === 'vrm') {
                        vrmModelPath = '/static/vrm/sister1.0.vrm';

                        // 如果 vrmValue 是字符串 "undefined" 或 "null"，视为"未配置"，不显示警告
                        // 只有在 vrm 字段存在且值不是字符串 "undefined"/"null" 时才显示警告
                        if (hasVrmField && vrmValue !== undefined && vrmValue !== null && !isVrmValueInvalid) {
                            // 这种情况不应该发生，因为 isVrmValueInvalid 为 false 时应该已经设置了 vrmModelPath
                            const vrmValueStr = typeof vrmValue === 'string' ? `"${vrmValue}"` : String(vrmValue);
                            console.warn(`[猫娘切换] VRM 模型路径无效 (${vrmValueStr})，使用默认模型`);
                        } else {
                            // vrmValue 是字符串 "undefined"、"null" 或未配置，视为正常情况，只显示 info
                            console.info('[猫娘切换] VRM 模型路径未配置或无效，使用默认模型');

                            // 如果 vrmValue 是字符串 "undefined"，尝试自动修复后端配置
                            if (hasVrmField && isVrmValueInvalid && typeof vrmValue === 'string') {
                                try {
                                    const fixResponse = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(newCatgirl)}`, {
                                        method: 'PUT',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({
                                            model_type: 'vrm',
                                            vrm: vrmModelPath  // 使用默认模型路径
                                        })
                                    });
                                    if (fixResponse.ok) {
                                        const fixResult = await fixResponse.json();
                                        if (fixResult.success) {
                                            console.log(`[猫娘切换] 已自动修复角色 ${newCatgirl} 的 VRM 模型路径配置（从 "undefined" 修复为默认模型）`);
                                        }
                                    }
                                } catch (fixError) {
                                    console.warn('[猫娘切换] 自动修复配置时出错:', fixError);
                                }
                            }
                        }
                        console.info('[猫娘切换] 使用默认 VRM 模型:', vrmModelPath);
                    } else {
                        // model_type 不是 'vrm'，抛出错误
                        const vrmValueStr = hasVrmField && vrmValue !== undefined && vrmValue !== null
                            ? (typeof vrmValue === 'string' ? `"${vrmValue}"` : String(vrmValue))
                            : '(未配置)';
                        throw new Error(`VRM 模型路径无效: ${vrmValueStr}`);
                    }
                }

                // 确保 VRM 管理器已初始化
                console.log('[猫娘切换] 检查VRM管理器 - 存在:', !!window.vrmManager, '已初始化:', window.vrmManager?._isInitialized);
                if (!window.vrmManager || !window.vrmManager._isInitialized) {
                    console.log('[猫娘切换] VRM管理器需要初始化');

                    // 等待 VRM 模块加载（双保险：事件 + 轮询）
                    if (typeof window.VRMManager === 'undefined') {
                        await new Promise((resolve, reject) => {
                            // 先检查是否已经就绪（事件可能已经发出）
                            if (window.VRMManager) {
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
                                if (!resolved && window.VRMManager) {
                                    resolved = true;
                                    clearTimeout(timeoutId);
                                    window.removeEventListener('vrm-modules-ready', eventHandler);
                                    resolve();
                                }
                            };
                            window.addEventListener('vrm-modules-ready', eventHandler, { once: true });

                            // 方法2：轮询检查（双保险）
                            const pollInterval = setInterval(() => {
                                if (window.VRMManager) {
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

                    if (!window.vrmManager) {
                        window.vrmManager = new window.VRMManager();
                        // 初始化时确保 _goodbyeClicked 为 false
                        window.vrmManager._goodbyeClicked = false;
                    } else {
                        // 如果 vrmManager 已存在，也清除 goodbyeClicked 标志，确保新模型可以正常显示
                        window.vrmManager._goodbyeClicked = false;
                    }

                    // 确保容器和 canvas 存在
                    const vrmContainer = document.getElementById('vrm-container');
                    if (vrmContainer && !vrmContainer.querySelector('canvas')) {
                        const canvas = document.createElement('canvas');
                        canvas.id = 'vrm-canvas';
                        vrmContainer.appendChild(canvas);
                    }

                    // 初始化 Three.js 场景，传入光照配置（如果存在）
                    const lightingConfig = catgirlConfig.lighting || null;
                    await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', lightingConfig);
                }

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

                // 应用角色的光照配置
                if (catgirlConfig.lighting && window.vrmManager) {
                    const lighting = catgirlConfig.lighting;

                    // 确保光照已初始化，如果没有则等待（添加最大重试次数和切换取消条件）
                    let applyLightingRetryCount = 0;
                    const MAX_RETRY_COUNT = 50; // 最多重试50次（5秒）
                    let applyLightingTimerId = null;
                    const currentSwitchId = Symbol(); // 用于标识当前切换，防止旧切换的定时器继续执行
                    window._currentCatgirlSwitchId = currentSwitchId;

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
                            // VRoid Hub 风格：极高环境光，柔和主光，无辅助光
                            const defaultLighting = {
                                ambient: 1.0,      // 极高环境光，消除所有暗部
                                main: 0.6,         // 适中主光，配合跟随相机
                                fill: 0.0,         // 不需要补光
                                rim: 0.0,          // 不需要外部轮廓光
                                top: 0.0,          // 不需要顶光
                                bottom: 0.0        // 不需要底光
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
                    mmdCanvasVrm.style.visibility = 'hidden';
                    mmdCanvasVrm.style.pointerEvents = 'none';
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
                console.log('[猫娘切换] VRM - 对话框已恢复，当前类:', chatContainerVrm ? chatContainerVrm.className : 'N/A');

                // 确保 VRM 按钮和锁图标可见
                setTimeout(() => {
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

                if (!mmdModelPath) {
                    throw new Error('MMD 模型路径未配置');
                }
                console.log('[猫娘切换] MMD 模型路径:', mmdModelPath);

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

                // 显示 MMD 容器
                const mmdContainerShow = document.getElementById('mmd-container');
                if (mmdContainerShow) {
                    mmdContainerShow.classList.remove('hidden');
                    mmdContainerShow.style.display = 'block';
                    mmdContainerShow.style.visibility = 'visible';
                    mmdContainerShow.style.removeProperty('pointer-events');
                }
                const mmdCanvasShow = document.getElementById('mmd-canvas');
                if (mmdCanvasShow) {
                    mmdCanvasShow.style.display = 'block';
                    mmdCanvasShow.style.visibility = 'visible';
                    mmdCanvasShow.style.pointerEvents = 'auto';
                }

                // 初始化 MMD 管理器（MMD→MMD 切换时需要完全重新初始化以避免状态残留）
                console.log('[猫娘切换] 重新初始化 MMD 管理器');
                if (typeof window.initMMDModel === 'function') {
                    await window.initMMDModel();
                } else if (typeof initMMDModel === 'function') {
                    await initMMDModel();
                }

                // 加载 MMD 模型
                if (window.mmdManager) {
                    // 重置 goodbyeClicked 标志
                    window.mmdManager._goodbyeClicked = false;
                    // 提前获取设置并预置物理开关
                    let savedSettings = null;
                    try {
                        const settingsRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(newCatgirl) + '/mmd_settings');
                        const settingsData = await settingsRes.json();
                        if (settingsData.success && settingsData.settings) {
                            savedSettings = settingsData.settings;
                            if (savedSettings.physics?.enabled != null) {
                                window.mmdManager.enablePhysics = !!savedSettings.physics.enabled;
                            }
                        }
                    } catch (e) { /* ignore - will use current enablePhysics */ }
                    await window.mmdManager.loadModel(mmdModelUrl);
                    console.log('[猫娘切换] MMD 模型加载完成');

                    // 应用完整设置（光照、渲染、物理、鼠标跟踪）
                    if (savedSettings) {
                        window.mmdManager.applySettings(savedSettings);
                    }

                    // 播放待机动作（使用已获取的 catgirlConfig，无需重复请求）
                    const mmdIdleAnimation = catgirlConfig?.mmd_idle_animation;
                    if (mmdIdleAnimation) {
                        try {
                            await window.mmdManager.loadAnimation(mmdIdleAnimation);
                            window.mmdManager.playAnimation();
                            console.log('[猫娘切换] 已播放待机动作:', mmdIdleAnimation);
                        } catch (idleErr) {
                            console.warn('[猫娘切换] 播放待机动作失败:', idleErr);
                        }
                    }
                } else {
                    console.error('[猫娘切换] MMD 管理器初始化失败');
                }

                if (window.LanLan1) {
                    window.LanLan1.live2dModel = null;
                    window.LanLan1.currentModel = null;
                }

                const chatContainerMmd = document.getElementById('chat-container');
                const textInputAreaMmd = document.getElementById('text-input-area');
                if (chatContainerMmd) chatContainerMmd.classList.remove('minimized');
                if (textInputAreaMmd) textInputAreaMmd.classList.remove('hidden');

                // 延时显示 MMD 浮动按钮和锁图标
                setTimeout(() => {
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
                const modelData = await modelResponse.json();

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
                }

                // 加载新模型
                if (modelData.success && modelData.model_info) {
                    const modelConfigRes = await fetch(modelData.model_info.path);
                    if (modelConfigRes.ok) {
                        const modelConfig = await modelConfigRes.json();
                        modelConfig.url = modelData.model_info.path;

                        const preferences = await window.live2dManager.loadUserPreferences();
                        const modelPreferences = preferences ? preferences.find(p => p.model_path === modelConfig.url) : null;

                        await window.live2dManager.loadModel(modelConfig, {
                            preferences: modelPreferences,
                            isMobile: window.innerWidth <= 768
                        });

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
                        console.warn(`[猫娘切换] 模型配置获取失败 (HTTP ${modelConfigRes.status}: ${modelData.model_info.path}), 回退到默认模型 mao_pro`);
                        try {
                            const defaultPath = '/static/mao_pro/mao_pro.model3.json';
                            const defaultRes = await fetch(defaultPath);
                            if (defaultRes.ok) {
                                const defaultConfig = await defaultRes.json();
                                defaultConfig.url = defaultPath;
                                await window.live2dManager.loadModel(defaultConfig, {
                                    isMobile: window.innerWidth <= 768
                                });
                                if (window.LanLan1) {
                                    window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                                    window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                                }
                                // 确保 ticker 启动
                                if (window.live2dManager?.pixi_app?.ticker && !window.live2dManager.pixi_app.ticker.started) {
                                    window.live2dManager.pixi_app.ticker.start();
                                }
                                console.log('[猫娘切换] 已回退加载默认模型 mao_pro');
                            } else {
                                console.error('[猫娘切换] 默认模型也无法加载');
                            }
                        } catch (fallbackErr) {
                            console.error('[猫娘切换] 默认模型加载失败:', fallbackErr);
                        }
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
                    mmdCanvasL2d.style.visibility = 'hidden';
                    mmdCanvasL2d.style.pointerEvents = 'none';
                }

                const chatContainerL2d = document.getElementById('chat-container');
                const textInputAreaL2d = document.getElementById('text-input-area');
                if (chatContainerL2d) chatContainerL2d.classList.remove('minimized');
                if (textInputAreaL2d) textInputAreaL2d.classList.remove('hidden');

                // 延时重启 Ticker 和显示按钮（双重保险）
                setTimeout(() => {

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

            showStatusToast(window.t ? window.t('app.switchedCatgirl', { name: newCatgirl }) : `已切换到 ${newCatgirl}`, 3000);

            // 【成就】解锁换肤成就
            if (window.unlockAchievement) {
                try {
                    await window.unlockAchievement('ACH_CHANGE_SKIN');
                } catch (err) {
                    console.error('解锁换肤成就失败:', err);
                }
            }

        } catch (error) {
            console.error('[猫娘切换] 失败:', error);
            showStatusToast(`切换失败: ${error.message}`, 4000);
        } finally {
            S.isSwitchingCatgirl = false;
            // 清理切换标识，取消所有 pending 的 applyLighting 定时器
            window._currentCatgirlSwitchId = null;

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
})();

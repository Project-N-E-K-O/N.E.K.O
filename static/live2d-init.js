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

// 1. 表情控制 (setEmotion / playExpression)
window.LanLan1.setEmotion = function(emotion) {
    // 优先检查 VRM 模式
    if (window.vrmManager && window.vrmManager.currentModel) {
        if (window.vrmManager.expression) {
            // 调用 VRM 的情绪切换
            window.vrmManager.expression.setMood(emotion);
        }
        return; // VRM 处理完直接返回，不再打扰 Live2D
    }
    
    // 如果不是 VRM，且 Live2D 模型已加载，才调用 Live2D
    if (window.live2dManager && window.live2dManager.currentModel) {
        window.live2dManager.setEmotion(emotion);
    }
};

// 兼容旧接口 playExpression，逻辑同 setEmotion
window.LanLan1.playExpression = window.LanLan1.setEmotion;

// 2. 动作控制 (playMotion)
window.LanLan1.playMotion = function(group, no, priority) {
    // VRM 模式下忽略 Live2D 的动作指令，防止报错
    if (window.vrmManager && window.vrmManager.currentModel) {
        return;
    }

    // Live2D 模式
    if (window.live2dManager && window.live2dManager.currentModel) {
        window.live2dManager.playMotion(group, no, priority);
    }
};

// 3. 清除表情/特效
window.LanLan1.clearEmotionEffects = function() {
    if (window.vrmManager && window.vrmManager.currentModel) {
        // VRM 暂时不需要清除特效逻辑，或在此重置表情
        if (window.vrmManager.expression) window.vrmManager.expression.setMood('neutral');
        return;
    }
    if (window.live2dManager) window.live2dManager.clearEmotionEffects();
};

window.LanLan1.clearExpression = function() {
    if (window.vrmManager && window.vrmManager.currentModel) return;
    if (window.live2dManager) window.live2dManager.clearExpression();
};

// 4. 嘴型控制
window.LanLan1.setMouth = function(value) {
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

// 自动初始化函数（延迟执行，等待 cubism4Model 设置）
async function initLive2DModel() {
    // 检查是否在 VRM 模式下，如果是则跳过 Live2D 初始化
    const isVRMMode = window.vrmManager && window.vrmManager.currentModel;
    if (isVRMMode) {
        console.log('[Live2D Init] 当前为 VRM 模式，跳过 Live2D 初始化');
        return;
    }

    // 检查是否在 model_manager 页面且当前选择的是 VRM 模型
    const isModelManagerPage = window.location.pathname.includes('model_manager');
    if (isModelManagerPage) {
        // 兼容 model_manager.html：当前使用的是 <select id="model-type-select"> (live2d/vrm)
        const modelTypeSelect = document.getElementById('model-type-select');
        const activeModelType = modelTypeSelect?.value || localStorage.getItem('modelType');
        if (activeModelType === 'vrm') {
            console.log('[Live2D Init] 模型管理页面当前选择的是 VRM 模型，跳过 Live2D 初始化');
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

    // 等待配置加载完成（如果存在）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }

    // 获取模型路径
    const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));

    if (!targetModelPath && !isModelManagerPage) {
        console.log('未设置模型路径，且不在模型管理页面，跳过Live2D初始化');
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

        // 确保Live2D容器可见
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) live2dContainer.style.display = 'block';

        // 初始化 PIXI 应用；再次检查是否在 VRM 模式下（防止在异步操作期间切换到 VRM）
        if (window.vrmManager && window.vrmManager.currentModel) {
            console.log('[Live2D Init] 检测到 VRM 模式，取消 Live2D 初始化');
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
                isMobile: window.innerWidth <= 768,
                // 在常驻表情应用完成后应用参数（事件驱动，替代不可靠的 setTimeout）
                onResidentExpressionApplied: (model) => {
                    if (modelPreferences && modelPreferences.parameters && 
                        model && model.internalModel && model.internalModel.coreModel) {
                        window.live2dManager.applyModelParameters(model, modelPreferences.parameters);
                        console.log('[Live2D Init] 在常驻表情应用后已重新应用用户偏好参数');
                    }
                }
            });

        // 设置全局引用（兼容性）
        window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
        window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
        window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();

        // 设置页面卸载时的自动清理（确保资源正确释放）
        window.live2dManager.setupUnloadCleanup();

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


/**
 * Live2D Init - 全局导出和自动初始化
 */

// 创建全局 Live2D 管理器实例
window.live2dManager = new Live2DManager();

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
    if (window.live2dManager && window.live2dManager.model) {
        window.live2dManager.setEmotion(emotion);
    }
};

// 兼容旧接口 playExpression，逻辑同 setEmotion
window.LanLan1.playExpression = window.LanLan1.setEmotion;

// 2. 动作控制 (playMotion)
window.LanLan1.playMotion = function(group, no, priority) {
    // VRM 模式下忽略 Live2D 的动作指令，防止报错
    if (window.vrmManager && window.vrmManager.currentModel) {
        console.log('[LanLan1] VRM 模式忽略 Live2D 动作指令:', group);
        return;
    }

    // Live2D 模式
    if (window.live2dManager && window.live2dManager.model) {
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
    if (window.live2dManager && window.live2dManager.model) {
        window.live2dManager.setMouth(value);
    }
};

// 自动初始化函数（延迟执行，等待 cubism4Model 设置）
async function initLive2DModel() {
    // 等待配置加载完成（如果存在）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }

    // 检查是否在model_manager页面，如果是则跳过自动模型加载，但仍初始化Live2D管理器
    const isModelManagerPage = window.location.pathname.includes('model_manager') ||
                              document.querySelector('#model-select') !== null ||
                              document.querySelector('#live2dModelSelect') !== null;

    // 获取模型路径
    const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));

    if (!targetModelPath && !isModelManagerPage) {
        console.log('未设置模型路径，且不在模型管理页面，跳过Live2D初始化');
        return;
    }

    try {
        console.log('开始初始化Live2D模型，路径:', targetModelPath);

        // 初始化 PIXI 应用
        await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
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

                // 首先尝试精确匹配
                modelPreferences = preferences.find(p => p && p.model_path === targetModelPath);

                // 如果精确匹配失败，尝试文件名匹配
                if (!modelPreferences) {
                    const targetFileName = targetModelPath.split('/').pop() || '';
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
                    const targetPathParts = targetModelPath.split('/').filter(p => p);
                    const modelName = targetPathParts[targetPathParts.length - 2] || targetPathParts[targetPathParts.length - 1]?.replace('.model3.json', '');
                    console.log('尝试模型名称匹配，模型名称:', modelName);
                    if (modelName) {
                        modelPreferences = preferences.find(p => {
                            if (!p || !p.model_path) return false;
                            if (p.model_path.includes(modelName)) {
                                console.log('模型名称匹配成功:', p.model_path);
                                return true;
                            }
                            return false;
                        });
                    }
                }
            
                // 如果还是没找到，尝试部分路径匹配
                if (!modelPreferences) {
                    console.log('尝试部分路径匹配...');
                    const targetPathParts = targetModelPath.split('/').filter(p => p);
                    modelPreferences = preferences.find(p => {
                        if (!p || !p.model_path) return false;
                        const prefPathParts = p.model_path.split('/').filter(p => p);
                        // 检查是否有足够的共同部分
                        const commonParts = targetPathParts.filter(part => prefPathParts.includes(part));
                        if (commonParts.length >= 2) {
                            console.log('部分路径匹配成功:', p.model_path, '共同部分:', commonParts);
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
            // 加载模型
            await window.live2dManager.loadModel(targetModelPath, {
                preferences: modelPreferences,
                isMobile: window.innerWidth <= 768
            });

            // 确保参数在常驻表情设置后再次应用（防止被覆盖）
            if (modelPreferences && modelPreferences.parameters) {
                const model = window.live2dManager.getCurrentModel();
                if (model && model.internalModel && model.internalModel.coreModel) {
                    // 延迟一点确保常驻表情已经设置完成
                    setTimeout(() => {
                        window.live2dManager.applyModelParameters(model, modelPreferences.parameters);
                    }, 300);
                }
            }

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

// 自动初始化（如果存在 cubism4Model 变量）
// 如果 pageConfigReady 存在，等待它完成；否则立即执行
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


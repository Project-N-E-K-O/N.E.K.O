/**
 * Live2D Init - 全局导出和自动初始化
 */

// 创建全局 Live2D 管理器实例
window.live2dManager = new Live2DManager();

// 兼容性：保持原有的全局变量和函数
window.LanLan1 = window.LanLan1 || {};
window.LanLan1.setEmotion = (emotion) => window.live2dManager.setEmotion(emotion);
window.LanLan1.playExpression = (emotion) => window.live2dManager.playExpression(emotion);
window.LanLan1.playMotion = (emotion) => window.live2dManager.playMotion(emotion);
window.LanLan1.clearEmotionEffects = () => window.live2dManager.clearEmotionEffects();
window.LanLan1.clearExpression = () => window.live2dManager.clearExpression();
window.LanLan1.setMouth = (value) => window.live2dManager.setMouth(value);

// 自动初始化函数（延迟执行，等待 cubism4Model 设置）
async function initLive2DModel() {
    // 等待配置加载完成（如果存在）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        await window.pageConfigReady;
    }
    
    // 获取模型路径
    const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));
    
    if (!targetModelPath) {
        console.log('未设置模型路径，跳过Live2D初始化');
        return;
    }
    
    try {
        console.log('开始初始化Live2D模型，路径:', targetModelPath);
        
        // 初始化 PIXI 应用
        await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
        
        // 加载用户偏好
        const preferences = await window.live2dManager.loadUserPreferences();
        console.log('加载到的偏好设置数量:', preferences.length);
        
        // 根据模型路径找到对应的偏好设置（使用多种匹配方式）
        let modelPreferences = null;
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
        }
        
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

        console.log('✓ Live2D 管理器自动初始化完成');
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


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

// 自动初始化（如果存在 cubism4Model 变量）
const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));
if (targetModelPath) {
    (async function() {
        try {
            // 初始化 PIXI 应用
            await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
            
            // 加载用户偏好
            const preferences = await window.live2dManager.loadUserPreferences();
            
            // 根据模型路径找到对应的偏好设置
            let modelPreferences = null;
            if (preferences && preferences.length > 0) {
                modelPreferences = preferences.find(p => p && p.model_path === targetModelPath);
                if (modelPreferences) {
                    console.log('找到模型偏好设置:', modelPreferences);
                } else {
                    console.log('未找到模型偏好设置，将使用默认设置');
                }
            }
            
            // 加载模型
            await window.live2dManager.loadModel(targetModelPath, {
                preferences: modelPreferences,
                isMobile: window.innerWidth <= 768
            });

            // 设置全局引用（兼容性）
            window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
            window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
            window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();

            console.log('Live2D 管理器自动初始化完成');
        } catch (error) {
            console.error('Live2D 管理器自动初始化失败:', error);
        }
    })();
}


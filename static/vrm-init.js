/**
 * VRM Init - 全局导出和自动初始化
 */

// VRM 模型路径常量（与服务器端路由保持一致）
const VRM_STATIC_PATH = '/static/vrm';  // 项目目录下的 VRM 模型路径
const VRM_USER_PATH = '/user_vrm';  // 用户文档目录下的 VRM 模型路径

// 创建全局 VRM 管理器实例（如果VRMManager类存在）
try {
    if (typeof VRMManager !== 'undefined') {
        window.vrmManager = new VRMManager();
    } else {
        console.warn('VRMManager 类未定义，跳过VRM初始化（不影响Live2D）');
        window.vrmManager = null;
    }
} catch (error) {
    console.warn('创建 VRMManager 实例失败（不影响Live2D）:', error);
    window.vrmManager = null;
}

// 自动初始化函数（延迟执行，等待 vrmModel 设置）
async function initVRMModel() {
    console.log('[VRM Init] 开始检查VRM初始化条件...');

    // 等待配置加载完成（如果存在）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        console.log('[VRM Init] 等待页面配置加载完成...');
        await window.pageConfigReady;
    }

    // 获取模型路径
    let targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
    console.log('[VRM Init] 模型路径检查:', {
        vrmModel: typeof vrmModel !== 'undefined' ? vrmModel : 'undefined',
        windowVrmModel: window.vrmModel || 'undefined',
        targetModelPath: targetModelPath || 'empty'
    });

    // 如果没有设置模型路径，使用默认模型
    if (!targetModelPath) {
        console.log('[VRM Init] 没有设置模型路径，使用默认VRM模型');
        targetModelPath = '/static/vrm/sister1.0.vrm';
        window.vrmModel = targetModelPath;
    }
    
    // 检查 vrmManager 是否已初始化
    if (!window.vrmManager) {
        console.warn('VRM管理器未初始化，跳过VRM模型加载');
        return;
    }
    
    try {
        console.log('开始初始化VRM模型，路径:', targetModelPath);
        
        // 初始化 Three.js 场景
        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        
        // 显示VRM容器，隐藏Live2D容器
        const vrmContainer = document.getElementById('vrm-container');
        const live2dContainer = document.getElementById('live2d-container');
        if (vrmContainer) {
            vrmContainer.style.display = 'block';
        }
        if (live2dContainer) {
            live2dContainer.style.display = 'none';
        }
        
        // 确保模型URL正确（如果是相对路径，转换为绝对路径）
        let modelUrl = targetModelPath;
        console.log('[VRM Init] 原始模型路径:', modelUrl);
        
        if (!modelUrl.startsWith('http://') && !modelUrl.startsWith('https://') && !modelUrl.startsWith('/')) {
            // 如果是文件名，先尝试 user_vrm，如果不存在再尝试 static/vrm
            if (modelUrl.endsWith('.vrm')) {
                const filename = modelUrl.split('/').pop();
                modelUrl = `${VRM_USER_PATH}/${filename}`;
            } else {
                modelUrl = `${VRM_USER_PATH}/${modelUrl}`;
            }
            console.log('[VRM Init] 转换后的模型路径:', modelUrl);
        }
        
        // 如果路径是本地文件路径（包含盘符或反斜杠），尝试转换为HTTP路径
        if (modelUrl.includes(':\\') || modelUrl.includes('\\')) {
            console.log('[VRM Init] 检测到本地文件路径，尝试转换...');
            const filename = modelUrl.split(/[\\/]/).pop();
            // 先尝试 /static/vrm/（项目目录），再尝试 /user_vrm/（用户文档目录）
            modelUrl = `${VRM_STATIC_PATH}/${filename}`;
            console.log('[VRM Init] 转换为项目目录路径:', modelUrl);
        }
        
        // 验证路径格式
        if (!modelUrl.startsWith(VRM_USER_PATH + '/') && !modelUrl.startsWith(VRM_STATIC_PATH + '/') && !modelUrl.startsWith('http')) {
            console.warn('[VRM Init] 警告：模型路径格式可能不正确:', modelUrl);
        }
        
        console.log('[VRM Init] 最终使用的模型URL:', modelUrl);
        
        // 在加载前验证文件是否存在（可选，但有助于调试）
        // 如果文件不存在，尝试从 /static/vrm/ 路径加载
        let fileExists = false;
        try {
            const response = await fetch(modelUrl, { method: 'HEAD' });
            if (response.ok) {
                fileExists = true;
                console.log('[VRM Init] 模型文件存在，开始加载');
            } else {
                console.warn(`[VRM Init] 警告：模型文件可能不存在 (HTTP ${response.status}):`, modelUrl);
                // 如果 /user_vrm/ 路径不存在，尝试 /static/vrm/ 路径
                if (modelUrl.startsWith(VRM_USER_PATH + '/')) {
                    const filename = modelUrl.replace(VRM_USER_PATH + '/', '');
                    const staticPath = `${VRM_STATIC_PATH}/${filename}`;
                    console.log(`[VRM Init] 尝试备用路径: ${staticPath}`);
                    try {
                        const staticResponse = await fetch(staticPath, { method: 'HEAD' });
                        if (staticResponse.ok) {
                            modelUrl = staticPath;
                            fileExists = true;
                            console.log(`[VRM Init] 在项目目录找到模型文件: ${modelUrl}`);
                        }
                    } catch (e) {
                        console.warn('[VRM Init] 备用路径也不存在:', e);
                    }
                }
                
                // 如果仍然不存在，尝试列出可用的模型文件
                if (!fileExists) {
                    try {
                        const modelsResponse = await fetch('/api/model/vrm/models');
                        if (modelsResponse.ok) {
                            const modelsData = await modelsResponse.json();
                            if (modelsData.success && modelsData.models) {
                                console.log('[VRM Init] 可用的VRM模型列表:', modelsData.models.map(m => m.filename));
                            }
                        }
                    } catch (e) {
                        console.warn('[VRM Init] 无法获取模型列表:', e);
                    }
                }
            }
        } catch (fetchError) {
            console.warn('[VRM Init] 无法验证模型文件（继续尝试加载）:', fetchError);
        }
        
        // 加载模型
        await window.vrmManager.loadModel(modelUrl, {
            scale: { x: 1, y: 1, z: 1 }
        });
        
        console.log('✓ VRM 管理器自动初始化完成');

    // 延迟检查模型加载状态
    setTimeout(() => {
        console.log('[VRM Init] 延迟检查模型加载状态...');
        if (window.vrmManager && window.vrmManager.currentModel) {
            console.log('[VRM Init] ✅ 模型加载成功！');
            console.log('模型信息:', {
                name: window.vrmManager.currentModel.name,
                hasScene: !!window.vrmManager.currentModel.scene,
                hasVRM: !!window.vrmManager.currentModel.vrm
            });
        } else {
            console.log('[VRM Init] ❌ 模型加载可能失败或仍在进行中');
            console.log('vrmManager存在:', !!window.vrmManager);
            if (window.vrmManager) {
                console.log('currentModel存在:', !!window.vrmManager.currentModel);
            }
        }
    }, 3000);
    } catch (error) {
        console.error('VRM 管理器自动初始化失败:', error);
        console.error('错误堆栈:', error.stack);
        
        // 提供更详细的错误信息
        if (error.message && error.message.includes('404')) {
            console.error('[VRM Init] 模型文件未找到。请检查：');
            console.error(`  1. 文件是否存在于服务器上的 ${VRM_USER_PATH}/ 或 ${VRM_STATIC_PATH}/ 目录`);
            console.error('  2. 文件名是否正确（包括大小写）');
            console.error('  3. 配置中的模型路径是否正确');
            console.error('  尝试的路径:', targetModelPath);
        }
    }
}

// 自动初始化（如果存在 vrmModel 变量且 vrmManager 已初始化）
// 如果 pageConfigReady 存在，等待它完成；否则立即执行
if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
    window.pageConfigReady.then(() => {
        // 检查是否有VRM模型路径和vrmManager
        if (window.vrmManager) {
            const targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
            if (targetModelPath) {
                initVRMModel();
            }
        }
    }).catch(() => {
        // 即使配置加载失败，也尝试初始化（可能使用默认模型）
        if (window.vrmManager) {
            const targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
            if (targetModelPath) {
                initVRMModel();
            }
        }
    });
} else {
    // 如果没有 pageConfigReady，检查 vrmModel 是否已设置
    console.log('[VRM Init] 没有pageConfigReady，检查vrmModel设置...');
    if (window.vrmManager) {
        const targetModelPath = (typeof vrmModel !== 'undefined' ? vrmModel : (window.vrmModel || ''));
        console.log('[VRM Init] 当前vrmModel状态:', targetModelPath);
        if (targetModelPath) {
            initVRMModel();
        } else {
            console.log('[VRM Init] vrmModel未设置，使用默认VRM模型');
            // 使用项目中的默认VRM模型
            window.vrmModel = '/static/vrm/sister1.0.vrm';
            setTimeout(() => {
                if (window.vrmManager) {
                    initVRMModel();
                }
            }, 1000);
        }
    } else {
        console.log('[VRM Init] vrmManager不存在，VRM模块可能未加载');
    }
}

// 添加调试函数
window.checkVRMStatus = function() {
    console.log('[VRM Status Check] === VRM 状态检查 ===');
    console.log('window.vrmManager:', !!window.vrmManager);
    if (window.vrmManager) {
        console.log('currentModel:', !!window.vrmManager.currentModel);
        console.log('scene:', !!window.vrmManager.scene);
        console.log('camera:', !!window.vrmManager.camera);
        console.log('renderer:', !!window.vrmManager.renderer);
        console.log('isLocked:', window.vrmManager.isLocked);
        console.log('interaction:', !!window.vrmManager.interaction);
        console.log('canvas:', !!window.vrmManager.canvas);
        if (window.vrmManager.canvas) {
            const computedStyle = window.getComputedStyle(window.vrmManager.canvas);
            console.log('canvas.pointerEvents:', window.vrmManager.canvas.style.pointerEvents);
            console.log('canvas.computedPointerEvents:', computedStyle.pointerEvents);
            console.log('canvas.display:', window.vrmManager.canvas.style.display);
            console.log('canvas.visibility:', window.vrmManager.canvas.style.visibility);
            console.log('canvas.zIndex:', window.vrmManager.canvas.style.zIndex);
            console.log('canvas.position:', window.vrmManager.canvas.style.position);
            const rect = window.vrmManager.canvas.getBoundingClientRect();
            console.log('canvas.rect:', { left: rect.left, top: rect.top, width: rect.width, height: rect.height });
        }
        if (window.vrmManager.interaction) {
            console.log('interaction.isLocked:', window.vrmManager.interaction.isLocked);
        }
    }
    console.log('[VRM Status Check] === 检查完成 ===');
};

// 添加强制解锁函数
window.forceUnlockVRM = function() {
    if (window.vrmManager) {
        console.log('[VRM Force Unlock] 强制解锁VRM');
        window.vrmManager.isLocked = false;
        if (window.vrmManager.canvas) {
            window.vrmManager.canvas.style.setProperty('pointer-events', 'auto', 'important');
            console.log('[VRM Force Unlock] Canvas pointer-events设置为auto');
        }
        if (window.vrmManager.container) {
            window.vrmManager.container.style.setProperty('pointer-events', 'auto', 'important');
            console.log('[VRM Force Unlock] Container pointer-events设置为auto');
        }
        if (window.vrmManager.interaction) {
            window.vrmManager.interaction.setLocked(false);
        }
        console.log('[VRM Force Unlock] 解锁完成');
    } else {
        console.log('[VRM Force Unlock] vrmManager不存在');
    }
};


const ModelPathHelper = {
    /**
     * 验证模型路径是否有效
     * 拒绝 undefined/null 字符串、空值、以及包含 'undefined'/'null' 的字符串
     * @param {*} path - 原始路径值
     * @returns {string} 验证后的字符串，无效时返回空字符串
     */
    validatePath(path) {
        if (path === undefined || path === null) return '';
        if (typeof path !== 'string') {
            path = String(path);
        }
        const trimmed = path.trim();
        if (trimmed === '') return '';
        if (trimmed === 'undefined' || trimmed === 'null') return '';
        if (trimmed.toLowerCase().includes('undefined') || trimmed.toLowerCase().includes('null')) return '';
        return trimmed;
    },

    /**
     * 从模型数据中提取有效的 VRM 路径
     * @param {Object} model - 模型数据对象
     * @returns {Object} { path: string, isValid: boolean, filename: string }
     */
    extractVrmPath(model) {
        if (!model || typeof model !== 'object') {
            return { path: '', isValid: false, filename: '' };
        }

        // 优先检查 url 字段
        let validPath = this.validatePath(model.url);
        if (validPath) {
            return { path: validPath, isValid: true, filename: model.filename || '' };
        }

        // 检查 path 字段
        validPath = this.validatePath(model.path);
        if (validPath) {
            return { path: validPath, isValid: true, filename: model.filename || '' };
        }

        // 如果都没有，但有 filename，根据 location 构建路径
        const validFilename = this.validatePath(model.filename);
        if (validFilename) {
            const builtPath = model.location === 'project'
                ? `/static/vrm/${validFilename}`
                : `/user_vrm/${validFilename}`;
            return { path: builtPath, isValid: true, filename: validFilename };
        }

        return { path: '', isValid: false, filename: '' };
    },

    /**
     * 标准化模型路径
     * 处理 Windows 反斜杠、/user_vrm/ 前缀和本地文件路径
     * @param {string} rawPath - 原始路径
     * @param {string} type - 类型：'model' 或 'animation'（默认 'model'）
     * @returns {string} 标准化后的路径
     */
    normalizeModelPath(rawPath, type = 'model') {
        const path = this.validatePath(rawPath);
        if (!path) return '';

        // 如果已经是 URL 格式 (http/https) 或 Web 绝对路径 (/)，直接返回
        if (path.startsWith('http') || path.startsWith('/')) {
            // 统一将 Windows 的反斜杠转换为正斜杠
            return path.replace(/\\/g, '/');
        }

        // 统一将 Windows 的反斜杠转换为正斜杠
        const normalizedPath = path.replace(/\\/g, '/');
        const filename = normalizedPath.split('/').pop();

        // 1. 优先检测是否是项目内置的 static 目录
        if (normalizedPath.includes('static/vrm')) {
            return type === 'animation'
                ? `/static/vrm/animation/${filename}`
                : `/static/vrm/${filename}`;
        }

        // 2. 检测其他可能的目录结构
        else if (normalizedPath.includes('models/vrm')) {
            return type === 'animation'
                ? `/models/vrm/animations/${filename}`
                : `/models/vrm/${filename}`;
        }

        // 3. 默认 Fallback：如果是只有文件名，或者无法识别路径，默认去 user_vrm 找
        return `/user_vrm/${type === 'animation' ? 'animation/' : ''}${filename}`;
    },

    /**
     * 将后端返回的相对路径或本地路径转换为前端可用的 URL（VRM 专用）
     * @param {string} path - 原始路径
     * @param {string} type - 类型：'animation' 或 'model'（默认 'animation'）
     * @returns {string} 转换后的 URL
     */
    vrmToUrl(path, type = 'animation') {
        return this.normalizeModelPath(path, type);
    }
};
/**
 * ===== 代码质量改进：API 请求标准化 =====
 *
 * RequestHelper: 统一处理所有网络请求，确保一致的错误处理和超时机制
 *
 * 改进原因：
 * - 之前使用原生 fetch() 导致错误处理不一致
 * - 缺少统一的超时机制
 * - 错误信息不够详细
 *
 * 功能：
 * - fetchJson(): 统一的 JSON API 请求方法
 *   - 自动超时处理（默认10秒）
 *   - 统一的错误处理和错误信息提取
 *   - 自动验证响应格式（确保是 JSON）
 *
 * 已替换的 fetch() 调用：
 * - getLanlanName() 中的 /api/config/page_config
 * - saveModelToCharacter() 中的 /api/characters 相关调用
 * - loadCurrentCharacterModel() 中的 /api/characters 相关调用
 * - loadCharacterLighting() 中的 /api/characters/
 * - checkVoiceModeStatus() 中的 /api/characters/catgirl/{name}/voice_mode_status
 * - loadUserModels() 中的 /api/live2d/user_models
 * - 删除模型功能中的 /api/live2d/model/{name} (DELETE)
 * - 表情映射相关中的 /api/live2d/emotion_mapping/{name}
 * - loadEmotionMappingForModel() 中的 /api/live2d/emotion_mapping/{name}
 * - 模型配置文件加载中的 modelJsonUrl
 * - 以及其他所有 JSON API 调用
 *
 * 注意：文件上传（FormData）的 fetch() 调用保留原样，因为需要特殊处理
 */
const RequestHelper = {
    /**
     * 统一的 JSON API 请求方法
     * @param {string} url - 请求 URL
     * @param {object} options - fetch 选项（method, headers, body 等）
     * @param {number} timeout - 超时时间（毫秒），默认 10000
     * @returns {Promise<object>} 解析后的 JSON 数据
     * @throws {Error} 如果请求失败、超时或响应不是有效的 JSON
     */
    async fetchJson(url, options = {}, timeout = 10000) {
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeout);

        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            clearTimeout(id);

            // 检查 HTTP 状态码
            if (!response.ok) {
                // 尝试读取错误响应体以获取详细错误信息
                let errorMessage = `网络请求失败 (HTTP ${response.status})`;
                try {
                    const errorData = await response.json();
                    if (errorData.error) {
                        errorMessage = errorData.error;
                        // 如果有错误类型和堆栈跟踪，也记录到控制台
                        if (errorData.error_type) {
                            console.error(`错误类型: ${errorData.error_type}`);
                        }
                        if (errorData.traceback && errorData.traceback.length > 0) {
                            console.error('错误堆栈:', errorData.traceback.join('\n'));
                        }
                    }
                } catch (parseError) {
                    // 如果无法解析 JSON，使用默认错误消息
                    console.warn('无法解析错误响应:', parseError);
                }
                throw new Error(errorMessage);
            }

            // 检查内容类型，确保是 JSON
            const contentType = response.headers.get("content-type");
            if (!contentType || !contentType.includes("application/json")) {
                throw new Error("服务器未返回有效的 JSON 数据");
            }

            const data = await response.json();
            return data;
        } catch (error) {
            clearTimeout(id);
            if (error.name === 'AbortError') throw new Error("请求超时，请检查后端服务");
            throw error;
        }
    }
};
// 全屏控制函数
const requestFullscreen = () => {
    const elem = document.documentElement;
    if (elem.requestFullscreen) {
        return elem.requestFullscreen();
    } else if (elem.webkitRequestFullscreen) {
        return elem.webkitRequestFullscreen();
    } else if (elem.mozRequestFullScreen) {
        return elem.mozRequestFullScreen();
    } else if (elem.msRequestFullscreen) {
        return elem.msRequestFullscreen();
    }
    return Promise.reject(new Error('Fullscreen not supported'));
};

const exitFullscreen = () => {
    if (document.exitFullscreen) {
        return document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
        return document.webkitExitFullscreen();
    } else if (document.mozCancelFullScreen) {
        return document.mozCancelFullScreen();
    } else if (document.msExitFullscreen) {
        return document.msExitFullscreen();
    }
    return Promise.reject(new Error('Exit fullscreen not supported'));
};

const isFullscreen = () => {
    return !!(document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.mozFullScreenElement ||
        document.msFullscreenElement);
};

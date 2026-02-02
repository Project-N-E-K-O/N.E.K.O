/**
 * N.E.K.O 新手引导管理器
 * 基于 driver.js v1.0+ 实现
 *
 * 功能：
 * - 首次访问自动触发引导
 * - localStorage 记录用户是否已看过引导
 * - 支持跳过和重新开始
 * - 深色/磨砂风格定制
 */

class TutorialManager {
    constructor() {
        this.STORAGE_KEY = 'neko_has_seen_tutorial';
        this.driver = null;
        this.isInitialized = false;
        this.currentStep = 0;

        // 等待 driver.js 库加载
        this.waitForDriver();
    }

    /**
     * 等待 driver.js 库加载
     */
    waitForDriver() {
        // 检查是否已加载
        if (typeof window.driver !== 'undefined') {
            this.initDriver();
            return;
        }

        // 监听 driver-ready 事件
        const onDriverReady = () => {
            window.removeEventListener('driver-ready', onDriverReady);
            console.log('[Tutorial] driver.js 已加载');
            this.initDriver();
        };

        window.addEventListener('driver-ready', onDriverReady);

        // 备用：轮询检查（最多等待 10 秒）
        let attempts = 0;
        const maxAttempts = 100;

        const checkDriver = () => {
            attempts++;

            if (typeof window.driver !== 'undefined') {
                window.removeEventListener('driver-ready', onDriverReady);
                console.log('[Tutorial] driver.js 已加载（轮询检测）');
                this.initDriver();
                return;
            }

            if (attempts >= maxAttempts) {
                window.removeEventListener('driver-ready', onDriverReady);
                console.error('[Tutorial] driver.js 加载失败（超时 10 秒）');
                console.warn('[Tutorial] 请检查：');
                console.warn('  1. CDN 连接是否正常');
                console.warn('  2. 浏览器控制台是否有其他错误');
                console.warn('  3. 网络是否被代理/防火墙阻止');
                return;
            }

            setTimeout(checkDriver, 100);
        };

        checkDriver();
    }

    /**
     * 初始化 driver.js 实例
     */
    initDriver() {
        if (this.isInitialized) return;

        try {
            // window.driver 是 Driver 类本身
            const DriverClass = window.driver;

            if (!DriverClass) {
                console.error('[Tutorial] driver.js 类未找到');
                return;
            }

            // 创建 driver 实例
            this.driver = new DriverClass({
                padding: 8,
                allowClose: true,
                overlayClickNext: false,
                animate: true,
                className: 'neko-tutorial-driver',
                disableActiveInteraction: false,
            });

            this.isInitialized = true;
            console.log('[Tutorial] driver.js 初始化成功');

            // 检查是否需要自动启动引导
            this.checkAndStartTutorial();
        } catch (error) {
            console.error('[Tutorial] driver.js 初始化失败:', error);
        }
    }

    /**
     * 检查是否需要自动启动引导
     */
    checkAndStartTutorial() {
        const hasSeen = localStorage.getItem(this.STORAGE_KEY);

        if (!hasSeen) {
            // 延迟启动，确保 DOM 完全加载
            setTimeout(() => {
                this.startTutorial();
            }, 1500);
        }
    }

    /**
     * 获取引导步骤配置
     */
    getSteps() {
        return [
            {
                element: '#live2d-container',
                popover: {
                    title: window.t ? window.t('tutorial.step1.title', '👋 欢迎来到 N.E.K.O') : '👋 欢迎来到 N.E.K.O',
                    description: window.t ? window.t('tutorial.step1.desc', '这是你的虚拟伙伴，她会陪伴你进行各种交互。点击她可以触发不同的表情和动作哦~') : '这是你的虚拟伙伴，她会陪伴你进行各种交互。点击她可以触发不同的表情和动作哦~',
                    side: 'left',
                    align: 'center',
                }
            },
            {
                element: '#chat-container',
                popover: {
                    title: window.t ? window.t('tutorial.step2.title', '💬 对话区域') : '💬 对话区域',
                    description: window.t ? window.t('tutorial.step2.desc', '在这里可以和伙伴进行文字对话。输入你的想法，她会给你有趣的回应呢~') : '在这里可以和伙伴进行文字对话。输入你的想法，她会给你有趣的回应呢~',
                    side: 'right',
                    align: 'center',
                }
            },
            {
                element: '#textInputBox',
                popover: {
                    title: window.t ? window.t('tutorial.step3.title', '✍️ 输入框') : '✍️ 输入框',
                    description: window.t ? window.t('tutorial.step3.desc', '在这里输入你想说的话。按 Enter 发送，Shift+Enter 换行。也可以点击右边的按钮发送截图哦~') : '在这里输入你想说的话。按 Enter 发送，Shift+Enter 换行。也可以点击右边的按钮发送截图哦~',
                    side: 'top',
                    align: 'center',
                }
            },
            {
                element: '#button-group',
                popover: {
                    title: window.t ? window.t('tutorial.step4.title', '🎮 快速操作') : '🎮 快速操作',
                    description: window.t ? window.t('tutorial.step4.desc', '左边是发送按钮，右边是截图按钮。你可以分享屏幕截图给伙伴，她会帮你分析哦~') : '左边是发送按钮，右边是截图按钮。你可以分享屏幕截图给伙伴，她会帮你分析哦~',
                    side: 'top',
                    align: 'center',
                }
            }
        ];
    }

    /**
     * 启动引导
     */
    startTutorial() {
        if (!this.isInitialized) {
            console.warn('[Tutorial] driver.js 未初始化');
            return;
        }

        try {
            const steps = this.getSteps();

            // 过滤掉不存在的元素
            const validSteps = steps.filter(step => {
                const element = document.querySelector(step.element);
                if (!element) {
                    console.warn(`[Tutorial] 元素不存在: ${step.element}`);
                    return false;
                }
                return true;
            });

            if (validSteps.length === 0) {
                console.warn('[Tutorial] 没有有效的引导步骤');
                return;
            }

            // 定义步骤
            this.driver.setSteps(validSteps);

            // 监听事件
            this.driver.on('destroy', () => this.onTutorialEnd());
            this.driver.on('next', () => this.onStepChange());
            this.driver.on('previous', () => this.onStepChange());

            // 启动引导
            this.driver.start();
            console.log('[Tutorial] 引导已启动');
        } catch (error) {
            console.error('[Tutorial] 启动引导失败:', error);
        }
    }

    /**
     * 步骤改变时的回调
     */
    onStepChange() {
        this.currentStep = this.driver.activeIndex || 0;
        console.log(`[Tutorial] 当前步骤: ${this.currentStep + 1}`);
    }

    /**
     * 引导结束时的回调
     */
    onTutorialEnd() {
        // 标记用户已看过引导
        localStorage.setItem(this.STORAGE_KEY, 'true');
        console.log('[Tutorial] 引导已完成，已保存标记');

        // 显示完成提示
        this.showCompletionMessage();
    }

    /**
     * 显示完成提示
     */
    showCompletionMessage() {
        const message = window.t ? window.t('tutorial.completed', '✨ 引导完成！祝你使用愉快~') : '✨ 引导完成！祝你使用愉快~';

        // 使用项目现有的 toast 系统（如果有的话）
        if (window.showStatusToast) {
            window.showStatusToast(message, 3000);
        } else {
            // 备用方案：简单的 alert
            console.log('[Tutorial]', message);
        }
    }

    /**
     * 重新启动引导（用户手动触发）
     */
    restartTutorial() {
        // 清除标记
        localStorage.removeItem(this.STORAGE_KEY);

        // 重新启动
        if (this.driver) {
            this.driver.destroy();
        }

        this.startTutorial();
    }

    /**
     * 跳过引导
     */
    skipTutorial() {
        if (this.driver) {
            this.driver.destroy();
        }
        this.onTutorialEnd();
    }

    /**
     * 销毁引导实例
     */
    destroy() {
        if (this.driver) {
            this.driver.destroy();
            this.driver = null;
        }
        this.isInitialized = false;
    }

    /**
     * 获取引导状态
     */
    hasSeenTutorial() {
        return localStorage.getItem(this.STORAGE_KEY) === 'true';
    }

    /**
     * 重置引导状态（开发用）
     */
    resetTutorialState() {
        localStorage.removeItem(this.STORAGE_KEY);
        console.log('[Tutorial] 引导状态已重置');
    }
}

// 创建全局实例
window.tutorialManager = null;

/**
 * 初始化教程管理器
 * 应在 DOM 加载完成后调用
 */
function initTutorialManager() {
    if (!window.tutorialManager) {
        window.tutorialManager = new TutorialManager();
        console.log('[Tutorial] 教程管理器已初始化');
    }
}

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TutorialManager, initTutorialManager };
}

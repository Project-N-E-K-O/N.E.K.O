(function () {
    // Shared contract file owned by the main integrator.
    // Dev B edits performance blocks. Dev C edits anchor/navigation blocks.
    // If you need to change field shape or scene IDs, update the freeze doc first.

    const CONTRACT_VERSION = 2;
    const DEFAULT_PAGE_KEYS = Object.freeze([
        'home',
        'api_key',
        'memory_browser',
        'plugin_dashboard'
    ]);

    const DEFAULT_SCENE_ORDER = Object.freeze({
        home: [],
        api_key: [],
        memory_browser: [],
        plugin_dashboard: []
    });
    const DEFAULT_RESISTANCE_STEP_PATCHES = Object.freeze({
        interrupt_resist_light: Object.freeze({
            page: 'home',
            anchor: '#${p}-container',
            tutorial: Object.freeze({
                title: '轻微抵抗',
                description: '用户轻微试探时的较劲反馈。'
            }),
            performance: Object.freeze({
                bubbleText: '喂！不要拽我啦，现在还没轮到你的回合呢！',
                bubbleTextKey: 'tutorial.yuiGuide.lines.interruptResistLight1',
                voiceKey: 'interrupt_resist_light_1',
                emotion: 'angry',
                cursorAction: 'wobble',
                cursorTarget: '#${p}-container',
                interruptible: true,
                resistanceVoices: Object.freeze([
                    '喂！不要拽我啦，现在还没轮到你的回合呢！',
                    '等一下啦！还没结束呢，不要这么随便打断我啦！'
                ]),
                resistanceVoiceKeys: Object.freeze([
                    'tutorial.yuiGuide.lines.interruptResistLight1',
                    'tutorial.yuiGuide.lines.interruptResistLight3'
                ])
            }),
            interrupts: Object.freeze({
                mode: 'theatrical_abort',
                threshold: 3,
                throttleMs: 500,
                resetOnStepAdvance: false
            })
        }),
        interrupt_angry_exit: Object.freeze({
            page: 'home',
            anchor: '#${p}-container',
            tutorial: Object.freeze({
                title: '生气退出',
                description: '连续有效打断达到阈值后，进入带演出的 angry exit。'
            }),
            performance: Object.freeze({
                bubbleText: '人类！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！',
                bubbleTextKey: 'tutorial.yuiGuide.lines.interruptAngryExit',
                voiceKey: 'interrupt_angry_exit',
                emotion: 'angry',
                cursorAction: 'none',
                cursorTarget: '#${p}-container',
                interruptible: true
            }),
            interrupts: Object.freeze({
                mode: 'theatrical_abort',
                threshold: 3,
                throttleMs: 500,
                resetOnStepAdvance: false
            })
        })
    });

    function deepFreeze(value) {
        if (!value || typeof value !== 'object' || Object.isFrozen(value)) {
            return value;
        }
        Object.freeze(value);
        Object.keys(value).forEach(function (key) {
            deepFreeze(value[key]);
        });
        return value;
    }

    function createBaseStep(id, page, anchor) {
        return {
            id: id,
            page: page,
            anchor: anchor,
            tutorial: {
                title: '',
                description: '',
                autoAdvance: false,
                allowUserInteraction: false
            },
            performance: {
                bubbleText: '',
                bubbleTextKey: '',
                voiceKey: '',
                emotion: 'neutral',
                cursorAction: 'none',
                cursorTarget: '',
                settingsMenuId: '',
                cursorSpeedMultiplier: 1,
                delayMs: 0,
                interruptible: false,
                timeline: [],
                resistanceVoices: [],
                resistanceVoiceKeys: []
            },
            navigation: {
                openUrl: '',
                windowName: '',
                resumeScene: null
            },
            interrupts: {
                mode: 'ignore',
                threshold: 3,
                throttleMs: 500,
                resetOnStepAdvance: true
            }
        };
    }

    function getDailyGuide(day) {
        const registry = window.YuiGuideDailyGuides || {};
        return registry[Number(day)] || null;
    }

    function mergeSection(target, patch) {
        if (!patch || typeof patch !== 'object') {
            return;
        }
        Object.keys(patch).forEach(function (key) {
            target[key] = patch[key];
        });
    }

    function createStepFromPatch(id, patch) {
        const normalizedPatch = patch && typeof patch === 'object' ? patch : {};
        const step = createBaseStep(
            id,
            normalizedPatch.page || 'home',
            normalizedPatch.anchor || ''
        );

        mergeSection(step.tutorial, normalizedPatch.tutorial);
        mergeSection(step.performance, normalizedPatch.performance);
        mergeSection(step.navigation, normalizedPatch.navigation);
        mergeSection(step.interrupts, normalizedPatch.interrupts);

        if (normalizedPatch.page) step.page = normalizedPatch.page;
        if (normalizedPatch.anchor) step.anchor = normalizedPatch.anchor;

        return step;
    }

    const day1Guide = getDailyGuide(1) || {};
    const configuredPageKeys = Array.isArray(day1Guide.pageKeys) ? day1Guide.pageKeys : [];
    const pageKeys = DEFAULT_PAGE_KEYS.concat(configuredPageKeys).filter(function (page, index, list) {
        return typeof page === 'string' && page && list.indexOf(page) === index;
    });
    const steps = {};
    const day1Steps = day1Guide.steps && typeof day1Guide.steps === 'object'
        ? day1Guide.steps
        : {};

    Object.keys(day1Steps).forEach(function (id) {
        steps[id] = createStepFromPatch(id, day1Steps[id]);
    });
    Object.keys(DEFAULT_RESISTANCE_STEP_PATCHES).forEach(function (id) {
        if (!steps[id]) {
            steps[id] = createStepFromPatch(id, DEFAULT_RESISTANCE_STEP_PATCHES[id]);
        }
    });

    const guideSceneOrder = day1Guide.sceneOrder && typeof day1Guide.sceneOrder === 'object'
        ? day1Guide.sceneOrder
        : {};
    const sceneOrder = {};
    pageKeys.forEach(function (page) {
        const configuredOrder = guideSceneOrder[page];
        const defaultOrder = DEFAULT_SCENE_ORDER[page] || [];
        sceneOrder[page] = Array.isArray(configuredOrder)
            ? configuredOrder.slice()
            : defaultOrder.slice();
    });

    const DEV_MODE = !!(
        typeof window !== 'undefined'
        && window.location
        && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    );

    if (DEV_MODE) {
        Object.keys(sceneOrder).forEach(function (page) {
            (sceneOrder[page] || []).forEach(function (id) {
                if (!steps[id]) {
                    console.warn('[YuiGuideSteps] sceneOrder 引用了未定义步骤:', page, id);
                }
            });
        });
    }

    const registry = {
        contractVersion: CONTRACT_VERSION,
        pageKeys: pageKeys.slice(),
        sceneOrder: sceneOrder,
        steps: steps,
        getPageSteps: function (page) {
            const order = sceneOrder[page] || [];
            return order.map(function (id) {
                return steps[id];
            }).filter(Boolean);
        },
        getStep: function (id) {
            return steps[id] || null;
        },
        hasStep: function (id) {
            return Object.prototype.hasOwnProperty.call(steps, id);
        }
    };

    deepFreeze(sceneOrder);
    deepFreeze(steps);
    deepFreeze(registry);

    window.YuiGuideStepsRegistry = registry;
    window.getYuiGuideStepsRegistry = function () {
        return registry;
    };
})();

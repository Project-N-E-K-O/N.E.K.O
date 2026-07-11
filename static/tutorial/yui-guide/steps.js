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
                bubbleText: '喵！现在是人家的教学时间，不可以乱动鼠标和键盘啦！乖乖看着人家，好不好嘛？',
                bubbleTextKey: 'tutorial.yuiGuide.lines.interruptResistLight1',
                voiceKey: 'interrupt_resist_light_1',
                emotion: 'angry',
                cursorAction: 'wobble',
                cursorTarget: '#${p}-container',
                interruptible: true,
                resistanceVoices: Object.freeze([
                    '喵！现在是人家的教学时间，不可以乱动鼠标和键盘啦！乖乖看着人家，好不好嘛？',
                    '真是的，又在乱动鼠标和键盘！再不听话的话，人家可真的要生气了喵！',
                    '最后警告一次喵！你要是再乱动一下，人家就直接退出新手教程，不教你了！'
                ]),
                resistanceVoiceKeys: Object.freeze([
                    'tutorial.yuiGuide.lines.interruptResistLight1',
                    'tutorial.yuiGuide.lines.interruptResistLight2',
                    'tutorial.yuiGuide.lines.interruptResistLight3'
                ])
            }),
            interrupts: Object.freeze({
                mode: 'theatrical_abort',
                threshold: 4,
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
                bubbleText: '人家已经忍你很久了！既然你就是不肯乖乖听话，那新手教程到此结束，接下来你自己慢慢研究吧，哼！',
                bubbleTextKey: 'tutorial.yuiGuide.lines.interruptAngryExit',
                voiceKey: 'interrupt_angry_exit',
                emotion: 'angry',
                cursorAction: 'none',
                cursorTarget: '#${p}-container',
                interruptible: true
            }),
            interrupts: Object.freeze({
                mode: 'theatrical_abort',
                threshold: 4,
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
                threshold: 4,
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

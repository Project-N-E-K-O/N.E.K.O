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

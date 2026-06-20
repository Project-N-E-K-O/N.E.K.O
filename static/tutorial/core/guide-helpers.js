(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialGuideHelpers = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

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

    function registerGuide(config, options) {
        if (!config || !config.day) {
            return;
        }
        const normalizedOptions = options || {};
        const win = normalizedOptions.window || root;
        if (!win) {
            return;
        }
        const registry = win.YuiGuideDailyGuides || {};
        registry[config.day] = config;
        win.YuiGuideDailyGuides = registry;
        if (normalizedOptions.day1Alias === true || Number(config.day) === 1) {
            win.YuiGuideDay1HomeGuide = config;
        }
    }

    function audioFilesForAllLocales(fileName) {
        return Object.freeze({
            zh: fileName,
            ja: fileName,
            en: fileName,
            ko: fileName,
            ru: fileName
        });
    }

    return {
        deepFreeze,
        registerGuide,
        audioFilesForAllLocales
    };
});

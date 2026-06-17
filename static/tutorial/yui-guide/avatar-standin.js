(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.YuiGuideAvatarStandIn = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const RESOURCE_PATHS = Object.freeze({
        'peek-head': '/static/assets/tutorial/avatar-standins/peek-head.png',
        'peek-left-border': '/static/assets/tutorial/avatar-standins/peek-left-border.png',
        'peek-right-border': '/static/assets/tutorial/avatar-standins/peek-right-border.png'
    });

    const CUES_BY_DAY = Object.freeze({
        2: Object.freeze({
            day2_personalization_space: Object.freeze({
                resource: 'peek-right-border',
                position: 'top-right-border',
                delayMs: 900,
                durationMs: 4200
            }),
            day2_proactive_chat: Object.freeze({
                resource: 'peek-head',
                position: 'bottom-right',
                delayMs: 700,
                durationMs: 3600
            })
        })
    });

    function getResourcePath(resource) {
        const key = typeof resource === 'string' ? resource.trim() : '';
        return RESOURCE_PATHS[key] || '';
    }

    function getCue(day, sceneId) {
        const normalizedDay = Number(day);
        const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
        const dayCues = CUES_BY_DAY[normalizedDay];
        return dayCues && normalizedSceneId ? (dayCues[normalizedSceneId] || null) : null;
    }

    return Object.freeze({
        getCue,
        getResourcePath,
        resources: RESOURCE_PATHS
    });
});

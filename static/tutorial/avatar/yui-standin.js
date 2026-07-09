(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else if (root) {
        root.YuiGuideAvatarStandIn = factory();
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DELAY_MS = 900;
    const DURATION_MS = 5000;

    function cue(position, delayMs) {
        return Object.freeze({
            delay: Number.isFinite(delayMs) ? delayMs : DELAY_MS,
            duration: DURATION_MS,
            position: position
        });
    }

    const CUES = Object.freeze({
        2: Object.freeze({
            day2_galgame_entry: cue('top-right')
        }),
        3: Object.freeze({
            day3_proactive_chat: cue('top-left')
        }),
        4: Object.freeze({
            day4_gaze_follow: cue('top-left'),
            day4_privacy_mode: cue('bottom-right')
        }),
        5: Object.freeze({}),
        6: Object.freeze({
            day6_plugin_dashboard: cue('bottom-right'),
            day6_agent_task_hud: cue('top-left')
        }),
        7: Object.freeze({})
    });

    function cloneCue(value) {
        return value ? Object.assign({}, value) : null;
    }

    function getCue(day, sceneId) {
        const dayCues = CUES[Number(day)] || null;
        if (!dayCues || !sceneId || !dayCues[sceneId]) {
            return null;
        }
        return cloneCue(dayCues[sceneId]);
    }

    function getAllCues() {
        const result = {};
        Object.keys(CUES).forEach((day) => {
            result[day] = {};
            Object.keys(CUES[day]).forEach((sceneId) => {
                result[day][sceneId] = cloneCue(CUES[day][sceneId]);
            });
        });
        return result;
    }

    return {
        DELAY_MS,
        DURATION_MS,
        getCue,
        getAllCues
    };
});

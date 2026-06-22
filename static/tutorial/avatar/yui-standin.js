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
    const DAY5_CHARACTER_SETTINGS_DELAY_MS = 2900;

    function cue(position, delayMs) {
        return Object.freeze({
            delay: Number.isFinite(delayMs) ? delayMs : DELAY_MS,
            duration: DURATION_MS,
            position: position
        });
    }

    const CUES = Object.freeze({
        2: Object.freeze({
            day2_intro_context: cue('bottom-right'),
            day2_proactive_chat: cue('top-left')
        }),
        3: Object.freeze({
            day3_avatar_tools: cue('bottom-left'),
            day3_galgame_entry: cue('top-right')
        }),
        4: Object.freeze({
            day4_gaze_follow: cue('top-left'),
            day4_privacy_mode: cue('bottom-right')
        }),
        5: Object.freeze({
            day5_character_settings: cue('top-right', DAY5_CHARACTER_SETTINGS_DELAY_MS)
        }),
        6: Object.freeze({
            day6_plugin_dashboard: cue('bottom-right'),
            day6_agent_task_hud: cue('top-left')
        }),
        7: Object.freeze({
            day7_memory_review: cue('bottom-left')
        })
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
        DAY5_CHARACTER_SETTINGS_DELAY_MS,
        getCue,
        getAllCues
    };
});

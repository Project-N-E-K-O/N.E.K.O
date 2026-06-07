(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }
    root.YuiGuideAvatarStandIn = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DELAY_MS = 900;
    const DURATION_MS = 5000;

    const CUES = Object.freeze({
        2: Object.freeze({
            day2_intro_context: cue('peek-head', 'bottom-right'),
            day2_proactive_chat: cue('peek-right-border', 'right-bottom')
        }),
        3: Object.freeze({
            day3_avatar_tools: cue('peek-left-border', 'left-bottom'),
            day3_galgame_choices: cue('peek-head', 'top-left-flipped')
        }),
        4: Object.freeze({
            day4_gaze_follow: cue('peek-head', 'bottom-right'),
            day4_return_home: cue('peek-left-border', 'left-bottom')
        }),
        5: Object.freeze({
            day5_character_settings: cue('peek-right-border', 'right-bottom'),
            day5_memory_entry: cue('peek-head', 'top-left-flipped')
        }),
        6: Object.freeze({
            day6_plugin_dashboard: cue('peek-right-border', 'right-bottom'),
            day6_wrap_cleanup: cue('peek-head', 'bottom-right')
        }),
        7: Object.freeze({
            day7_memory_review: cue('peek-head', 'bottom-right'),
            day7_memory_control: cue('peek-left-border', 'left-bottom')
        })
    });

    const RESOURCE_PATHS = Object.freeze({
        'peek-left-border': '/static/assets/tutorial/avatar-standins/peek-left-border.png',
        'peek-right-border': '/static/assets/tutorial/avatar-standins/peek-right-border.png',
        'peek-head': '/static/assets/tutorial/avatar-standins/peek-head.png'
    });

    function cue(resource, position) {
        return Object.freeze({
            delayMs: DELAY_MS,
            durationMs: DURATION_MS,
            resource,
            position
        });
    }

    function cloneCue(value) {
        if (!value) {
            return null;
        }
        return {
            delayMs: value.delayMs,
            durationMs: value.durationMs,
            resource: value.resource,
            position: value.position
        };
    }

    function getCue(day, sceneId) {
        const dayCues = CUES[String(Number(day))];
        if (!dayCues || !sceneId) {
            return null;
        }
        return cloneCue(dayCues[String(sceneId)] || null);
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

    function getResourcePath(resource) {
        return RESOURCE_PATHS[String(resource || '')] || '';
    }

    return {
        DELAY_MS,
        DURATION_MS,
        getCue,
        getAllCues,
        getResourcePath
    };
}));

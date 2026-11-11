/* Static registry for the built-in cat appearance and voice groups. */
(function installNekoCatResourceRegistry(global) {
    'use strict';

    const CAT_RESOURCE_GROUPS = Object.freeze({ dev_neko: 'dev_neko', yui_neko: 'yui_neko' });
    const normalizeGroupId = (value) => value === CAT_RESOURCE_GROUPS.yui_neko
        ? CAT_RESOURCE_GROUPS.yui_neko
        : CAT_RESOURCE_GROUPS.dev_neko;

    function readSessionGroupId() {
        try {
            return global.sessionStorage && global.sessionStorage.getItem('neko:cat-resource-group');
        } catch (_) {
            return null;
        }
    }

    // The desktop preload supplies the authoritative value before the page is
    // ready. Plain web pages use sessionStorage as a fallback for the one
    // reload used to apply a live settings change.
    const selectedAppearanceGroupId = normalizeGroupId(
        global.__nekoCatResourceGroup || readSessionGroupId()
    );
    const selectedVoiceGroupId = selectedAppearanceGroupId;

    // Union of opaque pixels across each animation's frames. Keep collision geometry
    // stable during playback, but select it by the actual asset, not cat tier.
    const appearanceBounds = {
        'idle/cat-idle-cat1.webp': [512, 512, 89, 49, 394, 493],
        'idle/cat-idle-cat2.webp': [512, 512, 22, 151, 491, 406],
        'idle/cat-idle-cat3.webp': [512, 512, 54, 140, 462, 461],
        'click/cat-idle-cat1-click.webp': [512, 512, 89, 48, 396, 494],
        'click/cat-idle-cat2-click.webp': [512, 512, 22, 141, 492, 406],
        'click/cat-idle-cat3-click.webp': [512, 512, 41, 141, 462, 460],
        'drag/cat-idle-cat-move-1.webp': [512, 512, 113, 1, 444, 507],
        'drag/cat-idle-cat-move-2.webp': [512, 512, 112, 2, 415, 497],
        'drag/cat-idle-cat-move-3.webp': [512, 512, 87, 7, 419, 508],
        'drag/cat-idle-cat-move-4.webp': [512, 512, 88, 7, 415, 501],
        'drag/cat-idle-cat-move-5.webp': [512, 512, 112, 3, 415, 497],
        'movement/cat-idle-cat4-1.gif': [512, 512, 26, 59, 501, 460],
        'movement/cat-idle-cat4-2.gif': [512, 512, 23, 59, 509, 460],
        'movement/cat-idle-cat4-3.gif': [512, 512, 17, 56, 487, 455],
        'action/cat-idle-cat1-eat.webp': [512, 512, 88, 49, 469, 494],
        'action/cat-idle-cat1-happy.webp': [512, 512, 83, 48, 406, 493],
        'action/cat-idle-cat1-touch.webp': [512, 512, 59, 0, 437, 510],
        'action/cat-idle-cat1-cos-1.webp': [512, 512, 2, 3, 495, 510],
        'action/cat-idle-cat1-change.webp': [512, 512, 33, 24, 501, 512],
        'action/cat-idle-cat1-cos-2.webp': [512, 512, 83, 41, 408, 498],
        'action/cat-idle-cat1-eat-2.webp': [512, 512, 27, 1, 401, 512],
        'action/cat-idle-cat1-eat-3.webp': [512, 512, 53, 46, 463, 493],
        'action/cat-idle-cat1-laugh.webp': [512, 512, 89, 46, 408, 501],
        'action/cat-idle-cat1-lion_dance.webp': [512, 704, 76, 4, 436, 687],
        'action/cat-idle-cat1-love-1.webp': [512, 512, 53, 49, 396, 496],
        'action/cat-idle-cat1-mouse.webp': [896, 512, 6, 42, 851, 508],
        'action/cat-idle-cat1-wave.webp': [512, 512, 80, 48, 399, 510],
        'action/cat-idle-cat1-sad.webp': [512, 512, 86, 54, 434, 493],
        'action/cat-idle-cat1-scared.webp': [512, 512, 27, 1, 434, 512],
        'action/cat-idle-cat1-stroke.webp': [512, 512, 86, 14, 435, 492],
        'action/cat-idle-cat1-wagging.webp': [512, 512, 0, 44, 400, 512],
        'action/cat-idle-cat-play-1.webp': [896, 512, 133, 44, 896, 486]
    };
    const appearanceArtLayouts = Object.freeze({
        'action/cat-idle-cat-play-1.webp': 'wide-centered',
        'action/cat-idle-cat1-mouse.webp': 'wide-centered',
        'action/cat-idle-cat1-lion_dance.webp': 'tall-bottom'
    });
    const appearanceDurationsMs = Object.freeze({
        'idle/cat-idle-cat1.webp': 7590,
        'idle/cat-idle-cat2.webp': 7410,
        'idle/cat-idle-cat3.webp': 8640,
        'click/cat-idle-cat1-click.webp': 2430,
        'click/cat-idle-cat2-click.webp': 4890,
        'click/cat-idle-cat3-click.webp': 3660,
        'drag/cat-idle-cat-move-1.webp': 13560,
        'drag/cat-idle-cat-move-2.webp': 6150,
        'drag/cat-idle-cat-move-3.webp': 7680,
        'drag/cat-idle-cat-move-4.webp': 6300,
        'drag/cat-idle-cat-move-5.webp': 6090,
        'action/cat-idle-cat1-eat.webp': 4170,
        'action/cat-idle-cat1-happy.webp': 4040,
        'action/cat-idle-cat1-touch.webp': 6040,
        'action/cat-idle-cat1-cos-1.webp': 4040,
        'action/cat-idle-cat1-change.webp': 6040,
        'action/cat-idle-cat1-cos-2.webp': 6040,
        'action/cat-idle-cat1-eat-2.webp': 5040,
        'action/cat-idle-cat1-eat-3.webp': 4040,
        'action/cat-idle-cat1-laugh.webp': 4040,
        'action/cat-idle-cat1-lion_dance.webp': 4040,
        'action/cat-idle-cat1-love-1.webp': 4040,
        'action/cat-idle-cat1-mouse.webp': 5040,
        'action/cat-idle-cat1-wave.webp': 4040,
        'action/cat-idle-cat1-sad.webp': 4040,
        'action/cat-idle-cat1-scared.webp': 4040,
        'action/cat-idle-cat1-stroke.webp': 7980,
        'action/cat-idle-cat1-wagging.webp': 4040,
        'action/cat-idle-cat-play-1.webp': 3900
    });
    const appearanceInsets = new Map();
    for (const [asset, [width, height, left, top, right, bottom]] of Object.entries(appearanceBounds)) {
        const artLayout = appearanceArtLayouts[asset] || 'normal';
        const boxWidth = artLayout === 'wide-centered' ? 1.75 : 1;
        const boxHeight = artLayout === 'tall-bottom' ? 1.375 : 1;
        const scale = Math.min(boxWidth / width, boxHeight / height);
        const x = (1 - width * scale) / 2;
        const y = artLayout === 'tall-bottom'
            ? 1 - height * scale
            : (1 - height * scale) / 2;
        const normal = Object.freeze({ left: x + left * scale, top: y + top * scale,
            right: 1 - (x + right * scale), bottom: 1 - (y + bottom * scale) });
        const mirrored = Object.freeze({ ...normal, left: normal.right, right: normal.left });
        appearanceInsets.set(`/static/assets/cat-resources/appearance/dev_neko/${asset}`, { normal, mirrored });
    }

    function getAppearanceVisibleInsets(url, options = {}) {
        const path = String(url || '').replace(/^https?:\/\/[^/]+/i, '').split(/[?#]/)[0];
        const entry = appearanceInsets.get(path);
        return entry ? (options.facingRight ? entry.mirrored : entry.normal) : null;
    }

    function getAppearanceDurationMs(url) {
        const path = String(url || '').replace(/^https?:\/\/[^/]+/i, '').split(/[?#]/)[0];
        const prefix = '/static/assets/cat-resources/appearance/dev_neko/';
        return path.startsWith(prefix) ? appearanceDurationsMs[path.slice(prefix.length)] || 0 : 0;
    }

    const appearanceGroups = {
        dev_neko: {
            'idle.cat1': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat1.webp'],
            'idle.cat2': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat2.webp'],
            'idle.cat3': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat3.webp'],
            'click.cat1': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat1-click.webp'],
            'click.cat2': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat2-click.webp'],
            'click.cat3': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat3-click.webp'],
            'drag.cat1': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-1.webp', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.webp'],
            'drag.cat2': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.webp', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.webp'],
            'drag.cat3': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.webp', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-4.webp'],
            'drag.rapid': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-5.webp'],
            'movement.cat1.walking': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-1.gif'],
            'movement.cat1.stretch': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-2.gif'],
            'movement.cat1.interactive': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-3.gif'],
            'action.cat1.eat': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat.webp'],
            'action.cat1.happy': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-happy.webp'],
            'action.cat1.touch': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-touch.webp'],
            'action.cat1.cosplay_1': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-cos-1.webp'],
            'action.cat1.change': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-change.webp'],
            'action.cat1.cosplay_2': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-cos-2.webp'],
            'action.cat1.eat_2': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat-2.webp'],
            'action.cat1.eat_3': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat-3.webp'],
            'action.cat1.laugh': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-laugh.webp'],
            'action.cat1.lion_dance': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-lion_dance.webp'], metadata: { artLayout: 'tall-bottom' } },
            'action.cat1.love_1': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-love-1.webp'],
            'action.cat1.mouse': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-mouse.webp'], metadata: { wideArt: true, artLayout: 'wide-centered' } },
            'action.cat1.wave': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-wave.webp'],
            'action.cat1.sad': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-sad.webp'],
            'action.cat1.scared': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-scared.webp'],
            'action.cat1.stroke': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-stroke.webp'],
            'action.cat1.wagging': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-wagging.webp'],
            'action.cat1.play_yarn': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-play-1.webp'], metadata: { wideArt: true } },
            'playground.cat1.air': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.webp']
        },
        yui_neko: {
            'idle.cat1': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat1.webp'],
            'idle.cat2': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat2.webp'],
            'idle.cat3': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat3.webp'],
            'click.cat1': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat1-click.webp'],
            'click.cat2': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat2-click.webp'],
            'click.cat3': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat3-click.webp'],
            'drag.cat1': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-1.webp', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.webp'],
            'drag.cat2': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.webp', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.webp'],
            'drag.cat3': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.webp', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-4.webp'],
            'drag.rapid': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-5.webp'],
            'movement.cat1.walking': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-1.gif'],
            'movement.cat1.stretch': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-2.gif'],
            'movement.cat1.interactive': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-3.gif'],
            'action.cat1.eat': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat.webp'],
            'action.cat1.happy': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-happy.webp'],
            'action.cat1.touch': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-touch.webp'],
            'action.cat1.cosplay_1': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-cos-1.webp'],
            'action.cat1.change': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-change.webp'],
            'action.cat1.cosplay_2': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-cos-2.webp'],
            'action.cat1.eat_2': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat-2.webp'],
            'action.cat1.eat_3': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat-3.webp'],
            'action.cat1.laugh': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-laugh.webp'],
            'action.cat1.lion_dance': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-lion_dance.webp'], metadata: { artLayout: 'tall-bottom' } },
            'action.cat1.love_1': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-love-1.webp'],
            'action.cat1.mouse': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-mouse.webp'], metadata: { wideArt: true, artLayout: 'wide-centered' } },
            'action.cat1.wave': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-wave.webp'],
            'action.cat1.sad': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-sad.webp'],
            'action.cat1.scared': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-scared.webp'],
            'action.cat1.stroke': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-stroke.webp'],
            'action.cat1.wagging': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-wagging.webp'],
            'action.cat1.play_yarn': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-play-1.webp'], metadata: { wideArt: true } },
            'playground.cat1.air': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.webp']
        }
    };

    const voiceGroups = {
        dev_neko: {
            'cat1.ambient': ['/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice1.mp3', '/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice2.mp3', '/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice3.mp3'],
            'cat1.drag': ['/static/assets/cat-resources/voice/dev_neko/interaction/cat1-voice-click.mp3'],
            'cat1.rapid_drag': ['/static/assets/cat-resources/voice/dev_neko/interaction/cat1-voice-funny.mp3'],
            'cat1.eat': ['/static/assets/cat-resources/voice/dev_neko/action/cat1-voice-eat.mp3'],
            'cat1.play_yarn': ['/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice3.mp3'],
            'cat1.hiss': ['/static/assets/cat-resources/voice/dev_neko/interaction/cat1-voice-chat-angry.mp3'],
            'cat2.sleep': ['/static/assets/cat-resources/voice/dev_neko/sleep/cat2-sleep1.mp3', '/static/assets/cat-resources/voice/dev_neko/sleep/cat2-sleep2.mp3'],
            'cat3.sleep': ['/static/assets/cat-resources/voice/dev_neko/sleep/cat3-sleep1.mp3', '/static/assets/cat-resources/voice/dev_neko/sleep/cat3-sleep2.mp3']
        },
        yui_neko: {
            'cat1.ambient': ['/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice1.mp3', '/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice2.mp3', '/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice3.mp3'],
            'cat1.drag': ['/static/assets/cat-resources/voice/dev_neko/interaction/cat1-voice-click.mp3'],
            'cat1.rapid_drag': ['/static/assets/cat-resources/voice/dev_neko/interaction/cat1-voice-funny.mp3'],
            'cat1.eat': ['/static/assets/cat-resources/voice/dev_neko/action/cat1-voice-eat.mp3'],
            'cat1.play_yarn': ['/static/assets/cat-resources/voice/dev_neko/ambient/cat1-voice3.mp3'],
            'cat1.hiss': ['/static/assets/cat-resources/voice/dev_neko/interaction/cat1-voice-chat-angry.mp3'],
            'cat2.sleep': ['/static/assets/cat-resources/voice/dev_neko/sleep/cat2-sleep1.mp3', '/static/assets/cat-resources/voice/dev_neko/sleep/cat2-sleep2.mp3'],
            'cat3.sleep': ['/static/assets/cat-resources/voice/dev_neko/sleep/cat3-sleep1.mp3', '/static/assets/cat-resources/voice/dev_neko/sleep/cat3-sleep2.mp3']
        }
    };

    const actionDependencies = {
        cat1_social_ping: { appearance: [], voice: ['cat1.ambient'] },
        cat1_eat_snack: {
            appearance: ['action.cat1.eat', 'action.cat1.eat_2', 'action.cat1.eat_3'],
            voice: ['cat1.eat']
        },
        cat1_small_move: { appearance: ['movement.cat1.walking'], voice: [] },
        cat1_play_yarn: { appearance: ['action.cat1.play_yarn'], voice: ['cat1.play_yarn'] },
        cat1_idle_random: {
            appearance: [
                'action.cat1.happy',
                'action.cat1.touch',
                'action.cat1.mouse',
                'action.cat1.laugh',
                'action.cat1.love_1',
                'action.cat1.wagging'
            ],
            voice: []
        },
        cat1_inactive_sad: { appearance: ['action.cat1.sad'], voice: [] },
        cat2_nap_feedback: { appearance: ['idle.cat2'], voice: ['cat2.sleep'] },
        cat3_sleep_feedback: { appearance: ['idle.cat3'], voice: ['cat3.sleep'] },
        cat1_happy: { appearance: ['action.cat1.happy'], voice: [] },
        cat1_touch: { appearance: ['action.cat1.touch'], voice: [] },
        cat1_cosplay_1: { appearance: ['action.cat1.cosplay_1'], voice: [] },
        cat1_change: { appearance: ['action.cat1.change'], voice: [] },
        cat1_cosplay_2: { appearance: ['action.cat1.cosplay_2'], voice: [] },
        cat1_eat_2: { appearance: ['action.cat1.eat_2'], voice: [] },
        cat1_eat_3: { appearance: ['action.cat1.eat_3'], voice: [] },
        cat1_laugh: { appearance: ['action.cat1.laugh'], voice: [] },
        cat1_lion_dance: { appearance: ['action.cat1.lion_dance'], voice: [] },
        cat1_love_1: { appearance: ['action.cat1.love_1'], voice: [] },
        cat1_mouse: { appearance: ['action.cat1.mouse'], voice: [] },
        cat1_wave: { appearance: ['action.cat1.wave'], voice: [] },
        cat1_sad: { appearance: ['action.cat1.sad'], voice: [] },
        cat1_scared: { appearance: ['action.cat1.scared'], voice: [] },
        cat1_stroke: { appearance: ['action.cat1.stroke'], voice: [] },
        cat1_wagging: { appearance: ['action.cat1.wagging'], voice: [] },
        cat1_hiss_stretch: { appearance: ['movement.cat1.stretch'], voice: ['cat1.hiss'] }
    };

    function readSlot(groups, groupId, slot, options = {}) {
        const entry = groups[groupId] && groups[groupId][slot];
        const urls = Array.isArray(entry) ? entry : (entry && entry.urls) || [];
        const metadata = (entry && !Array.isArray(entry) && entry.metadata) || {};
        let selectedUrl = null;
        if (urls.length) {
            if (options.random === false) {
                selectedUrl = urls[0];
            } else if (Number.isSafeInteger(options.seed)) {
                selectedUrl = urls[(options.seed >>> 0) % urls.length];
            } else {
                selectedUrl = urls[Math.floor(Math.random() * urls.length)];
            }
        }
        return {
            available: urls.length > 0,
            url: selectedUrl,
            urls: urls.slice(),
            groupId,
            metadata: Object.assign({}, metadata)
        };
    }

    function getAppearanceForGroup(groupId, slot, options) {
        return readSlot(appearanceGroups, normalizeGroupId(groupId), slot, options);
    }

    function getAppearance(slot, options) {
        return getAppearanceForGroup(selectedAppearanceGroupId, slot, options);
    }

    function normalizeResourcePath(value) {
        if (typeof value !== 'string' || !value) return '';
        try {
            return new URL(value, global.location && global.location.href).pathname;
        } catch (_) {
            return value.split(/[?#]/, 1)[0];
        }
    }

    function getAppearanceSlotForUrl(groupId, value) {
        const path = normalizeResourcePath(value);
        if (!path) return null;
        const group = appearanceGroups[normalizeGroupId(groupId)] || {};
        const slots = Object.keys(group);
        for (let index = 0; index < slots.length; index += 1) {
            const slot = slots[index];
            const entry = group[slot];
            const urls = Array.isArray(entry) ? entry : (entry && entry.urls) || [];
            if (urls.some((url) => normalizeResourcePath(url) === path)) return slot;
        }
        return null;
    }

    function getVoice(slot, options) {
        return readSlot(voiceGroups, selectedVoiceGroupId, slot, options);
    }

    // The settings window can change the group while Pet is open. Resource
    // URLs are intentionally fixed for one page lifetime, so reload once after
    // the new group arrives and let the preload provide it on the next load.
    if (typeof global.addEventListener === 'function') {
        global.addEventListener('neko:cat-resource-group', (event) => {
            const nextGroupId = normalizeGroupId(event && event.detail && event.detail.group);
            if (nextGroupId === selectedAppearanceGroupId) {
                // A previous reload may have failed. Keep the fallback state
                // and persisted value aligned before returning.
                global.__nekoCatResourceGroup = nextGroupId;
                try {
                    if (global.sessionStorage) global.sessionStorage.setItem('neko:cat-resource-group', nextGroupId);
                } catch (_) {}
                return;
            }
            global.__nekoCatResourceGroup = nextGroupId;
            let persisted = false;
            try {
                if (global.sessionStorage) {
                    global.sessionStorage.setItem('neko:cat-resource-group', nextGroupId);
                    persisted = true;
                }
            } catch (_) {}
            try {
                if (persisted && global.location && typeof global.location.reload === 'function') global.location.reload();
            } catch (_) {}
        });
    }

    function getActionCapabilities(actionId) {
        const dependencies = actionDependencies[actionId];
        if (!dependencies) return { available: false, reason: 'unknown_action' };
        // Capability checks must be read-only: do not consume the random
        // selection used by the actual runner when it later picks a URL.
        const appearance = dependencies.appearance.map((slot) => getAppearance(slot, { random: false }));
        const voice = dependencies.voice.map((slot) => getVoice(slot, { random: false }));
        const missingAppearance = appearance.some((item) => !item.available);
        const missingVoice = voice.some((item) => !item.available);
        return {
            available: !missingAppearance && !missingVoice,
            appearance,
            voice,
            reason: missingAppearance ? 'appearance_unavailable' : (missingVoice ? 'voice_unavailable' : null)
        };
    }

    global.NekoCatResourceRegistry = Object.freeze({
        selectedAppearanceGroupId,
        selectedVoiceGroupId,
        getAppearanceForGroup,
        getAppearanceSlotForUrl,
        getAppearance,
        getAppearanceVisibleInsets,
        getAppearanceDurationMs,
        getVoice,
        getActionCapabilities
    });
})(typeof window !== 'undefined' ? window : globalThis);

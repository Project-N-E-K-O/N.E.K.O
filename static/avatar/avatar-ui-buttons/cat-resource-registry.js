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

    // Union of opaque pixels across each GIF's frames. Keep collision geometry
    // stable during playback, but select it by the actual asset, not cat tier.
    const appearanceBounds = {
        'idle/cat-idle-cat1.gif': [512, 512, 89, 49, 394, 493],
        'idle/cat-idle-cat2.gif': [512, 512, 22, 151, 491, 406],
        'idle/cat-idle-cat3.gif': [512, 512, 54, 140, 462, 461],
        'click/cat-idle-cat1-click.gif': [512, 512, 89, 48, 396, 494],
        'click/cat-idle-cat2-click.gif': [512, 512, 22, 141, 492, 406],
        'click/cat-idle-cat3-click.gif': [512, 512, 41, 141, 462, 460],
        'drag/cat-idle-cat-move-1.gif': [512, 512, 113, 1, 444, 507],
        'drag/cat-idle-cat-move-2.gif': [512, 512, 112, 2, 415, 497],
        'drag/cat-idle-cat-move-3.gif': [512, 512, 87, 7, 419, 508],
        'drag/cat-idle-cat-move-4.gif': [512, 512, 88, 7, 415, 501],
        'drag/cat-idle-cat-move-5.gif': [512, 512, 112, 3, 415, 497],
        'movement/cat-idle-cat4-1.gif': [512, 512, 26, 59, 501, 460],
        'movement/cat-idle-cat4-2.gif': [512, 512, 23, 59, 509, 460],
        'movement/cat-idle-cat4-3.gif': [512, 512, 17, 56, 487, 455],
        'action/cat-idle-cat1-eat.gif': [512, 512, 88, 49, 469, 494],
        'action/cat-idle-cat-play-1.gif': [896, 512, 133, 44, 896, 486]
    };
    const appearanceInsets = new Map();
    for (const [asset, [width, height, left, top, right, bottom]] of Object.entries(appearanceBounds)) {
        // The wide play animation fills a 175% image box; other art fills 100%.
        const boxWidth = asset === 'action/cat-idle-cat-play-1.gif' ? 1.75 : 1;
        const scale = Math.min(boxWidth / width, 1 / height);
        const x = (1 - width * scale) / 2;
        const y = (1 - height * scale) / 2;
        const normal = Object.freeze({ left: x + left * scale, top: y + top * scale,
            right: x + (width - right) * scale, bottom: y + (height - bottom) * scale });
        const mirrored = Object.freeze({ ...normal, left: normal.right, right: normal.left });
        appearanceInsets.set(`/static/assets/cat-resources/appearance/dev_neko/${asset}`, { normal, mirrored });
    }

    function getAppearanceVisibleInsets(url, options = {}) {
        const path = String(url || '').replace(/^https?:\/\/[^/]+/i, '').split(/[?#]/)[0];
        const entry = appearanceInsets.get(path);
        return entry ? (options.facingRight ? entry.mirrored : entry.normal) : null;
    }

    const appearanceGroups = {
        dev_neko: {
            'idle.cat1': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat1.gif'],
            'idle.cat2': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat2.gif'],
            'idle.cat3': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat3.gif'],
            'click.cat1': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat1-click.gif'],
            'click.cat2': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat2-click.gif'],
            'click.cat3': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat3-click.gif'],
            'drag.cat1': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-1.gif', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.gif'],
            'drag.cat2': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.gif', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.gif'],
            'drag.cat3': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.gif', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-4.gif'],
            'drag.rapid': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-5.gif'],
            'movement.cat1.walking': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-1.gif'],
            'movement.cat1.stretch': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-2.gif'],
            'movement.cat1.interactive': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-3.gif'],
            'action.cat1.eat': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat.gif'],
            'action.cat1.play_yarn': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-play-1.gif'], metadata: { wideArt: true } },
            'playground.cat1.air': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.gif']
        },
        yui_neko: {
            'idle.cat1': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat1.gif'],
            'idle.cat2': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat2.gif'],
            'idle.cat3': ['/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat3.gif'],
            'click.cat1': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat1-click.gif'],
            'click.cat2': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat2-click.gif'],
            'click.cat3': ['/static/assets/cat-resources/appearance/dev_neko/click/cat-idle-cat3-click.gif'],
            'drag.cat1': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-1.gif', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.gif'],
            'drag.cat2': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.gif', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.gif'],
            'drag.cat3': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-3.gif', '/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-4.gif'],
            'drag.rapid': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-5.gif'],
            'movement.cat1.walking': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-1.gif'],
            'movement.cat1.stretch': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-2.gif'],
            'movement.cat1.interactive': ['/static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-3.gif'],
            'action.cat1.eat': ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-eat.gif'],
            'action.cat1.play_yarn': { urls: ['/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-play-1.gif'], metadata: { wideArt: true } },
            'playground.cat1.air': ['/static/assets/cat-resources/appearance/dev_neko/drag/cat-idle-cat-move-2.gif']
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
        cat1_eat_snack: { appearance: ['action.cat1.eat'], voice: ['cat1.eat'] },
        cat1_small_move: { appearance: ['movement.cat1.walking'], voice: [] },
        cat1_play_yarn: { appearance: ['action.cat1.play_yarn'], voice: ['cat1.play_yarn'] },
        cat2_nap_feedback: { appearance: ['idle.cat2'], voice: ['cat2.sleep'] },
        cat3_sleep_feedback: { appearance: ['idle.cat3'], voice: ['cat3.sleep'] },
        cat1_hiss_stretch: { appearance: ['movement.cat1.stretch'], voice: ['cat1.hiss'] }
    };

    function readSlot(groups, groupId, slot, options = {}) {
        const entry = groups[groupId] && groups[groupId][slot];
        const urls = Array.isArray(entry) ? entry : (entry && entry.urls) || [];
        const metadata = (entry && !Array.isArray(entry) && entry.metadata) || {};
        return {
            available: urls.length > 0,
            url: urls.length ? (options.random === false ? urls[0] : urls[Math.floor(Math.random() * urls.length)]) : null,
            urls: urls.slice(),
            groupId,
            metadata: Object.assign({}, metadata)
        };
    }

    function getAppearance(slot, options) {
        return readSlot(appearanceGroups, selectedAppearanceGroupId, slot, options);
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
        getAppearance,
        getAppearanceVisibleInsets,
        getVoice,
        getActionCapabilities
    });
})(typeof window !== 'undefined' ? window : globalThis);

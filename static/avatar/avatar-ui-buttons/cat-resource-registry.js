/* Static registry for the built-in cat appearance and voice groups. */
(function installNekoCatResourceRegistry(global) {
    'use strict';

    const selectedAppearanceGroupId = 'dev_neko';
    const selectedVoiceGroupId = 'dev_neko';

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
        getVoice,
        getActionCapabilities
    });
})(typeof window !== 'undefined' ? window : globalThis);

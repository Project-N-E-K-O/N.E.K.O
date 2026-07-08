(function () {
    'use strict';

    var DEFAULT_FAKE_LOADING_MS = 1100;

    function waitMs(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, Math.max(0, Number(ms) || 0));
        });
    }

    function dispatchThinking(active, source) {
        try {
            window.dispatchEvent(new CustomEvent('neko-focus-thinking', {
                detail: { active: active === true, source: String(source || '') }
            }));
        } catch (error) {
            console.warn('[NewUserIcebreaker] thinking indicator dispatch failed:', error);
        }
    }

    function showAssistantFakeLoading(options) {
        var config = options && typeof options === 'object' ? options : {};
        var session = config.session || null;
        var getActiveSession = typeof config.getActiveSession === 'function'
            ? config.getActiveSession
            : function () { return null; };
        var shouldRender = typeof config.shouldRender === 'function'
            ? config.shouldRender
            : function () { return false; };
        var waitForChatHost = typeof config.waitForChatHost === 'function'
            ? config.waitForChatHost
            : function () { return Promise.resolve(null); };
        var waitForMounted = typeof config.waitForMounted === 'function'
            ? config.waitForMounted
            : function () { return Promise.resolve(false); };
        var durationMs = Number(config.durationMs || DEFAULT_FAKE_LOADING_MS);
        var source = String(config.source || 'new_user_icebreaker');

        if (!session || getActiveSession() !== session || !shouldRender()) {
            return Promise.resolve(false);
        }
        var started = false;
        return waitForChatHost(30000).then(function (host) {
            if (!host) return false;
            if (typeof host.openWindow === 'function') {
                host.openWindow();
            }
            return waitForMounted(host).then(function () {
                if (getActiveSession() !== session) return false;
                started = true;
                dispatchThinking(true, source);
                return waitMs(durationMs).then(function () {
                    return true;
                });
            });
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] fake loading failed:', error);
            return false;
        }).then(function (result) {
            if (started) {
                dispatchThinking(false, source);
            }
            return result;
        });
    }

    window.NekoIcebreakerAssistantLoading = {
        DEFAULT_FAKE_LOADING_MS: DEFAULT_FAKE_LOADING_MS,
        waitMs: waitMs,
        dispatchThinking: dispatchThinking,
        showAssistantFakeLoading: showAssistantFakeLoading
    };
})();

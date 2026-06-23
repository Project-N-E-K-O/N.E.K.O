(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialVisualControllers = api;
        root.TutorialHighlightController = root.TutorialHighlightController || {
            createController: api.createHighlightController
        };
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    function loadHighlightControllerApi() {
        if (root && root.TutorialHighlightController) {
            return root.TutorialHighlightController;
        }
        if (typeof require === 'function') {
            try {
                return require('./highlight-controller.js');
            } catch (_) {}
        }
        return null;
    }

    function loadAvatarStandInControllerApi() {
        if (root && root.TutorialAvatarStandInController) {
            return root.TutorialAvatarStandInController;
        }
        if (typeof require === 'function') {
            try {
                return require('../avatar/standin-controller.js');
            } catch (_) {}
        }
        return null;
    }

    function loadSpotlightControllerApi() {
        if (root && root.TutorialSpotlightController) {
            return root.TutorialSpotlightController;
        }
        if (typeof require === 'function') {
            try {
                return require('./spotlight-controller.js');
            } catch (_) {}
        }
        return null;
    }

    function loadGhostCursorControllerApi() {
        if (root && root.TutorialGhostCursorController) {
            return root.TutorialGhostCursorController;
        }
        if (typeof require === 'function') {
            try {
                return require('./ghost-cursor-controller.js');
            } catch (_) {}
        }
        return null;
    }

    function loadPetalTransitionControllerApi() {
        if (root && root.TutorialPetalTransitionController) {
            return root.TutorialPetalTransitionController;
        }
        if (typeof require === 'function') {
            try {
                return require('./petal-transition-controller.js');
            } catch (_) {}
        }
        return null;
    }

    const highlightControllerApi = loadHighlightControllerApi();
    const ghostCursorControllerApi = loadGhostCursorControllerApi();
    const spotlightControllerApi = loadSpotlightControllerApi();
    const avatarStandInControllerApi = loadAvatarStandInControllerApi();
    const petalTransitionControllerApi = loadPetalTransitionControllerApi();
    const TutorialHighlightController = (
        highlightControllerApi
        && typeof highlightControllerApi.TutorialHighlightController === 'function'
    )
        ? highlightControllerApi.TutorialHighlightController
        : null;
    const createHighlightController = (
        highlightControllerApi
        && typeof highlightControllerApi.createHighlightController === 'function'
    )
        ? highlightControllerApi.createHighlightController
        : (highlightControllerApi && typeof highlightControllerApi.createController === 'function'
            ? highlightControllerApi.createController
            : null);
    const YuiGuideGhostCursor = (
        ghostCursorControllerApi
        && typeof ghostCursorControllerApi.YuiGuideGhostCursor === 'function'
    )
        ? ghostCursorControllerApi.YuiGuideGhostCursor
        : null;
    const GhostCursorController = (
        ghostCursorControllerApi
        && typeof ghostCursorControllerApi.GhostCursorController === 'function'
    )
        ? ghostCursorControllerApi.GhostCursorController
        : null;
    const SpotlightController = (
        spotlightControllerApi
        && typeof spotlightControllerApi.SpotlightController === 'function'
    )
        ? spotlightControllerApi.SpotlightController
        : null;
    const AvatarStandInController = (
        avatarStandInControllerApi
        && typeof avatarStandInControllerApi.AvatarStandInController === 'function'
    )
        ? avatarStandInControllerApi.AvatarStandInController
        : null;
    const PetalTransitionController = (
        petalTransitionControllerApi
        && typeof petalTransitionControllerApi.PetalTransitionController === 'function'
    )
        ? petalTransitionControllerApi.PetalTransitionController
        : null;

    return {
        TutorialHighlightController,
        createHighlightController,
        YuiGuideGhostCursor,
        GhostCursorController,
        SpotlightController,
        AvatarStandInController,
        PetalTransitionController
    };
});

(function (namespace) {
    'use strict';

    const YuiGuideDirector = namespace.YuiGuideDirector;

    window.createYuiGuideDirector = function createYuiGuideDirector(options) {
        return new YuiGuideDirector(options);
    };
})(window.__YuiGuideDirector);

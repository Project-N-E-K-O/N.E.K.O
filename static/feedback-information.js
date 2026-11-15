(function () {
    'use strict';
    const dialog = document.getElementById('feedback-information');
    dialog.showModal();
    // Leave a restored page usable without repeatedly interrupting it.
    window.addEventListener('pagehide', () => dialog.close(), { once: true });
}());

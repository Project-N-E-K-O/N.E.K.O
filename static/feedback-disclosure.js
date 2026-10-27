(function () {
    'use strict';
    const dialog = document.getElementById('feedback-disclosure');
    const openButton = document.getElementById('disclosure-open');
    const closeButton = document.getElementById('disclosure-close');
    const title = document.getElementById('disclosure-title');
    const start = document.getElementById('disclosure-start');
    const stop = document.getElementById('disclosure-stop');
    let mode = null, listening = false, hiddenPage = false;

    function copy(node, key) {
        node.dataset.i18n = 'feedbackDevices.' + key;
        // The translation bootstrap will translate the current data-i18n key
        // if a status snapshot arrives before window.t is ready.
        if (typeof window.t === 'function') node.textContent = window.t(node.dataset.i18n);
    }
    function render() {
        copy(start, mode === 1 ? 'disclosureStartAutomatic' : mode === 2 ? 'disclosureStartDevice' : 'disclosureStartUnknown');
        copy(stop, mode === 2 ? 'disclosureStopDevice' : mode === 1 ? 'disclosureStopAccount' : 'disclosureStopUnknown');
    }
    function receive(event) {
        const state = event.detail;
        // Retain only the capability needed by this explanation, not identity
        // or queue contents. A retired/failed read always returns to unknown.
        mode = state && !state.upgrade_required && [1, 2].includes(state.protocol_version) ? state.protocol_version : null;
        render();
    }
    function bind() {
        if (listening) return;
        listening = true;
        window.addEventListener('neko:feedback-status', receive);
        window.addEventListener('localechange', render);
    }
    function open() {
        if (!dialog.open) {
            dialog.showModal();
            title.focus({ preventScroll: true });
        }
    }
    openButton.addEventListener('click', open);
    closeButton.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        if (!hiddenPage) openButton.focus({ preventScroll: true });
    });
    window.addEventListener('pagehide', () => {
        hiddenPage = true;
        window.removeEventListener('neko:feedback-status', receive);
        window.removeEventListener('localechange', render);
        listening = false;
        mode = null;
        if (dialog.open) dialog.close();
    });
    window.addEventListener('pageshow', () => {
        hiddenPage = false;
        bind();
        render();
    });
    bind();
    render();
    open();
}());

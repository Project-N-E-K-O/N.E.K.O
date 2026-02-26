/**
 * Shared popup positioning utilities for Live2D/VRM.
 */
(function () {
    if (window.AvatarPopupUI) return;

    function toNumber(value, fallback = 0) {
        const n = Number.parseFloat(value);
        return Number.isFinite(n) ? n : fallback;
    }

    function resetPopupPosition(popup, options = {}) {
        const left = options.left || '100%';
        const top = options.top || '0';
        popup.style.left = left;
        popup.style.right = 'auto';
        popup.style.top = top;
        popup.style.marginLeft = '8px';
        popup.style.marginRight = '0';
    }

    function positionPopup(popup, options = {}) {
        const buttonId = options.buttonId;
        const buttonPrefix = options.buttonPrefix || 'live2d-btn-';
        const triggerPrefix = options.triggerPrefix || 'live2d-trigger-icon-';
        const rightMargin = Number.isFinite(options.rightMargin) ? options.rightMargin : 20;
        const bottomMargin = Number.isFinite(options.bottomMargin) ? options.bottomMargin : 60;
        const topMargin = Number.isFinite(options.topMargin) ? options.topMargin : 8;
        const gap = Number.isFinite(options.gap) ? options.gap : 8;

        const triggerIcon = document.querySelector(`.${triggerPrefix}${buttonId}`);
        const screenWidth = window.innerWidth;
        const screenHeight = window.innerHeight;
        let opensLeft = false;

        // Horizontal overflow handling.
        let popupRect = popup.getBoundingClientRect();
        if (popupRect.right > screenWidth - rightMargin) {
            const button = document.getElementById(`${buttonPrefix}${buttonId}`);
            const buttonWidth = button ? button.offsetWidth : 48;
            popup.style.left = 'auto';
            popup.style.right = '0';
            popup.style.marginLeft = '0';
            popup.style.marginRight = `${buttonWidth + gap}px`;
            opensLeft = true;
            if (triggerIcon) triggerIcon.style.transform = 'rotate(180deg)';
        } else {
            popup.style.left = popup.style.left || '100%';
            popup.style.right = 'auto';
            popup.style.marginLeft = `${gap}px`;
            popup.style.marginRight = '0';
            if (triggerIcon) triggerIcon.style.transform = 'rotate(0deg)';
        }

        popup.dataset.opensLeft = String(opensLeft);

        // Vertical overflow handling.
        popupRect = popup.getBoundingClientRect();
        const currentTop = toNumber(popup.style.top, 0);
        let nextTop = currentTop;
        if (popupRect.bottom > screenHeight - bottomMargin) {
            nextTop -= (popupRect.bottom - (screenHeight - bottomMargin));
        }
        popup.style.top = `${nextTop}px`;

        popupRect = popup.getBoundingClientRect();
        if (popupRect.top < topMargin) {
            popup.style.top = `${toNumber(popup.style.top, 0) + (topMargin - popupRect.top)}px`;
        }

        return { opensLeft };
    }

    window.AvatarPopupUI = {
        positionPopup,
        resetPopupPosition
    };
})();


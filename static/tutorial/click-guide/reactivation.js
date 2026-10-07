(function (root) {
    'use strict';
    let active = null;
    root.NekoTutorialReactivation = {
        open(save) {
            if (active) return active;
            const t = key => root.t?.('clickGuide.' + key) || key;
            const origin = document.activeElement;
            const wrapper = document.createElement('div');
            wrapper.className = 'click-guide-choice';
            for (const type of ['pointerdown', 'mousedown', 'touchstart', 'click']) {
                wrapper.addEventListener(type, event => event.stopPropagation(), { passive: true });
            }
            const card = document.createElement('section');
            card.className = 'click-guide-card';
            card.setAttribute('role', 'dialog');
            card.setAttribute('aria-modal', 'true');
            card.setAttribute('aria-label', t('choice.title'));
            const title = document.createElement('h2');
            title.textContent = t('choice.title');
            const description = document.createElement('p');
            description.textContent = t('choice.body');
            const actions = document.createElement('div');
            actions.className = 'click-guide-actions';
            card.append(title, description, actions);
            wrapper.append(card);
            document.body.append(wrapper);
            let saving = false;
            active = new Promise(resolve => {
                const close = choice => {
                    wrapper.remove();
                    origin?.focus();
                    resolve(choice);
                };
                for (const choice of ['click', 'seven-day', null]) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.textContent = t(choice === 'click' ? 'choice.click'
                        : choice === 'seven-day' ? 'choice.sevenDay' : 'close');
                    if (choice === 'click') button.className = 'click-guide-next';
                    button.onclick = async () => {
                        if (saving) return;
                        if (!choice) { close(null); return; }
                        saving = true;
                        [...actions.children].forEach(item => { item.disabled = true; });
                        let failed = false;
                        try {
                            await save(choice);
                            close(choice);
                        } catch (_) {
                            failed = true;
                            description.setAttribute('role', 'alert');
                            description.textContent = t('saveFailed');
                        } finally {
                            saving = false;
                            [...actions.children].forEach(item => { item.disabled = false; });
                            if (failed) button.focus();
                        }
                    };
                    actions.append(button);
                }
                wrapper.addEventListener('keydown', event => {
                    if (event.key === 'Escape' && !saving) close(null);
                    if (event.key === 'Tab') {
                        event.preventDefault();
                        const buttons = [...actions.children];
                        const index = buttons.indexOf(document.activeElement);
                        buttons[(index + (event.shiftKey ? 2 : 1)) % 3].focus();
                    }
                });
                actions.firstElementChild.focus();
            }).finally(() => { active = null; });
            return active;
        }
    };
})(window);

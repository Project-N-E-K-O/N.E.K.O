/* The Mao sample character is © Live2D Inc. Prints are drawn locally on a
 * transient copy of the user's loaded atlas. Only the choices are saved. */
(() => {
    'use strict';
    const panel = document.getElementById('live2d-wardrobe');
    const fields = panel.querySelector('fieldset');
    const hat = panel.querySelector('[name=hat]');
    const color = panel.querySelector('[name=color]');
    const print = panel.querySelector('[name=print]');
    const status = panel.querySelector('output');
    const defaults = {version: 1, wardrobe: 'mao-hoodie-v1', hat: true, color: '#ffffff', print: 'plain'};
    let active;
    window.live2dWardrobeSupportsModel = path => {
        const url = new URL(path, location.href);
        return url.origin === location.origin && url.pathname === '/static/mao_pro/mao_pro.model3.json';
    };

    function current(session) {
        return active === session && !session.model.destroyed && window.live2dManager.currentModel === session.model
            && window.lanlan_config?.lanlan_name === session.name;
    }

    function message(key) {
        status.textContent = key ? window.t('wardrobe.' + key) : '';
    }

    function printedAtlas(source, motif) {
        const canvas = document.createElement('canvas');
        canvas.width = source.realWidth;
        canvas.height = source.realHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(source.resource.source, 0, 0, canvas.width, canvas.height);
        // The print area lies inside ArtMesh135 (Mao's hoodie). UV Y points up.
        ctx.translate(canvas.width * .396, canvas.height * .78);
        ctx.scale(canvas.width / 4096, canvas.height / 4096);
        ctx.globalCompositeOperation = 'source-atop';
        ctx.fillStyle = '#374166';
        for (const x of [-30, 0, 30]) {
            ctx.beginPath();
            if (motif === 'dots') {
                ctx.arc(x, 0, 9, 0, Math.PI * 2);
            } else {
                for (let i = 0; i < 10; i++) {
                    const angle = i * Math.PI / 5 - Math.PI / 2;
                    const radius = i % 2 ? 6 : 14;
                    ctx.lineTo(x + radius * Math.cos(angle), radius * Math.sin(angle));
                }
                ctx.closePath();
            }
            ctx.fill();
        }
        return canvas;
    }

    function showChoices(outfit) {
        const choice = outfit || defaults;
        hat.checked = choice.hat;
        color.value = choice.color;
        print.value = choice.print;
    }

    function apply(session, outfit) {
        const manager = window.live2dManager;
        if (!outfit) {
            manager.clearOutfit(session.model);
            session.print = 'plain';
        } else {
            const rgb = [1, 3, 5].map(i => parseInt(outfit.color.slice(i, i + 2), 16) / 255);
            manager.setOutfitOverrides(session.model, {
                partOpacity: {PartHat: Number(outfit.hat)},
                colors: outfit.color.toLowerCase() === '#ffffff' ? [] : [{partId: 'PartHoodie', rgba: [...rgb, 1]}],
            });
            if (outfit.print !== session.print) {
                const atlas = outfit.print === 'plain' ? null : printedAtlas(session.source, outfit.print);
                manager.setOutfitTexture(session.model, 0, atlas);
                session.print = outfit.print;
            }
        }
    }

    window.addEventListener('live2d-model-ready', async ({detail}) => {
        active = undefined;
        panel.hidden = true;
        panel.open = false;
        fields.disabled = true;
        const name = window.lanlan_config?.lanlan_name;
        const model = window.live2dManager.currentModel;
        if (!name || !model || !window.live2dWardrobeSupportsModel(detail.modelPath)) return;
        const session = {model, name, url: '/api/characters/catgirl/' + encodeURIComponent(name) + '/live2d_outfit',
            source: model.textures[0].baseTexture, print: 'plain'};
        active = session;
        const detach = () => {
            if (active === session) { active = undefined; panel.hidden = true; }
        };
        model.once('destroy', detach);
        model.once('wardrobe-detached', detach);
        try {
            const response = await fetch(session.url, {cache: 'no-store'});
            if (!response.ok) throw Error('Unable to read outfit');
            const data = await response.json();
            if (!current(session) || data.wardrobe !== defaults.wardrobe) return;
            const o = data.outfit;
            if (o != null && (o.wardrobe !== defaults.wardrobe || o.version !== 1 ||
                typeof o.hat !== 'boolean' || !/^#[0-9a-f]{6}$/i.test(o.color) ||
                !['plain', 'stars', 'dots'].includes(o.print))) return;
            session.modelRef = data.model_ref;
            apply(session, o);
            showChoices(o);
            fields.disabled = false;
            message('');
            panel.hidden = false;
        } catch {
            if (current(session)) { panel.hidden = false; fields.disabled = true; message('loadError'); }
        }
    });

    fields.addEventListener('change', () => {
        if (!active || !current(active)) return;
        apply(active, {...defaults, hat: hat.checked, color: color.value, print: print.value});
        message('');
    });
    async function save(clear) {
        const session = active;
        if (!session || !current(session) || fields.disabled) return;
        fields.disabled = true;
        const outfit = clear ? null : {...defaults, hat: hat.checked, color: color.value, print: print.value};
        try {
            const response = await fetch(session.url, {method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model_ref: session.modelRef, outfit})});
            if (!response.ok) throw Error('Unable to save outfit');
            if (!current(session)) return;
            apply(session, outfit);
            showChoices(outfit);
            message('saved');
        } catch {
            if (current(session)) message('saveError');
        } finally {
            if (current(session)) fields.disabled = false;
        }
    }
    panel.querySelector('[name=save]').onclick = () => save(false);
    panel.querySelector('[name=clear]').onclick = () => save(true);
})();

// Part responsibility: card/list rendering, dissolve effects, and character detail-panel lifecycle.

let charaCardsViewMode = localStorage.getItem('charaCardsViewMode') || 'card';

// 切换视图
function switchCharaCardsView(mode) {
    if (charaCardsViewMode === mode) return;
    charaCardsViewMode = mode;
    localStorage.setItem('charaCardsViewMode', mode);
    // 更新按钮状态
    document.getElementById('chara-view-card-btn')?.classList.toggle('active', mode === 'card');
    document.getElementById('chara-view-list-btn')?.classList.toggle('active', mode === 'list');

    const container = document.getElementById('chara-cards-container');
    if (container) {
        container.style.opacity = '1';
        container.style.transform = 'none';
        renderCharaCardsView();
    } else {
        renderCharaCardsView();
    }
}
window.switchCharaCardsView = switchCharaCardsView;

// 搜索过滤
let _charaSearchQuery = '';

function filterCharaCards(query) {
    _charaSearchQuery = (query || '').trim().toLowerCase();
    renderCharaCardsView();
}
window.filterCharaCards = filterCharaCards;

// 渲染角色卡视图
function renderCharaCardsView() {
    const container = document.getElementById('chara-cards-container');
    if (!container) return;

    let cards = window.characterCards || [];

    // 应用搜索过滤
    const hiddenKeys = getHiddenCatgirlKeys();

    if (_charaSearchQuery) {
        cards = cards.filter(card => {
            const name = (card.originalName || card.name || '').toLowerCase();
            return name.includes(_charaSearchQuery);
        });
    }

    // 默认过滤掉隐藏的猫娘（除非开启显示已隐藏）
    if (!window._showHiddenCatgirls) {
        cards = cards.filter(card => !hiddenKeys.includes(card.originalName || card.name));
    }

    if (cards.length === 0) {
        const hiddenArea = container.querySelector('#hidden-catgirl-area');
        container.querySelectorAll('.chara-cards-grid, .chara-cards-list, .empty-state').forEach(el => el.remove());
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'empty-state';
        emptyDiv.innerHTML = '<p>' + (window.t ? window.t('steam.noCharacterCards') : '暂无角色卡') + '</p>';
        if (hiddenArea) {
            container.insertBefore(emptyDiv, hiddenArea);
        } else {
            container.appendChild(emptyDiv);
        }
        return;
    }

    const currentCatgirl = window._workshopCurrentCatgirl || '';

    if (charaCardsViewMode === 'card') {
        renderCharaCardsGrid(container, cards, currentCatgirl, hiddenKeys);
    } else {
        renderCharaCardsList(container, cards, currentCatgirl, hiddenKeys);
    }

    // 恢复按钮激活状态
    document.getElementById('chara-view-card-btn')?.classList.toggle('active', charaCardsViewMode === 'card');
    document.getElementById('chara-view-list-btn')?.classList.toggle('active', charaCardsViewMode === 'list');
}

function _ensureCharaCardParticleCanvas() {
    if (charaCardParticleCanvas) return;
    charaCardParticleCanvas = document.createElement('canvas');
    charaCardParticleCanvas.id = 'chara-card-particle-canvas';
    charaCardParticleCanvas.className = 'chara-card-particle-canvas';
    charaCardParticleCanvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(charaCardParticleCanvas);
    charaCardParticleContext = charaCardParticleCanvas.getContext('2d');

    if (!charaCardParticleResizeBound) {
        charaCardParticleResizeHandler = function () {
            if (!charaCardParticleCanvas || !charaCardParticleContext) return;
            const dpr = window.devicePixelRatio || 1;
            charaCardParticleCanvas.width = Math.max(1, Math.floor(window.innerWidth * dpr));
            charaCardParticleCanvas.height = Math.max(1, Math.floor(window.innerHeight * dpr));
            charaCardParticleCanvas.style.width = `${window.innerWidth}px`;
            charaCardParticleCanvas.style.height = `${window.innerHeight}px`;
            charaCardParticleContext.setTransform(dpr, 0, 0, dpr, 0, 0);
        };
        window.addEventListener('resize', charaCardParticleResizeHandler);
        charaCardParticleResizeBound = true;
        charaCardParticleResizeHandler();
    }
}

function _teardownCharaCardParticleCanvas() {
    if (!charaCardParticleCanvas) return;
    if (charaCardParticleResizeBound && charaCardParticleResizeHandler) {
        window.removeEventListener('resize', charaCardParticleResizeHandler);
    }
    window.cancelAnimationFrame(charaCardParticleFrame);
    charaCardParticleFrame = 0;
    if (charaCardParticleContext) {
        charaCardParticleContext.clearRect(0, 0, window.innerWidth, window.innerHeight);
    }
    if (charaCardParticleCanvas.parentNode) {
        charaCardParticleCanvas.parentNode.removeChild(charaCardParticleCanvas);
    }
    charaCardParticleCanvas = null;
    charaCardParticleContext = null;
    charaCardParticles = [];
    charaCardParticleResizeBound = false;
    charaCardParticleResizeHandler = null;
}

function _randomBetween(min, max) {
    return min + Math.random() * (max - min);
}

function _createCharaCardParticle(x, y, color, delay) {
    const angle = _randomBetween(-Math.PI * 0.9, -Math.PI * 0.1);
    const speed = _randomBetween(1, 3.8);
    charaCardParticles.push({
        x,
        y,
        vx: Math.cos(angle) * speed + _randomBetween(-0.4, 0.4),
        vy: Math.sin(angle) * speed - _randomBetween(0.2, 1.2),
        rotation: _randomBetween(0, Math.PI),
        spin: _randomBetween(-0.18, 0.18),
        size: _randomBetween(2.8, 6.4),
        life: 0,
        maxLife: _randomBetween(42, 76),
        delay: delay || 0,
        color,
        alpha: 1,
    });
}

function _spawnCharaCardParticles(target) {
    const rect = target.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const palette = ['#40c5f1', '#7fd9ff', '#ffffff', '#ff9eb5', '#ff6f8b', '#e3f4ff'];
    const particleCount = Math.min(CHARA_CARD_DISSOLVE_PARTICLE_LIMIT, Math.max(18, Math.floor(rect.width * rect.height / 180)));

    for (let i = 0; i < particleCount; i++) {
        _createCharaCardParticle(
            _randomBetween(rect.left + rect.width * 0.07, rect.right - rect.width * 0.07),
            _randomBetween(rect.top + rect.height * 0.05, rect.bottom - rect.height * 0.05),
            palette[Math.floor(Math.random() * palette.length)],
            _randomBetween(0, 22)
        );
    }
}

function _animateCharaCardParticles() {
    if (!charaCardParticleContext) return;
    charaCardParticleContext.clearRect(0, 0, window.innerWidth, window.innerHeight);

    charaCardParticles = charaCardParticles.filter(particle => {
        if (particle.delay > 0) {
            particle.delay -= 1;
            return true;
        }

        particle.life += 1;
        const progress = particle.life / particle.maxLife;
        particle.vy += 0.018;
        particle.vx *= 0.992;
        particle.x += particle.vx;
        particle.y += particle.vy;
        particle.rotation += particle.spin;
        particle.alpha = Math.max(0, 1 - progress);

        charaCardParticleContext.save();
        charaCardParticleContext.globalAlpha = particle.alpha;
        charaCardParticleContext.translate(particle.x, particle.y);
        charaCardParticleContext.rotate(particle.rotation);
        charaCardParticleContext.fillStyle = particle.color;
        charaCardParticleContext.shadowColor = 'rgba(64, 197, 241, 0.35)';
        charaCardParticleContext.shadowBlur = 7 * particle.alpha;
        charaCardParticleContext.fillRect(-particle.size / 2, -particle.size / 2, particle.size, particle.size);
        charaCardParticleContext.restore();

        return particle.life < particle.maxLife;
    });

    if (charaCardParticles.length) {
        charaCardParticleFrame = requestAnimationFrame(_animateCharaCardParticles);
    } else {
        cancelAnimationFrame(charaCardParticleFrame);
        charaCardParticleFrame = 0;
        _teardownCharaCardParticleCanvas();
    }
}

function _startCharaCardParticles() {
    if (!charaCardParticleCanvas) _ensureCharaCardParticleCanvas();
    if (!charaCardParticleFrame) {
        charaCardParticleFrame = requestAnimationFrame(_animateCharaCardParticles);
    }
}

function _wait(duration) {
    return new Promise(resolve => window.setTimeout(resolve, duration));
}

async function _dissolveCharaCardElement(target) {
    if (!(target && target.classList)) {
        return;
    }
    const runId = ++charaCardDissolveRunId;
    const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    target.style.pointerEvents = 'none';

    if (reduceMotion) {
        target.style.opacity = '0';
        target.style.visibility = 'hidden';
        return;
    }

    target.classList.add('is-dissolving');
    _ensureCharaCardParticleCanvas();
    _spawnCharaCardParticles(target);
    _startCharaCardParticles();

    await _wait(CHARA_CARD_DISSOLVE_DURATION);
    if (runId !== charaCardDissolveRunId) {
        target.classList.remove('is-dissolving');
        target.style.opacity = '0';
        target.style.visibility = 'hidden';
        target.style.pointerEvents = 'none';
        return;
    }
    target.classList.remove('is-dissolving');
    target.style.opacity = '0';
    target.style.visibility = 'hidden';
    target.style.pointerEvents = '';
}

async function _deleteCharaCardWithParticle(name, targetCardElement, triggerButton) {
    try {
        const deleted = await workshopDeleteCatgirl(name, { skipReload: true });
        if (!deleted) {
            if (triggerButton) triggerButton.disabled = false;
            return false;
        }

        if (targetCardElement) {
            await _dissolveCharaCardElement(targetCardElement);
        }

        try {
            await loadCharacterCards();
        } catch (error) {
            console.error('刷新角色卡列表失败:', error);
            if (targetCardElement && targetCardElement.parentNode) {
                targetCardElement.parentNode.removeChild(targetCardElement);
            } else if (triggerButton) {
                triggerButton.disabled = false;
            }
        }
        return true;
    } catch (error) {
        console.error('删除角色卡粒子消散流程失败:', error);
        if (triggerButton) triggerButton.disabled = false;
        return false;
    }
}

// 卡片视图渲染
function renderCharaCardsGrid(container, cards, currentCatgirl, hiddenKeys) {
    const grid = document.createElement('div');
    grid.className = 'chara-cards-grid';

    cards.forEach(card => {
        const name = card.originalName || card.name;
        const isCurrent = name === currentCatgirl;
        const isHidden = (hiddenKeys || []).includes(name);

        const item = document.createElement('div');
        item.className = 'chara-card-item' + (isCurrent ? ' active' : '') + (isHidden ? ' hidden-catgirl-card' : '');
        if (isHidden) item.style.opacity = '0.6';
        item.style.cursor = 'pointer';
        item.onclick = function (e) {
            if (e.target.closest('.card-action-btn') || e.target.closest('.card-hide-corner')) return;
            openCatgirlPanel(card, item);
        };

        // 左上角隐藏/显示按钮
        if (!isCurrent) {
            const cornerBtn = document.createElement('button');
            cornerBtn.className = 'card-hide-corner';
            cornerBtn.type = 'button';
            if (isHidden) {
                cornerBtn.title = window.t ? window.t('character.show') : '显示';
                cornerBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
                cornerBtn.onclick = function (e) {
                    e.stopPropagation();
                    workshopUnhideCatgirl(name);
                };
            } else {
                cornerBtn.title = window.t ? window.t('character.hideCatgirl') : '隐藏猫娘';
                cornerBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
                cornerBtn.onclick = function (e) {
                    e.stopPropagation();
                    workshopHideCatgirl(name);
                };
            }
            item.appendChild(cornerBtn);
        }

        // 角色卡图片
        const avatar = document.createElement('div');
        avatar.className = 'card-avatar';
        const placeholderSpan = document.createElement('span');
        placeholderSpan.className = 'card-avatar-placeholder';
        const translatedNoCardImage = window.t && window.t('steam.noCardImage');
        placeholderSpan.textContent = translatedNoCardImage && translatedNoCardImage !== 'steam.noCardImage'
            ? translatedNoCardImage
            : '暂未设置卡面';
        avatar.appendChild(placeholderSpan);

        // 加载已有的卡面图片（仅在服务器侧确实存在时才请求，避免 404 噪声）
        if (window._cardFaceNames && window._cardFaceNames.has(name)) {
            const avatarImg = document.createElement('img');
            avatarImg.className = 'card-face-img';
            avatarImg.alt = name;
            avatarImg.onload = () => {
                placeholderSpan.style.display = 'none';
                avatar.insertBefore(avatarImg, placeholderSpan);
            };
            avatarImg.src = `/api/characters/catgirl/${encodeURIComponent(name)}/card-face?t=${Date.now()}`;
        }

        item.appendChild(avatar);

        // 名称
        const nameDiv = document.createElement('div');
        nameDiv.className = 'card-name';
        nameDiv.textContent = name;
        item.appendChild(nameDiv);

        // 当前角色卡标记（胶囊 + 肇状图标）
        if (isCurrent) {
            const badge = document.createElement('span');
            badge.className = 'card-badge';
            badge.innerHTML = '<img src="/static/icons/paw_ui.png" class="card-badge-icon" alt="">'
                + '<span>' + (window.t ? window.t('character.currentCard') : '当前角色卡') + '</span>';
            item.appendChild(badge);
        }

        // 操作按钮
        const actionsRow = document.createElement('div');
        actionsRow.className = 'card-actions-row';

        const switchBtn = document.createElement('button');
        switchBtn.className = 'card-action-btn switch-btn';
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换该角色';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span>' + (window.t ? window.t('character.switchCard') : '切换该角色') + '</span>';
        switchBtn.disabled = isCurrent;
        switchBtn.onclick = function (e) {
            e.stopPropagation();
            workshopSwitchCatgirl(name);
        };
        actionsRow.appendChild(switchBtn);

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'card-action-btn delete-btn';
        deleteBtn.title = window.t ? window.t('character.deleteCard') : '删除角色卡';
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span>' + (window.t ? window.t('character.deleteCard') : '删除角色卡') + '</span>';
        deleteBtn.onclick = async function (e) {
            e.stopPropagation();
            if (deleteBtn.disabled) return;
            deleteBtn.disabled = true;
            const deleted = await _deleteCharaCardWithParticle(name, item, deleteBtn);
            if (!deleted) deleteBtn.disabled = false;
        };
        actionsRow.appendChild(deleteBtn);

        item.appendChild(actionsRow);
        grid.appendChild(item);
    });

    const hiddenArea = container.querySelector('#hidden-catgirl-area');
    container.querySelectorAll('.chara-cards-grid, .chara-cards-list, .empty-state').forEach(el => el.remove());
    if (hiddenArea) {
        container.insertBefore(grid, hiddenArea);
    } else {
        container.appendChild(grid);
    }
}

// 列表视图渲染
function renderCharaCardsList(container, cards, currentCatgirl, hiddenKeys) {
    const list = document.createElement('div');
    list.className = 'chara-cards-list';

    cards.forEach(card => {
        const name = card.originalName || card.name;
        const isCurrent = name === currentCatgirl;
        const isHidden = (hiddenKeys || []).includes(name);

        const item = document.createElement('div');
        item.className = 'chara-list-item' + (isCurrent ? ' active' : '') + (isHidden ? ' hidden-catgirl-item' : '');
        if (isHidden) item.style.opacity = '0.6';
        item.style.cursor = 'pointer';
        item.onclick = function (e) {
            if (e.target.closest('.list-action-btn')) return;
            openCatgirlPanel(card, item);
        };

        // 头像缩略图在列表视图中已移除（列表仅展示名称/状态/操作）

        // 名称
        const nameDiv = document.createElement('div');
        nameDiv.className = 'list-name';
        nameDiv.textContent = name;
        item.appendChild(nameDiv);

        // 当前角色卡标记
        if (isCurrent) {
            const badge = document.createElement('span');
            badge.className = 'list-badge';
            badge.innerHTML = '<img src="/static/icons/paw_ui.png" class="list-badge-icon" alt="">'
                + '<span>' + (window.t ? window.t('character.currentCard') : '当前角色卡') + '</span>';
            item.appendChild(badge);
        }

        // 操作按钮
        const actions = document.createElement('div');
        actions.className = 'list-actions';

        const switchBtn = document.createElement('button');
        switchBtn.className = 'list-action-btn switch-btn';
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换该角色';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span class="list-action-label">' + (window.t ? window.t('character.switchCard') : '切换该角色') + '</span>';
        switchBtn.disabled = isCurrent;
        switchBtn.onclick = function (e) {
            e.stopPropagation();
            workshopSwitchCatgirl(name);
        };
        actions.appendChild(switchBtn);

        if (isHidden) {
            const unhideBtn = document.createElement('button');
            unhideBtn.className = 'list-action-btn';
            unhideBtn.title = window.t ? window.t('character.show') : '显示';
            unhideBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
                + '<span class="list-action-label">' + (window.t ? window.t('character.show') : '显示') + '</span>';
            unhideBtn.onclick = function (e) {
                e.stopPropagation();
                workshopUnhideCatgirl(name);
            };
            actions.appendChild(unhideBtn);
        } else if (!isCurrent) {
            const hideBtn = document.createElement('button');
            hideBtn.className = 'list-action-btn';
            hideBtn.title = window.t ? window.t('character.hideCatgirl') : '隐藏猫娘';
            hideBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
                + '<span class="list-action-label">' + (window.t ? window.t('character.hideCatgirl') : '隐藏') + '</span>';
            hideBtn.onclick = function (e) {
                e.stopPropagation();
                workshopHideCatgirl(name);
            };
            actions.appendChild(hideBtn);
        }

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'list-action-btn delete-btn';
        deleteBtn.title = window.t ? window.t('character.deleteCard') : '删除角色卡';
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span class="list-action-label">' + (window.t ? window.t('character.deleteCard') : '删除角色卡') + '</span>';
        deleteBtn.onclick = async function (e) {
            e.stopPropagation();
            if (deleteBtn.disabled) return;
            deleteBtn.disabled = true;
            const deleted = await _deleteCharaCardWithParticle(name, item, deleteBtn);
            if (!deleted) deleteBtn.disabled = false;
        };
        actions.appendChild(deleteBtn);

        item.appendChild(actions);
        list.appendChild(item);
    });

    const hiddenArea = container.querySelector('#hidden-catgirl-area');
    container.querySelectorAll('.chara-cards-grid, .chara-cards-list, .empty-state').forEach(el => el.remove());
    if (hiddenArea) {
        container.insertBefore(list, hiddenArea);
    } else {
        container.appendChild(list);
    }
}

// ===== 角色卡详情面板 =====

let _catgirlPanelOpen = false;
const CATGIRL_PANEL_STEAM_COMPACT_WIDTH = 1280;
let _catgirlPanelSteamLayoutRaf = null;

function isCatgirlPanelSteamCompactWindow() {
    const width = window.innerWidth || document.documentElement.clientWidth || 0;
    return width > 0 && width < CATGIRL_PANEL_STEAM_COMPACT_WIDTH;
}

function refreshSteamPreviewAfterPanelLayoutChange() {
    requestAnimationFrame(function () {
        if (typeof buildPreviewRing === 'function') buildPreviewRing();
        if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
            const l2dContainer = document.getElementById('live2d-preview-content');
            if (l2dContainer && l2dContainer.clientWidth > 0 && l2dContainer.clientHeight > 0) {
                live2dPreviewManager.pixi_app.renderer.resize(l2dContainer.clientWidth, l2dContainer.clientHeight);
                if (live2dPreviewManager.currentModel) {
                    live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                    live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                }
            }
        }
        syncWorkshop3DPreviewSize(workshopVrmManager, 'vrm-preview-canvas');
        syncWorkshop3DPreviewSize(workshopMmdManager, 'mmd-preview-canvas');
    });
}

function updateCatgirlPanelSteamCardLayout(wrapper) {
    const panel = wrapper || document.getElementById('catgirl-panel-wrapper');
    if (!panel) return;

    const activeTab = panel.querySelector('.panel-tab.active');
    const shouldHideCardFace = !!(
        activeTab
        && activeTab.dataset.tab === 'steam'
        && isCatgirlPanelSteamCompactWindow()
    );
    const wasHidden = panel.classList.contains('steam-compact-card-hidden');
    panel.classList.toggle('steam-compact-card-hidden', shouldHideCardFace);
    const indicator = panel.querySelector('.panel-tabs-indicator');
    if (activeTab && indicator) {
        indicator.style.left = activeTab.offsetLeft + 'px';
        indicator.style.width = activeTab.offsetWidth + 'px';
    }
    const changed = wasHidden !== shouldHideCardFace;
    if (changed) {
        setTimeout(refreshSteamPreviewAfterPanelLayoutChange, 430);
    }
}

function scheduleCatgirlPanelSteamCardLayoutUpdate() {
    if (_catgirlPanelSteamLayoutRaf) cancelAnimationFrame(_catgirlPanelSteamLayoutRaf);
    _catgirlPanelSteamLayoutRaf = requestAnimationFrame(function () {
        _catgirlPanelSteamLayoutRaf = null;
        updateCatgirlPanelSteamCardLayout();
    });
}

window.addEventListener('resize', scheduleCatgirlPanelSteamCardLayoutUpdate);

function openCatgirlPanel(card, originEl) {
    if (_catgirlPanelOpen) return;
    _catgirlPanelOpen = true;

    const name = card ? (card.originalName || card.name) : null;
    const rawData = card ? (card.rawData || {}) : {};
    const isNew = !name;

    // 创建遮罩层
    const overlay = document.createElement('div');
    overlay.className = 'catgirl-panel-overlay';
    overlay.onclick = function (e) {
        if (e.target === overlay) closeCatgirlPanel();
    };

    // 创建面板容器
    const wrapper = document.createElement('div');
    wrapper.className = 'catgirl-panel-wrapper card-only';
    wrapper.id = 'catgirl-panel-wrapper';
    if (name) wrapper.dataset.catgirlName = name;

    // 设置动画起点
    if (originEl) {
        const rect = originEl.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        wrapper.style.transformOrigin = cx + 'px ' + cy + 'px';
    }

    // 左侧：卡片预览
    const leftSection = document.createElement('div');
    leftSection.className = 'catgirl-panel-left';

    const cardImage = document.createElement('div');
    cardImage.className = 'catgirl-panel-card-image';
    const imgPlaceholder = document.createElement('span');
    imgPlaceholder.className = 'card-avatar-placeholder';
    const translatedNoCardImage = window.t && window.t('steam.noCardImage');
    imgPlaceholder.textContent = translatedNoCardImage && translatedNoCardImage !== 'steam.noCardImage'
        ? translatedNoCardImage
        : '暂未设置卡面';
    cardImage.appendChild(imgPlaceholder);

    const cardActionOverlay = document.createElement('div');
    cardActionOverlay.className = 'catgirl-panel-card-actions';
    const modelSettingsAction = document.createElement('button');
    modelSettingsAction.type = 'button';
    modelSettingsAction.className = 'catgirl-panel-card-action';
    modelSettingsAction.textContent = window.t ? window.t('character.cardFaceModelSettings') : '模型设置';
    const editCardFaceAction = document.createElement('button');
    editCardFaceAction.type = 'button';
    editCardFaceAction.className = 'catgirl-panel-card-action';
    editCardFaceAction.textContent = window.t ? window.t('character.editCardFace') : '编辑卡面';
    cardActionOverlay.appendChild(modelSettingsAction);
    cardActionOverlay.appendChild(editCardFaceAction);
    cardImage.appendChild(cardActionOverlay);

    // 加载已有的卡面图片（仅在服务器侧确实存在时才请求，避免 404 噪声）
    if (name && window._cardFaceNames && window._cardFaceNames.has(name)) {
        const cardFaceUrl = `/api/characters/catgirl/${encodeURIComponent(name)}/card-face`;
        const img = document.createElement('img');
        img.className = 'card-face-img';
        img.alt = '角色卡面';
        img.onload = () => {
            imgPlaceholder.style.display = 'none';
            cardImage.insertBefore(img, imgPlaceholder);
        };
        img.src = cardFaceUrl + '?t=' + Date.now();
    }

    const openCardMaker = async () => {
        // 优先使用表单中当前填写的档案名（新建猫娘可能已临时保存）
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        const currentName = getProfileNameFromCharacterForm(form, name);
        if (!currentName) {
            await showProfileNameRequiredDialog(
                'character.fillProfileNameFirstForCardFace',
                '请先填写猫娘档案名，然后再设置卡面'
            );
            return;
        }
        const nameInput = form?.querySelector?.('[name="档案名"]');
        if (form && form._isNew && !form._autoCreated) {
            if (!(await ensureValidCharacterProfileName(currentName, nameInput))) {
                return;
            }
        } else if (!(await ensureSafeExistingCharacterPathName(currentName, nameInput))) {
            return;
        }
        const makerUrl = `/card_maker?name=${encodeURIComponent(currentName)}&mode=maker`;
        openManagedPopup(makerUrl, CHARACTER_MANAGER_CARD_MAKER_WINDOW_NAME, 'width=1200,height=800');
    };

    const openCardModelManager = async () => {
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        await openModelManagerForCharacterForm(form, name);
    };

    // 点击卡面主体打开模型管理；编辑卡面按钮仍进入角色卡制作页面。
    cardImage.addEventListener('click', async (event) => {
        if (event.target.closest('.catgirl-panel-card-action')) return;
        await openCardModelManager();
    });
    editCardFaceAction.addEventListener('click', (event) => {
        event.stopPropagation();
        openCardMaker();
    });
    modelSettingsAction.addEventListener('click', async (event) => {
        event.stopPropagation();
        await openCardModelManager();
    });

    // 监听角色卡制作页面的保存消息
    const onCardFaceMessage = (event) => {
        if (event.origin !== window.location.origin) return;
        // 获取当前实际的档案名（新建猫娘时 name 为 null，需要从表单读取）
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        const currentName = form?.querySelector('[name="档案名"]')?.value || name;
        if (!currentName) return;

        if (event.data && event.data.type === 'card-face-updated' && event.data.name === currentName) {
            applyCardFaceUpdated(currentName, event.data.timestamp);
        }
    };
    window.addEventListener('message', onCardFaceMessage);
    // 面板关闭时清理监听器（利用MutationObserver）
    const panelCleanupObserver = new MutationObserver(() => {
        if (!document.contains(cardImage)) {
            window.removeEventListener('message', onCardFaceMessage);
            panelCleanupObserver.disconnect();
        }
    });
    panelCleanupObserver.observe(document.body, { childList: true, subtree: true });

    leftSection.appendChild(cardImage);

    // === 卡面信息 ===
    const metaBlock = document.createElement('div');
    metaBlock.className = 'card-meta-block';
    metaBlock.id = 'card-meta-block';
    leftSection.appendChild(metaBlock);
    renderCardMetaBlock(metaBlock, name, isNew, rawData);

    // === 角色卡操作按钮（仅已存在的猫娘） ===
    if (!isNew && name) {
        const actions = document.createElement('div');
        actions.className = 'card-panel-actions';

        const exportBtn = document.createElement('button');
        exportBtn.type = 'button';
        exportBtn.className = 'card-panel-action-btn export-btn';
        exportBtn.title = window.t ? window.t('character.exportCardOnly') : '导出角色卡';
        exportBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
            + '<span>' + (window.t ? window.t('character.exportCardOnly') : '导出') + '</span>';
        exportBtn.onclick = function (e) {
            e.stopPropagation();
            exportCharacterCard(name);
        };
        actions.appendChild(exportBtn);

        const isCurrentChara = (window._workshopCurrentCatgirl || '') === name;

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'card-panel-action-btn delete-btn' + (isCurrentChara ? ' disabled' : '');
        deleteBtn.title = isCurrentChara
            ? (window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡')
            : (window.t ? window.t('character.deleteCard') : '删除角色卡');
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span>' + (window.t ? window.t('character.deleteCard') : '删除') + '</span>';
        deleteBtn.onclick = async function (e) {
            e.stopPropagation();
            // 不再用打开面板时快照的 isCurrentChara 拦截——workshopDeleteCatgirl 内部会用权威当前角色名做判断，
            // 这样跨窗口切换、用户取消、后端拒绝等情况都会被正确处理，且只有真正删除成功后才关面板，避免提前关掉丢未保存改动
            const deleted = await workshopDeleteCatgirl(name);
            if (deleted) {
                closeCatgirlPanel();
            }
        };
        actions.appendChild(deleteBtn);

        leftSection.appendChild(actions);
    }

    wrapper.appendChild(leftSection);

    // 右侧：编辑表单
    const rightSection = document.createElement('div');
    rightSection.className = 'catgirl-panel-right';

    // === 面板标题栏 ===
    const headerBar = document.createElement('div');
    headerBar.className = 'panel-header-bar';

    const tabsContainer = document.createElement('div');
    tabsContainer.className = 'panel-tabs';

    // 滑动指示器
    const indicator = document.createElement('div');
    indicator.className = 'panel-tabs-indicator';
    tabsContainer.appendChild(indicator);

    // 设定标签
    const settingsTab = document.createElement('button');
    settingsTab.type = 'button';
    settingsTab.className = 'panel-tab active';
    settingsTab.dataset.tab = 'settings';
    const settingsIcon = document.createElement('img');
    settingsIcon.src = '/static/icons/set_on.png';
    settingsIcon.className = 'panel-tab-icon';
    settingsIcon.alt = '';
    settingsTab.appendChild(settingsIcon);
    settingsTab.appendChild(document.createTextNode(window.t ? window.t('character.settings') : '设定'));
    tabsContainer.appendChild(settingsTab);

    if (!isNew) {
        // Steam 标签
        const steamTab = document.createElement('button');
        steamTab.type = 'button';
        steamTab.className = 'panel-tab';
        steamTab.dataset.tab = 'steam';
        const steamIcon = document.createElement('img');
        steamIcon.src = '/static/icons/Steam_icon_logo.png';
        steamIcon.className = 'panel-tab-icon';
        steamIcon.alt = '';
        steamTab.appendChild(steamIcon);
        steamTab.appendChild(document.createTextNode('Steam'));
        tabsContainer.appendChild(steamTab);
    }

    headerBar.appendChild(tabsContainer);

    // 关闭按钮（统一样式）
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'panel-close-btn';
    closeBtn.title = window.t ? window.t('common.close') : '关闭';
    const closeBtnImg = document.createElement('img');
    closeBtnImg.src = '/static/icons/close_button.png';
    closeBtnImg.alt = window.t ? window.t('common.close') : '关闭';
    closeBtnImg.draggable = false;
    closeBtn.appendChild(closeBtnImg);
    closeBtn.onclick = closeCatgirlPanel;
    headerBar.appendChild(closeBtn);

    rightSection.appendChild(headerBar);

    // === 设定标签内容 ===
    const settingsContent = document.createElement('div');
    settingsContent.className = 'panel-tab-content panel-tab-settings active';
    buildCatgirlDetailForm(name, rawData, isNew, settingsContent);
    rightSection.appendChild(settingsContent);

    // === Steam 标签内容 ===
    if (!isNew) {
        const steamContent = document.createElement('div');
        steamContent.className = 'panel-tab-content panel-tab-steam';
        rightSection.appendChild(steamContent);

        // 标签切换逻辑（含滑动指示器 + 幕布转场）
        const updateIndicator = function () {
            const activeTab = tabsContainer.querySelector('.panel-tab.active');
            if (activeTab && indicator) {
                indicator.style.left = activeTab.offsetLeft + 'px';
                indicator.style.width = activeTab.offsetWidth + 'px';
            }
        };

        // 幕布转场特效
        const CURTAIN_SCATTER_ICONS = [
            '/static/icons/star.png',
            '/static/icons/paw_ui.png',
            '/static/icons/star.png',
            '/static/icons/paw_ui.png'
        ];
        const spawnCurtainTransition = function (targetTabName, reverse, fullPanel) {
            const curtain = document.createElement('div');
            curtain.className = 'panel-transition-curtain'
                + (reverse ? ' curtain-reverse' : '')
                + (fullPanel ? ' full-panel' : '');

            // 幕布色块
            const sweep = document.createElement('div');
            sweep.className = 'curtain-sweep';
            curtain.appendChild(sweep);

            // 散落小图标（跟着幕布走）
            for (let i = 0; i < 10; i++) {
                const icon = document.createElement('img');
                icon.className = 'curtain-icon';
                icon.src = CURTAIN_SCATTER_ICONS[i % CURTAIN_SCATTER_ICONS.length];
                const size = 18 + Math.random() * 20;
                icon.style.width = size + 'px';
                icon.style.height = size + 'px';
                icon.style.top = (5 + Math.random() * 85) + '%';
                icon.style.left = (5 + Math.random() * 85) + '%';
                icon.style.animationDelay = (0.15 + i * 0.04) + 's';
                sweep.appendChild(icon);
            }

            // 中央大图标 — 根据目标标签页显示不同图标
            const centerIcon = document.createElement('img');
            centerIcon.className = 'curtain-center-icon';
            if (targetTabName === 'steam') {
                centerIcon.src = '/static/icons/Steam_icon_logo.png';
                centerIcon.style.width = '72px';
                centerIcon.style.height = '72px';
                centerIcon.style.background = 'white';
                centerIcon.style.borderRadius = '50%';
                centerIcon.style.padding = '4px';
                centerIcon.style.boxShadow = '0 4px 16px rgba(0,100,200,0.25)';
            } else {
                centerIcon.src = '/static/icons/set_on.png';
                centerIcon.style.width = '64px';
                centerIcon.style.height = '64px';
            }
            centerIcon.style.animationDelay = '0.18s';
            curtain.appendChild(centerIcon);

            const curtainHost = fullPanel ? wrapper : rightSection;
            curtainHost.appendChild(curtain);
            setTimeout(function () { curtain.remove(); }, 900);
        };

        let _tabSwitching = false;

        // 初始化指示器位置（等 DOM 渲染后）
        requestAnimationFrame(updateIndicator);

        headerBar.querySelectorAll('.panel-tab').forEach(tab => {
            tab.addEventListener('click', function () {
                if (_tabSwitching) return;
                const targetTab = this.dataset.tab;
                const currentActive = rightSection.querySelector('.panel-tab-content.active');
                const targetClass = 'panel-tab-' + targetTab;
                const target = rightSection.querySelector('.' + targetClass);
                if (!target || target === currentActive) return;

                // 计算动画方向：点击位于当前激活 tab 左侧的则反向动画
                const allTabs = Array.from(headerBar.querySelectorAll('.panel-tab'));
                const currentActiveTabBtn = headerBar.querySelector('.panel-tab.active');
                const currentIdx = currentActiveTabBtn ? allTabs.indexOf(currentActiveTabBtn) : -1;
                const targetIdx = allTabs.indexOf(this);
                const reverseDirection = (currentIdx >= 0 && targetIdx >= 0 && targetIdx < currentIdx);
                const needsFullPanelCurtain = wrapper.classList.contains('steam-compact-card-hidden')
                    || (targetTab === 'steam' && isCatgirlPanelSteamCompactWindow());

                _tabSwitching = true;
                headerBar.querySelectorAll('.panel-tab').forEach(t => t.classList.remove('active'));
                this.classList.add('active');
                updateIndicator();

                // 小窗口 Steam 切换会联动左侧卡面，幕布先覆盖整个面板再更新布局。
                spawnCurtainTransition(targetTab, reverseDirection, needsFullPanelCurtain);
                updateCatgirlPanelSteamCardLayout(wrapper);

                // 根据当前激活状态切换设定齿轮图标 on/off
                if (settingsIcon) {
                    settingsIcon.src = (targetTab === 'settings')
                        ? '/static/icons/set_on.png'
                        : '/static/icons/set_off.png';
                }

                // 退出当前页 — absolute定位防止撑高容器
                if (currentActive) {
                    currentActive.classList.remove('active');
                    currentActive.classList.add('tab-exit');
                    if (reverseDirection) currentActive.classList.add('tab-reverse');
                }

                // 幕布扫过中央时切入新页
                setTimeout(function () {
                    if (currentActive) {
                        currentActive.classList.remove('tab-exit');
                        currentActive.classList.remove('tab-reverse');
                    }
                    target.classList.add('active', 'tab-enter');
                    if (reverseDirection) target.classList.add('tab-reverse');

                    // Steam 标签变为可见后，强制刷新模型预览尺寸并重新计算模型位置
                    if (targetTab === 'steam') {
                        requestAnimationFrame(function () {
                            // Live2D resize + 重新应用模型设置
                            if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
                                const l2dContainer = document.getElementById('live2d-preview-content');
                                if (l2dContainer && l2dContainer.clientWidth > 0 && l2dContainer.clientHeight > 0) {
                                    live2dPreviewManager.pixi_app.renderer.resize(l2dContainer.clientWidth, l2dContainer.clientHeight);
                                    // 重新计算模型缩放和位置（修复在隐藏标签中加载导致的0尺寸问题）
                                    if (live2dPreviewManager.currentModel) {
                                        live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                                        if (live2dPreviewManager.pixi_app && live2dPreviewManager.pixi_app.renderer) {
                                            live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                                        }
                                    }
                                }
                            }
                            // VRM / MMD resize：同步 renderer、camera 和 OutlineEffect，避免只改 canvas 尺寸导致 3D 预览横向变形。
                            syncWorkshop3DPreviewSize(workshopVrmManager, 'vrm-preview-canvas');
                            syncWorkshop3DPreviewSize(workshopMmdManager, 'mmd-preview-canvas');
                        });
                    }

                    // 入场动画结束后清理class
                    setTimeout(function () {
                        target.classList.remove('tab-enter');
                        target.classList.remove('tab-reverse');
                        _tabSwitching = false;
                    }, 460);
                }, 320);
            });
        });
    } else {
        // 单标签模式也初始化指示器
        requestAnimationFrame(function () {
            if (indicator && settingsTab) {
                indicator.style.left = settingsTab.offsetLeft + 'px';
                indicator.style.width = settingsTab.offsetWidth + 'px';
            }
        });
    }

    wrapper.appendChild(rightSection);

    overlay.appendChild(wrapper);
    document.body.appendChild(overlay);
    updateCatgirlPanelSteamCardLayout(wrapper);

    // 动画 Phase 1: 卡面移动到中间
    requestAnimationFrame(() => {
        overlay.classList.add('active');
        wrapper.classList.add('phase-center');
        // Phase 2: 展开右侧表单
        setTimeout(() => {
            wrapper.classList.remove('phase-center');
            wrapper.classList.add('phase-expand');

            // 在展开动画刚开始时立即测量并调整 textarea 高度，
            // 这样多行内容（>3 行）的输入框在展开过程中就直接呈现出
            // 「带滚动条+左下圆角」的最终形态，不再出现展开后才变化的延迟感。
            // 因为 phase-expand 仅做 opacity / translateX 过渡（宽度已是终态），
            // textarea 的 scrollHeight 已可正确测量。
            const _resizeAllPanelTextareas = () => {
                const settingsForm = rightSection.querySelector('form');
                if (!settingsForm) return;
                settingsForm.querySelectorAll('textarea').forEach(ta => {
                    ta.style.height = 'auto';
                    const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 20;
                    const maxHeight = lineHeight * 3 + 10;
                    const scrollHeight = ta.scrollHeight;
                    ta.style.height = Math.min(scrollHeight, maxHeight) + 'px';
                    const fieldRow = ta.closest('.field-row');
                    if (fieldRow) {
                        if (scrollHeight > maxHeight) {
                            ta.style.overflowY = 'auto';
                            fieldRow.classList.add('has-scrollbar');
                        } else {
                            ta.style.overflowY = 'hidden';
                            fieldRow.classList.remove('has-scrollbar');
                        }
                    }
                });
            };
            // 双 rAF 等一次 layout flush，再做测量
            requestAnimationFrame(() => requestAnimationFrame(_resizeAllPanelTextareas));
            // 兜底：动画结束后再测量一次（处理字体延迟加载等情况）
            setTimeout(_resizeAllPanelTextareas, 500);

            // 延迟初始化 Steam 标签页内容（等待面板展开动画完成后）
            // 用 overlay 持有 timer id，关闭时统一 clearTimeout，避免在 closing 期间重建预览
            if (!isNew) {
                const steamInitTimer = setTimeout(() => {
                    overlay._steamTabInitTimer = null;
                    if (overlay.dataset.closing === 'true' || !overlay.isConnected) return;
                    const steamContainer = rightSection.querySelector('.panel-tab-steam');
                    if (steamContainer && !steamContainer.dataset.initialized) {
                        steamContainer.dataset.initialized = 'true';
                        buildSteamTabContent(name, rawData, card, steamContainer);
                    }
                }, 500);
                overlay._steamTabInitTimer = steamInitTimer;
            }
        }, 500);
    });
}
window.openCatgirlPanel = openCatgirlPanel;

function openNewCatgirlPanel() {
    openCatgirlPanel(null, null);
}
window.openNewCatgirlPanel = openNewCatgirlPanel;

function buildCreatedCatgirlPanelActions(name) {
    const actions = document.createElement('div');
    actions.className = 'card-panel-actions';

    const exportBtn = document.createElement('button');
    exportBtn.type = 'button';
    exportBtn.className = 'card-panel-action-btn export-btn';
    exportBtn.title = window.t ? window.t('character.exportCardOnly') : '导出角色卡';
    exportBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
        + '<span>' + (window.t ? window.t('character.exportCardOnly') : '导出') + '</span>';
    exportBtn.onclick = function (e) {
        e.stopPropagation();
        exportCharacterCard(name);
    };
    actions.appendChild(exportBtn);

    const isCurrentChara = (window._workshopCurrentCatgirl || '') === name;

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'card-panel-action-btn delete-btn' + (isCurrentChara ? ' disabled' : '');
    deleteBtn.title = isCurrentChara
        ? (window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡')
        : (window.t ? window.t('character.deleteCard') : '删除角色卡');
    deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
        + '<span>' + (window.t ? window.t('character.deleteCard') : '删除') + '</span>';
    deleteBtn.onclick = async function (e) {
        e.stopPropagation();
        const deleted = await workshopDeleteCatgirl(name);
        if (deleted) {
            closeCatgirlPanel();
        }
    };
    actions.appendChild(deleteBtn);

    return actions;
}

async function rollbackAutoCreatedCatgirl(form, targetName = '') {
    if (!form) return;
    const tempNames = Array.from(new Set(
        (targetName
            ? [targetName]
            : [form._autoCreatedName, form._autoCreatedDetachedName]
        ).filter(Boolean)
    ));
    if (!tempNames.length) return;
    const deletedNames = [];
    try {
        for (const tempName of tempNames) {
            const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(tempName), {
                method: 'DELETE'
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                console.warn('[角色面板] 回滚临时角色失败:', tempName, errData.error || resp.statusText);
                continue;
            }
            deletedNames.push(tempName);
            if (window._cardFaceNames) window._cardFaceNames.delete(tempName);
            if (window._cardMetas) delete window._cardMetas[tempName];
        }
        if (!deletedNames.length) return;
        if (deletedNames.includes(form._autoCreatedName)) {
            form._autoCreated = false;
            form._autoCreatedName = '';
        }
        if (deletedNames.includes(form._autoCreatedDetachedName)) {
            form._autoCreatedDetachedName = '';
        }
        if (!form._autoCreatedName && !form._autoCreatedDetachedName) {
            form._autoCreatedRollbackWhenDependentCloses = false;
            form._autoCreatedDependentPopupSaved = false;
        }
        if (typeof loadCharacterCards === 'function') {
            loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
        }
    } catch (e) {
        console.warn('[角色面板] 回滚临时角色请求失败:', tempNames.join(', '), e);
    }
}

function hasOpenAutoCreatedDependentPopup(form) {
    const popup = form && form._autoCreatedDependentPopup;
    return !!(popup && !popup.closed);
}

async function closeCatgirlPanel() {
    const overlay = document.querySelector('.catgirl-panel-overlay');
    if (!overlay) return;
    if (overlay.dataset.closing === 'true') return;
    overlay.dataset.closing = 'true';

    // 详情面板被显式关闭：companion 是绑在「这一次卡片编辑会话」上的助手，会话结束就
    // 跟着收掉。关键安全点（Codex #3328901017）：打开/切换到「别的卡」必须先走到这里把
    // 当前面板关掉（openCatgirlPanel 顶部有 _catgirlPanelOpen 互斥 guard，开新面板前
    // 一定先 closeCatgirlPanel）。所以在这里直接 teardown+destroy companion，就从根上
    // 杜绝了「A 的聊天被 _companionEnsureLiveForm 的选择器回退误绑到下一张打开的卡 B、
    // 后续 action/autosave 改错卡」。合法的 in-place rebuild（改档案名字段后
    // saveCatgirlFromPanel 重建、新卡首存 popup 被拦走 rebuildSavedCatgirlPanel）都不经过
    // closeCatgirlPanel，companion 不受影响、照常跟随。
    if (window._cardCompanion) {
        _companionTeardown(window._cardCompanion);
        _companionDestroy(window._cardCompanion);
        window._cardCompanion = null;
    }

    const currentForm = overlay.querySelector('form');
    if (currentForm && currentForm._voiceSelectCleanup) {
        currentForm._voiceSelectCleanup();
        delete currentForm._voiceSelectCleanup;
    }
    if (currentForm && currentForm._characterPersonalityUpdateHandler) {
        window.removeEventListener('neko:character-personality-updated', currentForm._characterPersonalityUpdateHandler);
        delete currentForm._characterPersonalityUpdateHandler;
    }
    // _autoCreatedDependentPopupSaved 由 500ms 轮询置位，存在 popup 已关到 timer 下次触发之间的窗口；
    // 同时直接读 popup._modelManagerHasSaved 兜底，避免误把刚保存好的临时角色回滚掉
    const dependentPopupForCheck = currentForm && currentForm._autoCreatedDependentPopup;
    const dependentSaved = !!(currentForm && (
        currentForm._autoCreatedDependentPopupSaved
        || (dependentPopupForCheck && dependentPopupForCheck._modelManagerHasSaved)
    ));
    if (!dependentSaved && hasOpenAutoCreatedDependentPopup(currentForm)) {
        currentForm._autoCreatedRollbackWhenDependentCloses = true;
    } else if (!dependentSaved) {
        await rollbackAutoCreatedCatgirl(currentForm);
    }

    // 取消所有预览加载：包括尚未完成的 Live2D/VRM/MMD 异步加载，避免清理后又把预览建回来
    if (typeof cancelWorkshopPreviewLoads === 'function') {
        cancelWorkshopPreviewLoads();
    } else if (typeof cancelPendingLive2DPreviewLoads === 'function') {
        cancelPendingLive2DPreviewLoads();
    }

    // 取消尚未触发的 Steam 标签页延迟初始化，避免在清理后又把预览建回来
    if (overlay._steamTabInitTimer) {
        clearTimeout(overlay._steamTabInitTimer);
        overlay._steamTabInitTimer = null;
    }

    // 清理模型预览资源（如果 Steam 标签页曾加载过）；清理完成后再执行收起动画
    try {
        const cleanupTasks = [];
        if (typeof disposeWorkshopVrm === 'function') cleanupTasks.push(disposeWorkshopVrm());
        if (typeof disposeWorkshopMmd === 'function') cleanupTasks.push(disposeWorkshopMmd());
        if (typeof destroyLive2DPreviewContext === 'function') cleanupTasks.push(destroyLive2DPreviewContext());
        await Promise.allSettled(cleanupTasks);
    } catch (e) {
        console.warn('[Panel] 清理预览资源时出错:', e);
    }

    const wrapper = overlay.querySelector('.catgirl-panel-wrapper');
    if (wrapper) {
        wrapper.classList.remove('phase-expand');
        wrapper.classList.add('phase-center');
    }

    await new Promise(resolve => setTimeout(resolve, 300));
    overlay.classList.remove('active');
    if (wrapper) wrapper.classList.remove('phase-center');
    await new Promise(resolve => setTimeout(resolve, 400));
    overlay.remove();
    _catgirlPanelOpen = false;
}
window.closeCatgirlPanel = closeCatgirlPanel;

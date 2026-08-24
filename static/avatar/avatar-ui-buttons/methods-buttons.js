(function setupSocialUnlockState() {
    const STORAGE_KEY = 'neko.social.unlock.v1';
    const LOCK_DAYS = 3;
    const DAY_MS = 24 * 60 * 60 * 1000;
    let midnightTimer = null;
    let localeListenerBound = false;
    let tutorialReadyRefreshBound = false;
    let tutorialLoadListenerBound = false;
    let fallbackFirstSeenDate = null;
    let lastPublishedUnlockStatus = '';

    function getLocalDateKey(date = new Date()) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function parseDateKey(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
            return null;
        }
        const [year, month, day] = value.split('-').map(Number);
        const date = new Date(year, month - 1, day);
        if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
            return null;
        }
        return { year, month, day };
    }

    function getCalendarDayDelta(firstSeenDate, todayDate) {
        const first = parseDateKey(firstSeenDate);
        const today = parseDateKey(todayDate);
        if (!first || !today) return 0;
        const firstUtc = Date.UTC(first.year, first.month - 1, first.day);
        const todayUtc = Date.UTC(today.year, today.month - 1, today.day);
        return Math.max(0, Math.floor((todayUtc - firstUtc) / DAY_MS));
    }

    function getEarlierDateKey(left, right) {
        const parsedLeft = parseDateKey(left);
        const parsedRight = parseDateKey(right);
        if (!parsedLeft) return parsedRight ? right : null;
        if (!parsedRight) return left;
        const leftUtc = Date.UTC(parsedLeft.year, parsedLeft.month - 1, parsedLeft.day);
        const rightUtc = Date.UTC(parsedRight.year, parsedRight.month - 1, parsedRight.day);
        return leftUtc <= rightUtc ? left : right;
    }

    function readTutorialState() {
        const tutorialApi = window.NekoSevenDayTutorialState;
        if (!tutorialApi || typeof tutorialApi.loadState !== 'function') return null;
        try {
            const state = tutorialApi.loadState({ persistMigration: false });
            return state && typeof state === 'object' ? state : null;
        } catch (_) {
            return null;
        }
    }

    function isExistingTutorialUser(state) {
        if (!state) return false;
        const settledRounds = new Set([
            ...(Array.isArray(state.completedRounds) ? state.completedRounds : []),
            ...(Array.isArray(state.skippedRounds) ? state.skippedRounds : [])
        ].map(Number));
        const roundCount = Number(window.NekoSevenDayTutorialState?.ROUND_COUNT) || 7;
        for (let round = 1; round <= roundCount; round += 1) {
            if (!settledRounds.has(round)) return false;
        }
        return true;
    }

    function getUnlockedDateKey(todayDate) {
        const unlockedDate = new Date(todayDate);
        unlockedDate.setDate(unlockedDate.getDate() - LOCK_DAYS);
        return getLocalDateKey(unlockedDate);
    }

    function publishUnlockStatus(status) {
        if (!status || typeof window.dispatchEvent !== 'function'
            || typeof window.CustomEvent !== 'function') return;
        const detail = {
            firstSeenDate: status.firstSeenDate,
            unlocked: status.unlocked === true,
            existingUser: status.existingUser === true
        };
        const signature = `${detail.firstSeenDate}|${detail.unlocked ? 1 : 0}|${detail.existingUser ? 1 : 0}`;
        if (signature === lastPublishedUnlockStatus) return;
        try {
            window.dispatchEvent(new window.CustomEvent('neko-social-unlock-status', { detail }));
            lastPublishedUnlockStatus = signature;
        } catch (_) {
            // Electron preload 同步只是共享门控的镜像；失败不能影响喵宇宙入口本身。
        }
    }

    function readUnlockEvidence(todayDate) {
        const today = getLocalDateKey(todayDate);
        const tutorialState = readTutorialState();
        const tutorialFirstSeenDate = tutorialState && parseDateKey(tutorialState.firstSeenDate)
            ? tutorialState.firstSeenDate
            : null;
        const existingUser = isExistingTutorialUser(tutorialState);
        let storedFirstSeenDate = null;
        try {
            const stored = window.localStorage && window.localStorage.getItem(STORAGE_KEY);
            if (parseDateKey(stored)) storedFirstSeenDate = stored;
        } catch (_) {
            // localStorage may be unavailable in private or restricted contexts.
        }

        let firstSeenDate = getEarlierDateKey(storedFirstSeenDate, tutorialFirstSeenDate);
        if (!firstSeenDate) firstSeenDate = fallbackFirstSeenDate || today;
        if (existingUser && getCalendarDayDelta(firstSeenDate, today) < LOCK_DAYS) {
            // 七日教程会把升级用户迁移为“全部轮次已结算”。将这个结论固化到
            // 社交入口自己的状态里，避免教程被重置后老用户又重新充能。
            firstSeenDate = getUnlockedDateKey(todayDate);
        }
        fallbackFirstSeenDate = firstSeenDate;

        if (storedFirstSeenDate !== firstSeenDate) {
            try {
                if (window.localStorage) window.localStorage.setItem(STORAGE_KEY, firstSeenDate);
            } catch (_) {
                // localStorage may be unavailable in private or restricted contexts.
            }
        }
        return { firstSeenDate, existingUser };
    }

    function getStatus(todayDate = new Date()) {
        const evidence = readUnlockEvidence(todayDate);
        const firstSeenDate = evidence.firstSeenDate;
        const today = getLocalDateKey(todayDate);
        const dayDelta = getCalendarDayDelta(firstSeenDate, today);
        const status = {
            firstSeenDate,
            dayDelta,
            remainingDays: Math.max(0, LOCK_DAYS - dayDelta),
            unlocked: evidence.existingUser || dayDelta >= LOCK_DAYS
        };
        publishUnlockStatus({ ...status, existingUser: evidence.existingUser });
        return status;
    }

    function getTitle(status) {
        if (!status.unlocked) {
            return window.t
                ? window.t('buttons.socialCharging', { days: status.remainingDays })
                : `喵宇宙充能中 ${status.remainingDays}天后再回来看看吧`;
        }
        return window.t ? window.t('buttons.social') : '喵宇宙';
    }

    function applyButtonState(btn, imgOff, imgOn, status = getStatus()) {
        if (!btn) return status;
        const locked = !status.unlocked;
        const title = getTitle(status);
        btn.dataset.socialButton = 'true';
        btn.dataset.socialLocked = locked ? 'true' : 'false';
        btn.title = title;
        btn.setAttribute('aria-disabled', locked ? 'true' : 'false');
        btn.removeAttribute('data-i18n-title');

        if (imgOff) imgOff.alt = title;
        if (imgOn) imgOn.alt = title;

        if (locked) {
            btn.style.cursor = 'default';
            btn.style.filter = 'grayscale(1)';
            btn.style.opacity = '0.58';
            btn.style.background = 'rgba(128, 128, 128, 0.45)';
            btn.style.border = 'var(--neko-btn-border, 1px solid rgba(255, 255, 255, 0.18))';
            btn.style.boxShadow = '0 2px 4px rgba(0,0,0,0.04)';
            btn.style.transform = 'scale(1)';
            if (imgOff && imgOn) {
                imgOff.style.opacity = '0.75';
                imgOn.style.opacity = '0';
            }
        } else {
            btn.style.cursor = 'pointer';
            btn.style.filter = '';
            btn.style.opacity = '';
            btn.style.background = 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';
            btn.style.border = 'var(--neko-btn-border, 1px solid rgba(255, 255, 255, 0.18))';
            btn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
        }
        return status;
    }

    function refreshButtons() {
        const status = getStatus();
        document.querySelectorAll('[data-social-button="true"]').forEach((btn) => {
            const images = btn.querySelectorAll('img');
            applyButtonState(btn, images[0], images[1], status);
        });
        if (status.unlocked && midnightTimer) {
            clearTimeout(midnightTimer);
            midnightTimer = null;
        }
        return status;
    }

    function scheduleNextMidnight() {
        if (midnightTimer) clearTimeout(midnightTimer);
        const nextMidnight = new Date();
        nextMidnight.setHours(24, 0, 0, 50);
        midnightTimer = setTimeout(() => {
            refreshButtons();
            if (!getStatus().unlocked) scheduleNextMidnight();
        }, Math.max(1000, nextMidnight.getTime() - Date.now()));
    }

    function bindTutorialReadyRefresh() {
        if (tutorialReadyRefreshBound) return true;
        const tutorialApi = window.NekoSevenDayTutorialState;
        if (!tutorialApi || typeof tutorialApi.ready !== 'function') return false;
        tutorialReadyRefreshBound = true;
        Promise.resolve(tutorialApi.ready()).then(refreshButtons, refreshButtons);
        return true;
    }

    window.nekoSocialUnlock = window.nekoSocialUnlock || {
        getStatus,
        isUnlocked: (todayDate) => getStatus(todayDate).unlocked,
        isLocked: (todayDate) => !getStatus(todayDate).unlocked,
        applyButtonState,
        refreshButtons,
        registerButton: (btn, imgOff, imgOn) => {
            const status = applyButtonState(btn, imgOff, imgOn);
            if (!localeListenerBound) {
                window.addEventListener('localechange', refreshButtons);
                localeListenerBound = true;
            }
            if (!bindTutorialReadyRefresh() && !tutorialLoadListenerBound) {
                window.addEventListener('load', () => {
                    bindTutorialReadyRefresh();
                    refreshButtons();
                }, { once: true });
                tutorialLoadListenerBound = true;
            }
            if (!status.unlocked && !midnightTimer) scheduleNextMidnight();
            return status;
        }
    };
})();

Object.assign(AvatarButtonMixin.methods, {
    buttons(ManagerPrototype, prefix, options) {
        ManagerPrototype.getDefaultButtonConfigs = function() {
            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : `?v=${Date.now()}`;
            return [
                {
                    id: 'mic',
                    emoji: '🎤',
                    title: window.t ? window.t('buttons.voiceControl') : '语音控制',
                    titleKey: 'buttons.voiceControl',
                    hasPopup: true,
                    toggle: true,
                    separatePopupTrigger: true,
                    iconOff: `/static/icons/mic_icon_off.png${iconVersion}`,
                    iconOn: `/static/icons/mic_icon_on.png${iconVersion}`
                },
                {
                    // 手机布局不渲染麦克风设置 trigger，因此保留一个可直接触发
                    // 屏幕分享的入口；桌面仍通过语音设置里的开关控制。
                    id: 'screen',
                    title: window.t ? window.t('buttons.screenShare') : '屏幕分享',
                    titleKey: 'buttons.screenShare',
                    hasPopup: false,
                    toggle: true,
                    mobileOnly: true,
                    iconOff: `/static/icons/screen_icon_off.png${iconVersion}`,
                    iconOn: `/static/icons/screen_icon_on.png${iconVersion}`
                },
                {
                    id: 'agent',
                    emoji: '🔨',
                    title: window.t ? window.t('buttons.agentTools') : 'Agent工具',
                    titleKey: 'buttons.agentTools',
                    hasPopup: true,
                    popupToggle: true,
                    exclusive: 'settings',
                    iconOff: `/static/icons/Agent_off.png${iconVersion}`,
                    iconOn: `/static/icons/Agent_on.png${iconVersion}`
                },
                {
                    // N.E.K.O.Servers 社交平台入口（替代桌面 screen 槽位）。
                    id: 'social',
                    title: window.t ? window.t('buttons.social') : '喵宇宙',
                    titleKey: 'buttons.social',
                    hasPopup: false,
                    iconOff: `/static/icons/neko_community_off.png${iconVersion}`,
                    iconOn: `/static/icons/neko_community_on.png${iconVersion}`,
                    imageRendering: 'auto'
                },
                {
                    id: 'settings',
                    emoji: '⚙️',
                    title: window.t ? window.t('buttons.settings') : '设置',
                    titleKey: 'buttons.settings',
                    hasPopup: true,
                    popupToggle: true,
                    exclusive: 'agent',
                    iconOff: `/static/icons/set_off.png${iconVersion}`,
                    iconOn: `/static/icons/set_on.png${iconVersion}`
                },
                {
                    id: 'goodbye',
                    emoji: '💤',
                    title: window.t ? window.t('buttons.leave') : '请她离开',
                    titleKey: 'buttons.leave',
                    hasPopup: false,
                    iconOff: `/static/icons/rest_off.png${iconVersion}`,
                    iconOn: `/static/icons/rest_on.png${iconVersion}`
                }
            ];
        };

        /**
         * 创建单个按钮及其包装器
         */
        ManagerPrototype.createButtonElement = function(config, buttonsContainer, index) {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;

            // 创建包装器
            const btnWrapper = document.createElement('div');
            if (config.mobileOnly) {
                btnWrapper.dataset.mobileOnly = 'true';
            }
            Object.assign(btnWrapper.style, {
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                pointerEvents: 'auto',
                height: '48px',
                minHeight: '48px',
                flex: '0 0 48px',
                boxSizing: 'border-box'
            });

            const stopWrapperEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                btnWrapper.addEventListener(evt, stopWrapperEvent);
            });

            // 创建按钮
            const btn = document.createElement('div');
            btn.id = `${prefix}-btn-${config.id}`;
            btn.className = opts.buttonClassPrefix;
            btn.title = config.title;
            if (config.titleKey) {
                btn.setAttribute('data-i18n-title', config.titleKey);
            }

            let imgOff = null;
            let imgOn = null;

            // 创建按钮内容（图片或 emoji）
            if (config.iconOff && config.iconOn) {
                const imgContainer = document.createElement('div');
                Object.assign(imgContainer.style, {
                    position: 'absolute',
                    inset: '-1px',
                    boxSizing: 'border-box',
                    pointerEvents: 'none'
                });

                imgOff = document.createElement('img');
                imgOff.src = config.iconOff;
                imgOff.alt = config.title;
                Object.assign(imgOff.style, {
                    position: 'absolute',
                    inset: '0',
                    width: '100%',
                    height: '100%',
                    objectFit: 'contain',
                    display: 'block',
                    pointerEvents: 'none',
                    opacity: '0.75',
                    transition: 'opacity 0.3s ease',
                    imageRendering: config.imageRendering || 'auto'
                });

                imgOn = document.createElement('img');
                imgOn.src = config.iconOn;
                imgOn.alt = config.title;
                Object.assign(imgOn.style, {
                    position: 'absolute',
                    inset: '0',
                    width: '100%',
                    height: '100%',
                    objectFit: 'contain',
                    display: 'block',
                    pointerEvents: 'none',
                    opacity: '0',
                    transition: 'opacity 0.3s ease',
                    imageRendering: config.imageRendering || 'auto'
                });

                imgContainer.appendChild(imgOff);
                imgContainer.appendChild(imgOn);
                btn.appendChild(imgContainer);
            } else if (config.emoji) {
                btn.innerText = config.emoji;
            }

            // 按钮样式
            Object.assign(btn.style, {
                width: '48px',
                height: '48px',
                boxSizing: 'border-box',
                position: 'relative',
                borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255, 255, 255, 0.18))',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                cursor: 'pointer',
                userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'all 0.1s ease',
                pointerEvents: 'auto'
            });

            if (config.id === 'social' && window.nekoSocialUnlock) {
                // 捕获阶段拦截，保证各渲染器稍后注册的 click 监听器无法绕过锁定。
                btn.addEventListener('click', (e) => {
                    if (window.nekoSocialUnlock.isLocked()) {
                        e.preventDefault();
                        e.stopImmediatePropagation();
                    }
                }, true);
                window.nekoSocialUnlock.registerButton(btn, imgOff, imgOn);
            }

            // 阻止按钮上的指针事件传播
            const stopBtnEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                btn.addEventListener(evt, stopBtnEvent);
            });

            // 悬停效果
            btn.addEventListener('mouseenter', () => {
                if (config.id === 'social' && window.nekoSocialUnlock && window.nekoSocialUnlock.isLocked()) {
                    window.nekoSocialUnlock.applyButtonState(btn, imgOff, imgOn);
                    return;
                }
                btn.style.transform = 'scale(1.05)';
                btn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                btn.style.background = 'var(--neko-btn-bg-hover, rgba(255, 255, 255, 0.8))';

                if (config.separatePopupTrigger) {
                    const popup = document.getElementById(`${prefix}-popup-${config.id}`);
                    const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                    if (isPopupVisible) return;
                }

                if (imgOff && imgOn) {
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                }
            });

            btn.addEventListener('mouseleave', () => {
                if (config.id === 'social' && window.nekoSocialUnlock && window.nekoSocialUnlock.isLocked()) {
                    window.nekoSocialUnlock.applyButtonState(btn, imgOff, imgOn);
                    return;
                }
                btn.style.transform = 'scale(1)';
                btn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                const isActive = btn.dataset.active === 'true';
                const popup = document.getElementById(`${prefix}-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                const shouldShowOnIcon = config.separatePopupTrigger
                    ? isActive
                    : (isActive || isPopupVisible);

                btn.style.background = shouldShowOnIcon
                    ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
                    : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

                if (imgOff && imgOn) {
                    imgOff.style.opacity = shouldShowOnIcon ? '0' : '0.75';
                    imgOn.style.opacity = shouldShowOnIcon ? '1' : '0';
                }
            });

            return { btnWrapper, btn, imgOff, imgOn };
        };

        ManagerPrototype.syncResponsiveButtonVisibility = function(buttonsContainer) {
            const isMobile = typeof window.isMobileWidth === 'function'
                ? window.isMobileWidth()
                : window.innerWidth <= 768;
            buttonsContainer.querySelectorAll('[data-mobile-only="true"]').forEach((wrapper) => {
                wrapper.style.display = isMobile ? 'flex' : 'none';
            });
        };

        /**
         * 创建"请她回来"按钮
         */
    }
});

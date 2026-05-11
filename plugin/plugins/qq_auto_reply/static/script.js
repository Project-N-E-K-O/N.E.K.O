const pluginId = 'qq_auto_reply';
        const RUNS_URL = '/runs';

        async function callPlugin(entry, args = {}) {
            const resp = await fetch(RUNS_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plugin_id: pluginId, entry_id: entry, args })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const { run_id, id } = await resp.json();
            const runId = run_id || id;
            if (!runId) throw new Error('未获取到 run_id');

            const deadline = Date.now() + 20000;
            let delay = 300;
            while (Date.now() < deadline) {
                const poll = await fetch(`${RUNS_URL}/${runId}`);
                if (!poll.ok) continue;
                const rec = await poll.json();
                if (rec.status === 'succeeded') {
                    const exp = await fetch(`${RUNS_URL}/${runId}/export`);
                    if (!exp.ok) return {};
                    const { items = [] } = await exp.json();
                    const item = items.find(i => i.type === 'json' && i.json) || items[0];
                    if (!item) return {};
                    let raw = item.json || {};
                    while (raw && raw.data && typeof raw.data === 'object' && ('success' in raw.data || 'error' in raw.data)) {
                        raw = raw.data;
                    }
                    return raw;
                }
                if (['failed', 'canceled', 'timeout'].includes(rec.status)) {
                    throw new Error(rec.error?.message || rec.message || rec.status);
                }
            }
            throw new Error('调用超时');
        }
        let state = {
            config: {
                url: '',
                token: '',
                path: '',
                showOnboarding: false,
                guideStepConfigDone: false,
                guideStepSettingsDone: false,
                guideStepRuntimeDone: false,
                normalRelayProbability: 0.1,
                truthReplyProbability: 0.1,
            },
            users: [],
            groups: [],
            currentTab: 'users',
            dashboard: null,
            entityFormMode: null,
            qrcodeLoaded: false,
        };

        function nextStep(stepNum) {
            document.querySelectorAll('.step-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.dot').forEach(dot => dot.classList.remove('active'));
            document.getElementById('step' + stepNum).classList.add('active');
            document.getElementById('dot' + stepNum).classList.add('active');
        }

        async function initConfig() {
            try {
                const payload = await callPlugin('init_config', {});
                applyDashboardState(payload);
            } catch (error) {
                showToast(error.message || t('ui.toast.load_failed', '加载失败'));
                throw error;
            }
        }

        async function finishOnboarding() {
            try {
                await callPlugin('save_settings', { show_onboarding: false, guide_step_config_done: true });
                await reloadDashboard();
            } catch (error) {
                showToast(error.message || t('ui.toast.save_failed', '保存失败'));
                return;
            }
            const onboarding = document.getElementById('onboarding');
            onboarding.classList.add('hidden');
            onboarding.style.display = 'none';
            updateConfigGate();
        }

        async function enterApp() {
            await finishOnboarding();
        }

        function updateConfigGate() {
            return;
        }

        function reopenOnboarding() {
            document.getElementById('onboarding').classList.remove('hidden');
            document.getElementById('onboarding').style.display = 'flex';
            nextStep(1);
        }

        function uiT(key, fallback) {
            return window.I18n && typeof window.I18n.t === 'function'
                ? window.I18n.t(key, fallback)
                : (fallback || key);
        }

        function uiTf(key, fallback, values = {}) {
            const template = uiT(key, fallback);
            return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
                Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
            ));
        }

        function t(key, fallback) { return uiT(key, fallback); }
        function showToast(message) {
            const el = document.getElementById('toast');
            el.textContent = message;
            el.classList.add('show');
            window.clearTimeout(showToast._timer);
            showToast._timer = window.setTimeout(() => el.classList.remove('show'), 2400);
        }
        
        function updateGuideStep(id, completed) {
            const card = document.getElementById(`guide-step-${id}`);
            const badge = document.getElementById(`guide-step-${id}-badge`);
            if (!card || !badge) return;
            card.classList.toggle('is-complete', completed);
            card.classList.toggle('is-pending', !completed);
            badge.textContent = completed ? t('ui.guide.completed', '已完成') : t('ui.guide.pending', '未完成');
        }

        function refreshGuideProgress() {
            const guide = (state.dashboard && state.dashboard.guide) || {};
            const runtimeRunning = !!(state.dashboard && state.dashboard.runtime && state.dashboard.runtime.auto_reply_running);
            const runtimeDone = !!guide.step_auto_reply_done;
            updateGuideStep('napcat', !!guide.step_napcat_done);
            updateGuideStep('settings', !!guide.step_service_done);
            updateGuideStep('contacts', !!guide.step_contacts_done);
            updateGuideStep('runtime', runtimeDone);
            const runtimeTitle = document.getElementById('guide-step-runtime-title');
            const runtimeDesc = document.getElementById('guide-step-runtime-desc');
            if (runtimeTitle) {
                runtimeTitle.textContent = runtimeRunning ? t('ui.guide.step4.done_title', '停止自动回复') : t('ui.guide.step4.title', '启动自动回复');
            }
            if (runtimeDesc) {
                runtimeDesc.textContent = runtimeRunning ? t('ui.guide.step4.done_desc', '点击后会停止自动回复，并把该步骤切回未完成状态。') : t('ui.guide.step4.desc', '点击启用自动回复后，该步骤会写入配置并显示为已完成。');
            }
        }

        function applyDashboardState(payload) {
            const raw = payload || {};
            const data = raw.value || raw.data || raw;
            const settings = data.settings || {};
            const permissions = data.permissions || {};
            console.log('[qq_auto_reply debug] applyDashboardState payload =', data);
            console.log('[qq_auto_reply debug] applyDashboardState settings =', settings);
            console.log('[qq_auto_reply debug] applyDashboardState permissions =', permissions);
            state.dashboard = data;
            state.users = Array.isArray(permissions.trusted_users) ? permissions.trusted_users : [];
            state.groups = Array.isArray(permissions.trusted_groups) ? permissions.trusted_groups : [];
            state.config.url = String(settings.onebot_url || '');
            state.config.path = String(settings.napcat_directory || '');
            state.config.showOnboarding = Boolean(settings.show_onboarding ?? true);
            state.config.guideStepConfigDone = Boolean(settings.guide_step_config_done ?? false);
            state.config.guideStepSettingsDone = Boolean(settings.guide_step_settings_done ?? false);
            state.config.guideStepRuntimeDone = Boolean(settings.guide_step_runtime_done ?? false);
            state.config.normalRelayProbability = Number(settings.normal_relay_probability ?? 0.1);
            state.config.truthReplyProbability = Number(settings.truth_reply_probability ?? 0.1);
            document.getElementById('cfg-url').value = state.config.url;
            console.log('[qq_auto_reply debug] cfg-url after set =', document.getElementById('cfg-url').value);
            document.getElementById('cfg-token').value = String(settings.token || '');
            console.log('[qq_auto_reply debug] cfg-token after set =', document.getElementById('cfg-token').value);
            document.getElementById('cfg-path').value = state.config.path;
            document.getElementById('cfg-show-napcat-window').checked = Boolean(settings.show_napcat_window ?? true);
            console.log('[qq_auto_reply debug] cfg-path after set =', document.getElementById('cfg-path').value);
            document.getElementById('cfg-normal-probability').value = Number.isFinite(state.config.normalRelayProbability) ? String(state.config.normalRelayProbability) : '0.1';
            document.getElementById('cfg-truth-probability').value = Number.isFinite(state.config.truthReplyProbability) ? String(state.config.truthReplyProbability) : '0.1';
            const runtime = data.runtime || {};
            document.getElementById('status-self-id').textContent = runtime.napcat_pid ? String(runtime.napcat_pid) : '-';
            document.getElementById('status-onebot').textContent = data.runtime && data.runtime.onebot_connected ? t('ui.status.connected', '已连接') : t('ui.status.disconnected', '未连接');
            const qrcodeImage = document.getElementById('qrcode-image');
            const qrcodeEmpty = document.getElementById('qrcode-empty');
            const qrcodeCard = document.getElementById('qrcode-card');
            const qrcodeToggle = document.getElementById('qrcode-toggle');
            const qrcodeUrl = runtime.qrcode_url || '';
            const collapsed = Boolean(qrcodeCard?.classList.contains('collapsed'));
            if (qrcodeImage && qrcodeEmpty) {
                if (state.qrcodeLoaded && qrcodeUrl) {
                    qrcodeImage.src = `${qrcodeUrl}?_ts=${Date.now()}`;
                    qrcodeImage.style.display = collapsed ? 'none' : 'block';
                    qrcodeEmpty.style.display = 'none';
                } else {
                    qrcodeImage.removeAttribute('src');
                    qrcodeImage.style.display = 'none';
                    qrcodeEmpty.style.display = collapsed ? 'none' : 'block';
                }
            }
            if (qrcodeToggle && qrcodeCard) {
                qrcodeToggle.textContent = collapsed ? t('ui.qrcode.toggle.show', '显示') : t('ui.qrcode.toggle.hide', '隐藏');
            }
            document.getElementById('status-users').textContent = String(state.users.length);
            document.getElementById('status-groups').textContent = String(state.groups.length);
            const loginStatus = data.login && data.login.status ? data.login.status : 'offline';
            document.getElementById('login-status-pill').textContent = loginStatus === 'online' ? uiT('ui.status.online', '在线') : (loginStatus === 'error' ? uiT('ui.status.error', '异常') : uiT('ui.status.offline', '离线'));
            updateConfigGate();
            refreshGuideProgress();
            renderList();
            console.log('[qq_auto_reply debug] onboarding desired visible =', state.config.showOnboarding);
            console.log('[qq_auto_reply debug] onboarding display after apply =', document.getElementById('onboarding').style.display);
        }
        function scrollToConfigSection() {
            document.getElementById('config-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function focusAddUser() {
            state.currentTab = 'users';
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab-users')?.classList.add('active');
            renderList();
            openEntityForm('users');
            document.getElementById('entity-form-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function openEntityForm(mode) {
            state.entityFormMode = mode;
            const isUser = mode === 'users';
            document.getElementById('entity-form-card').classList.add('show');
            document.getElementById('entity-form-title').textContent = isUser ? t('ui.entity_form.user.title', '添加用户') : t('ui.entity_form.group.title', '添加群聊');
            document.getElementById('entity-number-label').textContent = isUser ? t('ui.entity_form.user.number', '号码') : t('ui.entity_form.group.number', '号码');
            document.getElementById('entity-level-label').textContent = t('ui.entity_form.level', '级别');
            document.getElementById('entity-number').value = '';
            document.getElementById('entity-nickname').value = '';
            document.getElementById('entity-nickname-group').style.display = isUser ? 'block' : 'none';
            const levelSelect = document.getElementById('entity-level');
            const options = isUser
                ? [['admin', 'admin'], ['trusted', 'trusted'], ['normal', 'normal']]
                : [['trusted', 'trusted'], ['open', 'open'], ['normal', 'normal']];
            levelSelect.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
        }

        function closeEntityForm() {
            state.entityFormMode = null;
            document.getElementById('entity-form-card').classList.remove('show');
        }

        async function refreshQrcode() {
            state.qrcodeLoaded = true;
            const payload = await callPlugin('qq_auto_reply_sync_qrcode', {});
            applyDashboardState(payload);
        }

        function toggleQrcodeCard() {
            const card = document.getElementById('qrcode-card');
            if (!card) return;
            card.classList.toggle('collapsed');
            if (state.dashboard) {
                applyDashboardState(state.dashboard);
            }
        }

        function switchTab(tabId) {
            state.currentTab = tabId;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab-' + tabId).classList.add('active');
            renderList();
        }
        function renderList() {
            const container = document.getElementById('list-container');
            const items = state.currentTab === 'users' ? state.users : state.groups;
            if (!items.length) {
                container.innerHTML = `<div class="empty-state">${t('ui.empty.no_items', '暂无数据')}</div>`;
                return;
            }
            container.innerHTML = items.map((item, index) => {
                const name = state.currentTab === 'users' ? (item.nickname || item.qq || t('ui.defaults.user', '喵呜管理员')) : (item.group_id || t('ui.defaults.group', '核心猫草群'));
                const sub = state.currentTab === 'users' ? `${item.level || ''}${item.qq ? ` · ${item.qq}` : ''}` : `${item.level || ''}${item.group_id ? ` · ${item.group_id}` : ''}`;
                return `<div class="entity-item"><div class="avatar-circle">${String(name).charAt(0).toUpperCase()}</div><div class="item-meta"><span class="item-name">${name}</span><span class="item-sub">${sub}</span></div><span class="btn-del" onclick="deleteItem(${index})">✕</span></div>`;
            }).join('');
        }
        function validateProbability(rawValue, key) {
            const value = Number(rawValue);
            if (!Number.isFinite(value) || value < 0 || value > 1) {
                throw new Error(t(key, '概率必须在 0 到 1 之间'));
            }
            return value;
        }

        function saveSettings() {
            return (async () => {
                try {
                    const normalRelayProbability = validateProbability(document.getElementById('cfg-normal-probability').value, 'ui.probability.normal.invalid');
                    const truthReplyProbability = validateProbability(document.getElementById('cfg-truth-probability').value, 'ui.probability.truth.invalid');
                    await callPlugin('save_settings', {
                        onebot_url: document.getElementById('cfg-url').value.trim(),
                        token: document.getElementById('cfg-token').value,
                        napcat_directory: document.getElementById('cfg-path').value.trim(),
                        show_napcat_window: document.getElementById('cfg-show-napcat-window').checked,
                        guide_step_settings_done: true,
                        normal_relay_probability: normalRelayProbability,
                        truth_reply_probability: truthReplyProbability
                    });
                    await reloadDashboard();
                    showToast(t('ui.toast.saved', '设置已保存'));
                    return true;
                } catch (error) {
                    showToast(error.message || t('ui.toast.save_failed', '保存失败'));
                    return false;
                }
            })();
        }
        async function refreshContacts() {
            try {
                const refreshed = await callPlugin('refresh_actual_contacts', {});
                applyDashboardState(refreshed);
                showToast(t('ui.toast.refreshed', '联系人已刷新'));
            } catch (error) { showToast(error.message || t('ui.toast.refresh_failed', '刷新失败')); }
        }
        async function reloadDashboard() {
            const payload = await callPlugin('get_dashboard_state', {});
            applyDashboardState(payload);
            return payload;
        }

        async function bootstrapDashboard() {
            try {
                await reloadDashboard();
            } catch (error) { showToast(error.message || t('ui.toast.load_failed', '加载失败')); }
        }
        function addNewEntity() {
            openEntityForm(state.currentTab);
        }

        async function submitEntityForm() {
            const number = document.getElementById('entity-number').value.trim();
            const level = document.getElementById('entity-level').value;
            const nickname = document.getElementById('entity-nickname').value.trim();
            if (!number) {
                showToast(t('ui.entity_form.required', '请先填写号码'));
                return;
            }
            if (state.entityFormMode === 'users') {
                await saveUser(number, level, nickname);
            } else if (state.entityFormMode === 'groups') {
                await saveGroup(number, level);
            }
        }

        async function saveUser(qqNumber, level, nickname = '') {
            try {
                await callPlugin('add_trusted_user', { qq_number: qqNumber, level, nickname });
                await reloadDashboard();
                closeEntityForm();
                showToast(t('ui.toast.saved', '设置已保存'));
            } catch (error) { showToast(error.message || t('ui.toast.save_failed', '保存失败')); }
        }
        async function saveGroup(groupId, level) {
            try {
                await callPlugin('add_trusted_group', { group_id: groupId, level });
                await reloadDashboard();
                closeEntityForm();
                showToast(t('ui.toast.saved', '设置已保存'));
            } catch (error) { showToast(error.message || t('ui.toast.save_failed', '保存失败')); }
        }
        async function deleteItem(index) {
            try {
                const items = state.currentTab === 'users' ? state.users : state.groups;
                const item = items[index];
                if (!item) return;
                if (state.currentTab === 'users') {
                    await callPlugin('remove_trusted_user', { qq_number: item.qq });
                } else {
                    await callPlugin('remove_trusted_group', { group_id: item.group_id });
                }
                await reloadDashboard();
                showToast(t('ui.toast.saved', '设置已保存'));
            } catch (error) { showToast(error.message || t('ui.toast.save_failed', '保存失败')); }
        }
        document.getElementById('guide-step-napcat').addEventListener('click', () => {
            showToast(t('ui.toast.start_napcat_manual', '请先手动启动 NapCat，再回到这里继续配置。'));
        });
        document.getElementById('guide-step-settings').addEventListener('click', () => {
            scrollToConfigSection();
        });
        document.getElementById('guide-step-contacts').addEventListener('click', () => {
            focusAddUser();
        });
        document.getElementById('guide-step-runtime').addEventListener('click', async () => {
            const runtimeRunning = !!(state.dashboard && state.dashboard.runtime && state.dashboard.runtime.auto_reply_running);
            try {
                if (runtimeRunning) {
                    await callPlugin('stop_auto_reply', {});
                    await callPlugin('save_settings', { guide_step_runtime_done: false });
                    await reloadDashboard();
                    showToast(t('ui.toast.stopped', '自动回复已停止'));
                } else {
                    await callPlugin('start_auto_reply', {});
                    await callPlugin('save_settings', { guide_step_runtime_done: true });
                    await reloadDashboard();
                    showToast(t('ui.toast.started', '自动回复已启动'));
                }
            } catch (error) {
                showToast(error.message || t('ui.toast.start_failed', '启动失败'));
            }
        });
        document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
        document.getElementById('entity-form-save').addEventListener('click', submitEntityForm);
        document.getElementById('entity-form-cancel').addEventListener('click', closeEntityForm);
        document.getElementById('entity-form-cancel-top').addEventListener('click', closeEntityForm);
        document.getElementById('qrcode-toggle').addEventListener('click', toggleQrcodeCard);
        document.getElementById('qrcode-refresh').addEventListener('click', refreshQrcode);
        window.addEventListener('qq-auto-reply-i18n-refreshed', (event) => {
            console.log('[qq_auto_reply i18n debug] qq-auto-reply-i18n-refreshed', event.detail);
            if (state.dashboard) {
                applyDashboardState(state.dashboard);
            }
        });
        window.addEventListener('localechange', (event) => {
            console.log('[qq_auto_reply i18n debug] localechange received', event.detail, {
                documentLang: document.documentElement.lang,
                search: location.search,
                localStorageLocale: (() => { try { return localStorage.getItem('locale'); } catch { return null; } })(),
            });
            if (state.dashboard) {
                applyDashboardState(state.dashboard);
            }
        });
        window.onload = async () => {
            const onboarding = document.getElementById('onboarding');
            onboarding.style.display = 'none';
            onboarding.classList.remove('hidden');
            if (window.I18n?.whenReady) {
                await new Promise((resolve) => window.I18n.whenReady(resolve));
            }
            await bootstrapDashboard();
            refreshGuideProgress();
            onboarding.style.display = state.config.showOnboarding ? 'flex' : 'none';
            switchTab(state.currentTab);
        };

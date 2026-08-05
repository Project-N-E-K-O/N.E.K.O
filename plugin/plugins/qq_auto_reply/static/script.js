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
                guideStepNapcatDone: false,
                guideStepConfigDone: false,
                guideStepRuntimeDone: false,
                normalRelayProbability: 0.1,
                truthReplyProbability: 0.1,
                replyMode: 'text',
                strategyMode: 'neko_dynamic',
            },
            users: [],
            groups: [],
            currentTab: 'users',
            dashboard: null,
            entityFormMode: null,
            qrcodeLoaded: false,
            backlogSummary: null,
            backlogDetail: null,
            selectedBacklogGroupId: '',
            backlogLabels: [],
            backlogLabelDrafts: [],
            backlogItems: [],
            activeReplyItem: null,
        };

        function nextStep(stepNum) {
            document.querySelectorAll('.step-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.dot').forEach(dot => dot.classList.remove('active'));
            document.getElementById('step' + stepNum).classList.add('active');
            document.getElementById('dot' + stepNum).classList.add('active');
        }

        function onConnectionModeChange() {
            const el = document.getElementById('cfg-connection-mode');
            const napcatEl = document.getElementById('napcat-fields');
            const openplatEl = document.getElementById('openplat-fields');
            if (!el || !napcatEl || !openplatEl) return;
            const mode = el.value;
            napcatEl.style.display = mode === 'napcat' ? '' : 'none';
            openplatEl.style.display = mode === 'open_platform' ? '' : 'none';
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

        function openStep1GuideModal() {
            document.getElementById('step1-guide-modal-overlay')?.classList.add('show');
        }

        function closeStep1GuideModal() {
            document.getElementById('step1-guide-modal-overlay')?.classList.remove('show');
        }

        async function confirmStep1GuideModal() {
            try {
                await callPlugin('save_settings', { guide_step_napcat_done: true });
                await reloadDashboard();
                closeStep1GuideModal();
                showToast(t('ui.toast.saved', '设置已保存'));
            } catch (error) {
                showToast(error.message || t('ui.toast.save_failed', '保存失败'));
            }
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
            state.dashboard = data;
            state.users = Array.isArray(permissions.trusted_users) ? permissions.trusted_users : [];
            state.groups = Array.isArray(permissions.trusted_groups) ? permissions.trusted_groups : [];
            document.getElementById('backlog-review-button').disabled = !state.selectedBacklogGroupId;
            state.config.url = String(settings.onebot_url || '');
            state.config.path = String(settings.napcat_directory || '');
            state.config.showOnboarding = Boolean(settings.show_onboarding ?? true);
            state.config.guideStepNapcatDone = Boolean(settings.guide_step_napcat_done ?? false);
            state.config.guideStepConfigDone = Boolean(settings.guide_step_config_done ?? false);
            state.config.guideStepRuntimeDone = Boolean(settings.guide_step_runtime_done ?? false);
            state.config.normalRelayProbability = Number(settings.normal_relay_probability ?? 0.1);
            state.config.truthReplyProbability = Number(settings.truth_reply_probability ?? 0.1);
            state.config.replyMode = ['text', 'voice', 'both'].includes(settings.reply_mode) ? settings.reply_mode : 'text';
            state.backlogLabels = Array.isArray(settings.backlog_labels) ? settings.backlog_labels.map(normalizeBacklogLabelDraft) : [];
            state.backlogLabelDrafts = state.backlogLabels.map((item) => ({ ...item }));
            state.backlogItems = Array.isArray(data.backlog_items) ? data.backlog_items : [];
            renderBacklogLabelEditor();
            document.getElementById('cfg-url').value = state.config.url;
            document.getElementById('cfg-token').value = String(settings.token || '');
            document.getElementById('cfg-path').value = state.config.path;
            document.getElementById('cfg-show-napcat-window').checked = Boolean(settings.show_napcat_window ?? true);
            document.getElementById('cfg-reply-mode').value = state.config.replyMode;
            state.config.strategyMode = String(settings.strategy_mode || 'neko_dynamic');
            state.config.connectionMode = String(settings.qq_connection_mode || 'napcat');
            document.getElementById('cfg-strategy-mode').value = state.config.strategyMode;
            const connModeEl = document.getElementById('cfg-connection-mode');
            connModeEl.value = state.config.connectionMode;
            connModeEl.removeEventListener('change', onConnectionModeChange);
            connModeEl.addEventListener('change', onConnectionModeChange);
            document.getElementById('cfg-open-app-id').value = String(settings.qq_open_app_id || '');
            document.getElementById('cfg-open-secret').value = String(settings.qq_open_client_secret || '');
            onConnectionModeChange();
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
            renderBacklogSummary();
            if (state.selectedBacklogGroupId) {
                const exists = Array.isArray(state.backlogSummary?.groups) && state.backlogSummary.groups.some((item) => String(item.group_id || '') === state.selectedBacklogGroupId);
                if (!exists) {
                    state.selectedBacklogGroupId = '';
                    state.backlogDetail = null;
                    document.getElementById('backlog-review-button').disabled = true;
                    renderBacklogDetail();
                }
            }
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
        }

        function openEntityForm(mode, item = null) {
            state.entityFormMode = mode;
            const isUser = mode === 'users';
            const isEditing = !!item;
            document.getElementById('entity-form-overlay').classList.add('show');
            document.getElementById('entity-form-card').classList.add('show');
            document.getElementById('entity-form-title').textContent = isUser
                ? (isEditing ? t('ui.entity_form.user.edit_title', '编辑用户') : t('ui.entity_form.user.title', '添加用户'))
                : (isEditing ? t('ui.entity_form.group.edit_title', '编辑群聊') : t('ui.entity_form.group.title', '添加群聊'));
            document.getElementById('entity-number-label').textContent = isUser ? t('ui.entity_form.user.number', 'QQ 号') : t('ui.entity_form.group.number', '群号');
            document.getElementById('entity-level-label').textContent = t('ui.entity_form.level', '级别');
            document.getElementById('entity-number').value = isUser ? String(item?.qq || '') : String(item?.group_id || '');
            document.getElementById('entity-number').disabled = isEditing;
            document.getElementById('entity-nickname').value = isUser ? String(item?.nickname || '') : '';
            document.getElementById('entity-nickname-group').style.display = isUser ? 'block' : 'none';
            const levelSelect = document.getElementById('entity-level');
            const options = isUser
                ? [['admin', 'admin'], ['trusted', 'trusted'], ['normal', 'normal']]
                : [['trusted', 'trusted'], ['open', 'open'], ['normal', 'normal']];
            levelSelect.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
            levelSelect.value = String(item?.level || options[0][0]);
            const nicknameGroup = document.getElementById('entity-nickname-group');
            const normalProbabilityGroup = document.getElementById('entity-normal-probability-group');
            const openProbabilityGroup = document.getElementById('entity-open-probability-group');
            const normalProbabilityInput = document.getElementById('entity-normal-probability');
            const openProbabilityInput = document.getElementById('entity-open-probability');
            normalProbabilityInput.value = isUser
                ? (item?.normal_relay_probability === undefined ? '' : String(item.normal_relay_probability))
                : (item?.normal_relay_probability === undefined ? '' : String(item.normal_relay_probability));
            openProbabilityInput.value = isUser ? '' : (item?.open_reply_probability === undefined ? '' : String(item.open_reply_probability));
            const syncProbabilityFieldVisibility = () => {
                const selectedLevel = String(levelSelect.value || '');
                if (isUser) {
                    nicknameGroup.style.display = selectedLevel === 'admin' ? 'none' : 'block';
                    if (selectedLevel === 'admin') {
                        document.getElementById('entity-nickname').value = '';
                    }
                    normalProbabilityGroup.style.display = selectedLevel === 'normal' ? 'block' : 'none';
                    openProbabilityGroup.style.display = 'none';
                    if (selectedLevel !== 'normal') {
                        normalProbabilityInput.value = '';
                    }
                    return;
                }
                nicknameGroup.style.display = 'none';
                normalProbabilityGroup.style.display = selectedLevel === 'normal' ? 'block' : 'none';
                openProbabilityGroup.style.display = selectedLevel === 'open' ? 'block' : 'none';
                if (selectedLevel !== 'normal') {
                    normalProbabilityInput.value = '';
                }
                if (selectedLevel !== 'open') {
                    openProbabilityInput.value = '';
                }
            };
            levelSelect.onchange = syncProbabilityFieldVisibility;
            syncProbabilityFieldVisibility();
        }

        function closeEntityForm() {
            state.entityFormMode = null;
            document.getElementById('entity-number').disabled = false;
            document.getElementById('entity-level').onchange = null;
            document.getElementById('entity-form-card').classList.remove('show');
            document.getElementById('entity-form-overlay').classList.remove('show');
        }

        async function refreshQrcode() {
            state.qrcodeLoaded = true;
            const payload = await callPlugin('sync_qrcode', {});
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
                const isUser = state.currentTab === 'users';
                const name = isUser ? (item.nickname || item.qq || t('ui.defaults.user', '朋友们')) : (item.group_id || t('ui.defaults.group', '猫圈群聊'));
                const baseSub = isUser ? `${item.level || ''}${item.qq ? ` · ${item.qq}` : ''}` : `${item.level || ''}${item.group_id ? ` · ${item.group_id}` : ''}`;
                const probabilityParts = [];
                if (item.normal_relay_probability !== undefined) {
                    probabilityParts.push(`转发 ${item.normal_relay_probability}`);
                }
                if (!isUser && item.open_reply_probability !== undefined) {
                    probabilityParts.push(`回复 ${item.open_reply_probability}`);
                }
                const sub = probabilityParts.length ? `${baseSub} · ${probabilityParts.join(' · ')}` : baseSub;
                const displayName = escapeHtml(String(name));
                const displaySub = escapeHtml(String(sub));
                const avatarText = escapeHtml(String(name).charAt(0).toUpperCase());
                return `<button class="entity-item" type="button" onclick="editEntity(${index})"><div class="avatar-circle">${avatarText}</div><div class="item-meta"><span class="item-name">${displayName}</span><span class="item-sub">${displaySub}</span></div><span class="btn-del" onclick="event.stopPropagation();deleteItem(${index})">✕</span></button>`;
            }).join('');
        }
        function renderBacklogLabelEditor() {
            const container = document.getElementById('backlog-label-editor');
            if (!container) {
                return;
            }
            if (!state.backlogLabelDrafts.length) {
                container.innerHTML = `<div class="backlog-label-empty">${t('ui.backlog.labels.empty', '还没有 backlog 标签，点击“新增标签”开始配置。')}</div>`;
                return;
            }
            container.innerHTML = state.backlogLabelDrafts.map((item, index) => {
                const labelName = String(item.label || item.id || uiTf('ui.backlog.labels.item', '标签 {index}', { index: index + 1 }));
                const priorityClass = backlogLabelPriorityClass(item.priority);
                return `
                    <button class="backlog-chip backlog-label-chip ${priorityClass}" type="button" onclick="openBacklogLabelEditModal(${index})">
                        <span>${escapeHtml(labelName)}</span>
                    </button>
                `;
            }).join('');
        }

        function backlogLabelPriorityClass(priority) {
            const value = Number(priority || 0);
            if (value >= 100) return 'priority-high';
            if (value >= 70) return 'priority-medium';
            return 'priority-low';
        }

        function normalizeBacklogLabelDraft(item) {
            const labelId = String(item?.id || '').trim();
            const label = String(item?.label || '').trim();
            const priority = Number(item?.priority || 0);
            const keywords = Array.isArray(item?.keywords)
                ? item.keywords.map((keyword) => String(keyword || '').trim()).filter(Boolean)
                : normalizeKeywordText(String(item?.keywordsText || '')).keywords;
            return {
                id: labelId,
                label,
                priority: Number.isFinite(priority) ? priority : 0,
                keywords,
                keywordsText: keywords.join('\n'),
            };
        }

        function normalizeKeywordText(rawValue) {
            const text = String(rawValue || '');
            const keywords = text
                .split(/\r?\n|,|，/)
                .map((keyword) => keyword.trim())
                .filter(Boolean);
            return {
                keywords,
                keywordsText: keywords.join('\n'),
            };
        }

        function validateBacklogLabels() {
            const seenIds = new Set();
            for (const item of state.backlogLabelDrafts) {
                const labelId = String(item.id || '').trim();
                const label = String(item.label || '').trim();
                if (!labelId) {
                    throw new Error(t('ui.backlog.labels.id_required', '标签 ID 不能为空'));
                }
                if (!label) {
                    throw new Error(t('ui.backlog.labels.name_required', '显示名称不能为空'));
                }
                if (seenIds.has(labelId)) {
                    throw new Error(uiTf('ui.backlog.labels.id_duplicate', '标签 ID {id} 重复了', { id: labelId }));
                }
                seenIds.add(labelId);
            }
        }

        function buildBacklogLabelsPayload() {
            validateBacklogLabels();
            const normalized = [];
            const seenIds = new Set();
            for (const item of state.backlogLabelDrafts) {
                const labelId = String(item.id || '').trim();
                const label = String(item.label || '').trim();
                if (!labelId || !label || seenIds.has(labelId)) {
                    continue;
                }
                const priority = Number(item.priority || 0);
                const keywordResult = normalizeKeywordText(item.keywordsText);
                normalized.push({
                    id: labelId,
                    label,
                    priority: Number.isFinite(priority) ? priority : 0,
                    keywords: keywordResult.keywords,
                });
                item.keywords = keywordResult.keywords;
                item.keywordsText = keywordResult.keywordsText;
                seenIds.add(labelId);
            }
            return normalized;
        }

        function updateBacklogLabelDraft(index, field, value) {
            const item = state.backlogLabelDrafts[index];
            if (!item) {
                return;
            }
            if (field === 'priority') {
                item.priority = Number(value || 0);
            } else if (field === 'keywordsText') {
                const keywordResult = normalizeKeywordText(value);
                item.keywordsText = keywordResult.keywordsText;
                item.keywords = keywordResult.keywords;
            } else {
                item[field] = String(value || '');
            }
            renderBacklogLabelEditor();
        }

        function openBacklogLabelEditModal(index) {
            const item = state.backlogLabelDrafts[index];
            if (!item) {
                return;
            }
            document.getElementById('backlog-label-modal').dataset.editIndex = String(index);
            document.getElementById('backlog-label-modal-id').value = item.id || '';
            document.getElementById('backlog-label-modal-id').disabled = false;
            document.getElementById('backlog-label-modal-name').value = item.label || '';
            document.getElementById('backlog-label-modal-priority').value = String(item.priority || 0);
            document.getElementById('backlog-label-modal-keywords').value = item.keywordsText || '';
            document.getElementById('backlog-label-modal-save').textContent = t('ui.actions.save', '保存');
            const deleteButton = document.getElementById('backlog-label-modal-delete');
            if (deleteButton) {
                deleteButton.style.display = 'inline-flex';
                deleteButton.dataset.editIndex = String(index);
            }
            document.getElementById('backlog-label-modal-overlay').classList.add('show');
            document.getElementById('backlog-label-modal').classList.add('show');
        }

        function openBacklogLabelById(labelId) {
            const normalizedLabelId = String(labelId || '').trim();
            if (!normalizedLabelId) {
                return;
            }
            const index = state.backlogLabelDrafts.findIndex((item) => String(item.id || '').trim() === normalizedLabelId);
            if (index < 0) {
                return;
            }
            openBacklogLabelEditModal(index);
        }

        function addBacklogLabelDraft() {
            delete document.getElementById('backlog-label-modal').dataset.editIndex;
            document.getElementById('backlog-label-modal-id').value = '';
            document.getElementById('backlog-label-modal-id').disabled = false;
            document.getElementById('backlog-label-modal-name').value = '';
            document.getElementById('backlog-label-modal-priority').value = '0';
            document.getElementById('backlog-label-modal-keywords').value = '';
            document.getElementById('backlog-label-modal-save').textContent = t('ui.backlog.labels.add', '新增标签');
            const deleteButton = document.getElementById('backlog-label-modal-delete');
            if (deleteButton) {
                deleteButton.style.display = 'none';
                deleteButton.dataset.editIndex = '';
            }
            document.getElementById('backlog-label-modal-overlay').classList.add('show');
            document.getElementById('backlog-label-modal').classList.add('show');
        }

        function closeBacklogLabelModal() {
            document.getElementById('backlog-label-modal').classList.remove('show');
            document.getElementById('backlog-label-modal-overlay').classList.remove('show');
        }

        async function confirmBacklogLabelModal() {
            const labelId = document.getElementById('backlog-label-modal-id').value.trim();
            const label = document.getElementById('backlog-label-modal-name').value.trim();
            const priority = Number(document.getElementById('backlog-label-modal-priority').value || 0);
            const keywordResult = normalizeKeywordText(document.getElementById('backlog-label-modal-keywords').value);
            const editIndexValue = document.getElementById('backlog-label-modal').dataset.editIndex;
            const editIndex = editIndexValue === undefined ? -1 : Number(editIndexValue);
            if (!labelId) {
                showToast(t('ui.backlog.labels.id_required', '标签 ID 不能为空'));
                return;
            }
            if (!label) {
                showToast(t('ui.backlog.labels.name_required', '显示名称不能为空'));
                return;
            }
            if (state.backlogLabelDrafts.some((item, index) => index !== editIndex && String(item.id || '').trim() === labelId)) {
                showToast(uiTf('ui.backlog.labels.id_duplicate', '标签 ID {id} 重复了', { id: labelId }));
                return;
            }
            const payload = {
                id: labelId,
                label,
                priority: Number.isFinite(priority) ? priority : 0,
                keywords: keywordResult.keywords,
                keywordsText: keywordResult.keywordsText,
            };
            if (editIndex >= 0 && state.backlogLabelDrafts[editIndex]) {
                state.backlogLabelDrafts[editIndex] = payload;
            } else {
                state.backlogLabelDrafts.push(payload);
            }
            try {
                await persistBacklogLabels();
                closeBacklogLabelModal();
            } catch (error) {
                showToast(error.message || t('ui.toast.save_failed', '保存失败'));
            }
        }

        async function deleteBacklogLabelFromModal() {
            const deleteButton = document.getElementById('backlog-label-modal-delete');
            const editIndexValue = deleteButton?.dataset.editIndex;
            const editIndex = editIndexValue === undefined ? -1 : Number(editIndexValue);
            if (editIndex < 0) {
                return;
            }
            const item = state.backlogLabelDrafts[editIndex];
            if (!item) {
                return;
            }
            const nextDrafts = state.backlogLabelDrafts.filter((_, index) => index !== editIndex);
            const previousDrafts = state.backlogLabelDrafts;
            state.backlogLabelDrafts = nextDrafts;
            try {
                await persistBacklogLabels();
                closeBacklogLabelModal();
            } catch (error) {
                state.backlogLabelDrafts = previousDrafts;
                showToast(error.message || t('ui.toast.save_failed', '保存失败'));
            }
        }

        function removeBacklogLabelDraft(index) {
            const item = state.backlogLabelDrafts[index];
            if (!item) {
                return;
            }
            state.backlogLabelDrafts.splice(index, 1);
            renderBacklogLabelEditor();
        }

        function openBacklogReplyModal(index) {
            const item = state.backlogItems[index];
            if (!item) {
                return;
            }
            state.activeReplyItem = item;
            document.getElementById('backlog-reply-target').textContent = String(item.target_label || item.target_id || '-');
            document.getElementById('backlog-reply-original').textContent = String(item.original_message || '');
            document.getElementById('backlog-reply-input').value = '';
            document.getElementById('backlog-reply-modal-overlay').classList.add('show');
            document.getElementById('backlog-reply-modal').classList.add('show');
        }

        function openBacklogDetailReply(payload) {
            try {
                state.activeReplyItem = typeof payload === 'string' ? JSON.parse(payload) : payload;
            } catch {
                state.activeReplyItem = null;
            }
            if (!state.activeReplyItem) {
                return;
            }
            document.getElementById('backlog-reply-target').textContent = String(state.activeReplyItem.target_label || state.activeReplyItem.target_id || '-');
            document.getElementById('backlog-reply-original').textContent = String(state.activeReplyItem.original_message || '');
            document.getElementById('backlog-reply-input').value = '';
            document.getElementById('backlog-reply-modal-overlay').classList.add('show');
            document.getElementById('backlog-reply-modal').classList.add('show');
        }

        function closeBacklogReplyModal() {
            state.activeReplyItem = null;
            document.getElementById('backlog-reply-modal').classList.remove('show');
            document.getElementById('backlog-reply-modal-overlay').classList.remove('show');
        }

        async function sendBacklogReply() {
            const item = state.activeReplyItem;
            if (!item) {
                return;
            }
            const replyText = String(document.getElementById('backlog-reply-input').value || '').trim();
            if (!replyText) {
                showToast(t('ui.backlog.reply.required', '请先填写回复内容'));
                return;
            }
            try {
                await callPlugin('send_backlog_reply_direct', {
                    source_type: String(item.source_type || ''),
                    target_id: String(item.target_id || ''),
                    sender_id: String(item.sender_id || ''),
                    message_id: String(item.message_id || ''),
                    original_message: String(item.original_message || ''),
                    reply_text: replyText,
                });
                await loadBacklogSummary();
                if (state.selectedBacklogGroupId && String(item.target_id || '') === state.selectedBacklogGroupId) {
                    await openBacklogGroupDetail(state.selectedBacklogGroupId, { silent: true });
                }
                closeBacklogReplyModal();
                showToast(t('ui.backlog.reply.sent', '回复已发送'));
            } catch (error) {
                showToast(error.message || t('ui.toast.save_failed', '保存失败'));
            }
        }

        function renderBacklogSummary() {
            const summary = state.backlogSummary || {};
            const groups = Array.isArray(summary.groups) ? summary.groups : [];
            const labels = Array.isArray(summary.labels) ? summary.labels : [];
            const labelMap = Object.fromEntries(labels.map((item) => [String(item.id || ''), String(item.label || item.id || '')]));
            state.backlogLabels = labels;
            const overviewGrid = document.getElementById('backlog-overview-grid');
            if (overviewGrid) {
                const fixedCards = [
                    {
                        id: 'groups',
                        label: t('ui.backlog.pending_groups', '待审阅群聊'),
                        count: Number(summary.group_count || groups.length || 0),
                    },
                    {
                        id: 'items',
                        label: t('ui.backlog.pending_items', '待审阅消息'),
                        count: Number(summary.unread_count || 0),
                    },
                    ...labels.map((item) => {
                        const labelId = String(item.id || '').trim();
                        return {
                            id: labelId,
                            label: String(item.label || labelId || ''),
                            count: Number((summary.label_counts || {})[labelId] || 0),
                        };
                    }),
                ];
                overviewGrid.innerHTML = fixedCards.map((item) => `
                    <div class="status-item">
                        <span class="status-label">${escapeHtml(item.label)}</span>
                        <div class="status-value">${escapeHtml(String(item.count))}</div>
                    </div>
                `).join('');
            }
            const overviewEmpty = document.getElementById('backlog-overview-empty');
            if (overviewEmpty) {
                overviewEmpty.style.display = groups.length ? 'none' : 'block';
            }
            const container = document.getElementById('backlog-group-list');
            if (!container) {
                return;
            }
            if (!groups.length) {
                container.innerHTML = `<div class="empty-state">${t('ui.backlog.empty', '暂时没有待审阅的群反馈。')}</div>`;
                return;
            }
            container.innerHTML = groups.map((group) => {
                const groupId = String(group.group_id || '');
                const active = groupId === state.selectedBacklogGroupId;
                const displayName = group.display_name || group.group_name || uiTf('ui.backlog.default_group', 'QQ群 {groupId}', { groupId: groupId || '-' });
                const unreadCount = Number(group.unread_count || 0);
                const labelCounts = Object.entries(group.label_counts || {}).filter(([, count]) => Number(count || 0) > 0);
                const highlights = Array.isArray(group.highlights) ? group.highlights : [];
                const brief = highlights.length ? highlights.join('；') : t('ui.backlog.no_highlights', '暂时没有提炼出的重点摘要。');
                return `
                    <button class="backlog-group-item${active ? ' is-active' : ''}" type="button" onclick="openBacklogGroupDetail('${groupId}')">
                        <div class="backlog-group-top">
                            <div>
                                <div class="backlog-group-name">${escapeHtml(displayName)}</div>
                                <div class="backlog-group-meta">${uiTf('ui.backlog.pending_meta', '未审阅 {count} 条', { count: unreadCount })}</div>
                            </div>
                            <span class="backlog-chip">${uiTf('ui.backlog.unread_chip', '{count} 条待审阅', { count: unreadCount })}</span>
                        </div>
                        <div class="backlog-group-counts">
                            ${labelCounts.map(([labelId, count]) => `<span class="backlog-chip ${escapeHtml(String(labelId))}">${escapeHtml(String(labelMap[String(labelId)] || labelId))} ${escapeHtml(String(count))}</span>`).join('')}
                        </div>
                        <div class="backlog-brief">${escapeHtml(brief)}</div>
                    </button>
                `;
            }).join('');
        }

        function renderBacklogDetail() {
            const detail = state.backlogDetail;
            const container = document.getElementById('backlog-detail');
            const reviewButton = document.getElementById('backlog-review-button');
            if (!container || !reviewButton) {
                return;
            }
            reviewButton.disabled = !state.selectedBacklogGroupId;
            if (!detail || !detail.group) {
                container.className = 'backlog-detail empty-state';
                container.textContent = t('ui.backlog.select_group', '请选择一个群查看详情。');
                return;
            }
            container.className = 'backlog-detail';
            const labels = Array.isArray(detail.labels) ? detail.labels : state.backlogLabels;
            const labelMap = Object.fromEntries(labels.map((item) => [String(item.id || ''), String(item.label || item.id || '')]));
            const group = detail.group || {};
            const groupId = String(group.group_id || state.selectedBacklogGroupId || '');
            const displayName = group.display_name || group.group_name || uiTf('ui.backlog.default_group', 'QQ群 {groupId}', { groupId: groupId || '-' });
            const conversations = Array.isArray(detail.conversations) ? detail.conversations : [];
            const messages = conversations.flatMap((conversation) => {
                const conversationName = conversation.display_name || '';
                return (Array.isArray(conversation.messages) ? conversation.messages : []).map((message) => ({
                    ...message,
                    _conversationName: conversationName,
                }));
            });
            const highlights = [];
            messages.forEach((message) => {
                const category = String(message.category || '');
                if (!category || category === 'chat') {
                    return;
                }
                const text = String(message.text || '').trim();
                if (!text) {
                    return;
                }
                const sender = String(message.sender_name || message.sender_id || t('ui.backlog.unknown_sender', '未知用户'));
                highlights.push({
                    sender,
                    sender_id: String(message.sender_id || ''),
                    message_id: String(message.message_id || ''),
                    category,
                    categoryLabel: labelMap[category] || category,
                    text,
                    target_id: groupId,
                    target_label: displayName,
                });
            });
            const topHighlights = highlights.slice(0, 5);
            const messageItems = messages.length ? messages.sort((a, b) => Number(b.timestamp || 0) - Number(a.timestamp || 0)).map((message) => {
                const sender = String(message.sender_name || message.sender_id || t('ui.backlog.unknown_sender', '未知用户'));
                const category = String(message.category || 'chat');
                const categoryLabel = labelMap[category] || category;
                const timestamp = Number(message.timestamp || 0);
                const text = String(message.text || '').trim() || t('ui.backlog.empty_message', '（空消息）');
                const dateText = timestamp ? new Date(timestamp * 1000).toLocaleString() : t('ui.backlog.unknown_time', '未知时间');
                const meta = message._conversationName ? `${sender} · ${message._conversationName}` : sender;
                const replyPayload = encodeURIComponent(JSON.stringify({
                    source_type: 'group',
                    target_id: groupId,
                    sender_id: String(message.sender_id || ''),
                    message_id: String(message.message_id || ''),
                    target_label: displayName,
                    original_message: text,
                }));
                return `
                    <button class="backlog-message-item reply-action" type="button" data-reply-payload="${escapeHtml(replyPayload)}">
                        <div class="backlog-message-top">
                            <span>${escapeHtml(meta)}</span>
                            <span>${escapeHtml(dateText)}</span>
                        </div>
                        <div class="backlog-group-counts">
                            <span class="backlog-chip ${escapeHtml(category)}">${escapeHtml(categoryLabel)}</span>
                        </div>
                        <div class="backlog-message-text">${escapeHtml(text)}</div>
                    </button>
                `;
            }).join('') : `<div class="empty-state">${t('ui.backlog.no_messages', '这个群当前没有待审阅消息。')}</div>`;
            const highlightsHtml = topHighlights.length
                ? `<div class="backlog-highlight-list">${topHighlights.map((item) => {
                    const replyPayload = encodeURIComponent(JSON.stringify({
                        source_type: 'group',
                        target_id: item.target_id,
                        sender_id: item.sender_id,
                        message_id: item.message_id,
                        target_label: item.target_label,
                        original_message: item.text,
                    }));
                    return `<button class="backlog-highlight-item reply-action" type="button" data-reply-payload="${escapeHtml(replyPayload)}">${escapeHtml(`${item.sender}（${item.categoryLabel}）：${item.text}`)}</button>`;
                }).join('')}</div>`
                : `<div class="empty-state">${t('ui.backlog.no_highlights', '暂时没有提炼出的重点摘要。')}</div>`;
            container.innerHTML = `
                <div class="backlog-detail-header">
                    <div>
                        <div class="backlog-detail-name">${escapeHtml(displayName)}</div>
                        <div class="backlog-detail-meta">${uiTf('ui.backlog.detail_meta', '群号 {groupId} · 待审阅 {count} 条', { groupId, count: messages.length })}</div>
                    </div>
                </div>
                <div class="backlog-section-title">${t('ui.backlog.highlights_title', '重点摘要')}</div>
                ${highlightsHtml}
                <div class="backlog-section-title">${t('ui.backlog.messages_title', '待审阅消息')}</div>
                <div class="backlog-message-list">${messageItems}</div>
            `;
            container.querySelectorAll('.reply-action').forEach((button) => {
                button.addEventListener('click', () => {
                    const replyPayload = button.dataset.replyPayload || '';
                    openBacklogDetailReply(decodeURIComponent(replyPayload));
                });
            });
        }

        function escapeHtml(value) {
            return String(value ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        async function loadBacklogSummary() {
            try {
                const payload = await callPlugin('get_backlog_summary', {});
                const data = payload?.value || payload?.data || payload || {};
                state.backlogSummary = data;
                renderBacklogSummary();
                if (state.selectedBacklogGroupId) {
                    const exists = Array.isArray(data.groups) && data.groups.some((item) => String(item.group_id || '') === state.selectedBacklogGroupId);
                    if (exists) {
                        await openBacklogGroupDetail(state.selectedBacklogGroupId, { silent: true });
                    } else {
                        state.selectedBacklogGroupId = '';
                        state.backlogDetail = null;
                        renderBacklogDetail();
                    }
                }
            } catch (error) {
                showToast(error.message || t('ui.backlog.load_failed', '加载群反馈摘要失败'));
            }
        }

        async function openBacklogGroupDetail(groupId, options = {}) {
            const normalizedGroupId = String(groupId || '').trim();
            if (!normalizedGroupId) {
                return;
            }
            state.selectedBacklogGroupId = normalizedGroupId;
            document.getElementById('backlog-review-button').disabled = false;
            renderBacklogSummary();
            try {
                const payload = await callPlugin('get_group_backlog_detail', { group_id: normalizedGroupId });
                state.backlogDetail = payload?.value || payload?.data || payload || null;
                renderBacklogDetail();
            } catch (error) {
                state.backlogDetail = null;
                renderBacklogDetail();
                if (!options.silent) {
                    showToast(error.message || t('ui.backlog.detail_failed', '加载群详情失败'));
                }
            }
        }

        async function markBacklogGroupReviewed(groupId = state.selectedBacklogGroupId) {
            const normalizedGroupId = String(groupId || '').trim();
            if (!normalizedGroupId) {
                showToast(t('ui.backlog.select_group', '请选择一个群查看详情。'));
                return;
            }
            try {
                const payload = await callPlugin('mark_group_backlog_reviewed', { group_id: normalizedGroupId });
                const data = payload?.value || payload?.data || payload || {};
                state.backlogSummary = {
                    ...(state.backlogSummary || {}),
                    groups: Array.isArray(data.groups) ? data.groups : [],
                    group_count: Array.isArray(data.groups) ? data.groups.length : 0,
                    unread_count: Array.isArray(data.groups) ? data.groups.reduce((sum, item) => sum + Number(item.unread_count || 0), 0) : 0,
                    label_counts: Array.isArray(data.groups) ? data.groups.reduce((acc, item) => {
                        const counts = item.label_counts || {};
                        Object.entries(counts).forEach(([labelId, count]) => {
                            acc[labelId] = (acc[labelId] || 0) + Number(count || 0);
                        });
                        return acc;
                    }, {}) : {},
                    labels: Array.isArray(state.backlogLabels) ? state.backlogLabels : [],
                };
                state.backlogDetail = null;
                state.selectedBacklogGroupId = '';
                renderBacklogSummary();
                renderBacklogDetail();
                document.getElementById('backlog-review-button').disabled = true;
                showToast(t('ui.backlog.reviewed', '已标记为审阅完成'));
            } catch (error) {
                showToast(error.message || t('ui.backlog.review_failed', '标记已审阅失败'));
            }
        }

        function validateProbability(rawValue, key) {
            const value = Number(rawValue);
            if (!Number.isFinite(value) || value < 0 || value > 1) {
                throw new Error(t(key, '概率必须在 0 到 1 之间'));
            }
            return value;
        }

        function isMaskedTokenPlaceholder(value) {
            return /^[*●•]{3,8}$/.test(String(value || '').trim());
        }

        async function persistBacklogLabels(options = {}) {
            const { successMessage = t('ui.toast.saved', '设置已保存') } = options;
            await callPlugin('save_settings', {
                backlog_labels: buildBacklogLabelsPayload(),
            });
            await reloadDashboard();
            await loadBacklogSummary();
            if (successMessage) {
                showToast(successMessage);
            }
        }

        function saveSettings() {
            return (async () => {
                try {
                    const normalRelayProbability = validateProbability(document.getElementById('cfg-normal-probability').value, 'ui.probability.normal.invalid');
                    const truthReplyProbability = validateProbability(document.getElementById('cfg-truth-probability').value, 'ui.probability.truth.invalid');
                    const tokenValue = document.getElementById('cfg-token').value;
                    const args = {
                        qq_connection_mode: document.getElementById('cfg-connection-mode').value,
                        onebot_url: document.getElementById('cfg-url').value.trim(),
                        napcat_directory: document.getElementById('cfg-path').value.trim(),
                        show_napcat_window: document.getElementById('cfg-show-napcat-window').checked,
                        qq_open_app_id: document.getElementById('cfg-open-app-id').value.trim(),
                        qq_open_client_secret: document.getElementById('cfg-open-secret').value,
                        reply_mode: document.getElementById('cfg-reply-mode').value,
                        normal_relay_probability: normalRelayProbability,
                        truth_reply_probability: truthReplyProbability,
                        backlog_labels: buildBacklogLabelsPayload(),
                        strategy_mode: document.getElementById('cfg-strategy-mode').value,
                    };
                    if (!isMaskedTokenPlaceholder(tokenValue)) {
                        args.token = tokenValue;
                    }
                    await callPlugin('save_settings', args);
                    await reloadDashboard();
                    await loadBacklogSummary();
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
                await loadBacklogSummary();
            } catch (error) { showToast(error.message || t('ui.toast.load_failed', '加载失败')); }
        }
        function addNewEntity() {
            state.currentTab = state.currentTab || 'users';
            openEntityForm(state.currentTab);
        }

        function normalizeOptionalProbability(rawValue, key) {
            if (rawValue === '' || rawValue === null || rawValue === undefined) {
                return undefined;
            }
            return validateProbability(rawValue, key);
        }

        function editEntity(index) {
            const items = state.currentTab === 'users' ? state.users : state.groups;
            const item = items[index];
            if (!item) {
                return;
            }
            openEntityForm(state.currentTab, item);
        }

        function optimisticUpsertGroup(group) {
            const normalizedGroupId = String(group?.group_id || '').trim();
            if (!normalizedGroupId) {
                return;
            }
            const nextGroup = {
                ...group,
                group_id: normalizedGroupId,
            };
            const existingIndex = state.groups.findIndex((item) => String(item.group_id || '') === normalizedGroupId);
            if (existingIndex >= 0) {
                state.groups[existingIndex] = { ...state.groups[existingIndex], ...nextGroup };
            } else {
                state.groups = [...state.groups, nextGroup];
            }
            const summary = state.backlogSummary || { groups: [], group_count: 0, unread_count: 0, label_counts: {}, labels: state.backlogLabels || [] };
            const summaryGroups = Array.isArray(summary.groups) ? [...summary.groups] : [];
            const summaryIndex = summaryGroups.findIndex((item) => String(item.group_id || '') === normalizedGroupId);
            const summaryItem = {
                group_id: normalizedGroupId,
                display_name: String(group.display_name || group.group_name || `QQ群 ${normalizedGroupId}`),
                unread_count: 0,
                label_counts: {},
                highlights: [],
            };
            if (summaryIndex >= 0) {
                summaryGroups[summaryIndex] = { ...summaryGroups[summaryIndex], ...summaryItem };
            } else {
                summaryGroups.push(summaryItem);
            }
            state.backlogSummary = {
                ...summary,
                groups: summaryGroups,
                group_count: summaryGroups.length,
                unread_count: summaryGroups.reduce((sum, item) => sum + Number(item.unread_count || 0), 0),
            };
        }

        function optimisticRemoveGroup(groupId) {
            const normalizedGroupId = String(groupId || '').trim();
            if (!normalizedGroupId) {
                return;
            }
            state.groups = state.groups.filter((item) => String(item.group_id || '') !== normalizedGroupId);
            if (state.backlogSummary && Array.isArray(state.backlogSummary.groups)) {
                const nextGroups = state.backlogSummary.groups.filter((item) => String(item.group_id || '') !== normalizedGroupId);
                state.backlogSummary = {
                    ...state.backlogSummary,
                    groups: nextGroups,
                    group_count: nextGroups.length,
                    unread_count: nextGroups.reduce((sum, item) => sum + Number(item.unread_count || 0), 0),
                };
            }
            if (state.selectedBacklogGroupId === normalizedGroupId) {
                state.selectedBacklogGroupId = '';
                state.backlogDetail = null;
            }
        }

        async function submitEntityForm() {
            const number = document.getElementById('entity-number').value.trim();
            const level = document.getElementById('entity-level').value;
            const nickname = document.getElementById('entity-nickname').value.trim();
            const normalRelayProbability = normalizeOptionalProbability(document.getElementById('entity-normal-probability').value, 'ui.probability.normal.invalid');
            const openReplyProbability = normalizeOptionalProbability(document.getElementById('entity-open-probability').value, 'ui.probability.truth.invalid');
            if (!number) {
                showToast(t('ui.entity_form.required', '请先填写号码'));
                return;
            }
            if (state.entityFormMode === 'users') {
                await saveUser(number, level, nickname, normalRelayProbability);
            } else if (state.entityFormMode === 'groups') {
                await saveGroup(number, level, normalRelayProbability, openReplyProbability);
            }
        }

        async function saveUser(qqNumber, level, nickname = '', normalRelayProbability = undefined) {
            try {
                const payload = { qq_number: qqNumber, level, nickname };
                if (level === 'normal' && normalRelayProbability !== undefined) {
                    payload.normal_relay_probability = normalRelayProbability;
                }
                await callPlugin('add_trusted_user', payload);
                await reloadDashboard();
                closeEntityForm();
                showToast(t('ui.toast.saved', '设置已保存'));
            } catch (error) { showToast(error.message || t('ui.toast.save_failed', '保存失败')); }
        }
        async function saveGroup(groupId, level, normalRelayProbability = undefined, openReplyProbability = undefined) {
            try {
                const payload = { group_id: groupId, level };
                if (level === 'normal' && normalRelayProbability !== undefined) {
                    payload.normal_relay_probability = normalRelayProbability;
                }
                if (level === 'open' && openReplyProbability !== undefined) {
                    payload.open_reply_probability = openReplyProbability;
                }
                await callPlugin('add_trusted_group', payload);
                optimisticUpsertGroup({
                    group_id: groupId,
                    level,
                    ...(level === 'normal' && normalRelayProbability !== undefined ? { normal_relay_probability: normalRelayProbability } : {}),
                    ...(level === 'open' && openReplyProbability !== undefined ? { open_reply_probability: openReplyProbability } : {}),
                });
                renderList();
                renderBacklogSummary();
                closeEntityForm();
                showToast(t('ui.toast.saved', '设置已保存'));
                await reloadDashboard();
                await loadBacklogSummary();
            } catch (error) { showToast(error.message || t('ui.toast.save_failed', '保存失败')); }
        }
        async function deleteItem(index) {
            try {
                const items = state.currentTab === 'users' ? state.users : state.groups;
                const item = items[index];
                if (!item) return;
                if (state.currentTab === 'users') {
                    await callPlugin('remove_trusted_user', { qq_number: item.qq });
                    await reloadDashboard();
                } else {
                    await callPlugin('remove_trusted_group', { group_id: item.group_id });
                    optimisticRemoveGroup(item.group_id);
                    renderList();
                    renderBacklogSummary();
                    renderBacklogDetail();
                    await reloadDashboard();
                    await loadBacklogSummary();
                }
                showToast(t('ui.toast.saved', '设置已保存'));
            } catch (error) { showToast(error.message || t('ui.toast.save_failed', '保存失败')); }
        }
        window.switchTab = switchTab;
        window.addNewEntity = addNewEntity;
        window.deleteItem = deleteItem;
        window.editEntity = editEntity;
        window.openBacklogDetailReply = openBacklogDetailReply;
        window.openBacklogLabelEditModal = openBacklogLabelEditModal;
        window.updateBacklogLabelDraft = updateBacklogLabelDraft;
        window.addBacklogLabelDraft = addBacklogLabelDraft;
        window.removeBacklogLabelDraft = removeBacklogLabelDraft;
        window.closeBacklogLabelModal = closeBacklogLabelModal;
        window.confirmBacklogLabelModal = confirmBacklogLabelModal;
        window.deleteBacklogLabelFromModal = deleteBacklogLabelFromModal;
        window.nextStep = nextStep;
        window.enterApp = enterApp;
        window.finishOnboarding = finishOnboarding;
        window.reopenOnboarding = reopenOnboarding;

        document.getElementById('guide-step-napcat').addEventListener('click', () => {
            openStep1GuideModal();
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
        document.getElementById('backlog-refresh').addEventListener('click', loadBacklogSummary);
        document.getElementById('backlog-review-button').addEventListener('click', () => markBacklogGroupReviewed());
        document.getElementById('step1-guide-confirm').addEventListener('click', confirmStep1GuideModal);
        document.getElementById('step1-guide-cancel').addEventListener('click', closeStep1GuideModal);
        document.getElementById('step1-guide-modal-overlay').addEventListener('click', (event) => {
            if (event.target === event.currentTarget) {
                closeStep1GuideModal();
            }
        });
        document.getElementById('backlog-label-add').addEventListener('click', addBacklogLabelDraft);
        document.getElementById('backlog-label-modal-save').addEventListener('click', confirmBacklogLabelModal);
        document.getElementById('backlog-label-modal-delete').addEventListener('click', deleteBacklogLabelFromModal);
        document.getElementById('backlog-label-modal-cancel').addEventListener('click', closeBacklogLabelModal);
        document.getElementById('backlog-label-modal-overlay').addEventListener('click', (event) => {
            if (event.target === event.currentTarget) {
                closeBacklogLabelModal();
            }
        });
        document.getElementById('backlog-reply-send').addEventListener('click', sendBacklogReply);
        document.getElementById('backlog-reply-cancel').addEventListener('click', closeBacklogReplyModal);
        document.getElementById('backlog-reply-modal-overlay').addEventListener('click', (event) => {
            if (event.target === event.currentTarget) {
                closeBacklogReplyModal();
            }
        });
        document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
        document.getElementById('entity-form-save').addEventListener('click', submitEntityForm);
        document.getElementById('entity-form-cancel').addEventListener('click', closeEntityForm);
        document.getElementById('entity-form-overlay').addEventListener('click', (event) => {
            if (event.target === event.currentTarget) {
                closeEntityForm();
            }
        });
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

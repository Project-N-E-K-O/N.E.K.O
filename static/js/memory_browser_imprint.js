(function () {
    'use strict';

    const ADVANCED_DIAG_STORAGE_KEY = 'neko_memory_advanced_diagnostics';

    let activeTab = 'imprint';
    let activeCharacter = '';
    let activeProfileFilter = 'about_you';
    let overviewData = null;
    let overviewCharacter = '';
    let starMapData = null;
    let searchResults = null;
    let searchQuery = '';
    let healthExpanded = false;
    let loadingRequestId = 0;
    let starMapRequestId = 0;
    let noteGenerating = false;
    let clusterLabeling = false;
    let relatedCheckContext = null;
    let snapshotsList = [];
    let snapshotsLoading = false;
    let snapshotsRequestId = 0;
    let snapshotCreating = false;
    let snapshotRestoringId = '';
    let snapshotConfirmId = '';
    let vaultOpen = false;
    let dagExpanded = false;
    let dagData = null;
    let dagLoading = false;
    let deepConflictsData = null;
    let deepConflictsLoading = false;
    let exportingMode = '';
    let activeToast = null;
    let csrfTokenCache = '';
    let overviewError = '';
    let dataVersion = 0;
    let searchRequestId = 0;
    let lineageRequestId = 0;
    let dagRequestId = 0;
    let unmountStarMap = null;

    async function fetchCsrfToken(forceRefresh) {
        if (csrfTokenCache && !forceRefresh) {
            return csrfTokenCache;
        }
        try {
            const resp = await fetch('/api/config/page_config', { cache: 'no-store' });
            if (resp.ok) {
                const cfg = await resp.json();
                if (cfg && typeof cfg.autostart_csrf_token === 'string' && cfg.autostart_csrf_token) {
                    csrfTokenCache = cfg.autostart_csrf_token;
                    return csrfTokenCache;
                }
            }
        } catch (_err) {
            // Non-fatal fallback for mocked/preview environments
        }
        return csrfTokenCache;
    }

    async function postJsonWithCsrf(url, bodyObj) {
        const payload = JSON.stringify(bodyObj || {});
        const token = await fetchCsrfToken(false);
        const headers = { 'Content-Type': 'application/json' };
        if (token) {
            headers['X-CSRF-Token'] = token;
        }
        let resp = await fetch(url, {
            method: 'POST',
            headers: headers,
            body: payload,
        });
        if (resp.status === 403) {
            let isCsrfError = false;
            try {
                const clone = await resp.clone().json();
                isCsrfError = Boolean(clone && clone.error_code === 'csrf_validation_failed');
            } catch (_e) {
                // ignore parse failure
            }
            if (isCsrfError) {
                const fresh = await fetchCsrfToken(true);
                if (fresh) {
                    const retryHeaders = {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': fresh,
                    };
                    resp = await fetch(url, {
                        method: 'POST',
                        headers: retryHeaders,
                        body: payload,
                    });
                }
            }
        }
        return resp;
    }

    function tr(key, fallback, vars) {
        if (typeof window.t === 'function') {
            try {
                const res = window.t(key, vars);
                if (res && res !== key) {
                    return res;
                }
            } catch (_e) {
                // fallback below
            }
        }
        let text = fallback || key;
        if (vars && typeof vars === 'object') {
            Object.keys(vars).forEach(function (k) {
                text = text.replace(new RegExp('\\{\\{' + k + '\\}\\}', 'g'), String(vars[k]));
            });
        }
        return text;
    }

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                const v = attrs[k];
                if (v === null || v === undefined || v === false) return;
                if (k === 'className') {
                    node.className = v;
                } else if (k === 'textContent') {
                    node.textContent = v;
                } else if (k.startsWith('on') && typeof v === 'function') {
                    node.addEventListener(k.slice(2).toLowerCase(), v);
                } else if (v === true) {
                    node.setAttribute(k, '');
                } else {
                    node.setAttribute(k, String(v));
                }
            });
        }
        if (Array.isArray(children)) {
            children.forEach(function (c) {
                if (c === null || c === undefined) return;
                if (typeof c === 'string') {
                    node.appendChild(document.createTextNode(c));
                } else {
                    node.appendChild(c);
                }
            });
        }
        return node;
    }

    function getUiLanguage() {
        const cands = [
            window.i18n && window.i18n.language,
            window.i18next && window.i18next.language,
            document.documentElement && document.documentElement.lang,
        ];
        for (let i = 0; i < cands.length; i += 1) {
            if (typeof cands[i] === 'string' && cands[i].trim()) {
                return cands[i].trim();
            }
        }
        return 'zh-CN';
    }

    function isAdvancedDiagnosticsEnabled() {
        try {
            return window.localStorage.getItem(ADVANCED_DIAG_STORAGE_KEY) === 'true';
        } catch (_e) {
            return false;
        }
    }

    function setAdvancedDiagnosticsEnabled(enabled) {
        try {
            window.localStorage.setItem(ADVANCED_DIAG_STORAGE_KEY, enabled ? 'true' : 'false');
        } catch (_e) {
            // ignore storage errors
        }
        if (enabled) {
            healthExpanded = true;
        }
        syncAdvancedDiagnosticsToggle();
        renderProfilePanel();
    }

    function syncAdvancedDiagnosticsToggle() {
        const checkbox = document.getElementById('advanced-diagnostics-toggle-checkbox');
        const label = document.getElementById('advanced-diagnostics-toggle-text');
        const enabled = isAdvancedDiagnosticsEnabled();
        if (checkbox) {
            checkbox.checked = enabled;
        }
        if (label) {
            label.textContent = enabled
                ? tr('memory.enabled', '已开启')
                : tr('memory.disabled', '已关闭');
        }
    }

    function notifyMainWindowMemoryEdited(characterName) {
        if (!characterName) return;
        try {
            if (typeof BroadcastChannel !== 'undefined') {
                const channel = new BroadcastChannel('neko_page_channel');
                channel.postMessage({
                    action: 'memory_edited',
                    catgirl_name: characterName,
                });
                channel.close();
                return;
            }
        } catch (_e) {
            // fallback to postMessage
        }
        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage(
                    { type: 'memory_edited', catgirl_name: characterName },
                    window.location.origin
                );
            }
        } catch (_e) {
            // ignore
        }
    }

    function renderActiveToastIntoHost() {
        const hosts = [
            document.getElementById('memory-imprint-toast-slot'),
            document.getElementById('memory-profile-toast-slot'),
        ];
        hosts.forEach(function (host) {
            if (!host) return;
            host.textContent = '';
            if (!activeToast || !activeToast.message) return;
            host.appendChild(el('div', {
                className: 'memory-profile-toast' + (activeToast.isError ? ' is-error' : ''),
                role: 'status',
                textContent: activeToast.message,
            }));
        });
    }

    function showToast(message, isError) {
        activeToast = message ? { message: String(message), isError: Boolean(isError) } : null;
        renderActiveToastIntoHost();
        window.clearTimeout(showToast._timer);
        if (activeToast) {
            showToast._timer = window.setTimeout(function () {
                activeToast = null;
                renderActiveToastIntoHost();
            }, 3600);
        }
    }

    function formatShortDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function setActiveTab(tabName) {
        if (tabName !== 'imprint' && tabName !== 'profile' && tabName !== 'recent') {
            tabName = 'imprint';
        }
        activeTab = tabName;
        document.querySelectorAll('#memory-view-tabs .memory-view-tab').forEach(function (btn) {
            const selected = btn.getAttribute('data-memory-tab') === tabName;
            btn.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
        const panels = {
            imprint: document.getElementById('memory-panel-imprint'),
            profile: document.getElementById('memory-panel-profile'),
            recent: document.getElementById('memory-panel-recent'),
        };
        Object.keys(panels).forEach(function (key) {
            if (panels[key]) {
                panels[key].hidden = key !== tabName;
            }
        });
        const titleEl = document.getElementById('memory-editor-active-title');
        if (titleEl) {
            if (activeCharacter) {
                titleEl.textContent = activeCharacter;
            } else {
                titleEl.textContent = tr('memory.title', '记忆浏览');
            }
        }
    }

    async function fetchOverview(characterName) {
        if (!characterName) {
            overviewData = null;
            overviewCharacter = '';
            overviewError = '';
            starMapData = null;
            relatedCheckContext = null;
            snapshotsList = [];
            dagData = null;
            deepConflictsData = null;
            snapshotConfirmId = '';
            renderImprintPanel();
            renderProfilePanel();
            updateAttentionBadges();
            return;
        }
        const reqId = ++loadingRequestId;
        overviewError = '';
        if (overviewCharacter !== characterName) {
            overviewData = null;
            overviewCharacter = characterName;
            renderImprintPanel();
            renderProfilePanel();
        }
        fetchStarMap(characterName);
        fetchSnapshots(characterName);
        try {
            const resp = await fetch(
                '/api/memory/overview/' + encodeURIComponent(characterName)
            );
            const data = await resp.json();
            if (reqId !== loadingRequestId) return;
            if (!resp.ok || !data.success) {
                overviewData = null;
                overviewError = data.error || tr('memory.loadFailed', '加载失败');
                renderImprintPanel();
                renderProfilePanel();
                return;
            }
            overviewError = '';
            overviewData = data;
            searchResults = null;
            updateAttentionBadges();
            renderImprintPanel();
            renderProfilePanel();
        } catch (err) {
            if (reqId !== loadingRequestId) return;
            overviewData = null;
            overviewError = (err && err.message) ? err.message : tr('memory.loadFailed', '加载失败');
            renderImprintPanel();
            renderProfilePanel();
        }
    }

    async function fetchStarMap(characterName) {
        if (!characterName) {
            starMapData = null;
            return;
        }
        const reqId = ++starMapRequestId;
        try {
            const resp = await fetch(
                '/api/memory/star_map/' + encodeURIComponent(characterName)
            );
            const data = await resp.json();
            if (reqId !== starMapRequestId) return;
            if (resp.ok && data && data.success) {
                starMapData = data;
                renderImprintPanel();
            }
        } catch (_err) {
            // Star map is non-blocking progressive enhancement
        }
    }

    async function fetchSnapshots(characterName) {
        if (!characterName) {
            snapshotsList = [];
            return;
        }
        const reqId = ++snapshotsRequestId;
        snapshotsLoading = true;
        try {
            const resp = await fetch(
                '/api/memory/snapshots/' + encodeURIComponent(characterName)
            );
            const data = await resp.json();
            if (reqId !== snapshotsRequestId) return;
            if (resp.ok && data && data.success && Array.isArray(data.snapshots)) {
                snapshotsList = data.snapshots;
            }
        } catch (_err) {
            // Non-blocking
        } finally {
            if (reqId === snapshotsRequestId) {
                snapshotsLoading = false;
                if (activeTab === 'profile' && !overviewError) {
                    const existingVault = document.getElementById('memory-vault-section');
                    if (existingVault && existingVault.parentNode) {
                        const prevInput = existingVault.querySelector('input');
                        const prevDraft = prevInput ? prevInput.value : '';
                        const wasFocused = Boolean(prevInput && document.activeElement === prevInput);
                        const nextVault = renderMemoryVaultSection();
                        existingVault.replaceWith(nextVault);
                        const nextInput = nextVault.querySelector('input');
                        if (nextInput && prevDraft) {
                            nextInput.value = prevDraft;
                        }
                        if (nextInput && wasFocused) {
                            nextInput.focus();
                        }
                    } else {
                        renderProfilePanel();
                    }
                }
            }
        }
    }

    async function handleCreateSnapshot(labelText) {
        const targetChar = activeCharacter;
        if (!targetChar || snapshotCreating) return;
        snapshotCreating = true;
        vaultOpen = true;
        renderProfilePanel();
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/snapshots/create/' + encodeURIComponent(targetChar),
                { label: String(labelText || '').trim(), reason: 'manual' }
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter) return;
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.snapshotCreateFailed', '创建安全点失败'), true);
            } else {
                showToast(tr('memory.snapshotCreatedToast', '已创建记忆安全点'), false);
                await fetchSnapshots(targetChar);
            }
        } catch (err) {
            if (targetChar !== activeCharacter) return;
            showToast((err && err.message) || tr('memory.snapshotCreateFailed', '创建安全点失败'), true);
        } finally {
            if (targetChar === activeCharacter) {
                snapshotCreating = false;
                renderProfilePanel();
            }
        }
    }

    async function handleRestoreSnapshot(snapshotId) {
        const targetChar = activeCharacter;
        if (!targetChar || !snapshotId || snapshotRestoringId) return;
        snapshotRestoringId = snapshotId;
        snapshotConfirmId = '';
        vaultOpen = true;
        renderProfilePanel();
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/snapshots/restore/' + encodeURIComponent(targetChar),
                { snapshot_id: snapshotId }
            );
            const data = await resp.json();
            if (resp.ok && data && data.success) {
                notifyMainWindowMemoryEdited(targetChar);
            }
            if (targetChar !== activeCharacter) return;
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.snapshotRestoreFailed', '恢复安全点失败'), true);
            } else {
                showToast(tr('memory.snapshotRestoredToast', '已恢复记忆安全点（恢复前已自动留存备份）'), false);
                dagData = null;
                deepConflictsData = null;
                await fetchOverview(targetChar);
            }
        } catch (err) {
            if (targetChar !== activeCharacter) return;
            showToast((err && err.message) || tr('memory.snapshotRestoreFailed', '恢复安全点失败'), true);
        } finally {
            if (targetChar === activeCharacter) {
                snapshotRestoringId = '';
                renderProfilePanel();
            }
        }
    }

    async function handleExportZip(mode) {
        const targetChar = activeCharacter;
        if (!targetChar || exportingMode) return;
        const normMode = mode === 'diagnostics' ? 'diagnostics' : 'keepsake';
        exportingMode = normMode;
        if (normMode === 'keepsake') renderImprintPanel();
        else renderProfilePanel();
        try {
            const resp = await fetch(
                '/api/memory/export/' + encodeURIComponent(targetChar) + '?mode=' + normMode
            );
            if (!resp.ok) {
                let errMsg = tr('memory.exportFailed', '导出失败');
                try {
                    const j = await resp.json();
                    if (j && j.error) errMsg = j.error;
                } catch (_e) { /* ignore */ }
                if (targetChar === activeCharacter) {
                    showToast(errMsg, true);
                }
                return;
            }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = normMode === 'diagnostics'
                ? 'neko_memory_diagnostics_redacted.zip'
                : (targetChar + '_keepsake.zip');
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            if (targetChar === activeCharacter) {
                showToast(
                    normMode === 'diagnostics'
                        ? tr('memory.exportDiagnosticsSuccess', '脱敏诊断包 (ZIP) 已开始下载')
                        : tr('memory.exportKeepsakeSuccess', '珍藏记忆包 (ZIP) 已开始下载'),
                    false
                );
            }
        } catch (err) {
            if (targetChar === activeCharacter) {
                showToast((err && err.message) || tr('memory.exportFailed', '导出失败'), true);
            }
        } finally {
            if (targetChar === activeCharacter) {
                exportingMode = '';
                renderImprintPanel();
                renderProfilePanel();
            }
        }
    }

    async function handleToggleDag() {
        const targetChar = activeCharacter;
        if (!targetChar) return;
        if (dagExpanded) {
            dagExpanded = false;
            renderProfilePanel();
            return;
        }
        dagExpanded = true;
        if (dagData && dagData.character_name === targetChar && dagData._dataVersion === dataVersion) {
            renderProfilePanel();
            return;
        }
        const reqId = ++dagRequestId;
        const reqVer = dataVersion;
        dagLoading = true;
        renderProfilePanel();
        try {
            const resp = await fetch(
                '/api/memory/lineage_graph/' + encodeURIComponent(targetChar)
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter || reqId !== dagRequestId || reqVer !== dataVersion) return;
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.dagLoadFailed', '加载记忆因果图失败'), true);
                dagExpanded = false;
            } else {
                data._dataVersion = reqVer;
                dagData = data;
            }
        } catch (err) {
            if (targetChar !== activeCharacter || reqId !== dagRequestId) return;
            showToast((err && err.message) || tr('memory.dagLoadFailed', '加载记忆因果图失败'), true);
            dagExpanded = false;
        } finally {
            if (targetChar === activeCharacter && reqId === dagRequestId) {
                dagLoading = false;
                renderProfilePanel();
            }
        }
    }

    async function handleRunDeepConflictCheck() {
        const targetChar = activeCharacter;
        if (!targetChar || deepConflictsLoading) return;
        deepConflictsLoading = true;
        renderProfilePanel();
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/deep_conflicts/' + encodeURIComponent(targetChar),
                { language: getUiLanguage() }
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter) return;
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.deepConflictFailed', '深度矛盾核查失败，请检查纠错模型配置'), true);
            } else {
                deepConflictsData = data;
                showToast(
                    tr('memory.deepConflictDone', '深度核查完成，共检查 {{count}} 组高相似记忆', {
                        count: data.checked_pairs || 0,
                    }),
                    false
                );
            }
        } catch (err) {
            if (targetChar !== activeCharacter) return;
            showToast((err && err.message) || tr('memory.deepConflictFailed', '深度矛盾核查失败'), true);
        } finally {
            if (targetChar === activeCharacter) {
                deepConflictsLoading = false;
                renderProfilePanel();
            }
        }
    }

    async function handleLabelStarMapClusters() {
        const targetChar = activeCharacter;
        if (!targetChar || clusterLabeling) return;
        clusterLabeling = true;
        renderImprintPanel();
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/star_map/label_clusters/' + encodeURIComponent(targetChar),
                { language: getUiLanguage() }
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter) return;
            if (!resp.ok || !data.success) {
                // Backend error text (machine codes, English exception text) is
                // for diagnosis; the user gets the translated hint.
                if (data && data.error) console.warn('[MemoryBrowser] star map labeling failed:', data.error);
                if (resp.status === 429) {
                    // Manual cooldown: say how long to wait, not that the model is misconfigured.
                    showToast(tr('memory.starMapLabelCooldown', '操作太频繁，请 {{seconds}} 秒后再刷新星云主题', {
                        seconds: (data && data.retry_after) || 15,
                    }), true);
                } else {
                    showToast(tr('memory.starMapLabelFailed', '刷新星云主题失败'), true);
                }
            } else {
                if (data.star_map) {
                    starMapData = Object.assign({ success: true }, data.star_map);
                } else {
                    await fetchStarMap(targetChar);
                }
                showToast(tr('memory.starMapLabelSuccess', '星云主题已更新'), false);
            }
        } catch (err) {
            if (targetChar !== activeCharacter) return;
            console.warn('[MemoryBrowser] star map labeling failed:', err);
            showToast(tr('memory.starMapLabelFailed', '刷新星云主题失败'), true);
        } finally {
            if (targetChar === activeCharacter) {
                clusterLabeling = false;
                renderImprintPanel();
            }
        }
    }

    async function checkRelatedMemoriesAfterMutation(kind, entryId, text, expectedChar, scopeMeta) {
        const targetChar = expectedChar || activeCharacter;
        if (!targetChar || targetChar !== activeCharacter || !text) return;
        const scope = scopeMeta || {};
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/related/' + encodeURIComponent(targetChar),
                {
                    kind: kind,
                    entry_id: entryId || '',
                    text: text,
                    top_k: 4,
                    subject_kind: scope.subject_kind || '',
                    subject_id: scope.subject_id || '',
                    scope: scope.scope || '',
                }
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter) return;
            if (resp.ok && data && data.success && Array.isArray(data.related) && data.related.length > 0) {
                relatedCheckContext = {
                    sourceText: text,
                    items: data.related,
                };
                updateRelatedBannerSlot();
            }
        } catch (_err) {
            // Best-effort cascade helper
        }
    }

    function closeLineageDrawer() {
        const drawer = document.getElementById('memory-lineage-drawer');
        const backdrop = document.getElementById('memory-lineage-backdrop');
        if (drawer) drawer.hidden = true;
        if (backdrop) backdrop.hidden = true;
    }

    async function openLineageDrawer(item) {
        const targetChar = activeCharacter;
        if (!targetChar || !item || !item.id) return;
        const reqId = ++lineageRequestId;
        const drawer = document.getElementById('memory-lineage-drawer');
        const backdrop = document.getElementById('memory-lineage-backdrop');
        const body = document.getElementById('memory-lineage-drawer-body');
        if (!drawer || !backdrop || !body) return;

        backdrop.hidden = false;
        drawer.hidden = false;
        drawer.focus();
        body.textContent = '';
        body.appendChild(el('div', {
            className: 'memory-empty-state',
            textContent: tr('memory.loading', '加载中...'),
        }));

        try {
            const qs = '?kind=' + encodeURIComponent(item.kind || 'persona')
                + '&entry_id=' + encodeURIComponent(item.id);
            const resp = await fetch(
                '/api/memory/lineage/' + encodeURIComponent(targetChar) + qs
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter || reqId !== lineageRequestId) return;
            if (!resp.ok || !data.success) {
                body.textContent = '';
                body.appendChild(el('div', {
                    className: 'memory-empty-state',
                    textContent: data.error || tr('memory.lineageLoadFailed', '暂时找不到这条记忆的来龙去脉'),
                }));
                return;
            }
            renderLineageStory(body, data);
        } catch (err) {
            if (targetChar !== activeCharacter || reqId !== lineageRequestId) return;
            body.textContent = '';
            body.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: (err && err.message) || tr('memory.lineageLoadFailed', '暂时找不到这条记忆的来龙去脉'),
            }));
        }
    }

    function renderLineageStory(body, story) {
        body.textContent = '';
        const steps = story.steps || {};
        const target = story.target || {};
        const conversations = steps.conversations || [];
        const facts = steps.facts || [];
        const reflections = steps.reflections || [];

        // Step 1: Conversations
        const step1 = el('section', { className: 'lineage-step-card' }, [
            el('div', { className: 'lineage-step-head' }, [
                el('span', { className: 'lineage-step-num', textContent: tr('memory.lineageStep1Title', '① 当时的那几句聊天') }),
                conversations.some(function (c) { return c.inferred; })
                    ? el('span', { className: 'memory-mini-badge', textContent: tr('memory.lineageInferredTag', '推测来自这段聊天') })
                    : null,
            ]),
        ]);
        if (!conversations.length) {
            step1.appendChild(el('div', {
                className: 'imprint-note-meta',
                textContent: tr('memory.lineageNoConv', '原始对话已较久远，未匹配到直接片段。'),
            }));
        } else {
            conversations.forEach(function (c) {
                const who = c.role === 'ai'
                    ? (activeCharacter || 'AI')
                    : tr('memory.me', '我');
                step1.appendChild(el('div', { className: 'lineage-snippet' }, [
                    el('div', { className: 'lineage-snippet-meta' }, [
                        el('span', { textContent: who }),
                        el('span', { textContent: formatShortDate(c.timestamp) }),
                    ]),
                    el('div', { textContent: c.text }),
                ]));
            });
        }
        body.appendChild(step1);

        // Step 2: Concrete Facts (with 1-click root forget!)
        const step2 = el('section', { className: 'lineage-step-card' }, [
            el('div', { className: 'lineage-step-head' }, [
                el('span', { className: 'lineage-step-num', textContent: tr('memory.lineageStep2Title', '② 记下的那件事') }),
                steps.facts_inferred
                    ? el('span', { className: 'memory-mini-badge', textContent: tr('memory.lineageInferredFactTag', '关联推测') })
                    : null,
            ]),
        ]);
        if (!facts.length) {
            step2.appendChild(el('div', {
                className: 'imprint-note-meta',
                textContent: tr('memory.lineageNoFacts', '没有关联到独立的事件记录（可能来自手动添加或外部导入）。'),
            }));
        } else {
            facts.forEach(function (f) {
                const snippet = el('div', { className: 'lineage-snippet' }, [
                    el('div', { className: 'lineage-snippet-meta' }, [
                        el('span', { textContent: f.archived ? tr('memory.lineageFactArchived', '已归档事件') : tr('memory.lineageFactActive', '具体事件') }),
                        el('span', { textContent: formatShortDate(f.created_at) }),
                    ]),
                    el('div', { textContent: f.text }),
                ]);
                if (!f.archived && f.id) {
                    snippet.appendChild(el('div', { className: 'memory-card-actions' }, [
                        el('button', {
                            type: 'button',
                            className: 'memory-pill-btn is-danger is-sm',
                            textContent: tr('memory.lineageForgetRootFact', '忘掉这条源头记忆'),
                            onClick: async function () {
                                const ok = await postMutation('/api/memory/fact/archive', {
                                    character_name: activeCharacter,
                                    fact_id: f.id,
                                }, tr('memory.lineageRootForgottenToast', '已忘掉源头事件'));
                                if (ok) {
                                    openLineageDrawer(target);
                                }
                            },
                        }),
                    ]));
                }
                step2.appendChild(snippet);
            });
        }
        body.appendChild(step2);

        // Step 3: Current Impression
        const targetDisplayText = target.text || (target.archived ? tr('memory.badgeArchived', '已归档') : '');
        const step3 = el('section', { className: 'lineage-step-card' }, [
            el('div', { className: 'lineage-step-head' }, [
                el('span', { className: 'lineage-step-num', textContent: tr('memory.lineageStep3Title', '③ 最终形成的印象') }),
            ]),
            el('div', { className: 'lineage-snippet' }, [
                el('div', { className: 'lineage-snippet-meta' }, [
                    el('span', { textContent: tr('memory.lineageCurrentImpression', '当前记忆') }),
                    el('span', { textContent: formatShortDate(target.created_at) }),
                ]),
                el('div', { textContent: targetDisplayText }),
            ]),
        ]);
        reflections.forEach(function (r) {
            const displayText = r.text || (r.archived ? tr('memory.badgeArchived', '已归档') : '');
            const sameAsTarget = target.kind === 'reflection'
                && String(r.id || '') !== ''
                && String(r.id || '') === String(target.id || '');
            if (displayText && !sameAsTarget && displayText !== targetDisplayText) {
                step3.appendChild(el('div', { className: 'lineage-snippet' }, [
                    el('div', { className: 'lineage-snippet-meta' }, [
                        el('span', { textContent: tr('memory.lineageLinkedReflection', '中间总结') }),
                        el('span', { textContent: formatShortDate(r.created_at) }),
                    ]),
                    el('div', { textContent: displayText }),
                ]));
            }
        });
        const versionHist = Array.isArray(steps.version_history) ? steps.version_history : [];
        if (versionHist.length > 0) {
            versionHist.slice().reverse().forEach(function (vh) {
                step3.appendChild(el('div', { className: 'lineage-snippet' }, [
                    el('div', { className: 'lineage-snippet-meta' }, [
                        el('span', { textContent: tr('memory.evolutionTag', '印象演变') }),
                        el('span', { textContent: formatShortDate(vh.replaced_at) }),
                    ]),
                    el('div', { textContent: vh.text }),
                ]));
            });
        }
        body.appendChild(step3);
    }

    function renderErrorState(msg) {
        const imp = document.getElementById('memory-panel-imprint');
        const prof = document.getElementById('memory-panel-profile');
        [imp, prof].forEach(function (panel) {
            if (!panel) return;
            panel.textContent = '';
            panel.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: msg,
            }));
        });
    }

    function updateAttentionBadges() {
        const badge = document.getElementById('memory-profile-tab-badge');
        if (!badge) return;
        const count = (overviewData && overviewData.imprint && overviewData.imprint.stats)
            ? Number(overviewData.imprint.stats.pending_attention_count || 0)
            : 0;
        if (count > 0) {
            badge.textContent = String(count);
            badge.hidden = false;
        } else {
            badge.textContent = '';
            badge.hidden = true;
        }
    }

    async function postMutation(url, payload, successToast) {
        const targetChar = (payload && payload.character_name) || activeCharacter;
        try {
            const resp = await postJsonWithCsrf(url, payload);
            const data = await resp.json();
            if (targetChar !== activeCharacter) {
                if (resp.ok && data.success) {
                    notifyMainWindowMemoryEdited(targetChar);
                }
                return Boolean(resp.ok && data.success);
            }
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.operationFailed', '操作失败'), true);
                if (resp.status === 409) {
                    await fetchOverview(targetChar);
                }
                return false;
            }
            if (successToast) {
                showToast(successToast, false);
            }
            dataVersion += 1;
            dagData = null;
            deepConflictsData = null;
            notifyMainWindowMemoryEdited(targetChar);
            await fetchOverview(targetChar);
            return true;
        } catch (err) {
            if (targetChar === activeCharacter) {
                showToast(err && err.message ? err.message : tr('memory.operationFailed', '操作失败'), true);
            }
            return false;
        }
    }

    // ── Tab 1: Imprint Rendering ─────────────────────────────────────

    function renderRadarSvg(dimensions) {
        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', '0 0 280 240');
        svg.setAttribute('class', 'imprint-radar-svg');
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', tr('memory.imprintRadarTitle', 'TA 眼中的你维度分布'));

        const cx = 140;
        const cy = 120;
        const maxR = 76;
        const count = dimensions.length || 6;

        function pointAt(idx, radius) {
            const angle = (-Math.PI / 2) + (idx * 2 * Math.PI) / count;
            return [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius];
        }

        // 4 concentric rings
        [0.25, 0.5, 0.75, 1.0].forEach(function (level) {
            const pts = [];
            for (let i = 0; i < count; i += 1) {
                const p = pointAt(i, maxR * level);
                pts.push(p[0].toFixed(1) + ',' + p[1].toFixed(1));
            }
            const poly = document.createElementNS(ns, 'polygon');
            poly.setAttribute('points', pts.join(' '));
            poly.setAttribute('class', 'imprint-radar-grid');
            svg.appendChild(poly);
        });

        // Axes & Labels
        const dataPts = [];
        const dotCoords = [];
        dimensions.forEach(function (dim, i) {
            const outer = pointAt(i, maxR);
            const axis = document.createElementNS(ns, 'line');
            axis.setAttribute('x1', String(cx));
            axis.setAttribute('y1', String(cy));
            axis.setAttribute('x2', outer[0].toFixed(1));
            axis.setAttribute('y2', outer[1].toFixed(1));
            axis.setAttribute('class', 'imprint-radar-axis');
            svg.appendChild(axis);

            const ratio = Math.max(dim.count > 0 ? 0.18 : 0.06, Math.min(1, Number(dim.ratio || 0)));
            const dp = pointAt(i, maxR * ratio);
            dataPts.push(dp[0].toFixed(1) + ',' + dp[1].toFixed(1));
            dotCoords.push(dp);

            const lp = pointAt(i, maxR + 22);
            const label = document.createElementNS(ns, 'text');
            label.setAttribute('x', lp[0].toFixed(1));
            label.setAttribute('y', (lp[1] + 4).toFixed(1));
            label.setAttribute('text-anchor', lp[0] < cx - 8 ? 'end' : (lp[0] > cx + 8 ? 'start' : 'middle'));
            label.setAttribute('class', 'imprint-radar-label');
            const dimTitle = tr('memory.dim_' + dim.key, dim.key);
            label.textContent = dimTitle + ' (' + dim.count + ')';
            svg.appendChild(label);
        });

        const area = document.createElementNS(ns, 'polygon');
        area.setAttribute('points', dataPts.join(' '));
        area.setAttribute('class', 'imprint-radar-area');
        svg.appendChild(area);

        dotCoords.forEach(function (dp) {
            const dot = document.createElementNS(ns, 'circle');
            dot.setAttribute('cx', dp[0].toFixed(1));
            dot.setAttribute('cy', dp[1].toFixed(1));
            dot.setAttribute('r', '3.5');
            dot.setAttribute('class', 'imprint-radar-dot');
            svg.appendChild(dot);
        });

        return svg;
    }

    function getFallbackWarmCopy(imprint, charName) {
        const stats = imprint.stats || {};
        const warm = imprint.warm_summary || {};
        const stage = warm.stage_key || 'fresh';
        const topDims = (warm.top_dimensions || [])
            .map(function (k) { return tr('memory.dim_' + k, k); })
            .join(' · ');

        const stageLabel = tr('memory.stage_' + stage, stage);
        const headline = tr('memory.warmHeadline_' + stage, '{{name}} 与你的陪伴印记', {
            name: charName || tr('memory.defaultCompanion', 'TA'),
        });
        const summary = tr(
            'memory.warmSummary_' + stage,
            '你们已经相伴 {{days}} 天，聊过 {{messages}} 次，留下了 {{imprints}} 条专属了解与 {{facts}} 段珍贵记忆。',
            {
                name: charName || '',
                days: stats.companion_days || 0,
                messages: stats.total_messages || 0,
                imprints: stats.imprint_persona_count || 0,
                facts: stats.facts_total || 0,
            }
        );
        const commentary = topDims
            ? tr('memory.warmCommentaryWithDims', '{{name}} 最常记挂关于你的方面：{{dims}}。', {
                name: charName || '',
                dims: topDims,
            })
            : tr('memory.warmCommentaryEmpty', '随着日常对话慢慢积累，{{name}} 会越来越懂你的小习惯与偏好。', {
                name: charName || '',
            });
        const blessing = tr('memory.warmBlessing_' + stage, '愿接下来的每一天，都有温暖的陪伴和被记住的安心。', {
            name: charName || '',
        });
        return { stageLabel, headline, summary, commentary, blessing };
    }

    async function handleGenerateWarmNote() {
        const targetChar = activeCharacter;
        if (!targetChar || noteGenerating) return;
        noteGenerating = true;
        renderImprintPanel();
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/imprint_note/' + encodeURIComponent(targetChar),
                { language: getUiLanguage() }
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter) return;
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.generateNoteFailed', '生成寄语失败，请检查总结模型配置'), true);
            } else {
                if (overviewData && overviewData.imprint && overviewData.imprint.warm_summary) {
                    overviewData.imprint.warm_summary.cached_note = data.note;
                }
                showToast(tr('memory.generateNoteSuccess', '专属关系寄语已更新'), false);
            }
        } catch (err) {
            if (targetChar !== activeCharacter) return;
            showToast(err && err.message ? err.message : tr('memory.generateNoteFailed', '生成寄语失败'), true);
        } finally {
            if (targetChar === activeCharacter) {
                noteGenerating = false;
                renderImprintPanel();
            }
        }
    }

    function renderImprintPanel() {
        const panel = document.getElementById('memory-panel-imprint');
        if (!panel) return;
        if (typeof unmountStarMap === 'function') {
            unmountStarMap();
            unmountStarMap = null;
        }
        panel.textContent = '';
        panel.appendChild(el('div', { id: 'memory-imprint-toast-slot' }));
        renderActiveToastIntoHost();

        if (!activeCharacter) {
            panel.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.selectCharacterPrompt', '请先在左侧选择一位角色'),
            }));
            return;
        }
        if (overviewError) {
            panel.appendChild(el('div', { className: 'memory-empty-state' }, [
                el('p', { textContent: overviewError }),
                el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-sm',
                    textContent: tr('memory.retry', '重试'),
                    onClick: function () {
                        overviewError = '';
                        renderImprintPanel();
                        renderProfilePanel();
                        fetchOverview(activeCharacter);
                    },
                }),
            ]));
            return;
        }
        if (!overviewData || !overviewData.imprint) {
            panel.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.loading', '加载中...'),
            }));
            return;
        }

        const imprint = overviewData.imprint;
        const stats = imprint.stats || {};
        const warm = imprint.warm_summary || {};
        const fallback = getFallbackWarmCopy(imprint, activeCharacter);
        const cached = warm.cached_note;

        const summaryText = (cached && cached.summary) ? cached.summary : fallback.summary;
        const commentaryText = (cached && cached.commentary) ? cached.commentary : fallback.commentary;
        const blessingText = (cached && cached.blessing) ? cached.blessing : fallback.blessing;

        // 1. Warm Hero Card
        const keepsakeBtn = el('button', {
            type: 'button',
            id: 'memory-export-keepsake-btn',
            className: 'memory-pill-btn',
            disabled: exportingMode === 'keepsake',
            onClick: function () { handleExportZip('keepsake'); },
            textContent: exportingMode === 'keepsake'
                ? tr('memory.exportingZip', '打包中...')
                : tr('memory.exportKeepsakeZip', '珍藏导出 (ZIP)'),
        });

        const genBtn = el('button', {
            type: 'button',
            id: 'memory-generate-note-btn',
            className: 'memory-pill-btn',
            disabled: noteGenerating,
            onClick: handleGenerateWarmNote,
            textContent: noteGenerating
                ? tr('memory.generatingNote', '正在整理寄语...')
                : (cached
                    ? tr('memory.refreshWarmNote', '刷新专属寄语')
                    : tr('memory.generateWarmNote', '生成专属寄语')),
        });

        const hero = el('section', { className: 'imprint-hero-card' }, [
            el('div', { className: 'imprint-hero-head' }, [
                el('div', { className: 'imprint-hero-title-group' }, [
                    el('span', { className: 'imprint-stage-pill', textContent: fallback.stageLabel }),
                    el('h3', { className: 'imprint-hero-headline', textContent: fallback.headline }),
                ]),
                el('div', { className: 'imprint-hero-actions' }, [keepsakeBtn, genBtn]),
            ]),
            el('div', { className: 'imprint-warm-copy' }, [
                el('p', { className: 'imprint-summary-text', textContent: summaryText }),
                el('div', { className: 'imprint-commentary-box', textContent: commentaryText }),
            ]),
            el('div', { className: 'imprint-blessing-row' }, [
                el('p', { className: 'imprint-blessing-text', textContent: '“' + blessingText + '”' }),
                cached && cached.generated_at
                    ? el('span', {
                        className: 'imprint-note-meta',
                        textContent: tr('memory.noteUpdatedAt', '更新于 {{date}}', {
                            date: formatShortDate(cached.generated_at),
                        }),
                    })
                    : null,
            ]),
        ]);
        panel.appendChild(hero);

        // 2. Stats Wall
        const statsGrid = el('section', { className: 'imprint-stats-grid' }, [
            el('div', { className: 'imprint-stat-card' }, [
                el('span', {
                    className: 'imprint-stat-value',
                    textContent: stats.companion_days > 0 ? String(stats.companion_days) : '—',
                }),
                el('span', { className: 'imprint-stat-label', textContent: tr('memory.statCompanionDays', '相伴天数') }),
            ]),
            el('div', { className: 'imprint-stat-card' }, [
                el('span', { className: 'imprint-stat-value', textContent: String(stats.total_messages || 0) }),
                el('span', { className: 'imprint-stat-label', textContent: tr('memory.statTotalMessages', '对话印记') }),
            ]),
            el('div', { className: 'imprint-stat-card' }, [
                el('span', { className: 'imprint-stat-value', textContent: String(stats.facts_total || 0) }),
                el('span', { className: 'imprint-stat-label', textContent: tr('memory.statFactsTotal', '记住的事') }),
            ]),
            el('div', { className: 'imprint-stat-card' }, [
                el('span', { className: 'imprint-stat-value', textContent: String(stats.imprint_persona_count || 0) }),
                el('span', { className: 'imprint-stat-label', textContent: tr('memory.statImprintPersona', '刻进心里的了解') }),
            ]),
        ]);
        panel.appendChild(statsGrid);

        // 3. Soft attention nudge (only if pending items exist)
        if (Number(stats.pending_attention_count || 0) > 0) {
            panel.appendChild(el('div', { className: 'imprint-attention-banner' }, [
                el('span', {
                    textContent: tr(
                        'memory.imprintPendingBanner',
                        '有 {{count}} 条记忆待确认或需要整理，帮 {{name}} 校准一下？',
                        { count: stats.pending_attention_count, name: activeCharacter }
                    ),
                }),
                el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-primary is-sm',
                    textContent: tr('memory.goReviewNow', '去看看'),
                    onClick: function () { setActiveTab('profile'); },
                }),
            ]));
        }

        // 3b. Memory Star Map Card (Phase 2)
        const starMapHost = el('div', { id: 'memory-starmap-host' });
        const starMapHeaderActions = el('div', { className: 'starmap-header-actions' });
        if (starMapData && starMapData.ready && (starMapData.clusters || []).length > 0) {
            starMapHeaderActions.appendChild(el('button', {
                type: 'button',
                className: 'memory-pill-btn is-sm',
                disabled: clusterLabeling,
                textContent: clusterLabeling
                    ? tr('memory.starMapLabeling', '正在命名星云...')
                    : tr('memory.starMapRefreshLabels', '刷新星云主题'),
                onClick: handleLabelStarMapClusters,
            }));
        }
        const starMapCard = el('section', { className: 'imprint-section-card', id: 'memory-starmap-card' }, [
            el('div', { className: 'starmap-header-row' }, [
                el('div', {}, [
                    el('h4', { className: 'imprint-section-title', textContent: tr('memory.starMapTitle', '专属记忆星图') }),
                    el('p', { className: 'imprint-section-sub', textContent: tr('memory.starMapSub', '每一颗星都是一段被记住的片段，相近的回忆会自然聚成主题星云') }),
                ]),
                starMapHeaderActions,
            ]),
            starMapHost,
        ]);
        panel.appendChild(starMapCard);

        if (starMapData && window.nekoMemoryStarMap && typeof window.nekoMemoryStarMap.mount === 'function') {
            unmountStarMap = window.nekoMemoryStarMap.mount(starMapHost, starMapData, {
                onTrace: function (point) {
                    openLineageDrawer(point);
                },
                onCorrect: function (point) {
                    setActiveTab('profile');
                    if (point && point.text) {
                        runSearch(point.text.slice(0, 14));
                    }
                },
            });
        } else {
            starMapHost.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.loading', '加载中...'),
            }));
        }

        // 4. Radar + Milestones / Evolution
        const radarCard = el('section', { className: 'imprint-section-card' }, [
            el('h4', { className: 'imprint-section-title', textContent: tr('memory.imprintRadarTitle', 'TA 了解你的维度') }),
            el('p', { className: 'imprint-section-sub', textContent: tr('memory.imprintRadarSub', '记录你们在偏好、习惯、性格、回忆、约定与共同成长上的沉淀') }),
            el('div', { className: 'imprint-radar-wrap' }, [
                renderRadarSvg(imprint.dimensions || []),
            ]),
        ]);

        const milestonesList = el('ul', { className: 'imprint-timeline-list' });
        const milestones = imprint.milestones || [];
        const evolution = imprint.evolution || [];
        if (milestones.length === 0 && evolution.length === 0) {
            milestonesList.appendChild(el('li', {
                className: 'memory-empty-state',
                textContent: tr('memory.noMilestonesYet', '随着一起经历更多故事，重要的回忆与默契会沉淀在这里。'),
            }));
        } else {
            milestones.slice(0, 6).forEach(function (m) {
                milestonesList.appendChild(el('li', { className: 'imprint-timeline-item' }, [
                    el('div', { className: 'imprint-timeline-meta' }, [
                        el('span', {
                            className: 'imprint-timeline-tag',
                            textContent: tr('memory.milestoneCat_' + m.category, m.category || 'highlight'),
                        }),
                        el('span', { textContent: formatShortDate(m.date) }),
                    ]),
                    el('p', { className: 'imprint-timeline-text', textContent: m.text }),
                ]));
            });
            evolution.slice(0, 4).forEach(function (ev) {
                milestonesList.appendChild(el('li', { className: 'imprint-timeline-item' }, [
                    el('div', { className: 'imprint-timeline-meta' }, [
                        el('span', {
                            className: 'imprint-timeline-tag',
                            textContent: tr('memory.evolutionTag', '印象演变'),
                        }),
                        el('span', { textContent: formatShortDate(ev.changed_at) }),
                    ]),
                    el('div', { className: 'imprint-evolution-diff' }, [
                        el('span', { className: 'imprint-evolution-old', textContent: ev.previous_text }),
                        el('span', { className: 'imprint-evolution-new', textContent: '→ ' + ev.current_text }),
                    ]),
                ]));
            });
        }

        const timelineCard = el('section', { className: 'imprint-section-card' }, [
            el('h4', { className: 'imprint-section-title', textContent: tr('memory.milestonesTitle', '高光印记与印象变迁') }),
            el('p', { className: 'imprint-section-sub', textContent: tr('memory.milestonesSub', '那些被牢记的共同片段，以及 TA 对你认识的悄然更新') }),
            milestonesList,
        ]);

        panel.appendChild(el('div', { className: 'imprint-visual-grid' }, [radarCard, timelineCard]));
    }

    // ── Tab 2: Profile / Correction & 6-Axis Technical Health ────────

    function resolveOptimisticTone(score, rawStatus) {
        const s = Number(score == null ? 100 : score);
        if (rawStatus === 'bad' || s < 65) return 'care';
        if (s >= 88 && rawStatus !== 'bad') return 'great';
        if (s >= 75) return 'good';
        return 'care';
    }

    function renderHealthDiagnostics(health) {
        if (!health) return null;
        const advMode = isAdvancedDiagnosticsEnabled();
        const expanded = healthExpanded;
        const overallStatus = health.overall_status || 'good';
        const overallTone = resolveOptimisticTone(health.overall_score, overallStatus);

        const statusText = tr(
            'memory.healthStatus_' + overallStatus,
            overallStatus === 'bad'
                ? '基础稳固，几处细节待整理'
                : (overallStatus === 'warn' ? '整体很棒，稍作梳理更贴合你' : '记忆井井有条')
        );
        const encourageText = tr(
            'memory.healthEncourage_' + overallStatus,
            overallStatus === 'bad'
                ? '大部分记忆都在稳定工作，顺手整理一下标出的条目就能很快恢复最佳状态。'
                : (overallStatus === 'warn'
                    ? '记忆库状态非常稳健！只有几处小细节可以顺手确认，整体已经很懂你啦。'
                    : '每一份被留下的回忆都清晰稳固，继续轻松聊天、留下更多印记吧。')
        );

        const bar = el('button', {
            type: 'button',
            className: 'memory-health-summary-bar',
            'aria-expanded': expanded ? 'true' : 'false',
            onClick: function () {
                healthExpanded = !expanded;
                renderProfilePanel();
            },
        }, [
            el('span', { className: 'memory-health-summary-left' }, [
                el('span', { className: 'memory-health-dot is-' + overallTone }),
                el('span', {
                    textContent: tr('memory.healthBarTitle', '多维度健康诊断 · {{status}} ({{score}}/100)', {
                        status: statusText,
                        score: health.overall_score,
                    }),
                }),
            ]),
            el('span', {
                className: 'memory-health-toggle-hint',
                textContent: expanded
                    ? tr('memory.collapseDiagnostics', '收起诊断')
                    : tr('memory.expandDiagnostics', '展开技术诊断'),
            }),
        ]);

        const body = el('div', { className: 'memory-health-body', hidden: !expanded });
        if (expanded) {
            body.appendChild(el('div', {
                className: 'memory-health-encourage-note is-' + overallTone,
                textContent: encourageText,
            }));

            const grid = el('div', { className: 'memory-health-grid' });
            (health.dimensions || []).forEach(function (dim) {
                const dimTone = resolveOptimisticTone(dim.score, dim.status);
                const metricsLines = [];
                const m = dim.metrics || {};
                if (dim.key === 'consistency') {
                    metricsLines.push(tr('memory.diagConsistencyMetrics', '待裁决矛盾 {{c}} · 动摇条目 {{n}} · 连续负分 {{s}}', {
                        c: m.pending_corrections || 0,
                        n: m.negative_evidence || 0,
                        s: m.sub_zero_entries || 0,
                    }));
                } else if (dim.key === 'redundancy') {
                    metricsLines.push(tr('memory.diagRedundancyMetrics', '高相似近重复对 {{n}}', {
                        n: m.near_duplicate_pairs || 0,
                    }));
                } else if (dim.key === 'embedding') {
                    metricsLines.push(tr('memory.diagEmbeddingMetrics', '覆盖 {{e}}/{{t}} ({{p}}%) · 过期 {{s}} · 损坏 {{c}}', {
                        e: m.embedded || 0,
                        t: m.total || 0,
                        p: m.coverage_pct != null ? m.coverage_pct : 100,
                        s: m.stale || 0,
                        c: m.corrupt || 0,
                    }));
                } else if (dim.key === 'structure') {
                    metricsLines.push(tr('memory.diagStructureMetrics', '重复ID {{dup}} · 悬空引用 {{d4}} · 无源反思 {{d1}}', {
                        dup: m.duplicate_ids || 0,
                        d4: m.dangling_fact_refs || 0,
                        d1: m.sourceless_reflections || 0,
                    }));
                } else if (dim.key === 'pipeline') {
                    metricsLines.push(tr('memory.diagPipelineMetrics', '未吸收事实 {{u}} · 滞留猜想 {{st}} · 30天晋升 {{p}}', {
                        u: m.unabsorbed_facts || 0,
                        st: m.stalled_pending_reflections || 0,
                        p: m.promoted_30d || 0,
                    }));
                } else if (dim.key === 'freshness') {
                    metricsLines.push(tr('memory.diagFreshnessMetrics', '30天活跃 {{a}} · 暂不提起 {{sup}} · 禁提边界 {{dir}}', {
                        a: m.recent_30d_activity || 0,
                        sup: m.suppressed_entries || 0,
                        dir: m.active_directives || 0,
                    }));
                }

                const findingsList = el('ul', { className: 'memory-health-findings' });
                (dim.findings || []).forEach(function (f) {
                    const fTone = f.severity === 'bad' ? 'care' : (f.severity === 'warn' ? 'good' : 'info');
                    findingsList.appendChild(el('li', {
                        className: 'memory-health-finding-pill is-' + fTone,
                        textContent: '[' + f.code + '] ' + tr('memory.finding_' + f.code, f.code, { count: f.count }),
                    }));
                });

                grid.appendChild(el('div', { className: 'memory-health-card' }, [
                    el('div', { className: 'memory-health-card-head' }, [
                        el('span', { textContent: tr('memory.healthDim_' + dim.key, dim.key) }),
                        el('span', {
                            className: 'memory-health-score is-' + dimTone,
                            textContent: String(dim.score),
                        }),
                    ]),
                    el('div', { className: 'memory-health-metrics', textContent: metricsLines.join(' ') }),
                    (dim.findings && dim.findings.length) ? findingsList : null,
                ]));
            });
            body.appendChild(grid);

            const funnel = health.funnel_30d || {};
            const exportBtn = el('button', {
                type: 'button',
                className: 'memory-pill-btn is-sm',
                textContent: tr('memory.exportDiagnosticsJson', '导出诊断报告 (JSON)'),
                onClick: function () {
                    const report = {
                        character_name: activeCharacter,
                        generated_at: overviewData && overviewData.generated_at,
                        stats: overviewData && overviewData.imprint && overviewData.imprint.stats,
                        health: health,
                    };
                    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'neko-memory-diagnostics-' + activeCharacter + '.json';
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                },
            });

            body.appendChild(el('div', { className: 'memory-health-funnel-row' }, [
                el('span', {
                    textContent: tr(
                        'memory.funnelSummary30d',
                        '近30天漏斗：事实 +{{f}} → 反思合成 {{s}} → 确认 {{c}} → 晋升 {{p}} / 否决 {{d}}',
                        {
                            f: funnel.facts_added || 0,
                            s: funnel.reflections_synthesized || 0,
                            c: funnel.reflections_confirmed || 0,
                            p: funnel.reflections_promoted || 0,
                            d: funnel.reflections_denied || 0,
                        }
                    ),
                }),
                exportBtn,
            ]));

            // Developer Mode deep inspection tools (strictly unlocked when toggle is ON)
            if (advMode) {
                const devBox = el('div', { className: 'memory-dev-tools-box', id: 'memory-dev-tools-box' });
                devBox.appendChild(el('div', { className: 'memory-dev-tools-head' }, [
                    el('div', { className: 'memory-dev-tools-title-wrap' }, [
                        el('span', { className: 'memory-mini-badge is-accent', textContent: tr('memory.devModeBadge', '开发者模式') }),
                        el('span', { className: 'memory-dev-tools-title', textContent: tr('memory.devToolsTitle', '高级因果分析与深度巡检') }),
                    ]),
                    el('div', { className: 'memory-dev-tools-actions' }, [
                        el('button', {
                            type: 'button',
                            id: 'memory-export-diagnostics-btn',
                            className: 'memory-pill-btn is-sm',
                            disabled: exportingMode === 'diagnostics',
                            textContent: exportingMode === 'diagnostics'
                                ? tr('memory.exportingZip', '打包中...')
                                : tr('memory.exportDiagnosticsZip', '导出脱敏诊断包 (ZIP)'),
                            onClick: function () { handleExportZip('diagnostics'); },
                        }),
                        el('button', {
                            type: 'button',
                            id: 'memory-deep-conflict-btn',
                            className: 'memory-pill-btn is-sm',
                            disabled: deepConflictsLoading,
                            textContent: deepConflictsLoading
                                ? tr('memory.deepConflictRunning', '正在深度核查...')
                                : tr('memory.deepConflictRunBtn', '深度矛盾核查 (LLM)'),
                            onClick: handleRunDeepConflictCheck,
                        }),
                        el('button', {
                            type: 'button',
                            id: 'memory-dag-toggle-btn',
                            className: 'memory-pill-btn is-sm is-primary',
                            textContent: dagExpanded
                                ? tr('memory.dagCollapseBtn', '收起 5 泳道因果图')
                                : tr('memory.dagExpandBtn', '展开 5 泳道因果图 (DAG)'),
                            onClick: handleToggleDag,
                        }),
                    ]),
                ]));

                // Deep conflict results (if run)
                if (deepConflictsData && deepConflictsData.character_name === activeCharacter) {
                    const summary = deepConflictsData.summary || {};
                    const findings = deepConflictsData.findings || [];
                    const dcWrap = el('div', { className: 'memory-deep-conflict-panel', id: 'memory-deep-conflict-results' }, [
                        el('div', { className: 'memory-deep-conflict-summary' }, [
                            el('span', {
                                textContent: tr(
                                    'memory.deepConflictSummary',
                                    '核查 {{total}} 组：潜在矛盾 {{c}} · 高度重复 {{d}} · 互补 {{m}} · 无关 {{u}}',
                                    {
                                        total: deepConflictsData.checked_pairs || 0,
                                        c: summary.contradiction || 0,
                                        d: summary.duplicate || 0,
                                        m: summary.complementary || 0,
                                        u: summary.unrelated || 0,
                                    }
                                ),
                            }),
                        ]),
                    ]);
                    if (!findings.length) {
                        dcWrap.appendChild(el('div', {
                            className: 'imprint-note-meta',
                            textContent: tr('memory.deepConflictClean', '未发现需要重点复核的高相似冲突对，当前记忆一致性良好。'),
                        }));
                    } else {
                        findings.forEach(function (f) {
                            const vTone = f.verdict === 'contradiction'
                                ? 'is-warn'
                                : (f.verdict === 'duplicate' ? 'is-accent' : '');
                            dcWrap.appendChild(el('div', { className: 'memory-deep-conflict-card' }, [
                                el('div', { className: 'memory-entry-badges' }, [
                                    el('span', {
                                        className: 'memory-mini-badge ' + vTone,
                                        textContent: tr('memory.verdict_' + f.verdict, f.verdict),
                                    }),
                                    el('span', {
                                        className: 'memory-mini-badge',
                                        textContent: Math.round((f.similarity || 0) * 100) + '%',
                                    }),
                                ]),
                                el('div', { className: 'memory-compare-grid' }, [
                                    el('div', { className: 'memory-compare-side' }, [
                                        el('span', { className: 'memory-compare-label', textContent: 'A · ' + (f.left && f.left.kind || '') }),
                                        el('span', { className: 'memory-compare-text', textContent: (f.left && f.left.text) || '' }),
                                    ]),
                                    el('div', { className: 'memory-compare-side' }, [
                                        el('span', { className: 'memory-compare-label', textContent: 'B · ' + (f.right && f.right.kind || '') }),
                                        el('span', { className: 'memory-compare-text', textContent: (f.right && f.right.text) || '' }),
                                    ]),
                                ]),
                                f.reason
                                    ? el('div', { className: 'memory-deep-conflict-reason', textContent: f.reason + (f.suggestion ? ' · ' + f.suggestion : '') })
                                    : null,
                            ]));
                        });
                    }
                    devBox.appendChild(dcWrap);
                }

                // 5-Lane Causal DAG host
                if (dagExpanded) {
                    const dagHost = el('div', { className: 'memory-dag-host', id: 'memory-dag-host' });
                    if (dagLoading) {
                        dagHost.appendChild(el('div', {
                            className: 'memory-empty-state',
                            textContent: tr('memory.loading', '加载中...'),
                        }));
                    } else if (dagData && window.nekoMemoryDag && typeof window.nekoMemoryDag.mount === 'function') {
                        window.nekoMemoryDag.mount(dagHost, dagData, {
                            onTrace: function (item) { openLineageDrawer(item); },
                        });
                    }
                    devBox.appendChild(dagHost);
                }

                body.appendChild(devBox);
            }
        }

        return el('section', { className: 'memory-health-shell' }, [bar, body]);
    }

    function renderPendingAttentionBox(memories) {
        const corrections = memories.pending_corrections || [];
        const reflections = memories.pending_reflections || [];
        const duplicates = memories.near_duplicates || [];
        if (!corrections.length && !reflections.length && !duplicates.length) {
            return null;
        }

        const box = el('section', { className: 'memory-attention-box' }, [
            el('h4', {
                className: 'imprint-section-title',
                textContent: tr('memory.attentionSectionTitle', '帮 TA 确认一下'),
            }),
        ]);

        // 1. Contradiction resolution cards
        const cardChar = activeCharacter;
        corrections.forEach(function (c) {
            const mergeInput = el('textarea', {
                className: 'memory-inline-textarea',
                placeholder: tr('memory.mergeTextPlaceholder', '也可以直接修改成你想让 TA 记住的话...'),
            });
            mergeInput.value = c.new_text || c.old_text || '';
            const baseCorrPayload = {
                character_name: cardChar,
                correction_index: c.index,
                correction_id: c.correction_id || null,
                expected_old_text: c.old_text || '',
                expected_new_text: c.new_text || '',
                expected_entity: c.entity || 'master',
                expected_scope: c.scope || '',
                expected_queued_at: c.queued_at || '',
            };

            box.appendChild(el('div', { className: 'memory-attention-item' }, [
                el('div', { className: 'memory-entry-badges' }, [
                    el('span', { className: 'memory-mini-badge is-warn', textContent: tr('memory.badgeContradiction', '发现矛盾记忆') }),
                ]),
                el('div', { className: 'memory-compare-grid' }, [
                    el('div', { className: 'memory-compare-side' }, [
                        el('span', { className: 'memory-compare-label', textContent: tr('memory.oldImpression', '之前的印象') }),
                        el('span', { className: 'memory-compare-text', textContent: c.old_text }),
                    ]),
                    el('div', { className: 'memory-compare-side' }, [
                        el('span', { className: 'memory-compare-label', textContent: tr('memory.newDiscovery', '后来的新发现') }),
                        el('span', { className: 'memory-compare-text', textContent: c.new_text }),
                    ]),
                ]),
                mergeInput,
                el('div', { className: 'memory-card-actions' }, [
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-primary is-sm',
                        textContent: tr('memory.actionKeepNew', '按新的更新'),
                        onClick: function () {
                            postMutation('/api/memory/correction/resolve', Object.assign({}, baseCorrPayload, {
                                action: 'keep_new',
                                merged_text: mergeInput.value.trim() || c.new_text,
                            }), tr('memory.correctionResolvedToast', '已更新记忆'));
                        },
                    }),
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm',
                        textContent: tr('memory.actionKeepOld', '保留原来的'),
                        onClick: function () {
                            postMutation('/api/memory/correction/resolve', Object.assign({}, baseCorrPayload, {
                                action: 'keep_old',
                            }), tr('memory.correctionKeptOldToast', '已保留原印象'));
                        },
                    }),
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm',
                        textContent: tr('memory.actionMerge', '按编辑框合并'),
                        onClick: function () {
                            postMutation('/api/memory/correction/resolve', Object.assign({}, baseCorrPayload, {
                                action: 'merge',
                                merged_text: mergeInput.value.trim(),
                            }), tr('memory.correctionResolvedToast', '已更新记忆'));
                        },
                    }),
                ]),
            ]));
        });

        // 2. Pending reflection guesses
        reflections.forEach(function (r) {
            const editBox = el('textarea', { className: 'memory-inline-textarea', hidden: true });
            editBox.value = r.text || '';
            const saveEditBtn = el('button', {
                type: 'button',
                className: 'memory-pill-btn is-primary is-sm',
                hidden: true,
                textContent: tr('memory.saveAndConfirm', '保存并确认'),
                onClick: function () {
                    postMutation('/api/memory/reflection/review', {
                        character_name: activeCharacter,
                        reflection_id: r.id,
                        decision: 'confirm',
                        edited_text: editBox.value.trim(),
                    }, tr('memory.reflectionConfirmedToast', '已确认这条印象'));
                },
            });

            box.appendChild(el('div', { className: 'memory-attention-item' }, [
                el('div', { className: 'memory-entry-badges' }, [
                    el('span', { className: 'memory-mini-badge is-accent', textContent: tr('memory.badgeGuess', 'TA 的近期猜想') }),
                ]),
                el('p', { className: 'memory-entry-text', textContent: r.text }),
                editBox,
                el('div', { className: 'memory-card-actions' }, [
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-primary is-sm',
                        textContent: tr('memory.actionConfirmGuess', '确实如此'),
                        onClick: function () {
                            postMutation('/api/memory/reflection/review', {
                                character_name: activeCharacter,
                                reflection_id: r.id,
                                decision: 'confirm',
                            }, tr('memory.reflectionConfirmedToast', '已确认这条印象'));
                        },
                    }),
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm',
                        textContent: tr('memory.actionEditGuess', '不太对，改一下'),
                        onClick: function () {
                            editBox.hidden = false;
                            saveEditBtn.hidden = false;
                            editBox.focus();
                        },
                    }),
                    saveEditBtn,
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-danger is-sm',
                        textContent: tr('memory.actionForget', '忘掉这个'),
                        onClick: function () {
                            postMutation('/api/memory/reflection/review', {
                                character_name: activeCharacter,
                                reflection_id: r.id,
                                decision: 'forget',
                            }, tr('memory.forgottenToast', '已遗忘该条记忆'));
                        },
                    }),
                ]),
            ]));
        });

        // 3. Near duplicates merge suggestions
        duplicates.slice(0, 5).forEach(function (dup) {
            const mergedInput = el('textarea', { className: 'memory-inline-textarea' });
            mergedInput.value = dup.a_text.length >= dup.b_text.length ? dup.a_text : dup.b_text;
            box.appendChild(el('div', { className: 'memory-attention-item' }, [
                el('div', { className: 'memory-entry-badges' }, [
                    el('span', { className: 'memory-mini-badge', textContent: tr('memory.badgeDuplicate', '高度相似记忆') }),
                ]),
                el('div', { className: 'memory-compare-grid' }, [
                    el('div', { className: 'memory-compare-side' }, [
                        el('span', { className: 'memory-compare-label', textContent: 'A' }),
                        el('span', { className: 'memory-compare-text', textContent: dup.a_text }),
                    ]),
                    el('div', { className: 'memory-compare-side' }, [
                        el('span', { className: 'memory-compare-label', textContent: 'B' }),
                        el('span', { className: 'memory-compare-text', textContent: dup.b_text }),
                    ]),
                ]),
                mergedInput,
                el('div', { className: 'memory-card-actions' }, [
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-primary is-sm',
                        textContent: tr('memory.actionMergeDuplicate', '合并为一条'),
                        onClick: function () {
                            postMutation('/api/memory/duplicate/merge', {
                                character_name: activeCharacter,
                                kind: dup.kind,
                                entity: dup.entity,
                                keep_id: dup.a_id,
                                drop_id: dup.b_id,
                                merged_text: mergedInput.value.trim(),
                            }, tr('memory.mergedDuplicateToast', '已合并相似记忆'));
                        },
                    }),
                ]),
            ]));
        });

        return box;
    }

    function renderMemoryCard(item) {
        const isProtected = Boolean(item.protected);
        const isArchived = Boolean(item.archived);
        const isPermRetired = Boolean(item.permanent_suppression);
        const isReadOnly = isProtected || isArchived;
        const card = el('article', {
            className: 'memory-entry-card'
                + (item.user_pinned ? ' is-pinned' : '')
                + ((item.suppress || isPermRetired) ? ' is-suppressed' : ''),
        });

        const badges = el('div', { className: 'memory-entry-badges' });
        if (item.user_pinned) {
            badges.appendChild(el('span', { className: 'memory-mini-badge is-accent', textContent: tr('memory.badgePinned', '已牢记') }));
        }
        if (isPermRetired) {
            badges.appendChild(el('span', { className: 'memory-mini-badge is-warn', textContent: tr('memory.badgePermanentlyRetired', '已自动退役') }));
        } else if (item.suppress) {
            badges.appendChild(el('span', { className: 'memory-mini-badge is-warn', textContent: tr('memory.badgeSuppressed', '暂不提起') }));
        }
        if (isArchived) {
            badges.appendChild(el('span', { className: 'memory-mini-badge', textContent: tr('memory.badgeArchived', '已归档') }));
        } else if (isProtected) {
            badges.appendChild(el('span', { className: 'memory-mini-badge', textContent: tr('memory.badgeCardProtected', '角色初始设定') }));
        }
        const strength = Number(item.strength || 1);
        if (!isReadOnly) {
            badges.appendChild(el('span', {
                className: 'memory-mini-badge',
                textContent: '★'.repeat(Math.max(1, Math.min(3, strength))),
                title: tr('memory.strengthHint', '记忆深刻度'),
            }));
        }

        card.appendChild(el('div', { className: 'memory-entry-head' }, [
            badges,
            item.created_at
                ? el('span', { className: 'imprint-note-meta', textContent: formatShortDate(item.created_at) })
                : null,
        ]));

        const textEl = el('p', { className: 'memory-entry-text', textContent: item.text });
        card.appendChild(textEl);

        if (isPermRetired) {
            card.appendChild(el('div', {
                className: 'imprint-note-meta',
                textContent: tr('memory.permanentlyRetiredNote', '该条印象已由自动整理退役，不会再被提起。'),
            }));
        }

        if (!isReadOnly && item.id) {
            const editArea = el('textarea', { className: 'memory-inline-textarea', hidden: true });
            editArea.value = item.text || '';
            const saveBtn = el('button', {
                type: 'button',
                className: 'memory-pill-btn is-primary is-sm',
                hidden: true,
                textContent: tr('memory.save', '保存'),
                onClick: async function () {
                    const nextText = editArea.value.trim();
                    if (!nextText) return;
                    const targetChar = activeCharacter;
                    let ok = false;
                    if (item.kind === 'persona') {
                        ok = await postMutation('/api/memory/persona/edit', {
                            character_name: targetChar,
                            entity: item.entity,
                            entry_id: item.id,
                            text: nextText,
                        }, tr('memory.savedToast', '已保存修改'));
                    } else if (item.kind === 'reflection') {
                        ok = await postMutation('/api/memory/reflection/review', {
                            character_name: targetChar,
                            reflection_id: item.id,
                            decision: 'edit',
                            edited_text: nextText,
                        }, tr('memory.savedToast', '已保存修改'));
                    } else {
                        ok = await postMutation('/api/memory/fact/edit', {
                            character_name: targetChar,
                            fact_id: item.id,
                            text: nextText,
                        }, tr('memory.savedToast', '已保存修改'));
                    }
                    if (ok && targetChar === activeCharacter) {
                        checkRelatedMemoriesAfterMutation(item.kind, item.id, item.text || nextText, targetChar, {
                            subject_kind: item.subject_kind || '',
                            subject_id: item.subject_id || '',
                            scope: item.scope || '',
                        });
                    }
                },
            });
            const cancelBtn = el('button', {
                type: 'button',
                className: 'memory-pill-btn is-sm',
                hidden: true,
                textContent: tr('memory.cancelSwitch', '取消'),
                onClick: function () {
                    editArea.hidden = true;
                    saveBtn.hidden = true;
                    cancelBtn.hidden = true;
                    textEl.hidden = false;
                },
            });

            const actions = el('div', { className: 'memory-card-actions' }, isPermRetired ? [] : [
                el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-sm',
                    textContent: tr('memory.actionEdit', '修改'),
                    onClick: function () {
                        textEl.hidden = true;
                        editArea.hidden = false;
                        saveBtn.hidden = false;
                        cancelBtn.hidden = false;
                        editArea.focus();
                    },
                }),
                saveBtn,
                cancelBtn,
            ]);

            if (item.kind === 'persona' && !isPermRetired) {
                actions.appendChild(el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-sm',
                    textContent: item.user_pinned
                        ? tr('memory.actionUnpin', '取消牢记')
                        : tr('memory.actionPin', '牢记'),
                    onClick: function () {
                        postMutation('/api/memory/persona/flags', {
                            character_name: activeCharacter,
                            entity: item.entity,
                            entry_id: item.id,
                            user_pinned: !item.user_pinned,
                        }, item.user_pinned
                            ? tr('memory.unpinnedToast', '已取消牢记')
                            : tr('memory.pinnedToast', '已标记为牢记'));
                    },
                }));
                actions.appendChild(el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-sm',
                    textContent: item.suppress
                        ? tr('memory.actionUnsuppress', '恢复提及')
                        : tr('memory.actionSuppress', '最近别提'),
                    onClick: function () {
                        postMutation('/api/memory/persona/flags', {
                            character_name: activeCharacter,
                            entity: item.entity,
                            entry_id: item.id,
                            suppress: !item.suppress,
                        }, item.suppress
                            ? tr('memory.unsuppressedToast', '已恢复提及')
                            : tr('memory.suppressedToast', '已设为最近别提'));
                    },
                }));
            }

            actions.appendChild(el('button', {
                type: 'button',
                className: 'memory-pill-btn is-sm',
                textContent: tr('memory.actionWhyRemember', '为什么这么记？'),
                onClick: function () {
                    openLineageDrawer(item);
                },
            }));

            actions.appendChild(el('button', {
                type: 'button',
                className: 'memory-pill-btn is-danger is-sm',
                textContent: tr('memory.actionForget', '遗忘'),
                onClick: async function () {
                    const targetChar = activeCharacter;
                    let ok = false;
                    if (item.kind === 'persona') {
                        ok = await postMutation('/api/memory/persona/archive', {
                            character_name: targetChar,
                            entity: item.entity,
                            entry_id: item.id,
                        }, tr('memory.forgottenToast', '已遗忘该条记忆'));
                    } else if (item.kind === 'reflection') {
                        ok = await postMutation('/api/memory/reflection/review', {
                            character_name: targetChar,
                            reflection_id: item.id,
                            decision: 'forget',
                        }, tr('memory.forgottenToast', '已遗忘该条记忆'));
                    } else {
                        ok = await postMutation('/api/memory/fact/archive', {
                            character_name: targetChar,
                            fact_id: item.id,
                        }, tr('memory.forgottenToast', '已遗忘该条记忆'));
                    }
                    if (ok && targetChar === activeCharacter) {
                        checkRelatedMemoriesAfterMutation(item.kind, item.id, item.text, targetChar, {
                            subject_kind: item.subject_kind || '',
                            subject_id: item.subject_id || '',
                            scope: item.scope || '',
                        });
                    }
                },
            }));

            card.appendChild(editArea);
            card.appendChild(actions);

            // Version history rollback
            if (Array.isArray(item.version_history) && item.version_history.length > 0) {
                const histDetails = el('details', {}, [
                    el('summary', {
                        className: 'imprint-note-meta',
                        textContent: tr('memory.viewHistoryVersions', '查看历史版本 ({{count}})', {
                            count: item.version_history.length,
                        }),
                    }),
                ]);
                const histBox = el('div', { className: 'memory-entry-history' });
                item.version_history.slice().reverse().forEach(function (vh) {
                    histBox.appendChild(el('div', { className: 'memory-history-row' }, [
                        el('span', { textContent: vh.text + (vh.replaced_at ? ' (' + formatShortDate(vh.replaced_at) + ')' : '') }),
                        el('button', {
                            type: 'button',
                            className: 'memory-pill-btn is-sm',
                            textContent: tr('memory.actionRestoreVersion', '恢复此版'),
                            onClick: function () {
                                postMutation('/api/memory/persona/restore_version', {
                                    character_name: activeCharacter,
                                    entity: item.entity,
                                    entry_id: item.id,
                                    version_index: vh.index,
                                    expected_text: vh.text || '',
                                    expected_replaced_at: vh.replaced_at || '',
                                }, tr('memory.restoredVersionToast', '已恢复为该历史版本'));
                            },
                        }),
                    ]));
                });
                histDetails.appendChild(histBox);
                card.appendChild(histDetails);
            }
        }

        return card;
    }

    function renderDirectivesSection(memories) {
        const container = el('div', { className: 'memory-cards-list' });
        const input = el('input', {
            type: 'text',
            className: 'memory-search-input',
            placeholder: tr('memory.directivePlaceholder', '输入希望 TA 别再提的话题（2-40字）...'),
            maxlength: '40',
        });

        async function submitDirective() {
            const term = input.value.trim();
            if (term.length < 2) {
                showToast(tr('memory.directiveTooShort', '请输入 2 到 40 个字的话题'), true);
                return;
            }
            const ok = await postMutation('/api/memory/directive/add', {
                character_name: activeCharacter,
                term: term,
                language: getUiLanguage(),
            }, tr('memory.directiveAddedToast', '已添加禁提边界'));
            if (ok) {
                input.value = '';
            }
        }

        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                submitDirective();
            }
        });

        container.appendChild(el('div', { className: 'memory-directive-form' }, [
            input,
            el('button', {
                type: 'button',
                className: 'memory-pill-btn is-primary',
                textContent: tr('memory.directiveAddBtn', '添加边界'),
                onClick: submitDirective,
            }),
        ]));

        const directives = memories.directives || [];
        const suppressed = memories.suppressed || [];

        if (!directives.length && !suppressed.length) {
            container.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.noDirectivesYet', '当前没有禁提话题或暂不提及的记忆。'),
            }));
            return container;
        }

        if (directives.length) {
            const chipList = el('div', { className: 'memory-directive-chip-list' });
            directives.forEach(function (d) {
                chipList.appendChild(el('span', { className: 'memory-directive-chip' }, [
                    el('span', { textContent: d.term }),
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm is-danger',
                        textContent: tr('memory.directiveRemoveBtn', '解除'),
                        onClick: function () {
                            postMutation('/api/memory/directive/remove', {
                                character_name: activeCharacter,
                                term: d.term,
                            }, tr('memory.directiveRemovedToast', '已解除禁提边界'));
                        },
                    }),
                ]));
            });
            container.appendChild(chipList);
        }

        suppressed.forEach(function (item) {
            container.appendChild(renderMemoryCard(item));
        });

        return container;
    }

    async function runSearch(query) {
        const targetChar = activeCharacter;
        searchQuery = (query || '').trim();
        const reqId = ++searchRequestId;
        if (!searchQuery || !targetChar) {
            searchResults = null;
            renderProfilePanel();
            return;
        }
        try {
            const resp = await postJsonWithCsrf(
                '/api/memory/search/' + encodeURIComponent(targetChar),
                { query: searchQuery, top_k: 25 }
            );
            const data = await resp.json();
            if (targetChar !== activeCharacter || reqId !== searchRequestId) return;
            if (!resp.ok || !data.success) {
                showToast(data.error || tr('memory.searchFailed', '搜索失败'), true);
                return;
            }
            searchResults = data.results || [];
            renderProfilePanel();
            const footer = document.getElementById('memory-profile-search-footer');
            if (footer && typeof footer.scrollIntoView === 'function') {
                footer.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        } catch (err) {
            if (targetChar !== activeCharacter || reqId !== searchRequestId) return;
            showToast(err && err.message ? err.message : tr('memory.searchFailed', '搜索失败'), true);
        }
    }

    function renderRelatedBannerSlot() {
        const slot = el('div', { id: 'memory-related-banner-slot' });
        if (!relatedCheckContext || !Array.isArray(relatedCheckContext.items) || !relatedCheckContext.items.length) {
            return slot;
        }
        const relBanner = el('section', { className: 'memory-related-banner', id: 'memory-related-check-banner' }, [
            el('div', { className: 'memory-related-banner-head' }, [
                el('span', {
                    textContent: tr(
                        'memory.relatedCheckTitle',
                        '发现还有 {{count}} 条相近记忆也提到了相关内容，要不要顺便一起看看？',
                        { count: relatedCheckContext.items.length }
                    ),
                }),
                el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-sm',
                    textContent: tr('memory.relatedCheckDismiss', '知道了'),
                    onClick: function () {
                        relatedCheckContext = null;
                        updateRelatedBannerSlot();
                    },
                }),
            ]),
        ]);
        const relList = el('div', { className: 'memory-related-list' });
        relatedCheckContext.items.forEach(function (relItem) {
            relList.appendChild(el('div', { className: 'memory-related-row' }, [
                el('span', { className: 'memory-related-text', textContent: relItem.text }),
                el('div', { className: 'memory-card-actions' }, [
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm',
                        textContent: tr('memory.actionWhyRemember', '为什么这么记？'),
                        onClick: function () { openLineageDrawer(relItem); },
                    }),
                    el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-danger is-sm',
                        textContent: tr('memory.actionForget', '遗忘'),
                        onClick: async function () {
                            const targetChar = activeCharacter;
                            const ep = relItem.kind === 'persona'
                                ? '/api/memory/persona/archive'
                                : (relItem.kind === 'reflection' ? '/api/memory/reflection/review' : '/api/memory/fact/archive');
                            const payload = relItem.kind === 'persona'
                                ? { character_name: targetChar, entity: relItem.entity, entry_id: relItem.id }
                                : (relItem.kind === 'reflection'
                                    ? { character_name: targetChar, reflection_id: relItem.id, decision: 'forget' }
                                    : { character_name: targetChar, fact_id: relItem.id });
                            const ok = await postMutation(ep, payload, tr('memory.forgottenToast', '已遗忘该条记忆'));
                            if (ok && targetChar === activeCharacter && relatedCheckContext) {
                                relatedCheckContext.items = relatedCheckContext.items.filter(function (x) {
                                    return !(x.kind === relItem.kind && x.id === relItem.id);
                                });
                                updateRelatedBannerSlot();
                            }
                        },
                    }),
                ]),
            ]));
        });
        relBanner.appendChild(relList);
        slot.appendChild(relBanner);
        return slot;
    }

    function updateRelatedBannerSlot() {
        const existingSlot = document.getElementById('memory-related-banner-slot');
        if (existingSlot && existingSlot.parentNode) {
            existingSlot.replaceWith(renderRelatedBannerSlot());
        }
    }

    function renderProfilePanel() {
        const panel = document.getElementById('memory-panel-profile');
        if (!panel) return;
        panel.textContent = '';

        panel.appendChild(el('div', { id: 'memory-profile-toast-slot' }));

        if (!activeCharacter) {
            panel.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.selectCharacterPrompt', '请先在左侧选择一位角色'),
            }));
            return;
        }
        if (overviewError) {
            panel.appendChild(el('div', { className: 'memory-empty-state' }, [
                el('p', { textContent: overviewError }),
                el('button', {
                    type: 'button',
                    className: 'memory-pill-btn is-sm',
                    textContent: tr('memory.retry', '重试'),
                    onClick: function () {
                        overviewError = '';
                        renderImprintPanel();
                        renderProfilePanel();
                        fetchOverview(activeCharacter);
                    },
                }),
            ]));
            return;
        }
        if (!overviewData || !overviewData.memories) {
            panel.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.loading', '加载中...'),
            }));
            return;
        }

        const memories = overviewData.memories;
        const groups = memories.groups || {};
        renderActiveToastIntoHost();

        // 1. Technical 6-Axis Health Diagnostics (collapsible, auto-expanded in Developer Mode)
        const healthNode = renderHealthDiagnostics(overviewData.health);
        if (healthNode) {
            panel.appendChild(healthNode);
        }

        // 1b. Cascade related-memories review banner (shown after an edit or forget)
        panel.appendChild(renderRelatedBannerSlot());

        // 2. Pending Attention Box
        const attentionBox = renderPendingAttentionBox(memories);
        if (attentionBox) {
            panel.appendChild(attentionBox);
        }

        // 3. Category filter chips
        const filterSpecs = [
            { key: 'about_you', label: tr('memory.groupAboutYou', '关于你'), count: (groups.about_you || []).length },
            { key: 'relationship', label: tr('memory.groupRelationship', '我们的关系与回忆'), count: (groups.relationship || []).length },
            { key: 'growth', label: tr('memory.groupGrowth', '因你而成长的 TA'), count: (groups.growth || []).length },
            {
                key: 'directives',
                label: tr('memory.groupDirectives', '禁提边界'),
                count: (memories.directives || []).length + (memories.suppressed || []).length,
            },
        ];

        const chipsRow = el('div', { className: 'memory-filter-chips' });
        filterSpecs.forEach(function (spec) {
            chipsRow.appendChild(el('button', {
                type: 'button',
                className: 'memory-filter-chip',
                'aria-pressed': activeProfileFilter === spec.key ? 'true' : 'false',
                onClick: function () {
                    activeProfileFilter = spec.key;
                    renderProfilePanel();
                },
            }, [
                el('span', { textContent: spec.label }),
                el('span', { className: 'memory-chip-count', textContent: String(spec.count) }),
            ]));
        });
        panel.appendChild(chipsRow);

        // 4. Active category cards
        if (activeProfileFilter === 'directives') {
            panel.appendChild(renderDirectivesSection(memories));
        } else {
            const items = groups[activeProfileFilter] || [];
            const list = el('div', { className: 'memory-cards-list' });
            if (!items.length) {
                list.appendChild(el('div', {
                    className: 'memory-empty-state',
                    textContent: tr('memory.emptyGroupPlaceholder', '这一类还没有沉淀长期记忆，多聊聊天就会慢慢积累起来。'),
                }));
            } else {
                items.forEach(function (item) {
                    list.appendChild(renderMemoryCard(item));
                });
            }
            panel.appendChild(list);
        }

        // 5. Protected Character Card foldout
        const cardItems = groups.character_card || [];
        if (cardItems.length) {
            const details = el('details', { className: 'imprint-section-card' }, [
                el('summary', {
                    className: 'imprint-section-title',
                    textContent: tr('memory.characterCardSection', '角色初始设定（受保护 · {{count}}）', {
                        count: cardItems.length,
                    }),
                }),
            ]);
            const list = el('div', { className: 'memory-cards-list' });
            cardItems.forEach(function (item) {
                list.appendChild(renderMemoryCard(item));
            });
            details.appendChild(list);
            panel.appendChild(details);
        }

        // 5b. Memory Vault (Safety Snapshots & Rollback)
        panel.appendChild(renderMemoryVaultSection());

        // 6. Keyword Search Footer (placed cleanly at the bottom of the Profile view)
        const searchFooter = el('section', { className: 'memory-search-footer', id: 'memory-profile-search-footer' });
        searchFooter.appendChild(el('div', {
            className: 'memory-search-footer-label',
            textContent: tr('memory.searchFooterLabel', '按关键词查找某段记忆'),
        }));
        const searchInput = el('input', {
            type: 'search',
            className: 'memory-search-input',
            placeholder: tr('memory.searchPlaceholder', '搜索 TA 记住的关键词（如：咖啡、熬夜、猫）...'),
        });
        searchInput.value = searchQuery;
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                runSearch(searchInput.value);
            }
        });
        searchFooter.appendChild(el('div', { className: 'memory-search-bar' }, [
            searchInput,
            el('button', {
                type: 'button',
                className: 'memory-pill-btn is-primary',
                textContent: tr('memory.searchBtn', '搜索'),
                onClick: function () { runSearch(searchInput.value); },
            }),
            searchResults !== null
                ? el('button', {
                    type: 'button',
                    className: 'memory-pill-btn',
                    textContent: tr('memory.clearSearchBtn', '清空'),
                    onClick: function () {
                        searchQuery = '';
                        searchResults = null;
                        renderProfilePanel();
                    },
                })
                : null,
        ]));

        if (searchResults !== null) {
            searchFooter.appendChild(el('h4', {
                className: 'imprint-section-title',
                textContent: tr('memory.searchResultsTitle', '搜索结果 ({{count}})', {
                    count: searchResults.length,
                }),
            }));
            const list = el('div', { className: 'memory-cards-list' });
            if (!searchResults.length) {
                list.appendChild(el('div', {
                    className: 'memory-empty-state',
                    textContent: tr('memory.noSearchResults', '没有找到与该关键词相关的长期记忆。'),
                }));
            } else {
                searchResults.forEach(function (item) {
                    list.appendChild(renderMemoryCard(item));
                });
            }
            searchFooter.appendChild(list);
        }
        panel.appendChild(searchFooter);
    }

    function renderMemoryVaultSection() {
        const details = el('details', {
            className: 'imprint-section-card memory-vault-card',
            id: 'memory-vault-section',
            open: vaultOpen ? true : null,
        });
        details.addEventListener('toggle', function () {
            vaultOpen = Boolean(details.open);
        });

        details.appendChild(el('summary', {
            className: 'imprint-section-title memory-vault-summary',
            textContent: tr('memory.vaultTitle', '记忆保险箱 · 安全点与撤回 ({{count}})', {
                count: snapshotsList.length,
            }),
        }));

        const vaultBody = el('div', { className: 'memory-vault-body' });
        vaultBody.appendChild(el('p', {
            className: 'imprint-section-sub',
            textContent: tr('memory.vaultDesc', '批量整理或合并重复记忆前可创建安全点；恢复任何历史安全点前会自动留存当前状态的备份。'),
        }));

        const labelInput = el('input', {
            type: 'text',
            id: 'memory-snapshot-label-input',
            className: 'memory-search-input',
            placeholder: tr('memory.snapshotLabelPlaceholder', '给这个安全点起个备注（可选，例如：大整理前）'),
        });
        const createBtn = el('button', {
            type: 'button',
            id: 'memory-snapshot-create-btn',
            className: 'memory-pill-btn is-primary is-sm',
            disabled: snapshotCreating,
            textContent: snapshotCreating
                ? tr('memory.snapshotCreating', '创建中...')
                : tr('memory.snapshotCreateBtn', '创建安全点'),
            onClick: function () { handleCreateSnapshot(labelInput.value); },
        });
        vaultBody.appendChild(el('div', { className: 'memory-vault-create-row' }, [labelInput, createBtn]));

        if (snapshotsLoading && !snapshotsList.length) {
            vaultBody.appendChild(el('div', {
                className: 'imprint-note-meta',
                textContent: tr('memory.loading', '加载中...'),
            }));
        } else if (!snapshotsList.length) {
            vaultBody.appendChild(el('div', {
                className: 'memory-empty-state',
                textContent: tr('memory.vaultEmpty', '还没有安全点，点击上方按钮即可随时备份当前长期记忆。'),
            }));
        } else {
            const list = el('div', { className: 'memory-vault-list' });
            snapshotsList.forEach(function (snap) {
                const stats = snap.stats || {};
                const isConfirming = snapshotConfirmId === snap.snapshot_id;
                const isRestoring = snapshotRestoringId === snap.snapshot_id;
                const reasonLabel = tr('memory.snapshotReason_' + (snap.reason || 'manual'), snap.reason || 'manual');

                const actions = el('div', { className: 'memory-card-actions' });
                if (isConfirming) {
                    actions.appendChild(el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-primary is-sm',
                        disabled: Boolean(snapshotRestoringId),
                        textContent: tr('memory.snapshotConfirmRestoreBtn', '确认恢复'),
                        onClick: function () { handleRestoreSnapshot(snap.snapshot_id); },
                    }));
                    actions.appendChild(el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm',
                        textContent: tr('memory.cancel', '取消'),
                        onClick: function () {
                            snapshotConfirmId = '';
                            renderProfilePanel();
                        },
                    }));
                } else {
                    actions.appendChild(el('button', {
                        type: 'button',
                        className: 'memory-pill-btn is-sm',
                        disabled: Boolean(snapshotRestoringId),
                        textContent: isRestoring
                            ? tr('memory.snapshotRestoring', '恢复中...')
                            : tr('memory.snapshotRestoreBtn', '恢复至此'),
                        onClick: function () {
                            snapshotConfirmId = snap.snapshot_id;
                            vaultOpen = true;
                            renderProfilePanel();
                        },
                    }));
                }

                list.appendChild(el('div', { className: 'memory-vault-row' }, [
                    el('div', { className: 'memory-vault-info' }, [
                        el('div', { className: 'memory-vault-row-head' }, [
                            el('span', { className: 'memory-vault-label', textContent: snap.label || snap.snapshot_id }),
                            el('span', { className: 'memory-mini-badge', textContent: reasonLabel }),
                        ]),
                        el('div', {
                            className: 'imprint-note-meta',
                            textContent: formatShortDate(snap.created_at) + ' · ' + tr(
                                'memory.snapshotStatsLine',
                                '了解 {{p}} · 猜想 {{r}} · 事实 {{f}}',
                                {
                                    p: stats.persona_count || 0,
                                    r: stats.reflections_count || 0,
                                    f: stats.facts_count || 0,
                                }
                            ),
                        }),
                    ]),
                    actions,
                ]));
            });
            vaultBody.appendChild(list);
        }

        details.appendChild(vaultBody);
        return details;
    }

    function init() {
        // Determine initial tab (default: 'imprint', allow ?tab=recent|profile|imprint override)
        let initialTab = 'imprint';
        try {
            const params = new URLSearchParams(window.location.search || '');
            const requested = params.get('tab');
            if (requested === 'imprint' || requested === 'profile' || requested === 'recent') {
                initialTab = requested;
            }
        } catch (_e) {
            // ignore
        }

        document.querySelectorAll('#memory-view-tabs .memory-view-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const tab = btn.getAttribute('data-memory-tab');
                setActiveTab(tab);
            });
        });

        const advCheckbox = document.getElementById('advanced-diagnostics-toggle-checkbox');
        if (advCheckbox) {
            syncAdvancedDiagnosticsToggle();
            if (isAdvancedDiagnosticsEnabled()) {
                healthExpanded = true;
            }
            advCheckbox.addEventListener('change', function () {
                setAdvancedDiagnosticsEnabled(Boolean(advCheckbox.checked));
            });
        }

        if (window.__nekoMemoryLimitedMode) {
            initialTab = 'recent';
        }
        setActiveTab(initialTab);

        window.addEventListener('neko:memory-limited-state', function () {
            setActiveTab('recent');
        });

        const closeDrawerBtn = document.getElementById('memory-lineage-drawer-close');
        const lineageBackdrop = document.getElementById('memory-lineage-backdrop');
        if (closeDrawerBtn) {
            closeDrawerBtn.addEventListener('click', closeLineageDrawer);
        }
        if (lineageBackdrop) {
            lineageBackdrop.addEventListener('click', closeLineageDrawer);
        }
        document.addEventListener('keydown', function (ev) {
            if (ev.key === 'Escape') {
                const drawer = document.getElementById('memory-lineage-drawer');
                if (drawer && !drawer.hidden) {
                    closeLineageDrawer();
                }
            }
        });

        window.addEventListener('neko:memory-character-selected', function (evt) {
            const catName = (evt && evt.detail && evt.detail.catName) ? String(evt.detail.catName) : '';
            activeCharacter = catName;
            overviewData = null;
            starMapData = null;
            snapshotsList = [];
            relatedCheckContext = null;
            searchQuery = '';
            searchResults = null;
            dagData = null;
            dagExpanded = false;
            dagLoading = false;
            deepConflictsData = null;
            deepConflictsLoading = false;
            noteGenerating = false;
            clusterLabeling = false;
            snapshotCreating = false;
            snapshotRestoringId = '';
            snapshotConfirmId = '';
            exportingMode = '';
            closeLineageDrawer();
            setActiveTab(activeTab);
            updateAttentionBadges();
            renderImprintPanel();
            renderProfilePanel();
            fetchOverview(catName);
        });

        window.addEventListener('neko:memory-refresh-overview', function () {
            if (activeCharacter) {
                fetchOverview(activeCharacter);
            }
        });

        window.addEventListener('localechange', function () {
            syncAdvancedDiagnosticsToggle();
            setActiveTab(activeTab);
            renderImprintPanel();
            renderProfilePanel();
        });

        // Expose lightweight API for cross-script coordination & testing
        window.nekoMemoryImprint = {
            setActiveTab: setActiveTab,
            getActiveTab: function () { return activeTab; },
            refresh: function () { return fetchOverview(activeCharacter); },
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

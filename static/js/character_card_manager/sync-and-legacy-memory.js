// Part responsibility: cross-page character synchronization, unload cleanup, and legacy-memory management.

const CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY = 'neko_cloudsave_character_sync';
const CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE = 'cloudsave_character_changed';
const CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME = 'neko_cloudsave_character_sync';

function handleCloudsaveCharacterSync(data) {
    if (!data || data.type !== CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE) return;
    if (hasUnsavedNewCatgirlDraft()) {
        console.log('[CharacterCardManager] Unsaved draft detected, deferring sync refresh');
        return;
    }
    console.log('[CharacterCardManager] Received cloudsave sync:', data.action);
    loadCharacterCards().catch(e => console.warn('Cloudsave sync refresh failed:', e));
}

(function initCloudsaveSync() {
    if (typeof BroadcastChannel === 'function') {
        try {
            const channel = new BroadcastChannel(CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME);
            channel.onmessage = function (event) {
                handleCloudsaveCharacterSync(event.data);
            };
        } catch (e) {
            console.warn('BroadcastChannel init failed:', e);
        }
    }

    window.addEventListener('storage', function (event) {
        if (event.key !== CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY) return;
        try {
            const data = JSON.parse(event.newValue);
            handleCloudsaveCharacterSync(data);
        } catch (e) {
            console.warn('localStorage sync parse failed:', e);
        }
    });
})();

// sendBeacon 生命周期
window.addEventListener('beforeunload', function () {
    try {
        navigator.sendBeacon('/api/beacon/shutdown');
    } catch (e) { /* ignore */ }
});

window.addEventListener('unload', function () {
    try {
        navigator.sendBeacon('/api/beacon/shutdown');
    } catch (e) { /* ignore */ }
});

// =========================================================================
// 清理遗留记忆（Legacy Memory Cleanup）
// -----------------------------------------------------------------------
// 流程：按钮点击 → openLegacyMemoryModal() → fetch GET /api/memory/legacy/scan
// → 填充表格 → 用户勾选 → legacyMemoryPurgeSelected() → POST /api/memory/legacy/purge
// → toast 汇报 → 重新扫描刷新弹层
// =========================================================================

// 最近一次 scan 结果缓存（用于快捷全选/只选未关联的复用）
let _legacyMemoryLastScan = null;

function _legacyMemoryI18n(key, fallback, opts) {
    try {
        if (window.t) {
            const v = window.t(key, opts || {});
            if (v && v !== key) return v;
        }
    } catch (_) { /* ignore */ }
    return fallback;
}

function _legacyFormatSize(bytes) {
    if (typeof bytes !== 'number' || bytes < 0) return '—';
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let v = bytes;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function _legacyEscapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function openLegacyMemoryModal() {
    const modal = document.getElementById('legacyMemoryModal');
    if (!modal) return;
    modal.style.display = 'flex';
    // 重置状态
    const tableWrap = document.getElementById('legacy-memory-table-wrap');
    const toolbar = document.getElementById('legacy-memory-toolbar');
    const runtimeInfo = document.getElementById('legacy-memory-runtime-info');
    const deleteBtn = document.getElementById('legacy-memory-delete-btn');
    const deleteCount = document.getElementById('legacy-memory-delete-count');
    if (tableWrap) {
        tableWrap.innerHTML = `<div class="empty-state"><p>${_legacyEscapeHtml(
            _legacyMemoryI18n('steam.legacyScanLoading', '扫描中...')
        )}</p></div>`;
    }
    if (toolbar) toolbar.style.display = 'none';
    if (runtimeInfo) runtimeInfo.textContent = '';
    if (deleteBtn) deleteBtn.disabled = true;
    if (deleteCount) deleteCount.textContent = ' (0)';
    // 发起扫描
    _legacyMemoryScan();
}

function closeLegacyMemoryModal() {
    const modal = document.getElementById('legacyMemoryModal');
    if (modal) modal.style.display = 'none';
}

function closeLegacyMemoryModalOnOutsideClick(event) {
    if (event && event.target && event.target.id === 'legacyMemoryModal') {
        closeLegacyMemoryModal();
    }
}

function _legacyMemoryScan() {
    fetch('/api/memory/legacy/scan')
        .then((resp) => resp.json().then((data) => ({ resp, data })).catch(() => ({ resp, data: null })))
        .then(({ resp, data }) => {
            // 只记录状态 + 汇总计数；legacy_roots 里包含 Documents 路径，不落日志
            console.info('[legacy memory scan]', {
                status: resp.status,
                ok: resp.ok,
                success: !!(data && data.success),
                total_entries: data && data.total_entries,
                total_size_bytes: data && data.total_size_bytes,
                root_count: data && Array.isArray(data.legacy_roots) ? data.legacy_roots.length : 0,
            });
            if (!resp.ok || !data || !data.success) {
                const errMsg = (data && data.error) || `HTTP ${resp.status}`;
                const tableWrap = document.getElementById('legacy-memory-table-wrap');
                if (tableWrap) {
                    tableWrap.innerHTML = `<div class="empty-state"><p style="color:#e57373;">${_legacyEscapeHtml(
                        _legacyMemoryI18n('steam.legacyScanFailed', '扫描失败') + ': ' + errMsg
                    )}</p></div>`;
                }
                return;
            }
            _legacyMemoryLastScan = data;
            _legacyMemoryRenderTable(data);
        })
        .catch((err) => {
            console.error('[legacy memory scan] 失败:', err);
            const tableWrap = document.getElementById('legacy-memory-table-wrap');
            if (tableWrap) {
                tableWrap.innerHTML = `<div class="empty-state"><p style="color:#e57373;">${_legacyEscapeHtml(
                    _legacyMemoryI18n('steam.legacyScanFailed', '扫描失败') + ': ' + (err && err.message ? err.message : err)
                )}</p></div>`;
            }
        });
}

function _legacyMemoryRenderTable(data) {
    const tableWrap = document.getElementById('legacy-memory-table-wrap');
    const toolbar = document.getElementById('legacy-memory-toolbar');
    const runtimeInfo = document.getElementById('legacy-memory-runtime-info');
    if (!tableWrap) return;

    if (runtimeInfo) {
        const runtimePath = data.runtime_memory_dir || '-';
        runtimeInfo.textContent = _legacyMemoryI18n(
            'steam.legacyRuntimeMemory',
            `runtime memory: ${runtimePath}`,
            { path: runtimePath }
        );
    }

    // 总条目数为 0 → empty state
    if (!data.legacy_roots || data.total_entries === 0) {
        tableWrap.innerHTML = `<div class="empty-state"><p>${_legacyEscapeHtml(
            _legacyMemoryI18n('steam.legacyScanEmpty', '未发现遗留记忆，无需清理')
        )}</p></div>`;
        if (toolbar) toolbar.style.display = 'none';
        const deleteBtn = document.getElementById('legacy-memory-delete-btn');
        if (deleteBtn) deleteBtn.disabled = true;
        return;
    }

    // 构造表格
    const rows = [];
    let globalIndex = 0;
    for (const root of data.legacy_roots) {
        if (!root.entries || root.entries.length === 0) continue;
        rows.push(`
            <tr>
                <td colspan="5" style="background:#2a2a2a;color:#ccc;padding:6px 10px;font-size:12px;">
                    <strong>${_legacyEscapeHtml(root.root)}</strong>
                    <span style="color:#888;margin-left:8px;">[${_legacyEscapeHtml(root.source || '')}]</span>
                </td>
            </tr>
        `);
        for (const entry of root.entries) {
            const statusLabel = entry.is_unlinked
                ? _legacyMemoryI18n('steam.legacyStatusUnlinked', '未关联')
                : (entry.runtime_has_same_name
                    ? _legacyMemoryI18n('steam.legacyStatusDuplicate', '已有同名副本')
                    : _legacyMemoryI18n('steam.legacyStatusListed', '仍在角色列表'));
            const statusColor = entry.is_unlinked ? '#e57373' : (entry.runtime_has_same_name ? '#64b5f6' : '#9e9e9e');
            const sizeStr = _legacyFormatSize(entry.size_bytes);
            rows.push(`
                <tr data-index="${globalIndex}" data-unlinked="${entry.is_unlinked ? '1' : '0'}">
                    <td style="padding:6px 10px;width:30px;">
                        <input type="checkbox" class="legacy-memory-row-cb" data-path="${_legacyEscapeHtml(entry.path)}" onchange="_legacyMemoryUpdateDeleteCount()">
                    </td>
                    <td style="padding:6px 10px;">${_legacyEscapeHtml(entry.name)}</td>
                    <td style="padding:6px 10px;color:#888;font-size:12px;word-break:break-all;">${_legacyEscapeHtml(entry.path)}</td>
                    <td style="padding:6px 10px;text-align:right;color:#ccc;">${_legacyEscapeHtml(sizeStr)}</td>
                    <td style="padding:6px 10px;color:${statusColor};font-weight:500;">${_legacyEscapeHtml(statusLabel)}</td>
                </tr>
            `);
            globalIndex++;
        }
    }

    tableWrap.innerHTML = `
        <div style="overflow-x:auto;max-height:50vh;overflow-y:auto;border:1px solid #333;border-radius:4px;">
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead style="position:sticky;top:0;background:#1a1a1a;z-index:1;">
                    <tr>
                        <th style="padding:6px 10px;text-align:left;"></th>
                        <th style="padding:6px 10px;text-align:left;" data-i18n="steam.legacyColName">名称</th>
                        <th style="padding:6px 10px;text-align:left;" data-i18n="steam.legacyColPath">路径</th>
                        <th style="padding:6px 10px;text-align:right;" data-i18n="steam.legacyColSize">大小</th>
                        <th style="padding:6px 10px;text-align:left;" data-i18n="steam.legacyColStatus">状态</th>
                    </tr>
                </thead>
                <tbody>${rows.join('')}</tbody>
            </table>
        </div>
        <div style="margin-top:10px;color:#888;font-size:12px;">
            ${_legacyEscapeHtml(_legacyMemoryI18n(
                'steam.legacyScanFooter',
                `共 ${data.total_entries} 条，总大小约 ${_legacyFormatSize(data.total_size_bytes)}`,
                { count: data.total_entries, size: _legacyFormatSize(data.total_size_bytes) }
            ))}
        </div>
    `;
    if (toolbar) toolbar.style.display = 'flex';
    _legacyMemoryUpdateDeleteCount();
}

function _legacyMemoryUpdateDeleteCount() {
    const cbs = document.querySelectorAll('.legacy-memory-row-cb');
    let checked = 0;
    cbs.forEach((cb) => { if (cb.checked) checked++; });
    const deleteBtn = document.getElementById('legacy-memory-delete-btn');
    const deleteCount = document.getElementById('legacy-memory-delete-count');
    if (deleteBtn) deleteBtn.disabled = checked === 0;
    if (deleteCount) deleteCount.textContent = ` (${checked})`;
}

function legacyMemorySelectAll() {
    document.querySelectorAll('.legacy-memory-row-cb').forEach((cb) => { cb.checked = true; });
    _legacyMemoryUpdateDeleteCount();
}

function legacyMemorySelectNone() {
    document.querySelectorAll('.legacy-memory-row-cb').forEach((cb) => { cb.checked = false; });
    _legacyMemoryUpdateDeleteCount();
}

function legacyMemorySelectUnlinked() {
    document.querySelectorAll('tr[data-index]').forEach((tr) => {
        const cb = tr.querySelector('.legacy-memory-row-cb');
        if (!cb) return;
        cb.checked = tr.getAttribute('data-unlinked') === '1';
    });
    _legacyMemoryUpdateDeleteCount();
}

function legacyMemoryPurgeSelected() {
    const cbs = document.querySelectorAll('.legacy-memory-row-cb');
    const paths = [];
    cbs.forEach((cb) => {
        if (cb.checked) {
            const p = cb.getAttribute('data-path');
            if (p) paths.push(p);
        }
    });
    if (paths.length === 0) return;

    const confirmMsg = _legacyMemoryI18n(
        'steam.legacyDeleteConfirm',
        `确认永久删除 ${paths.length} 个目录？此操作不可撤销。`,
        { count: paths.length }
    );
    if (!window.confirm(confirmMsg)) return;

    const deleteBtn = document.getElementById('legacy-memory-delete-btn');
    if (deleteBtn) deleteBtn.disabled = true;

    fetch('/api/memory/legacy/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths }),
    })
        .then((resp) => resp.json().then((data) => ({ resp, data })).catch(() => ({ resp, data: null })))
        .then(({ resp, data }) => {
            // 只记录状态 + 计数；removed / errors 内容含本地路径，不落日志
            console.info('[legacy memory purge]', {
                status: resp.status,
                ok: resp.ok,
                success: !!(data && data.success),
                removed_count: data && Array.isArray(data.removed) ? data.removed.length : 0,
                error_count: data && Array.isArray(data.errors) ? data.errors.length : 0,
            });
            if (!resp.ok || !data || !data.success) {
                const errMsg = (data && data.error) || `HTTP ${resp.status}`;
                showMessage(
                    _legacyMemoryI18n('steam.legacyDeleteFailed', '清理失败') + ': ' + errMsg,
                    'error',
                    6000
                );
                if (deleteBtn) deleteBtn.disabled = false;
                return;
            }
            const okCount = Array.isArray(data.removed) ? data.removed.length : 0;
            const failCount = Array.isArray(data.errors) ? data.errors.length : 0;
            const msg = _legacyMemoryI18n(
                'steam.legacyDeleteDone',
                `已删除 ${okCount} 条，失败 ${failCount} 条`,
                { ok: okCount, failed: failCount }
            );
            showMessage(msg, failCount > 0 ? 'warning' : 'success', 5000);
            if (failCount > 0) {
                console.warn('[legacy memory purge errors]', data.errors);
            }
            // 刷新扫描
            _legacyMemoryScan();
        })
        .catch((err) => {
            console.error('[legacy memory purge] 失败:', err);
            showMessage(
                _legacyMemoryI18n('steam.legacyDeleteFailed', '清理失败') + ': ' + (err && err.message ? err.message : err),
                'error',
                6000
            );
            if (deleteBtn) deleteBtn.disabled = false;
        });
}

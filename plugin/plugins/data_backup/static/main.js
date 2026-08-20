const PLUGIN_ID = 'data_backup';
const RUNS_URL = '/runs';
const GROUP_COPY = {
  core: ['核心数据', '配置、角色卡与长期记忆'],
  assets: ['模型资源', '角色立绘、Live2D、VRM、MMD、PngTuber 与创意工坊资源'],
};

let activeGroup = 'core';
let state = null;
let pendingConfirmation = null;

const $ = (selector) => document.querySelector(selector);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setNotice(message = '', type = '') {
  const notice = $('#notice');
  notice.textContent = message;
  notice.className = `notice ${type}`.trim();
}

function warningText(payload) {
  return Array.isArray(payload?.warnings)
    ? payload.warnings.filter(Boolean).join('；')
    : '';
}

function setBusy(busy) {
  document.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
}

function openConfirmation(action, snapshotId) {
  const restoring = action === 'restore';
  pendingConfirmation = { action, snapshotId };
  $('#confirm-label').textContent = restoring ? 'RESTORE SNAPSHOT' : 'DELETE SNAPSHOT';
  $('#confirm-title').textContent = restoring ? '确认恢复快照' : '确认删除快照';
  $('#confirm-description').textContent = restoring
    ? '当前备份组会恢复到此快照的状态，操作前将自动创建安全快照。'
    : '删除后将无法从快照列表恢复，请确认目标无误。';
  $('#confirm-snapshot-id').textContent = snapshotId;
  $('#confirm-input').value = '';
  $('#confirm-error').textContent = '';
  const submit = $('#confirm-submit');
  submit.textContent = restoring ? '确认恢复' : '确认删除';
  submit.className = `${restoring ? 'primary' : 'danger'} dialog-submit`;
  $('#confirm-dialog').showModal();
  $('#confirm-input').focus();
}

function closeConfirmation() {
  const dialog = $('#confirm-dialog');
  if (dialog.open) dialog.close();
  pendingConfirmation = null;
}

function openDirectoryDialog() {
  if (!state) return;
  $('#directory-input').value = state.backup_root;
  $('#default-backup-root').textContent = state.default_backup_root;
  $('#directory-error').textContent = '';
  $('#directory-dialog').showModal();
  $('#directory-input').focus();
  $('#directory-input').select();
}

function closeDirectoryDialog() {
  const dialog = $('#directory-dialog');
  if (dialog.open) dialog.close();
}

async function saveBackupDirectory(directory) {
  $('#directory-error').textContent = '';
  setBusy(true);
  try {
    state = await callPlugin('backup_set_directory', { directory });
    render();
    closeDirectoryDialog();
    setNotice(`备份目录已切换为 ${state.backup_root}`, 'success');
  } catch (error) {
    $('#directory-error').textContent = error.message || String(error);
  } finally {
    setBusy(false);
  }
}

function submitConfirmation() {
  if (!pendingConfirmation) return;
  const { action, snapshotId } = pendingConfirmation;
  if ($('#confirm-input').value.trim() !== snapshotId) {
    $('#confirm-error').textContent = '快照 ID 不匹配，请重新输入。';
    $('#confirm-input').focus();
    return;
  }
  closeConfirmation();
  if (action === 'restore') restoreSnapshot(snapshotId);
  else deleteSnapshot(snapshotId);
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '—';
}

function updateScheduleToggle() {
  if (!state?.schedule) return;
  const enabled = $('#schedule-enabled').checked;
  const changed = enabled !== state.schedule.enabled;
  $('#schedule-toggle').classList.toggle('is-enabled', enabled);
  $('#schedule-options').classList.toggle('is-muted', !enabled);
  $('#schedule-toggle-state').textContent = enabled ? '开启' : '关闭';
  $('#schedule-toggle-title').textContent = changed
    ? enabled ? '将开启定时快照' : '将关闭定时快照'
    : enabled ? '定时快照已开启' : '定时快照已关闭';
  $('#schedule-toggle-help').textContent = changed
    ? '当前更改尚未保存，点击下方按钮后生效。'
    : enabled
      ? '到达设定时间后自动创建快照，可随时在这里关闭。'
      : '不会自动创建快照，仍可随时手动备份。';
}

function renderSchedule() {
  const schedule = state?.schedule;
  if (!schedule) return;
  $('#schedule-enabled').checked = schedule.enabled;
  $('#schedule-interval').value = schedule.interval_days;
  $('#schedule-core').checked = schedule.groups.includes('core');
  $('#schedule-assets').checked = schedule.groups.includes('assets');
  updateScheduleToggle();
  $('#schedule-status').textContent = schedule.running
    ? '正在创建定时快照…'
    : schedule.enabled
      ? `下次执行：${formatTime(schedule.next_run_at)}`
      : '已关闭';
  $('#schedule-history').textContent = schedule.last_run_at
    ? `上次执行：${formatTime(schedule.last_run_at)}`
    : '尚未执行过定时快照';
  const scheduleIssue = $('#schedule-error');
  scheduleIssue.className = schedule.last_error ? 'schedule-error' : 'schedule-warning';
  scheduleIssue.textContent = schedule.last_error
    ? `最近错误：${schedule.last_error}`
    : schedule.last_warning
      ? `最近警告：${schedule.last_warning}`
      : '';
}

async function callPlugin(entryId, args = {}) {
  const created = await fetch(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entryId, args }),
  });
  if (!created.ok) throw new Error(`创建任务失败（HTTP ${created.status}）`);
  const createdPayload = await created.json();
  const runId = createdPayload.run_id || createdPayload.id;
  if (!runId) throw new Error('任务 ID 缺失');

  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    await sleep(500);
    const response = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}`, { cache: 'no-store' });
    if (!response.ok) continue;
    const record = await response.json();
    if (record.status === 'succeeded') {
      const exported = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}/export`, { cache: 'no-store' });
      if (!exported.ok) throw new Error(`读取结果失败（HTTP ${exported.status}）`);
      const payload = await exported.json();
      const item = (payload.items || []).find((candidate) => candidate.type === 'json');
      const result = item?.json || {};
      if (result.success === false || result.error) {
        throw new Error(result.error?.message || result.message || '插件调用失败');
      }
      return result.data || {};
    }
    if (['failed', 'canceled', 'timeout'].includes(record.status)) {
      throw new Error(record.error?.message || record.message || record.status);
    }
  }
  throw new Error('操作超时');
}

function render() {
  if (!state) return;
  const group = state.groups[activeGroup];
  const [title, description] = GROUP_COPY[activeGroup];
  $('#data-root').textContent = state.data_root;
  $('#backup-root').textContent = state.backup_root;
  $('#retention').textContent = `${state.retention} 份`;
  renderSchedule();
  $('#group-title').textContent = title;
  $('#group-description').textContent = description;
  $('#paths').replaceChildren(...group.paths.map((path) => {
    const chip = document.createElement('span');
    chip.textContent = path;
    return chip;
  }));
  $('#snapshot-count').textContent = `${group.snapshots.length} 份`;

  const list = $('#snapshots');
  list.replaceChildren();
  if (!group.snapshots.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    const mark = document.createElement('span');
    mark.className = 'empty-mark';
    mark.textContent = '◇';
    const title = document.createElement('strong');
    title.textContent = '还没有快照';
    const hint = document.createElement('span');
    hint.textContent = '创建第一份快照，为重要数据留一个安心的还原点。';
    empty.append(mark, title, hint);
    list.append(empty);
    return;
  }

  group.snapshots.forEach((snapshot) => {
    const row = document.createElement('article');
    row.className = 'snapshot';
    const meta = document.createElement('div');
    meta.className = 'snapshot-meta';
    const id = document.createElement('code');
    id.className = 'snapshot-id';
    id.textContent = snapshot.id;
    const detail = document.createElement('div');
    detail.className = 'snapshot-detail';
    detail.textContent = `${new Date(snapshot.created_at).toLocaleString()} · ${snapshot.file_count} 个文件 · ${formatBytes(snapshot.total_bytes)}`;
    meta.append(id, detail);

    const actions = document.createElement('div');
    actions.className = 'snapshot-actions';
    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'secondary';
    restore.textContent = '恢复';
    restore.addEventListener('click', () => openConfirmation('restore', snapshot.id));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'danger';
    remove.textContent = '删除';
    remove.addEventListener('click', () => openConfirmation('delete', snapshot.id));
    actions.append(restore, remove);
    row.append(meta, actions);
    list.append(row);
  });
}

async function refresh() {
  setBusy(true);
  setNotice('正在读取快照…');
  try {
    state = await callPlugin('backup_status');
    render();
    const warning = warningText(state);
    setNotice(warning, warning ? 'warning' : '');
  } catch (error) {
    setNotice(error.message || String(error), 'error');
  } finally {
    setBusy(false);
  }
}

async function createSnapshot() {
  setBusy(true);
  setNotice('正在创建快照，请勿关闭页面…');
  try {
    const snapshot = await callPlugin('backup_create', { group: activeGroup });
    state = await callPlugin('backup_status');
    render();
    const warning = warningText(snapshot) || warningText(state);
    setNotice(
      warning ? `快照 ${snapshot.id} 已创建，但需要处理：${warning}` : `快照 ${snapshot.id} 已创建。`,
      warning ? 'warning' : 'success',
    );
  } catch (error) {
    setNotice(error.message || String(error), 'error');
  } finally {
    setBusy(false);
  }
}

async function restoreSnapshot(snapshotId) {
  setBusy(true);
  setNotice('正在校验并恢复快照，请勿关闭 N.E.K.O…');
  try {
    const result = await callPlugin('backup_restore', { group: activeGroup, snapshot_id: snapshotId, confirmation: snapshotId });
    const safety = result.safety_snapshot
      ? `安全快照为 ${result.safety_snapshot}`
      : '当前备份组为空，未创建安全快照';
    setNotice(`恢复完成；${safety}。请立即重启 N.E.K.O。`, 'success');
    state = await callPlugin('backup_status');
    render();
    const warning = warningText(result) || warningText(state);
    if (warning) setNotice(`恢复完成；${safety}，但需要处理：${warning}。请立即重启 N.E.K.O。`, 'warning');
  } catch (error) {
    setNotice(error.message || String(error), 'error');
  } finally {
    setBusy(false);
  }
}

async function deleteSnapshot(snapshotId) {
  setBusy(true);
  setNotice('正在删除快照…');
  try {
    await callPlugin('backup_delete', { group: activeGroup, snapshot_id: snapshotId, confirmation: snapshotId });
    state = await callPlugin('backup_status');
    render();
    const warning = warningText(state);
    setNotice(
      warning ? `快照已删除，但需要处理：${warning}` : `快照 ${snapshotId} 已删除。`,
      warning ? 'warning' : 'success',
    );
  } catch (error) {
    setNotice(error.message || String(error), 'error');
  } finally {
    setBusy(false);
  }
}

async function saveSchedule() {
  const intervalDays = Number.parseInt($('#schedule-interval').value, 10);
  const groups = ['core', 'assets'].filter((group) => $(`#schedule-${group}`).checked);
  if (!Number.isInteger(intervalDays) || intervalDays < 1 || intervalDays > 365) {
    setNotice('定时周期必须是 1 到 365 天。', 'error');
    return;
  }
  if (!groups.length) {
    setNotice('请至少选择一个备份组。', 'error');
    return;
  }
  setBusy(true);
  setNotice('正在保存定时快照计划…');
  try {
    state = await callPlugin('backup_set_schedule', {
      enabled: $('#schedule-enabled').checked,
      interval_days: intervalDays,
      groups,
    });
    render();
    setNotice(state.schedule.enabled ? '定时快照计划已启用。' : '定时快照已关闭。', 'success');
  } catch (error) {
    setNotice(error.message || String(error), 'error');
  } finally {
    setBusy(false);
  }
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    activeGroup = tab.dataset.group;
    document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === tab));
    render();
  });
});
$('#refresh').addEventListener('click', refresh);
$('#create').addEventListener('click', createSnapshot);
$('#change-directory').addEventListener('click', openDirectoryDialog);
$('#schedule-enabled').addEventListener('change', updateScheduleToggle);
$('#schedule-form').addEventListener('submit', (event) => {
  event.preventDefault();
  saveSchedule();
});
$('#confirm-form').addEventListener('submit', (event) => {
  event.preventDefault();
  submitConfirmation();
});
$('#confirm-cancel').addEventListener('click', closeConfirmation);
$('#confirm-close').addEventListener('click', closeConfirmation);
$('#confirm-dialog').addEventListener('cancel', (event) => {
  event.preventDefault();
  closeConfirmation();
});
$('#directory-form').addEventListener('submit', (event) => {
  event.preventDefault();
  saveBackupDirectory($('#directory-input').value.trim());
});
$('#directory-default').addEventListener('click', () => saveBackupDirectory(''));
$('#directory-cancel').addEventListener('click', closeDirectoryDialog);
$('#directory-close').addEventListener('click', closeDirectoryDialog);
$('#directory-dialog').addEventListener('cancel', (event) => {
  event.preventDefault();
  closeDirectoryDialog();
});
refresh();

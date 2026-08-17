(function () {
  'use strict';

  const PLUGIN_ID = 'study_companion';
  const URLS = Object.freeze({
    tesseract: `/plugin/${PLUGIN_ID}/ui-api/tesseract/install`,
    rapidocr_models: `/plugin/${PLUGIN_ID}/ui-api/rapidocr-models`,
  });
  const state = { kind: '', taskId: '', task: null, source: null, busy: false, dependencies: {}, refresh: null };

  function t(key, fallback) {
    return window.I18n && typeof window.I18n.t === 'function' ? window.I18n.t(key, fallback) : fallback;
  }

  function tf(key, fallback, values) {
    let value = t(key, fallback);
    Object.entries(values || {}).forEach(([name, replacement]) => {
      value = value.replaceAll(`{${name}}`, String(replacement));
    });
    return value;
  }

  function ready(item) {
    return item?.installed === true || item?.available === true || item?.ready === true;
  }

  function terminal(task) {
    return ['completed', 'succeeded', 'failed', 'cancelled', 'canceled'].includes(String(task?.status || '').toLowerCase());
  }

  function statusKey(name, item) {
    if (ready(item)) return 'status_ready';
    const detail = String(item?.detail || '').toLowerCase();
    if (name === 'rapidocr') {
      if (detail === 'missing_model_files') return 'status_models_missing';
      if (detail === 'broken_runtime') return 'status_runtime_broken';
      return 'status_runtime_missing';
    }
    if (name === 'tesseract' && detail === 'missing_languages') return 'status_languages_missing';
    return name === 'dxcam' ? 'status_capture_missing' : 'status_not_installed';
  }

  function errorKey(kind, task) {
    const value = `${task?.diagnostic || ''} ${task?.error || ''} ${task?.phase || ''}`.toLowerCase();
    if (value.includes('timeout')) return 'error_install_timeout';
    if (value.includes('permission') || value.includes('access denied')) return 'error_install_permission_denied';
    if (value.includes('manifest') || value.includes('checksum')) return 'error_install_manifest_invalid';
    if (value.includes('network') || value.includes('download') || value.includes('connection')) {
      return kind === 'rapidocr_models' ? 'error_rapidocr_model_download_failed' : 'error_install_network';
    }
    if (value.includes('incomplete') || value.includes('interrupted')) return 'error_install_incomplete';
    if (value.includes('dependency_refresh_failed')) return 'error_dependency_refresh_failed';
    return 'error_unknown';
  }

  function render(dependencies) {
    state.dependencies = dependencies || {};
    const readiness = state.dependencies.ocr_readiness || {};
    const selected = String(readiness.selected_backend || '').toLowerCase();
    const chain = document.getElementById('settingsOcrChainStatus');
    if (chain) {
      const key = readiness.diagnostic === 'ocr_disabled'
        ? 'chain_disabled' : readiness.ready === true ? 'chain_ready' : 'chain_unavailable';
      chain.textContent = t(`ui.settings.dependencies.${key}`, key === 'chain_ready'
        ? 'The current OCR path is ready' : key === 'chain_disabled' ? 'OCR is disabled' : 'The current OCR path is unavailable');
    }

    ['rapidocr', 'tesseract', 'dxcam'].forEach((name) => {
      const row = document.querySelector(`[data-dependency="${name}"]`);
      if (!row) return;
      const item = state.dependencies[name] || {};
      const current = name === selected;
      const role = row.querySelector('.dependency-row__role');
      const label = row.querySelector('.dependency-row__status');
      const action = row.querySelector('.dependency-row__action');
      if (role) role.textContent = name === 'dxcam'
        ? ''
        : t(`ui.settings.dependencies.${current ? 'current' : 'optional'}`, current ? 'In use' : 'Optional');
      const key = statusKey(name, item);
      if (label) label.textContent = t(`ui.settings.dependencies.${key}`, key === 'status_ready' ? 'Ready' : 'Not installed');
      if (!action) return;
      const kind = action.dataset.dependencyAction;
      const allowed = kind === 'tesseract'
        ? item.can_install === true && !ready(item)
        : item.can_download_models === true && String(item.detail || '').toLowerCase() === 'missing_model_files';
      const failed = state.kind === kind && String(state.task?.status || '').toLowerCase() === 'failed';
      action.hidden = !allowed;
      action.disabled = state.busy;
      action.textContent = state.busy && state.kind === kind
        ? t('ui.settings.dependencies.working', 'Working...')
        : failed ? t('ui.settings.dependencies.retry', 'Retry')
          : t(`ui.settings.dependencies.${kind === 'tesseract' ? 'install_tesseract' : 'download_models'}`, kind === 'tesseract' ? 'Install Tesseract' : 'Download models');
    });

    const rapidocr = state.dependencies.rapidocr || {};
    const runtimeMissing = String(rapidocr.detail || '').toLowerCase() === 'missing';
    const failedTask = String(state.task?.status || '').toLowerCase() === 'failed' ? state.task : null;
    const help = document.getElementById('settingsDependencyHelp');
    if (help) {
      help.hidden = !runtimeMissing && !failedTask;
      help.textContent = runtimeMissing
        ? `${t('ui.settings.dependencies.source_fix', 'Source environment: run uv sync --group galgame, then restart the backend.')}\n${t('ui.settings.dependencies.package_fix', 'Packaged app: repair or reinstall N.E.K.O.')}`
        : failedTask ? t(`ui.settings.dependencies.${errorKey(state.kind, failedTask)}`, 'The dependency operation failed. Please retry.') : '';
    }

    const progressBox = document.getElementById('settingsDependencyProgress');
    if (progressBox) {
      const visible = state.busy && !terminal(state.task);
      const raw = Number(state.task?.progress) || 0;
      const progress = Math.max(0, Math.min(100, Math.round(raw <= 1 ? raw * 100 : raw)));
      progressBox.hidden = !visible;
      const bar = progressBox.querySelector('progress');
      const text = progressBox.querySelector('span');
      if (bar) bar.value = progress;
      if (text) text.textContent = tf('ui.settings.dependencies.progress', '{progress}% complete', { progress });
    }
  }

  async function apply(kind, task) {
    if (state.kind !== kind) return;
    state.task = task || {};
    state.busy = !terminal(state.task);
    render(state.dependencies);
    if (!terminal(state.task)) return;
    state.source?.close();
    state.source = null;
    if (['completed', 'succeeded'].includes(String(state.task.status || '').toLowerCase()) && state.refresh) {
      try {
        await state.refresh();
      } catch (_) {
        state.task = { status: 'failed', diagnostic: 'dependency_refresh_failed' };
        state.busy = false;
        render(state.dependencies);
      }
    }
  }

  async function poll(kind, taskId) {
    let consecutiveFailures = 0;
    while (state.busy && state.kind === kind && state.taskId === taskId) {
      try {
        const response = await fetch(`${URLS[kind]}/${encodeURIComponent(taskId)}`);
        if (!response.ok) throw new Error('install_status_unavailable');
        await apply(kind, await response.json());
        consecutiveFailures = 0;
        if (!state.busy) return;
      } catch (_) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 3) {
          await apply(kind, { status: 'failed', diagnostic: 'install_network_error' });
          return;
        }
      }
      if (state.busy) await new Promise((resolve) => setTimeout(resolve, 900));
    }
  }

  function watch(kind, taskId) {
    if (typeof EventSource !== 'function') {
      poll(kind, taskId);
      return;
    }
    const source = new EventSource(`${URLS[kind]}/${encodeURIComponent(taskId)}/stream`);
    state.source = source;
    source.onmessage = (event) => {
      try { apply(kind, JSON.parse(event.data)); } catch (_) { /* GET fallback handles malformed events */ }
    };
    source.onerror = () => {
      if (state.source !== source || !state.busy) return;
      source.close();
      state.source = null;
      poll(kind, taskId);
    };
  }

  async function start(kind) {
    if (state.busy || !URLS[kind]) return;
    state.kind = kind;
    state.taskId = '';
    state.task = { status: 'queued', progress: 0 };
    state.busy = true;
    render(state.dependencies);
    try {
      const response = await fetch(URLS[kind], {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: false }),
      });
      if (!response.ok) throw new Error('install_start_failed');
      const payload = await response.json();
      const taskId = String(payload.task_id || payload.run_id || '');
      if (!taskId) throw new Error('install_task_id_missing');
      state.taskId = taskId;
      await apply(kind, payload.state || payload);
      if (state.busy) watch(kind, taskId);
    } catch (_) {
      await apply(kind, { status: 'failed', diagnostic: 'install_network_error' });
    }
  }

  function initialize(options) {
    state.refresh = typeof options?.refresh === 'function' ? options.refresh : null;
    document.getElementById('settingsDependencyList')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-dependency-action]');
      if (button && !button.disabled) start(button.dataset.dependencyAction);
    });
  }

  window.StudyDependencyController = Object.freeze({ initialize, render });
}());

/* Manual package import owns only its panel; preview/save remain page operations. */
(function () {
    'use strict';
    const TASK_KEY = 'neko.modelWorkshop.task';
    const TASK_ID = /^[a-f0-9]{32}$/;
    const PHASES = new Set(['receiving', 'queued', 'validating', 'staging', 'committing',
        'completed', 'failed', 'cancelled']);

    function mount(panel, host) {
        if (!panel || panel.dataset.mounted) return;
        panel.dataset.mounted = 'true';
        const control = name => panel.querySelector('[data-workshop="' + name + '"]');
        const file = control('file'), start = control('start'), cancel = control('cancel');
        const retry = control('retry'), view = control('view'), progress = control('progress');
        const status = control('status'), resultBox = control('result');
        let taskId = null, upload = null, timer = null, stopped = false;
        let terminal = false, cancelling = false, failures = 0, result = null, token = null;
        let message = 'idle', parameters = {}, retryAction = null;

        function remember(value) {
            // Store only an opaque task handle, never a File, CSRF token or package text.
            try {
                if (value) sessionStorage.setItem(TASK_KEY, value);
                else sessionStorage.removeItem(TASK_KEY);
            } catch (_) { /* Storage can be disabled; in-page polling still works. */ }
        }
        function say(key, params = {}) {
            message = key;
            parameters = params;
            status.textContent = host.translate(key, params);
        }
        function renderLabels() {
            panel.querySelectorAll('[data-i18n^="modelWorkshop."]').forEach(element => {
                element.textContent = host.translate(element.dataset.i18n.slice('modelWorkshop.'.length));
            });
            say(message, parameters);
        }
        function retryWith(action) {
            retryAction = action;
            retry.hidden = !action;
        }
        function busy(value) {
            // Do not lock the manager: the user may keep editing the current model.
            file.disabled = value;
            start.disabled = value || !file.files.length;
            cancel.hidden = !value;
        }
        function errorKey(code) {
            if (code === 'model_workshop_task_not_found') return 'taskMissing';
            if (['csrf_validation_failed', 'local_access_required', 'origin_not_allowed'].includes(code)) return 'accessDenied';
            // Main's body/startup guards reject before parsing. Reuse the translated
            // size/availability messages instead of calling a blocked import a bad package.
            if (['model_bundle_too_large', 'request_body_too_large', 'payload_too_large'].includes(code)) return 'tooLarge';
            if (['model_workshop_busy', 'model_workshop_state_busy', 'model_workshop_closed',
                'storage_startup_blocked', 'model_workshop_storage_blocked'].includes(code)) return 'busy';
            if (code === 'model_workshop_install_conflict') return 'conflict';
            return 'importFailed';
        }
        async function request(path, options = {}) {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 15000);
            try {
                const response = await fetch(path, { ...options, credentials: 'same-origin',
                    redirect: 'error', signal: controller.signal });
                const data = await response.json();
                if (!response.ok) throw Object.assign(new Error('request_failed'), { code: data.error_code });
                return data;
            } finally { clearTimeout(timeout); }
        }
        async function csrf() {
            if (!token) {
                // Same established bootstrap contract used by voice identity; memory only.
                const config = await request('/api/config/page_config');
                if (typeof config.autostart_csrf_token !== 'string' || !config.autostart_csrf_token) {
                    throw new Error('csrf_unavailable');
                }
                token = config.autostart_csrf_token;
            }
            return token;
        }
        async function refresh() {
            const imported = result;
            retryWith(null);
            try {
                const response = await host.refresh(imported);
                if (stopped || result !== imported) return;
                const key = response?.status === 'refreshed' ? 'completed'
                    : response?.status === 'view_changed' ? 'viewChanged' : 'refreshFailed';
                say(imported.already_installed && key === 'completed' ? 'alreadyInstalled' : key);
                if (key === 'refreshFailed') retryWith(refresh);
            } catch (_) {
                if (!stopped && result === imported) { say('refreshFailed'); retryWith(refresh); }
            }
        }
        function validResult(value) {
            if (!value || !['live2d', 'vrm', 'mmd'].includes(value.model_format)
                || !TASK_ID.test(value.install_id || '')
                || !['display_name', 'attribution', 'license', 'url'].every(key => typeof value[key] === 'string')) return false;
            // This is a local resource handle, never an arbitrary URL from package metadata.
            const prefix = { live2d: ['/user_live2d/', '/user_live2d_local/'],
                vrm: ['/user_vrm/'], mmd: ['/user_mmd/'] }[value.model_format];
            const url = new URL(value.url, window.location.href);
            return url.origin === window.location.origin && prefix.some(item => url.pathname.startsWith(item));
        }
        async function accept(data) {
            if (stopped || terminal) return;
            if (data.task_id !== taskId || !PHASES.has(data.phase)) throw new Error('invalid_task');
            if (data.phase === 'completed' && !validResult(data.result)) throw new Error('invalid_result');
            if (['completed', 'failed', 'cancelled'].includes(data.phase)) {
                terminal = true;
                clearTimeout(timer);
                remember(null);
                busy(false);
                progress.hidden = true;
                retryWith(null);
                if (data.phase === 'completed') {
                    result = data.result;
                    // Attribution is unverified package text. Never interpret it as HTML.
                    for (const key of ['display_name', 'attribution', 'license']) control(key).textContent = result[key];
                    resultBox.hidden = false;
                    view.hidden = false;
                    await refresh();
                } else say(data.phase === 'cancelled' ? 'cancelled' : errorKey(data.error_code));
                return;
            }
            cancel.disabled = cancelling || data.phase === 'committing';
            say(cancelling ? 'cancelling' : data.phase);
            schedule();
        }
        function schedule() {
            clearTimeout(timer);
            if (!stopped && !terminal) timer = setTimeout(poll, 1000);
        }
        async function poll() {
            if (stopped || terminal) return;
            const queried = taskId;
            retryWith(null);
            try {
                const data = await request('/api/model-market/tasks/' + queried);
                if (queried !== taskId || upload) return;
                await accept(data);
                failures = 0;
            } catch (error) {
                if (stopped || terminal || queried !== taskId || upload) return;
                if (error.code === 'model_workshop_task_not_found') {
                    // A missing job says nothing about installed files. Never re-upload automatically.
                    terminal = true;
                    remember(null);
                    busy(false);
                    progress.hidden = true;
                    say('taskMissing');
                } else {
                    say('statusUnknown');
                    if (++failures < 3) schedule();
                    else retryWith(() => { failures = 0; return poll(); });
                }
            }
        }
        async function begin() {
            if (upload || (taskId && !terminal) || !file.files.length) return;
            const selected = file.files[0];
            if (!selected.size || !selected.name.toLowerCase().endsWith('.neko-model.zip')) return say('invalidFile');
            if (selected.size > 500 * 1024 * 1024) return say('tooLarge');
            busy(true);
            cancel.disabled = true;
            result = null;
            resultBox.hidden = true;
            view.hidden = true;
            retryWith(null);
            say('preparing');
            try {
                const csrfToken = await csrf();
                if (stopped) return;
                terminal = false;
                taskId = null;
                failures = 0;
                const xhr = new XMLHttpRequest();
                upload = xhr;
                // A reload during POST loses the response handle. Record uncertainty,
                // not a fake cancelled task; the server may already own the package.
                remember('receiving');
                xhr.open('POST', '/api/model-market/imports');
                xhr.setRequestHeader('X-CSRF-Token', csrfToken);
                xhr.responseType = 'json';
                xhr.timeout = 30 * 60 * 1000;
                xhr.upload.onprogress = event => {
                    if (stopped || upload !== xhr) return;
                    if (event.lengthComputable) {
                        progress.max = event.total;
                        progress.value = event.loaded;
                        say('uploading', { percent: Math.floor(event.loaded / event.total * 100) });
                    }
                };
                const uncertain = () => {
                    if (stopped || upload !== xhr) return;
                    upload = null;
                    terminal = true;
                    busy(false);
                    progress.hidden = true;
                    say('uploadUnknown');
                };
                xhr.onerror = xhr.ontimeout = xhr.onabort = uncertain;
                xhr.onload = () => {
                    if (stopped || upload !== xhr) return;
                    upload = null;
                    const data = xhr.response;
                    if (xhr.status === 202 && TASK_ID.test(data?.task_id)) {
                        taskId = data.task_id;
                        remember(taskId);
                        file.value = '';
                        progress.removeAttribute('value');
                        cancel.disabled = false;
                        say('queued');
                        poll();
                    } else if (xhr.status >= 400 && xhr.status < 500 && data?.error_code) {
                        // Only an explicit rejection before acceptance is safe to retry as a new import.
                        remember(null);
                        terminal = true;
                        busy(false);
                        progress.hidden = true;
                        if (data.error_code === 'csrf_validation_failed') token = null;
                        say(errorKey(data.error_code));
                    } else {
                        upload = xhr;
                        uncertain();
                    }
                };
                const body = new FormData();
                body.append('file', selected);
                progress.hidden = false;
                progress.removeAttribute('value');
                cancel.disabled = false;
                say('receiving');
                xhr.send(body); // Native File streaming; no whole-package ArrayBuffer/Blob copy.
            } catch (_) {
                upload = null;
                terminal = true;
                busy(false);
                progress.hidden = true;
                say('accessDenied');
            }
        }
        cancel.addEventListener('click', async () => {
            if (upload) return upload.abort();
            if (!taskId || terminal || cancelling) return;
            cancelling = true;
            cancel.disabled = true;
            say('cancelling');
            try {
                await accept(await request('/api/model-market/tasks/' + taskId + '/cancel', {
                    method: 'POST', headers: { 'X-CSRF-Token': await csrf() }
                }));
            } catch (_) {
                // The request may have reached commit. Query the authoritative state.
                if (!stopped && !terminal) { say('statusUnknown'); schedule(); }
            } finally { cancelling = false; }
        });
        start.addEventListener('click', begin);
        file.addEventListener('change', () => { start.disabled = !file.files.length; });
        retry.addEventListener('click', () => retryAction?.());
        view.addEventListener('click', async () => {
            if (!result || view.disabled) return;
            const imported = result;
            view.disabled = true;
            try {
                const response = await host.view(imported);
                if (!stopped && result === imported) say(response?.status === 'preview_requested' ? 'previewRequested' : 'viewChanged');
            } catch (_) { if (!stopped && result === imported) say('refreshFailed'); }
            finally { view.disabled = false; }
        });
        renderLabels();
        window.i18next?.on('languageChanged', renderLabels);
        window.addEventListener('pagehide', () => {
            stopped = true;
            clearTimeout(timer);
            window.i18next?.off('languageChanged', renderLabels);
            // Closing stops local polling, not an accepted server job.
        }, { once: true });
        let saved;
        try { saved = sessionStorage.getItem(TASK_KEY); } catch (_) { /* See remember(). */ }
        if (TASK_ID.test(saved || '')) {
            taskId = saved;
            busy(true);
            progress.hidden = false;
            progress.removeAttribute('value');
            say('queued');
            poll();
        } else {
            busy(false);
            if (saved === 'receiving') say('uploadUnknown');
            else remember(null);
        }
    }
    window.NekoModelWorkshop = { mount };
})();

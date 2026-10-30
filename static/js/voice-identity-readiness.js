(function (root) {
    'use strict';
    root.createVoiceIdentityReadiness = function (hooks) {
        const originalRequest = hooks.request;
        hooks = { ...hooks, request: async function (path, options) {
            const config = options || {};
            const { timeoutMs = 15000, ...requestConfig } = config;
            const controller = new AbortController();
            const abort = () => controller.abort();
            if (config.signal) { if (config.signal.aborted) abort(); else config.signal.addEventListener('abort', abort, { once: true }); }
            let timer;
            try {
                return await Promise.race([
                    originalRequest(path, { ...requestConfig, signal: controller.signal }),
                    new Promise((_, reject) => { timer = root.setTimeout(() => { abort(); reject(new Error('request_timeout')); }, timeoutMs); })
                ]);
            } finally { root.clearTimeout(timer); if (config.signal) config.signal.removeEventListener('abort', abort); }
        } };
        const el = {};
        ['microphone', 'actual-device', 'input-notice', 'input-setup', 'input-settings', 'input-close', 'input-summary', 'resource-card', 'resource-overview', 'resource-progress', 'resource-progress-panel', 'resource-phase', 'resource-step', 'resource-targets', 'resource-download-info', 'gain', 'gain-value', 'meter', 'test', 'test-cancel', 'test-result', 'resources', 'prepare', 'download', 'repair', 'resource-cancel', 'resource-message', 'wake-enable'].forEach(name => { el[name] = document.getElementById('voice-identity-' + name); });
        if (!el.microphone) return null;
        const t = hooks.translate;
        const completeDialog = document.getElementById('voice-identity-resource-complete');
        const completeClose = document.getElementById('voice-identity-resource-complete-close');
        let completeNoticeTimer = null;
        const resourceDetails = document.getElementById('voice-identity-resource-details');
        let resourceCollapseTimer = null;
        let resourceOverviewHidden = false;
        function cancelResourceCollapse() {
            if (resourceCollapseTimer !== null) root.clearTimeout(resourceCollapseTimer);
            resourceCollapseTimer = null;
        }
        function canCollapseResources() {
            return resources && resources.can_enroll === true && !resourceFeedbackIssue
                && Object.values(resources.resources || {}).every(value => !value.required || value.state === 'ready');
        }
        function scheduleResourceCollapse(at) {
            cancelResourceCollapse();
            if (!resourceDetails || !canCollapseResources()) return;
            const timer = root.setTimeout(() => {
                if (resourceCollapseTimer !== timer) return;
                resourceCollapseTimer = null;
                if (at !== epoch || pageHidden || pending || operation || hooks.enrolling() || !canCollapseResources()) return;
                if (typeof resourceDetails.contains === 'function' && resourceDetails.contains(document.activeElement)) {
                    el.repair.focus();
                }
                resourceDetails.open = false;
                resourceOverviewHidden = true;
                inputSetupRequested = false;
                render(false);
            }, 5000);
            resourceCollapseTimer = timer;
        }
        resourceDetails?.addEventListener('toggle', cancelResourceCollapse);
        function closeResourceNotice() {
            if (completeNoticeTimer !== null) root.clearTimeout(completeNoticeTimer);
            completeNoticeTimer = null;
            if (completeDialog && completeDialog.open) completeDialog.close();
        }
        function showResourceNotice(at, needsAttention = false) {
            if (at !== epoch || pageHidden || !completeDialog || typeof completeDialog.showModal !== 'function') return;
            closeResourceNotice();
            completeDialog.classList.toggle('needs-attention', needsAttention);
            const title = document.getElementById('voice-identity-resource-complete-title');
            const titleKey = needsAttention ? 'voiceIdentity.resourceCheckAttention' : 'voiceIdentity.resourceCheckComplete';
            title.setAttribute('data-i18n', titleKey);
            title.textContent = t(titleKey, needsAttention ? 'Check finished: action needed' : 'Check complete');
            const help = document.getElementById('voice-identity-resource-complete-help');
            const resourcesKey = resourcesHintKey();
            const helpKey = needsAttention
                ? resourcesKey === 'voiceIdentity.resourcesReady' ? 'voiceIdentity.resourceRepair' : resourcesKey
                : 'voiceIdentity.resourcesReadyForInput';
            help.setAttribute('data-i18n', helpKey);
            help.textContent = t(helpKey, RESOURCE_HINT_FALLBACKS[helpKey] || (needsAttention ? 'Check the resource status and repair the unavailable components.' : 'Resources are ready. Select your microphone and complete the input test below.'));
            const unresolved = needsAttention && repairDetails();
            if (unresolved) {
                help.removeAttribute('data-i18n');
                help.textContent = unresolved;
            }
            completeDialog.showModal();
            const timer = root.setTimeout(() => {
                if (completeNoticeTimer !== timer) return;
                completeNoticeTimer = null;
                if (at === epoch && !pageHidden && completeDialog.open) completeDialog.close();
            }, 5000);
            completeNoticeTimer = timer;
        }
        completeClose?.addEventListener('click', closeResourceNotice);
        completeDialog?.addEventListener('close', () => {
            if (!completeDialog.open) closeResourceNotice();
        });
        completeDialog?.addEventListener('keydown', event => {
            if (event.key === 'Tab' && !event.isComposing) {
                event.preventDefault();
                completeClose.focus();
            }
        });
        let epoch = 0;
        let accepted = null;
        let inputChangedDuringEnrollment = false;
        try { inputChangedDuringEnrollment = localStorage.getItem('neko_voice_enrollment_input_changed') === '1'; } catch (_) {}
        let resources = null;
        let resourceSequence = 0;
        let deviceSequence = 0;
        let operation = null;
        let pending = false;
        let trialActive = false;
        let isolationId = null;
        let isolationToken = null;
        let isolationServerOwned = false;
        let requestAbort = null;
        let pollTimer = null;
        let pollResolve = null;
        let selectedId = '';
        let gainDb = 0;
        let fallbackRequired = false;
        let actualLabel = '';
        let actualDeviceId = '';
        let fallbackShown = false;
        let currentMessage = null;
        let inputSetupStarted = false;
        let inputSetupRequested = false;
        let inputSetupDismissed = false;
        const SETUP_FEEDBACK_VISIBLE_MS = 3000;
        let setupFeedbackTimer = null;
        let compactSetupProof = null;
        let resourceFeedbackIssue = false;
        let resourceFeedback = null;
        let pageHidden = false;
        try { selectedId = localStorage.getItem('neko_selected_microphone') || ''; gainDb = root.nekoMicrophoneInput.gain(localStorage.getItem('neko_mic_gain_db')); } catch (_) {}
        el.gain.value = String(gainDb);
        const RESOURCE_HINT_FALLBACKS = {
            'voiceIdentity.resourcesChecking': 'Enrollment resources are not confirmed. Check and load them.',
            'voiceIdentity.resourcesReady': 'Voice enrollment resources are ready',
            'voiceIdentity.resourcesReadyWakeRepair': 'Voice enrollment resources are ready. Wake word components need repair: select “Repair resources”.',
            'voiceIdentity.resourcesNeeded': 'Enrollment resources are not ready. Check and load resources.',
            'voiceIdentity.resourcesNeedRepair': 'Required enrollment resources are missing or unavailable. Select “Repair resources”.'
        };
        // A missing or corrupt cache-managed wake model is fixed by downloading
        // a fresh bundle. A configured model directory takes precedence over
        // the cache, so download cannot fix that one.
        function wakeModelDownloadable() {
            const model = resources && resources.resources && resources.resources.wake_model;
            return !!model && !resources.wake_configured && (model.state === 'missing'
                || (model.state === 'unavailable' && model.reason === 'WAKE_WORD_MODEL_INVALID'));
        }
        // Wake components only become required once wake word is on.
        function needsRepair(name, value) {
            return !!value && value.required && ['missing', 'unavailable'].includes(value.state)
                && !(name === 'wake_model' && wakeModelDownloadable());
        }
        function wakeNeedsRepair() {
            const items = resources && resources.resources || {};
            return ['wake_model', 'wake_runtime'].some(name => needsRepair(name, items[name]));
        }
        // Loading cannot fix a missing or unavailable enrollment resource; repair can.
        // The details are folded away, so name a wake repair in the visible summary.
        function resourcesHintKey() {
            if (!resources) return 'voiceIdentity.resourcesChecking';
            if (resources.can_enroll === true) return wakeNeedsRepair() ? 'voiceIdentity.resourcesReadyWakeRepair' : 'voiceIdentity.resourcesReady';
            const items = resources.resources || {};
            return ['campp', 'silero', 'noise_reduction'].some(name => needsRepair(name, items[name]))
                ? 'voiceIdentity.resourcesNeedRepair' : 'voiceIdentity.resourcesNeeded';
        }
        function message(key, fallback, error) { currentMessage = { key, fallback, error }; el['test-result'].textContent = t(key, fallback); el['test-result'].classList.toggle('is-error', !!error); }
        function updateSetupFeedback(inputReady, inputSetupVisible, resourcesKey) {
            const canCompact = inputSetupStarted && inputReady && !inputSetupVisible && !pending && !operation
                && !resourceFeedbackIssue && resourcesKey === 'voiceIdentity.resourcesReady' && !pageHidden;
            if (!canCompact) {
                if (setupFeedbackTimer !== null) root.clearTimeout(setupFeedbackTimer);
                setupFeedbackTimer = null;
                compactSetupProof = null;
            } else if (setupFeedbackTimer === null && compactSetupProof !== accepted) {
                const proof = accepted;
                const at = epoch;
                const timer = root.setTimeout(() => {
                    if (setupFeedbackTimer !== timer) return;
                    setupFeedbackTimer = null;
                    if (at !== epoch || proof !== accepted || pageHidden) return;
                    compactSetupProof = proof;
                    render(false);
                }, SETUP_FEEDBACK_VISIBLE_MS);
                setupFeedbackTimer = timer;
            }
            const compact = canCompact && compactSetupProof === accepted;
            el['resource-card'].classList.toggle('is-compact', compact);
            const summary = document.getElementById('voice-identity-resource-summary');
            if (summary) summary.hidden = compact;
            el['resource-message'].hidden = compact;
            el['input-summary'].hidden = !inputReady || inputSetupVisible || compact;
        }
        function transferFeedback(detail) {
            if (!detail || typeof detail !== 'object') return null;
            const phases = {
                checking: ['voiceIdentity.resourcePhaseChecking', 'Checking {{resource}}'],
                downloading: ['voiceIdentity.resourcePhaseDownloading', 'Downloading {{resource}}'],
                verifying: ['voiceIdentity.resourcePhaseVerifying', 'Verifying {{resource}}'],
                loading: ['voiceIdentity.resourcePhaseLoading', 'Loading {{resource}}'],
                refreshing: ['voiceIdentity.resourcePhaseRefreshing', 'Refreshing voice resources']
            };
            if (!Object.prototype.hasOwnProperty.call(phases, detail.phase)) return null;
            const name = ['campp', 'silero', 'noise_reduction', 'wake_model', 'wake_runtime'].includes(detail.resource)
                ? t('voiceIdentity.resource_' + detail.resource, detail.resource) : '';
            const copy = phases[detail.phase];
            const phase = t(copy[0], copy[1].replace('{{resource}}', name), { resource: name });
            let progress = null, text = '';
            if (detail.phase === 'downloading' && Number.isSafeInteger(detail.downloaded_bytes) && detail.downloaded_bytes >= 0) {
                const received = detail.downloaded_bytes;
                const total = Number.isSafeInteger(detail.total_bytes) && detail.total_bytes > 0 && received <= detail.total_bytes ? detail.total_bytes : null;
                const number = new Intl.NumberFormat(document.documentElement.lang || undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
                const downloaded = number.format(received / 1000000);
                if (total !== null) {
                    progress = received / total;
                    const size = number.format(total / 1000000), percent = number.format(progress * 100);
                    text = t('voiceIdentity.resourceDownloadProgressKnown', downloaded + ' / ' + size + ' MB · ' + percent + '%', { downloaded, total: size, percent });
                } else text = t('voiceIdentity.resourceDownloadProgressUnknown', downloaded + ' MB downloaded', { downloaded });
                if (Number.isSafeInteger(detail.attempt) && detail.attempt > 1) text += ' · ' + t('voiceIdentity.resourceDownloadRetry', 'Retry ' + detail.attempt, { attempt: detail.attempt });
            }
            return { phase, progress, text };
        }
        function render(updateParent = true) {
            if (resourceOverviewHidden && !canCollapseResources()) resourceOverviewHidden = false;
            el['resource-overview'].hidden = resourceOverviewHidden;
            const resourceBusy = pending && resourceFeedback && resourceFeedback.epoch === epoch;
            const preparing = resourceBusy && resourceFeedback.kind === 'prepare';
            const repairing = resourceBusy && resourceFeedback.kind === 'repair';
            el['resource-card'].classList.toggle('is-loading-resources', !!resourceBusy);
            el['resource-progress-panel'].hidden = !resourceBusy;
            el['resource-progress'].hidden = !resourceBusy;
            const transfer = resourceBusy && resourceFeedback.phase === 'running' ? transferFeedback(resourceFeedback.detail) : null;
            const progress = resourceBusy && resourceFeedback.detail ? transfer && transfer.progress : resourceBusy && resourceFeedback.progress;
            if (resourceBusy && Number.isFinite(progress)) el['resource-progress'].value = progress;
            else el['resource-progress'].removeAttribute('value');
            el['resource-download-info'].hidden = !transfer || !transfer.text;
            if (transfer) el['resource-download-info'].textContent = transfer.text;
            if (resourceBusy) {
                const phase = resourceFeedback.phase;
                const phaseCopy = phase === 'confirming'
                    ? ['voiceIdentity.resourceProgressConfirming', 'Confirming the result', 3]
                    : phase === 'running'
                        ? repairing ? ['voiceIdentity.resourceProgressRepairing', 'Checking and repairing resources', 2]
                            : ['voiceIdentity.resourceOperation_running', 'Resource operation running', 2]
                        : ['voiceIdentity.resourceProgressSubmitting', 'Submitting the resource task', 1];
                const phaseText = transfer ? transfer.phase : t(phaseCopy[0], phaseCopy[1]);
                if (el['resource-phase'].textContent !== phaseText) el['resource-phase'].textContent = phaseText;
                el['resource-step'].textContent = t('voiceIdentity.resourceProgressStep', 'Step ' + phaseCopy[2] + ' / 3', { current: phaseCopy[2], total: 3 });
                el['resource-targets'].hidden = !repairing;
                if (repairing) {
                    const names = ['campp', 'silero'];
                    if (resources && resources.wake_enabled) names.push('wake_runtime', 'wake_model');
                    const labels = { campp: 'CAM++', silero: 'Silero VAD', wake_runtime: 'Sherpa ONNX', wake_model: 'Wake word model' };
                    const targets = names.map(name => t('voiceIdentity.resource_' + name, labels[name])).join(' / ');
                    el['resource-targets'].textContent = t('voiceIdentity.resourceRepairTargets', 'Resources: ' + targets, { resources: targets });
                }
            }
            const prepareKey = preparing ? 'voiceIdentity.resourcePreparing' : 'voiceIdentity.resourcePrepare';
            el.prepare.setAttribute('data-i18n', prepareKey);
            el.prepare.textContent = t(prepareKey, preparing ? 'Checking and loading…' : 'Check and load resources');
            el.prepare.classList.toggle('is-loading', !!preparing);
            el.repair.textContent = t(repairing ? 'voiceIdentity.resourceRepairing' : 'voiceIdentity.resourceRepairButton', repairing ? 'Repairing resources…' : 'Repair resources');
            el.repair.setAttribute('data-i18n', repairing ? 'voiceIdentity.resourceRepairing' : 'voiceIdentity.resourceRepairButton');
            el.repair.classList.toggle('is-loading', !!repairing);
            const inputReady = !!resources && resources.can_enroll === true && !!accepted && accepted.inputSnapshot === snapshot();
            const inputSetupVisible = inputSetupStarted && (trialActive || !inputSetupDismissed && (inputSetupRequested || (!inputReady && !resourceOverviewHidden)));
            el['input-setup'].hidden = !inputSetupVisible;
            el['input-settings'].hidden = !inputSetupStarted || inputSetupVisible;
            el['input-settings'].disabled = pending || !!operation || hooks.enrolling();
            el['input-settings'].setAttribute('aria-expanded', String(inputSetupVisible));
            el['input-close'].hidden = !inputSetupVisible || pending || !!operation || trialActive || hooks.enrolling();
            el.prepare.setAttribute('aria-expanded', String(inputSetupVisible));
            el.prepare.classList.toggle('needs-input-setup', !inputReady);
            el['input-summary'].hidden = !inputReady || inputSetupVisible;
            const inputSummary = inputReady ? t('voiceIdentity.inputPassed', 'Input test passed.') + ' ' + actualLabel : '';
            if (el['input-summary'].textContent !== inputSummary) el['input-summary'].textContent = inputSummary;
            el.microphone.disabled = el.gain.disabled = !trialActive && (pending || !!operation);
            el.test.disabled = pending || hooks.enrolling();
            el['test-cancel'].hidden = !trialActive;
            el.prepare.disabled = pending || !!operation || hooks.enrolling();
            el.download.disabled = pending || !!operation || hooks.enrolling() || !resources || !resources.resources || !resources.resources.wake_runtime || ['missing', 'unavailable'].includes(resources.resources.wake_runtime.state);
            el['wake-enable'].disabled = pending || hooks.enrolling() || !resources || resources.wake_managed === true;
            el['wake-enable'].checked = !!(resources && resources.wake_enabled);
            el.repair.hidden = false;
            el.repair.disabled = pending || !!operation || hooks.enrolling();
            el['resource-cancel'].hidden = !operation;
            el['gain-value'].textContent = gainDb + ' dB';
            const resourcesKey = resourcesHintKey();
            updateSetupFeedback(inputReady, inputSetupVisible, resourcesKey);
            const summary = document.getElementById('voice-identity-resource-summary');
            if (summary) {
                const text = resourceBusy
                    ? t(repairing ? 'voiceIdentity.resourceRepairing' : preparing ? 'voiceIdentity.resourcePreparing' : 'voiceIdentity.resourceOperation_running', 'Preparing resources…')
                    : inputSetupStarted && !inputReady && resourcesKey === 'voiceIdentity.resourcesReady'
                        ? t('voiceIdentity.resourcesReadyForInput', 'Resources are ready. Select your microphone and complete the input test below.')
                        : t(resourcesKey, RESOURCE_HINT_FALLBACKS[resourcesKey]);
                if (summary.textContent !== text) summary.textContent = text;
                summary.classList.toggle('is-ready', !resourceBusy && resourcesKey === 'voiceIdentity.resourcesReady');
                summary.classList.toggle('is-next', !resourceBusy && inputSetupStarted && !inputReady && resourcesKey === 'voiceIdentity.resourcesReady');
            }
            const wakeSummary = document.getElementById('voice-identity-wake-summary');
            if (wakeSummary) {
                const wakeState = name => resources && resources.resources && resources.resources[name] && resources.resources[name].state;
                const wakeReady = wakeState('wake_model') === 'ready' && wakeState('wake_runtime') === 'ready';
                const [key, fallback] = !resources || !resources.wake_enabled
                    ? ['voiceIdentity.wakeOptional', 'Wake word is off. This does not block voice enrollment.']
                    : wakeReady
                        ? ['voiceIdentity.wakeReady', 'Wake word components are ready.']
                        : wakeNeedsRepair()
                            ? ['voiceIdentity.wakeNeedsRepair', 'Wake word components are missing or unavailable. Select “Repair resources”.']
                            : wakeModelDownloadable()
                                ? ['voiceIdentity.wakeNeedsModel', 'Wake word is on. Download the wake-word model.']
                                : ['voiceIdentity.wakeNeedsResources', 'Wake word is on. Check and load its components.'];
                const text = !resources ? '' : t(key, fallback);
                if (wakeSummary.textContent !== text) wakeSummary.textContent = text;
            }
            const downloadHelp = document.getElementById('voice-identity-download-help');
            if (downloadHelp) {
                const runtime = resources && resources.resources && resources.resources.wake_runtime;
                downloadHelp.hidden = !runtime || !['missing', 'unavailable'].includes(runtime.state);
                // The repair button only appears once the runtime is required,
                // that is, once wake word is on; until then, point there first.
                // A managed preference cannot be switched on from this page.
                const text = downloadHelp.hidden ? '' : runtime.required || resources.wake_managed === true
                    ? t('voiceIdentity.downloadNeedsRuntime', 'Install or repair the wake word runtime before downloading the model.')
                    : t('voiceIdentity.downloadNeedsWakeOn', 'The wake-word runtime is unavailable. Turn on wake word, then repair the runtime and download the model.');
                if (downloadHelp.textContent !== text) downloadHelp.textContent = text;
            }
            if (updateParent) hooks.render();
        }
        function snapshot() {
            const track = root.nekoMicrophoneInput.liveTrack(hooks.stream());
            const settings = track && typeof track.getSettings === 'function' ? track.getSettings() : {};
            return JSON.stringify({ device: settings.deviceId || actualDeviceId || selectedId, gain: gainDb });
        }
        function invalidate(key) {
            closeResourceNotice();
            cancelResourceCollapse();
            inputSetupDismissed = false;
            epoch += 1;
            accepted = null;
            if (hooks.enrolling()) {
                inputChangedDuringEnrollment = true;
                try { localStorage.setItem('neko_voice_enrollment_input_changed', '1'); } catch (_) {}
            }
            if (requestAbort) requestAbort.abort();
            if (pollTimer !== null) root.clearTimeout(pollTimer);
            if (pollResolve) pollResolve();
            pollTimer = null; pollResolve = null;
            hooks.stop('capture_cancelled');
            if (hooks.enrolling()) hooks.cancel();
            pending = false;
            trialActive = false;
            if (key) message(key, '', true);
            render();
        }
        async function enumerate() {
            if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
            const at = epoch;
            const sequence = ++deviceSequence;
            const devices = await navigator.mediaDevices.enumerateDevices();
            if (at !== epoch || sequence !== deviceSequence) return;
            el.microphone.replaceChildren();
            const defaultOption = document.createElement('option');
            defaultOption.value = '';
            defaultOption.textContent = t('voiceIdentity.inputDefault', 'System default');
            el.microphone.appendChild(defaultOption);
            let index = 0;
            devices.filter(device => device.kind === 'audioinput').forEach(device => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = device.label || t('voiceIdentity.inputUnnamed', 'Microphone {{index}}', { index: ++index });
                el.microphone.appendChild(option);
            });
            el.microphone.value = selectedId;
        }
        function receivedStream(info) {
            const newSnapshot = JSON.stringify({ device: info.deviceId || selectedId, gain: gainDb });
            if (info.fallback || !accepted || accepted.inputSnapshot !== newSnapshot) accepted = null;
            actualDeviceId = info.deviceId || selectedId;
            actualLabel = info.label || t('voiceIdentity.inputUnnamed', 'Microphone', { index: 1 });
            if (typeof el['actual-device'].removeAttribute === 'function') el['actual-device'].removeAttribute('data-i18n');
            el['actual-device'].textContent = actualLabel;
            if (info.fallback) {
                fallbackShown = true;
                fallbackRequired = true;
                if (hooks.enrolling()) {
                    inputChangedDuringEnrollment = true;
                    try { localStorage.setItem('neko_voice_enrollment_input_changed', '1'); } catch (_) {}
                }
                selectedId = info.deviceId || '';
                try { localStorage.setItem('neko_selected_microphone', selectedId); } catch (_) {}
                el['input-notice'].textContent = t('voiceIdentity.inputFallback', 'Selected microphone unavailable. Using the displayed device; repeat the input test.');
            }
            enumerate().catch(() => {});
        }
        function resourceText(name, value) {
            const label = t('voiceIdentity.resource_' + name, name);
            const status = value && value.state || 'unavailable';
            const stateText = t('voiceIdentity.resourceState_' + status, status);
            const reason = value && value.reason;
            return label + ': ' + stateText + (reason ? ' — ' + t('voiceIdentity.resourceReason_' + reason, t('voiceIdentity.resourceRepair', 'Check or repair this resource.')) : '');
        }
        function repairDetails() {
            const unresolved = Object.entries(resources && resources.resources || {})
                .filter(([name, value]) => needsRepair(name, value));
            const details = unresolved.map(([name, value]) => resourceText(name, value));
            if (unresolved.some(([name, value]) => ['campp', 'silero', 'noise_reduction'].includes(name) && value.repair_action === 'app_repair')) {
                details.push(t('voiceIdentity.resourceAppRepairHelp', 'Check for updates in desktop Settings → About, or reinstall the application running the backend. For remote deployments, contact the service administrator.'));
            }
            return details.join('\n');
        }
        async function refreshResources() {
            const at = epoch;
            const sequence = ++resourceSequence;
            const payload = await hooks.request('/resources', { method: 'GET' }).catch(error => {
                if (at === epoch && sequence === resourceSequence) {
                    resourceFeedbackIssue = true;
                    el['resource-message'].textContent = hooks.error(error);
                    render(false);
                }
                throw error;
            });
            if (at !== epoch || sequence !== resourceSequence) return;
            const previousContract = resources && resources.audio_contract;
            resources = payload;
            if (previousContract && payload.audio_contract && JSON.stringify(previousContract) !== JSON.stringify(payload.audio_contract)) {
                // The current resource action already discarded its input proof.
                // Its own refreshed contract must not cancel its completion feedback.
                const ownedResourceAction = pending && resourceFeedback && resourceFeedback.epoch === epoch && !hooks.enrolling();
                if (!ownedResourceAction) invalidate('voiceIdentity.inputChanged');
            }
            el.resources.replaceChildren();
            Object.entries(payload.resources || {}).forEach(([name, value]) => {
                const item = document.createElement('li');
                item.textContent = resourceText(name, value);
                el.resources.appendChild(item);
            });
            render();
        }
        async function inactiveTicket(id, at) {
            const ticket = await hooks.request('/audio/check/isolation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: id }) });
            if (at !== epoch) { hooks.request('/audio/check/isolation/release', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: ticket.token }) }).catch(() => {}); throw new Error('capture_cancelled'); }
            isolationId = id; isolationToken = ticket.token; isolationServerOwned = true;
        }
        async function prepareIsolation(at) {
            const id = root.nekoMicrophoneInput.operationId();
            if (root.nekoVoiceEnrollment && typeof root.nekoVoiceEnrollment.prepare === 'function') {
                try {
                    const ack = await root.nekoVoiceEnrollment.prepare({ operationId: id });
                    if (at !== epoch || !ack || ack.operationId !== id || ack.stopped !== true || !ack.token) throw new Error('capture_cancelled');
                    isolationId = id;
                    isolationToken = ack.token;
                    isolationServerOwned = false;
                } catch (error) {
                    try { await root.nekoVoiceEnrollment.release({ operationId: id }); } catch (_) {}
                    if (at !== epoch) throw error;
                    // IPC failure is not proof of inactivity: the server must
                    // check every producer before granting this fallback.
                    await inactiveTicket(id, at);
                }
                return;
            }
            if (!root.opener || root.opener.closed) {
                await inactiveTicket(id, at); return;
            }
            try { await new Promise((resolve, reject) => {
                let timedOut = false;
                const retire = root.setTimeout(() => root.removeEventListener('message', receive), 75000);
                const timer = root.setTimeout(() => { timedOut = true; reject(new Error('capture_owner_unavailable')); }, 15000);
                function receive(event) {
                    if (event.origin !== root.location.origin || event.source !== root.opener || !event.data || event.data.type !== 'neko-voice-enrollment-stopped' || event.data.operationId !== id) return;
                    root.clearTimeout(timer); root.clearTimeout(retire); root.removeEventListener('message', receive);
                    if (timedOut || at !== epoch) {
                        if (event.data.token) {
                            root.opener.postMessage({ type: 'neko-voice-enrollment-release', operationId: id, token: event.data.token }, root.location.origin);
                            hooks.request('/audio/check/isolation/release', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: event.data.token }) }).catch(() => {});
                        }
                        reject(new Error('capture_cancelled'));
                        return;
                    }
                    if (event.data.stopped === true && at === epoch && event.data.token) { isolationToken = event.data.token; resolve(); } else reject(new Error('capture_cancelled'));
                }
                root.addEventListener('message', receive);
                root.opener.postMessage({ type: 'neko-voice-enrollment-prepare', operationId: id }, root.location.origin);
            }); } catch (error) {
                if (at !== epoch) throw error;
                // Silence from an opener is never proof of inactivity. Only the
                // server can issue this fallback after checking every producer.
                await inactiveTicket(id, at);
            }
            isolationId = id;
        }
        async function releaseIsolation(owned) {
            if (!owned || !owned.token) return;
            if (isolationId === owned.id && isolationToken === owned.token) { isolationId = null; isolationToken = null; }
            if (root.nekoVoiceEnrollment && !owned.serverOwned) await root.nekoVoiceEnrollment.release({ operationId: owned.id });
            else {
                if (root.opener && !root.opener.closed) root.opener.postMessage({ type: 'neko-voice-enrollment-release', operationId: owned.id, token: owned.token }, root.location.origin);
                await hooks.request('/audio/check/isolation/release', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: owned.token }) });
            }
        }
        async function testInput() {
            if (pending || hooks.enrolling()) return;
            const returnFocus = typeof el['input-setup'].contains === 'function' && el['input-setup'].contains(document.activeElement);
            const at = ++epoch;
            accepted = null;
            pending = true;
            trialActive = true;
            requestAbort = new AbortController();
            render();
            let pcm;
            let ownedIsolation = null;
            try {
                await prepareIsolation(at);
                if (at !== epoch) return;
                ownedIsolation = { id: isolationId, token: isolationToken, serverOwned: isolationServerOwned };
                await hooks.microphone();
                if (at !== epoch) return;
                if (fallbackRequired) { fallbackRequired = false; message('voiceIdentity.inputFallback', 'Selected microphone unavailable. Repeat the input test using the displayed device.', true); return; }
                const inputSnapshot = snapshot();
                message('voiceIdentity.inputTesting', 'Recording for three seconds. Say “hello” a few times or speak a short sentence.', false);
                pcm = await hooks.capture(3000);
                if (at !== epoch || inputSnapshot !== snapshot()) return;
                const payload = await hooks.request('/audio/check', {
                    method: 'POST', body: pcm,
                    // Body read (15s) plus audio worker (30s), with IPC margin.
                    timeoutMs: 50000,
                    signal: requestAbort.signal,
                    headers: { 'Content-Type': 'audio/pcm;format=pcm_s16le;rate=48000;channels=1', 'X-Voice-Audio-Contract': 'owner-campplus-desktop-v1', 'X-Voice-Input-Check': isolationToken || '' }
                });
                if (at !== epoch || inputSnapshot !== snapshot()) return;
                if (payload.accepted === true) {
                    accepted = { inputSnapshot, audioContract: payload.audio_contract };
                    inputChangedDuringEnrollment = false;
                    try { localStorage.removeItem('neko_voice_enrollment_input_changed'); } catch (_) {}
                    message('voiceIdentity.inputPassed', 'Input test passed.', false);
                } else {
                    const reason = payload.reason;
                    message('voiceIdentity.inputReason_' + reason, hooks.error(new Error(reason || 'no_speech_detected')), true);
                }
            } catch (error) {
                if (at === epoch) message('voiceIdentity.inputReason_' + (error.name === 'NotAllowedError' ? 'permission_denied' : error.message), hooks.error(error), true);
            } finally {
                if (pcm) new Uint8Array(pcm).fill(0);
                if (at === epoch) hooks.stop('capture_cancelled');
                try { await releaseIsolation(ownedIsolation); } catch (error) { if (at === epoch) { accepted = null; message('voiceIdentity.inputReason_capture_owner_unavailable', hooks.error(error), true); } }
                if (at === epoch) {
                    pending = false; trialActive = false; hooks.pause(); requestAbort = null;
                    if (accepted) inputSetupRequested = false;
                    render();
                    if (accepted && returnFocus && el['input-setup'].hidden
                        && (document.activeElement === document.body || el['input-setup'].contains(document.activeElement))) el['input-settings'].focus();
                }
            }
        }
        function operationReason(reason) {
            if (reason === 'runtime_degraded') return t('voiceIdentity.reasonRuntimeDegraded', 'Voice activation is temporarily unavailable; standby audio will not be uploaded.');
            return t('voiceIdentity.resourceReason_' + reason, t('voiceIdentity.resourceRepair', 'Check or repair this resource.'));
        }
        // Resource changes may alter audio processing, so a passed test no
        // longer counts. Replace its "passed" result, which would now mislead.
        function discardTestForResources() {
            if (accepted) message('voiceIdentity.inputRetestAfterResources', 'Resource changes invalidate the passed input test. Repeat the test.', false);
            accepted = null;
        }
        async function runResource(kind) {
            if (pending || operation || hooks.enrolling()) return;
            closeResourceNotice();
            cancelResourceCollapse();
            resourceFeedbackIssue = false;
            if (resourceOverviewHidden && resourceDetails) resourceDetails.open = true;
            resourceOverviewHidden = false;
            if (kind === 'prepare' || kind === 'repair') { inputSetupStarted = true; inputSetupRequested = true; inputSetupDismissed = false; }
            const at = ++epoch;
            discardTestForResources(); pending = true;
            resourceFeedback = { epoch: at, kind, phase: 'submitting', progress: null };
            el['resource-message'].textContent = kind === 'repair'
                ? t('voiceIdentity.resourceRepairingHelp', 'Downloading and verifying missing voice models, then checking whether they can load. Verified downloads are kept if cancelled.')
                : t('voiceIdentity.resourcePreparingHelp', 'Preparing voice resources. Test your microphone below when loading finishes. The first load may take a little time.');
            requestAbort = new AbortController(); render();
            if (kind === 'prepare') document.getElementById('voice-input-title')?.focus?.();
            let ownedOperation = null;
            let completionResult = null;
            try {
                // Reserving an ID cannot load or download anything. Once start
                // is sent we already own its ID, even if the receipt is lost.
                const payload = await hooks.request('/resources/operations', { method: 'POST', signal: requestAbort.signal,
                    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }) });
                if (typeof payload.operation_id !== 'string' || !payload.operation_id || payload.operation_id.length > 128) throw new Error('invalid_resource_operation');
                ownedOperation = payload.operation_id;
                if (at !== epoch) { hooks.request('/resources/operations/' + encodeURIComponent(ownedOperation) + '/cancel', { method: 'POST', keepalive: true }).catch(() => {}); return; }
                operation = payload.operation_id;
                render();
                await hooks.request('/resources/operations/' + encodeURIComponent(operation) + '/start', { method: 'POST', signal: requestAbort.signal });
                if (at !== epoch) { hooks.request('/resources/operations/' + encodeURIComponent(ownedOperation) + '/cancel', { method: 'POST', keepalive: true }).catch(() => {}); return; }
                resourceFeedback.phase = 'running';
                render(false);
                // Repair may use runtime check/stage/publish (30+240+15s),
                // model download/publish (180+5s), and primary repair (420s).
                // Allow their 890s total plus 70s for retirement and refresh.
                const deadline = Date.now() + (kind === 'repair' ? 960000 : kind === 'download' ? 210000 : 60000);
                while (at === epoch && operation) {
                    const id = operation;
                    const result = await hooks.request('/resources/operations/' + encodeURIComponent(id), { method: 'GET', signal: requestAbort.signal });
                    if (at !== epoch || operation !== id) return;
                    resourceFeedbackIssue = ['failed', 'cancelled'].includes(result.state) || !!result.reason;
                    el['resource-message'].textContent = t('voiceIdentity.resourceOperation_' + result.state, result.state || '') + (!result.progress_detail && Number.isFinite(result.progress) ? ' ' + Math.round(result.progress * 100) + '%' : '');
                    if (result.reason) el['resource-message'].textContent += ' — ' + operationReason(result.reason);
                    resourceFeedback.progress = Number.isFinite(result.progress) ? Math.max(0, Math.min(1, result.progress)) : null;
                    resourceFeedback.detail = result.progress_detail || null;
                    render(false);
                    if (['succeeded', 'failed', 'cancelled'].includes(result.state)) {
                        completionResult = result;
                        operation = null;
                        break;
                    }
                    if (Date.now() >= deadline) throw new Error('resource_operation_timeout');
                    await new Promise(resolve => { pollResolve = resolve; pollTimer = root.setTimeout(resolve, 1000); });
                    pollTimer = null; pollResolve = null;
                }
                if (at === epoch) {
                    resourceFeedback.phase = 'confirming';
                    render(false);
                    await refreshResources();
                    if (at === epoch) await hooks.status();
                }
            } catch (error) {
                if (ownedOperation) {
                    try {
                        const result = await hooks.request('/resources/operations/' + encodeURIComponent(ownedOperation) + '/cancel', { method: 'POST', keepalive: true });
                        if (operation === ownedOperation && ['succeeded', 'failed', 'cancelled'].includes(result.state)) operation = null;
                        if (at === epoch && result.committed === true) {
                            completionResult = result;
                            resourceFeedbackIssue = result.state !== 'succeeded' || !!result.reason;
                            el['resource-message'].textContent = t('voiceIdentity.resourceOperation_' + result.state, result.state) + (result.reason ? ' — ' + operationReason(result.reason) : '');
                            await refreshResources();
                            if (at === epoch) await hooks.status();
                            return;
                        }
                    } catch (cancelError) {
                        // This server never recreates an unknown reservation.
                        // Retirement is not reported as successful cancellation.
                        if (operation === ownedOperation && cancelError.status === 400 && cancelError.message === 'invalid_resource_operation') operation = null;
                    }
                }
                if (at === epoch) {
                    resourceFeedbackIssue = true;
                    el['resource-message'].textContent = error.message === 'resource_repair_unavailable'
                        ? [operationReason(error.message), repairDetails()].filter(Boolean).join('\n')
                        : hooks.error(error);
                }
            } finally {
                if (at === epoch) {
                    if (kind === 'repair' && completionResult && completionResult.state === 'succeeded' && !completionResult.reason && !resourceFeedbackIssue) {
                        const unresolved = repairDetails();
                        if (unresolved) { resourceFeedbackIssue = true; el['resource-message'].textContent = unresolved; }
                    }
                    pending = false; resourceFeedback = null; requestAbort = null; render();
                    if ((kind === 'prepare' || kind === 'repair') && completionResult && completionResult.state !== 'cancelled') {
                        const needsAttention = completionResult.state !== 'succeeded' || resourceFeedbackIssue || resourcesHintKey() !== 'voiceIdentity.resourcesReady';
                        showResourceNotice(at, needsAttention);
                        if (kind === 'repair' && !needsAttention) scheduleResourceCollapse(at);
                    }
                }
            }
        }
        async function cancelOperation() {
            const id = operation;
            invalidate('voiceIdentity.inputTestRequired');
            if (!id) return;
            const at = epoch;
            pending = true; render();
            try {
                const result = await hooks.request('/resources/operations/' + encodeURIComponent(id) + '/cancel', { method: 'POST', keepalive: true });
                if (operation === id && ['succeeded', 'failed', 'cancelled'].includes(result.state)) operation = null;
                if (at !== epoch) return;
                resourceFeedbackIssue = result.state !== 'succeeded' || !!result.reason;
                el['resource-message'].textContent = t('voiceIdentity.resourceOperation_' + result.state, result.state || '');
                if (result.reason) el['resource-message'].textContent += ' — ' + operationReason(result.reason);
                await refreshResources();
                if (at === epoch) await hooks.status();
            } catch (error) {
                if (operation === id && error.status === 400 && error.message === 'invalid_resource_operation') operation = null;
                if (at === epoch) { resourceFeedbackIssue = true; el['resource-message'].textContent = hooks.error(error); }
            } finally { if (at === epoch) { pending = false; render(); } }
        }
        el.test.addEventListener('click', testInput);
        el['input-settings'].addEventListener('click', () => {
            if (pending || operation || hooks.enrolling()) return;
            cancelResourceCollapse();
            inputSetupDismissed = false;
            inputSetupRequested = true;
            render();
            el.microphone.focus();
        });
        el['input-close'].addEventListener('click', () => {
            if (pending || operation || trialActive || hooks.enrolling()) return;
            inputSetupRequested = false;
            inputSetupDismissed = true;
            render();
            el['input-settings'].focus();
        });
        el['test-cancel'].addEventListener('click', () => invalidate('voiceIdentity.inputTestRequired'));
        el.prepare.addEventListener('click', () => runResource('prepare'));
        el.download.addEventListener('click', () => runResource('download'));
        // Let the backend validate its repair capability. A discovery snapshot
        // cannot rule out repair of an existing but unusable model. Keep an
        // unsupported installation's reason here instead of opening raw docs.
        el.repair.addEventListener('click', () => runResource('repair'));
        el['resource-cancel'].addEventListener('click', () => cancelOperation().catch(() => {}));
        el['wake-enable'].addEventListener('change', async () => {
            if (pending || hooks.enrolling()) return;
            const desired = el['wake-enable'].checked;
            const at = ++epoch;
            discardTestForResources();
            requestAbort = new AbortController();
            pending = true; render();
            try { await hooks.request('/resources/wake-word/preference', { method: 'POST', signal: requestAbort.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: desired }) }); if (at === epoch) await refreshResources(); }
            catch (error) { if (at === epoch) { resourceFeedbackIssue = true; el['resource-message'].textContent = hooks.error(error); } }
            finally { if (at === epoch) { pending = false; requestAbort = null; render(); } }
        });
        el.microphone.addEventListener('change', () => { selectedId = el.microphone.value; try { localStorage.setItem('neko_selected_microphone', selectedId); } catch (_) {} invalidate('voiceIdentity.inputChanged'); });
        el.gain.addEventListener('change', () => { gainDb = root.nekoMicrophoneInput.gain(el.gain.value); try { localStorage.setItem('neko_mic_gain_db', String(gainDb)); } catch (_) {} invalidate('voiceIdentity.inputChanged'); });
        root.addEventListener('storage', event => {
            if (!['neko_selected_microphone', 'neko_mic_gain_db', 'neko_noise_reduction'].includes(event.key)) return;
            try { selectedId = localStorage.getItem('neko_selected_microphone') || ''; gainDb = root.nekoMicrophoneInput.gain(localStorage.getItem('neko_mic_gain_db')); } catch (_) {}
            el.gain.value = String(gainDb);
            if (hooks.enrolling()) {
                enumerate().catch(() => {});
                inputChangedDuringEnrollment = true;
                try { localStorage.setItem('neko_voice_enrollment_input_changed', '1'); } catch (_) {}
                accepted = null;
                message('voiceIdentity.inputChanged', 'Input settings changed. Cancel enrollment and repeat the input test.');
                render();
                return;
            }
            invalidate('voiceIdentity.inputChanged');
            enumerate().catch(() => {});
        });
        root.addEventListener('localechange', () => {
            if (actualLabel) el['actual-device'].textContent = actualLabel;
            if (fallbackShown) el['input-notice'].textContent = t('voiceIdentity.inputFallback', 'Selected microphone unavailable. Using the displayed device; repeat the input test.');
            if (currentMessage) message(currentMessage.key, currentMessage.fallback, currentMessage.error);
            if (resources) {
                el.resources.replaceChildren();
                Object.entries(resources.resources || {}).forEach(([name, value]) => { const item = document.createElement('li'); item.textContent = resourceText(name, value); el.resources.appendChild(item); });
            }
            enumerate().catch(() => {}); render();
        });
        if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) navigator.mediaDevices.addEventListener('devicechange', async () => {
            // Trial tracks have already been released. A default-device change
            // still invalidates that saved input proof, even without a live track.
            if (accepted && !hooks.enrolling()) invalidate('voiceIdentity.inputChanged');
            const stream = hooks.stream();
            const at = epoch;
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                if (at !== epoch || stream !== hooks.stream()) return;
                const track = root.nekoMicrophoneInput.liveTrack(stream);
                const actualId = track && typeof track.getSettings === 'function' && track.getSettings().deviceId;
                const ids = devices.filter(device => device.kind === 'audioinput').map(device => device.deviceId);
                if (stream && (!track || (actualId && !ids.includes(actualId)) || (selectedId && !ids.includes(selectedId)))) invalidate('voiceIdentity.inputReason_microphone_unavailable');
                await enumerate();
            } catch (_) { if (at === epoch && stream) invalidate('voiceIdentity.inputReason_microphone_unavailable'); }
        });
        root.addEventListener('pagehide', () => {
            pageHidden = true;
            if (setupFeedbackTimer !== null) root.clearTimeout(setupFeedbackTimer);
            setupFeedbackTimer = null;
            cancelOperation().catch(() => {});
            if (isolationId && root.nekoVoiceEnrollment) root.nekoVoiceEnrollment.release({ operationId: isolationId }).catch(() => {});
            if (isolationToken) {
                if (root.opener && !root.opener.closed) root.opener.postMessage({ type: 'neko-voice-enrollment-release', operationId: isolationId, token: isolationToken }, root.location.origin);
                hooks.request('/audio/check/isolation/release', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: isolationToken }) }).catch(() => {});
            }
            isolationId = null; isolationToken = null;
            if (pollTimer !== null) root.clearTimeout(pollTimer);
        });
        root.addEventListener('pageshow', () => { pageHidden = false; render(false); });
        enumerate().catch(() => {});
        // The single enrollment gate; canStart and the button hint both read it.
        function startHintKey() {
            if (pending) return 'voiceIdentity.setupBusy';
            if (!resources || resources.can_enroll !== true) return resourcesHintKey();
            if (!accepted || accepted.inputSnapshot !== snapshot()) return 'voiceIdentity.inputTestRequired';
            return 'voiceIdentity.enrollmentReady';
        }
        return {
            refreshResources, receivedStream, controls: () => render(false), isPending: () => pending,
            startHintKey,
            canStart: () => startHintKey() === 'voiceIdentity.enrollmentReady',
            canResume: () => !inputChangedDuringEnrollment,
            requireTest: () => message('voiceIdentity.inputTestRequired', 'Complete the input test before enrollment.', true),
            contractChanged: () => { accepted = null; message('voiceIdentity.inputChanged', 'Input settings changed. Repeat the input test.', true); render(); },
            updateMeter: rms => { el.meter.value = Math.min(1, rms * 8); },
            deviceLost: () => invalidate('voiceIdentity.inputReason_microphone_unavailable'),
            audioContract: () => accepted && accepted.audioContract
        };
    };
})(window);

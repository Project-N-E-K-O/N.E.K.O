/** Independent personal-library page; all durable state belongs to its API. */
(function () {
    'use strict';

    var BASE = '/api/knowledge-library';
    var TERMINAL = ['completed', 'failed', 'cancelled', 'interrupted'];
    var MAX_DOCUMENT_BYTES = 16 * 1024 * 1024;
    var PUBLISH_STORAGE_KEY = 'neko.knowledge-library.publications.v1';

    /** Accept a work ID or a detail link from the configured Market only. */
    function parseShareId(value, marketUrl) {
        var text = String(value || '').trim();
        if (/^[1-9]\d*$/.test(text)) { var id = Number(text); if (Number.isSafeInteger(id)) return id; }
        try {
            var url = new URL(text); var expected = new URL(marketUrl);
            if (url.origin !== expected.origin || !/^https?:$/.test(url.protocol) || url.username || url.password) throw new Error();
            var path = url.hash ? url.hash.slice(1).split('?')[0] : url.pathname;
            var match = /^\/shares\/([1-9]\d*)$/.exec(path);
            if (match && Number.isSafeInteger(Number(match[1]))) return Number(match[1]);
        } catch (_) { /* All invalid links become one public error, without fetching their URL. */ }
        throw new Error('invalid_share_link');
    }

    /** Keep transport bounded and refresh only an explicitly rejected CSRF token. */
    function createApi(host) {
        async function request(path, options) {
            var opts = options || {};
            var method = opts.method || 'GET';
            var helper = host.nekoLocalMutationSecurity;
            async function send() {
                var headers = { Accept: 'application/json' };
                if (method !== 'GET') {
                    if (!helper || typeof helper.getMutationHeaders !== 'function') throw new Error('security_unavailable');
                    Object.assign(headers, await helper.getMutationHeaders());
                }
                var body = opts.file || undefined;
                if (opts.body !== undefined) {
                    headers['Content-Type'] = 'application/json';
                    body = JSON.stringify(opts.body);
                }
                var controller = new AbortController();
                var timer = host.setTimeout(function () { controller.abort(); }, 45000);
                try {
                    // Browsers supply Origin; same-origin mode also prevents endpoint redirects to another host.
                    var response = await host.fetch(BASE + path, { method: method, headers: headers, body: body, mode: 'same-origin', credentials: 'same-origin', signal: controller.signal });
                    var payload = await response.json();
                    return { status: response.status, ok: response.ok, payload: payload };
                } catch (error) {
                    if (error.name === 'AbortError') throw new Error('request_timeout');
                    throw error;
                } finally { host.clearTimeout(timer); }
            }
            var result = await send();
            if (result.status === 403 && result.payload.reason === 'csrf_validation_failed' && helper && helper.refreshToken) {
                await helper.refreshToken();
                result = await send();
            }
            if (!result.ok || result.payload.ok === false) throw new Error(result.payload.reason || 'operation_failed');
            return result.payload;
        }
        return { request: request };
    }

    /** Render untrusted filenames and extracted source text as text nodes only. */
    function createPage(host, doc, api) {
        var state = { libraries: [], library: null, documents: [], jobs: [], packages: [], market: null, selected: new Set(), libraryId: '', epoch: 0, tickets: {}, busy: false, disposed: false, dialog: null, dialogEpoch: 0, reconcileNeeded: false, replacement: '', uploadLibraryId: '', timer: null, feedback: null };
        function $(id) { return doc.getElementById(id); }
        function t(key, options) { return host.t('knowledgeLibrary.' + key, options); }
        function node(tag, text, className) {
            var element = doc.createElement(tag);
            if (text !== undefined) element.textContent = String(text);
            if (className) element.className = className;
            return element;
        }
        function empty(target, key) { target.replaceChildren(node('p', t(key), 'library-note')); }
        function button(key, action, id) {
            var element = node('button', t(key));
            element.type = 'button'; element.dataset.action = action;
            if (id) element.dataset.id = id;
            return element;
        }
        function feedback(key, error, task) {
            // A later operation error or warning takes ownership from an old
            // task notice, so polling cannot clear or overwrite that message.
            state.feedback = key ? { key: key, error: !!error, task: task || null } : null;
            var element = $('library-feedback');
            element.hidden = !key; element.textContent = key ? t(key) : '';
            element.dataset.tone = error ? 'error' : 'info';
            return state.feedback;
        }
        function taskFeedback(job, epoch) {
            if (state.disposed || epoch !== state.epoch || !job || !job.job_id) return;
            feedback('queued', false, { jobId: job.job_id, libraryId: state.libraryId });
            updateTaskFeedback(job);
        }
        function backgroundFailure(error, message) {
            // Polling and deferred reconciliation cannot replace a later
            // operation message, or an already visible error/sync warning.
            if (state.disposed || state.feedback !== message || (message && (message.error || message.key === 'storageSyncUnconfirmed'))) return;
            feedback(errorKey(error), true);
        }
        function updateTaskFeedback(job) {
            var task = state.feedback && state.feedback.task;
            if (!task) return;
            if (task.libraryId !== state.libraryId) return feedback('');
            job = job || state.jobs.find(function (item) { return item.job_id === task.jobId; });
            if (!job) return feedback('');
            if (active(job)) return feedback(job.state === 'running' ? 'running' : 'queued', false, task);
            if (hasStorageWarning(job.result)) return feedback('storageSyncUnconfirmed');
            if (job.state === 'completed') return feedback('');
            if (job.state === 'cancelled') return feedback('cancelled');
            feedback(errorKey(new Error(job.reason || (job.state === 'interrupted' ? 'process_interrupted' : 'operation_failed'))), true);
        }
        function errorKey(error) {
            var map = { request_timeout: 'timeout', unsupported_document: 'unsupported', document_too_large: 'tooLarge', file_too_large: 'tooLarge', no_readable_text: 'noText', document_truncated: 'incomplete', truncated_document: 'incomplete', preview_expired: 'previewExpired', preview_not_found: 'previewExpired', version_conflict: 'conflict', preview_changed: 'conflict', sha256_mismatch: 'conflict', semantic_unavailable: 'semanticUnavailable', embedding_unavailable: 'semanticUnavailable', incompatible_model: 'incompatible', package_incompatible: 'incompatible', not_found: 'notFound', library_not_found: 'notFound', library_read_only: 'readOnly', installed_library_read_only: 'readOnly', process_interrupted: 'interruptedHint' };
            Object.assign(map, { document_too_many_pages: 'documentLimit', document_text_too_large: 'documentLimit', scan_unsupported: 'noText', invalid_text_encoding: 'invalidEncoding', invalid_pdf: 'invalidDocument', invalid_ooxml: 'invalidDocument', package_unsupported_embedding_spec: 'incompatible', package_embedding_identity_mismatch: 'incompatible' });
            Object.assign(map, { invalid_share_link: 'invalidShareLink', market_not_published: 'notPublished', market_not_knowledge_package: 'notPackage', market_revision_changed: 'marketChanged', market_auth_required: 'loginRequired', market_unavailable: 'marketUnavailable', publication_storage_unavailable: 'publicationStorageUnavailable', read_share_first: 'readShareFirst', popup_blocked: 'popupBlocked', share_revision_conflict: 'marketChanged' });
            // Retained submissions belong to their original account, even after an OAuth account switch.
            map.market_publisher_changed = 'marketAccountChanged';
            // An existing request UUID must never be silently reused for different public metadata.
            map.market_request_conflict = 'marketRequestConflict';
            // Released staging cannot be retried, and existing installations require an explicit update target.
            map.preview_input_discarded = 'previewInputDiscarded';
            map.package_update_requires_target = 'updateRequiresTarget';
            map.metadata_too_large = 'metadataTooLarge';
            map.input_token_limit = 'inputTokenLimit';
            map.embedding_incompatible = 'incompatible';
            // Admission pressure is temporary; it does not imply model failure.
            map.embedding_busy = map.jobs_busy = 'jobsBusy';
            return map[error.message] || 'failed';
        }
        function ticket(channel) {
            var identity = { epoch: state.epoch, sequence: (state.tickets[channel] || 0) + 1 };
            state.tickets[channel] = identity.sequence;
            return function () { return !state.disposed && (channel === 'jobs' || channel === 'market' || identity.epoch === state.epoch) && identity.sequence === state.tickets[channel]; };
        }
        function active(job) { return TERMINAL.indexOf(job.state) < 0; }
        function hasStorageWarning(result) {
            return result && Array.isArray(result.warnings) && result.warnings.indexOf('storage_sync_unconfirmed') >= 0;
        }
        function local() { return state.library && state.library.kind === 'local'; }
        function updateControls() {
            doc.querySelectorAll('[data-action]').forEach(function (element) {
                var action = element.dataset.action;
                if (action === 'select' || action === 'refresh') return;
                element.disabled = state.busy || (action === 'cancel-job' && state.jobs.some(function (job) { return job.job_id === element.dataset.id && job.cancel_requested; })) || (['rename', 'upload', 'build', 'replace', 'delete-document'].indexOf(action) >= 0 && !local());
            });
            $('library-dialog-submit').disabled = state.busy;
        }
        async function mutate(operation) {
            if (state.busy) return;
            state.busy = true; updateControls();
            try { await operation(); }
            catch (error) {
                if (!state.disposed) {
                    if (state.dialog) { $('library-dialog-error').hidden = false; $('library-dialog-error').textContent = t(errorKey(error)); }
                    else feedback(errorKey(error), true);
                }
            } finally { state.busy = false; if (!state.disposed) updateControls(); }
        }
        function renderLibraries() {
            var target = $('library-list'); target.replaceChildren();
            if (!state.libraries.length) return empty(target, 'emptyLibraries');
            state.libraries.forEach(function (library) {
                var element = button('select', 'select', library.library_id);
                element.textContent = library.name;
                element.setAttribute('aria-current', library.library_id === state.libraryId ? 'true' : 'false');
                target.appendChild(element);
            });
        }
        function documentRow(document) {
            var row = node('article', undefined, 'library-row');
            var heading = node('div', undefined, 'section-heading');
            var label = node('label', undefined, 'library-row-title');
            if (local()) {
                var check = node('input'); check.type = 'checkbox'; check.value = document.document_id;
                check.checked = state.selected.has(document.document_id);
                check.addEventListener('change', function () { if (check.checked) state.selected.add(document.document_id); else state.selected.delete(document.document_id); });
                label.appendChild(check);
            }
            label.appendChild(node('span', document.filename)); heading.appendChild(label);
            var actions = node('div', undefined, 'library-actions');
            actions.appendChild(button('view', 'view', document.document_id));
            if (local()) { actions.appendChild(button('replace', 'replace', document.document_id)); actions.appendChild(button('delete', 'delete-document', document.document_id)); }
            heading.appendChild(actions); row.appendChild(heading);
            row.appendChild(node('p', t('documentMeta', { chunks: document.chunks_count, state: semanticLabel(document.semantic_state) }), 'library-row-meta'));
            return row;
        }
        function semanticLabel(value) { return t(value === 'ready' ? 'semanticReady' : value === 'partial' ? 'semanticPartial' : 'semanticUnavailable'); }
        function renderDetail() {
            $('library-empty').hidden = !!state.library; $('library-detail').hidden = !state.library;
            if (!state.library) return;
            $('library-name').textContent = state.library.name;
            $('library-kind').textContent = t(local() ? 'local' : 'installed');
            var target = $('library-documents'); target.replaceChildren();
            if (!state.documents.length) empty(target, 'noDocuments');
            state.documents.forEach(function (document) { target.appendChild(documentRow(document)); });
            updateControls();
        }
        async function selectLibrary(id) {
            if (id !== state.libraryId && state.feedback && state.feedback.task) feedback('');
            state.epoch += 1; state.libraryId = id; state.library = null; state.documents = []; state.selected.clear();
            closeDialog(); renderDetail(); renderLibraries();
            $('library-results').replaceChildren(); $('library-packages').replaceChildren(); $('library-semantic').textContent = '';
            if (!id) return;
            var current = ticket('detail');
            try {
                var result = await api.request('/libraries/' + encodeURIComponent(id));
                if (!current()) return;
                state.library = result.library; state.documents = result.documents;
                state.documents.forEach(function (document) { state.selected.add(document.document_id); });
                renderDetail(); await loadPackages();
            } catch (error) { if (current()) feedback(errorKey(error), true); }
        }
        async function refreshLibraries() {
            var current = ticket('libraries');
            var result = await api.request('/libraries');
            if (!current()) return;
            state.libraries = result.libraries;
            renderLibraries();
            var id = state.libraries.some(function (item) { return item.library_id === state.libraryId; }) ? state.libraryId : (state.libraries[0] || {}).library_id || '';
            await selectLibrary(id);
        }
        /** Poll server records, including jobs restored after a process restart. */
        async function refreshJobs() {
            var current = ticket('jobs');
            try {
                var result = await api.request('/jobs');
                if (!current()) return;
                var old = state.jobs;
                state.jobs = result.jobs; renderJobs();
                updateTaskFeedback();
                // Small jobs may finish before their first poll; newly observed terminal records still reconcile.
                var finished = state.jobs.filter(function (job) { var previous = old.find(function (item) { return item.job_id === job.job_id; }); return !active(job) && (!previous || previous.state !== job.state); });
                if (finished.some(function (job) { return ((job.kind === 'install' || job.kind === 'market_install') && job.state === 'completed') || (job.library_id && job.library_id === state.libraryId); })) state.reconcileNeeded = true;
                await reconcileAfterJobs();
            } finally {
                if (current()) {
                    if (state.timer) host.clearTimeout(state.timer);
                    if (state.jobs.some(active)) state.timer = host.setTimeout(function () {
                        var message = state.feedback;
                        refreshJobs().catch(function (error) {
                            backgroundFailure(error, message);
                        });
                    }, 2500);
                }
            }
        }
        async function reconcileAfterJobs() {
            if (!state.reconcileNeeded || state.dialog || state.disposed) return;
            state.reconcileNeeded = false;
            try { await refreshLibraries(); }
            catch (error) { state.reconcileNeeded = true; throw error; }
        }
        function renderJobs() {
            var target = $('library-jobs'); target.replaceChildren();
            if (!state.jobs.length) return empty(target, 'noTasks');
            state.jobs.forEach(function (job) {
                var row = node('article', undefined, 'library-row');
                var heading = node('div', undefined, 'section-heading');
                var kindKeys = { preview: 'previewTask', index: 'indexTask', build: 'buildTask', install: 'installTask', market_publish: 'publishTask', market_install: 'marketInstall' };
                var stateKeys = { queued: 'queued', running: 'running', completed: 'completed', failed: 'taskFailed', cancelled: 'cancelled', interrupted: 'interrupted' };
                heading.appendChild(node('strong', t(kindKeys[job.kind] || 'tasks') + ' · ' + t(stateKeys[job.state] || 'running')));
                var actions = node('div', undefined, 'library-actions');
                if (active(job)) actions.appendChild(button(job.cancel_requested ? 'cancelling' : 'cancel', 'cancel-job', job.job_id));
                var retryable = ['failed', 'interrupted', 'cancelled'].indexOf(job.state) >= 0;
                if (retryable && !job.input_discarded) actions.appendChild(button('retry', 'retry-job', job.job_id));
                // Failed parsers retain their input for retry, but users can explicitly release its bounded slot.
                if (retryable && !job.input_discarded && job.preview_id) actions.appendChild(button('discardPreview', 'discard-preview', job.preview_id));
                if (job.state === 'completed' && !job.input_discarded && job.result && job.result.preview_id) actions.appendChild(button('preview', 'preview', job.result.preview_id));
                heading.appendChild(actions); row.appendChild(heading);
                if (job.reason) row.appendChild(node('p', t(errorKey(new Error(job.reason))), 'library-note'));
                // A visible commit remains successful even when the final
                // directory flush was unconfirmed; never offer a duplicate retry.
                if (hasStorageWarning(job.result)) row.appendChild(node('p', t('storageSyncUnconfirmed'), 'library-note'));
                if (job.input_discarded) row.appendChild(node('p', t('previewDiscarded'), 'library-note'));
                if (job.kind === 'market_publish' && job.state === 'completed' && job.result && job.result.share) {
                    row.appendChild(node('p', job.result.share.title + ' · ' + shareStatus(job.result.share.status), 'library-note'));
                    settlePublication(job);
                }
                target.appendChild(row);
            });
            updateControls();
        }
        function closeDialog(options) {
            var previous = state.dialog;
            state.dialogEpoch += 1;
            state.dialog = null;
            if ($('library-dialog').open) $('library-dialog').close();
            // A confirmed import now owns its source; only abandoned previews are reclaimed.
            if (previous && previous.discard && !(options && options.confirmed) && !previous.submitting) previous.discard().catch(function () { feedback('previewCleanupFailed', true); });
            // Defer through a microtask so replacing a dialog cannot trigger a refresh underneath it.
            if (state.reconcileNeeded) {
                var message = state.feedback;
                Promise.resolve().then(reconcileAfterJobs).catch(function (error) { backgroundFailure(error, message); });
            }
        }
        function openDialog(config) {
            closeDialog(); state.dialog = config;
            $('library-dialog-title').textContent = t(config.title);
            $('library-dialog-description').textContent = config.description || '';
            $('library-dialog-input').hidden = !config.input; $('library-dialog-label').hidden = !config.input;
            $('library-dialog-input').required = !!config.input; $('library-dialog-input').value = config.value || '';
            $('library-dialog-input').maxLength = config.maxLength || 120;
            $('library-dialog-input').disabled = !!config.locked;
            $('library-dialog-label').textContent = t(config.inputLabel || 'name');
            $('library-dialog-label').setAttribute('data-i18n', 'knowledgeLibrary.' + (config.inputLabel || 'name'));
            $('library-dialog-content').replaceChildren(); $('library-dialog-error').hidden = true;
            $('library-dialog-submit').hidden = !config.submit;
            $('library-dialog-submit').textContent = t(config.confirm || 'confirm');
            $('library-dialog-submit').setAttribute('data-i18n', 'knowledgeLibrary.' + (config.confirm || 'confirm'));
            if (config.content) $('library-dialog-content').appendChild(config.content);
            $('library-dialog').showModal(); updateControls();
            if (config.input) $('library-dialog-input').focus();
        }
        async function showPreview(id) {
            var current = ticket('preview');
            var dialogEpoch = ++state.dialogEpoch;
            var result = await api.request('/previews/' + encodeURIComponent(id));
            if (!current() || dialogEpoch !== state.dialogEpoch) return;
            var preview = result.preview;
            var epoch = state.epoch;
            // The confirmation is bound to the server's immutable preview, never to browser-supplied text.
            openDialog({ title: 'preview', description: preview.filename, content: node('pre', preview.content, 'library-source'), confirm: 'confirmImport', discard: async function () {
                await api.request('/previews/' + encodeURIComponent(id), { method: 'DELETE' });
                await refreshJobs();
            }, submit: async function () {
                var result = await api.request('/previews/' + encodeURIComponent(id) + '/confirm', { method: 'POST', body: { sha256: preview.sha256 } });
                closeDialog({ confirmed: true }); taskFeedback(result.job, epoch); await refreshJobs();
            } });
        }
        async function showDocument(id) {
            var current = ticket('source');
            var dialogEpoch = ++state.dialogEpoch;
            var result = await api.request('/libraries/' + encodeURIComponent(state.libraryId) + '/documents/' + encodeURIComponent(id));
            if (!current() || dialogEpoch !== state.dialogEpoch) return;
            openDialog({ title: 'source', description: result.document.filename, content: node('pre', result.document.content, 'library-source') });
        }
        function sourceLabel(location) {
            if (!location || typeof location !== 'object' || Array.isArray(location)) return '';
            var labels = [];
            // Whitelist real source coordinates. Technical offsets, split IDs
            // and unknown fields remain in the protocol and are never relabelled.
            if (Number.isSafeInteger(location.page) && location.page > 0) labels.push(t('sourcePage', { page: location.page }));
            if (Number.isSafeInteger(location.paragraph) && location.paragraph > 0) labels.push(t('sourceParagraph', { paragraph: location.paragraph }));
            if (typeof location.section === 'string' && location.section.trim()) labels.push(t('sourceSection', { section: location.section }));
            return labels.join(' · ');
        }
        async function search(query) {
            var current = ticket('search');
            $('library-semantic').textContent = t('searching');
            try {
                var result = await api.request('/libraries/' + encodeURIComponent(state.libraryId) + '/search', { method: 'POST', body: { query: query, limit: 10 } });
                if (!current()) return;
                $('library-semantic').textContent = semanticLabel(result.semantic_state);
                var target = $('library-results'); target.replaceChildren();
                if (!result.hits.length) empty(target, 'noResults');
                result.hits.forEach(function (hit) {
                    var card = node('article', undefined, 'library-result');
                    card.appendChild(node('h4', hit.filename));
                    var location = sourceLabel(hit.location);
                    if (location) card.appendChild(node('p', location, 'library-row-meta'));
                    card.appendChild(node('p', hit.text, 'library-excerpt'));
                    card.appendChild(button('view', 'view', hit.document_id)); target.appendChild(card);
                });
            } catch (error) { if (current()) { $('library-semantic').textContent = ''; feedback(errorKey(error), true); } }
        }
        async function upload(file) {
            if (!file || state.uploadLibraryId !== state.libraryId) return;
            if (!/\.(txt|md|markdown|pdf|docx)$/i.test(file.name)) return feedback('unsupported', true);
            if (file.size > MAX_DOCUMENT_BYTES) return feedback('tooLarge', true);
            var form = new host.FormData(); form.append('file', file);
            if (state.replacement) form.append('document_id', state.replacement);
            var libraryId = state.uploadLibraryId; var epoch = state.epoch;
            await mutate(async function () {
                var result = await api.request('/libraries/' + encodeURIComponent(libraryId) + '/documents', { method: 'POST', file: form });
                taskFeedback(result.job, epoch); await refreshJobs();
            });
        }
        function renderPackages() {
            var target = $('library-packages'); target.replaceChildren();
            if (!state.packages.length) return empty(target, 'noPackages');
            state.packages.forEach(function (artifact) {
                var row = node('article', undefined, 'library-row');
                var heading = node('div', undefined, 'section-heading');
                heading.appendChild(node('strong', artifact.name + ' · ' + artifact.version));
                // Keep the two artifact actions together; the page's shared
                // action layout wraps them as a group in narrow translations.
                var actions = node('div', undefined, 'library-actions');
                var link = button('export', 'package-export', artifact.artifact_id);
                link.className = 'library-download'; actions.appendChild(link);
                actions.appendChild(button('publish', 'market-publish', artifact.artifact_id));
                heading.appendChild(actions); row.appendChild(heading);
                row.appendChild(node('p', artifact.sha256, 'library-row-meta'));
                target.appendChild(row);
            });
        }
        async function loadPackages() {
            var current = ticket('packages');
            var result = await api.request('/packages');
            if (!current()) return;
            state.packages = result.packages; renderPackages();
        }
        /** Review private metadata from the selected digest, never the current draft selection. */
        async function readPackageScope(artifactId) {
            var dialogEpoch = ++state.dialogEpoch; var epoch = state.epoch;
            var result = await api.request('/packages/' + encodeURIComponent(artifactId) + '/preview');
            if (state.disposed || epoch !== state.epoch || dialogEpoch !== state.dialogEpoch) return null;
            if (!result.package || result.package.artifact_id !== artifactId || result.package.sha256 !== artifactId) throw new Error('package_artifact_hash_mismatch');
            return result.package;
        }
        function scopeInformation(artifact) {
            var content = node('section', undefined, 'library-package-scope');
            content.appendChild(node('h3', t('packageContents')));
            content.appendChild(node('p', artifact.name + ' · ' + artifact.version));
            content.appendChild(node('p', t('scopeDocuments', { count: artifact.documents.length })));
            var files = node('ul');
            artifact.documents.forEach(function (document) {
                files.appendChild(node('li', document.name + ' · ' + t('scopeCharacters', { count: document.characters })));
            });
            content.appendChild(files);
            content.appendChild(node('p', t('sourceName') + ': ' + artifact.source.author));
            content.appendChild(node('p', t('license') + ': ' + artifact.source.license));
            if (artifact.source.url) content.appendChild(node('p', artifact.source.url));
            content.appendChild(node('p', t('checksum') + ': ' + artifact.sha256, 'library-row-meta'));
            return content;
        }
        async function exportPackage(artifactId) {
            var artifact = await readPackageScope(artifactId);
            if (!artifact) return;
            var config = { title: 'export', description: t('exportScopeHint'), content: scopeInformation(artifact), confirm: 'export', submit: async function () {
                if (state.dialog !== config) return;
                // Local export is explicit and does not submit anything to the market.
                var link = node('a'); link.href = BASE + '/packages/' + encodeURIComponent(artifact.artifact_id) + '/download';
                link.setAttribute('download', ''); link.click(); closeDialog();
            } };
            openDialog(config);
        }
        async function build() {
            var ids = Array.from(state.selected);
            if (!ids.length) return feedback('selectDocuments', true);
            var libraryId = state.libraryId; var epoch = state.epoch;
            var fields = node('div');
            var sourceName = node('input'); sourceName.required = true; sourceName.maxLength = 160;
            var license = node('input'); license.required = true; license.maxLength = 200;
            var sourceLabel = node('label', t('sourceName')); sourceLabel.appendChild(sourceName);
            var licenseLabel = node('label', t('license')); licenseLabel.appendChild(license);
            fields.appendChild(sourceLabel); fields.appendChild(licenseLabel);
            openDialog({ title: 'build', description: t('buildSelection', { count: ids.length }), input: true, inputLabel: 'version', content: fields, confirm: 'build', submit: async function (version) {
                var result = await api.request('/libraries/' + encodeURIComponent(libraryId) + '/packages', { method: 'POST', body: { version: version, source: { author: sourceName.value.trim(), license: license.value.trim(), url: '' }, document_ids: ids } });
                closeDialog(); taskFeedback(result.job, epoch); await refreshJobs();
            } });
        }
        async function install(file) {
            if (!/\.neko-knowledge-pack\.zip$/i.test(file.name)) return feedback('invalidPackage', true);
            var installedId = state.library && state.library.kind === 'installed' ? state.libraryId : '';
            var epoch = state.epoch;
            var content = node('div'); var update = node('input'); update.type = 'checkbox';
            if (installedId) { var label = node('label'); label.appendChild(update); label.appendChild(node('span', t('updateInstalled'))); content.appendChild(label); }
            openDialog({ title: 'install', description: file.name, content: content, confirm: 'install', submit: async function () {
                var form = new host.FormData(); form.append('file', file);
                if (installedId && update.checked) form.append('library_id', installedId);
                var result = await api.request('/packages/install', { method: 'POST', file: form });
                closeDialog(); taskFeedback(result.job, epoch); await refreshJobs(); await refreshLibraries();
            } });
        }

        function shareStatus(status) {
            var keys = { published: 'sharePublished', pending_review: 'sharePendingReview', rejected: 'shareRejected', withdrawn: 'shareWithdrawn', hidden: 'shareHidden' };
            return t(keys[status] || 'shareUnknown');
        }
        async function refreshMarket() {
            var current = ticket('market');
            try {
                var result = await api.request('/market/status');
                if (!current()) return;
                state.market = result;
                $('library-market-status').textContent = t(result.authenticated ? 'marketConnected' : 'marketDisconnected');
            } catch (_) { if (current()) { state.market = null; $('library-market-status').textContent = t('marketUnavailable'); } }
        }
        /** Open only server-issued HTTP(S) destinations; account credentials never enter page state. */
        async function openMarket(kind) {
            var popup = host.open('', '_blank');
            if (!popup) throw new Error('popup_blocked');
            popup.opener = null;
            try {
                var result = await api.request('/market/' + kind, { method: 'POST', body: {} });
                var target = new URL(kind === 'login' ? result.auth_url : result.url);
                if (!/^https?:$/.test(target.protocol) || target.username || target.password) throw new Error('market_invalid_response');
                if (kind === 'browse' && state.market && target.origin !== new URL(state.market.market_url).origin) throw new Error('market_invalid_response');
                popup.location.href = target.href;
            } catch (error) { popup.close(); throw error; }
        }
        function shareInformation(share) {
            var box = node('div', undefined, 'library-share-information');
            box.appendChild(node('h3', share.title));
            box.appendChild(node('p', t('shareVersion') + ': ' + share.preview.package_version + ' · ' + shareStatus(share.status)));
            box.appendChild(node('p', t('checksum') + ': ' + share.file_sha256, 'library-row-meta'));
            return box;
        }
        async function readShare(value, requirePublished) {
            if (!state.market) await refreshMarket();
            if (!state.market) throw new Error('market_unavailable');
            var id = parseShareId(value, state.market.market_url);
            var result = await api.request('/market/shares/' + id);
            var share = result.share;
            if (!share || share.kind !== 'knowledge' || !share.preview || share.preview.format !== 'neko.personal-knowledge-package' || share.preview.format_version !== 1 || !/\.neko-knowledge-pack\.zip$/i.test(share.filename || '')) throw new Error('market_not_knowledge_package');
            if (requirePublished && share.status !== 'published') throw new Error('market_not_published');
            if (!Number.isSafeInteger(share.id) || !Number.isSafeInteger(share.revision) || share.revision < 1 || !/^[a-f0-9]{64}$/.test(share.file_sha256)) throw new Error('market_invalid_response');
            return share;
        }
        function marketInstall() {
            var config = { title: 'marketInstall', input: true, inputLabel: 'shareLink', maxLength: 2048, confirm: 'readShare', submit: async function (value) {
                var share = await readShare(value, true);
                if (state.dialog !== config) return;
                var frozen = { share_id: share.id, revision: share.revision, file_sha256: share.file_sha256 };
                var content = shareInformation(share);
                var update = node('input'); update.type = 'checkbox';
                var installedId = state.library && state.library.kind === 'installed' ? state.libraryId : '';
                var epoch = state.epoch;
                if (installedId) { var label = node('label'); label.appendChild(update); label.appendChild(node('span', t('updateInstalled'))); content.appendChild(label); }
                // Confirmation owns the exact revision/hash just displayed, never a later latest response.
                openDialog({ title: 'confirmMarketInstall', description: t('marketInstallHint'), content: content, confirm: 'install', submit: async function () {
                    var payload = Object.assign({}, frozen);
                    if (installedId && update.checked) payload.library_id = installedId;
                    var result = await api.request('/market/install', { method: 'POST', body: payload });
                    closeDialog(); taskFeedback(result.job, epoch); await refreshJobs();
                } });
            } };
            openDialog(config);
        }
        /** Retain only selected public metadata and a UUID, never document text or account tokens. */
        function publications() {
            try { return JSON.parse(host.localStorage.getItem(PUBLISH_STORAGE_KEY) || '{}'); }
            catch (_) { throw new Error('publication_storage_unavailable'); }
        }
        function savePublications(records) {
            try { host.localStorage.setItem(PUBLISH_STORAGE_KEY, JSON.stringify(records)); }
            catch (_) { throw new Error('publication_storage_unavailable'); }
        }
        function publicationId() {
            if (typeof host.crypto.randomUUID === 'function') return host.crypto.randomUUID();
            // getRandomValues remains available on LAN HTTP pages where randomUUID is unavailable.
            var bytes = new Uint8Array(16); host.crypto.getRandomValues(bytes);
            bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
            var hex = Array.from(bytes, function (value) { return value.toString(16).padStart(2, '0'); }).join('');
            return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
        }
        function settlePublication(job) {
            try {
                var records = publications(); var artifact = job.result.artifact_id;
                if (records[artifact] && records[artifact].job_id === job.job_id) { delete records[artifact]; savePublications(records); }
            } catch (_) { /* A retained draft is safer than silently inventing a new submission identity. */ }
        }
        async function retryJob(id) {
            var result = await api.request('/jobs/' + encodeURIComponent(id) + '/retry', { method: 'POST', body: {} });
            var previous = state.jobs.find(function (job) { return job.job_id === id; });
            if (previous && previous.kind === 'market_publish') {
                var records = publications();
                Object.keys(records).forEach(function (artifact) { if (records[artifact].job_id === id) records[artifact].job_id = result.job.job_id; });
                savePublications(records);
            }
            return result;
        }
        async function publish(artifactId) {
            var artifact = await readPackageScope(artifactId);
            if (!artifact) return;
            var epoch = state.epoch;
            var records = publications(); var pending = records[artifactId];
            var job = pending && state.jobs.find(function (item) { return item.job_id === pending.job_id; });
            // These explicit pre-commit rejections permit a new reviewed selection; transport failures do not.
            if (job && job.state === 'failed' && ['share_revision_conflict', 'share_knowledge_version_conflict', 'share_knowledge_identity_mismatch', 'share_knowledge_format_downgrade', 'share_knowledge_package_invalid', 'invalid_publish_request'].indexOf(job.reason) >= 0) {
                delete records[artifactId]; savePublications(records); pending = null; job = null;
            }
            var content = node('div');
            var description = node('textarea'); description.maxLength = 5000; description.rows = 4;
            description.value = pending ? pending.payload.description || '' : ''; description.disabled = !!pending;
            var descriptionLabel = node('label', t('shareDescription')); descriptionLabel.appendChild(description); content.appendChild(descriptionLabel);
            var update = node('input'); update.type = 'checkbox'; update.disabled = !!pending;
            var updateLabel = node('label'); updateLabel.appendChild(update); updateLabel.appendChild(node('span', t('updateShare'))); content.appendChild(updateLabel);
            var shareInput = node('input'); shareInput.maxLength = 2048; shareInput.disabled = true;
            var shareLabel = node('label', t('shareLink')); shareLabel.appendChild(shareInput); content.appendChild(shareLabel);
            var lookup = node('button', t('readShare')); lookup.type = 'button'; lookup.disabled = true; content.appendChild(lookup);
            var information = node('div'); content.appendChild(information);
            // Keep public-metadata controls in their existing order; private file
            // scope is rendered only here and never retained in localStorage.
            content.appendChild(scopeInformation(artifact));
            var snapshot = null;
            update.addEventListener('change', function () { shareInput.disabled = !update.checked; lookup.disabled = !update.checked; snapshot = null; information.replaceChildren(); });
            shareInput.addEventListener('input', function () { snapshot = null; information.replaceChildren(); });
            var config = { title: 'publish', description: t(pending ? 'retryPublicationHint' : 'publishHint') + '\n' + t('publishScopeHint'),
                input: true, inputLabel: 'shareTitle', value: pending ? pending.payload.title : artifact.name, locked: !!pending,
                content: content, confirm: pending ? 'retryPublication' : 'publish', submit: job && active(job) ? null : async function (title) {
                    if (state.dialog !== config) return;
                    var submitted;
                    if (pending && pending.job_id) {
                        var retried = await retryJob(pending.job_id);
                        records = publications(); records[artifactId] = { payload: pending.payload, job_id: retried.job.job_id }; savePublications(records);
                        submitted = retried.job;
                    } else {
                        if (!pending && update.checked && !snapshot) throw new Error('read_share_first');
                        var payload = pending ? pending.payload : { artifact_id: artifactId, title: title, description: description.value.trim(), client_request_id: publicationId() };
                        if (!pending && update.checked) { payload.share_id = snapshot.id; payload.revision = snapshot.revision; }
                        records = publications(); records[artifactId] = { payload: payload }; savePublications(records);
                        pending = records[artifactId];
                        // Lock to the durable payload before sending; an unknown response must replay identical metadata.
                        $('library-dialog-input').disabled = true; $('library-dialog-input').value = payload.title;
                        description.disabled = true; update.disabled = true; shareInput.disabled = true; lookup.disabled = true;
                        $('library-dialog-description').textContent = t('retryPublicationHint');
                        $('library-dialog-submit').textContent = t('retryPublication');
                        $('library-dialog-submit').setAttribute('data-i18n', 'knowledgeLibrary.retryPublication');
                        var result = await api.request('/market/publish', { method: 'POST', body: payload });
                        records = publications(); records[artifactId] = { payload: payload, job_id: result.job.job_id }; savePublications(records);
                        submitted = result.job;
                    }
                    closeDialog(); taskFeedback(submitted, epoch); await refreshJobs();
                } };
            lookup.addEventListener('click', async function () {
                var value = shareInput.value; lookup.disabled = true;
                try { var share = await readShare(value, false); if (state.dialog === config && shareInput.value === value && update.checked) { snapshot = { id: share.id, revision: share.revision }; information.replaceChildren(shareInformation(share)); } }
                catch (error) { if (state.dialog === config) { $('library-dialog-error').hidden = false; $('library-dialog-error').textContent = t(errorKey(error)); } }
                finally { if (state.dialog === config) lookup.disabled = !update.checked; }
            });
            openDialog(config);
        }

        function confirmDelete(path, title) {
            openDialog({ title: title, description: t('deleteHint'), confirm: 'delete', submit: async function () {
                await api.request(path, { method: 'DELETE' }); closeDialog(); await refreshLibraries();
            } });
        }
        async function action(name, id) {
            if (name === 'select') return selectLibrary(id);
            if (name === 'close-dialog') return closeDialog();
            if (name === 'refresh') return refresh();
            if (state.busy) return;
            var base = '/libraries/' + encodeURIComponent(state.libraryId);
            var actionEpoch = state.epoch;
            if (name === 'create' || name === 'rename') return openDialog({ title: name, input: true, value: name === 'rename' ? state.library.name : '', submit: async function (value) {
                var result = await api.request(name === 'create' ? '/libraries' : base, { method: name === 'create' ? 'POST' : 'PATCH', body: { name: value } });
                closeDialog();
                if (name === 'create' && actionEpoch === state.epoch) state.libraryId = result.library.library_id;
                await refreshLibraries();
                if (hasStorageWarning(result.library)) feedback('storageSyncUnconfirmed');
            } });
            if (name === 'delete-library') return confirmDelete(base, 'deleteLibrary');
            if (name === 'delete-document') return confirmDelete(base + '/documents/' + encodeURIComponent(id), 'deleteDocument');
            if (name === 'upload' || name === 'replace') { state.replacement = name === 'replace' ? id : ''; state.uploadLibraryId = state.libraryId; $('library-upload').click(); return; }
            if (name === 'preview') return showPreview(id);
            if (name === 'view') return showDocument(id);
            if (name === 'cancel-job') return mutate(async function () { await api.request('/jobs/' + encodeURIComponent(id) + '/cancel', { method: 'POST', body: {} }); await refreshJobs(); });
            if (name === 'retry-job') return mutate(async function () {
                var epoch = state.epoch; var result = await retryJob(id);
                taskFeedback(result.job, epoch); await refreshJobs();
            });
            if (name === 'discard-preview') return mutate(async function () { await api.request('/previews/' + encodeURIComponent(id), { method: 'DELETE' }); await refreshJobs(); });
            if (name === 'market-login' || name === 'market-browse') return mutate(function () { return openMarket(name === 'market-login' ? 'login' : 'browse'); });
            if (name === 'market-install') return marketInstall();
            if (name === 'market-publish') return mutate(function () { return publish(id); });
            if (name === 'package-export') return mutate(function () { return exportPackage(id); });
            if (name === 'build') return build();
            if (name === 'install') return $('library-install').click();
        }
        async function refresh() {
            var loading = feedback('loading');
            try {
                await Promise.all([refreshLibraries(), refreshJobs(), refreshMarket()]);
                if (state.feedback === loading) feedback('');
            } catch (error) { if (state.feedback === loading) feedback(errorKey(error), true); }
        }
        function dispose() { state.disposed = true; state.epoch += 1; if (state.timer) host.clearTimeout(state.timer); }
        function mount() {
            $('knowledge-library-app').addEventListener('click', function (event) {
                var element = event.target.closest('[data-action]');
                if (element && !element.disabled) Promise.resolve(action(element.dataset.action, element.dataset.id)).catch(function (error) { feedback(errorKey(error), true); });
            });
            $('library-dialog').addEventListener('click', function (event) { if (event.target.closest('[data-action="close-dialog"]')) closeDialog(); });
            $('library-dialog').addEventListener('cancel', function (event) { event.preventDefault(); if (!state.busy) closeDialog(); });
            $('library-dialog-form').addEventListener('submit', function (event) { event.preventDefault(); var config = state.dialog; if (!config || !config.submit) return; var value = $('library-dialog-input').value.trim(); if (config.input && !value) return; mutate(async function () { config.submitting = true; try { await config.submit(value); } finally { config.submitting = false; } }); });
            $('library-search').addEventListener('submit', function (event) { event.preventDefault(); var query = $('library-query').value.trim(); if (query) search(query); });
            $('library-upload').addEventListener('change', function (event) { var file = event.target.files[0]; event.target.value = ''; upload(file); });
            $('library-install').addEventListener('change', function (event) { var file = event.target.files[0]; event.target.value = ''; if (file) mutate(function () { return install(file); }); });
            host.addEventListener('pagehide', dispose, { once: true });
            doc.addEventListener('visibilitychange', function () { if (!state.disposed && !doc.hidden) refreshMarket(); });
            host.addEventListener('localechange', function () { if (!state.disposed) { renderLibraries(); renderDetail(); renderJobs(); renderPackages(); } });
            // Static controls stay disabled until translations and all handlers are ready.
            doc.querySelectorAll('[data-library-startup]').forEach(function (element) { element.disabled = false; });
            updateControls();
            return refresh();
        }
        return { state: state, mount: mount, selectLibrary: selectLibrary, refresh: refresh, refreshJobs: refreshJobs, showPreview: showPreview, search: search, upload: upload, install: install, action: action, marketInstall: marketInstall, publish: publish, closeDialog: closeDialog, dispose: dispose };
    }

    window.NekoKnowledgeLibrary = Object.freeze({ createApi: createApi, createPage: createPage, parseShareId: parseShareId });
    if (document.getElementById('knowledge-library-app')) {
        var booted = false;
        // The shared i18n script exports t before emitting localechange, including its fallback path.
        function boot() {
            if (booted || typeof window.t !== 'function') return;
            booted = true;
            window.removeEventListener('localechange', boot);
            createPage(window, document, createApi(window)).mount();
        }
        window.addEventListener('localechange', boot);
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
        else boot();
    }
})();

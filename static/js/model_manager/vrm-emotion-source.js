/** At most one current and one superseded load; no request queue or registry. */
(function () {
    'use strict';
    function canonicalUrl(value) {
        if (!value) return '';
        const url = new URL(value, window.location.href);
        return url.origin === window.location.origin ? url.href : '';
    }

    window.createVrmEmotionSource = function ({ getManager, getSelectedUrl, getLoadingTask,
        getLoadingTasks = () => [getLoadingTask()].filter(Boolean), loadModel }) {
        let pending = null;
        let superseded = null;
        let failure = null;
        let rejectedModelUrl = '';
        let disposed = false;

        function unfinishedCount(exclude) {
            // A source job and its opener callback describe the same load.
            // The opener exposes at most two jobs, including superseded work.
            // This temporary set has at most four entries and is never retained.
            return new Set([pending, superseded, ...getLoadingTasks()]
                .filter(job => job && job !== exclude && !job.settled)
                .map(job => job.loadingTask || job)
                .filter(task => !task.settled)).size;
        }

        function snapshot(modelUrl) {
            const manager = getManager();
            if (disposed) return { state: 'failed', errorCode: 'sourceUnavailable' };
            if (pending || getLoadingTask()) return { state: 'loading', modelUrl };
            if (failure?.modelUrl === modelUrl && failure.loadToken === manager?._activeLoadToken) {
                return { state: 'failed', ...failure };
            }
            if (canonicalUrl(getSelectedUrl()) !== modelUrl
                || canonicalUrl(manager?.currentModel?.url) !== modelUrl
                || !manager?._isModelReadyForInteraction
                || manager._isDisposed || !manager.currentModel?.vrm) return null;
            const expressions = manager.expression.getExpressionList();
            return { state: 'ready', modelUrl, loadToken: manager._activeLoadToken, expressions: [...expressions] };
        }

        function request(info, force = false) {
            const modelUrl = canonicalUrl(info?.url);
            if (!modelUrl) return { state: 'failed', errorCode: 'invalidModel' };
            if (disposed) return { state: 'failed', errorCode: 'sourceUnavailable' };
            // An adopted opener task has its own settlement path; release its
            // slot when the owner finishes, even if our replacement is pending.
            if (superseded?.settled) superseded = null;
            const current = snapshot(modelUrl);
            const existingAdmission = current?.state === 'ready'
                || pending?.modelUrl === modelUrl
                || canonicalUrl(getSelectedUrl()) === modelUrl;
            // Ordinary polling observes existing work; only an explicit retry
            // requests a replacement of the same admitted model.
            if (!force && existingAdmission && current) {
                if (current.state === 'ready' && rejectedModelUrl === modelUrl) rejectedModelUrl = '';
                return current;
            }
            // One rejected URL is retained separately from parsing errors so a
            // late failure of admitted work cannot turn rejection into a queue.
            if (!force && rejectedModelUrl === modelUrl) {
                return { state: 'failed', modelUrl, errorCode: 'recoveryBlocked' };
            }
            if (!existingAdmission && unfinishedCount() >= 2) {
                rejectedModelUrl = modelUrl;
                return { state: 'failed', modelUrl, errorCode: 'recoveryBlocked' };
            }
            let replacing = false;
            const active = pending || getLoadingTask();
            if (active && (force || Date.now() - active.startedAt >= 120000)) {
                // VRMLoader cannot abort parsing. Permit one replacement using the
                // manager's load token, but never accumulate abandoned promises.
                if (superseded || unfinishedCount() >= 2) return { state: 'failed', errorCode: 'recoveryBlocked' };
                superseded = active;
                pending = null;
                replacing = true;
            }
            if (!replacing && current?.state === 'loading') return current;
            if (!replacing && current && !force) return current;
            failure = null;
            rejectedModelUrl = '';
            const job = { modelUrl, startedAt: Date.now(), settled: false };
            pending = job;
            Promise.resolve().then(() => {
                if (disposed || pending !== job) return;
                // An opener selection can arrive after request() but before this
                // microtask. Recheck before starting any additional work.
                if (unfinishedCount(job) >= 2) {
                    const error = new Error('Load recovery capacity exhausted');
                    error.errorCode = 'recoveryBlocked';
                    throw error;
                }
                const result = loadModel(info);
                job.loadingTask = getLoadingTask();
                return result;
            }).then(result => {
                if (disposed || pending !== job) return;
                if (result?.error) throw Object.assign(new Error(result.error), { errorCode: result.errorCode });
                const manager = getManager();
                if (canonicalUrl(getSelectedUrl()) !== modelUrl
                    || canonicalUrl(manager?.currentModel?.url) !== modelUrl || !manager?._isModelReadyForInteraction) {
                    throw new Error(window.t ? window.t('vrmEmotionManager.modelLoadFailed') : '所选模型未成功加载，请重试');
                }
            }).catch(error => {
                if (!disposed && pending === job) {
                    if (error.errorCode === 'recoveryBlocked') rejectedModelUrl = modelUrl;
                    failure = {
                        modelUrl, loadToken: getManager()?._activeLoadToken,
                        error: error.message, errorCode: error.errorCode || 'modelLoadFailed'
                    };
                }
            }).finally(() => {
                job.settled = true;
                if (pending === job) pending = null;
                if (superseded === job) superseded = null;
            });
            return { state: 'loading', modelUrl };
        }

        function isCurrent(value) {
            if (!value || disposed) return false;
            const current = snapshot(value.modelUrl);
            return current?.state === 'ready' && current.loadToken === value.loadToken;
        }

        function preview(value, expression) {
            if (!isCurrent(value)) return false;
            const manager = getManager();
            if (expression !== 'neutral' && !manager.expression.getExpressionList().includes(expression)) return false;
            manager.expression.setBaseExpression(expression);
            return true;
        }

        function dispose(event) {
            if (event?.persisted) return;
            disposed = true;
            pending = null;
            superseded = null;
            failure = null;
            rejectedModelUrl = '';
            window.removeEventListener('pagehide', dispose);
        }
        window.addEventListener('pagehide', dispose);
        return { request, isCurrent, preview, dispose };
    };
})();

(function () {
    if (window.appStorageLocation) return;

    var state = {
        initialized: false,
        initPromise: null,
        phase: 'hidden',
        systemStatus: null,
        startupDecision: null,
        bootstrap: null,
        overlay: null,
        loadingView: null,
        loadingTitle: null,
        loadingSubtitle: null,
        selectionView: null,
        errorView: null,
        banner: null,
        currentPath: null,
        recommendedPath: null,
        anchorPath: null,
        cloudsavePath: null,
        legacyList: null,
        recommendedAdvice: null,
        recommendedRestart: null,
        otherPanel: null,
        legacyChoices: null,
        customInput: null,
        useOtherButton: null,
        previewPanel: null,
        previewText: null,
        previewSource: null,
        previewTarget: null,
        errorText: null,
        otherSelection: {
            key: '',
            path: '',
        },
    };

    function createDeferred() {
        var deferred = {
            settled: false,
            promise: null,
            resolve: null,
        };
        deferred.promise = new Promise(function (resolve) {
            deferred.resolve = function (value) {
                if (deferred.settled) return;
                deferred.settled = true;
                resolve(value);
            };
        });
        return deferred;
    }

    state.startupDecision = createDeferred();

    function translate(key, fallback) {
        try {
            if (typeof window.safeT === 'function') {
                return window.safeT(key, fallback);
            }
            if (typeof window.t === 'function') {
                var translated = window.t(key, { defaultValue: fallback });
                if (typeof translated === 'string' && translated) return translated;
            }
        } catch (_) {}
        return fallback || key;
    }

    function createElement(tag, className, text) {
        var element = document.createElement(tag);
        if (className) element.className = className;
        if (typeof text === 'string') element.textContent = text;
        return element;
    }

    function pathEquals(left, right) {
        return String(left || '').trim() === String(right || '').trim();
    }

    function clearChildren(element) {
        if (!element) return;
        while (element.firstChild) {
            element.removeChild(element.firstChild);
        }
    }

    function resolveStartupDecision(payload) {
        if (!state.startupDecision) {
            state.startupDecision = createDeferred();
        }
        state.startupDecision.resolve(payload || {
            canContinue: true,
            reason: 'continue_current_session',
        });
    }

    function setPhase(phase) {
        state.phase = phase;
        if (!state.overlay) return;

        state.overlay.hidden = phase === 'hidden';
        document.body.classList.toggle('storage-location-modal-open', phase !== 'hidden');

        state.loadingView.hidden = phase !== 'loading';
        state.selectionView.hidden = phase !== 'selection_required';
        state.errorView.hidden = phase !== 'error';
    }

    function hideOverlay() {
        // TEMP(Stage 1 development):
        // 这里只关闭当前页面上的覆盖层；刷新主页后，后端仍会返回 selection_required=true，
        // 因而开发阶段会再次弹出，便于反复检查首屏展示与交互。
        setPhase('hidden');
    }

    function setLoadingCopy(title, subtitle) {
        if (state.loadingTitle && typeof title === 'string' && title) {
            state.loadingTitle.textContent = title;
        }
        if (state.loadingSubtitle && typeof subtitle === 'string' && subtitle) {
            state.loadingSubtitle.textContent = subtitle;
        }
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    function shouldBlockMainUi(statusPayload) {
        if (!statusPayload || typeof statusPayload !== 'object') {
            return true;
        }

        var storage = statusPayload.storage || {};
        return statusPayload.ready !== true
            || statusPayload.status === 'migration_required'
            || !!storage.selection_required
            || !!storage.migration_pending
            || !!storage.recovery_required;
    }

    function shouldShowSelectionView(bootstrapPayload) {
        if (!bootstrapPayload || typeof bootstrapPayload !== 'object') {
            return false;
        }

        return !!bootstrapPayload.selection_required
            || !!bootstrapPayload.migration_pending
            || !!bootstrapPayload.recovery_required;
    }

    async function fetchSystemStatus() {
        var response = await fetch('/api/v1/system/status', {
            cache: 'no-store',
            headers: {
                'Accept': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error('system status request failed: ' + response.status);
        }

        var payload = await response.json();
        if (!payload || payload.ok !== true) {
            throw new Error(
                translate('storage.systemStatusUnexpected', '存储启动状态接口返回了未识别的结果。')
            );
        }
        state.systemStatus = payload;
        return payload;
    }

    async function waitForSystemStatus() {
        var lastError = null;

        for (var attempt = 0; attempt < 20; attempt += 1) {
            try {
                var payload = await fetchSystemStatus();
                if (payload.status !== 'starting') {
                    return payload;
                }
            } catch (error) {
                lastError = error;
            }

            setLoadingCopy(
                translate('storage.loadingTitle', '正在确认存储布局状态'),
                translate('storage.loadingWaitSubtitle', '主业务界面会在存储状态确认完成后再继续加载。')
            );
            await sleep(250);
        }

        throw lastError || new Error(
            translate('storage.systemStatusUnavailable', '暂时无法确认本地服务状态，请重试。')
        );
    }

    function renderLegacyList() {
        clearChildren(state.legacyList);
        clearChildren(state.legacyChoices);

        var legacySources = Array.isArray(state.bootstrap && state.bootstrap.legacy_sources)
            ? state.bootstrap.legacy_sources
            : [];

        if (!legacySources.length) {
            state.legacyList.appendChild(
                createElement(
                    'p',
                    'storage-location-empty',
                    translate('storage.legacyEmpty', '未检测到额外的旧数据目录。')
                )
            );
            state.legacyChoices.appendChild(
                createElement(
                    'p',
                    'storage-location-empty',
                    translate('storage.legacyEmpty', '未检测到额外的旧数据目录。')
                )
            );
            return;
        }

        legacySources.forEach(function (path, index) {
            var item = createElement('div', 'storage-location-legacy-item');
            item.appendChild(createElement('div', 'storage-location-label', translate('storage.legacyPath', '检测到的旧数据目录')));
            item.appendChild(createElement('div', 'storage-location-path', path));
            state.legacyList.appendChild(item);

            var choice = createElement('button', 'storage-location-choice');
            choice.type = 'button';
            if (state.otherSelection.key === 'legacy-' + index) {
                choice.classList.add('is-active');
            }
            choice.addEventListener('click', function () {
                state.otherSelection.key = 'legacy-' + index;
                state.otherSelection.path = path;
                renderLegacyList();
                updateOtherButtonState();
            });

            var title = createElement('div', 'storage-location-choice-title');
            title.appendChild(createElement('span', '', translate('storage.useLegacyPath', '使用该旧数据路径')));
            title.appendChild(createElement('span', 'storage-location-choice-check', state.otherSelection.key === 'legacy-' + index ? '✓' : ''));
            choice.appendChild(title);
            choice.appendChild(createElement('div', 'storage-location-path', path));
            state.legacyChoices.appendChild(choice);
        });
    }

    function updateSelectionSummary() {
        if (!state.bootstrap) return;

        var currentRoot = state.bootstrap.current_root || '';
        var recommendedRoot = state.bootstrap.recommended_root || '';
        var currentIsRecommended = pathEquals(currentRoot, recommendedRoot);

        state.currentPath.textContent = currentRoot;
        state.recommendedPath.textContent = recommendedRoot;
        state.anchorPath.textContent = state.bootstrap.anchor_root || '';
        state.cloudsavePath.textContent = state.bootstrap.cloudsave_root || '';

        state.recommendedAdvice.textContent = currentIsRecommended
            ? translate('storage.recommendationAlreadyCurrent', '当前路径已经是推荐位置，本次不建议额外切换。')
            : translate('storage.recommendationMoveSuggested', '推荐迁移到平台标准应用数据目录，以便后续固定锚点与 cloudsave 语义一致。');

        state.recommendedRestart.textContent = currentIsRecommended
            ? translate('storage.recommendationNoRestart', '若使用推荐位置：不需要重启，当前会话可继续。')
            : translate('storage.recommendationNeedsRestart', '若使用推荐位置：后续阶段需要关闭当前实例、迁移数据并自动重启。');

        if (state.bootstrap.recovery_required) {
            state.banner.hidden = false;
            state.banner.textContent =
                (state.bootstrap.migration && state.bootstrap.migration.last_error)
                || translate('storage.recoveryRequired', '检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。');
        } else {
            state.banner.hidden = true;
            state.banner.textContent = '';
        }

        renderLegacyList();
        updateOtherButtonState();
    }

    function updateOtherButtonState() {
        if (!state.useOtherButton) return;
        state.useOtherButton.disabled = !String(state.otherSelection.path || '').trim();
    }

    function openOtherPanel() {
        if (state.previewPanel) {
            state.previewPanel.hidden = true;
        }
        state.otherPanel.hidden = false;
        if (state.customInput) {
            state.customInput.focus();
        }
    }

    function showMigrationPreview(targetPath, selectionSource) {
        if (!state.bootstrap || !state.previewPanel) return;

        state.previewSource.textContent = state.bootstrap.current_root || '';
        state.previewTarget.textContent = targetPath || '';
        state.previewText.textContent = selectionSource === 'recommended'
            ? translate('storage.recommendedPreviewNotice', '如果后续改用推荐位置，正式流程会在当前实例关闭后迁移数据，再自动重启。')
            : translate('storage.customPreviewNotice', '如果后续改用这个位置，正式流程也会在当前实例关闭后迁移数据，再自动重启。');
        state.previewPanel.hidden = false;
        setPhase('selection_required');
    }

    function continueWithCurrentPath() {
        if (state.previewPanel) {
            state.previewPanel.hidden = true;
        }
        if (state.otherPanel) {
            state.otherPanel.hidden = true;
        }
        hideOverlay();
        resolveStartupDecision({
            canContinue: true,
            reason: 'continue_current_session',
        });
    }

    function useRecommendedPath() {
        if (!state.bootstrap) return;
        var recommendedRoot = String(state.bootstrap.recommended_root || '').trim();
        if (!recommendedRoot) return;
        if (pathEquals(recommendedRoot, state.bootstrap.current_root || '')) {
            continueWithCurrentPath();
            return;
        }
        showMigrationPreview(recommendedRoot, 'recommended');
    }

    function useOtherPath() {
        if (!state.bootstrap) return;
        var targetPath = String(state.otherSelection.path || '').trim();
        if (!targetPath) return;
        if (pathEquals(targetPath, state.bootstrap.current_root || '')) {
            continueWithCurrentPath();
            return;
        }
        showMigrationPreview(targetPath, 'custom');
    }

    function showError(error) {
        state.errorText.textContent = error
            ? String(error.message || error)
            : translate('storage.bootstrapError', '无法读取存储位置初始化信息，请重试。');
        setPhase('error');
    }

    function buildInfoPathRow(labelText, targetRefName) {
        var item = createElement('div', 'storage-location-path-item');
        item.appendChild(createElement('div', 'storage-location-label', labelText));
        var value = createElement('div', 'storage-location-path');
        state[targetRefName] = value;
        item.appendChild(value);
        return item;
    }

    function buildSelectionView() {
        var view = createElement('section', 'storage-location-view');
        var shell = createElement('div', 'storage-location-shell');

        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.badge', '存储位置')));
        hero.appendChild(createElement('h2', 'storage-location-title', translate('storage.selectionTitle', '请选择本次运行使用的存储位置')));
        hero.appendChild(createElement('p', 'storage-location-subtitle', translate('storage.selectionSubtitle', '应用已经正常打开。接下来请先在当前页面内确认存储位置，再继续使用。')));
        shell.appendChild(hero);

        var banner = createElement('div', 'storage-location-banner');
        banner.hidden = true;
        state.banner = banner;
        shell.appendChild(banner);

        var grid = createElement('div', 'storage-location-grid');

        var pathsPanel = createElement('section', 'storage-location-panel');
        pathsPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.pathOverview', '路径总览')));
        var pathList = createElement('div', 'storage-location-path-list');
        pathList.appendChild(buildInfoPathRow(translate('storage.currentPath', '当前路径'), 'currentPath'));
        pathList.appendChild(buildInfoPathRow(translate('storage.recommendedPath', '推荐路径'), 'recommendedPath'));
        pathList.appendChild(buildInfoPathRow(translate('storage.anchorRoot', '固定锚点目录'), 'anchorPath'));
        pathList.appendChild(buildInfoPathRow(translate('storage.cloudsaveRoot', '固定 cloudsave 目录'), 'cloudsavePath'));
        pathsPanel.appendChild(pathList);

        var legacySectionTitle = createElement('h3', 'storage-location-panel-title', translate('storage.legacyDetected', '已检测到的旧数据目录'));
        pathsPanel.appendChild(legacySectionTitle);
        var legacyList = createElement('div', 'storage-location-legacy-list');
        state.legacyList = legacyList;
        pathsPanel.appendChild(legacyList);

        var summaryPanel = createElement('section', 'storage-location-panel');
        summaryPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.decisionSummary', '本阶段提示')));
        var summaryList = createElement('div', 'storage-location-summary-list');

        var adviceItem = createElement('div', 'storage-location-summary-item');
        adviceItem.appendChild(createElement('div', 'storage-location-label', translate('storage.shouldMove', '是否建议切换')));
        var adviceValue = createElement('div', 'storage-location-summary-value');
        state.recommendedAdvice = adviceValue;
        adviceItem.appendChild(adviceValue);
        summaryList.appendChild(adviceItem);

        var restartItem = createElement('div', 'storage-location-summary-item');
        restartItem.appendChild(createElement('div', 'storage-location-label', translate('storage.restartNeeded', '若使用推荐位置是否需要重启')));
        var restartValue = createElement('div', 'storage-location-summary-value');
        state.recommendedRestart = restartValue;
        restartItem.appendChild(restartValue);
        summaryList.appendChild(restartItem);

        var noteItem = createElement('div', 'storage-location-summary-item');
        noteItem.appendChild(createElement('div', 'storage-location-label', translate('storage.stage1Boundary', '当前阶段边界')));
        noteItem.appendChild(
            createElement(
                'div',
                'storage-location-summary-value',
                translate('storage.stage1BoundaryText', '当前仅实现网页主页内的选择与后续预览，不在本阶段执行真实迁移、切根或自动重启。')
            )
        );
        summaryList.appendChild(noteItem);

        var currentBehaviorItem = createElement('div', 'storage-location-summary-item');
        currentBehaviorItem.appendChild(createElement('div', 'storage-location-label', translate('storage.currentBehavior', '若保持当前路径')));
        currentBehaviorItem.appendChild(
            createElement(
                'div',
                'storage-location-summary-value',
                translate('storage.currentBehaviorText', '当前会话会继续运行，不会关闭应用，也不会在当前会话里切换根目录。')
            )
        );
        summaryList.appendChild(currentBehaviorItem);

        summaryPanel.appendChild(summaryList);
        grid.appendChild(pathsPanel);
        grid.appendChild(summaryPanel);
        shell.appendChild(grid);

        var actions = createElement('div', 'storage-location-actions');

        var currentButton = createElement('button', 'storage-location-btn storage-location-btn--primary', translate('storage.useCurrent', '保持当前路径'));
        currentButton.type = 'button';
        currentButton.addEventListener('click', continueWithCurrentPath);
        actions.appendChild(currentButton);

        var recommendedButton = createElement('button', 'storage-location-btn', translate('storage.useRecommended', '使用推荐位置'));
        recommendedButton.type = 'button';
        recommendedButton.addEventListener('click', useRecommendedPath);
        actions.appendChild(recommendedButton);

        var chooseOtherButton = createElement('button', 'storage-location-btn storage-location-btn--secondary', translate('storage.chooseOther', '选择其他位置'));
        chooseOtherButton.type = 'button';
        chooseOtherButton.addEventListener('click', openOtherPanel);
        actions.appendChild(chooseOtherButton);

        shell.appendChild(actions);

        var otherPanel = createElement('section', 'storage-location-other');
        otherPanel.hidden = true;
        state.otherPanel = otherPanel;
        otherPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.otherPanelTitle', '其他位置')));
        otherPanel.appendChild(createElement('p', 'storage-location-note', translate('storage.otherPanelNote', '你可以直接使用已检测到的旧数据目录，或者手动输入其它目标路径。')));

        var legacyChoices = createElement('div', 'storage-location-choice-list');
        state.legacyChoices = legacyChoices;
        otherPanel.appendChild(legacyChoices);

        var customInput = createElement('input', 'storage-location-input');
        customInput.type = 'text';
        customInput.placeholder = translate('storage.customPathPlaceholder', '输入你希望后续迁移到的目标路径');
        customInput.addEventListener('focus', function () {
            state.otherSelection.key = 'custom';
        });
        customInput.addEventListener('input', function () {
            state.otherSelection.key = 'custom';
            state.otherSelection.path = String(customInput.value || '').trim();
            renderLegacyList();
            updateOtherButtonState();
        });
        state.customInput = customInput;
        otherPanel.appendChild(customInput);

        var otherActions = createElement('div', 'storage-location-actions');
        var useOtherButton = createElement('button', 'storage-location-btn', translate('storage.previewOther', '预览该位置的后续切换'));
        useOtherButton.type = 'button';
        useOtherButton.disabled = true;
        useOtherButton.addEventListener('click', useOtherPath);
        state.useOtherButton = useOtherButton;
        otherActions.appendChild(useOtherButton);
        otherPanel.appendChild(otherActions);

        shell.appendChild(otherPanel);

        var previewPanel = createElement('section', 'storage-location-panel');
        previewPanel.hidden = true;
        state.previewPanel = previewPanel;
        previewPanel.appendChild(createElement('h3', 'storage-location-panel-title', translate('storage.previewTitle', '不同路径的后续流程')));
        var previewText = createElement('p', 'storage-location-note');
        state.previewText = previewText;
        previewPanel.appendChild(previewText);

        var previewList = createElement('div', 'storage-location-restart-list');
        var sourceItem = createElement('div', 'storage-location-path-item');
        sourceItem.appendChild(createElement('div', 'storage-location-label', translate('storage.sourceLabel', '当前路径')));
        var previewSource = createElement('div', 'storage-location-restart-path');
        state.previewSource = previewSource;
        sourceItem.appendChild(previewSource);
        previewList.appendChild(sourceItem);

        var targetItem = createElement('div', 'storage-location-path-item');
        targetItem.appendChild(createElement('div', 'storage-location-label', translate('storage.targetLabel', '目标路径')));
        var previewTarget = createElement('div', 'storage-location-restart-path');
        state.previewTarget = previewTarget;
        targetItem.appendChild(previewTarget);
        previewList.appendChild(targetItem);
        previewPanel.appendChild(previewList);
        var previewSteps = createElement('div', 'storage-location-preview-steps');
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepClose', '1. 当前实例会先关闭。')
            )
        );
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepMigrate', '2. 关闭后才会迁移数据。')
            )
        );
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepRestart', '3. 迁移完成后会自动重启。')
            )
        );
        previewSteps.appendChild(
            createElement(
                'div',
                'storage-location-preview-step',
                translate('storage.previewStepRetain', '4. 旧数据默认不会自动删除。')
            )
        );
        previewPanel.appendChild(previewSteps);
        previewPanel.appendChild(
            createElement(
                'p',
                'storage-location-note',
                translate('storage.previewBoundary', '第一阶段先把主页内选择页做正确；真实关闭、迁移和自动重启会在后续阶段接入。')
            )
        );
        shell.appendChild(previewPanel);

        view.appendChild(shell);
        state.selectionView = view;
        return view;
    }

    function buildLoadingView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.badge', '存储位置')));
        var loadingTitle = createElement('h2', 'storage-location-title', translate('storage.loadingTitle', '正在确认存储布局状态'));
        var loadingSubtitle = createElement('p', 'storage-location-subtitle', translate('storage.loadingSubtitle', '主业务界面会在存储状态确认完成后再继续加载。'));
        state.loadingTitle = loadingTitle;
        state.loadingSubtitle = loadingSubtitle;
        hero.appendChild(loadingTitle);
        hero.appendChild(loadingSubtitle);
        shell.appendChild(hero);
        shell.appendChild(createElement('div', 'storage-location-loader'));
        view.appendChild(shell);

        state.loadingView = view;
        return view;
    }

    function buildErrorView() {
        var view = createElement('section', 'storage-location-view');
        view.hidden = true;

        var shell = createElement('div', 'storage-location-shell');
        var hero = createElement('div', 'storage-location-hero');
        hero.appendChild(createElement('span', 'storage-location-badge', translate('storage.errorBadge', '读取失败')));
        hero.appendChild(createElement('h2', 'storage-location-title', translate('storage.errorTitle', '暂时无法读取存储位置引导信息')));
        var errorText = createElement('p', 'storage-location-error-text');
        state.errorText = errorText;
        hero.appendChild(errorText);
        shell.appendChild(hero);

        var actions = createElement('div', 'storage-location-error-actions');
        var retryButton = createElement('button', 'storage-location-btn storage-location-btn--primary', translate('common.retry', '重试'));
        retryButton.type = 'button';
        retryButton.addEventListener('click', function () {
            beginSentinelFlow();
        });
        actions.appendChild(retryButton);
        shell.appendChild(actions);
        view.appendChild(shell);

        state.errorView = view;
        return view;
    }

    function buildModalDom() {
        if (state.overlay) return;

        var overlay = createElement('div', 'storage-location-overlay');
        overlay.id = 'storage-location-overlay';
        overlay.hidden = true;

        var modal = createElement('div', 'storage-location-modal');
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-label', translate('storage.dialogLabel', '存储位置选择'));

        modal.appendChild(buildLoadingView());
        modal.appendChild(buildSelectionView());
        modal.appendChild(buildErrorView());

        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        state.overlay = overlay;
    }

    async function fetchBootstrap() {
        setPhase('loading');
        setLoadingCopy(
            translate('storage.loadingTitle', '正在确认存储布局状态'),
            translate('storage.loadingFetchBootstrapSubtitle', '正在准备存储位置选择页面。')
        );
        try {
            var response = await fetch('/api/storage/location/bootstrap', {
                cache: 'no-store',
                headers: {
                    'Accept': 'application/json'
                }
            });
            if (!response.ok) {
                throw new Error('bootstrap request failed: ' + response.status);
            }

            state.bootstrap = await response.json();
            updateSelectionSummary();
            if (shouldShowSelectionView(state.bootstrap)) {
                setPhase('selection_required');
                return;
            }

            hideOverlay();
            resolveStartupDecision({
                canContinue: true,
                reason: 'status_ready',
            });
        } catch (error) {
            console.warn('[storage-location] bootstrap failed', error);
            showError(error);
        }
    }

    async function beginSentinelFlow() {
        buildModalDom();
        setPhase('loading');
        setLoadingCopy(
            translate('storage.loadingTitle', '正在确认存储布局状态'),
            translate('storage.loadingSubtitle', '主业务界面会在存储状态确认完成后再继续加载。')
        );

        try {
            var statusPayload = await waitForSystemStatus();
            if (!shouldBlockMainUi(statusPayload)) {
                hideOverlay();
                resolveStartupDecision({
                    canContinue: true,
                    reason: 'status_ready',
                });
                return;
            }

            await fetchBootstrap();
        } catch (error) {
            console.warn('[storage-location] sentinel init failed', error);
            showError(error);
        }
    }

    async function init() {
        if (state.initPromise) return state.initPromise;
        state.initialized = true;
        state.initPromise = state.startupDecision.promise;
        beginSentinelFlow();
        return state.initPromise;
    }

    function scheduleEarlyInit() {
        function start() {
            init();
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
            return;
        }

        window.setTimeout(start, 0);
    }

    window.appStorageLocation = {
        init: init,
        waitUntilMainUiAllowed: function () {
            return init();
        },
    };

    window.waitForStorageLocationStartupBarrier = function waitForStorageLocationStartupBarrier() {
        return init();
    };

    window.__nekoStorageLocationStartupBarrier = init();
    scheduleEarlyInit();
})();

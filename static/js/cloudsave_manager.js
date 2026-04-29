(function () {
    'use strict';

    const state = {
        summary: null,
        preferredCharacterName: '',
        expandedCharacterNames: new Set(),
    };
    const inflightActions = new Set();

    const CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY = 'neko_cloudsave_character_sync';
    const CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE = 'cloudsave_character_changed';
    const CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME = 'neko_cloudsave_character_sync';

    let detailPanelIdSequence = 0;
    let cloudsaveSyncChannel = null;

    const WARNING_MESSAGES = {
        local_resource_missing_on_this_device: {
            key: 'cloudsave.warning.local_resource_missing_on_this_device',
            fallback: 'Local resources are already missing on this device. Restore them using the source guidance above.',
        },
        cloud_resource_may_be_missing_after_download: {
            key: 'cloudsave.warning.cloud_resource_may_be_missing_after_download',
            fallback: 'Resources may still be missing after download. Restore the model manually based on the recorded source if needed.',
        },
    };

    const MODEL_TYPE_MESSAGES = {
        live2d: {
            key: 'cloudsave.modelType.live2d',
            fallback: 'Live2D',
        },
        vrm: {
            key: 'cloudsave.modelType.vrm',
            fallback: 'VRM',
        },
        live3d: {
            key: 'cloudsave.modelType.live3d',
            fallback: 'VRM',
        },
        mmd: {
            key: 'cloudsave.modelType.mmd',
            fallback: 'MMD',
        },
    };

    const RELATION_STATE_MESSAGES = {
        local_only: {
            key: 'cloudsave.relationState.local_only',
            fallback: 'Local only',
        },
        cloud_only: {
            key: 'cloudsave.relationState.cloud_only',
            fallback: 'Cloud only',
        },
        matched: {
            key: 'cloudsave.relationState.matched',
            fallback: 'Local and cloud match',
        },
        diverged: {
            key: 'cloudsave.relationState.diverged',
            fallback: 'Local and cloud differ',
        },
    };

    const ASSET_STATE_MESSAGES = {
        ready: {
            key: 'cloudsave.assetState.ready',
            fallback: 'Ready to use',
        },
        import_required: {
            key: 'cloudsave.assetState.import_required',
            fallback: 'Manual asset import required',
        },
        downloadable: {
            key: 'cloudsave.assetState.downloadable',
            fallback: 'Assets are missing. Check Workshop recovery status.',
        },
        missing: {
            key: 'cloudsave.assetState.missing',
            fallback: 'Only settings were restored. Assets are missing.',
        },
    };

    const WORKSHOP_STATUS_MESSAGES = {
        installed_and_subscribed: {
            key: 'cloudsave.workshopStatus.installed_and_subscribed',
            fallback: 'Installed and still subscribed',
        },
        installed_but_unsubscribed: {
            key: 'cloudsave.workshopStatus.installed_but_unsubscribed',
            fallback: 'Cached locally, but unsubscribed',
        },
        subscribed_not_installed: {
            key: 'cloudsave.workshopStatus.subscribed_not_installed',
            fallback: 'Subscribed, waiting for download',
        },
        available_needs_resubscribe: {
            key: 'cloudsave.workshopStatus.available_needs_resubscribe',
            fallback: 'Still available, but resubscription is required',
        },
        unavailable: {
            key: 'cloudsave.workshopStatus.unavailable',
            fallback: 'Item is no longer available',
        },
        steam_unavailable: {
            key: 'cloudsave.workshopStatus.steam_unavailable',
            fallback: 'Steam is unavailable, status not confirmed',
        },
        unknown: {
            key: 'cloudsave.workshopStatus.unknown',
            fallback: 'Status not confirmed',
        },
    };

    const ERROR_MESSAGES = {
        CLOUDSAVE_PROVIDER_UNAVAILABLE: {
            key: 'cloudsave.error.providerUnavailable',
            fallback: 'Cloud save provider is currently unavailable.',
        },
        ACTIVE_SESSION_BLOCKED: {
            key: 'cloudsave.error.activeSessionBlocked',
            fallback: 'This character has an active session. Stop the session before downloading.',
        },
        SESSION_TERMINATE_FAILED: {
            key: 'cloudsave.error.sessionTerminateFailed',
            fallback: 'Failed to terminate active session. Please try again later.',
        },
        MEMORY_SERVER_RELEASE_FAILED: {
            key: 'cloudsave.error.memoryServerReleaseFailed',
            fallback: 'Failed to release the local memory handle before overwrite. Please try again later.',
        },
        LOCAL_CHARACTER_NOT_FOUND: {
            key: 'cloudsave.error.localCharacterNotFound',
            fallback: 'The local character could not be found.',
        },
        CLOUD_CHARACTER_NOT_FOUND: {
            key: 'cloudsave.error.cloudCharacterNotFound',
            fallback: 'The cloud character could not be found.',
        },
        CLOUDSAVE_CHARACTER_NOT_FOUND: {
            key: 'cloudsave.error.cloudCharacterNotFound',
            fallback: 'The cloud character could not be found.',
        },
        LOCAL_CHARACTER_EXISTS: {
            key: 'cloudsave.error.localCharacterExists',
            fallback: 'A local character with the same name already exists.',
        },
        CLOUD_CHARACTER_EXISTS: {
            key: 'cloudsave.error.cloudCharacterExists',
            fallback: 'A cloud character with the same name already exists.',
        },
        CLOUDSAVE_WRITE_FENCE_ACTIVE: {
            key: 'cloudsave.error.writeFenceActive',
            fallback: 'Cloud save maintenance mode is active. Please try again later.',
        },
        NAME_AUDIT_FAILED: {
            key: 'cloudsave.error.nameAuditFailed',
            fallback: 'The character name did not pass the cloud save validation rules.',
        },
        CLOUDSAVE_UPLOAD_FAILED: {
            key: 'cloudsave.error.uploadFailed',
            fallback: 'Upload failed: {{message}}',
        },
        CLOUDSAVE_DOWNLOAD_FAILED: {
            key: 'cloudsave.error.downloadFailed',
            fallback: 'Download failed: {{message}}',
        },
        LOCAL_RELOAD_FAILED_ROLLED_BACK: {
            key: 'cloudsave.error.localReloadFailedRolledBack',
            fallback: 'The download was applied, but local reload failed.',
        },
        INVALID_JSON_BODY: {
            key: 'cloudsave.error.invalidJsonBody',
            fallback: 'Invalid JSON request body.',
        },
        INVALID_PARAMETER: {
            key: 'cloudsave.error.invalidBooleanParameter',
            fallback: 'Invalid parameter: {{parameter}} must be boolean.',
        },
    };

    function getPreferredCharacterName() {
        const params = new URLSearchParams(window.location.search);
        const lanlanNameFromQuery = params.get('lanlan_name');
        if (lanlanNameFromQuery) {
            return lanlanNameFromQuery;
        }

        const hiddenInput = document.getElementById('lanlan_name');
        if (hiddenInput && hiddenInput.value) {
            return hiddenInput.value;
        }
        return '';
    }

    function interpolateText(template, params = {}) {
        return String(template || '').replace(/\{\{\s*([^}]+?)\s*\}\}/g, (match, token) => {
            const key = String(token || '').trim();
            return Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : '';
        });
    }

    function translate(key, fallback, params = {}) {
        if (!key) {
            return interpolateText(fallback, params);
        }
        if (typeof window.t === 'function') {
            const translated = window.t(key, params);
            if (translated && translated !== key) {
                return translated;
            }
        }
        return interpolateText(fallback, params);
    }

    function translateCode(entry, params = {}, fallbackValue = '') {
        if (!entry || !entry.key) {
            return fallbackValue;
        }
        return translate(entry.key, entry.fallback, params);
    }

    function isI18nReady() {
        return !!(window.i18n && window.i18n.isInitialized && typeof window.t === 'function');
    }

    function setTranslatedText(element, key, fallback, params = {}) {
        if (!element) {
            return;
        }

        if (key) {
            element.setAttribute('data-i18n', key);
            if (params && Object.keys(params).length > 0) {
                element.setAttribute('data-i18n-params', JSON.stringify(params));
            } else {
                element.removeAttribute('data-i18n-params');
            }
        } else {
            element.removeAttribute('data-i18n');
            element.removeAttribute('data-i18n-params');
        }

        element.textContent = translate(key, fallback, params);
    }

    function waitForI18nReady(timeoutMs = 2500) {
        if (isI18nReady()) {
            return Promise.resolve();
        }

        return new Promise(resolve => {
            let finished = false;

            const finish = () => {
                if (finished) {
                    return;
                }
                finished = true;
                window.removeEventListener('localechange', onLocaleChange);
                resolve();
            };

            const onLocaleChange = () => {
                if (isI18nReady()) {
                    finish();
                }
            };

            const startTime = Date.now();
            const poll = () => {
                if (isI18nReady() || Date.now() - startTime >= timeoutMs) {
                    finish();
                    return;
                }
                window.setTimeout(poll, 50);
            };

            window.addEventListener('localechange', onLocaleChange);
            poll();
        });
    }

    function padDatePart(value) {
        return String(value).padStart(2, '0');
    }

    function getPreferredLocale() {
        const currentUiLanguage = getCurrentUiLanguage();
        if (currentUiLanguage) {
            return currentUiLanguage;
        }
        if (typeof navigator !== 'undefined' && typeof navigator.language === 'string' && navigator.language.trim()) {
            return navigator.language.trim();
        }
        return 'en-US';
    }

    function formatUtcTimestamp(utcValue) {
        const normalizedValue = String(utcValue || '').trim();
        if (!normalizedValue) {
            return '';
        }

        const date = new Date(normalizedValue);
        if (Number.isNaN(date.getTime())) {
            return normalizedValue;
        }

        try {
            return new Intl.DateTimeFormat(getPreferredLocale(), {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            }).format(date);
        } catch (error) {
            console.warn('Failed to localize UTC timestamp:', error);
            return [
                date.getFullYear(),
                padDatePart(date.getMonth() + 1),
                padDatePart(date.getDate()),
            ].join('-') + ' ' + [
                padDatePart(date.getHours()),
                padDatePart(date.getMinutes()),
                padDatePart(date.getSeconds()),
            ].join(':');
        }
    }

    function isProviderAvailable(summary = state.summary) {
        return !summary || summary.provider_available !== false;
    }

    function getCurrentSyncBackend(summary = state.summary) {
        return String((summary && summary.sync_backend) || '').trim();
    }

    function getSteamAutoCloudStatus(summary = state.summary) {
        if (!summary || typeof summary !== 'object') {
            return {};
        }
        return summary.steam_autocloud && typeof summary.steam_autocloud === 'object'
            ? summary.steam_autocloud
            : {};
    }

    function isSteamAutoCloudBackend(summary = state.summary) {
        return getCurrentSyncBackend(summary) === 'steam_auto_cloud';
    }

    function isSteamAutoCloudSessionReady(summary = state.summary) {
        return !!getSteamAutoCloudStatus(summary).steam_session_ready;
    }

    function isSourceLaunchSession(summary = state.summary) {
        return !!getSteamAutoCloudStatus(summary).source_launch;
    }

    function getCloudsaveSyncChannel() {
        if (typeof BroadcastChannel !== 'function') {
            return null;
        }
        if (!cloudsaveSyncChannel) {
            cloudsaveSyncChannel = new BroadcastChannel(CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME);
        }
        return cloudsaveSyncChannel;
    }

    function notifyCharacterManagerSync(detail = {}) {
        const payload = {
            type: CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE,
            source: 'cloudsave_manager',
            action: detail.action || '',
            character_name: detail.character_name || '',
            timestamp: Date.now(),
        };

        try {
            localStorage.setItem(CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY, JSON.stringify(payload));
        } catch (error) {
            console.warn('Failed to persist cloud save sync signal:', error);
        }

        try {
            if (window.opener && !window.opener.closed) {
                window.opener.postMessage(payload, window.location.origin);
            }
        } catch (error) {
            console.warn('Failed to notify opener about cloud save sync:', error);
        }

        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage(payload, window.location.origin);
            }
        } catch (error) {
            console.warn('Failed to notify parent about cloud save sync:', error);
        }

        try {
            const channel = getCloudsaveSyncChannel();
            if (channel) {
                channel.postMessage(payload);
            }
        } catch (error) {
            console.warn('Failed to broadcast cloud save sync:', error);
        }
    }

    function summarizeWarning(code) {
        return translateCode(WARNING_MESSAGES[code], {}, code);
    }

    function summarizeModelType(code) {
        return translateCode(
            MODEL_TYPE_MESSAGES[code],
            {},
            translate('cloudsave.modelType.unknown', 'Unknown model'),
        );
    }

    function summarizeAssetSource(code) {
        if (!code) {
            return translate('cloudsave.assetSource.notRecorded', 'Not recorded');
        }
        if (code === 'steam_workshop') {
            return translate('cloudsave.assetSource.steamWorkshop', 'Steam Workshop');
        }
        if (code === 'local_imported') {
            return translate('cloudsave.assetSource.localImported', 'Locally imported model');
        }
        if (code === 'manual_external') {
            return translate('cloudsave.assetSource.manualExternal', 'External model reference');
        }
        if (code === 'builtin') {
            return translate('cloudsave.assetSource.builtin', 'Built-in asset');
        }
        return code;
    }

    function summarizeRelationState(code) {
        return translateCode(
            RELATION_STATE_MESSAGES[code],
            {},
            translate('cloudsave.relationState.unknown', 'Unknown state'),
        );
    }

    function summarizeAssetState(code) {
        return translateCode(
            ASSET_STATE_MESSAGES[code],
            {},
            translate('cloudsave.assetState.notRecorded', 'Not recorded'),
        );
    }

    function isManualImportSource(code) {
        return code === 'local_imported' || code === 'manual_external';
    }

    function isWorkshopSource(code) {
        return code === 'steam_workshop';
    }

    function hasWorkshopOriginOverride(item, scope) {
        return isWorkshopSource(item[`${scope}_origin_source`]) && !isWorkshopSource(item[`${scope}_asset_source`]);
    }

    function shouldShowLocalManualSourceGuidance(item) {
        return item.local_exists
            && isManualImportSource(item.local_asset_source)
            && !hasWorkshopOriginOverride(item, 'local');
    }

    function shouldShowCloudManualSourceGuidance(item) {
        return !item.local_exists
            && item.cloud_exists
            && isManualImportSource(item.cloud_asset_source)
            && !hasWorkshopOriginOverride(item, 'cloud');
    }

    function shouldShowLocalModifiedWorkshopModelGuidance(item) {
        return item.local_exists
            && isManualImportSource(item.local_asset_source)
            && hasWorkshopOriginOverride(item, 'local');
    }

    function summarizeWorkshopStatus(code) {
        return translateCode(
            WORKSHOP_STATUS_MESSAGES[code],
            {},
            translate('cloudsave.workshopStatus.notConfirmed', 'Not confirmed'),
        );
    }

    function formatWorkshopStatus(item, scope) {
        const status = item[`${scope}_workshop_status`] || '';
        const label = summarizeWorkshopStatus(status);
        return label;
    }

    function translateErrorPayload(payload, fallbackMessage) {
        const params = {
            name: (payload && payload.character_name) || '',
            message: (payload && (payload.message || payload.error)) || fallbackMessage || '',
            ...(
                payload && payload.message_params && typeof payload.message_params === 'object'
                    ? payload.message_params
                    : {}
            ),
        };

        if (payload && payload.message_key) {
            return translate(payload.message_key, fallbackMessage || payload.message || payload.error || '', params);
        }

        const code = payload && payload.code;
        if (!code || !ERROR_MESSAGES[code]) {
            return fallbackMessage || '';
        }

        return translate(ERROR_MESSAGES[code].key, ERROR_MESSAGES[code].fallback, params);
    }

    function pushGuidanceItem(items, nextItem) {
        if (!nextItem || !nextItem.key) return;
        if (items.some(item => item.key === nextItem.key)) return;
        items.push(nextItem);
    }

    function buildBadgeItems(item) {
        const badges = [];
        if (item.model_type) {
            badges.push({ text: summarizeModelType(item.model_type) });
        }
        if (item.relation_state) {
            badges.push({ text: summarizeRelationState(item.relation_state) });
        }
        if (item.local_exists) {
            badges.push({
                text: translate(
                    'cloudsave.badge.local',
                    'Local: {{value}}',
                    { value: summarizeAssetSource(item.local_asset_source) },
                ),
            });
        }
        if (
            item.cloud_exists
            && (
                !item.local_exists
                || item.cloud_asset_source !== item.local_asset_source
                || item.cloud_asset_source_id !== item.local_asset_source_id
            )
        ) {
            badges.push({
                text: translate(
                    'cloudsave.badge.cloud',
                    'Cloud: {{value}}',
                    { value: summarizeAssetSource(item.cloud_asset_source) },
                ),
            });
        }
        return badges;
    }

    function buildWorkshopGuidanceForScope(item, scope) {
        const guidanceItems = [];
        const assetSource = item[`${scope}_asset_source`] || '';
        const assetState = item[`${scope}_asset_state`] || '';
        const workshopStatus = item[`${scope}_workshop_status`] || '';

        if (!isWorkshopSource(assetSource)) {
            return guidanceItems;
        }

        const isLocal = scope === 'local';
        const readyPrefix = isLocal
            ? translate('cloudsave.scopePrefix.localReady', 'Local resources are still usable')
            : translate('cloudsave.scopePrefix.cloudReady', 'Matching Workshop resources are already on this device');
        const missingPrefix = isLocal
            ? translate('cloudsave.scopePrefix.localMissing', 'Local resources are missing')
            : translate('cloudsave.scopePrefix.cloudAfterDownload', 'After cloud download');

        if (assetState === 'ready') {
            if (workshopStatus === 'installed_but_unsubscribed' || workshopStatus === 'available_needs_resubscribe') {
                pushGuidanceItem(guidanceItems, {
                    key: `${scope}-workshop-ready-resubscribe`,
                    tone: 'caution',
                    title: translate(
                        'cloudsave.guidance.workshopReadyResubscribe.title',
                        '{{prefix}}, but the current subscription is no longer active',
                        { prefix: readyPrefix },
                    ),
                    body: isLocal
                        ? translate(
                            'cloudsave.guidance.workshopReadyResubscribe.bodyLocal',
                            'This device can still use the current local Workshop files. If the local cache is lost in the future, you will usually need to resubscribe before the model can be restored again.',
                        )
                        : translate(
                            'cloudsave.guidance.workshopReadyResubscribe.bodyCloud',
                            'This device already has the matching Workshop resources. If the local cache is lost in the future, you will usually need to resubscribe before the model can be restored again.',
                        ),
                });
            } else if (workshopStatus === 'unavailable') {
                pushGuidanceItem(guidanceItems, {
                    key: `${scope}-workshop-ready-unavailable`,
                    tone: 'strong',
                    title: translate(
                        'cloudsave.guidance.workshopReadyUnavailable.title',
                        '{{prefix}}, but the original Workshop item may have been removed',
                        { prefix: readyPrefix },
                    ),
                    body: translate(
                        'cloudsave.guidance.workshopReadyUnavailable.body',
                        'The resource is still present on this device, so the character can keep working now. If the cache is lost later, Workshop recovery will usually no longer be possible and you will need your own backup or a manual import.',
                    ),
                });
            }
            return guidanceItems;
        }

        if (assetState !== 'downloadable') {
            return guidanceItems;
        }

        if (workshopStatus === 'installed_and_subscribed') {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-installed`,
                tone: 'info',
                title: translate(
                    'cloudsave.guidance.workshopInstalled.title',
                    '{{prefix}}: valid Workshop resources are still present on this device',
                    { prefix: missingPrefix },
                ),
                body: isLocal
                    ? translate(
                        'cloudsave.guidance.workshopInstalled.bodyLocal',
                        'The Workshop item is still installed and subscribed on this device, but the bound model file was not found. The folder layout may have changed, the update may be incomplete, or the model may need to be resynced.',
                    )
                    : translate(
                        'cloudsave.guidance.workshopInstalled.bodyCloud',
                        'The Workshop item is still installed and subscribed on this device. After downloading the character data, the existing recovery path can usually restore the model. If it still fails, the model may need to be resynced.',
                    ),
            });
        } else if (workshopStatus === 'installed_but_unsubscribed') {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-cache-only`,
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.workshopCacheOnly.title',
                    '{{prefix}}: only an old cache may remain, and the item is unsubscribed',
                    { prefix: missingPrefix },
                ),
                body: isLocal
                    ? translate(
                        'cloudsave.guidance.workshopCacheOnly.bodyLocal',
                        'The device is no longer subscribed, and the current bound file is missing. If the old cache is also unusable, you will usually need to resubscribe or import the model manually.',
                    )
                    : translate(
                        'cloudsave.guidance.workshopCacheOnly.bodyCloud',
                        'The device is no longer subscribed. After downloading the character data, you will usually need to resubscribe to the Workshop item or import the model manually if the old cache cannot restore it.',
                    ),
            });
        } else if (workshopStatus === 'subscribed_not_installed') {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-subscribed`,
                tone: 'info',
                title: translate(
                    'cloudsave.guidance.workshopSubscribed.title',
                    '{{prefix}}: this device is still subscribed to the Workshop item',
                    { prefix: missingPrefix },
                ),
                body: isLocal
                    ? translate(
                        'cloudsave.guidance.workshopSubscribed.bodyLocal',
                        'This device is still subscribed to the Workshop item. Once Steam finishes downloading it, the model can usually recover. If it does not, the item structure may have changed or the model may need to be resynced.',
                    )
                    : translate(
                        'cloudsave.guidance.workshopSubscribed.bodyCloud',
                        'This device is still subscribed to the Workshop item. After downloading the character data, the model can usually recover once Steam finishes downloading the resource.',
                    ),
            });
        } else if (workshopStatus === 'available_needs_resubscribe') {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-resubscribe`,
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.workshopResubscribe.title',
                    '{{prefix}}: the Workshop item must be resubscribed',
                    { prefix: missingPrefix },
                ),
                body: isLocal
                    ? translate(
                        'cloudsave.guidance.workshopResubscribe.bodyLocal',
                        'This character originally used Workshop resources, but the current device is no longer subscribed. If the item is still available, resubscribe first and then try to restore the model again.',
                    )
                    : translate(
                        'cloudsave.guidance.workshopResubscribe.bodyCloud',
                        'This cloud character originally used Workshop resources, but the current device is not subscribed. After downloading the character data, resubscribe first before trying to restore the model.',
                    ),
            });
        } else if (workshopStatus === 'unavailable') {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-unavailable`,
                tone: 'strong',
                title: translate(
                    'cloudsave.guidance.workshopUnavailable.title',
                    '{{prefix}}: the Workshop item is no longer accessible',
                    { prefix: missingPrefix },
                ),
                body: isLocal
                    ? translate(
                        'cloudsave.guidance.workshopUnavailable.bodyLocal',
                        'This character originally used Workshop resources, but the item has been removed or is no longer accessible. Automatic Workshop recovery is no longer available on this device. You will need a manual backup or a new import.',
                    )
                    : translate(
                        'cloudsave.guidance.workshopUnavailable.bodyCloud',
                        'This cloud character originally used Workshop resources, but the item has been removed or is no longer accessible. After download, Workshop will usually no longer be able to restore the model automatically.',
                    ),
            });
        } else if (workshopStatus === 'steam_unavailable') {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-steam-unavailable`,
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.workshopSteamUnavailable.title',
                    '{{prefix}}: Workshop recovery cannot be confirmed right now',
                    { prefix: missingPrefix },
                ),
                body: translate(
                    'cloudsave.guidance.workshopSteamUnavailable.body',
                    'Steam is unavailable, not logged in, or Steamworks is not initialized. Restore Steam access first so you can confirm whether the item can still be downloaded.',
                ),
            });
        } else {
            pushGuidanceItem(guidanceItems, {
                key: `${scope}-workshop-unknown`,
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.workshopUnknown.title',
                    '{{prefix}}: Workshop recovery status is not confirmed',
                    { prefix: missingPrefix },
                ),
                body: translate(
                    'cloudsave.guidance.workshopUnknown.body',
                    'The current status is still unknown. Check the Workshop page to confirm the subscription state and item availability.',
                ),
            });
        }

        return guidanceItems;
    }

    function buildWorkshopOriginGuidanceForScope(item, scope) {
        const guidanceItems = [];
        if (!hasWorkshopOriginOverride(item, scope)) {
            return guidanceItems;
        }

        const isLocal = scope === 'local';
        pushGuidanceItem(guidanceItems, {
            key: `${scope}-workshop-origin`,
            tone: 'strong',
            title: isLocal
                ? translate(
                    'cloudsave.guidance.localWorkshopOrigin.title',
                    'This character originally came from Steam Workshop',
                )
                : translate(
                    'cloudsave.guidance.cloudWorkshopOrigin.title',
                    'This cloud character originally came from Steam Workshop',
                ),
            body: isLocal
                ? translate(
                    'cloudsave.guidance.localWorkshopOrigin.body',
                    'A Workshop role source still matches this character, but the current bound model is stored as a local or manual asset on this device. Cloud save syncs the role data only and will not upload the current model files. If another device needs the current look, import the model manually. If you want Workshop auto-recovery again, switch back to the Workshop item shown above.',
                )
                : translate(
                    'cloudsave.guidance.cloudWorkshopOrigin.body',
                    'This cloud character was uploaded from a Workshop role, but the recorded bound model is now a local or manual asset instead of a Workshop path. Downloading it restores the role data only. If another device needs the current look, prepare the model files manually and also check the Workshop origin status shown above.',
                ),
        });
        return guidanceItems;
    }

    function buildWorkshopHintByStatus(workshopStatus, hintMap) {
        if (workshopStatus === 'installed_but_unsubscribed' || workshopStatus === 'available_needs_resubscribe') {
            return translate(
                hintMap.resubscribe.key,
                hintMap.resubscribe.fallback,
            );
        }
        if (workshopStatus === 'unavailable') {
            return translate(
                hintMap.unavailable.key,
                hintMap.unavailable.fallback,
            );
        }
        if (workshopStatus === 'steam_unavailable' || workshopStatus === 'unknown') {
            return translate(
                hintMap.unconfirmed.key,
                hintMap.unconfirmed.fallback,
            );
        }
        return '';
    }

    function buildWorkshopUploadHint(item) {
        if (isWorkshopSource(item.local_asset_source)) {
            return buildWorkshopHintByStatus(item.local_workshop_status || '', {
                resubscribe: {
                    key: 'cloudsave.hint.uploadResubscribe',
                    fallback: '\nThis local character still uses Workshop resources, but the current device is no longer in an active subscription state. After downloading on another device, you may need to resubscribe before the model can be restored.',
                },
                unavailable: {
                    key: 'cloudsave.hint.uploadUnavailable',
                    fallback: '\nThis local character still uses Workshop resources, but the original item is no longer accessible. After downloading on another device, Workshop recovery will usually no longer be possible.',
                },
                unconfirmed: {
                    key: 'cloudsave.hint.uploadUnconfirmed',
                    fallback: '\nThis local character comes from Workshop resources, but it is not currently possible to confirm whether another device will still be able to restore the model through Workshop.',
                },
            });
        }

        if (!hasWorkshopOriginOverride(item, 'local')) {
            return '';
        }

        return buildWorkshopHintByStatus(item.local_origin_workshop_status || '', {
            resubscribe: {
                key: 'cloudsave.hint.uploadOriginResubscribe',
                fallback: '\nThis character originally came from Workshop, but the current bound model is now local or manual. Other devices will still need the current model files, and resubscribing may also be required later if you want to restore the original Workshop source.',
            },
            unavailable: {
                key: 'cloudsave.hint.uploadOriginUnavailable',
                fallback: '\nThis character originally came from Workshop, but the original item is no longer accessible and the current bound model is now local or manual. Other devices will still need the current model files, and Workshop usually can no longer restore the original source automatically.',
            },
            unconfirmed: {
                key: 'cloudsave.hint.uploadOriginUnconfirmed',
                fallback: '\nThis character originally came from Workshop, but the current bound model is now local or manual. Other devices will still need the current model files, and it is not currently possible to confirm whether the original Workshop item can still be restored.',
            },
        });
    }

    function buildWorkshopDownloadHint(item) {
        if (isWorkshopSource(item.cloud_asset_source)) {
            return buildWorkshopHintByStatus(item.cloud_workshop_status || '', {
                resubscribe: {
                    key: 'cloudsave.hint.downloadResubscribe',
                    fallback: '\nThis cloud character originally used Workshop resources, but the current device is not in an active subscription state. After download, you will usually need to resubscribe before the model can be restored.',
                },
                unavailable: {
                    key: 'cloudsave.hint.downloadUnavailable',
                    fallback: '\nThis cloud character originally used Workshop resources, but the item is no longer accessible. After download, automatic Workshop recovery will usually no longer be possible.',
                },
                unconfirmed: {
                    key: 'cloudsave.hint.downloadUnconfirmed',
                    fallback: '\nThis cloud character originally used Workshop resources, but it is not currently possible to confirm whether the item can still be restored.',
                },
            });
        }

        if (!hasWorkshopOriginOverride(item, 'cloud')) {
            return '';
        }

        return buildWorkshopHintByStatus(item.cloud_origin_workshop_status || '', {
            resubscribe: {
                key: 'cloudsave.hint.downloadOriginResubscribe',
                fallback: '\nThis cloud character originally came from Workshop, but the recorded bound model is now local or manual. After download, you will still need the current model files, and resubscribing may also be required later if you want to restore the original Workshop source.',
            },
            unavailable: {
                key: 'cloudsave.hint.downloadOriginUnavailable',
                fallback: '\nThis cloud character originally came from Workshop, but the original item is no longer accessible and the recorded bound model is now local or manual. After download, you will still need the current model files, and Workshop usually can no longer restore the original source automatically.',
            },
            unconfirmed: {
                key: 'cloudsave.hint.downloadOriginUnconfirmed',
                fallback: '\nThis cloud character originally came from Workshop, but the recorded bound model is now local or manual. After download, you will still need the current model files, and it is not currently possible to confirm whether the original Workshop item can still be restored.',
            },
        });
    }

    function buildResourceGuidance(item) {
        const guidanceItems = [];

        buildWorkshopGuidanceForScope(item, 'local').forEach(guidanceItem => pushGuidanceItem(guidanceItems, guidanceItem));
        buildWorkshopGuidanceForScope(item, 'cloud').forEach(guidanceItem => pushGuidanceItem(guidanceItems, guidanceItem));
        buildWorkshopOriginGuidanceForScope(item, 'local').forEach(guidanceItem => pushGuidanceItem(guidanceItems, guidanceItem));
        buildWorkshopOriginGuidanceForScope(item, 'cloud').forEach(guidanceItem => pushGuidanceItem(guidanceItems, guidanceItem));

        if (item.local_exists && item.local_asset_state === 'import_required') {
            pushGuidanceItem(guidanceItems, {
                key: 'local-manual-import',
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.localManualImport.title',
                    'Local resources: manual model import required',
                ),
                body: translate(
                    'cloudsave.guidance.localManualImport.body',
                    'This device is missing the locally imported or external model used by this character. Import or move the model files manually.',
                ),
            });
        }
        if (item.cloud_exists && item.cloud_asset_state === 'import_required') {
            pushGuidanceItem(guidanceItems, {
                key: 'cloud-manual-import',
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.cloudManualImport.title',
                    'After cloud download: manual model import required',
                ),
                body: translate(
                    'cloudsave.guidance.cloudManualImport.body',
                    'This cloud character depends on a locally imported or external model. After downloading it to this device, import or move the matching model files manually.',
                ),
            });
        }

        if (shouldShowLocalManualSourceGuidance(item)) {
            pushGuidanceItem(guidanceItems, {
                key: 'local-manual-source',
                tone: 'caution',
                title: translate(
                    'cloudsave.guidance.localManualSource.title',
                    'This character currently uses a local or manual model',
                ),
                body: translate(
                    'cloudsave.guidance.localManualSource.body',
                    'Cloud save only syncs the character data. The current local or external model files are not included and must still be prepared manually on other devices.',
                ),
            });
        }

        if (shouldShowLocalModifiedWorkshopModelGuidance(item)) {
            pushGuidanceItem(guidanceItems, {
                key: 'local-modified-model',
                tone: 'strong',
                title: translate(
                    'cloudsave.guidance.localModifiedModel.title',
                    'Pay attention if you changed this character model manually',
                ),
                body: translate(
                    'cloudsave.guidance.localModifiedModel.body',
                    'If you replaced a model that used to be recoverable from Workshop, it now behaves like a manually imported model. After upload, other devices will still need the model files to be restored manually.',
                ),
            });
        } else if (shouldShowCloudManualSourceGuidance(item)) {
            pushGuidanceItem(guidanceItems, {
                key: 'cloud-manual-source',
                tone: 'strong',
                title: translate(
                    'cloudsave.guidance.cloudManualSource.title',
                    'This cloud character currently records a local or manual model',
                ),
                body: translate(
                    'cloudsave.guidance.cloudManualSource.body',
                    'Downloading this cloud character restores the character data only. The recorded local or external model files are still not included and must be prepared manually on this device.',
                ),
            });
        }

        if (
            item.local_exists
            && item.cloud_exists
            && isManualImportSource(item.local_asset_source)
            && item.cloud_asset_source === 'steam_workshop'
        ) {
            pushGuidanceItem(guidanceItems, {
                key: 'source-diverged',
                tone: 'strong',
                title: translate(
                    'cloudsave.guidance.sourceDiverged.title',
                    'The local model source is now different from the cloud record',
                ),
                body: translate(
                    'cloudsave.guidance.sourceDiverged.body',
                    'The local character currently uses a manually imported or external model, while the cloud record still points to Steam Workshop. If you upload again, the cloud copy will also only sync character data and not the model files.',
                ),
            });
        }

        return guidanceItems;
    }

    function appendMetaCards(container, entries) {
        entries.forEach(([label, value]) => {
            const card = document.createElement('div');
            card.className = 'cloudsave-meta';
            const labelEl = document.createElement('span');
            labelEl.className = 'cloudsave-meta-label';
            labelEl.textContent = label;
            const valueEl = document.createElement('span');
            valueEl.className = 'cloudsave-meta-value';
            valueEl.textContent = value;
            card.appendChild(labelEl);
            card.appendChild(valueEl);
            container.appendChild(card);
        });
    }

    function buildMetaSection(sectionClassName, titleText, entries) {
        const section = document.createElement('section');
        section.className = `cloudsave-meta-section ${sectionClassName}`;

        const title = document.createElement('div');
        title.className = 'cloudsave-meta-section-title';
        title.textContent = titleText;
        section.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'cloudsave-meta-grid';
        appendMetaCards(grid, entries);
        section.appendChild(grid);

        return section;
    }

    function getItemLocalUpdatedAtSortValue(item) {
        const normalizedValue = String(item?.local_updated_at_utc || '').trim();
        if (!normalizedValue) {
            return -1;
        }

        const timestamp = new Date(normalizedValue).getTime();
        return Number.isNaN(timestamp) ? -1 : timestamp;
    }

    function getLocallyUpdatedItems(items) {
        const list = Array.isArray(items) ? [...items] : [];
        list.sort((left, right) => {
            const leftTime = getItemLocalUpdatedAtSortValue(left);
            const rightTime = getItemLocalUpdatedAtSortValue(right);
            if (leftTime !== rightTime) {
                return rightTime - leftTime;
            }

            const leftRank = left.character_name === state.preferredCharacterName ? 0 : 1;
            const rightRank = right.character_name === state.preferredCharacterName ? 0 : 1;
            if (leftRank !== rightRank) {
                return leftRank - rightRank;
            }

            return String(left.character_name || '').localeCompare(String(right.character_name || ''));
        });
        return list;
    }

    function isCharacterExpanded(characterName) {
        return Boolean(characterName) && state.expandedCharacterNames.has(characterName);
    }

    function setCharacterExpanded(characterName, expanded) {
        if (!characterName) {
            return;
        }
        if (expanded) {
            state.expandedCharacterNames.add(characterName);
        } else {
            state.expandedCharacterNames.delete(characterName);
        }
    }

    function updateExpandButtonState(button, expanded) {
        const label = expanded
            ? translate('cloudsave.action.collapseDetails', 'Collapse details')
            : translate('cloudsave.action.expandDetails', 'Expand details');
        button.classList.toggle('open', expanded);
        button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        button.setAttribute('aria-label', label);
        button.title = label;
    }

    function getOrderedItems(items) {
        const list = Array.isArray(items) ? [...items] : [];
        if (!state.preferredCharacterName) {
            list.sort((left, right) => {
                return String(left.character_name || '').localeCompare(String(right.character_name || ''));
            });
            return list;
        }

        list.sort((left, right) => {
            const leftRank = left.character_name === state.preferredCharacterName ? 0 : 1;
            const rightRank = right.character_name === state.preferredCharacterName ? 0 : 1;
            if (leftRank !== rightRank) {
                return leftRank - rightRank;
            }
            return String(left.character_name || '').localeCompare(String(right.character_name || ''));
        });
        return list;
    }

    function isWorkshopCharacterItem(item) {
        if (!item || typeof item !== 'object') {
            return false;
        }
        return isWorkshopSource(item.local_origin_source)
            || isWorkshopSource(item.cloud_origin_source);
    }

    function createGroupSection(options) {
        const section = document.createElement('section');
        section.className = `cloudsave-group ${options.kind || ''}`.trim();

        const header = document.createElement('div');
        header.className = 'cloudsave-group-header';

        const titleRow = document.createElement('div');
        titleRow.className = 'cloudsave-group-title-row';

        const title = document.createElement('h2');
        title.className = 'cloudsave-group-title';
        title.textContent = options.title;

        const count = document.createElement('span');
        count.className = 'cloudsave-group-count';
        count.textContent = translate(
            'cloudsave.group.count',
            '{{count}} items',
            { count: options.items.length },
        );

        titleRow.appendChild(title);
        titleRow.appendChild(count);

        const subtitle = document.createElement('p');
        subtitle.className = 'cloudsave-group-subtitle';
        subtitle.textContent = options.subtitle;

        header.appendChild(titleRow);
        header.appendChild(subtitle);
        section.appendChild(header);

        const list = document.createElement('div');
        list.className = 'cloudsave-group-list';
        options.items.forEach(item => {
            list.appendChild(renderItem(item));
        });
        section.appendChild(list);

        return section;
    }

    async function requestJson(url, options) {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
            const error = new Error(payload.message || payload.error || `HTTP ${response.status}`);
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    async function fetchCharacterDetail(characterName) {
        return requestJson(`/api/cloudsave/character/${encodeURIComponent(characterName)}`);
    }

    function buildConfirmMessage(item, action) {
        const localUsesManualAsset = isManualImportSource(item.local_asset_source);
        const cloudUsesManualAsset = isManualImportSource(item.cloud_asset_source);

        if (action === 'upload') {
            const baseMessage = item.relation_state === 'diverged' || item.relation_state === 'matched'
                ? translate(
                    'cloudsave.confirm.uploadOverwrite',
                    'This will overwrite the local cloud snapshot entry with the same name. It will not change the local character immediately, and model asset files are still not included.',
                )
                : translate(
                    'cloudsave.confirm.uploadNew',
                    'This will write the local character into the local cloud snapshot cache. Model asset files are not included.',
                );
            const manualAssetHint = localUsesManualAsset
                ? translate(
                    'cloudsave.confirm.uploadManualAsset',
                    '\nThis local character uses a manually imported or external model. After another device downloads this character, the same model files must still be imported manually.',
                )
                : '';
            const workshopHint = buildWorkshopUploadHint(item);
            return `${baseMessage}${manualAssetHint}${workshopHint}${translate('cloudsave.confirm.uploadFinal', '\nDo you want to continue preparing the Steam Cloud snapshot?')}`;
        }

        const baseMessage = item.relation_state === 'diverged' || item.relation_state === 'matched'
            ? translate(
                'cloudsave.confirm.downloadOverwrite',
                'This will overwrite the local character settings and memory directory with the same name using the cloud snapshot already synced to this device. Model assets will not be downloaded automatically, and a local backup will be created first.',
            )
            : translate(
                'cloudsave.confirm.downloadNew',
                'This will restore the cloud snapshot already synced to this device into local storage. Model assets will not be downloaded automatically, and a local backup will be created first.',
            );
        const manualAssetHint = cloudUsesManualAsset
            ? translate(
                'cloudsave.confirm.downloadManualAsset',
                '\nThis cloud character depends on a manually imported or external model. If this device does not already have those files, you will still need to import or move them manually after download.',
            )
            : '';
        const workshopHint = buildWorkshopDownloadHint(item);
        return `${baseMessage}${manualAssetHint}${workshopHint}${translate('cloudsave.confirm.downloadFinal', '\nDo you want to continue restoring from the Steam Cloud snapshot?')}`;
    }

    async function refreshSummaryAfterStateChange(preferredCharacterName) {
        if (preferredCharacterName) {
            state.preferredCharacterName = preferredCharacterName;
        }

        try {
            await loadSummary({ silent: true });
        } catch (error) {
            // Ignore summary refresh errors here so the user still sees the state-changed message.
        }

        await showAlert(
            translate(
                'cloudsave.dialog.stateChanged',
                'Cloud save status changed. Please review the refreshed summary and try again.',
            ),
        );
    }

    async function _handleDownloadSuccess(result, action, latestItem, item) {
        await showAlert(
            action === 'upload'
                ? (
                    isSteamAutoCloudBackend(result)
                        ? translate(
                            'cloudsave.dialog.uploadSuccessSteamAutoCloud',
                            'Local cloud snapshot updated. Steam will upload it automatically after you exit the game through Steam.',
                        )
                        : translate('cloudsave.dialog.uploadSuccess', 'Upload completed.')
                )
                : (
                    isSteamAutoCloudBackend(result)
                        ? translate(
                            'cloudsave.dialog.downloadSuccessSteamAutoCloud',
                            'The Steam Cloud snapshot was applied locally.',
                        )
                        : translate('cloudsave.dialog.downloadSuccess', 'Download completed.')
                ),
        );
        if (result && result.detail && result.detail.item) {
            state.preferredCharacterName = result.detail.item.character_name || state.preferredCharacterName;
        } else if (latestItem.character_name) {
            state.preferredCharacterName = latestItem.character_name;
        }
        if (action === 'download') {
            notifyCharacterManagerSync({
                action,
                character_name: state.preferredCharacterName || latestItem.character_name || item.character_name,
            });
            window.dispatchEvent(new CustomEvent('neko-cloudsave-character-reloaded', {
                detail: { character_name: state.preferredCharacterName || latestItem.character_name },
            }));
        }
        await loadSummary();
    }

    async function _handleDownloadError(error) {
        const payloadError = error.payload || {};
        const message = translateErrorPayload(
            payloadError,
            payloadError.message || payloadError.error || error.message,
        ) || payloadError.message || payloadError.error || error.message;
        let rollbackHint = '';
        if (payloadError.code === 'LOCAL_RELOAD_FAILED_ROLLED_BACK') {
            if (payloadError.rolled_back) {
                rollbackHint = translate(
                    'cloudsave.dialog.rollbackApplied',
                    '\nLocal data was rolled back automatically to the state before the operation.',
                );
            } else if (payloadError.rollback_error) {
                rollbackHint = translate(
                    'cloudsave.dialog.rollbackFailed',
                    '\nRollback also failed: {{message}}',
                    {
                        message: translateErrorPayload(
                            {
                                code: payloadError.rollback_error,
                                message: payloadError.rollback_error,
                                error: payloadError.rollback_error,
                            },
                            payloadError.rollback_error,
                        ) || payloadError.rollback_error,
                    },
                );
            }
        }
        await showAlert(
            translate(
                'cloudsave.dialog.operationFailed',
                'Operation failed: {{message}}',
                { message },
            ) + rollbackHint,
        );
        try {
            await loadSummary({ silent: true });
        } catch (refreshError) {
            // Keep the original action error visible even if the silent refresh also fails.
        }
    }

    async function performCharacterAction(item, action) {
        if (!isProviderAvailable()) {
            await showAlert(
                translate(
                    'cloudsave.dialog.providerUnavailable',
                    'Cloud save provider is currently unavailable. Please try again later.',
                ),
            );
            return;
        }

        const actionKey = `${item && item.character_name ? item.character_name : ''}::${action}`;
        if (inflightActions.has(actionKey)) {
            await showAlert(
                translate(
                    'cloudsave.dialog.operationInProgress',
                    'An operation for this character is already in progress. Please wait a moment.',
                ),
            );
            return;
        }
        inflightActions.add(actionKey);

        try {
            let latestDetail;
            try {
                latestDetail = await fetchCharacterDetail(item.character_name);
            } catch (error) {
                const payload = error.payload || {};
                if (
                    payload.code === 'CLOUDSAVE_CHARACTER_NOT_FOUND'
                    || payload.code === 'LOCAL_CHARACTER_NOT_FOUND'
                    || payload.code === 'CLOUD_CHARACTER_NOT_FOUND'
                ) {
                    await refreshSummaryAfterStateChange(item.character_name);
                    return;
                }

                const message = translateErrorPayload(
                    payload,
                    payload.message || payload.error || error.message,
                ) || payload.message || payload.error || error.message;
                await showAlert(
                    translate(
                        'cloudsave.dialog.operationFailed',
                        'Operation failed: {{message}}',
                        { message },
                    ),
                );
                return;
            }

            const latestItem = latestDetail && latestDetail.item ? latestDetail.item : null;
            if (!latestItem) {
                await refreshSummaryAfterStateChange(item.character_name);
                return;
            }

            state.preferredCharacterName = latestItem.character_name || state.preferredCharacterName;

            if (latestDetail.provider_available === false) {
                try {
                    await loadSummary({ silent: true });
                } catch (error) {
                    // Ignore refresh failures and surface the provider-unavailable message instead.
                }
                await showAlert(
                    translate(
                        'cloudsave.dialog.providerUnavailable',
                        'Cloud save provider is currently unavailable. Please try again later.',
                    ),
                );
                return;
            }

            const availableActions = Array.isArray(latestItem.available_actions) ? latestItem.available_actions : [];
            if (!availableActions.includes(action)) {
                await refreshSummaryAfterStateChange(latestItem.character_name || item.character_name);
                return;
            }

            const isOverwrite = latestItem.relation_state === 'diverged' || latestItem.relation_state === 'matched';
            const confirmed = await showConfirm(
                buildConfirmMessage(latestItem, action),
                action === 'upload'
                    ? (
                        isSteamAutoCloudBackend(latestDetail)
                            ? translate('cloudsave.dialog.uploadTitleSteamAutoCloud', 'Prepare Steam Cloud Snapshot')
                            : translate('cloudsave.dialog.uploadTitle', 'Upload Cloud Save')
                    )
                    : (
                        isSteamAutoCloudBackend(latestDetail)
                            ? translate('cloudsave.dialog.downloadTitleSteamAutoCloud', 'Apply Steam Cloud Snapshot')
                            : translate('cloudsave.dialog.downloadTitle', 'Download Cloud Save')
                    ),
                { danger: action === 'download' || isOverwrite },
            );
            if (!confirmed) return;

            const endpoint = `/api/cloudsave/character/${encodeURIComponent(latestItem.character_name)}/${action}`;
            const payload = action === 'upload'
                ? { overwrite: isOverwrite }
                : { overwrite: isOverwrite, backup_before_overwrite: true };

            try {
                const result = await requestJson(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                await _handleDownloadSuccess(result, action, latestItem, item);
            } catch (error) {
                const payloadError = error.payload || {};

                if (payloadError.code === 'ACTIVE_SESSION_BLOCKED' && payloadError.can_force) {
                    const forceConfirmed = await showConfirm(
                        translate(
                            'cloudsave.confirm.forceTerminateSession',
                            'Character "{{name}}" has an active session.\nTerminating the session will discard the current conversation, but the cloud save download can proceed.\n\nTerminate session and continue?',
                            { name: latestItem.character_name || item.character_name },
                        ),
                        translate('cloudsave.dialog.forceTerminateTitle', 'Terminate session and continue?'),
                        { danger: true },
                    );
                    if (!forceConfirmed) {
                        return;
                    }
                    try {
                        const retryResult = await requestJson(endpoint, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ ...payload, force: true }),
                        });
                        await _handleDownloadSuccess(retryResult, action, latestItem, item);
                    } catch (retryError) {
                        await _handleDownloadError(retryError);
                    }
                    return;
                }

                await _handleDownloadError(error);
            }
        } finally {
            inflightActions.delete(actionKey);
        }
    }

    function renderItem(item) {
        const wrapper = document.createElement('section');
        wrapper.className = 'cloudsave-item';

        const header = document.createElement('div');
        header.className = 'cloudsave-item-header';

        const headerMain = document.createElement('div');
        headerMain.className = 'cloudsave-item-main';

        const expandButton = document.createElement('button');
        expandButton.type = 'button';
        expandButton.className = 'cloudsave-item-expand';

        const expandIcon = document.createElement('img');
        expandIcon.className = 'cloudsave-item-expand-icon';
        expandIcon.src = '/static/icons/dropdown_arrow.png';
        expandIcon.alt = '';
        expandButton.appendChild(expandIcon);

        const titleGroup = document.createElement('div');
        const title = document.createElement('h2');
        title.className = 'cloudsave-item-title';
        title.textContent = item.display_name || item.character_name;
        const subtitle = document.createElement('p');
        subtitle.className = 'cloudsave-item-subtitle';
        subtitle.textContent = `${item.character_name} - ${summarizeRelationState(item.relation_state)}`;
        titleGroup.appendChild(title);
        titleGroup.appendChild(subtitle);
        headerMain.appendChild(expandButton);
        headerMain.appendChild(titleGroup);

        const badges = document.createElement('div');
        badges.className = 'cloudsave-badges';
        buildBadgeItems(item).forEach(({ text }) => {
            const badge = document.createElement('span');
            badge.className = 'cloudsave-badge';
            badge.textContent = text;
            badges.appendChild(badge);
        });

        header.appendChild(headerMain);
        header.appendChild(badges);
        wrapper.appendChild(header);

        const details = document.createElement('div');
        details.className = 'cloudsave-item-details';
        detailPanelIdSequence += 1;
        details.id = `cloudsave-item-details-${detailPanelIdSequence}`;
        expandButton.setAttribute('aria-controls', details.id);

        const noValueText = translate('cloudsave.metaNoValue', 'None');
        const localMetaEntries = [
            [translate('cloudsave.meta.localAssetState', 'Local asset state'), summarizeAssetState(item.local_asset_state)],
            [translate('cloudsave.meta.localAssetSource', 'Local asset source'), summarizeAssetSource(item.local_asset_source)],
        ];
        if (isWorkshopSource(item.local_asset_source)) {
            localMetaEntries.push([translate('cloudsave.meta.localWorkshopStatus', 'Local current status'), formatWorkshopStatus(item, 'local')]);
        }
        if (item.local_origin_source) {
            localMetaEntries.push([translate('cloudsave.meta.localCharacterOrigin', 'Local character origin'), summarizeAssetSource(item.local_origin_source)]);
        }
        if (isWorkshopSource(item.local_origin_source)) {
            localMetaEntries.push([translate('cloudsave.meta.localOriginWorkshopStatus', 'Local character origin and current status'), formatWorkshopStatus(item, 'local_origin')]);
        }
        localMetaEntries.push(
            [translate('cloudsave.meta.localUpdatedAt', 'Local updated at'), formatUtcTimestamp(item.local_updated_at_utc) || noValueText],
        );

        const cloudMetaEntries = [
            [translate('cloudsave.meta.cloudAssetState', 'Cloud asset state'), summarizeAssetState(item.cloud_asset_state)],
            [translate('cloudsave.meta.cloudAssetSource', 'Cloud asset source'), summarizeAssetSource(item.cloud_asset_source)],
        ];
        if (isWorkshopSource(item.cloud_asset_source)) {
            cloudMetaEntries.push([translate('cloudsave.meta.cloudWorkshopStatus', 'Cloud current status'), formatWorkshopStatus(item, 'cloud')]);
        }
        if (item.cloud_origin_source) {
            cloudMetaEntries.push([translate('cloudsave.meta.cloudCharacterOrigin', 'Cloud character origin'), summarizeAssetSource(item.cloud_origin_source)]);
        }
        if (isWorkshopSource(item.cloud_origin_source)) {
            cloudMetaEntries.push([translate('cloudsave.meta.cloudOriginWorkshopStatus', 'Cloud character origin and current status'), formatWorkshopStatus(item, 'cloud_origin')]);
        }
        cloudMetaEntries.push(
            [translate('cloudsave.meta.cloudUpdatedAt', 'Cloud updated at'), formatUtcTimestamp(item.cloud_updated_at_utc) || noValueText],
        );

        const metaSections = document.createElement('div');
        metaSections.className = 'cloudsave-meta-sections';
        metaSections.appendChild(
            buildMetaSection(
                'local',
                translate('cloudsave.meta.groupLocal', 'Local status'),
                localMetaEntries,
            ),
        );
        metaSections.appendChild(
            buildMetaSection(
                'cloud',
                translate('cloudsave.meta.groupCloud', 'Cloud status'),
                cloudMetaEntries,
            ),
        );
        details.appendChild(metaSections);

        if (Array.isArray(item.warnings) && item.warnings.length > 0) {
            const warnings = document.createElement('div');
            warnings.className = 'cloudsave-warning-list';
            item.warnings.forEach(code => {
                const warning = document.createElement('div');
                warning.className = 'cloudsave-warning';
                warning.textContent = summarizeWarning(code);
                warnings.appendChild(warning);
            });
            details.appendChild(warnings);
        }

        const guidanceItems = buildResourceGuidance(item);
        if (guidanceItems.length > 0) {
            const titleNode = document.createElement('div');
            titleNode.className = 'cloudsave-section-title';
            titleNode.textContent = translate('cloudsave.resourceRecoveryTitle', 'Resource recovery notes');
            details.appendChild(titleNode);

            const guidanceList = document.createElement('div');
            guidanceList.className = 'cloudsave-guidance-list';
            guidanceItems.forEach(guidanceItem => {
                const guidance = document.createElement('div');
                guidance.className = `cloudsave-guidance ${guidanceItem.tone}`;

                const guidanceTitle = document.createElement('div');
                guidanceTitle.className = 'cloudsave-guidance-title';
                guidanceTitle.textContent = guidanceItem.title;

                const guidanceBody = document.createElement('div');
                guidanceBody.className = 'cloudsave-guidance-body';
                guidanceBody.textContent = guidanceItem.body;

                guidance.appendChild(guidanceTitle);
                guidance.appendChild(guidanceBody);
                guidanceList.appendChild(guidance);
            });
            details.appendChild(guidanceList);
        }

        const actions = document.createElement('div');
        actions.className = 'cloudsave-item-actions';
        const providerAvailable = isProviderAvailable();
        const canUpload = providerAvailable && (item.available_actions || []).includes('upload');
        const canDownload = providerAvailable && (item.available_actions || []).includes('download');

        const uploadButton = document.createElement('button');
        uploadButton.type = 'button';
        uploadButton.className = 'btn';
        uploadButton.textContent = !providerAvailable
            ? translate('cloudsave.action.uploadDisabledByProvider', 'Prepare snapshot unavailable')
            : (canUpload
                ? translate('cloudsave.action.uploadNew', 'Prepare snapshot')
                : translate('cloudsave.action.uploadUnavailable', 'Prepare snapshot unavailable'));
        uploadButton.disabled = !canUpload;
        uploadButton.addEventListener('click', () => performCharacterAction(item, 'upload'));
        actions.appendChild(uploadButton);

        const downloadButton = document.createElement('button');
        downloadButton.type = 'button';
        downloadButton.className = 'btn danger';
        downloadButton.textContent = !providerAvailable
            ? translate('cloudsave.action.downloadDisabledByProvider', 'Apply snapshot unavailable')
            : (canDownload
                ? translate('cloudsave.action.downloadNew', 'Apply snapshot')
                : translate('cloudsave.action.downloadUnavailable', 'Apply snapshot unavailable'));
        downloadButton.disabled = !canDownload;
        downloadButton.addEventListener('click', () => performCharacterAction(item, 'download'));
        actions.appendChild(downloadButton);

        details.appendChild(actions);

        const shouldBeOpen = isCharacterExpanded(item.character_name);
        details.hidden = !shouldBeOpen;
        wrapper.classList.toggle('is-open', shouldBeOpen);
        updateExpandButtonState(expandButton, shouldBeOpen);

        expandButton.addEventListener('click', () => {
            const nextExpanded = details.hidden;
            details.hidden = !nextExpanded;
            wrapper.classList.toggle('is-open', nextExpanded);
            setCharacterExpanded(item.character_name, nextExpanded);
            updateExpandButtonState(expandButton, nextExpanded);
        });

        wrapper.appendChild(details);
        return wrapper;
    }

    function renderSummary(summary) {
        const providerStatus = document.getElementById('cloudsave-provider-status');
        const providerScope = document.getElementById('cloudsave-provider-scope');
        const currentCharacter = document.getElementById('cloudsave-current-character');
        const list = document.getElementById('cloudsave-list');
        const emptyState = document.getElementById('cloudsave-empty-state');
        const steamAutoCloud = getSteamAutoCloudStatus(summary);

        if (providerStatus) {
            if (!summary.provider_available) {
                setTranslatedText(
                    providerStatus,
                    'cloudsave.providerUnavailable',
                    'Cloud save is currently unavailable. This page only shows local summaries.',
                );
            } else if (isSteamAutoCloudBackend(summary) && isSourceLaunchSession(summary)) {
                setTranslatedText(
                    providerStatus,
                    'cloudsave.providerSteamAutoCloudSourceLaunch',
                    'This session was started from source. If Steam is running and logged in, the desktop RemoteStorage helper can assist with cloudsave download and upload, but this still is not the same as the packaged Steam Auto-Cloud path. For production-path verification, launch once through Steam or the desktop launcher.',
                );
            } else if (isSteamAutoCloudBackend(summary) && isSteamAutoCloudSessionReady(summary)) {
                setTranslatedText(
                    providerStatus,
                    'cloudsave.providerSteamAutoCloudReady',
                    'Steam Cloud is connected. Upload writes the local cloud snapshot now, and Steam will upload it automatically after you exit the game through Steam.',
                );
            } else if (isSteamAutoCloudBackend(summary)) {
                setTranslatedText(
                    providerStatus,
                    'cloudsave.providerSteamAutoCloudOffline',
                    'Local cloud snapshots are available, but Steam Cloud is not currently connected. Start the game from Steam while logged in if you want Steam to upload or download snapshots automatically.',
                );
            } else {
                setTranslatedText(
                    providerStatus,
                    'cloudsave.providerAvailable',
                    'Cloud save is available. You can prepare or restore individual character snapshots manually.',
                );
            }
        }

        if (providerScope) {
            const scopeParts = [
                translate(
                    'cloudsave.providerSnapshotScope',
                    'This page shows the staged cloud snapshot already stored on this device.',
                ),
            ];
            if (Number.isFinite(Number(steamAutoCloud.snapshot_sequence_number)) && Number(steamAutoCloud.snapshot_sequence_number) > 0) {
                scopeParts.push(
                    translate(
                        'cloudsave.providerSnapshotSequence',
                        'Sequence {{sequence}}',
                        { sequence: steamAutoCloud.snapshot_sequence_number },
                    ),
                );
            }
            if (steamAutoCloud.snapshot_exported_at_utc) {
                scopeParts.push(
                    translate(
                        'cloudsave.providerSnapshotExportedAt',
                        'Exported {{time}}',
                        { time: formatUtcTimestamp(steamAutoCloud.snapshot_exported_at_utc) || steamAutoCloud.snapshot_exported_at_utc },
                    ),
                );
            }
            if (steamAutoCloud.manual_download_required) {
                scopeParts.push(
                    translate(
                        'cloudsave.providerSnapshotManualApply',
                        'A newer staged snapshot is already on this device, but runtime data will change only after you click Apply snapshot manually.',
                    ),
                );
            }
            setTranslatedText(providerScope, null, scopeParts.join(' '));
        }

        if (summary.current_character_name) {
            setTranslatedText(
                currentCharacter,
                'cloudsave.currentCharacter',
                'Current character: {{name}}',
                { name: summary.current_character_name },
            );
        } else {
            setTranslatedText(
                currentCharacter,
                'cloudsave.noCurrentCharacter',
                'Current character: Not set',
            );
        }

        list.innerHTML = '';
        const sourceItems = Array.isArray(summary.items) ? summary.items : [];
        if (sourceItems.length === 0) {
            emptyState.style.display = 'block';
            return;
        }
        emptyState.style.display = 'none';

        const workshopItems = [];
        const otherItems = [];
        sourceItems.forEach(item => {
            if (isWorkshopCharacterItem(item)) {
                workshopItems.push(item);
            } else {
                otherItems.push(item);
            }
        });

        if (otherItems.length > 0) {
            list.appendChild(createGroupSection({
                kind: 'other',
                title: translate(
                    'cloudsave.group.otherTitle',
                    'My characters',
                ),
                subtitle: translate(
                    'cloudsave.group.otherDescription',
                    'Characters here use local imports, built-in resources, or other non-Workshop asset sources.',
                ),
                items: getLocallyUpdatedItems(otherItems),
            }));
        }

        if (workshopItems.length > 0) {
            list.appendChild(createGroupSection({
                kind: 'workshop',
                title: translate(
                    'cloudsave.group.workshopTitle',
                    'Workshop characters',
                ),
                subtitle: translate(
                    'cloudsave.group.workshopDescription',
                    'Characters here are currently related to Steam Workshop assets on either the local side or the cloud side.',
                ),
                items: getOrderedItems(workshopItems),
            }));
        }
    }

    async function loadSummary(options = {}) {
        const silent = options.silent === true;
        try {
            const summary = await requestJson('/api/cloudsave/summary');
            state.summary = summary;
            renderSummary(summary);
            return summary;
        } catch (error) {
            if (silent) {
                throw error;
            }
            const payload = error.payload || {};
            const message = translateErrorPayload(payload, payload.message || payload.error || error.message)
                || payload.message
                || payload.error
                || error.message;
            await showAlert(
                translate(
                    'cloudsave.dialog.loadFailed',
                    'Failed to load the cloud save summary: {{message}}',
                    { message },
                ),
            );
            return null;
        }
    }

    function bindEvents() {
        const refreshButton = document.getElementById('refresh-cloudsave-btn');
        if (refreshButton) {
            refreshButton.addEventListener('click', loadSummary);
        }

        const backButton = document.getElementById('back-to-chara-manager-btn');
        if (backButton) {
            backButton.addEventListener('click', () => {
                if (window.opener && !window.opener.closed) {
                    window.close();
                    return;
                }
                // Replace the current history entry so closing the character manager
                // does not bounce back into the cloud save page and create a loop.
                window.location.replace('/character_card_manager');
            });
        }

        if (!window.__cloudsaveLocaleListenerBound) {
            window.__cloudsaveLocaleListenerBound = true;
            window.addEventListener('localechange', () => {
                window.setTimeout(() => {
                    if (state.summary) {
                        renderSummary(state.summary);
                    }
                }, 0);
            });
        }
    }

    async function initPage() {
        state.preferredCharacterName = getPreferredCharacterName();
        bindEvents();
        await waitForI18nReady();
        if (typeof window.updatePageTexts === 'function') {
            window.updatePageTexts();
        }
        await loadSummary();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPage);
    } else {
        initPage();
    }
})();

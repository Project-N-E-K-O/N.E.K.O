const streamIds = new WeakMap();
let nextStreamId = 0;
function streamKey(stream) {
    if (!streamIds.has(stream)) streamIds.set(stream, ++nextStreamId);
    return `stream:${streamIds.get(stream)}`;
}

// On-demand visual inspection. Native-resolution pixels go only to the owning turn.
// Source selection is read-only: inspecting a window never changes screen sharing.
export async function handleScreenInspection(request, state, socket) {
    const reply = {action: 'screen_inspection_response', request_id: request.request_id};
    const provider = typeof window.getDesktopCaptureProvider === 'function'
        ? window.getDesktopCaptureProvider() : window.tauriDesktopCapturer || window.electronDesktopCapturer;
    const mayPrompt = provider && (typeof window.desktopSourceEnumerationMayPrompt === 'function'
        ? window.desktopSourceEnumerationMayPrompt(provider) : provider.sourceEnumerationMayPrompt !== false);
    const deadline = Date.now() + 7000;
    const bounded = async (work) => {
        let timer;
        try {
            return await Promise.race([Promise.resolve().then(work), new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('timeout')), Math.max(0, deadline - Date.now()));
            })]);
        } finally { clearTimeout(timer); }
    };
    const blocked = () => (
        (typeof window.isNekoHomeTutorialProactiveSuppressed === 'function'
            && window.isNekoHomeTutorialProactiveSuppressed())
        || (window.appProactive && typeof window.appProactive.isProactiveVisionEnabledNow === 'function'
            ? !window.appProactive.isProactiveVisionEnabledNow() : state.proactiveVisionEnabled === false)
    );
    const scopeNow = () => {
        const id = state.selectedScreenSourceId || '';
        const candidate = state.screenCaptureStream;
        const track = candidate && candidate.active && candidate.getVideoTracks()[0];
        const stream = track && track.readyState !== 'ended' ? candidate : null;
        const displaySurface = stream && track.getSettings().displaySurface;
        const bound = stream && typeof window.getScreenCaptureStreamSourceId === 'function'
            && window.getScreenCaptureStreamSourceId(stream) === id;
        if (id.startsWith('window:')) {
            return {key: id, restricted: true, id, stream: bound ? stream : null};
        }
        if (displaySurface === 'window' || displaySurface === 'browser') {
            return {key: streamKey(stream), restricted: true, id: '', stream};
        }
        return {key: id || (stream ? streamKey(stream) : 'desktop'), restricted: false, id, stream};
    };
    try {
        if (blocked()) throw new Error('disabled');
        let remembered = null;
        if (typeof window.prepareRememberedWindowCapture === 'function') {
            remembered = await bounded(() => window.prepareRememberedWindowCapture({readOnly: true}));
            if (remembered && remembered.required && !remembered.allowed) {
                throw new Error('scope_changed');
            }
        }
        const scope = scopeNow();
        // A remembered window must never silently fall through to the desktop.
        if (remembered && remembered.required && !scope.restricted) {
            throw new Error('scope_changed');
        }
        const check = () => {
            if (blocked() || scopeNow().key !== scope.key || scopeNow().stream !== scope.stream
                || (remembered && remembered.required && typeof remembered.isCurrent === 'function' && !remembered.isCurrent())) {
                throw new Error('scope_changed');
            }
        };
        check();
        if (request.scope !== undefined && request.scope !== scope.key) throw new Error('scope_changed');
        if (request.operation === 'validate') {
            Object.assign(reply, {success: true, scope: scope.key});
        } else {
            if (!['overview', 'window'].includes(request.operation)) throw new Error('invalid_request');
            let sources = [];
            if (provider && typeof provider.getSources === 'function' && !mayPrompt) {
                sources = await bounded(() => provider.getSources({types: scope.restricted ? ['window'] : ['window', 'screen'],
                    thumbnailSize: {width: 0, height: 0}}));
                check();
            }
            if (!Array.isArray(sources)) sources = [];
            if (scope.restricted) sources = sources.filter(s => s.id === scope.id);
            const windows = sources.filter(s => typeof s.id === 'string' && s.id.startsWith('window:'))
                .slice(0, 80).map(s => ({id: s.id, name: String(s.name || '').slice(0, 200)}));
            let sourceId = scope.id;
            if (request.operation === 'window') {
                if (!windows.some(w => w.id === request.window_id)) throw new Error('window_unavailable');
                sourceId = request.window_id;
            } else if (!sourceId && !scope.stream && !scope.restricted) {
                sourceId = (sources.find(s => s.id.startsWith('screen:')) || {}).id;
            }
            let dataUrl, captureType, title = '';
            if (sourceId && provider && !mayPrompt
                && typeof provider.captureSourceAsDataUrl === 'function') {
                const result = await bounded(() => provider.captureSourceAsDataUrl(sourceId));
                check();
                if (!result || !result.success || !result.dataUrl) throw new Error('capture_failed');
                dataUrl = result.dataUrl;
                captureType = sourceId.startsWith('screen:') ? 'screen' : null;
                title = (sources.find(s => s.id === sourceId) || {}).name || sourceId;
            } else if (request.operation === 'overview' && scope.stream) {
                const video = document.createElement('video');
                video.muted = true;
                video.srcObject = scope.stream;
                try {
                    await bounded(() => video.play());
                    check();
                    if (!video.videoWidth || !video.videoHeight) throw new Error('capture_failed');
                    const canvas = document.createElement('canvas');
                    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
                    canvas.getContext('2d').drawImage(video, 0, 0);
                    dataUrl = canvas.toDataURL('image/png');
                    captureType = scope.restricted ? null : 'screen';
                    sourceId = scope.key;
                    title = scope.restricted ? 'Shared window' : 'Shared screen';
                } finally { video.pause(); video.srcObject = null; }
            } else throw new Error('unsupported_capture');
            check();
            // Stay below the WebSocket transport ceiling, including the JSON envelope.
            if (typeof dataUrl !== 'string' || dataUrl.length > 12 * 1024 * 1024) {
                throw new Error('invalid_image');
            }
            const avatar = captureType === 'screen' && typeof window.getAvatarScreenPosition === 'function'
                ? window.getAvatarScreenPosition('screen') : null;
            Object.assign(reply, {success: true, data_url: dataUrl, scope: scope.key,
                source_id: sourceId || null, title, windows, avatar_position: avatar});
        }
    } catch (error) {
        Object.assign(reply, {success: false, error: String(error.message || 'capture_failed').slice(0, 160)});
    }
    // Never deliver an old request through a replacement connection.
    if (socket && socket === state.socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(reply));
}

// On-demand visual inspection. Native-resolution pixels go only to the owning turn.
// Source selection is read-only: inspecting a window never changes screen sharing.
export async function handleScreenInspection(request, state, socket) {
    const reply = {action: 'screen_inspection_response', request_id: request.request_id};
    const provider = window.electronDesktopCapturer || window.tauriDesktopCapturer;
    const blocked = () => (
        (typeof window.isNekoHomeTutorialProactiveSuppressed === 'function'
            && window.isNekoHomeTutorialProactiveSuppressed())
        || (window.appProactive && typeof window.appProactive.isProactiveVisionEnabledNow === 'function'
            ? !window.appProactive.isProactiveVisionEnabledNow() : state.proactiveVisionEnabled === false)
    );
    const scopeNow = () => {
        const id = state.selectedScreenSourceId || '';
        const stream = state.screenCaptureStream;
        const track = stream && stream.active && stream.getVideoTracks()[0];
        const displaySurface = track && track.getSettings().displaySurface;
        if (id.startsWith('window:')) return {key: id, restricted: true, id};
        if (displaySurface === 'window' || displaySurface === 'browser') {
            return {key: 'stream:'+track.id, restricted: true, id, stream};
        }
        return {key: id || 'desktop', restricted: false, id, stream};
    };
    try {
        if (blocked()) throw new Error('disabled');
        let remembered = null;
        if (typeof window.prepareRememberedWindowCapture === 'function') {
            remembered = await window.prepareRememberedWindowCapture();
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
            if (blocked() || scopeNow().key !== scope.key
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
            if (provider && typeof provider.getSources === 'function' && !provider.sourceEnumerationMayPrompt) {
                sources = await provider.getSources({types: scope.restricted ? ['window'] : ['window', 'screen'],
                    thumbnailSize: {width: 0, height: 0}});
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
            if (sourceId && provider && !provider.sourceEnumerationMayPrompt
                && typeof provider.captureSourceAsDataUrl === 'function') {
                const result = await provider.captureSourceAsDataUrl(sourceId);
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
                    await video.play();
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
            } else if (request.operation === 'overview' && !scope.restricted && window.appProactive) {
                const shot = await window.appProactive.captureProactiveChatScreenshotWithSource();
                check();
                if (!shot || shot.declined || !shot.dataUrl) throw new Error('capture_failed');
                dataUrl = shot.dataUrl; captureType = shot.captureType;
                title = 'Shared source (compatibility capture)';
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

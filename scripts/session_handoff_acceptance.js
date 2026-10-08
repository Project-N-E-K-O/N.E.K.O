/* Paste into the real microphone owner's Browser/Electron DevTools console. */
(() => {
    'use strict';
    if (window.sessionHandoffAcceptance?.running) throw new Error('Acceptance is already running');
    const S = window.appState;
    if (!S) throw new Error('Run in the microphone owner page with appState');
    const report = { schema: 1, created: new Date().toISOString(), runs: [] };
    let stopped = false;
    const now = () => performance.now();
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const state = () => ({ recording: !!S.isRecording, starting: !!window.isMicStarting,
        pending: !!S.voiceStartPending, playing: !!S.isPlaying,
        voiceChatActive: !!S.voiceChatActive, socketState: S.socket?.readyState });
    function click(id) {
        const button = document.getElementById(id);
        if (!button || button.disabled) throw new Error(`UI unavailable: ${id}`);
        button.click();
    }
    async function until(check, timeout) {
        const deadline = now() + timeout;
        while (!check()) {
            if (stopped) throw new Error('operator_stop');
            if (now() >= deadline) return false;
            await sleep(10);
        }
        return true;
    }
    const api = window.sessionHandoffAcceptance = {
        running: false, report,
        stop() { stopped = true; },
        export() {
            const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `session-handoff-${Date.now()}.json`; a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            return report;
        },
        async run({ environment, phase = 'idle', configLabel = 'voiceprint-off',
            repetitions = 10, gaps = [0, 50, 100, 300, 1000], audioTimeoutMs = 15000 } = {}) {
            if (api.running) throw new Error('Acceptance is already running');
            if (!['browser', 'electron'].includes(environment)) throw new Error('Set environment explicitly');
            if (!['idle', 'speaking'].includes(phase)) throw new Error('Unknown phase');
            const socket = S.socket;
            if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error('Real backend socket is not open');
            const run = { environment, phase, configLabel, started: new Date().toISOString(),
                path: location.pathname, rows: [], outcome: 'running' };
            report.runs.push(run);
            stopped = false; api.running = true;
            let row = null;
            let audioHeader = null;
            const originalSend = socket.send;
            const ownSend = Object.getOwnPropertyDescriptor(socket, 'send');
            function wrappedSend(data) {
                if (row && typeof data === 'string') {
                    try {
                        const msg = JSON.parse(data);
                        if (msg.action === 'start_session') {
                            row.requestId = msg.request_id || null;
                            row.requestSentAt = now();
                        } else if (msg.action === 'pause_session') row.pauseSentAt = now();
                    } catch (_) { /* Do not retain message content. */ }
                }
                return originalSend.call(this, data);
            }
            function onMessage(event) {
                if (!row) return;
                const at = now();
                if (typeof event.data !== 'string') {
                    if (row.ackAt && audioHeader && !row.firstBinaryAt) {
                        row.firstBinaryAt = at;
                        row.firstBinaryBytes = event.data.byteLength ?? event.data.size ?? null;
                        row.audioCorrelation = 'speech_header_after_matching_ack';
                    }
                    audioHeader = null;
                    return;
                }
                let msg;
                try { msg = JSON.parse(event.data); } catch (_) { return; }
                if (msg.type === 'audio_chunk') {
                    audioHeader = row.ackAt ? { at } : null;
                    return;
                }
                if (['session_preparing', 'session_started', 'session_failed'].includes(msg.type)) {
                    const matching = !!row.requestId && msg.request_id === row.requestId;
                    row.notifications.push({ type: msg.type, at, requestId: msg.request_id || null, matching });
                    if (matching && msg.type === 'session_started') row.ackAt = at;
                    if (matching && msg.type === 'session_failed') row.failure = 'session_failed';
                }
            }
            socket.send = wrappedSend;
            socket.addEventListener('message', onMessage);
            try {
                if (!S.isRecording) {
                    row = { kind: 'bootstrap', notifications: [], startedAt: now() };
                    run.rows.push(row);
                    click('micButton');
                    if (!await until(() => row.failure || (row.ackAt && S.isRecording), 16000) || row.failure)
                        throw new Error(row.failure || 'bootstrap_timeout');
                    row.outcome = 'ready';
                }
                for (const gapMs of gaps) for (let iteration = 1; iteration <= repetitions; iteration++) {
                    row = { gapMs, iteration, before: state(), notifications: [], requestId: null,
                        ttsReadyMs: null, ttsReadyReason: 'no_frontend_runtime_ready_event' };
                    run.rows.push(row);
                    if (phase === 'speaking') console.info('[handoff] Speak to trigger a real assistant reply now', gapMs, iteration);
                    if (!await until(() => phase === 'speaking' ? S.isPlaying : !S.isPlaying, 30000)) {
                        row.outcome = 'skipped'; row.reason = `${phase}_state_not_observed`;
                        continue;
                    }
                    row.before = state(); row.stopClickedAt = now();
                    // app-buttons.js binds muteButton -> stopMicCapture -> stopRecording.
                    // stopButton is screen sharing and is intentionally not used.
                    click('muteButton');
                    await sleep(gapMs);
                    if (stopped) throw new Error('operator_stop');
                    row.startClickedAt = now();
                    row.actualGapMs = row.startClickedAt - row.stopClickedAt;
                    audioHeader = null;
                    click('micButton');
                    if (!await until(() => row.failure || (row.ackAt && S.isRecording), 16000) || row.failure)
                        throw new Error(row.failure || 'start_timeout');
                    row.ackMs = row.ackAt - row.startClickedAt;
                    row.recordingReadyMs = now() - row.startClickedAt;
                    console.info('[handoff] Reopened; speak for first audio timing', gapMs, iteration);
                    await until(() => row.firstBinaryAt || row.failure, audioTimeoutMs);
                    if (row.failure) throw new Error(row.failure);
                    row.firstBinaryMs = row.firstBinaryAt ? row.firstBinaryAt - row.startClickedAt : null;
                    row.after = state();
                    row.outcome = row.firstBinaryAt ? 'observed' : 'incomplete_audio';
                    if (S.socket !== socket) throw new Error('socket_replaced');
                    console.info('[handoff]', row.outcome, gapMs, iteration, 'ack ms', row.ackMs);
                }
                run.outcome = run.rows.some(x => ['skipped', 'incomplete_audio'].includes(x.outcome))
                    ? 'incomplete' : 'observed_requires_review';
            } catch (error) {
                if (row) { row.outcome = 'failed'; row.reason = error.message; row.after = state(); }
                run.outcome = stopped ? 'stopped' : 'failed';
                run.reason = error.message;
                console.warn('[handoff] Matrix stopped; no automatic retry', error.message);
            } finally {
                socket.removeEventListener('message', onMessage);
                if (socket.send === wrappedSend) {
                    if (ownSend) Object.defineProperty(socket, 'send', ownSend);
                    else delete socket.send;
                }
                api.running = false; run.finished = new Date().toISOString();
            }
            return run;
        },
    };
    console.info('Ready: sessionHandoffAcceptance.run({environment:"browser",phase:"idle"})');
})();

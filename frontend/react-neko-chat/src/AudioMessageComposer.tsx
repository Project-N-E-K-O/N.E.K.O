import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import {
  AUDIO_MAX_SAMPLES, AUDIO_SAMPLE_RATE, recordAudio, recordingBase64, recordingWav,
  type AudioMessagePayload, type Recording,
} from './audioRecording';

const RECORDING_LIMIT_SECONDS = AUDIO_MAX_SAMPLES / AUDIO_SAMPLE_RATE;

type Props = {
  target?: string;
  disabled: boolean;
  send?: (payload: AudioMessagePayload) => Promise<unknown>;
};

// The chat root owns capture and the draft so both survive the compact surface being folded away.
export function useAudioMessageComposer({ target, disabled, send }: Props) {
  const [phase, setPhase] = useState<'idle' | 'starting' | 'recording' | 'preview' | 'sending' | 'failed'>('idle');
  const [preview, setPreview] = useState('');
  const [beganAt, setBeganAt] = useState(0);
  const [durationSeconds, setDurationSeconds] = useState(0);
  const [error, setError] = useState('');
  const audio = useRef<HTMLAudioElement>(null);
  const generation = useRef(0);
  const recording = useRef<Recording>();
  const pcm = useRef<Int16Array>();
  const url = useRef('');
  const messageId = useRef('');
  const submitted = useRef(false);
  const limitTimer = useRef<ReturnType<typeof setTimeout>>();

  const dispose = () => {
    generation.current += 1;
    recording.current?.cancel();
    recording.current = undefined;
    clearTimeout(limitTimer.current);
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = '';
    pcm.current = undefined;
  };
  useEffect(() => {
    cancel();
    window.addEventListener('react-chat-window:closed', cancel);
    return () => {
      window.removeEventListener('react-chat-window:closed', cancel);
      dispose();
    };
  }, [target]);
  useEffect(() => {
    if (disabled && phase !== 'idle' && phase !== 'sending') cancel();
  }, [disabled]);

  function cancel() {
    dispose();
    messageId.current = '';
    submitted.current = false;
    setPhase('idle');
    setPreview('');
    setError('');
  }

  function reportError(message: string) {
    setError(message);
    (window as Window & { showStatusToast?: (message: string, duration?: number) => void })
      .showStatusToast?.(message, 5000);
  }

  async function stop(sendImmediately: boolean) {
    const current = recording.current;
    if (!current) return;
    recording.current = undefined;
    const own = generation.current;
    clearTimeout(limitTimer.current);
    setPhase('starting');
    const samples = await current.stop();
    if (own !== generation.current) return;
    if (samples.length < AUDIO_SAMPLE_RATE / 10) {
      cancel();
      return;
    }
    pcm.current = samples;
    url.current = URL.createObjectURL(recordingWav(samples));
    setPreview(url.current);
    setDurationSeconds(samples.length / AUDIO_SAMPLE_RATE);
    if (sendImmediately) {
      await submit();
    } else {
      setPhase('preview');
    }
  }

  async function start() {
    if (disabled || !target || !send || phase !== 'idle') return;
    if (globalThis.isSecureContext === false) {
      reportError(i18n('instanceAccess.httpsRequired', 'Use HTTPS or a trusted HTTPS tunnel to connect securely.'));
      return;
    }
    const own = ++generation.current;
    try {
      setError('');
      setPhase('starting');
      submitted.current = false;
      const capture = await recordAudio(() => { void stop(false); }, () => own === generation.current);
      if (own !== generation.current) { capture.cancel(); return; }
      recording.current = capture;
      setBeganAt(Date.now());
      setPhase('recording');
      limitTimer.current = setTimeout(() => { void stop(false); }, RECORDING_LIMIT_SECONDS * 1000);
    } catch {
      if (own !== generation.current) return;
      dispose();
      setPhase('idle');
      reportError(i18n('chat.audioMessageMicrophoneError', 'Microphone unavailable. Check its permission and try again.'));
    }
  }

  async function submit() {
    if (disabled || !target || !send || submitted.current || !pcm.current) return;
    if (!messageId.current) messageId.current = `${Date.now()}-${crypto.randomUUID()}`;
    submitted.current = true;
    if (audio.current && !audio.current.paused) audio.current.pause();
    const own = generation.current;
    setError('');
    setPhase('sending');
    try {
      await send({ requestId: messageId.current, target, audio: recordingBase64(pcm.current), sampleRateHz: AUDIO_SAMPLE_RATE });
      if (own === generation.current) cancel();
    } catch (failure) {
      if (own !== generation.current) return;
      if (failure instanceof Error && failure.name === 'AudioMessageRejected'
          && (failure.message === 'busy' || failure.message === 'storage')) {
        submitted.current = false;
        setPhase('preview');
        reportError(failure.message === 'storage'
          ? i18n('chat.audioHistoryStorageError', 'The recording could not be saved. Try again.')
          : i18n('chat.audioMessageBusy', 'The conversation is busy. Try sending the recording again when it is ready.'));
        return;
      }
      setPhase('failed');
      reportError(i18n('chat.audioMessageSendError', 'Delivery could not be confirmed. Check the conversation before recording again.'));
    }
  }

  return {
    phase, preview, beganAt, durationSeconds, error, audio, disabled,
    active: phase !== 'idle' && phase !== 'failed',
    start, stop, submit, cancel,
  };
}

export default function AudioMessageComposer({ composer }: { composer: ReturnType<typeof useAudioMessageComposer> }) {
  const {
    phase, preview, beganAt, durationSeconds, error, audio, disabled, active,
    start, stop, submit, cancel,
  } = composer;
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false);
  useLayoutEffect(() => {
    if (phase !== 'recording') return;
    const tick = () => setElapsed(Math.min(RECORDING_LIMIT_SECONDS, (Date.now() - beganAt) / 1000));
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [beganAt, phase]);
  useLayoutEffect(() => {
    setPlaying(false);
    const player = audio.current;
    return () => {
      if (player && !player.paused) player.pause();
    };
  }, [audio, preview]);

  function togglePlayback() {
    const player = audio.current;
    if (!player) return;
    if (player.paused) {
      void player.play().catch(() => {
        if (audio.current === player) setPlaying(false);
      });
    } else {
      player.pause();
    }
  }

  const displayedSeconds = preview ? Math.ceil(durationSeconds) : Math.floor(elapsed);
  const duration = `0:${String(displayedSeconds).padStart(2, '0')}`;
  const recordLabel = i18n('chat.audioMessageRecord', 'Record a voice message');
  const stopLabel = i18n('chat.audioMessageStop', 'Stop and send');
  const sendLabel = i18n('chat.audioMessageSend', 'Send recording');
  const cancelLabel = phase === 'failed'
    ? i18n('chat.audioMessageClosePreview', 'Close preview')
    : i18n('chat.audioMessageCancel', 'Discard recording');
  const playbackLabel = playing ? i18n('common.pause', 'Pause') : i18n('chat.audioMessagePreview', 'Preview recording');
  return <div className="audio-message-composer" data-compact-no-drag="true"
    data-audio-message-active={active ? 'true' : undefined}
    data-compact-hit-region="true" data-compact-geometry-item="audio-message"
    onPointerDown={event => event.stopPropagation()}>
    {phase === 'idle' ? <button type="button" className="composer-tool-btn audio-message-mic"
      aria-label={recordLabel} title={recordLabel}
      disabled={disabled} onClick={() => { void start(); }}>
      <span className="audio-message-mic-glyph" aria-hidden="true" />
    </button> : <div className="audio-message-strip" data-recording={phase === 'recording' || undefined}
      data-audio-message-failed={phase === 'failed' || undefined}>
      {phase === 'recording' ? (
        <>
          <span className="audio-message-clock">
            <span className="audio-message-recording-dot" aria-hidden="true" />
            {duration}
          </span>
          <span className="audio-message-track" aria-hidden="true">
            <span style={{ inlineSize: `${elapsed / RECORDING_LIMIT_SECONDS * 100}%` }} />
          </span>
        </>
      ) : null}
      {phase === 'starting' ? <span className="audio-message-state" role="status">{i18n('chat.audioMessagePreparing', 'Preparing…')}</span> : null}
      {preview ? <>
        <audio ref={audio} src={preview} preload="metadata" hidden
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)} onError={() => setPlaying(false)} />
        <button type="button" className="audio-message-play" aria-label={playbackLabel}
          data-playing={playing} disabled={phase === 'sending'} onClick={togglePlayback}>
          <span className="audio-message-play-icon" aria-hidden="true" />
          {duration}
        </button>
      </> : null}
      {phase === 'sending' ? <span className="audio-message-state" role="status">{i18n('chat.audioMessageSending', 'Sending…')}</span> : null}
      {phase !== 'sending' ? <button type="button" className="audio-message-cancel" onClick={cancel}
        aria-label={cancelLabel} title={cancelLabel} /> : null}
      {phase === 'recording' ? <button type="button" className="audio-message-stop"
        aria-label={stopLabel} title={stopLabel} onClick={() => { void stop(true); }}>
        <span className="audio-message-stop-icon" aria-hidden="true" />
        <span className="audio-message-stop-label">{stopLabel}</span>
      </button> : null}
      {phase === 'preview' ? <button type="button" className="audio-message-send" disabled={disabled}
        aria-label={sendLabel} title={sendLabel} onClick={() => { void submit(); }}>
        <span className="audio-message-send-label">{i18n('chat.send', 'Send')}</span>
      </button> : null}
    </div>}
    <span className="audio-message-announcement" role="alert">{error}</span>
  </div>;
}

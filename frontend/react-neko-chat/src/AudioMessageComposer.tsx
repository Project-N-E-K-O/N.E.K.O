import { useEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import {
  AUDIO_SAMPLE_RATE, recordAudio, recordingBase64, recordingWav,
  type AudioMessagePayload, type Recording,
} from './audioRecording';

type Props = {
  target: string;
  disabled: boolean;
  send: (payload: AudioMessagePayload) => Promise<unknown>;
  onBusyChange: (busy: boolean) => void;
};

export default function AudioMessageComposer({ target, disabled, send, onBusyChange }: Props) {
  const [phase, setPhase] = useState<'idle' | 'starting' | 'recording' | 'preview' | 'sending' | 'failed'>('idle');
  const [preview, setPreview] = useState('');
  const [playing, setPlaying] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState('');
  const audio = useRef<HTMLAudioElement>(null);
  const generation = useRef(0);
  const recording = useRef<Recording>();
  const pcm = useRef<Int16Array>();
  const url = useRef('');
  const messageId = useRef('');
  const submitted = useRef(false);
  const timer = useRef<ReturnType<typeof setInterval>>();
  const limitTimer = useRef<ReturnType<typeof setTimeout>>();

  const dispose = () => {
    generation.current += 1;
    recording.current?.cancel();
    recording.current = undefined;
    clearInterval(timer.current);
    clearTimeout(limitTimer.current);
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = '';
    pcm.current = undefined;
  };
  useEffect(() => {
    cancel();
    return () => { dispose(); onBusyChange(false); };
  }, [target]);
  useEffect(() => {
    if (disabled && phase !== 'idle' && phase !== 'sending') cancel();
  }, [disabled]);

  function cancel() {
    dispose();
    submitted.current = false;
    setPhase('idle');
    setPreview('');
    setPlaying(false);
    setError('');
    onBusyChange(false);
  }

  async function stop(sendImmediately: boolean) {
    const current = recording.current;
    if (!current) return;
    recording.current = undefined;
    const own = generation.current;
    clearInterval(timer.current);
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
    setSeconds(samples.length / AUDIO_SAMPLE_RATE);
    if (sendImmediately) {
      await submit();
    } else {
      setPhase('preview');
    }
  }

  async function start() {
    if (disabled || phase !== 'idle') return;
    if (globalThis.isSecureContext === false) {
      setError(i18n('instanceAccess.httpsRequired', 'Use HTTPS or a trusted HTTPS tunnel to connect securely.'));
      return;
    }
    const own = ++generation.current;
    try {
      messageId.current = `${Date.now()}-${crypto.randomUUID()}`;
      setError('');
      setSeconds(0);
      setPhase('starting');
      onBusyChange(true);
      submitted.current = false;
      const capture = await recordAudio(() => { void stop(false); }, () => own === generation.current);
      if (own !== generation.current) { capture.cancel(); return; }
      recording.current = capture;
      const beganAt = Date.now();
      setPhase('recording');
      timer.current = setInterval(() => setSeconds(Math.min(30, (Date.now() - beganAt) / 1000)), 250);
      limitTimer.current = setTimeout(() => { void stop(false); }, 30000);
    } catch {
      if (own !== generation.current) return;
      dispose();
      onBusyChange(false);
      setPhase('idle');
      setError(i18n('chat.audioMessageMicrophoneError', 'Microphone unavailable. Check its permission and try again.'));
    }
  }

  async function submit() {
    if (submitted.current || !pcm.current) return;
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
        setError(failure.message === 'storage'
          ? i18n('chat.audioHistoryStorageError', 'The recording could not be saved. Try again.')
          : i18n('chat.audioMessageBusy', 'The conversation is busy. Try sending the recording again when it is ready.'));
        return;
      }
      setPhase('failed');
      setError(i18n('chat.audioMessageSendError', 'Delivery could not be confirmed. Check the conversation before recording again.'));
      onBusyChange(false);
    }
  }

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

  const displayedSeconds = preview ? Math.ceil(seconds) : Math.floor(seconds);
  const duration = `0:${String(displayedSeconds).padStart(2, '0')}`;
  const playbackLabel = playing ? i18n('common.pause', 'Pause') : i18n('chat.audioMessagePreview', 'Preview recording');
  return <div className={`audio-message-composer${phase === 'idle' ? '' : ' is-open'}`} data-compact-no-drag="true"
    data-compact-hit-region="true" data-compact-geometry-item="audio-message"
    onPointerDown={event => event.stopPropagation()}>
    {phase === 'idle' ? <button type="button" className="composer-tool-btn audio-message-mic"
      aria-label={i18n('chat.audioMessageRecord', 'Record a voice message')}
      title={i18n('chat.audioMessageRecord', 'Record a voice message')}
      disabled={disabled} onClick={() => { void start(); }}>
      <img src="/static/icons/mic_icon_off.png" alt="" aria-hidden="true" />
    </button> : <div className="audio-message-strip" data-recording={phase === 'recording' || undefined}>
      {phase === 'recording' ? (
        <span className="audio-message-clock">
          <span className="audio-message-recording-dot" aria-hidden="true" />
          {duration}
        </span>
      ) : null}
      {phase === 'starting' ? <span role="status">{i18n('chat.audioMessagePreparing', 'Preparing…')}</span> : null}
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
      {phase === 'sending' ? <span role="status">{i18n('chat.audioMessageSending', 'Sending…')}</span> : null}
      {phase !== 'sending' ? <button type="button" className="audio-message-cancel" onClick={cancel}
        aria-label={i18n('chat.audioMessageCancel', 'Discard recording')}>{i18n('common.cancel', 'Cancel')}</button> : null}
      {phase === 'recording' ? <button type="button" className="audio-message-stop" onClick={() => { void stop(true); }}>
        <span className="audio-message-stop-disc" aria-hidden="true" />
        <span>{i18n('chat.audioMessageStop', 'Stop and send')}</span>
      </button> : null}
      {phase === 'preview' ? <button type="button" className="audio-message-send"
        aria-label={i18n('chat.audioMessageSend', 'Send recording')} onClick={() => { void submit(); }}>
        {i18n('chat.send', 'Send')}
      </button> : null}
    </div>}
    {error ? <span className="audio-message-error" role="alert">{error}</span> : null}
  </div>;
}

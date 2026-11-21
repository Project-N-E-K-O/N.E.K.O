import './message-voice-replay.css';

export function VoiceReplayIcon({ playing = false }: { playing?: boolean }) {
  return <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {playing ? <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />
      : <><path d="M11 5 6 9H3v6h3l5 4Z" /><path d="M15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14" /></>}
  </svg>;
}

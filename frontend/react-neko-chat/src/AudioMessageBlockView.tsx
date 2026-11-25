import { useEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import type { MessageBlock } from './message-schema';

type Props = {
  block: Extract<MessageBlock, { type: 'audio_message' }>;
  interactive: boolean;
};

// Recordings are not stored. A block only plays while its host still holds an
// in-session audioUrl; otherwise it shows the duration and transcript.
export default function AudioMessageBlockView({ block, interactive }: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [error, setError] = useState('');
  const seconds = Math.ceil(block.durationMs / 1000);
  const duration = `0:${String(seconds).padStart(2, '0')}`;
  const title = i18n('chat.audioHistoryMessage', 'Voice message');
  const playLabel = playing ? i18n('common.pause', 'Pause') : i18n('chat.audioMessagePreview', 'Play recording');
  const url = block.audioUrl;
  useEffect(() => {
    const player = audio.current;
    setProgress(0);
    return () => {
      if (player?.hasAttribute('src')) {
        player.pause();
        player.removeAttribute('src');
      }
    };
  }, [url, interactive]);

  function togglePlayback() {
    const player = audio.current;
    if (!player || !url) return;
    setError('');
    if (!player.paused) {
      player.pause();
      return;
    }
    if (!player.getAttribute('src')) player.src = url;
    void player.play().catch(() => {
      setPlaying(false);
      setError(i18n('chat.audioHistoryPlaybackError', 'Could not play this recording.'));
    });
  }

  if (!interactive) return <span>{title} {duration}</span>;
  return (
    <div className="message-audio-block" data-compact-hit-region="true" data-compact-no-drag="true"
      onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false);
      }}
      onKeyDown={event => {
        if (event.key !== 'Escape' || !menuOpen) return;
        event.stopPropagation();
        setMenuOpen(false);
        moreButton.current?.focus();
      }}>
      <div className="message-audio-controls">
        <audio ref={audio} preload="none" hidden
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onTimeUpdate={({ currentTarget: { currentTime, duration } }) => {
            if (duration > 0 && Number.isFinite(duration)) setProgress(Math.min(currentTime / duration, 1));
          }}
          onEnded={() => { setPlaying(false); setProgress(0); }} />
        <button type="button" className="message-audio-play" disabled={!url}
          data-playing={playing}
          aria-label={url ? playLabel : `${title} ${duration}`}
          onClick={togglePlayback}>
          <span className="message-audio-play-disc" data-playing={playing} aria-hidden="true">
            <span className="audio-message-play-icon" />
          </span>
          <span className="audio-message-track" aria-hidden="true">
            <span style={{ inlineSize: `${progress * 100}%` }} />
          </span>
          <span className="message-audio-duration">{duration}</span>
        </button>
        {block.transcript && <button ref={moreButton} type="button" className="message-audio-more"
          aria-label={i18n('chat.audioHistoryActions', 'Recording actions')}
          aria-expanded={menuOpen} onClick={() => setMenuOpen(open => !open)}>
          <span aria-hidden="true">⋯</span>
        </button>}
      </div>
      {menuOpen && block.transcript && <div className="message-audio-menu">
        <button type="button" data-audio-action="transcript"
          aria-expanded={transcriptOpen} onClick={() => {
            setTranscriptOpen(open => !open);
            setMenuOpen(false);
            moreButton.current?.focus();
          }}>
          {i18n('chat.audioHistoryTranscript', 'Transcript')}
        </button>
      </div>}
      {transcriptOpen && block.transcript && <p className="message-audio-transcript">{block.transcript}</p>}
      {error && <span role="alert">{error}</span>}
    </div>
  );
}

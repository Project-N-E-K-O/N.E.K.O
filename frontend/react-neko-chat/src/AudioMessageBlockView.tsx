import { useEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import type { MessageBlock } from './message-schema';
import { chatAudioUrl } from './useChatAudioHistory';

type Props = {
  block: Extract<MessageBlock, { type: 'audio_message' }>;
  interactive: boolean;
};

export default function AudioMessageBlockView({ block, interactive }: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const [playing, setPlaying] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const seconds = Math.ceil(block.durationMs / 1000);
  const duration = `0:${String(seconds).padStart(2, '0')}`;
  const title = i18n('chat.audioHistoryMessage', 'Voice message');
  const url = chatAudioUrl(block.character, block.requestId);
  useEffect(() => {
    const player = audio.current;
    return () => {
      if (player?.hasAttribute('src')) {
        player.pause();
        player.removeAttribute('src');
      }
    };
  }, [url, interactive]);

  function togglePlayback() {
    const player = audio.current;
    if (!player) return;
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

  async function remove(all: boolean) {
    if (deleting) return;
    const confirmation = all
      ? i18n('chat.audioHistoryClearConfirm', 'Delete all local recordings for this character?')
      : i18n('chat.audioHistoryDeleteConfirm', 'Delete this recording from local history?');
    if (!window.confirm(confirmation)) return;
    setDeleting(true);
    setError('');
    try {
      const result = await fetch(all ? chatAudioUrl(block.character) : url, { method: 'DELETE' });
      if (!result.ok) throw new Error('Could not delete recording');
      if (audio.current && !audio.current.paused) audio.current.pause();
      window.dispatchEvent(new Event('chat-audio-local-change'));
    } catch {
      setError(i18n('chat.audioHistoryDeleteError', 'Could not delete the recording.'));
      setDeleting(false);
    }
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
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
        <button type="button" className="message-audio-play" disabled={!block.available}
          data-playing={playing}
          aria-label={playing ? i18n('common.pause', 'Pause') : i18n('chat.audioMessagePreview', 'Play recording')}
          onClick={togglePlayback}>
          <span className="message-audio-play-disc" data-playing={playing} aria-hidden="true">
            <span className="audio-message-play-icon" />
          </span>
          <span>{duration}</span>
        </button>
        {block.available && <button ref={moreButton} type="button" className="message-audio-more"
          aria-label={i18n('chat.audioHistoryActions', 'Recording actions')}
          aria-expanded={menuOpen} onClick={() => setMenuOpen(open => !open)}>
          <span aria-hidden="true">⋯</span>
        </button>}
      </div>
      {menuOpen && <div className="message-audio-menu">
        {block.transcript && <button type="button" data-audio-action="transcript"
          aria-expanded={transcriptOpen} onClick={() => {
            setTranscriptOpen(open => !open);
            setMenuOpen(false);
            moreButton.current?.focus();
          }}>
          {i18n('chat.audioHistoryTranscript', 'Transcript')}
        </button>}
        <button type="button" data-audio-action="delete" disabled={deleting} onClick={() => { void remove(false); }}>
          {i18n('chat.audioHistoryDelete', 'Delete recording')}
        </button>
        <button type="button" data-audio-action="clear" disabled={deleting} onClick={() => { void remove(true); }}>
          {i18n('chat.audioHistoryClear', 'Clear recordings')}
        </button>
      </div>}
      {transcriptOpen && block.transcript && <p className="message-audio-transcript">{block.transcript}</p>}
      {block.state === 'sending' && <span className="message-audio-status" role="status">
        {i18n('chat.audioMessageSending', 'Sending…')}
      </span>}
      {block.state === 'unconfirmed' && <span className="message-audio-status">
        {i18n('chat.audioHistoryUnconfirmed', 'Delivery unconfirmed')}
      </span>}
      {block.canLoadOlder && <button type="button" onClick={() => window.dispatchEvent(
        new CustomEvent('chat-audio-load-older', { detail: block.character }),
      )}>
        {i18n('chat.audioHistoryOlder', 'Load older recordings')}
      </button>}
      {error && <span role="alert">{error}</span>}
    </div>
  );
}

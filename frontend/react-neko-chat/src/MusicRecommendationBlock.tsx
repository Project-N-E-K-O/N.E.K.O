import { useRecommendationExposure } from './useRecommendationExposure';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { i18n } from './i18n';
import RecommendationIcon from './RecommendationIcon';
import type { ChatMessage, MessageAction, MessageBlock } from './message-schema';

type MusicBlock = Extract<MessageBlock, { type: 'music_recommendation' }>;
const fallbackCover = '/static/assets/music/music-cover-placeholder.png';

export default function MusicRecommendationBlock({ block, message, interactive, onAction, presentation = 'card' }: {
  block: MusicBlock;
  message: ChatMessage;
  interactive: boolean;
  presentation?: 'card' | 'bubble';
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const root = useRecommendationExposure(message, block.recommendationId, interactive, 'music_recommendation_shown', onAction);
  const [now, setNow] = useState(Date.now);
  const deadline = interactive && block.state === 'pending' && !block.error ? block.autoPlayAt || 0 : 0;
  useEffect(() => {
    if (!deadline) return;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      const current = Date.now();
      setNow(current);
      if (current < deadline) timer = setTimeout(tick, Math.min(1000, deadline - current));
    };
    tick();
    return () => clearTimeout(timer);
  }, [deadline]);
  const seconds = Math.max(0, Math.ceil((deadline - now) / 1000));
  const countdownDelay = useMemo(() => `${Math.min(0, (deadline - Date.now() - 10000) / 1000)}s`, [deadline]);

  const choose = (kind: 'accepted' | 'skipped') => onAction?.(message, {
    id: kind, action: 'music_recommendation_decision', label: kind,
    payload: { recommendationId: block.recommendationId, kind },
  });
  const status = {
    pending: i18n('music.recommendation.pending', 'Would you like to listen?'),
    saving: i18n('music.recommendation.saving', 'Saving your choice…'),
    accepted: i18n('music.recommendation.accepted', 'Preparing playback…'),
    skipped: i18n('music.recommendation.skipped', 'Skipped this time'),
    playing: i18n('music.recommendation.playing', 'Playing'),
    paused: i18n('music.recommendation.paused', 'Paused'),
    ended: i18n('music.recommendation.ended', 'Finished listening'),
    stopped: i18n('music.recommendation.stopped', 'Playback stopped'),
    failed: i18n('music.recommendation.failed', 'Playback failed'),
    unavailable: i18n('music.recommendation.unavailable', 'This recommendation is no longer available'),
  }[block.state];
  return <div ref={root} className={`message-block music-recommendation music-recommendation-${presentation}`} data-recommendation-id={block.recommendationId}>
    <div className="music-recommendation-track">
      {presentation === 'card' ? <img src={block.track.cover || fallbackCover} alt="" loading="lazy" referrerPolicy="no-referrer"
        onError={event => { if (!event.currentTarget.src.endsWith(fallbackCover)) event.currentTarget.src = fallbackCover; }} /> : ['pending', 'saving'].includes(block.state) ? (
        <span className="music-recommendation-icon neko-recommendation-icon" aria-hidden="true">
          <RecommendationIcon kind="music" />
        </span>
      ) : null}
      <div>{presentation === 'card' ? <div className="music-recommendation-label">{i18n('music.recommendation.title', 'Music recommendation')}</div> : null}
        <strong>{block.track.name || i18n('music.unknownTrack', 'Unknown Track')}</strong>
        <div>{block.track.artist || i18n('music.unknownArtist', 'Unknown Artist')}</div></div>
    </div>
    <div role="status">{block.error ? i18n('music.recommendation.saveFailed', 'Could not save your choice. Please try again.') : status}</div>
    {interactive && block.state === 'pending' ? <div className="message-block-buttons">
      <button type="button" className="message-action-button variant-primary" onClick={() => choose('accepted')}>
        √ {i18n('music.recommendation.accept', 'Listen')}
      </button>
      <button type="button" className="message-action-button variant-secondary" onClick={() => choose('skipped')}>
        × {i18n('music.recommendation.skip', 'Not now')}
      </button>
    </div> : null}
    {deadline > 0 && seconds > 0 ? <div className="music-recommendation-countdown">
      <span>{i18n('music.recommendation.autoPlayCountdown', 'Plays automatically in {{seconds}} s', { seconds: String(seconds) })}</span>
      <div className="music-recommendation-countdown-track" role="progressbar"
        aria-label={i18n('music.recommendation.autoPlayProgress', 'Time until automatic playback')}
        aria-valuemin={0} aria-valuemax={10} aria-valuenow={seconds}>
        <div key={deadline} className="music-recommendation-countdown-fill" style={{
          '--countdown-duration': '10s', '--countdown-delay': countdownDelay,
        } as CSSProperties} />
      </div>
    </div> : null}
  </div>;
}

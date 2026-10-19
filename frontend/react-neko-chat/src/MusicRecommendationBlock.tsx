import { useRecommendationExposure } from './useRecommendationExposure';
import { i18n } from './i18n';
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
        onError={event => { if (!event.currentTarget.src.endsWith(fallbackCover)) event.currentTarget.src = fallbackCover; }} /> : null}
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
  </div>;
}

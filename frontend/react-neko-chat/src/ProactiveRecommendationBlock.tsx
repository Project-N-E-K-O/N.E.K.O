import { i18n } from './i18n';
import type { ReactNode } from 'react';
import { normalizeExternalUrlHref, openExternalUrl } from './openExternal';
import MessageImageBlock from './MessageImageBlock';
import RecommendationIcon from './RecommendationIcon';
import { useRecommendationExposure } from './useRecommendationExposure';
import { hasProactiveRecommendationContent } from './message-schema';
import type { ChatMessage, MessageAction, MessageBlock } from './message-schema';

type ProactiveBlock = Extract<MessageBlock, { type: 'proactive_recommendation' }>;

const icons: Record<ProactiveBlock['category'], ReactNode> = {
  news: <RecommendationIcon kind="news" />,
  community: <><path d="M7 10a4 4 0 1 1 7 2M4 20v-2a5 5 0 0 1 9-3M17 13v6m-3-3h6" /></>,
  video: <RecommendationIcon kind="video" />,
  personal: <RecommendationIcon kind="personal" />,
  meme: null,
  vision: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>,
  window: <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M3 9h18M7 6.5h.01M10 6.5h.01" /></>,
  chat: <path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z" />,
  web: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></>,
  work_break: <><path d="M4 9h13v6a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5Zm13 1h1a3 3 0 1 1 0 6h-1M8 3v3m5-3v3" /></>,
  anti_slack: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
};

export default function ProactiveRecommendationBlock({ block, message, interactive, onAction, presentation = 'card' }: {
  block: ProactiveBlock; message: ChatMessage; interactive: boolean; presentation?: 'card' | 'bubble';
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const visible = hasProactiveRecommendationContent(block);
  const root = useRecommendationExposure(message, block.recommendationId, interactive && visible, 'proactive_recommendation_shown', onAction);
  if (!visible) return null;
  const category = i18n(`proactiveCard.categories.${block.category}`, 'A thought for you');
  const url = normalizeExternalUrlHref(block.content.url);
  const isMeme = block.category === 'meme';
  const usesNekoIcon = ['news', 'video', 'personal'].includes(block.category);
  const isScreenObservation = block.category === 'vision' && !block.content.url;
  const direct = !!block.directDelivery && (isMeme || isScreenObservation);
  const showMeme = isMeme && (block.state === 'accepted' || (direct && block.state === 'pending'));
  const generatedMemeTitle = isMeme && /^(?:表情(?:包)?|meme|gif|image)[_\s-]*\d+(?:\.(?:gif|png|jpe?g|webp))?$/i.test(block.content.title.trim());
  const title = generatedMemeTitle ? i18n('proactiveCard.memeTitle', 'A meme I wanted to share with you')
    : block.content.title || i18n(`proactiveCard.hints.${block.category}`, 'Would you like to take a look?');
  const choose = (kind: 'accepted' | 'skipped') => onAction?.(message, {
    id: kind, action: 'proactive_recommendation_decision', label: kind,
    payload: { recommendationId: block.recommendationId, kind },
  });
  const status = block.state === 'saving' ? i18n('proactiveCard.saving', 'Saving your choice…')
    : block.state === 'skipped' ? i18n('proactiveCard.skipped', 'Skipped this time')
      : block.state === 'accepted' ? i18n(isScreenObservation ? 'proactiveCard.interestSaved' : 'proactiveCard.accepted', isScreenObservation ? 'Interest saved' : 'Choice saved')
        : block.state === 'unavailable' ? i18n('proactiveCard.unavailable', 'This recommendation is no longer available') : '';
  return <div ref={root} className={`proactive-recommendation proactive-recommendation-${presentation}`}
    data-recommendation-id={block.recommendationId} data-proactive-category={block.category}>
    {direct && isScreenObservation ? <p className="proactive-direct-observation">{title}</p> : <header className="proactive-recommendation-heading">
      {!isMeme ? <span className={`proactive-recommendation-icon${usesNekoIcon ? ' neko-recommendation-icon' : ''}`} aria-hidden="true">{usesNekoIcon ? icons[block.category] : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">{icons[block.category]}</svg>}</span> : null}
      <div><div className="proactive-recommendation-meta"><span className="proactive-recommendation-category">{category}</span>
        {block.content.source ? <><span aria-hidden="true">·</span><small title={block.content.source}>{block.content.source}</small></> : null}</div>
        <strong>{title}</strong>
      </div>
    </header>}
    {showMeme && url && !block.dismissed ? <MessageImageBlock
      block={{ type: 'image', url: `/api/meme/proxy-image?url=${encodeURIComponent(url)}`, alt: title }}
      interactive={interactive} /> : null}
    {showMeme ? <button type="button" className="message-action-button proactive-recommendation-preview-toggle" disabled={!interactive}
      onClick={() => onAction?.(message, { id: 'preview', action: block.dismissed ? 'proactive_recommendation_reveal' : 'proactive_recommendation_dismiss', label: 'preview', payload: { recommendationId: block.recommendationId } })}>
      {block.dismissed ? i18n('proactiveCard.reveal', 'Show me') : i18n('proactiveCard.dismiss', 'Close preview')}</button> : null}
    {status && !direct ? <div role="status">{status}</div> : null}
    {block.error ? <div role="alert">{i18n(block.state === 'accepted' ? 'proactiveCard.openFailed' : 'proactiveCard.saveFailed',
      block.state === 'accepted' ? 'Could not open the content. Use the link below to try again.' : 'Could not save. Please try again.')}</div> : null}
    {!direct && (block.state === 'pending' || block.state === 'saving') ? <div className="proactive-recommendation-actions">
      <button type="button" className="message-action-button proactive-recommendation-action proactive-recommendation-accept" disabled={!interactive || block.state === 'saving'}
        onClick={() => choose('accepted')}>
        <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="m3.5 8 3 3 6-6" /></svg>{i18n(isScreenObservation ? 'proactiveCard.interested' : isMeme ? 'proactiveCard.reveal' : url ? 'proactiveCard.view' : 'proactiveCard.acknowledge', isScreenObservation ? 'Interested' : isMeme ? 'Show me' : url ? 'View' : 'Got it')}
      </button>
      <button type="button" className="message-action-button proactive-recommendation-action proactive-recommendation-skip" disabled={!interactive || block.state === 'saving'}
        onClick={() => choose('skipped')}><svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="m5 5 6 6m0-6-6 6" /></svg>{i18n('proactiveCard.skip', 'Skip')}</button>
    </div> : null}
    {block.state === 'accepted' && !isMeme && url ? <a className="proactive-recommendation-link" href={url}
      target="_blank" rel="noreferrer" onClick={event => { event.preventDefault(); if (interactive) openExternalUrl(url); }}
      aria-disabled={!interactive} tabIndex={interactive ? 0 : -1}>{i18n('proactiveCard.view', 'View')}</a> : null}
  </div>;
}

import { useEffect, useRef } from 'react';
import type { ChatMessage, MessageAction } from './message-schema';

export function useRecommendationExposure(message: ChatMessage, recommendationId: string, interactive: boolean,
  action: string, onAction?: (message: ChatMessage, action: MessageAction) => void) {
  const root = useRef<HTMLDivElement>(null);
  const actionRef = useRef(onAction);
  actionRef.current = onAction;
  useEffect(() => {
    if (!interactive || !root.current) return;
    let shown = false;
    let inView = typeof IntersectionObserver === 'undefined';
    const report = () => {
      if (shown || !inView || document.visibilityState === 'hidden') return;
      shown = true;
      actionRef.current?.(message, { id: 'shown', action, label: 'shown', payload: { recommendationId } });
    };
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      inView = entries.some(entry => entry.isIntersecting);
      report();
    });
    observer?.observe(root.current);
    document.addEventListener('visibilitychange', report);
    report();
    return () => { observer?.disconnect(); document.removeEventListener('visibilitychange', report); };
  // Exposure identity survives status updates and surface remounts; the backend deduplicates mirrors.
  }, [recommendationId, interactive, message.id, action]);
  return root;
}

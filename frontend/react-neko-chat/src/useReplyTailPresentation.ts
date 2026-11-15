import { useLayoutEffect } from 'react';
import type { ChatMessage } from './message-schema';

declare global {
  interface Window {
    __nekoReplyTailPresentationVersion?: number;
  }
}

export function useReplyTailPresentation(
  messages: ChatMessage[],
  compact: boolean,
  turnId: string | undefined,
  ended: boolean,
  fullText: string,
  visibleText: string,
  historyVisible: boolean,
  enabled = true,
) {
  // The layout effect observes the actual committed caption, including fallback
  // reveal after model completion; generation end alone is not this condition.
  useLayoutEffect(() => {
    if (!enabled) return;
    window.__nekoReplyTailPresentationVersion = 1;
    window.dispatchEvent(new CustomEvent('neko-reply-tail-presentation', {
      detail: {
        compact,
        turnId,
        complete: ended && !!fullText && visibleText === fullText,
      },
    }));
  }, [messages, compact, turnId, ended, fullText, visibleText, historyVisible, enabled]);
}

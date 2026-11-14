import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { i18n } from './i18n';
import { hasVisibleMessageContent, type ChatMessage } from './message-schema';

const EMPTY_HIDDEN_IDS: ReadonlySet<string> = new Set();
const ChatScreenVisibilityContext = createContext({
  hiddenMessageIds: EMPTY_HIDDEN_IDS,
  isMessageHidden: (_message: ChatMessage): boolean => false,
  canClearScreen: false,
  audioHistoryPaginationBlocked: false,
  clearScreen: () => {},
});

const isRecommendationMessage = (message: ChatMessage) => message.blocks.some(
  block => block.type === 'music_recommendation' || block.type === 'proactive_recommendation',
);

// Realistic mode gives every sentence of one reply its own message ID, so a reply that is
// still arriving when the screen is cleared must also be hidden by its turn. Recommendation
// cards stay ID-only: a later card may carry a countdown the user still needs to answer.
const isHiddenMessage = (
  message: ChatMessage,
  hiddenIds: ReadonlySet<string>,
  hiddenTurnIds: ReadonlySet<string>,
) => hiddenIds.has(message.id)
  || (message.role === 'assistant' && !!message.turnId && hiddenTurnIds.has(message.turnId)
    && !isRecommendationMessage(message));

export function ChatScreenVisibilityProvider({ messages, audioHistoryTarget, oldestRecordingTime, children }: {
  messages: ChatMessage[];
  audioHistoryTarget?: string;
  oldestRecordingTime?: number;
  children: ReactNode;
}) {
  const captionTurnRef = useRef('');
  const [hiddenMessageIds, setHiddenMessageIds] = useState<ReadonlySet<string>>(EMPTY_HIDDEN_IDS);
  const [hiddenTurnIds, setHiddenTurnIds] = useState<ReadonlySet<string>>(EMPTY_HIDDEN_IDS);
  const [audioHistoryClearedAt, setAudioHistoryClearedAt] = useState<ReadonlyMap<string, number>>(new Map());
  useEffect(() => {
    // A real host reset starts a new conversation; a visual clear never emits it.
    const reset = (event: Event) => {
      // Neither a history restore nor a sync refresh starts a new conversation, and
      // both keep every message ID, so a visual clear outlives them.
      const detail = (event as CustomEvent).detail;
      if (event.type === 'react-chat-window:messages-replaced' && detail?.conversationReset === false
        && (detail?.historyRestore === true || detail?.keepClearedScreen === true)) return;
      captionTurnRef.current = '';
      setHiddenMessageIds(EMPTY_HIDDEN_IDS);
      setHiddenTurnIds(EMPTY_HIDDEN_IDS);
      setAudioHistoryClearedAt(new Map());
    };
    const rememberCaptionTurn = (event: Event) => {
      const turnId = (event as CustomEvent).detail?.turnId;
      if (turnId) captionTurnRef.current = String(turnId);
    };
    window.addEventListener('react-chat-window:messages-cleared', reset);
    window.addEventListener('react-chat-window:set-messages', reset);
    window.addEventListener('react-chat-window:messages-replaced', reset);
    window.addEventListener('neko-assistant-turn-start', rememberCaptionTurn);
    window.addEventListener('neko-compact-caption-update', rememberCaptionTurn);
    return () => {
      window.removeEventListener('react-chat-window:messages-cleared', reset);
      window.removeEventListener('react-chat-window:set-messages', reset);
      window.removeEventListener('react-chat-window:messages-replaced', reset);
      window.removeEventListener('neko-assistant-turn-start', rememberCaptionTurn);
      window.removeEventListener('neko-compact-caption-update', rememberCaptionTurn);
    };
  }, []);
  const isMessageHidden = useCallback(
    (message: ChatMessage) => {
      if (isHiddenMessage(message, hiddenMessageIds, hiddenTurnIds)) return true;
      const { createdAt } = message;
      return typeof createdAt === 'number' && message.blocks.some(block =>
        block.type === 'audio_message' && createdAt <= (audioHistoryClearedAt.get(block.character) ?? -Infinity));
    },
    [audioHistoryClearedAt, hiddenMessageIds, hiddenTurnIds],
  );
  const clearedAt = audioHistoryTarget ? audioHistoryClearedAt.get(audioHistoryTarget) : undefined;
  const audioHistoryPaginationBlocked = clearedAt !== undefined
    && (oldestRecordingTime === undefined || oldestRecordingTime <= clearedAt);

  const value = useMemo(() => ({
    hiddenMessageIds,
    isMessageHidden,
    audioHistoryPaginationBlocked,
    canClearScreen: messages.some(message => !isMessageHidden(message) && hasVisibleMessageContent(message)),
    // Keep host messages intact, including streaming IDs and export metadata.
    clearScreen: () => {
      const captionTurnId = captionTurnRef.current;
      if (audioHistoryTarget) {
        const clearedAt = Date.now();
        setAudioHistoryClearedAt(previous => new Map(previous).set(audioHistoryTarget, clearedAt));
      }
      setHiddenMessageIds(previous => new Set([
        ...previous,
        ...messages.map(message => message.id),
        ...(captionTurnId ? [`compact-caption:${captionTurnId}`] : []),
      ]));
      setHiddenTurnIds(previous => new Set([
        ...previous,
        ...messages.flatMap(message => (message.role === 'assistant' && message.turnId ? [message.turnId] : [])),
        ...(captionTurnId ? [captionTurnId] : []),
      ]));
    },
  }), [audioHistoryPaginationBlocked, audioHistoryTarget, hiddenMessageIds, isMessageHidden, messages]);
  return <ChatScreenVisibilityContext.Provider value={value}>{children}</ChatScreenVisibilityContext.Provider>;
}

export const useChatScreenVisibility = () => useContext(ChatScreenVisibilityContext);

export function ClearChatScreenButton({ disabled = false }: { disabled?: boolean }) {
  const { canClearScreen, clearScreen } = useChatScreenVisibility();
  const [cleared, setCleared] = useState(false);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (feedbackTimer.current !== null) clearTimeout(feedbackTimer.current);
  }, []);
  const label = i18n('chat.clearScreen', 'Clear screen');
  const done = i18n('chat.clearScreenDone', 'Screen cleared');
  const hint = i18n('chat.clearScreenHint', 'Hide existing messages on this screen. Conversation history and memory are kept.');
  return (
    <>
      <button
        type="button"
        className="chat-clear-screen-button"
        disabled={disabled || !canClearScreen}
        aria-label={label}
        data-neko-tooltip={cleared ? done : `${label} · ${hint}`}
        data-cleared={cleared ? 'true' : undefined}
        data-compact-hit-region={!disabled ? 'true' : undefined}
        data-compact-hit-region-id="history:clear-screen"
        data-compact-hit-region-kind="controls"
        data-compact-no-drag="true"
        onClick={() => {
          clearScreen();
          setCleared(true);
          if (feedbackTimer.current !== null) clearTimeout(feedbackTimer.current);
          feedbackTimer.current = setTimeout(() => setCleared(false), 1000);
        }}
      >
        <svg className="chat-clear-screen-art" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <g className="chat-clear-screen-brush" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
            <path d="m14.5 4.5 5 5-9 9-5-5z" fill="currentColor" fillOpacity=".1" />
            <path d="m5.5 13.5-2 2a1.4 1.4 0 0 0 0 2l3 3h5l4-4M9 17l3 3M14.5 4.5l1-1a1.4 1.4 0 0 1 2 0l4 4a1.4 1.4 0 0 1 0 2l-2 2" />
          </g>
          <g className="chat-clear-screen-sparkles" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round">
            <path d="M5 3v4M3 5h4M20 17v4M18 19h4" />
          </g>
          <path className="chat-clear-screen-check" d="m6.5 12 3.5 3.5 7.5-8" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <span className="chat-clear-screen-announcement" role="status">{cleared ? done : ''}</span>
    </>
  );
}

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { i18n } from './i18n';
import { hasVisibleMessageContent, type ChatMessage } from './message-schema';

const EMPTY_HIDDEN_IDS: ReadonlySet<string> = new Set();
const ChatScreenVisibilityContext = createContext({
  hiddenMessageIds: EMPTY_HIDDEN_IDS,
  canClearScreen: false,
  clearScreen: () => {},
});

export function ChatScreenVisibilityProvider({ messages, children }: {
  messages: ChatMessage[];
  children: ReactNode;
}) {
  const [hiddenMessageIds, setHiddenMessageIds] = useState<ReadonlySet<string>>(EMPTY_HIDDEN_IDS);
  useEffect(() => {
    // A real host reset starts a new conversation; a visual clear never emits it.
    const reset = () => setHiddenMessageIds(EMPTY_HIDDEN_IDS);
    window.addEventListener('react-chat-window:messages-cleared', reset);
    return () => window.removeEventListener('react-chat-window:messages-cleared', reset);
  }, []);
  const value = useMemo(() => ({
    hiddenMessageIds,
    canClearScreen: messages.some(message => !hiddenMessageIds.has(message.id) && hasVisibleMessageContent(message)),
    // Keep host messages intact, including streaming IDs and export metadata.
    clearScreen: () => setHiddenMessageIds(previous => new Set([...previous, ...messages.map(message => message.id)])),
  }), [hiddenMessageIds, messages]);
  return <ChatScreenVisibilityContext.Provider value={value}>{children}</ChatScreenVisibilityContext.Provider>;
}

export const useChatScreenVisibility = () => useContext(ChatScreenVisibilityContext);

export function ClearChatScreenButton({ disabled = false }: { disabled?: boolean }) {
  const { canClearScreen, clearScreen } = useChatScreenVisibility();
  return (
    <button
      type="button"
      className="chat-clear-screen-button"
      disabled={disabled || !canClearScreen}
      title={i18n('chat.clearScreenHint', 'Hide existing messages on this screen. Conversation history and memory are kept.')}
      data-compact-hit-region={!disabled ? 'true' : undefined}
      data-compact-hit-region-id="history:clear-screen"
      data-compact-hit-region-kind="controls"
      data-compact-no-drag="true"
      onClick={clearScreen}
    >
      {i18n('chat.clearScreen', 'Clear screen')}
    </button>
  );
}

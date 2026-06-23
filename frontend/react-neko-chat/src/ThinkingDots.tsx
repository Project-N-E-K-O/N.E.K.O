import { i18n } from './i18n';

/**
 * Animated "…" thinking indicator shown at the tail of the chat history while a
 * Focus (凝神) turn is thinking-on but hasn't emitted visible content yet. Three
 * dots cycle one → two → three and back. Driven purely by CSS so it costs no
 * React re-renders; degrades to a static row under prefers-reduced-motion.
 */
export default function ThinkingDots() {
  return (
    <span
      className="focus-thinking-dots"
      role="status"
      aria-label={i18n('chat.focusThinking', '正在思考')}
    >
      <span className="focus-thinking-dot" aria-hidden="true" />
      <span className="focus-thinking-dot" aria-hidden="true" />
      <span className="focus-thinking-dot" aria-hidden="true" />
    </span>
  );
}

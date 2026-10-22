import { i18n } from './i18n';
import RecommendationIcon from './RecommendationIcon';
import type { ChoiceOption, ChoicePrompt } from './message-schema';

export default function MiniGameInvitePrompt({ prompt, presentation, disabled = false, onSelect }: {
  prompt: NonNullable<ChoicePrompt>;
  presentation: 'card' | 'bubble';
  disabled?: boolean;
  onSelect: (option: ChoiceOption) => void;
}) {
  return (
    <section className={`mini-game-invite mini-game-invite-${presentation}`}
      data-invite-session-id={prompt.sessionId} data-compact-no-drag="true"
      data-compact-hit-region={presentation === 'bubble' ? 'true' : undefined}
      data-compact-hit-region-id={presentation === 'bubble' ? `choice:mini-game:${prompt.sessionId || 'pending'}` : undefined}
      data-compact-hit-region-kind={presentation === 'bubble' ? 'choice' : undefined}
      aria-label={i18n('chat.miniGameInviteTitle', 'Game invitation')}>
      <header className="mini-game-invite-heading">
        <span className="mini-game-invite-icon neko-recommendation-icon" aria-hidden="true"><RecommendationIcon kind="game" /></span>
        <div>
          <strong>{i18n('chat.miniGameInviteTitle', 'Game invitation')}</strong>
          <p>{i18n('chat.miniGameInviteHint', 'Shall we play together?')}</p>
        </div>
      </header>
      <div className="mini-game-invite-actions" role="group"
        aria-label={i18n('chat.miniGameInviteOptionsAriaLabel', 'Mini-game invite options')}>
        {prompt.options.slice(0, 3).map(option => (
          <button key={option.choice} type="button" className="mini-game-invite-action"
            data-invite-choice={option.choice} aria-label={option.label} disabled={disabled}
            onClick={() => onSelect(option)}>
            <span aria-hidden="true">{({ accept: '√', decline: '×', later: '◷' } as Record<string, string>)[option.choice]}</span>
            <span>{option.label}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

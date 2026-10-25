import { fireEvent, render, screen } from '@testing-library/react';
import App from './App';
import type { ChoicePrompt } from './message-schema';

const prompt: NonNullable<ChoicePrompt> = {
  source: 'mini_game_invite', sessionId: 'invite-test', gameType: 'soccer',
  options: [
    { choice: 'accept', label: 'Play now' },
    { choice: 'decline', label: 'Skip' },
    { choice: 'later', label: 'Later' },
  ],
};

describe('mini-game invitation surfaces', () => {
  beforeEach(() => window.localStorage.clear());

  it('places one full card in the conversation, outside the composer, even without messages', () => {
    const { container } = render(<App chatSurfaceMode="full" messages={[]} choicePrompt={prompt} />);
    expect(container.querySelectorAll('.message-list .mini-game-invite-card')).toHaveLength(1);
    expect(container.querySelector('.composer-panel .mini-game-invite')).toBeNull();
    expect(container.querySelector('.composer-choice-slot.is-mini_game_invite')).toBeNull();
    expect(screen.getAllByRole('button', { name: /Play now|Skip|Later/ })).toHaveLength(3);
  });

  it('keeps one compact bubble with the same invitation when switching layouts', () => {
    const props = { messages: [], choicePrompt: prompt };
    const { rerender } = render(<App {...props} chatSurfaceMode="full" />);
    rerender(<App {...props} chatSurfaceMode="compact" />);
    const bubble = document.body.querySelector('.compact-chat-choice-anchor .mini-game-invite-bubble');
    expect(bubble).toHaveAttribute('data-invite-session-id', 'invite-test');
    expect(bubble).toHaveAttribute('data-compact-hit-region', 'true');
    expect(document.body.querySelector('.mini-game-invite-card')).toBeNull();
    rerender(<App {...props} chatSurfaceMode="full" />);
    expect(document.body.querySelector('.mini-game-invite-bubble')).toBeNull();
    expect(document.body.querySelector('.mini-game-invite-card')).toHaveAttribute('data-invite-session-id', 'invite-test');
  });

  it.each(['full', 'compact'] as const)('routes all three %s choices through the existing invitation callback', mode => {
    for (const option of prompt.options) {
      const onChoiceSelect = vi.fn();
      const { unmount } = render(<App chatSurfaceMode={mode} messages={[]} choicePrompt={prompt} onChoiceSelect={onChoiceSelect} />);
      fireEvent.click(screen.getByRole('button', { name: option.label }));
      fireEvent.click(screen.getByRole('button', { name: option.label }));
      expect(onChoiceSelect).toHaveBeenCalledExactlyOnceWith(option, 'mini_game_invite');
      unmount();
    }
  });

  it.each(['full', 'compact'] as const)('respects disabled state and resolved/failed prompt updates in %s', mode => {
    const onChoiceSelect = vi.fn();
    const { rerender } = render(<App chatSurfaceMode={mode} choicePrompt={prompt} composerDisabled onChoiceSelect={onChoiceSelect} />);
    for (const option of prompt.options) {
      expect(screen.getByRole('button', { name: option.label })).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: option.label }));
    }
    expect(onChoiceSelect).not.toHaveBeenCalled();
    rerender(<App chatSurfaceMode={mode} choicePrompt={null} />);
    expect(document.body.querySelector('.mini-game-invite')).toBeNull();
    rerender(<App chatSurfaceMode={mode} choicePrompt={prompt} onChoiceSelect={onChoiceSelect} />);
    expect(screen.getByRole('button', { name: 'Play now' })).toBeEnabled();
  });

  it.each(['full', 'compact', 'minimized'] as const)('does not expose game choices in restricted %s surfaces', mode => {
    render(<App chatSurfaceMode={mode} choicePrompt={prompt} catLocalTextOnly />);
    expect(document.body.querySelector('.mini-game-invite')).toBeNull();
  });

  it('keeps non-game choice prompts on their existing path', () => {
    render(<App chatSurfaceMode="full" choicePrompt={{ source: 'new_user_icebreaker', options: [{ choice: 'chat', label: 'Chat first' }] }} />);
    expect(document.body.querySelector('.mini-game-invite')).toBeNull();
    expect(screen.getByRole('button', { name: 'Chat first' })).toHaveClass('composer-choice-option');
  });
});

import { render } from '@testing-library/react';
import App from './App';
import { parseChatMessage } from './message-schema';

// #3474: frames sent to the model hide the chat history and reply-option
// regions marked with data-neko-capture-mask; the composer stays visible.
const messages = [
  parseChatMessage({
    id: 'capture-mask-assistant',
    role: 'assistant',
    author: 'Neko',
    time: '10:00',
    createdAt: 1,
    blocks: [{ type: 'text', text: 'Synthetic reply.' }],
    status: 'sent',
  }),
];
const galgameOptions = [
  { label: 'A', text: 'Synthetic option A' },
  { label: 'B', text: 'Synthetic option B' },
];

function markedRegions() {
  return Array.from(document.body.querySelectorAll<HTMLElement>('[data-neko-capture-mask]'));
}

function expectComposerUnmasked() {
  const inputs = Array.from(document.body.querySelectorAll<HTMLElement>('textarea, input[type="text"], .compact-chat-capsule-button'));
  expect(inputs.length).toBeGreaterThan(0);
  for (const input of inputs) {
    expect(input.closest('[data-neko-capture-mask]')).toBeNull();
  }
}

describe('capture mask markers', () => {
  it('full style: marks the message list and the Gal option slot, not the composer', () => {
    render(
      <App chatSurfaceMode="full" messages={messages} galgameModeEnabled galgameOptions={galgameOptions} />,
    );
    const regions = markedRegions();
    const history = regions.filter((el) => el.dataset.nekoCaptureMask === 'history');
    const choices = regions.filter((el) => el.dataset.nekoCaptureMask === 'choices');
    expect(history.length).toBeGreaterThan(0);
    expect(history.some((el) => el.classList.contains('message-list-shell'))).toBe(true);
    expect(choices).toHaveLength(1);
    expect(choices[0]).toHaveClass('composer-galgame-slot');
    expect(choices[0].textContent).toContain('Synthetic option A');
    expectComposerUnmasked();
  });

  it('full style with Gal mode off: no option slot is marked', () => {
    render(<App chatSurfaceMode="full" messages={messages} galgameModeEnabled={false} galgameOptions={galgameOptions} />);
    expect(markedRegions().filter((el) => el.dataset.nekoCaptureMask === 'choices')).toHaveLength(0);
    expect(markedRegions().filter((el) => el.dataset.nekoCaptureMask === 'history').length).toBeGreaterThan(0);
  });

  it('compact style: marks the Gal option slot, not the composer', () => {
    render(<App chatSurfaceMode="compact" messages={messages} galgameModeEnabled galgameOptions={galgameOptions} />);
    const choices = markedRegions().filter((el) => el.dataset.nekoCaptureMask === 'choices');
    expect(choices).toHaveLength(1);
    expect(choices[0]).toHaveClass('composer-galgame-slot');
    expectComposerUnmasked();
  });

  it('compact style with Gal mode off: no option slot is marked', () => {
    render(<App chatSurfaceMode="compact" messages={messages} galgameModeEnabled={false} galgameOptions={galgameOptions} />);
    expect(markedRegions().filter((el) => el.dataset.nekoCaptureMask === 'choices')).toHaveLength(0);
  });
});

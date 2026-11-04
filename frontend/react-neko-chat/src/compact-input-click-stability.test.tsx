import { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import App from './App';
import type { CompactChatState } from './message-schema';

/**
 * 紧凑输入态点击稳定性。
 *
 * 窗口失焦立即折叠空输入。输入窗口自己重画命中区域时不再抢走焦点，所以这里不再
 * 用一段宽限去吸收瞬时 blur。Escape、岛外按下、工具扇展开中的守卫保持不变。
 */

function renderControlledCompactInput() {
  const stateChanges: CompactChatState[] = [];
  function Harness() {
    const [compactChatState, setCompactChatState] = useState<CompactChatState>('input');
    return (
      <App
        chatSurfaceMode="compact"
        compactChatState={compactChatState}
        onCompactChatStateChange={(next) => {
          stateChanges.push(next);
          setCompactChatState(next);
        }}
      />
    );
  }
  render(<Harness />);
  return stateChanges;
}

async function renderFocusedCompactInput() {
  const stateChanges = renderControlledCompactInput();
  const input = (await screen.findByPlaceholderText('Type a message...')) as HTMLTextAreaElement;
  await act(async () => {
    input.focus();
  });
  expect(input).toHaveFocus();
  return { stateChanges, input };
}

describe('compact input state click stability', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps input state for a plain click on the focused textarea', async () => {
    const { stateChanges, input } = await renderFocusedCompactInput();

    fireEvent.pointerDown(input, { pointerId: 7, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.mouseDown(input, { button: 0, buttons: 1 });
    fireEvent.pointerUp(input, { pointerId: 7, button: 0, buttons: 0, pointerType: 'mouse' });
    fireEvent.mouseUp(input, { button: 0, buttons: 0 });
    fireEvent.click(input, { button: 0 });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(stateChanges).toEqual([]);
  });

  it('collapses empty input as soon as the window blurs', async () => {
    const { stateChanges } = await renderFocusedCompactInput();
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);

    fireEvent.blur(window);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(stateChanges).toEqual(['default']);
  });

  it('keeps empty input when the blur stays inside the document', async () => {
    const { stateChanges } = await renderFocusedCompactInput();
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);

    fireEvent.blur(window);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(stateChanges).toEqual([]);
  });

  it('does not collapse after unmount if the window has already blurred', async () => {
    const stateChanges: CompactChatState[] = [];
    function Harness() {
      const [compactChatState, setCompactChatState] = useState<CompactChatState>('input');
      return (
        <App
          chatSurfaceMode="compact"
          compactChatState={compactChatState}
          onCompactChatStateChange={(next) => {
            stateChanges.push(next);
            setCompactChatState(next);
          }}
        />
      );
    }
    const view = render(<Harness />);
    const input = (await screen.findByPlaceholderText('Type a message...')) as HTMLTextAreaElement;
    await act(async () => {
      input.focus();
    });
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    fireEvent.blur(window);
    view.unmount();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(stateChanges).toEqual([]);
  });

  it('still collapses when the pointer press starts outside the input island', async () => {
    const { stateChanges } = await renderFocusedCompactInput();

    fireEvent.pointerDown(document.body, { pointerId: 9, button: 0, buttons: 1, pointerType: 'mouse' });
    fireEvent.pointerUp(document.body, { pointerId: 9, button: 0, buttons: 0, pointerType: 'mouse' });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(stateChanges).toEqual(['default']);
  });

  it('still collapses on Escape', async () => {
    const { stateChanges } = await renderFocusedCompactInput();

    fireEvent.keyDown(document.body, { key: 'Escape' });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(stateChanges).toEqual(['default']);
  });

  it('does not collapse on window blur while the tool fan is open', async () => {
    const { stateChanges } = await renderFocusedCompactInput();

    fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
    fireEvent.blur(window);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(stateChanges).toEqual([]);
    expect(document.body.querySelector('.compact-input-tool-fan'))
      .toHaveAttribute('data-compact-input-tool-fan-open', 'true');
  });
});

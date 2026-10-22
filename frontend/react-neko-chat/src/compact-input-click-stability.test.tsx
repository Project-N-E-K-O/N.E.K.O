import { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import App from './App';
import type { CompactChatState } from './message-schema';

/**
 * 紧凑输入态点击稳定性回归。
 *
 * Windows 实测（NEKO_TRACE_DIALOG_INPUT 取证）：进入输入态 ~230ms 后，原生宿主会产生一次
 * 瞬时窗口失焦并自行 refocus。立即执行的强制折叠会把输入态折回胶囊，下一次点击再拉回输入态，
 * 形成用户可见的闪烁。
 *
 * 契约：
 * - window blur 触发的强制折叠延迟执行；执行时页面已重新持有焦点（瞬时抖动）则放弃折叠。
 * - 焦点未回归（真实失焦，如切换应用）时仍按原语义折叠；Escape / 岛外 pointerdown 的立即
 *   折叠语义不变。
 * - 同一时刻只保留一份 blur 折叠宽限：连续两次抖动按各自的宽限期独立分类。
 * - 宽限期定时器到期时读取最新的折叠回调（扇面展开等守卫状态必须可见）。
 * - 组件卸载时取消待执行的折叠定时器。
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

  it('absorbs a transient window blur whose focus returns before the collapse deadline', async () => {
    const { stateChanges } = await renderFocusedCompactInput();
    const hasFocus = vi.spyOn(document, 'hasFocus');

    // 瞬时失焦：blur 后 ~几十毫秒内焦点回归（hasFocus 恢复 true）。
    hasFocus.mockReturnValue(false);
    fireEvent.blur(window);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    hasFocus.mockReturnValue(true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
    });
    hasFocus.mockRestore();

    expect(stateChanges).toEqual([]);
    expect(screen.getByPlaceholderText('Type a message...')).toHaveFocus();
  });

  it('still collapses on window blur when focus does not return', async () => {
    const { stateChanges } = await renderFocusedCompactInput();
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(false);

    fireEvent.blur(window);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
    });
    hasFocus.mockRestore();

    expect(stateChanges).toEqual(['default']);
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

  it('reschedules the grace window on a second blur so consecutive jitters do not collapse', async () => {
    // 用假定时器保证确定性：第二次 blur 必须落在第一次宽限期（280ms）内、早于旧定时器到期。
    vi.useFakeTimers();
    const hasFocus = vi.spyOn(document, 'hasFocus');
    try {
      const stateChanges = renderControlledCompactInput();
      const input = screen.getByPlaceholderText('Type a message...') as HTMLTextAreaElement;
      await act(async () => {
        input.focus();
      });

      hasFocus.mockReturnValue(false);
      fireEvent.blur(window);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(270); // 第一次宽限期还剩 10ms，旧定时器未到期
      });
      // 第二次抖动在第一次宽限期内到达：旧定时器必须被重置，
      // 否则它会在第二次失焦期间读 hasFocus=false 而误折叠。
      fireEvent.blur(window);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(130); // t=400：第二次抖动仍失焦
      });
      hasFocus.mockReturnValue(true);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300); // t=700：越过第二次的宽限期（550ms），焦点已回归
      });

      expect(stateChanges).toEqual([]);
      expect(screen.getByPlaceholderText('Type a message...')).toHaveFocus();
    } finally {
      hasFocus.mockRestore();
      vi.useRealTimers();
    }
  });

  it('uses the latest collapse closure so a fan opened during the grace window blocks the collapse', async () => {
    // CodeRabbit round-2：定时器若持有 blur 时刻的旧闭包，宽限期内展开的工具扇
    // 对它不可见，会绕过「扇面展开不折叠」守卫。断言定时器读到的是最新闭包。
    const { stateChanges } = await renderFocusedCompactInput();
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(false);

    try {
      fireEvent.blur(window);
      fireEvent.click(screen.getByRole('button', { name: '更多工具' }));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 400)); // 越过 280ms 宽限期，焦点未回归
      });

      expect(stateChanges).toEqual([]);
      // 确认扇面确实处于展开态（防假通过）。
      expect(document.body.querySelector('.compact-input-tool-fan'))
        .toHaveAttribute('data-compact-input-tool-fan-open', 'true');
    } finally {
      hasFocus.mockRestore();
    }
  });

  it('cancels the pending blur collapse on unmount', async () => {
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
    const { unmount } = render(<Harness />);
    const input = (await screen.findByPlaceholderText('Type a message...')) as HTMLTextAreaElement;
    await act(async () => {
      input.focus();
    });

    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    try {
      fireEvent.blur(window);
      unmount();
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
      });

      expect(stateChanges).toEqual([]);
    } finally {
      hasFocus.mockRestore();
    }
  });
});

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import FullChatSurface from './FullChatSurface';

let width = 800;
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute('open', ''); });
  HTMLDialogElement.prototype.close = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes('characters') ? { characters: ['Alice'] } : { messages: [], nextCursor: null, available: true } })));
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      if (target.classList.contains('composer-bottom-bar')) this.callback([{ target, contentRect: { width } } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    unobserve() {} disconnect() {}
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
for (const narrow of [false, true]) {
  it(`opens saved history from the full ${narrow ? 'overflow menu' : 'toolbar'}`, async () => {
    width = narrow ? 200 : 800;
    const { container } = render(<FullChatSurface messages={[]} chatSurfaceMode="full" />);
    if (narrow) {
      await waitFor(() => expect(container.querySelector('.composer-overflow-btn')).not.toBeNull());
      fireEvent.click(container.querySelector('.composer-overflow-btn')!);
    }
    fireEvent.click(screen.getByLabelText('Chat history'));
    expect(await screen.findByRole('dialog')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(HTMLDialogElement.prototype.showModal).toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('close'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
}

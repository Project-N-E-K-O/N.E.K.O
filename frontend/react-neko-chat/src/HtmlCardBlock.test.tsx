import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import HtmlCardBlock from './HtmlCardBlock';
import MessageBlockView from './MessageBlockView';
import { parseChatMessage, type HtmlCard } from './message-schema';

const block: HtmlCard = {
  type: 'html_card', cardId: 'one', pluginId: 'demo', targetLanlan: 'Alice',
  html: '<button data-neko-action="go">Play</button>', css: '', summary: 'Play a song',
  actions: { go: { entry: 'play_track', args: { track_id: '123' } } },
};

async function buttonIn(container: HTMLElement) {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
  const frame = container.querySelector('iframe')!;
  fireEvent.load(frame);
  return frame.contentDocument!.querySelector('button')!;
}

afterEach(() => vi.unstubAllGlobals());

describe('HTML card buttons', () => {
  it.each([
    { summary: 'Working' },
    { css: 'button { color: red; }' },
    { html: '<p>Working</p><button data-neko-action="go">Play</button>' },
  ])('keeps the same action pending across a partial update: %j', async (patch) => {
    let finish!: (value: unknown) => void;
    const fetch = vi.fn((_url: string, _request: RequestInit) => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal('fetch', fetch);
    const { container, rerender } = render(<HtmlCardBlock block={block} />);
    fireEvent.click(await buttonIn(container));
    const signal = fetch.mock.calls[0][1].signal!;

    rerender(<HtmlCardBlock block={{ ...block, ...patch }} />);
    const button = container.querySelector('iframe')!.contentDocument!.querySelector('button')!;
    expect(signal.aborted).toBe(false);
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(fetch).toHaveBeenCalledTimes(1);

    await act(async () => finish({ ok: true, json: async () => ({ result: { message: 'Finished' } }) }));
    expect(button.disabled).toBe(false);
    expect(screen.getByRole('status')).toHaveTextContent('Finished');
  });

  it('ignores a late result after the action binding changes', async () => {
    let finishOld!: (value: unknown) => void;
    const fetch = vi.fn()
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ result: { message: 'New result' } }) });
    vi.stubGlobal('fetch', fetch);
    const { container, rerender } = render(<HtmlCardBlock block={block} />);
    fireEvent.click(await buttonIn(container));
    const signal = fetch.mock.calls[0][1].signal as AbortSignal;
    rerender(<HtmlCardBlock block={{ ...block, actions: { go: { entry: 'play_other' } } }} />);
    expect(signal.aborted).toBe(true);
    fireEvent.click(container.querySelector('iframe')!.contentDocument!.querySelector('button')!);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('New result'));
    await act(async () => finishOld({ ok: true, json: async () => ({ result: { message: 'Old result' } }) }));
    expect(screen.getByRole('status')).toHaveTextContent('New result');
  });

  it('calls the bound plugin action once while pending, then displays the result', async () => {
    let finish!: (value: unknown) => void;
    const fetch = vi.fn(() => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal('fetch', fetch);
    const { container, rerender } = render(<HtmlCardBlock block={block} />);
    const button = await buttonIn(container);
    fireEvent.click(button);
    fireEvent.click(button);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(button.disabled).toBe(true);
    rerender(<HtmlCardBlock block={JSON.parse(JSON.stringify(block))} />);
    expect((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].signal?.aborted).toBe(false);
    const [url, request] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/plugin-cards/demo/action/play_track');
    expect(JSON.parse(String(request.body))).toMatchObject({ card_id: 'one', target_lanlan: 'Alice', presentation: 'chat', args: { track_id: '123' } });
    await act(async () => finish({ ok: true, json: async () => ({ result: { message: 'Playing' } }) }));
    expect(screen.getByRole('status')).toHaveTextContent('Playing');
    expect(button.disabled).toBe(false);
  });

  it('shows backend errors and cancels a pending request on replacement', async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: false, json: async () => ({ detail: { message: 'Plugin stopped' } }) })
      .mockImplementationOnce(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetch);
    const { container, rerender } = render(<HtmlCardBlock block={block} />);
    const button = await buttonIn(container);
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Plugin stopped'));
    fireEvent.click(container.querySelector('iframe')!.contentDocument!.querySelector('button')!);
    const signal = fetch.mock.calls[1][1].signal as AbortSignal;
    rerender(<HtmlCardBlock block={{ ...block, html: '<p>Done</p>', actions: {} }} />);
    expect(signal.aborted).toBe(true);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('renders only the summary in export mode', () => {
    vi.stubGlobal('fetch', vi.fn());
    const message = parseChatMessage({ id: 'one', role: 'system', author: 'demo', time: '', blocks: [block] });
    const { container } = render(<MessageBlockView message={message} block={block} interactive={false} />);
    expect(container.querySelector('iframe')).toBeNull();
    expect(screen.getByText('Play a song')).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('displays the localized proxy error through the component', async () => {
    vi.stubGlobal('safeT', (key: string, fallback: string) => key === 'chat.cardServerUnavailable' ? '插件服务不可用。' : fallback);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false,
      json: async () => ({ detail: { code: 'plugin_card_server_unavailable' } }),
    }));
    const { container } = render(<HtmlCardBlock block={block} />);
    fireEvent.click(await buttonIn(container));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('插件服务不可用。'));
  });

  it('cancels pending requests when the card is unmounted', async () => {
    const fetch = vi.fn((_url: string, _request: RequestInit) => new Promise(() => {}));
    vi.stubGlobal('fetch', fetch);
    const { container, unmount } = render(<HtmlCardBlock block={block} />);
    fireEvent.click(await buttonIn(container));
    const signal = fetch.mock.calls[0][1].signal!;
    unmount();
    expect(signal.aborted).toBe(true);
  });
});

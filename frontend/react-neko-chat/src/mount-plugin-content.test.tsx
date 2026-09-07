import { act, fireEvent, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountPluginContent, unmountPluginContent, type PluginContentBlock } from './mount-plugin-content';

const block: PluginContentBlock = {
  type: 'html_card', cardId: 'view-one', pluginId: 'demo', targetLanlan: 'Alice', presentation: 'agent',
  html: '<button data-neko-action="play">Play</button>', summary: 'Music controls',
  actions: { play: { entry: 'play_track', args: { track_id: '123' } } },
};

const containers = new Set<HTMLElement>();

function makeContainer() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  containers.add(container);
  return container;
}

async function mountBlock(container: HTMLElement, definition = block) {
  let root!: ReturnType<typeof mountPluginContent>;
  await act(async () => { root = mountPluginContent(container, definition); });
  const frame = container.querySelector('iframe')!;
  fireEvent.load(frame);
  return { root, frame, button: frame.contentDocument!.querySelector('button')! };
}

afterEach(() => {
  for (const container of containers) {
    act(() => unmountPluginContent(container));
    container.remove();
  }
  containers.clear();
  vi.unstubAllGlobals();
});

describe('standalone plugin content', () => {
  it('mounts the content without chat UI and forwards its agent presentation', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ result: { message: 'Playing' } }) });
    vi.stubGlobal('fetch', fetch);
    const container = makeContainer();
    const { frame, button } = await mountBlock(container);
    expect(container.querySelectorAll('iframe')).toHaveLength(1);
    expect(container.querySelector('textarea')).toBeNull();
    expect(frame.title).toBe('Music controls');
    await act(async () => { fireEvent.click(button); });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('/api/plugin-cards/demo/action/play_track');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
      card_id: 'view-one', target_lanlan: 'Alice', presentation: 'agent', args: { track_id: '123' },
    });
    expect(within(container).getByRole('status')).toHaveTextContent('Playing');
  });

  it('reuses the root and preserves a pending action across updates and tab visibility changes', async () => {
    let finish!: (response: unknown) => void;
    const fetch = vi.fn((_url: string, _request: RequestInit) => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal('fetch', fetch);
    const container = makeContainer();
    const { root, frame, button } = await mountBlock(container);
    fireEvent.click(button);
    const signal = fetch.mock.calls[0][1].signal!;
    container.style.display = 'none';
    let updatedRoot!: ReturnType<typeof mountPluginContent>;
    await act(async () => {
      updatedRoot = mountPluginContent(container, {
        ...block, html: '<p>Working</p><button data-neko-action="play">Play</button>', summary: 'Working',
      });
    });
    container.style.display = '';
    const updatedButton = frame.contentDocument!.querySelector('button')!;
    expect(updatedRoot).toBe(root);
    expect(container.querySelector('iframe')).toBe(frame);
    expect(signal.aborted).toBe(false);
    expect(updatedButton.disabled).toBe(true);
    fireEvent.click(updatedButton);
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => finish({ ok: true, json: async () => ({ result: { message: 'Finished' } }) }));
    expect(updatedButton.disabled).toBe(false);
    expect(within(container).getByRole('status')).toHaveTextContent('Finished');
  });

  it('treats a change of presentation as a different action context', async () => {
    let finishOld!: (response: unknown) => void;
    const fetch = vi.fn()
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ result: { message: 'Chat action finished' } }) });
    vi.stubGlobal('fetch', fetch);
    const container = makeContainer();
    const { frame, button } = await mountBlock(container);
    fireEvent.click(button);
    const oldSignal = fetch.mock.calls[0][1].signal as AbortSignal;
    await act(async () => { mountPluginContent(container, { ...block, presentation: 'chat' }); });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => { fireEvent.click(frame.contentDocument!.querySelector('button')!); });
    expect(JSON.parse(fetch.mock.calls[1][1].body).presentation).toBe('chat');
    await act(async () => finishOld({ ok: true, json: async () => ({ result: { message: 'Old agent result' } }) }));
    expect(within(container).getByRole('status')).toHaveTextContent('Chat action finished');
  });

  it('keeps multiple content roots independent', async () => {
    const fetch = vi.fn((_url: string, _request: RequestInit) => new Promise(() => {}));
    vi.stubGlobal('fetch', fetch);
    const first = await mountBlock(makeContainer());
    const second = await mountBlock(makeContainer(), { ...block, cardId: 'view-two' });
    fireEvent.click(first.button);
    expect(first.button.disabled).toBe(true);
    expect(second.button.disabled).toBe(false);
    fireEvent.click(second.button);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetch.mock.calls[1][1].body)).card_id).toBe('view-two');
  });

  it('cancels actions on teardown and permits a fresh mount on the same container', async () => {
    let finish!: (response: unknown) => void;
    const fetch = vi.fn((_url: string, _request: RequestInit) => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal('fetch', fetch);
    const container = makeContainer();
    const { root, button } = await mountBlock(container);
    fireEvent.click(button);
    const signal = fetch.mock.calls[0][1].signal!;
    act(() => unmountPluginContent(container));
    expect(signal.aborted).toBe(true);
    expect(container.childElementCount).toBe(0);
    expect(() => unmountPluginContent(container)).not.toThrow();
    await act(async () => finish({ ok: true, json: async () => ({ result: { message: 'Late result' } }) }));
    const remounted = await mountBlock(container);
    expect(remounted.root).not.toBe(root);
    expect(remounted.button.disabled).toBe(false);
    expect(within(container).queryByRole('status')).toBeNull();
  });

  it('validates updates before disturbing mounted content', async () => {
    const container = makeContainer();
    const { frame } = await mountBlock(container);
    expect(() => mountPluginContent(container, { ...block, targetLanlan: '' })).toThrow();
    expect(() => mountPluginContent(container, {
      ...block, presentation: 'unknown',
    } as unknown as PluginContentBlock)).toThrow();
    expect(container.querySelector('iframe')).toBe(frame);
    expect(frame.contentDocument!.querySelector('button')).toHaveTextContent('Play');
  });
});

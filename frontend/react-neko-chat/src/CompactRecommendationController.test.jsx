import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import CompactMusicRecommendationPrompt from './CompactMusicRecommendationPrompt';
import { parseChatMessage } from './message-schema';

const controllerSource = readFileSync(resolve(process.cwd(), '../../static/app/app-recommendations.js'), 'utf8');

describe('compact countdown through the recommendation controller', () => {
  it.each(['meme', 'vision'])('keeps the original deadline when the controller appends direct %s content', async category => {
    vi.useFakeTimers();
    vi.setSystemTime(100000);
    window.localStorage.clear();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    const events = [];
    const play = vi.fn(async () => ({ ok: true }));
    const musicId = '19f3bbca-a275-43d4-923d-1b2ceee91943';
    let messages = [];
    const onAction = (message, action) => {
      window.dispatchEvent(new CustomEvent('react-chat-window:action', { detail: { message, action } }));
    };
    const view = render(<CompactMusicRecommendationPrompt messages={messages} onAction={onAction} />);
    const draw = () => view.rerender(<CompactMusicRecommendationPrompt messages={[...messages]} onAction={onAction} />);
    // The UI receives serialized snapshots, never the controller's mutable object.
    const copy = message => parseChatMessage(JSON.parse(JSON.stringify(message)));
    const host = {
      appendMessage(message) { messages.push(copy(message)); draw(); },
      updateMessage(id, patch) {
        messages = messages.map(message => message.id === id ? copy({ ...message, ...patch }) : message);
        draw();
      },
    };
    vi.stubGlobal('reactChatWindowHost', host);
    vi.stubGlobal('lanlan_config', { lanlan_name: 'Neko' });
    vi.stubGlobal('nekoLocalMutationSecurity', { getMutationHeaders: async () => ({}) });
    vi.stubGlobal('nekoMusicRecommendations', undefined);
    vi.stubGlobal('nekoRecommendations', undefined);
    vi.stubGlobal('dispatchMusicPlayDetailed', play);
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
      const event = JSON.parse(String(init.body));
      events.push(event);
      return { ok: true, status: 200, json: async () => ({ success: true,
        state: event.kind === 'accepted' ? 'accepted' : 'pending', playback_grant: event.kind === 'accepted' }) };
    }));
    const windowRegistration = vi.spyOn(window, 'addEventListener');
    const documentRegistration = vi.spyOn(document, 'addEventListener');
    window.eval(controllerSource);
    const windowHandlers = [...windowRegistration.mock.calls];
    const documentHandlers = [...documentRegistration.mock.calls];
    windowRegistration.mockRestore();
    documentRegistration.mockRestore();
    const controller = window;
    const musicBlock = () => messages.find(message => message.id === 'music-recommendation-' + musicId).blocks[0];
    try {
      await act(async () => {
        expect(controller.nekoMusicRecommendations.offer({ id: musicId, state: 'pending', expires_at: 1900,
          track: { name: 'Controller song', artist: 'Artist', url: 'https://example.com/song.mp3', cover: '' } }, 'topic')).toBe(true);
      });
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '10');
      const initialBlock = musicBlock();
      expect(initialBlock.type === 'music_recommendation' && initialBlock.autoPlayAt).toBe(110000);
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      await act(async () => {
        expect(controller.nekoRecommendations.offer({ id: '023bf0f3-77ac-433a-8138-5ba7dc6da15f', category,
          state: 'pending', expires_at: 1900, content: { title: 'Direct content', source: '',
            url: category === 'meme' ? 'https://example.com/meme.png' : '' } }, 'topic')).toBe(true);
      });
      const directBlock = messages.find(message => message.id.startsWith('proactive-recommendation-')).blocks[0];
      expect(directBlock.type === 'proactive_recommendation' && directBlock.directDelivery).toBe(true);
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '6');
      expect(screen.getByRole('button', { name: /Not now/ })).toBeInTheDocument();
      expect(musicBlock()).toEqual(initialBlock);
      await act(async () => { await vi.advanceTimersByTimeAsync(5999); });
      expect(play).not.toHaveBeenCalled();
      expect(events.filter(event => event.kind === 'accepted')).toHaveLength(0);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(events.filter(event => event.kind === 'accepted')).toEqual([expect.objectContaining({ reason: 'countdown_elapsed' })]);
      expect(play).toHaveBeenCalledTimes(1);
      expect(view.container.querySelector('[data-proactive-category]')).toHaveAttribute('data-proactive-category', category);
    } finally {
      act(() => window.dispatchEvent(new Event('pagehide')));
      view.unmount();
      for (const [name, listener, options] of windowHandlers) window.removeEventListener(name, listener, options);
      for (const [name, listener, options] of documentHandlers) document.removeEventListener(name, listener, options);
      window.localStorage.clear();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });
});

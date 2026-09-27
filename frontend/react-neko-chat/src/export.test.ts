import { expect, it, vi } from 'vitest';
import { mountPluginContent, unmountPluginContent } from './mount-plugin-content';

vi.mock('./mount', () => ({
  mount: vi.fn(), unmount: vi.fn(), mountChatWindow: vi.fn(), unmountChatWindow: vi.fn(),
}));

it('announces plugin content availability after exposing its public API', async () => {
  const ready = vi.fn(() => {
    expect(window.NekoChatWindow?.mountPluginContent).toBe(mountPluginContent);
    expect(window.NekoChatWindow?.unmountPluginContent).toBe(unmountPluginContent);
  });
  window.addEventListener('neko-plugin-content-ready', ready);
  try {
    const api = await import('./export');
    expect(api.mountPluginContent).toBe(mountPluginContent);
    expect(api.unmountPluginContent).toBe(unmountPluginContent);
    expect(ready).toHaveBeenCalledTimes(1);
  } finally {
    window.removeEventListener('neko-plugin-content-ready', ready);
    delete window.NekoChatWindow;
  }
});

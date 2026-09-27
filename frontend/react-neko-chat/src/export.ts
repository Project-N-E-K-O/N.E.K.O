import { mount, mountChatWindow, unmount, unmountChatWindow } from './mount';
import { mountPluginContent, unmountPluginContent } from './mount-plugin-content';

const api = {
  mount,
  unmount,
  mountChatWindow,
  unmountChatWindow,
  mountPluginContent,
  unmountPluginContent,
};

declare global {
  interface Window {
    NekoChatWindow?: typeof api;
  }
}

if (typeof window !== 'undefined') {
  window.NekoChatWindow = api;
  window.dispatchEvent(new Event('neko-plugin-content-ready'));
}

export { mountChatWindow, unmountChatWindow, mountPluginContent, unmountPluginContent };
export type { PluginContentBlock } from './mount-plugin-content';

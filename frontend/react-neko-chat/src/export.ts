import { mount, mountChatWindow, unmount, unmountChatWindow } from './mount';

const api = {
  mount,
  unmount,
  mountChatWindow,
  unmountChatWindow,
};

declare global {
  interface Window {
    NekoChatWindow?: typeof api;
  }
}

if (typeof window !== 'undefined') {
  window.NekoChatWindow = api;
}

export { mountChatWindow, unmountChatWindow };

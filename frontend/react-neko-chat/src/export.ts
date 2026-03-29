import { mountChatWindow, unmountChatWindow } from './mount';

const api = {
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

import {
  mount,
  mountAvatarToolEditor,
  mountChatWindow,
  unmount,
  unmountChatWindow,
} from './mount';

const api = {
  mount,
  unmount,
  mountChatWindow,
  mountAvatarToolEditor,
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

export { mountAvatarToolEditor, mountChatWindow, unmountChatWindow };

import React from 'react';
import ReactDOM from 'react-dom/client';
import App, { type ChatWindowProps } from './App';
import { parseChatWindowProps } from './message-schema';
import './styles.css';

const roots = new WeakMap<HTMLElement, ReactDOM.Root>();

export function mount(container: HTMLElement, props: ChatWindowProps = {}) {
  const normalizedProps = parseChatWindowProps(props);
  const existingRoot = roots.get(container);

  if (existingRoot) {
    existingRoot.render(
      <React.StrictMode>
        <App {...normalizedProps} />
      </React.StrictMode>,
    );
    return existingRoot;
  }

  const root = ReactDOM.createRoot(container);
  root.render(
    <React.StrictMode>
      <App {...normalizedProps} />
    </React.StrictMode>,
  );
  roots.set(container, root);
  return root;
}

export function unmount(container: HTMLElement) {
  const root = roots.get(container);
  if (!root) return;
  root.unmount();
  roots.delete(container);
}

export const mountChatWindow = mount;
export const unmountChatWindow = unmount;

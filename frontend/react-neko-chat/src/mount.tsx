import React from 'react';
import ReactDOM from 'react-dom/client';
import App, { type ChatWindowProps } from './App';
import './styles.css';

type MountOptions = ChatWindowProps;

const roots = new WeakMap<HTMLElement, ReactDOM.Root>();

export function mountChatWindow(container: HTMLElement, options: MountOptions = {}) {
  const existingRoot = roots.get(container);

  if (existingRoot) {
    existingRoot.render(
      <React.StrictMode>
        <App {...options} />
      </React.StrictMode>,
    );
    return existingRoot;
  }

  const root = ReactDOM.createRoot(container);
  root.render(
    <React.StrictMode>
      <App {...options} />
    </React.StrictMode>,
  );
  roots.set(container, root);
  return root;
}

export function unmountChatWindow(container: HTMLElement) {
  const root = roots.get(container);
  if (!root) return;
  root.unmount();
  roots.delete(container);
}

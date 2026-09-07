import React from 'react';
import ReactDOM from 'react-dom/client';
import HtmlCardBlock from './HtmlCardBlock';
import { htmlCardBlockSchema, type HtmlCardInput } from './message-schema';

const roots = new WeakMap<HTMLElement, ReactDOM.Root>();

export type PluginContentBlock = HtmlCardInput;

export function mountPluginContent(container: HTMLElement, block: PluginContentBlock): ReactDOM.Root {
  const parsed = htmlCardBlockSchema.parse(block);
  let root = roots.get(container);
  if (!root) {
    root = ReactDOM.createRoot(container);
    roots.set(container, root);
  }
  root.render(
    <React.StrictMode>
      <HtmlCardBlock block={parsed} />
    </React.StrictMode>,
  );
  return root;
}

export function unmountPluginContent(container: HTMLElement): void {
  const root = roots.get(container);
  if (!root) return;
  root.unmount();
  roots.delete(container);
}

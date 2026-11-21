import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import HistoryResults from './HistoryResults';
afterEach(cleanup);
const entries = Array.from({ length: 10000 }, (_, id) => ({ id, text: String(id), time: '', role: 'user' }));
it('keeps DOM size bounded and renders rows around the scroll position', () => {
  const { container } = render(<HistoryResults entries={entries} renderEntry={e => <article>{e.text}</article>} load={vi.fn()} more={false} busy={false} error={false} active status="" />);
  expect(container.querySelectorAll('article').length).toBeLessThan(20);
  const root = container.querySelector('section')!;
  root.scrollTop = 160 * 5000;
  fireEvent.scroll(root);
  expect(container.querySelectorAll('article').length).toBeLessThan(20);
  expect(container.textContent).toContain('5000');
});
it('requests the next batch near the bottom, but stops on error or context view', async () => {
  const load = vi.fn();
  const props = { entries: entries.slice(0, 30), renderEntry: (e: typeof entries[number]) => <article>{e.text}</article>, load, more: true, busy: false, error: false, active: true, status: '' };
  const view = render(<HistoryResults {...props} />);
  const root = view.container.querySelector('section')!;
  Object.defineProperties(root, { clientHeight: { value: 500 }, scrollHeight: { value: 4800 } });
  root.scrollTop = 4200; fireEvent.scroll(root);
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  view.rerender(<HistoryResults {...props} error />);
  fireEvent.scroll(root);
  view.rerender(<HistoryResults {...props} active={false} />);
  fireEvent.scroll(root);
  await new Promise(resolve => setTimeout(resolve, 220));
  expect(load).toHaveBeenCalledTimes(1);
});

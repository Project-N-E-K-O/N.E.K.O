import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AvatarToolStandaloneEditor from './AvatarToolStandaloneEditor';

const LOCAL_ID = 'local-12345678-1234-4123-8123-123456789abc';
const OTHER_ID = 'local-22345678-1234-4123-8123-123456789abc';
const LIMITS = {
  maxTools: 64, maxNameChars: 20, maxMeaningChars: 100, maxChangeImages: 16,
  maxImages: 17, maxInteractions: 16, maxLinks: 32, maxDelayMs: 600000,
  maxImageBytes: 8_388_608, maxImagePixels: 16_000_000, maxAudioBytes: 5_242_880,
  maxAudioDurationMs: 10_000, maxTotalBytes: 268_435_456,
};
const DETAIL = {
  id: LOCAL_ID, recordVersion: 2, revision: '2-200', name: 'My Feather', changeMode: 'press-swap',
  defaultImage: { resource: 'default.png', url: '/default.png?v=1' },
  changeItems: [{ resource: 'change-000.png', url: '/change-000.png?v=1', meaning: 'A gentle touch' }],
};
const OTHER_ITEM = {
  id: OTHER_ID, recordVersion: 2, revision: '2-300', name: 'Another tool', changeMode: 'press-swap',
  defaultUrl: '/other.png?v=1', changeUrls: ['/other-change.png?v=1'],
};
const response = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200, headers: { 'Content-Type': 'application/json' },
});

function deferredResponse() {
  let resolve!: (value: Response) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<Response>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

describe('standalone editor authoritative catalog', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('waits for the catalog after edit detail and limits arrive', async () => {
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`);
    let resolveList!: (value: Response) => void;
    const list = new Promise<Response>(resolve => { resolveList = resolve; });
    vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL) => String(url).endsWith(`/${LOCAL_ID}`)
      ? Promise.resolve(response({ ok: true, detail: DETAIL, limits: LIMITS })) : list));
    render(<AvatarToolStandaloneEditor />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('textbox', { name: 'Tool name' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
    expect(screen.getByText('Opening…')).toHaveAttribute('role', 'status');
    await act(async () => resolveList(response({ ok: true, items: [OTHER_ITEM], limits: LIMITS })));
    expect(await screen.findByDisplayValue('My Feather')).toBeInTheDocument();
  });

  it.each(['create', 'edit'])('retries the failed %s catalog and validates against all custom names', async (mode) => {
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=${mode}&toolId=${LOCAL_ID}`);
    let resolveRetry!: (value: Response) => void;
    const retry = new Promise<Response>(resolve => { resolveRetry = resolve; });
    let listCalls = 0;
    let detailCalls = 0;
    const mutations: string[] = [];
    vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method && init.method !== 'GET') mutations.push(init.method);
      if (String(url).endsWith(`/${LOCAL_ID}`)) {
        detailCalls += 1;
        return Promise.resolve(response({ ok: true, detail: DETAIL, limits: LIMITS }));
      }
      listCalls += 1;
      return listCalls === 1 ? Promise.reject(new Error('offline')) : retry;
    }));
    render(<AvatarToolStandaloneEditor />);
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Tool name' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(listCalls).toBe(2));
    expect(screen.queryByRole('textbox', { name: 'Tool name' })).toBeNull();
    await act(async () => resolveRetry(response({ ok: true, items: [OTHER_ITEM], limits: LIMITS })));
    const name = await screen.findByRole('textbox', { name: 'Tool name' });
    if (mode === 'edit') {
      expect(name).toHaveValue('My Feather');
      expect(detailCalls).toBe(1);
    }
    fireEvent.change(name, { target: { value: 'another TOOL' } });
    fireEvent.click(screen.getByRole('button', { name: mode === 'edit' ? 'Save changes' : 'Save tool' }));
    expect(screen.getByText('“another TOOL” is already used by another tool. Choose a different name.')).toBeInTheDocument();
    expect(mutations).toEqual([]);
  });

  it.each(['success', 'failure'])('preserves the new draft when an earlier retry detail returns %s', async (outcome) => {
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`);
    const oldDetail = deferredResponse();
    let listCalls = 0;
    let detailCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL) => {
      if (String(url).endsWith(`/${LOCAL_ID}`)) {
        detailCalls += 1;
        if (detailCalls === 1) return Promise.reject(new Error('initial detail failed'));
        if (detailCalls === 2) return oldDetail.promise;
        return Promise.resolve(response({ ok: true, limits: LIMITS,
          detail: { ...DETAIL, revision: '2-400', name: 'Newer tool' } }));
      }
      listCalls += 1;
      return listCalls <= 2 ? Promise.reject(new Error('catalog failed'))
        : Promise.resolve(response({ ok: true, items: [OTHER_ITEM], limits: LIMITS }));
    }));
    render(<AvatarToolStandaloneEditor />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(detailCalls).toBe(2));
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    const name = await screen.findByDisplayValue('Newer tool');
    fireEvent.change(name, { target: { value: 'Unsaved draft' } });
    await act(async () => {
      if (outcome === 'success') oldDetail.resolve(response({ ok: true, detail: DETAIL, limits: LIMITS }));
      else oldDetail.reject(new Error('late detail failure'));
    });
    expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('Unsaved draft');
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it.each(['success', 'failure'])('keeps the new initial load pending when the old retry finishes with %s', async (outcome) => {
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`);
    const oldDetail = deferredResponse();
    const newDetail = deferredResponse();
    let oldDetailCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL) => {
      if (String(url).endsWith(`/${LOCAL_ID}`)) {
        oldDetailCalls += 1;
        return oldDetailCalls === 1 ? Promise.reject(new Error('initial detail failed')) : oldDetail.promise;
      }
      if (String(url).endsWith(`/${OTHER_ID}`)) return newDetail.promise;
      return Promise.resolve(response({ ok: true, items: [OTHER_ITEM], limits: LIMITS }));
    }));
    const { rerender } = render(<AvatarToolStandaloneEditor />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(oldDetailCalls).toBe(2));
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${OTHER_ID}`);
    rerender(<AvatarToolStandaloneEditor />);
    await act(async () => {
      if (outcome === 'success') oldDetail.resolve(response({ ok: true, detail: DETAIL, limits: LIMITS }));
      else oldDetail.reject(new Error('late detail failure'));
    });
    expect(screen.getByText('Opening…')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Tool name' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    await act(async () => newDetail.resolve(response({ ok: true, limits: LIMITS,
      detail: { ...DETAIL, id: OTHER_ID, revision: '2-400', name: 'Newer tool' } })));
    expect(await screen.findByDisplayValue('Newer tool')).toBeInTheDocument();
  });
});

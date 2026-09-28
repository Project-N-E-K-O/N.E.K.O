import { act, renderHook, waitFor } from '@testing-library/react';
import type { AvatarToolItem } from '../avatarTools';
import {
  MISSING_SLOT_REPROBE_INTERVAL_MS,
  useAvatarToolSlotReconciliation,
} from './useAvatarToolSlotReconciliation';

const FIRST_TOOL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
const SECOND_TOOL_ID = 'local-22345678-1234-4123-8123-123456789abc' as const;
const THIRD_TOOL_ID = 'local-32345678-1234-4123-8123-123456789abc' as const;

function detailError(code: string) {
  return new Response(JSON.stringify({ ok: false, error_code: code }), {
    status: 404,
    headers: { 'Content-Type': 'application/json' },
  });
}

function managementItem(id: `local-${string}`): AvatarToolItem {
  return {
    id,
    label: { kind: 'literal', value: 'Feather' },
    iconImagePath: '/default.png?v=1',
    pointerImagePath: '/default.png?v=1',
    pointerHotspotX: 40,
    pointerHotspotY: 40,
    pointerNaturalWidth: 80,
    pointerNaturalHeight: 80,
    pointerDisplayWidth: 80,
    pointerDisplayHeight: 80,
  };
}

describe('useAvatarToolSlotReconciliation', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reports only exact tool_not_found and retains invalid or temporarily unreadable records', async () => {
    const onConfirmedDeleted = vi.fn();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(detailError('record_invalid'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(detailError('tool_not_found'));
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => useAvatarToolSlotReconciliation({
      activeToolIds: [FIRST_TOOL_ID, SECOND_TOOL_ID, THIRD_TOOL_ID],
      authoritativeItems: [],
      authoritativeLoaded: true,
      onConfirmedDeleted,
    }));

    await waitFor(() => expect(onConfirmedDeleted).toHaveBeenCalledWith([THIRD_TOOL_ID]));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('probes each missing slot once across catalog refreshes and re-probes only after it reappears', async () => {
    const onConfirmedDeleted = vi.fn();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(FIRST_TOOL_ID)) return Promise.resolve(detailError('record_invalid'));
      return Promise.reject(new Error('offline'));
    });
    vi.stubGlobal('fetch', fetchMock);
    const probesFor = (toolId: string) => fetchMock.mock.calls.filter(([input]) => String(input).includes(toolId)).length;

    const { rerender } = renderHook(
      ({ authoritativeItems }) => useAvatarToolSlotReconciliation({
        activeToolIds: [FIRST_TOOL_ID, SECOND_TOOL_ID],
        authoritativeItems,
        authoritativeLoaded: true,
        onConfirmedDeleted,
      }),
      { initialProps: { authoritativeItems: [] as AvatarToolItem[] } },
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    for (let refresh = 0; refresh < 3; refresh += 1) {
      rerender({ authoritativeItems: [] });
      await act(async () => { await Promise.resolve(); });
    }
    expect(probesFor(FIRST_TOOL_ID)).toBe(1);
    expect(probesFor(SECOND_TOOL_ID)).toBe(1);

    rerender({ authoritativeItems: [managementItem(SECOND_TOOL_ID)] });
    rerender({ authoritativeItems: [] });
    await waitFor(() => expect(probesFor(SECOND_TOOL_ID)).toBe(2));
    expect(probesFor(FIRST_TOOL_ID)).toBe(1);
    expect(onConfirmedDeleted).not.toHaveBeenCalled();
  });

  it('re-probes a retained slot after the cooldown and does not duplicate an in-flight probe', async () => {
    let clock = 1_000_000;
    const nowSpy = vi.spyOn(Date, 'now').mockImplementation(() => clock);
    let resolveFirst!: (response: Response) => void;
    const fetchMock = vi.fn()
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(detailError('tool_not_found'));
    vi.stubGlobal('fetch', fetchMock);
    const onConfirmedDeleted = vi.fn();
    try {
      const { rerender } = renderHook(
        ({ authoritativeItems }) => useAvatarToolSlotReconciliation({
          activeToolIds: [FIRST_TOOL_ID],
          authoritativeItems,
          authoritativeLoaded: true,
          onConfirmedDeleted,
        }),
        { initialProps: { authoritativeItems: [] as AvatarToolItem[] } },
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
      rerender({ authoritativeItems: [] });
      await act(async () => { await Promise.resolve(); });
      expect(fetchMock).toHaveBeenCalledTimes(1);

      await act(async () => {
        resolveFirst(detailError('record_invalid'));
        await Promise.resolve();
      });
      rerender({ authoritativeItems: [] });
      await act(async () => { await Promise.resolve(); });
      expect(fetchMock).toHaveBeenCalledTimes(1);

      clock += MISSING_SLOT_REPROBE_INTERVAL_MS;
      rerender({ authoritativeItems: [] });
      await waitFor(() => expect(onConfirmedDeleted).toHaveBeenCalledWith([FIRST_TOOL_ID]));
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      nowSpy.mockRestore();
    }
  });

  it('ignores a late deletion confirmation after a newer catalog contains the tool', async () => {
    let resolveDetail!: (response: Response) => void;
    const pendingDetail = new Promise<Response>((resolve) => {
      resolveDetail = resolve;
    });
    const onConfirmedDeleted = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pendingDetail));

    const { rerender } = renderHook(
      ({ authoritativeItems }) => useAvatarToolSlotReconciliation({
        activeToolIds: [FIRST_TOOL_ID],
        authoritativeItems,
        authoritativeLoaded: true,
        onConfirmedDeleted,
      }),
      { initialProps: { authoritativeItems: [] as AvatarToolItem[] } },
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    rerender({ authoritativeItems: [managementItem(FIRST_TOOL_ID)] });
    await act(async () => {
      resolveDetail(detailError('tool_not_found'));
      await pendingDetail;
      await Promise.resolve();
    });

    expect(onConfirmedDeleted).not.toHaveBeenCalled();
  });
});

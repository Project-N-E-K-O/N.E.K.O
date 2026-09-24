import { act, renderHook, waitFor } from '@testing-library/react';
import type { AvatarToolItem } from '../avatarTools';
import { useAvatarToolSlotReconciliation } from './useAvatarToolSlotReconciliation';

const FIRST_TOOL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
const SECOND_TOOL_ID = 'local-22345678-1234-4123-8123-123456789abc' as const;
const THIRD_TOOL_ID = 'local-32345678-1234-4123-8123-123456789abc' as const;

function detailError(code: string) {
  return new Response(JSON.stringify({ ok: false, error_code: code }), {
    status: 404,
    headers: { 'Content-Type': 'application/json' },
  });
}

function managementItem(id: typeof FIRST_TOOL_ID): AvatarToolItem {
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

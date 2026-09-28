import { useEffect, useMemo, useRef } from 'react';
import {
  isLocalAvatarToolId,
  type AvatarToolId,
  type AvatarToolItem,
} from '../avatarTools';
import {
  fetchLocalAvatarToolDetail,
  LocalAvatarToolDetailError,
} from './localTools';

type AvatarToolSlotReconciliationOptions = {
  activeToolIds: ReadonlyArray<AvatarToolId>;
  authoritativeItems: ReadonlyArray<AvatarToolItem>;
  authoritativeLoaded: boolean;
  onConfirmedDeleted(toolIds: ReadonlyArray<`local-${string}`>): void;
};

export type LocalAvatarToolProbeResult = 'deleted' | 'retained' | 'unreachable';

// 没被确认删除的缺席 id 至少隔这么久才再确认一次：既不在每次 focus 时
// 重复全量哈希，又能在隔离记录之后被删掉时最终清掉槽位。
export const MISSING_SLOT_REPROBE_INTERVAL_MS = 60_000;

/** 只有详情接口明确回 tool_not_found 才算删除；隔离记录（record_invalid 等）算仍在。 */
export async function probeLocalAvatarTool(toolId: `local-${string}`): Promise<LocalAvatarToolProbeResult> {
  try {
    await fetchLocalAvatarToolDetail(toolId);
    return 'retained';
  } catch (error) {
    if (!(error instanceof LocalAvatarToolDetailError)) return 'unreachable';
    return error.message === 'tool_not_found' ? 'deleted' : 'retained';
  }
}

/**
 * Reconciles only deletions that the current surface can prove independently.
 * A list omission alone is not enough because invalid records are quarantined
 * out of the public list while their persisted slot intent must be retained.
 */
export function useAvatarToolSlotReconciliation({
  activeToolIds,
  authoritativeItems,
  authoritativeLoaded,
  onConfirmedDeleted,
}: AvatarToolSlotReconciliationOptions) {
  const missingKey = useMemo(() => {
    if (!authoritativeLoaded) return '';
    const availableIds = new Set(authoritativeItems.map(item => item.id));
    return activeToolIds
      .filter(toolId => isLocalAvatarToolId(toolId) && !availableIds.has(toolId))
      .sort()
      .join('\n');
  }, [activeToolIds, authoritativeItems, authoritativeLoaded]);
  // 答复「还在」（含 record_invalid 这类隔离记录）或暂时连不上的 id 记下探测时间，
  // 冷却期内的目录刷新不再重复探测：详情接口要在全局锁下哈希全部资源。id 重新
  // 出现在列表里就忘掉，之后再缺席时立即重新确认。
  const probedAtRef = useRef(new Map<`local-${string}`, number>());
  // 目录刷新会让 effect 重跑；进行中的探测不因此作废，也不重复发起，结果回来时
  // 以最新的缺席集合为准：期间重新出现在列表里的 id 不再上报删除。
  const inFlightIdsRef = useRef(new Set<`local-${string}`>());
  const latestMissingIdsRef = useRef(new Set<string>());
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const missingIds = new Set(missingKey ? missingKey.split('\n') : []);
    latestMissingIdsRef.current = missingIds;
    probedAtRef.current.forEach((_probedAt, toolId) => {
      if (!missingIds.has(toolId)) probedAtRef.current.delete(toolId);
    });
    const now = Date.now();
    const probeIds = [...missingIds].filter((toolId): toolId is `local-${string}` => {
      if (!isLocalAvatarToolId(toolId) || inFlightIdsRef.current.has(toolId)) return false;
      const probedAt = probedAtRef.current.get(toolId);
      return probedAt === undefined || now - probedAt >= MISSING_SLOT_REPROBE_INTERVAL_MS;
    });
    if (probeIds.length === 0) return;

    probeIds.forEach(toolId => inFlightIdsRef.current.add(toolId));
    void Promise.all(probeIds.map(async (toolId) => {
      try {
        const result = await probeLocalAvatarTool(toolId);
        const stillMissing = latestMissingIdsRef.current.has(toolId);
        if (result !== 'deleted' && stillMissing) probedAtRef.current.set(toolId, Date.now());
        return result === 'deleted' && stillMissing ? toolId : null;
      } finally {
        inFlightIdsRef.current.delete(toolId);
      }
    })).then((confirmedIds) => {
      if (!mountedRef.current) return;
      const deletedIds = confirmedIds.filter((toolId): toolId is `local-${string}` => toolId !== null);
      if (deletedIds.length > 0) onConfirmedDeleted(deletedIds);
    });
    // authoritativeItems 让每次目录刷新都重新检查冷却；缺席集合没变时只是一次 Map 查询。
  }, [missingKey, authoritativeItems, onConfirmedDeleted]);
}

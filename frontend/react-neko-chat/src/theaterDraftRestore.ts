// 剧场结束后宿主 viewProps 会一直保留 ordinaryDraftRestore；full 与 compact 是两个独立组件，
// 切换形态会重挂并重置各自的 ref。已消费的恢复 id 必须在模块级共享，否则旧草稿会在每次切换时被重新填回。
let lastConsumedOrdinaryDraftRestoreId = '';

/** 首次看到该恢复 id 时返回 true 并登记；之后任一聊天形态再次挂载都不会重复恢复。 */
export function claimOrdinaryDraftRestore(id: string | null | undefined): boolean {
  if (!id || id === lastConsumedOrdinaryDraftRestoreId) return false;
  lastConsumedOrdinaryDraftRestoreId = id;
  return true;
}

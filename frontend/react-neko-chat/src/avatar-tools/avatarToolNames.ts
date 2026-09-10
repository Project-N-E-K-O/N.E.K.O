export function normalizeAvatarToolComparableName(value: string): string {
  return value.normalize('NFC').trim().replace(/\s+/gu, ' ').toLowerCase();
}

export function findDuplicateAvatarToolNameIds<T extends { id: string }>(
  items: readonly T[],
  getDisplayName: (item: T, index: number) => string,
): Set<T['id']> {
  const idsByName = new Map<string, T['id'][]>();
  items.forEach((item, index) => {
    const name = normalizeAvatarToolComparableName(getDisplayName(item, index));
    if (!name) return;
    const ids = idsByName.get(name) ?? [];
    ids.push(item.id);
    idsByName.set(name, ids);
  });

  return new Set(
    [...idsByName.values()]
      .filter(ids => ids.length > 1)
      .flat(),
  );
}

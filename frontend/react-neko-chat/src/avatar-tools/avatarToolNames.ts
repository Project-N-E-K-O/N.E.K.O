export const AVATAR_TOOL_NAME_ALLOWED_PATTERN = /^[\p{L}\p{M}\p{N} _-]+$/u;

export function normalizeAvatarToolName(value: string): string {
  return value.normalize('NFC').trim().replace(/ +/g, ' ');
}

export function countAvatarToolNameCharacters(value: string): number {
  return Array.from(value).length;
}

export function getAvatarToolNameValidationError(
  value: string,
  maximum: number,
  required = false,
): 'required' | 'too-long' | 'invalid' | null {
  const normalized = normalizeAvatarToolName(value);
  if (!normalized) return required ? 'required' : null;
  if (countAvatarToolNameCharacters(normalized) > maximum) return 'too-long';
  return AVATAR_TOOL_NAME_ALLOWED_PATTERN.test(normalized) ? null : 'invalid';
}

export function normalizeAvatarToolComparableName(value: string): string {
  return normalizeAvatarToolName(value).toLowerCase();
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

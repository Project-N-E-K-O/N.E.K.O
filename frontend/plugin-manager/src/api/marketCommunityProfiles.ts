import { fetchBridge } from './marketBridge'

// The bridge accepts at most this many profile ids per query.
const AVATAR_QUERY_MAX_IDS = 100

/** Public community avatars by profile id; any number of actors, queried in batches. */
export async function fetchCommunityAuthorAvatars(
  actors: Array<{ community_profile_id?: string | null }>,
  signal?: AbortSignal
): Promise<Record<string, string>> {
  const userIds = [
    ...new Set(
      actors.flatMap((actor) => (actor.community_profile_id ? [actor.community_profile_id] : []))
    ),
  ]
  const avatars: Record<string, string> = {}
  for (let offset = 0; offset < userIds.length && !signal?.aborted; offset += AVATAR_QUERY_MAX_IDS) {
    Object.assign(
      avatars,
      await queryAvatars(userIds.slice(offset, offset + AVATAR_QUERY_MAX_IDS), signal)
    )
  }
  return avatars
}

async function queryAvatars(userIds: string[], signal?: AbortSignal): Promise<Record<string, string>> {
  try {
    const response = await fetchBridge(
      '/market/feedback/avatars',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_ids: userIds }),
        cache: 'no-store',
        signal,
      },
      { authorizationHeader: true }
    )
    return response?.ok ? await response.json() : {}
  } catch {
    return {}
  }
}

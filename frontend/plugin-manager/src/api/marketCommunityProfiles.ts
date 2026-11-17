import { fetchBridge } from './marketBridge'

export async function fetchCommunityAuthorAvatars(
  actors: Array<{ community_profile_id?: string | null }>,
  signal?: AbortSignal
): Promise<Record<string, string>> {
  const userIds = [
    ...new Set(
      actors.flatMap((actor) => (actor.community_profile_id ? [actor.community_profile_id] : []))
    ),
  ]
  if (userIds.length === 0) return {}
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

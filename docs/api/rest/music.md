# Music API

**Prefix:** `/api/music`

Music search, playback, and lyric/media proxy endpoints. Routes are declared with full inline paths (the router itself has no prefix), all sharing the `/api/music` namespace.

## Proxy

### `GET /api/music/proxy`

Generic media proxy that works around CORS and Referer restrictions when fetching audio from music sources. Only hosts on the music-source domain whitelist are allowed; redirects are followed but each hop is re-validated against the whitelist.

**Query:** `url` — Remote media URL to proxy (must be http/https).

- The cache/stream decision is made from the upstream `Content-Length` header (probed without downloading the body).
- Sources reporting a `Content-Length` under 10MB are buffered and cached (TTL ~6 hours); the response carries `X-Cache: HIT` / `MISS`.
- Sources of 10MB and above — **or that omit/hide `Content-Length`** — are streamed (`X-Cache: STREAM`), so playback can begin while downloading.
- A 50MB hard size limit is enforced.

::: info
On failure the endpoint returns a JSON envelope `{ "success": false, "error": "..." }` with an appropriate status code (e.g. 400 invalid URL, 403 disallowed domain, 413 too large, 504 timeout).
:::

## Domains

### `GET /api/music/domains`

Return the domain list of all music sources so the frontend can register them in its dynamic whitelist (used to authorize proxy requests).

**Response:**

```json
{
  "success": true,
  "domains": ["..."]
}
```

## Search

### `GET /api/music/search`

Smart music dispatch route that searches across the configured music sources.

**Query:**

- `query` — Search keyword (max length 200).
- `limit` — Maximum number of results (1–50, default 10). At least 5 candidates are always returned so the frontend can do intelligent matching.

**Response:** On success, an envelope `{ "success": true, "data": [...] }` containing the matched tracks. On an empty keyword or an error, `{ "success": false, "data": [], "error": "...", "message": "..." }`.

## Playback

### `GET /api/music/play/netease/{song_id}`

NetEase Cloud Music smart-redirect route. Uses the backend `MUSIC_U` cookie to resolve the real high-quality / authenticated direct link, then issues a redirect (HTTP 307) to it for playback.

**Path parameter:** `song_id` — The NetEase song id (decimal digits only).

::: info
If `pyncm_async` is unavailable, no cookie is present, or resolution fails, the endpoint falls back to redirecting to the public `music.163.com` outer URL for the song. A non-numeric `song_id` returns `{ "success": false, "error": "invalid song_id" }` with status 400.
:::

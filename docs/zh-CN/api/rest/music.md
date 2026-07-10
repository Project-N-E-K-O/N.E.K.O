# 音乐 API

**前缀：** `/api/music`

音乐搜索、播放以及歌词/媒体代理端点。这些路由以完整的内联路径声明（路由器本身没有前缀），统一归属于 `/api/music` 命名空间。

## 代理

### `GET /api/music/proxy`

通用媒体代理，在从音乐源拉取音频时绕过 CORS 与 Referer 限制。仅允许音乐源域名白名单中的主机；会跟随重定向，但每一跳都会重新对白名单进行校验。

**查询参数：** `url` — 要代理的远程媒体 URL（必须为 http/https）。

- 缓存/流式的判定依据上游的 `Content-Length` 头（只探测 header、不下载 body）。
- `Content-Length` 小于 10MB 的来源会被缓冲并缓存（TTL 约 6 小时），响应携带 `X-Cache: HIT` / `MISS`。
- 大于等于 10MB——**或未提供/隐藏 `Content-Length`** 的来源以流式传输（`X-Cache: STREAM`），可边下载边播放。
- 强制 50MB 的大小上限。

::: info
失败时该端点返回 JSON 信封 `{ "success": false, "error": "..." }`，并带有相应的状态码（例如 400 无效 URL、403 域名不允许、413 文件过大、504 超时）。
:::

## 域名

### `GET /api/music/domains`

返回所有音乐源的域名列表，供前端注册到其动态白名单中（用于授权代理请求）。

**响应：**

```json
{
  "success": true,
  "domains": ["..."]
}
```

## 搜索

### `GET /api/music/search`

智能音乐分发路由，在已配置的音乐源中进行搜索。

**查询参数：**

- `query` — 搜索关键词（最大长度 200）。
- `limit` — 返回结果的最大数量（1–50，默认 10）。始终至少返回 5 个候选项，便于前端做智能匹配。

**响应：** 成功时返回信封 `{ "success": true, "data": [...] }`，其中包含匹配到的曲目。当关键词为空或发生错误时，返回 `{ "success": false, "data": [], "error": "...", "message": "..." }`。

## 播放

### `GET /api/music/play/netease/{song_id}`

网易云音乐智能跳转路由。使用后端的 `MUSIC_U` cookie 解析出真实的高音质 / 鉴权直链，然后发起重定向（HTTP 307）到该地址进行播放。

**路径参数：** `song_id` — 网易云歌曲 id（仅限十进制数字）。

::: info
当 `pyncm_async` 不可用、没有 cookie 或解析失败时，该端点会降级跳转到该歌曲的公开 `music.163.com` 外链。非数字的 `song_id` 返回 `{ "success": false, "error": "invalid song_id" }`，状态码为 400。
:::

export const NOINDEX_ROUTE_PREFIXES = ['/design/']
export const NOINDEX_ROUTES = new Set([
  '/live2d_motion_plan',
  '/pngtuber-remix-physics-plan',
])

export function isNoindexRoute(pathname) {
  if (NOINDEX_ROUTES.has(pathname)) return true
  return NOINDEX_ROUTE_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

import { retryableModule } from '@/utils/retryableModule'

// No imports of Sucrase/UI Kit on the static or markdown paths. Keep these as
// explicit dynamic edges; successful loads are shared, failures/timeouts retry.
export const loadTsxRenderer = retryableModule(() => import('./tsxRuntime'))
export const loadMarkdownRenderer = retryableModule(() => import('./markdownRuntime'))

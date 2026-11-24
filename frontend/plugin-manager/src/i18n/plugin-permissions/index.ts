/** Every locale at once, for tests and tooling; language packs import their own locale only. */
import en from './en-US'
import zhCN from './zh-CN'
import zhTW from './zh-TW'
import ja from './ja'
import ko from './ko'
import ru from './ru'
import es from './es'
import pt from './pt'

export { PERMISSION_LEGACY_LABEL_KEYS, withPermissionLabels, type Label } from './shared'
export type { PermissionLabels } from './en-US'
export const permissionLabels = { en, zhCN, zhTW, ja, ko, ru, es, pt }

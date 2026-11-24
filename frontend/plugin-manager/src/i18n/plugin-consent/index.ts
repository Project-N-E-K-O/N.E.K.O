/** Every locale at once, for tests and tooling; language packs import their own locale only. */
import en from './en-US'
import zhCN from './zh-CN'
import zhTW from './zh-TW'
import ja from './ja'
import ko from './ko'
import ru from './ru'
import es from './es'
import pt from './pt'

export const pluginConsentMessages = {
  'zh-CN': zhCN,
  'en-US': en,
  ja,
  'zh-TW': zhTW,
  ko,
  ru,
  es,
  pt,
}

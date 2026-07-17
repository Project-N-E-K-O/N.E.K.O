import DefaultTheme from 'vitepress/theme'
import { h } from 'vue'
import LocaleSwitch from './LocaleSwitch.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout: () => h(DefaultTheme.Layout, null, {
    'nav-bar-content-after': () => h(LocaleSwitch, { variant: 'desktop' }),
    'nav-screen-content-after': () => h(LocaleSwitch, { variant: 'mobile' }),
  }),
}

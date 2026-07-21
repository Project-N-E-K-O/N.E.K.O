/// <reference types="vite/client" />

declare module 'element-plus/dist/locale/zh-cn.mjs'
declare module 'element-plus/dist/locale/zh-tw.mjs'
declare module 'element-plus/dist/locale/en.mjs'
declare module 'element-plus/dist/locale/ja.mjs'
declare module 'element-plus/dist/locale/ko.mjs'
declare module 'element-plus/dist/locale/ru.mjs'
declare module 'element-plus/dist/locale/es.mjs'
declare module 'element-plus/dist/locale/pt.mjs'

interface NekoWindowControlResult {
  ok?: boolean
  isMaximized?: boolean
  allowed?: boolean
  pinned?: boolean
  inherited?: boolean
  effective?: boolean
}

interface NekoWindowControlApi {
  minimize?: () => Promise<unknown> | unknown
  restore?: () => Promise<unknown> | unknown
  maximize?: () => Promise<NekoWindowControlResult> | NekoWindowControlResult
  isMaximized?: () => Promise<boolean> | boolean
  getAlwaysOnTopState?: () => Promise<NekoWindowControlResult> | NekoWindowControlResult
  toggleAlwaysOnTop?: () => Promise<NekoWindowControlResult> | NekoWindowControlResult
}

interface Window {
  nekoWindowControl?: NekoWindowControlApi
}

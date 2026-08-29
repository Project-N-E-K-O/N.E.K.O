<template>
  <el-config-provider
    :locale="elementLocale"
    :z-index="ELEMENT_Z_INDEX"
    :message="ELEMENT_MESSAGE_CONFIG"
  >
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import zhTw from 'element-plus/dist/locale/zh-tw.mjs'
import en from 'element-plus/dist/locale/en.mjs'
import jaLocale from 'element-plus/dist/locale/ja.mjs'
import koLocale from 'element-plus/dist/locale/ko.mjs'
import ruLocale from 'element-plus/dist/locale/ru.mjs'
import esLocale from 'element-plus/dist/locale/es.mjs'
import ptLocale from 'element-plus/dist/locale/pt.mjs'
import { getLocale } from './i18n'

const ELEMENT_Z_INDEX = 12000
const ELEMENT_MESSAGE_CONFIG = { offset: 54 }
const elementLocaleMap: Record<string, typeof zhCn> = {
  'zh-CN': zhCn,
  'zh-TW': zhTw,
  'en-US': en,
  ja: jaLocale,
  ko: koLocale,
  ru: ruLocale,
  es: esLocale,
  pt: ptLocale,
}
const elementLocale = elementLocaleMap[getLocale()] ?? zhCn
</script>

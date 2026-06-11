/**
 * userPreference Pinia store — 全局用户偏好（neko-market-version-sync）。
 *
 * 当前只存 channel: 'stable' | 'beta'，决定 Market 列表 / version list 拉取
 * 时透传的 ?channel= 查询参数。设计决策 D1：channel 是全局偏好而非
 * per-plugin override —— 简化交互、避免插件间互相干扰，未来可在插件详情
 * 页加 advanced override 但本期不做。
 *
 * 持久化：localStorage 单 key（`neko_user_preference_v1`），失败容错（隐私
 * 模式 / quota 满 / JSON 解析错全部回落 stable，绝不抛异常）。
 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const STORAGE_KEY = 'neko_user_preference_v1'

interface PersistedPref {
  channel: 'stable' | 'beta'
}

function loadFromStorage(): PersistedPref {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { channel: 'stable' }
    const parsed = JSON.parse(raw) as Partial<PersistedPref> | null
    // 容错：非 'beta' 一律回落 stable，避免历史数据 / 手工编辑塞进非法值
    return { channel: parsed?.channel === 'beta' ? 'beta' : 'stable' }
  } catch {
    return { channel: 'stable' }
  }
}

function saveToStorage(pref: PersistedPref): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pref))
  } catch {
    // 忽略 quota / privacy mode 异常 —— 用户切了也只是当前会话有效
  }
}

export const useUserPreferenceStore = defineStore('userPreference', () => {
  const initial = loadFromStorage()
  const channel = ref<'stable' | 'beta'>(initial.channel)

  // 双向持久化：响应式 channel 变更立即落 localStorage
  watch(channel, (val) => saveToStorage({ channel: val }))

  function setChannel(c: 'stable' | 'beta'): void {
    channel.value = c
  }

  return { channel, setChannel }
})

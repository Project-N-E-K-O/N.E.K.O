<template>
  <div v-loading="loading && !places.length" class="places-tab">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <section class="panel">
      <div class="heading">
        <h2>{{ t('power.runtime.places.title') }}</h2>
        <el-button :loading="loading" @click="load">{{ t('common.refresh') }}</el-button>
      </div>
      <p class="muted">{{ t('power.runtime.places.hint') }}</p>
      <table class="rows">
        <thead>
          <tr>
            <th>{{ t('power.runtime.places.what') }}</th>
            <th>{{ t('power.runtime.places.where') }}</th>
            <th>{{ t('power.runtime.places.holds') }}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="place in places" :key="place.key" :data-place="place.key">
            <td>{{ t(`power.runtime.places.keys.${place.key}`) }}</td>
            <td class="path">{{ place.path }}</td>
            <td class="muted">{{ holds(place) }}</td>
            <td class="act">
              <el-button v-if="place.exists" size="small" :loading="opening === place.key" @click="open(place.key)">
                {{ t('power.runtime.places.open') }}
              </el-button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getPlaces, openPlace } from '@/api/console'
import type { Place } from '@/api/console'
import { describeConsoleError, formatBytes } from './powerFormat'

const { t, te } = useI18n()
const places = ref<Place[]>([])
const loading = ref(false)
const opening = ref('')
const error = ref('')

function holds(place: Place): string {
  if (!place.exists) return t('power.runtime.places.missing')
  const size = formatBytes(place.bytes)
  return place.bytes_whole ? size : t('power.runtime.places.atLeast', { size })
}

async function load() {
  loading.value = true
  try {
    places.value = (await getPlaces()).places
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

async function open(key: string) {
  opening.value = key
  try {
    await openPlace(key)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    opening.value = ''
  }
}

onMounted(() => void load())
defineExpose({ load })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.heading h2 { margin: 0; font-size: 16px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.rows { width: 100%; border-collapse: collapse; margin-top: 8px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: middle; }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.path { font-family: var(--el-font-family-mono, monospace); font-size: 12px; overflow-wrap: anywhere; }
.act { text-align: right; white-space: nowrap; }
.block { margin-bottom: 12px; }
</style>

<template>
  <div v-if="installSource && installSource.source !== 'unknown'" class="source-detail-bar">
    <!-- Visually mirrors PluginMetricsInline: one capsule per field,
         same gap/radius/background as the metrics bar. Same 12 px text,
         same icon-in-secondary-colour treatment, same tabular numerics
         on any version strings. -->
    <div class="source-detail-bar__cells">
      <div class="source-cell">
        <el-icon class="source-cell__icon" :size="13"><Clock /></el-icon>
        <span class="source-cell__value">{{ installedAtDisplay }}</span>
        <span class="source-cell__label">{{ t('plugins.installSource.labels.installedAt') }}</span>
      </div>

      <template v-if="isImported">
        <div class="source-cell">
          <el-icon class="source-cell__icon" :size="13"><Document /></el-icon>
          <span class="source-cell__value source-cell__value--mono">{{ importedDetail.package_filename }}</span>
          <span class="source-cell__label">{{ t('plugins.installSource.labels.packageFilename') }}</span>
        </div>
        <div class="source-cell" :title="importedDetail.package_sha256">
          <el-icon class="source-cell__icon" :size="13"><Key /></el-icon>
          <span class="source-cell__value source-cell__value--mono">{{ sha256Short }}</span>
          <span class="source-cell__label">SHA-256</span>
        </div>
      </template>

      <template v-if="isMarket">
        <div class="source-cell">
          <el-icon class="source-cell__icon" :size="13"><ShoppingCart /></el-icon>
          <span class="source-cell__value source-cell__value--mono">{{ marketDetail.plugin_market_id }}</span>
          <span class="source-cell__label">{{ t('plugins.installSource.labels.marketId') }}</span>
        </div>
        <div class="source-cell">
          <el-icon class="source-cell__icon" :size="13"><Collection /></el-icon>
          <span class="source-cell__value">{{ marketDetail.version }}</span>
          <span class="source-cell__label">{{ t('plugins.installSource.labels.version') }}</span>
        </div>
        <!-- v2: Market 渠道 (stable / beta)；空时不显示 -->
        <div v-if="marketChannelDisplay" class="source-cell">
          <el-icon class="source-cell__icon" :size="13"><Connection /></el-icon>
          <span class="source-cell__value">{{ marketChannelDisplay }}</span>
          <span class="source-cell__label">{{ t('plugins.installSource.labels.channel') }}</span>
        </div>
        <!-- v2: Market 实际下载字节的 sha256；v1 entry 为空，故仅在有值时显示 -->
        <div
          v-if="marketSha256Short"
          class="source-cell"
          :title="marketDetail.package_sha256"
        >
          <el-icon class="source-cell__icon" :size="13"><Key /></el-icon>
          <span class="source-cell__value source-cell__value--mono">{{ marketSha256Short }}</span>
          <span class="source-cell__label">SHA-256</span>
        </div>
        <div v-if="marketDetail.previous_version" class="source-cell">
          <el-icon class="source-cell__icon" :size="13"><Back /></el-icon>
          <span class="source-cell__value">{{ marketDetail.previous_version }}</span>
          <span class="source-cell__label">{{ t('plugins.installSource.labels.previousVersion') }}</span>
        </div>
        <div
          v-if="latestVersion && latestVersion !== marketDetail.version"
          class="source-cell source-cell--alert"
        >
          <el-icon class="source-cell__icon" :size="13"><Top /></el-icon>
          <span class="source-cell__value">{{ latestVersion }}</span>
          <span class="source-cell__label">{{ t('plugins.installSource.labels.latestAvailable') }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Clock,
  Document,
  Key,
  ShoppingCart,
  Collection,
  Back,
  Top,
  Connection,
} from '@element-plus/icons-vue'
import type {
  PluginInstallSource,
  PluginInstallSourceDetailImported,
  PluginInstallSourceDetailMarket,
} from '@/types/api'

interface Props {
  installSource?: PluginInstallSource
  /** Market's latest version for this plugin, if known. Caller looks it
   *  up via the market versions store. */
  latestVersion?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  installSource: undefined,
  latestVersion: null,
})

const { t, locale } = useI18n()

const isImported = computed(() => props.installSource?.source === 'imported')
const isMarket = computed(() => props.installSource?.source === 'market')

const importedDetail = computed<PluginInstallSourceDetailImported>(() => {
  return props.installSource?.source_detail as PluginInstallSourceDetailImported
})

const marketDetail = computed<PluginInstallSourceDetailMarket>(() => {
  return props.installSource?.source_detail as PluginInstallSourceDetailMarket
})

/** First 8 hex chars of the sha256 — enough to eyeball; the full 64-char
 *  hash sits in the cell's ``title`` tooltip. */
const sha256Short = computed(() => {
  const full = importedDetail.value?.package_sha256 ?? ''
  return full.slice(0, 8) + (full.length > 8 ? '…' : '')
})

/** v2 (neko-market-version-sync §3.1.1): Market 实际下载字节的 sha256
 *  缩写；空字符串（v1 entry 升上来的）不显示对应 cell。 */
const marketSha256Short = computed(() => {
  const full = marketDetail.value?.package_sha256 ?? ''
  if (!full) return ''
  return full.slice(0, 8) + (full.length > 8 ? '…' : '')
})

/** v2: Market 渠道展示文本；'stable' / 'beta' 走 i18n，未知值原样展示。 */
const marketChannelDisplay = computed(() => {
  const ch = marketDetail.value?.channel
  if (!ch) return ''
  if (ch === 'stable') return t('plugins.installSource.channelLabels.stable')
  if (ch === 'beta') return t('plugins.installSource.channelLabels.beta')
  return ch
})

/** Parse the backend's canonical timestamp; fall back to raw on malformed
 *  input (should not happen for backend-produced values). */
const installedAtDisplay = computed(() => {
  const raw = props.installSource?.installed_at
  if (!raw) return '—'
  const dt = new Date(raw)
  if (Number.isNaN(dt.getTime())) return raw
  return dt.toLocaleString(locale.value || undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
})
</script>

<style scoped>
/* Layout + visuals mirror PluginMetricsInline's ``.metrics-bar`` so the
 * two inline strips read as members of the same family. Any change to
 * the metrics bar's styling should be mirrored here. */
.source-detail-bar {
  will-change: height, opacity, margin-top;
  margin-top: 8px;
}

.source-detail-bar__cells {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 1px 0;
}

.source-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--el-fill-color-light) 70%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 30%, transparent);
  font-size: 12px;
  line-height: 1;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease;
}

.source-cell--alert {
  background: color-mix(in srgb, var(--el-color-warning) 10%, var(--el-bg-color));
  border-color: color-mix(in srgb, var(--el-color-warning) 35%, var(--el-border-color));
}

.source-cell__icon {
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
}

.source-cell--alert .source-cell__icon {
  color: var(--el-color-warning);
}

.source-cell__value {
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

.source-cell--alert .source-cell__value {
  color: var(--el-color-warning);
}

.source-cell__value--mono {
  font-family: var(--el-font-family-mono, 'SF Mono', 'JetBrains Mono', 'Menlo', monospace);
  font-size: 11px;
  /* Truncate mid-length mono strings (filenames, sha prefixes) so they
   * don't push the row wider than its peers on small screens. */
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-cell__label {
  color: var(--el-text-color-secondary);
  font-size: 11px;
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .source-cell__label {
    display: none;
  }
}
</style>

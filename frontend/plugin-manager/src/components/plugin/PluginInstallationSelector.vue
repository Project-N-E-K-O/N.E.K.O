<template>
  <section v-if="visible" class="runtime-version" aria-live="polite">
    <div class="runtime-version__header">
      <div>
        <h3>{{ t('plugins.runtimeVersion.title') }}</h3>
        <p>{{ t('plugins.runtimeVersion.description') }}</p>
      </div>
      <el-button text :loading="loading" @click="loadInstallations">
        {{ t('common.refresh') }}
      </el-button>
    </div>

    <el-skeleton v-if="loading && !projection" :rows="2" animated />
    <el-alert
      v-else-if="loadFailed || projection?.status === 'blocked'"
      type="warning"
      :closable="false"
      show-icon
      :title="t('plugins.runtimeVersion.unavailable')"
      :description="t('plugins.runtimeVersion.unavailableHint')"
    />
    <div v-else-if="projection" class="runtime-version__choices">
      <article
        v-for="candidate in projection.candidates"
        :key="candidate.selection_id"
        class="runtime-version__choice"
        :class="{ 'runtime-version__choice--active': candidate.active }"
      >
        <div class="runtime-version__choice-copy">
          <div class="runtime-version__choice-title">
            <strong>{{ sourceLabel(candidate.source) }}</strong>
            <el-tag v-if="candidate.active" size="small" type="success">
              {{ t('plugins.runtimeVersion.current') }}
            </el-tag>
            <el-tag v-else size="small" type="info">v{{ candidate.version }}</el-tag>
          </div>
          <span>{{ candidate.name }} · v{{ candidate.version }}</span>
          <small>{{ candidateHint(candidate) }}</small>
        </div>
        <el-button
          v-if="!candidate.active"
          type="primary"
          plain
          :loading="switchingSelection === candidate.selection_id"
          :disabled="!candidate.selectable || Boolean(switchingSelection)"
          @click="confirmSwitch(candidate)"
        >
          {{ t('plugins.runtimeVersion.useThisVersion') }}
        </el-button>
      </article>
    </div>

    <p v-if="projection && projection.candidates.length > 1" class="runtime-version__shared-data">
      {{ t('plugins.runtimeVersion.sharedDataHint') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { getPluginInstallations, switchPluginInstallation } from '@/api/plugins'
import { usePluginStore } from '@/stores/plugin'
import { compareVersion } from '@/utils/version'
import type {
  PluginInstallationCandidate,
  PluginInstallationProjection,
  PluginInstallSourceChannel,
} from '@/types/api'

const props = defineProps<{ pluginId: string }>()
const { t } = useI18n()
const pluginStore = usePluginStore()
const projection = ref<PluginInstallationProjection | null>(null)
const loading = ref(false)
const loadFailed = ref(false)
const switchingSelection = ref('')

const visible = computed(() => (
  loading.value
  || loadFailed.value
  || projection.value?.status === 'blocked'
  || (projection.value?.candidates.length || 0) > 1
))

function sourceLabel(source: PluginInstallSourceChannel): string {
  return t(`plugins.installSource.channel.${source}`)
}

function candidateHint(candidate: PluginInstallationCandidate): string {
  if (!candidate.selectable && candidate.reason) {
    return t('plugins.runtimeVersion.blockedReason', { reason: candidate.reason })
  }
  if (candidate.kind === 'builtin') return t('plugins.runtimeVersion.builtinHint')
  if (candidate.source === 'market') return t('plugins.runtimeVersion.marketHint')
  if (candidate.source === 'imported') return t('plugins.runtimeVersion.importedHint')
  return t('plugins.runtimeVersion.localHint')
}

async function loadInstallations() {
  loading.value = true
  loadFailed.value = false
  try {
    projection.value = await getPluginInstallations(props.pluginId)
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

async function confirmSwitch(candidate: PluginInstallationCandidate) {
  const current = projection.value
  if (!current || !candidate.selectable || candidate.active) return
  try {
    const activeCandidate = current.candidates.find(item => item.active)
    const isDowngrade = activeCandidate
      ? compareVersion(candidate.version, activeCandidate.version) < 0
      : false
    await ElMessageBox.confirm(
      t(isDowngrade ? 'plugins.runtimeVersion.confirmDowngradeBody' : 'plugins.runtimeVersion.confirmBody', {
        source: sourceLabel(candidate.source),
        version: candidate.version,
        currentVersion: activeCandidate?.version || t('common.unknown'),
      }),
      t('plugins.runtimeVersion.confirmTitle'),
      {
        confirmButtonText: t('plugins.runtimeVersion.confirmAction'),
        cancelButtonText: t('common.cancel'),
        type: 'warning',
      },
    )
  } catch {
    return
  }

  switchingSelection.value = candidate.selection_id
  try {
    await switchPluginInstallation(
      props.pluginId,
      candidate.selection_id,
      current.generation,
    )
    await Promise.all([
      loadInstallations(),
      pluginStore.fetchPlugins(true),
      pluginStore.fetchPluginStatus(props.pluginId),
    ])
    ElMessage.success(t('plugins.runtimeVersion.switchSucceeded'))
  } catch {
    ElMessage.error(t('plugins.runtimeVersion.switchFailed'))
    await loadInstallations()
  } finally {
    switchingSelection.value = ''
  }
}

onMounted(loadInstallations)
watch(() => props.pluginId, loadInstallations)
</script>

<style scoped>
.runtime-version {
  margin-top: 20px;
  padding: 18px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 12px;
  background: var(--el-fill-color-extra-light);
}

.runtime-version__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.runtime-version__header h3,
.runtime-version__header p {
  margin: 0;
}

.runtime-version__header p {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.runtime-version__choices {
  display: grid;
  gap: 10px;
}

.runtime-version__choice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  background: var(--el-bg-color);
}

.runtime-version__choice--active {
  border-color: var(--el-color-success-light-5);
  background: var(--el-color-success-light-9);
}

.runtime-version__choice-copy {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.runtime-version__choice-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-version__choice-copy > span,
.runtime-version__choice-copy > small {
  color: var(--el-text-color-secondary);
}

.runtime-version__choice-copy > small {
  word-break: break-word;
}

.runtime-version__shared-data {
  margin: 12px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

@media (max-width: 640px) {
  .runtime-version__choice {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>

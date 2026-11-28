<template>
  <section class="mirror-source-panel" data-yui-guide-id="github-mirror-source-panel">
    <header class="mirror-source-panel__header">
      <div>
        <div class="mirror-source-panel__title">
          <el-icon><Connection /></el-icon>
          <span>{{ t('mirrorSource.title') }}</span>
        </div>
        <p>{{ t('mirrorSource.description') }}</p>
      </div>
      <el-button text circle :aria-label="t('mirrorSource.close')" @click="$emit('close')">
        <el-icon><Close /></el-icon>
      </el-button>
    </header>

    <div class="mirror-source-panel__content">
      <el-form label-position="top">
        <el-form-item :label="t('mirrorSource.sourceMode')">
          <el-select :model-value="mode" @update:model-value="setModeFromSelect">
            <el-option :label="t('mirrorSource.modeDirect')" value="direct" />
            <el-option :label="t('mirrorSource.auto')" value="auto" />
            <el-option :label="t('mirrorSource.specified')" value="specified" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="mode === 'auto'" :label="t('mirrorSource.source')">
          <el-input :model-value="t('mirrorSource.autoPending')" readonly />
        </el-form-item>

        <template v-if="mode === 'specified'">
          <el-form-item :label="t('mirrorSource.githubSource')">
            <el-select :model-value="specifiedSourceId" @update:model-value="setSpecifiedSourceFromSelect">
              <el-option
                v-for="item in sources"
                :key="item.id"
                :label="item.label"
                :value="item.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item :label="t('mirrorSource.pypiSource')">
            <el-select :model-value="specifiedPypiSourceId" @update:model-value="setSpecifiedPypiSourceFromSelect">
              <el-option
                v-for="item in pypiSources"
                :key="item.id"
                :label="item.label"
                :value="item.id"
              />
            </el-select>
          </el-form-item>
        </template>

        <el-button
          v-if="mode === 'auto'"
          type="primary"
          plain
          :loading="resetting"
          @click="resetHistory"
        >
          {{ t('mirrorSource.resetHistory') }}
        </el-button>
      </el-form>

      <el-alert v-if="saveFailed" type="error" :closable="false" :title="t('mirrorSource.saveFailed')" />

      <el-alert type="info" :closable="false" show-icon>
        <template #title>
          {{ t('mirrorSource.notice') }} {{ t('mirrorSource.preferenceNotice') }}
        </template>
      </el-alert>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { Close, Connection } from '@element-plus/icons-vue'
import { useGithubMirrorSource, type GithubMirrorMode } from '@/composables/useGithubMirrorSource'

defineEmits<{ close: [] }>()
const { t } = useI18n()

const {
  mode,
  specifiedSourceId,
  specifiedPypiSourceId,
  sources,
  pypiSources,
  saveFailed,
  setMode,
  setSpecifiedSourceId,
  setSpecifiedPypiSourceId,
  ensureDownloadPreferences,
  resetDownloadHistory,
} = useGithubMirrorSource()
const resetting = ref(false)

function setModeFromSelect(value: string | number | boolean | undefined) {
  if (value === 'direct' || value === 'auto' || value === 'specified') {
    setMode(value as GithubMirrorMode)
  }
}

function setSpecifiedSourceFromSelect(value: string | number | boolean | undefined) {
  const source = sources.find((item) => item.id === value)
  if (source) setSpecifiedSourceId(source.id)
}

function setSpecifiedPypiSourceFromSelect(value: string | number | boolean | undefined) {
  const source = pypiSources.find((item) => item.id === value)
  if (source) setSpecifiedPypiSourceId(source.id)
}

async function resetHistory() {
  resetting.value = true
  try {
    await resetDownloadHistory()
    ElMessage.success(t('mirrorSource.resetSuccess'))
  } catch {
    ElMessage.error(t('mirrorSource.resetFailed'))
  } finally {
    resetting.value = false
  }
}

onMounted(() => {
  ensureDownloadPreferences().catch(() => {
    ElMessage.error(t('mirrorSource.saveFailed'))
  })
})
</script>

<style scoped>
.mirror-source-panel { padding: 18px; border: 1px solid var(--surface-border); border-radius: var(--radius-card, 16px); background: var(--surface-card); box-shadow: var(--surface-shadow); }
.mirror-source-panel__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 16px; border-bottom: 1px solid var(--surface-border); }
.mirror-source-panel__title { display: flex; align-items: center; gap: 8px; color: var(--el-text-color-primary); font-size: 18px; font-weight: 650; }
.mirror-source-panel__title .el-icon { color: var(--el-color-primary); }
.mirror-source-panel__header p { max-width: 410px; margin: 7px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.55; }
.mirror-source-panel__content { padding-top: 20px; }
.mirror-source-panel :deep(.el-select) { width: 100%; }
</style>

<template>
    <el-dialog
      v-model="dialogVisible"
      class="plugin-import-dialog"
      :title="t('plugins.importDialogTitle')"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="!importing"
      :close-on-press-escape="!importing"
      :show-close="!importing"
      @closed="clearSelectedImportFile"
    >
      <div
        class="plugin-import-dropzone"
        :class="{
          'plugin-import-dropzone--active': importDropActive,
          'plugin-import-dropzone--selected': selectedImportFile,
          'plugin-import-dropzone--disabled': importing,
        }"
        role="button"
        :tabindex="importing ? -1 : 0"
        :aria-disabled="importing"
        @click="triggerImportFile"
        @keydown.enter.prevent="triggerImportFile"
        @keydown.space.prevent="triggerImportFile"
        @dragenter.prevent="importDropActive = true"
        @dragover.prevent="importDropActive = true"
        @dragleave.prevent="importDropActive = false"
        @drop.prevent="handleImportDrop"
      >
        <el-icon class="plugin-import-dropzone__icon"><Upload /></el-icon>
        <template v-if="selectedImportFile">
          <strong class="plugin-import-dropzone__filename">{{ selectedImportFile.name }}</strong>
          <span>{{ formatImportFileSize(selectedImportFile.size) }}</span>
        </template>
        <template v-else>
          <strong>{{ t('plugins.importDropHint') }}</strong>
          <span>{{ t('plugins.importDropSubHint') }}</span>
        </template>
      </div>
      <input
        ref="importFileInputRef"
        type="file"
        accept=".neko-plugin,.neko-bundle"
        class="import-file-input"
        @change="handleImportFileChange"
      />
      <p class="plugin-import-dialog__limit">{{ t('plugins.importFileLimit') }}</p>

      <template #footer>
        <el-button :disabled="importing" @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button :disabled="importing" @click="triggerImportFile">{{ t('plugins.importChooseFile') }}</el-button>
        <el-button type="primary" :disabled="!selectedImportFile" :loading="importing" @click="importSelectedPluginPackage">
          {{ importing ? t('plugins.importing') : t('plugins.import') }}
        </el-button>
      </template>
    </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { uploadPluginPackage } from '@/api/pluginCli'
import { usePluginPackageInstaller } from '@/composables/usePluginPackageInstaller'
import { resolvePluginPackageErrorMessage } from '@/utils/pluginPackageError'
import { notifyPluginInstallOutcome } from '@/utils/pluginInstallResult'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{
  'update:visible': [visible: boolean]
  'busy-change': [busy: boolean]
  installed: []
}>()
const { t } = useI18n()
const { installPackagePath: installImportedPackage } = usePluginPackageInstaller()
const importing = ref(false)
const importFileInputRef = ref<HTMLInputElement | null>(null)
const selectedImportFile = ref<File | null>(null)
const importDropActive = ref(false)
const dialogVisible = computed({
  get: () => props.visible,
  set: (visible: boolean) => { if (!importing.value) emit('update:visible', visible) },
})
watch(importing, (busy) => emit('busy-change', busy), { flush: 'sync' })

const PLUGIN_PACKAGE_MAX_BYTES = 500 * 1024 * 1024
const PLUGIN_PACKAGE_SUFFIXES = ['.neko-plugin', '.neko-bundle']

function triggerImportFile() {
  if (importing.value) return
  importFileInputRef.value?.click()
}

function clearSelectedImportFile() {
  selectedImportFile.value = null
  importDropActive.value = false
}

function formatImportFileSize(size: number): string {
  const sizeInMiB = size / 1024 / 1024
  return `${Number.isInteger(sizeInMiB) ? sizeInMiB : sizeInMiB.toFixed(size >= 10 * 1024 * 1024 ? 1 : 2)} MiB`
}

function selectImportFile(file: File) {
  if (importing.value) return
  const filename = file.name.toLowerCase()
  if (!PLUGIN_PACKAGE_SUFFIXES.some((suffix) => filename.endsWith(suffix))) {
    selectedImportFile.value = null
    ElMessage.error(t('plugins.importUnsupportedFile'))
    return
  }
  if (file.size > PLUGIN_PACKAGE_MAX_BYTES) {
    selectedImportFile.value = null
    ElMessage.error(t('plugins.importFileTooLarge', { limit: formatImportFileSize(PLUGIN_PACKAGE_MAX_BYTES) }))
    return
  }
  selectedImportFile.value = file
}

function handleImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // Reset input so the same file can be re-selected
  input.value = ''
  if (importing.value) return
  if (file) selectImportFile(file)
}

function handleImportDrop(event: DragEvent) {
  importDropActive.value = false
  if (importing.value) return
  const file = event.dataTransfer?.files?.[0]
  if (file) selectImportFile(file)
}

async function importSelectedPluginPackage() {
  const file = selectedImportFile.value
  if (!file || importing.value) return

  importDropActive.value = false
  importing.value = true
  try {
    const upload = await uploadPluginPackage(file)
    const result = await installImportedPackage(upload.path, {
      installSource: 'imported',
      discardOnFailure: true,
    })
    if (!result) return
    const count = result.installed_plugin_count ?? 0
    const outcome = notifyPluginInstallOutcome(result, t, ElMessage, {
      plugin: file.name,
      successMessage: t('plugins.importSuccess', { name: file.name, count }),
    })
    if (outcome.level === 'error') return
    emit('update:visible', false)
    emit('installed')
  } catch (error: any) {
    console.error('Failed to import plugin package:', error)
    ElMessage.error(resolvePluginPackageErrorMessage(error, t, 'upload'))
  } finally {
    importing.value = false
  }
}

</script>

<style scoped>
.import-file-input {
  display: none;
}

.plugin-import-dropzone {
  display: flex;
  min-height: 190px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  border: 1.5px dashed color-mix(in srgb, var(--el-color-primary) 46%, var(--el-border-color));
  border-radius: var(--radius-panel);
  background: color-mix(in srgb, var(--el-color-primary) 4%, var(--el-bg-color));
  color: var(--el-text-color-secondary);
  cursor: pointer;
  text-align: center;
  transition:
    border-color var(--motion-duration-fast) var(--motion-ease-standard),
    background-color var(--motion-duration-fast) var(--motion-ease-standard),
    transform var(--motion-duration-fast) var(--motion-ease-standard);
}

.plugin-import-dropzone:hover,
.plugin-import-dropzone--active {
  border-color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 10%, var(--el-bg-color));
  transform: translateY(-1px);
}

.plugin-import-dropzone--selected {
  border-style: solid;
  border-color: color-mix(in srgb, var(--el-color-success) 62%, var(--el-border-color));
  background: color-mix(in srgb, var(--el-color-success) 7%, var(--el-bg-color));
}

.plugin-import-dropzone--disabled {
  cursor: not-allowed;
  opacity: 0.65;
  pointer-events: none;
  transform: none;
}

.plugin-import-dropzone strong {
  color: var(--el-text-color-primary);
}

.plugin-import-dropzone__icon {
  font-size: 34px;
  color: var(--el-color-primary);
}

.plugin-import-dropzone__filename {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plugin-import-dialog__limit {
  margin: 12px 2px 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  .plugin-import-dropzone {
    transition: none;
  }

  .plugin-import-dropzone:hover,
  .plugin-import-dropzone--active {
    transform: none;
  }
}

</style>

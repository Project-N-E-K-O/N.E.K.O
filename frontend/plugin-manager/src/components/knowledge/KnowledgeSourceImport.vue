<template>
  <el-dialog :model-value="modelValue" :title="t('knowledge.importDocumentTitle')" width="min(720px, 94vw)" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving" @close="close">
    <template v-if="file">
      <el-form v-if="!preview" label-position="top" @submit.prevent="prepare">
        <el-form-item :label="t('knowledge.sourceName')"><span>{{ file.name }}</span></el-form-item>
        <el-form-item v-if="!pack" :label="t('knowledge.sourceTarget')">
          <el-select v-model="targetId" class="source-input">
            <el-option value="" :label="t('knowledge.newSourcePack')" />
            <el-option v-for="row in importedPacks" :key="row.pack_id" :value="row.pack_id" :label="row.source.name || row.pack_id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!targetId" :label="t('knowledge.sourcePackName')">
          <el-input v-model="packName" maxlength="160" />
        </el-form-item>
      </el-form>
      <template v-else>
        <p>{{ t('knowledge.sourcePreviewMeta', { name: preview.name, chars: preview.chars }) }}</p>
        <pre class="source-preview" data-testid="source-preview">{{ preview.content }}</pre>
        <el-alert :title="t(targetPack?.enabled !== false && targetPack ? 'knowledge.enabledPackDisclosure' : 'knowledge.cloudDisclosure')" type="warning" :closable="false" show-icon />
        <el-checkbox v-if="targetPack?.enabled !== false && targetPack" v-model="acknowledged" data-testid="source-privacy-confirm">
          {{ t('knowledge.acknowledgeEnabledPack') }}
        </el-checkbox>
        <el-checkbox v-else-if="!targetPack" v-model="enableAfterImport" data-testid="source-enable-after-import">
          {{ t('knowledge.enableImported') }}
        </el-checkbox>
      </template>
    </template>
    <template #footer>
      <el-button :disabled="saving" @click="close">{{ t('common.cancel') }}</el-button>
      <el-button v-if="!preview" type="primary" :loading="loading" :disabled="!file || targetMissing" @click="prepare">{{ t('knowledge.previewDocument') }}</el-button>
      <el-button v-else type="primary" :loading="saving" :disabled="requiresAcknowledgment && !acknowledged" @click="commit">{{ t('knowledge.sourceCommit') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { knowledgeApi, knowledgeFailureReason, type KnowledgeDocumentPreview, type KnowledgeImportResponse, type KnowledgePack } from '@/api/knowledge'

const props = defineProps<{ modelValue: boolean; file: File | null; pack?: KnowledgePack | null; docId?: string; packs?: KnowledgePack[] }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; committed: [KnowledgeImportResponse] }>()
const { t } = useI18n()
const targetId = ref('')
const packName = ref('')
const preview = ref<KnowledgeDocumentPreview | null>(null)
const loading = ref(false)
const saving = ref(false)
const enableAfterImport = ref(false)
const acknowledged = ref(false)
let generation = 0

// Cancelling or replacing a preview deletes that preview's own server files.
// Best effort: a failure keeps the files, exactly as before this request.
function discardPreview(previewId: string) {
  void knowledgeApi.discardSourcePreview({ preview_id: previewId }).catch(() => undefined)
}

// Previews invalidated while their own commit is in flight are discarded only
// after that request settles; the server keeps one the commit froze first.
const committing = new Set<string>()
const discardAfterCommit = new Set<string>()

function dropPreview() {
  const previous = preview.value
  preview.value = null
  if (!previous) return
  if (committing.has(previous.preview_id)) discardAfterCommit.add(previous.preview_id)
  else discardPreview(previous.preview_id)
}
// A finished offline pack has no originals to append to, even when its origin is imported.
const importedPacks = computed(() => (props.packs ?? []).filter(row => row.sources_managed === true && !row.broken && !row.retained))
const targetPack = computed(() => props.pack ?? importedPacks.value.find(row => row.pack_id === targetId.value))
const targetMissing = computed(() => Boolean(targetId.value && !targetPack.value))
const requiresAcknowledgment = computed(() => Boolean(targetPack.value && targetPack.value.enabled !== false))

watch([() => props.modelValue, () => props.file, () => props.pack?.pack_id, () => props.docId], () => {
  // A slow preview for a previously selected file must not replace this dialog.
  generation += 1
  dropPreview()
  loading.value = false
  // A commit still in flight keeps the dialog busy: never start an overlapping one.
  saving.value = committing.size > 0
  targetId.value = props.pack?.pack_id ?? ''
  packName.value = props.file?.name.replace(/\.[^.]+$/, '') ?? ''
  enableAfterImport.value = false
  acknowledged.value = false
}, { immediate: true })

watch([targetId, packName], () => {
  // Destination edits invalidate the preview already in flight, including its
  // disclosure and eventual commit identity.
  generation += 1
  dropPreview()
  loading.value = false
  acknowledged.value = false
})

watch(() => targetPack.value?.pack_id, () => {
  // A missing destination invalidates the server-frozen identity, not just its label.
  // Ordinary polling updates of the same pack keep the preview intact.
  generation += 1
  dropPreview()
  loading.value = false
  acknowledged.value = false
})
watch(requiresAcknowledgment, () => { acknowledged.value = false })

// Leaving the page through in-app navigation is also a cancel. Closing the
// browser itself cannot be observed reliably and keeps the preview.
onBeforeUnmount(() => {
  generation += 1
  dropPreview()
})

function close() {
  if (saving.value) return
  generation += 1
  dropPreview()
  emit('update:modelValue', false)
}

async function prepare() {
  if (!props.file || loading.value || targetMissing.value) return
  const request = ++generation
  loading.value = true
  try {
    const response = await knowledgeApi.previewSource(props.file, {
      filename: props.file.name,
      ...(targetPack.value ? { pack_id: targetPack.value.pack_id } : { name: packName.value.trim() }),
      ...(props.docId ? { doc_id: props.docId } : {}),
    })
    if (request === generation) preview.value = response.preview
    // A preview that finished after cancel/replace was never shown; delete it too.
    else discardPreview(response.preview.preview_id)
  } catch (error) {
    if (request === generation) ElMessage.error(t(`knowledge.reasons.${knowledgeFailureReason(error)}`))
  } finally {
    if (request === generation) loading.value = false
  }
}

async function commit() {
  if (!preview.value || saving.value || committing.size > 0 || (requiresAcknowledgment.value && !acknowledged.value)) return
  const previewId = preview.value.preview_id
  // File or destination changes advance the generation; this dialog then no
  // longer belongs to the commit and must not be closed by its success.
  const started = generation
  committing.add(previewId)
  saving.value = true
  try {
    // Existing pack switches are preserved by the server; only a new pack may
    // opt into retrieval here, and that checkbox always starts unchecked.
    const response = await knowledgeApi.commitSource({
      preview_id: preview.value.preview_id, sha256: preview.value.sha256,
      enabled: targetPack.value ? targetPack.value.enabled !== false : enableAfterImport.value,
      // This is consent for this document, distinct from the pack's mutable switch.
      enabled_confirmed: targetPack.value ? requiresAcknowledgment.value && acknowledged.value : enableAfterImport.value,
    })
    // The committed preview is frozen server-side; closing must not discard it.
    if (preview.value?.preview_id === previewId) preview.value = null
    ElMessage.success(t('knowledge.sourceQueued'))
    // The parent must observe admission before its first overview refresh:
    // a small document may already be active in that refresh's jobs snapshot.
    emit('committed', response)
    if (generation === started) emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(t(`knowledge.reasons.${knowledgeFailureReason(error)}`))
  } finally {
    committing.delete(previewId)
    saving.value = committing.size > 0
    if (discardAfterCommit.delete(previewId)) discardPreview(previewId)
  }
}
</script>

<style scoped>
.source-input { width: 100%; }
.source-preview { max-height: 45vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 14px; background: var(--el-fill-color-light); border-radius: 8px; }
.el-checkbox { height: auto; margin-top: 14px; white-space: normal; }
</style>

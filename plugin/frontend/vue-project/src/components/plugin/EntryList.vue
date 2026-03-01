<template>
  <div class="entry-list">
    <EmptyState v-if="entries.length === 0" :description="$t('plugins.noEntries')" />
    <el-table v-else :data="entries" stripe>
      <el-table-column prop="name" :label="$t('plugins.entryName')" width="200" />
      <el-table-column prop="description" :label="$t('plugins.entryDescription')" />
      <el-table-column :label="$t('plugins.actions')" width="120">
        <template #default="{ row }">
          <el-button
            type="primary"
            size="small"
            :disabled="!isRunning"
            @click="openExecuteDialog(row)"
          >
            {{ $t('plugins.trigger') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="$t('plugins.trigger')">
      <div v-if="currentEntry">
        <el-alert
          v-if="!isRunning"
          type="warning"
          :closable="false"
          :title="$t('plugins.triggerFailed')"
          :description="$t('status.stopped')"
          show-icon
          style="margin-bottom: 12px"
        />
        <p>
          {{ $t('plugins.entryName') }}: {{ currentEntry.name }}
        </p>
        <p>
          {{ $t('plugins.entryDescription') }}: {{ currentEntry.description || '-' }}
        </p>

        <!-- 当有 input_schema 时，按 schema 生成表单字段 -->
        <el-form v-if="hasSchema" label-position="top">
          <el-form-item
            v-for="(fieldSchema, key) in currentEntry.input_schema?.properties || {}"
            :key="key as string"
            :label="fieldSchema.description || (key as string)"
          >
            <el-input
              v-if="!fieldSchema.type || fieldSchema.type === 'string'"
              v-model="formModel[key as string]"
            />
            <el-input-number
              v-else-if="fieldSchema.type === 'number' || fieldSchema.type === 'integer'"
              v-model="formModel[key as string]"
            />
            <el-switch
              v-else-if="fieldSchema.type === 'boolean'"
              v-model="formModel[key as string]"
            />
            <el-input
              v-else
              v-model="formModel[key as string]"
            />
          </el-form-item>
        </el-form>

        <!-- 无 schema 时退回到 JSON 文本输入 -->
        <el-form v-else label-position="top">
          <el-form-item :label="$t('plugins.argsJson')">
            <el-input
              v-model="argsText"
              type="textarea"
              :rows="6"
              placeholder="{ }"
            />
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!isRunning" @click="handleExecute">
          {{ $t('plugins.trigger') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import EmptyState from '@/components/common/EmptyState.vue'
import type { PluginEntry } from '@/types/api'
import { createRun } from '@/api/runs'

interface Props {
  entries: PluginEntry[]
  pluginId: string
  pluginStatus: string
}

const props = defineProps<Props>()
const { t } = useI18n()

const isRunning = computed(() => props.pluginStatus === 'running')

const dialogVisible = ref(false)
const currentEntry = ref<PluginEntry | null>(null)
const argsText = ref<string>('{}')
const submitting = ref(false)
const formModel = ref<Record<string, any>>({})

const hasSchema = computed(() => {
  const entry = currentEntry.value
  const schema = entry?.input_schema
  return !!(schema?.properties && typeof schema.properties === 'object')
})

function initFormModelFromSchema(entry: PluginEntry) {
  const schema = entry.input_schema
  const schemaProps = schema?.properties || {}
  const initial: Record<string, any> = {}
  for (const key in schemaProps) {
    if (!Object.prototype.hasOwnProperty.call(schemaProps, key)) continue
    const field = schemaProps[key]
    if (!field) continue
    if ('default' in field) {
      initial[key] = field.default
    } else {
      switch (field.type) {
        case 'number':
        case 'integer':
          initial[key] = 0
          break
        case 'boolean':
          initial[key] = false
          break
        default:
          initial[key] = ''
      }
    }
  }
  formModel.value = initial
}

function openExecuteDialog(entry: PluginEntry) {
  currentEntry.value = entry
  argsText.value = '{}'
  if (entry.input_schema?.properties && typeof entry.input_schema.properties === 'object') {
    initFormModelFromSchema(entry)
  } else {
    formModel.value = {}
  }
  dialogVisible.value = true
}

async function handleExecute() {
  if (!currentEntry.value) return
  if (!isRunning.value) {
    ElMessage.warning(t('status.stopped'))
    return
  }

  let parsedArgs: Record<string, any> = {}
  if (hasSchema.value) {
    parsedArgs = { ...formModel.value }
  } else {
    const raw = argsText.value?.trim()
    if (raw) {
      try {
        parsedArgs = JSON.parse(raw)
      } catch (e) {
        ElMessage.error(t('plugins.invalidJsonArgs'))
        return
      }
    }
  }

  submitting.value = true
  try {
    const resp = await createRun({
      plugin_id: props.pluginId,
      entry_id: currentEntry.value.id,
      args: parsedArgs,
    })
    const rid = resp?.run_id
    ElMessage.success(rid ? `${t('plugins.triggerSuccess')} (${rid})` : t('plugins.triggerSuccess'))
    dialogVisible.value = false
  } catch (e: any) {
    ElMessage.error(e?.message || t('plugins.triggerFailed'))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.entry-list {
  padding: 20px 0;
}
</style>


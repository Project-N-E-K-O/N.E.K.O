<template>
  <div class="cve" :style="indentStyle">
    <template v-if="kind === 'object'">
      <div class="obj">
        <div v-for="k in objectKeys" :key="k" class="row">
          <div class="k">
            <el-tag size="small" type="info">{{ k }}</el-tag>
          </div>
          <div class="v">
            <ConfigValueEditor
              :model-value="(modelValue as any)[k]"
              @update:model-value="(val) => updateObjectKey(k, val)"
              :path="childPath(k)"
            />
          </div>
          <div class="ops">
            <el-button
              v-if="!isProtectedKey(k)"
              size="small"
              type="danger"
              text
              @click="removeObjectKey(k)"
            >
              {{ t('common.delete') }}
            </el-button>
          </div>
        </div>

        <div class="add">
          <el-button size="small" @click="openAddKey">
            {{ t('plugins.addField') }}
          </el-button>
        </div>
      </div>

      <el-dialog v-model="addKeyDialog" :title="t('plugins.addField')" width="420px">
        <el-form label-position="top">
          <el-form-item :label="t('plugins.fieldName')">
            <el-input v-model="newKey" />
          </el-form-item>
          <el-form-item :label="t('plugins.fieldType')">
            <el-select v-model="newType" style="width: 100%">
              <el-option label="string" value="string" />
              <el-option label="number" value="number" />
              <el-option label="boolean" value="boolean" />
              <el-option label="object" value="object" />
              <el-option label="array" value="array" />
            </el-select>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="addKeyDialog = false">{{ t('common.cancel') }}</el-button>
          <el-button type="primary" @click="confirmAddKey">{{ t('common.confirm') }}</el-button>
        </template>
      </el-dialog>
    </template>

    <template v-else-if="kind === 'array'">
      <div class="arr">
        <div v-for="(item, idx) in (modelValue as any[])" :key="idx" class="row">
          <div class="k">
            <el-tag size="small" type="info">{{ idx }}</el-tag>
          </div>
          <div class="v">
            <ConfigValueEditor
              :model-value="item"
              @update:model-value="(val) => updateArrayIndex(idx, val)"
              :path="childPath(String(idx))"
            />
          </div>
          <div class="ops">
            <el-button size="small" type="danger" text @click="removeArrayIndex(idx)">
              {{ t('common.delete') }}
            </el-button>
          </div>
        </div>

        <div class="add">
          <el-button size="small" @click="addArrayItem">{{ t('plugins.addItem') }}</el-button>
        </div>
      </div>
    </template>

    <template v-else-if="kind === 'boolean'">
      <div class="input-wrap">
        <el-switch v-model="boolVal" :disabled="isReadOnly" @change="emitUpdate(boolVal)" />
      </div>
    </template>

    <template v-else-if="kind === 'number'">
      <div class="input-wrap">
        <el-input-number v-model="numVal" :step="1" :disabled="isReadOnly" @change="emitUpdate(numVal)" />
      </div>
    </template>

    <template v-else>
      <div class="input-wrap">
        <el-input v-model="strVal" :disabled="isReadOnly" @input="emitUpdate(strVal)" />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

interface Props {
  modelValue: any
  path?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{ (e: 'update:modelValue', v: any): void }>()
const { t } = useI18n()

const kind = computed<'object' | 'array' | 'string' | 'number' | 'boolean'>(() => {
  const v = props.modelValue
  if (Array.isArray(v)) return 'array'
  if (v !== null && typeof v === 'object') return 'object'
  if (typeof v === 'boolean') return 'boolean'
  if (typeof v === 'number') return 'number'
  return 'string'
})

const objectKeys = computed(() => {
  if (kind.value !== 'object') return []
  return Object.keys(props.modelValue || {}).sort()
})

const strVal = ref('')
const numVal = ref<number | undefined>(undefined)
const boolVal = ref(false)

watch(
  () => props.modelValue,
  (v) => {
    if (kind.value === 'string') strVal.value = v == null ? '' : String(v)
    if (kind.value === 'number') numVal.value = typeof v === 'number' ? v : undefined
    if (kind.value === 'boolean') boolVal.value = Boolean(v)
  },
  { immediate: true }
)

function emitUpdate(v: any) {
  emit('update:modelValue', v)
}

function childPath(k: string) {
  const base = props.path || ''
  return base ? `${base}.${k}` : k
}

function isProtectedKey(k: string) {
  const p = childPath(k)
  return p === 'plugin.id' || p === 'plugin.entry'
}

const isReadOnly = computed(() => {
  const p = props.path || ''
  return p === 'plugin.id' || p === 'plugin.entry'
})

const indentStyle = computed(() => {
  const p = props.path || ''
  if (!p) return {}
  const depth = p.split('.').length - 1
  return { paddingLeft: `${Math.min(depth, 6) * 12}px` }
})

function updateObjectKey(k: string, v: any) {
  const next = { ...(props.modelValue || {}) }
  next[k] = v
  emitUpdate(next)
}

function removeObjectKey(k: string) {
  const next = { ...(props.modelValue || {}) }
  delete next[k]
  emitUpdate(next)
}

function updateArrayIndex(idx: number, v: any) {
  const next = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  next[idx] = v
  emitUpdate(next)
}

function removeArrayIndex(idx: number) {
  const next = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  next.splice(idx, 1)
  emitUpdate(next)
}

function addArrayItem() {
  const next = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  next.push('')
  emitUpdate(next)
}

const addKeyDialog = ref(false)
const newKey = ref('')
const newType = ref<'string' | 'number' | 'boolean' | 'object' | 'array'>('string')

function openAddKey() {
  addKeyDialog.value = true
  newKey.value = ''
  newType.value = 'string'
}

function initialValueByType(tp: typeof newType.value) {
  if (tp === 'number') return 0
  if (tp === 'boolean') return false
  if (tp === 'object') return {}
  if (tp === 'array') return []
  return ''
}

function confirmAddKey() {
  const key = (newKey.value || '').trim()
  if (!key) return

  const next = { ...(props.modelValue || {}) }
  if (Object.prototype.hasOwnProperty.call(next, key)) {
    addKeyDialog.value = false
    return
  }

  next[key] = initialValueByType(newType.value)
  emitUpdate(next)
  addKeyDialog.value = false
}
</script>

<style scoped>
.cve {
  width: 100%;
}

.row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  flex-wrap: wrap;
  padding: 6px 0;
}

.k {
  display: flex;
  justify-content: flex-start;
  padding-top: 6px;
  flex: 0 0 160px;
  max-width: 220px;
  min-width: 120px;
}

.v {
  min-width: 0;
  flex: 1 1 420px;
}

.ops {
  display: flex;
  justify-content: flex-end;
  padding-top: 2px;
  flex: 0 0 auto;
}

.add {
  margin-top: 8px;
}

.input-wrap {
  width: 100%;
}

.input-wrap :deep(.el-input),
.input-wrap :deep(.el-input-number) {
  width: 100%;
}

@media (max-width: 640px) {
  .k {
    flex: 1 1 100%;
    max-width: none;
    padding-top: 0;
  }

  .v {
    flex: 1 1 100%;
  }

  .ops {
    width: 100%;
    justify-content: flex-start;
    padding-top: 0;
  }
}
</style>

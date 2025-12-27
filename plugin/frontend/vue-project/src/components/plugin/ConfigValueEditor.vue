<template>
  <div class="cve" :style="indentStyle">
    <template v-if="kind === 'object'">
      <div class="obj">
        <div v-for="k in objectKeys" :key="k" class="row" :class="rowClassForKey(k)">
          <div class="k">
            <el-tag size="small" type="info">{{ k }}</el-tag>
          </div>
          <div class="v">
            <ConfigValueEditor
              :model-value="isKeyDeleted(k) ? baselineChild(k) : (modelValue as any)[k]"
              @update:model-value="(val) => updateObjectKey(k, val)"
              :baseline-value="baselineChild(k)"
              :path="childPath(k)"
            />
          </div>
          <div class="ops">
            <el-button
              v-if="!isProtectedKey(k) && !isKeyDeleted(k)"
              size="small"
              type="danger"
              text
              @click="removeObjectKey(k)"
            >
              {{ t('common.delete') }}
            </el-button>
            <el-button
              v-else-if="!isProtectedKey(k) && isKeyDeleted(k)"
              size="small"
              type="primary"
              text
              @click="restoreObjectKey(k)"
            >
              {{ t('common.reset') }}
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
        <div v-for="(item, idx) in arrayItems" :key="idx" class="row" :class="rowClassForArrayIndex(idx)">
          <div class="k">
            <el-tag size="small" type="info">{{ idx }}</el-tag>
          </div>
          <div class="v">
            <ConfigValueEditor
              :model-value="item"
              @update:model-value="(val) => updateArrayIndex(idx, val)"
              :baseline-value="baselineArrayItem(idx)"
              :path="childPath(String(idx))"
            />
          </div>
          <div class="ops">
            <el-button
              v-if="idx < (modelValue as any[]).length"
              size="small"
              type="danger"
              text
              @click="removeArrayIndex(idx)"
            >
              {{ t('common.delete') }}
            </el-button>
            <el-button v-else size="small" type="primary" text @click="restoreArrayIndex(idx)">
              {{ t('common.reset') }}
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
import { ElMessage } from 'element-plus'

interface Props {
  modelValue: any
  path?: string
  baselineValue?: any
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
  const a = props.modelValue && typeof props.modelValue === 'object' ? props.modelValue : {}
  const b = props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}
  const keys = new Set<string>([...Object.keys(a || {}), ...Object.keys(b || {})])
  return Array.from(keys).sort()
})

const arrayItems = computed(() => {
  if (kind.value !== 'array') return []
  const a = Array.isArray(props.modelValue) ? props.modelValue : []
  const b = Array.isArray(props.baselineValue) ? props.baselineValue : []
  const len = Math.max(a.length, b.length)
  const items: any[] = []
  for (let i = 0; i < len; i++) {
    if (i < a.length) items.push(a[i])
    else items.push(b[i])
  }
  return items
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

function baselineChild(k: string) {
  const b = props.baselineValue
  if (b && typeof b === 'object' && !Array.isArray(b)) return (b as any)[k]
  return undefined
}

function isKeyDeleted(k: string) {
  if (kind.value !== 'object') return false
  const a = props.modelValue && typeof props.modelValue === 'object' ? props.modelValue : {}
  const b = props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}
  return !(k in (a as any)) && k in (b as any)
}

function rowClassForKey(k: string) {
  if (kind.value !== 'object') return ''
  const a = props.modelValue && typeof props.modelValue === 'object' ? props.modelValue : {}
  const b = props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}

  const inA = k in (a as any)
  const inB = k in (b as any)
  if (inA && !inB) return 'diff-added'
  if (!inA && inB) return 'diff-deleted'
  if (inA && inB) {
    const av = (a as any)[k]
    const bv = (b as any)[k]
    if (JSON.stringify(av) !== JSON.stringify(bv)) return 'diff-modified'
  }
  return ''
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

function restoreObjectKey(k: string) {
  const next = { ...(props.modelValue || {}) }
  next[k] = baselineChild(k)
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

function baselineArrayItem(idx: number) {
  const b = Array.isArray(props.baselineValue) ? props.baselineValue : []
  return b[idx]
}

function rowClassForArrayIndex(idx: number) {
  if (kind.value !== 'array') return ''
  const a = Array.isArray(props.modelValue) ? props.modelValue : []
  const b = Array.isArray(props.baselineValue) ? props.baselineValue : []
  if (idx < a.length && idx >= b.length) return 'diff-added'
  if (idx >= a.length && idx < b.length) return 'diff-deleted'
  if (idx < a.length && idx < b.length) {
    if (JSON.stringify(a[idx]) !== JSON.stringify(b[idx])) return 'diff-modified'
  }
  return ''
}

function restoreArrayIndex(idx: number) {
  const a = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  const b = Array.isArray(props.baselineValue) ? props.baselineValue : []
  if (idx >= 0 && idx < b.length) {
    while (a.length < idx) a.push('')
    if (a.length === idx) a.push(b[idx])
    else a[idx] = b[idx]
    emitUpdate(a)
  }
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
    ElMessage.warning(t('plugins.duplicateFieldKey'))
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

.obj,
.arr {
  border-left: 2px solid rgba(0, 0, 0, 0.08);
  padding-left: 14px;
  margin: 6px 0 12px;
}

.row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  flex-wrap: nowrap;
  padding: 10px 0;
}

.row + .row {
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
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
  flex: 0 0 90px;
  min-width: 90px;
}

.add {
  margin-top: 12px;
}

.diff-added {
  background: rgba(46, 160, 67, 0.12);
}

.diff-modified {
  background: rgba(210, 153, 34, 0.14);
}

.diff-deleted {
  background: rgba(248, 81, 73, 0.10);
}

.input-wrap {
  width: 100%;
}

.input-wrap :deep(.el-input),
.input-wrap :deep(.el-input-number) {
  width: 100%;
}

@media (max-width: 640px) {
  .row {
    flex-wrap: wrap;
  }

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

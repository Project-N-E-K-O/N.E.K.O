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
              v-if="Array.isArray(modelValue) && idx < modelValue.length"
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
        <el-input v-model="strVal" :disabled="isReadOnly" @change="emitUpdate(strVal)" />
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

const FORBIDDEN_KEYS = new Set(['__proto__', 'prototype', 'constructor'])
function isValidKeySegment(key: string) {
  if (!key) return false
  if (key.includes('.')) return false
  if (FORBIDDEN_KEYS.has(key)) return false
  if (!props.path && key === 'plugin') return false
  return true
}

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

  // 在根节点编辑 profile 覆盖配置时，隐藏顶层的 plugin 段，避免在 diff 视图中被标记为“已删除”
  // plugin 段仍通过上方 JSON 预览完整展示，并且 profile 不能修改 plugin
  if (!props.path) {
    keys.delete('plugin')
  }

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
    if (kind.value === 'boolean') boolVal.value = typeof v === 'boolean' ? v : false
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
  const inA = Object.prototype.hasOwnProperty.call(a, k)
  const inB = Object.prototype.hasOwnProperty.call(b, k)
  return !inA && inB
}

function deepEqual(a: any, b: any, seen?: WeakMap<object, object>): boolean {
  if (a === b) return true
  if (a == null || b == null) return a === b
  const ta = typeof a
  const tb = typeof b
  if (ta !== tb) return false
  if (ta !== 'object') return false

  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime()
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false
    if (a.length !== b.length) return false
    const s = seen || new WeakMap<object, object>()
    const existing = s.get(a as object)
    if (existing) return existing === (b as object)
    s.set(a as object, b as object)
    for (let i = 0; i < a.length; i++) {
      if (!deepEqual(a[i], b[i], s)) return false
    }
    return true
  }

  const s = seen || new WeakMap<object, object>()
  const existing = s.get(a as object)
  if (existing) return existing === (b as object)
  s.set(a as object, b as object)

  const ak = Object.keys(a)
  const bk = Object.keys(b)
  if (ak.length !== bk.length) return false
  ak.sort()
  bk.sort()
  for (let i = 0; i < ak.length; i++) {
    if (ak[i] !== bk[i]) return false
  }
  for (const k of ak) {
    if (!deepEqual(a[k], b[k], s)) return false
  }
  return true
}

function rowClassForKey(k: string) {
  if (kind.value !== 'object') return ''
  const a = props.modelValue && typeof props.modelValue === 'object' ? props.modelValue : {}
  const b = props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}

  const inA = Object.prototype.hasOwnProperty.call(a, k)
  const inB = Object.prototype.hasOwnProperty.call(b, k)
  if (inA && !inB) return 'diff-added'
  // 对于只存在于基础配置、但未在当前覆盖中显式设置的字段，表示“继承基础配置”，
  // 不应在 UI 上标记为已删除，因此不返回 diff-deleted 样式
  if (!inA && inB) return ''
  if (inA && inB) {
    const av = (a as any)[k]
    const bv = (b as any)[k]
    if (!deepEqual(av, bv)) return 'diff-modified'
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
  if (!isValidKeySegment(k)) return
  const next = { ...(props.modelValue || {}) }
  next[k] = v
  emitUpdate(next)
}

function removeObjectKey(k: string) {
  if (!isValidKeySegment(k)) return
  const next = { ...(props.modelValue || {}) }
  delete next[k]
  emitUpdate(next)
}

function restoreObjectKey(k: string) {
  if (!isValidKeySegment(k)) return
  const next = { ...(props.modelValue || {}) }
  next[k] = baselineChild(k)
  emitUpdate(next)
}

function updateArrayIndex(idx: number, v: any) {
  const a = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  while (a.length < idx) a.push(undefined)
  if (a.length === idx) a.push(v)
  else a[idx] = v
  emitUpdate(a)
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
    if (!deepEqual(a[idx], b[idx])) return 'diff-modified'
  }
  return ''
}

function restoreArrayIndex(idx: number) {
  const a = Array.isArray(props.modelValue) ? [...props.modelValue] : []
  const b = Array.isArray(props.baselineValue) ? props.baselineValue : []
  if (idx >= 0 && idx < b.length) {
    while (a.length < idx) a.push(b[a.length])
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
  if (!key) {
    ElMessage.warning(t('plugins.fieldNameRequired'))
    return
  }

  if (!isValidKeySegment(key)) {
    ElMessage.warning(t('plugins.invalidFieldKey'))
    return
  }

  const next = { ...(props.modelValue || {}) }
  if (Object.prototype.hasOwnProperty.call(next, key)) {
    ElMessage.warning(t('plugins.duplicateFieldKey'))
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

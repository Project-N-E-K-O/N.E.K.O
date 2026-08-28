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
              :model-value="overlayChild(k)"
              @update:model-value="(val) => updateObjectKey(k, val)"
              :baseline-value="baselineChild(k)"
              :path="childPath(k)"
              :replace-semantics="replaceSemantics"
            />
          </div>
          <div class="ops">
            <el-button
              v-if="!isProtectedKey(k) && isOverriddenKey(k)"
              size="small"
              type="primary"
              text
              @click="resetObjectKey(k)"
            >
              {{ t('common.reset') }}
            </el-button>
            <el-button
              v-else-if="!isProtectedKey(k) && isCustomKey(k)"
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
              :replace-semantics="true"
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
  // 数组在后端是整体替换，数组项内部没有「未覆盖就继承基线」这回事：
  // 写回什么，生效的就是什么。这条上下文沿数组项往下传递，决定「重置」
  // 是把键摘掉退回继承，还是必须把基线值显式写回去。
  replaceSemantics?: boolean
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

// `modelValue` 只承载 profile overlay：某个键未被覆盖时它是 undefined。
// 渲染继承值要回落到 baseline，但写回时绝不能把 baseline 拷进 overlay，
// 否则用户只改一个字段就会把整段清单默认值固化进 profile。
function asPlainObject(v: unknown): Record<string, any> | null {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, any>) : null
}

function isEmptyPlainObject(v: unknown): boolean {
  const o = asPlainObject(v)
  return o !== null && Object.keys(o).length === 0
}

const overlayObject = computed<Record<string, any>>(() => asPlainObject(props.modelValue) ?? {})

const displayValue = computed<any>(() =>
  props.modelValue !== undefined ? props.modelValue : props.baselineValue
)

function overlayChild(k: string) {
  const a = asPlainObject(props.modelValue)
  return a ? a[k] : undefined
}

const kind = computed<'object' | 'array' | 'string' | 'number' | 'boolean'>(() => {
  const v = displayValue.value
  if (Array.isArray(v)) return 'array'
  if (v !== null && typeof v === 'object') return 'object'
  if (typeof v === 'boolean') return 'boolean'
  if (typeof v === 'number') return 'number'
  return 'string'
})

// 数组项内的对象同理：overlay 项存在时它就是生效值的全部，基线独有的字段
// 不会被继承，列出来只会让人以为它还在。此时基线只用于「重置」已覆盖的字段。
const isReplacedObject = computed(
  () => props.replaceSemantics === true && asPlainObject(props.modelValue) !== null
)

const objectKeys = computed(() => {
  if (kind.value !== 'object') return []
  const a = overlayObject.value
  const b =
    isReplacedObject.value || !props.baselineValue || typeof props.baselineValue !== 'object'
      ? {}
      : props.baselineValue
  const keys = new Set<string>([...Object.keys(a), ...Object.keys(b)])

  // 在根节点编辑 profile 覆盖配置时，隐藏顶层的 plugin 段，避免在 diff 视图中被标记为“已删除”
  // plugin 段仍通过上方 JSON 预览完整展示，并且 profile 不能修改 plugin
  if (!props.path) {
    keys.delete('plugin')
  }

  return Array.from(keys).sort()
})

// 数组是整体替换：overlay 一旦存在，它就是生效值的全部，基线不再逐位继承。
// 拿基线补尾会造出删不掉的幻影项 —— 用户删掉末项，界面立刻又把它填回来，
// 下一次编辑再随 currentArray() 写回去。overlay 不存在时整份继承基线，
// 此时首次写回要落成完整数组。
const arrayItems = computed(() => {
  if (kind.value !== 'array') return []
  if (Array.isArray(props.modelValue)) return [...props.modelValue]
  return Array.isArray(props.baselineValue) ? [...props.baselineValue] : []
})

const strVal = ref('')
const numVal = ref<number | undefined>(undefined)
const boolVal = ref(false)

watch(
  displayValue,
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

function hasOverlayKey(k: string) {
  return Object.prototype.hasOwnProperty.call(overlayObject.value, k)
}

function hasBaselineKey(k: string) {
  const b = props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}
  return Object.prototype.hasOwnProperty.call(b, k)
}

// 该键被 profile 覆盖了清单/运行时的默认值 —— 可以「重置」回继承
function isOverriddenKey(k: string) {
  if (kind.value !== 'object') return false
  return hasOverlayKey(k) && hasBaselineKey(k)
}

// 该键是 profile 自己新增的，基线里没有 —— 只能「删除」
function isCustomKey(k: string) {
  if (kind.value !== 'object') return false
  return hasOverlayKey(k) && !hasBaselineKey(k)
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
  const a = overlayObject.value
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
  const next = { ...overlayObject.value }
  // 子层把最后一个覆盖项重置掉后会回传空对象。基线里该键是张表时，空表不是
  // 「什么都不覆盖」而是「清空这张表」—— 后端 deep_merge 把空 mapping 当替换
  // 处理（config_merge.py），存下去会把整段基线抹掉，而前端预览的合并不实现
  // 这条语义，界面上还显示着继承内容。所以把键本身摘掉让它退回继承；摘完自己
  // 也空了就继续向上冒泡。基线里没有的键是 profile 自己建的空表，属显式意图，
  // 保留。
  if (!props.replaceSemantics && isEmptyPlainObject(v) && asPlainObject(baselineChild(k)) !== null) {
    delete next[k]
  } else {
    next[k] = v
  }
  emitUpdate(next)
}

// 可继承上下文里「重置」= 把键摘掉退回继承；替换语义下没有回填，
// 摘掉等于把该字段从生效配置里删了（例如 servers[0].host），
// 所以必须把基线值显式写回。
function resetObjectKey(k: string) {
  if (!isValidKeySegment(k)) return
  if (!props.replaceSemantics) {
    removeObjectKey(k)
    return
  }
  const next = { ...overlayObject.value }
  next[k] = baselineChild(k)
  emitUpdate(next)
}

// 「删除」始终是把键移出 overlay。
function removeObjectKey(k: string) {
  if (!isValidKeySegment(k)) return
  const next = { ...overlayObject.value }
  delete next[k]
  emitUpdate(next)
}

// 数组在后端是整体替换，overlay 里存的必须是完整数组。所以写回的基础必须与
// 界面渲染的是同一个视图（overlay 优先、缺位取基线）—— 只拷稀疏 overlay 会让
// 增、删、改把界面上看得见的继承项一起冲掉。
function currentArray(): any[] {
  return [...arrayItems.value]
}

function updateArrayIndex(idx: number, v: any) {
  const a = currentArray()
  if (idx < a.length) a[idx] = v
  else a.push(v)
  emitUpdate(a)
}

function removeArrayIndex(idx: number) {
  const next = currentArray()
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
  // 只在基线里、overlay 还没覆盖到的位置是「继承」，不标已删除（同对象侧）
  if (idx >= a.length) return ''
  if (idx < b.length && !deepEqual(a[idx], b[idx])) return 'diff-modified'
  return ''
}

function addArrayItem() {
  const next = currentArray()
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

  const next = { ...overlayObject.value }
  if (hasOverlayKey(key) || (!isReplacedObject.value && hasBaselineKey(key))) {
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

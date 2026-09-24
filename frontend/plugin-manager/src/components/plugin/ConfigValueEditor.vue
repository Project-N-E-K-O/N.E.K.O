<template>
  <div class="cve" :class="{ compact, 'is-root': !path }" :style="indentStyle">
    <template v-if="kind === 'object'">
      <div class="obj">
        <div
          v-for="k in objectKeys"
          v-show="visibleKey(k)"
          :key="k"
          class="row"
          :class="[
            rowClassForKey(k),
            {
              'section-row': compact && !path && containerKey(k),
              'table-row': compact && !!path && containerKey(k),
              'unsaved-row': compact && changedKey(k),
              'boolean-row': compact && valueType(k) === 'boolean',
              'wide-row': compact && wideKey(k),
            },
          ]"
          :data-config-path="childPath(k)"
        >
          <div class="k">
            <template v-if="compact"
              ><label :for="inputIdFor(k)" :title="childPath(k) + ' · ' + valueType(k)">{{
                k
              }}</label
              ><span
                v-if="changedKey(k)"
                class="unsaved-dot"
                :title="t('plugins.configUi.unsaved')"
            /></template>
            <el-tag v-else size="small" type="info">{{ k }}</el-tag>
          </div>
          <div class="v">
            <div :class="{ 'field-value-line': compact && !containerKey(k) }">
              <div
                :class="{
                  'field-input': compact && !containerKey(k),
                  'number-input': compact && valueType(k) === 'number',
                  'boolean-input': compact && valueType(k) === 'boolean',
                }"
              >
                <ConfigValueEditor
                  :model-value="overlayChild(k)"
                  @update:model-value="(val) => updateObjectKey(k, val)"
                  :baseline-value="baselineChild(k)"
                  :path="childPath(k)"
                  :replace-semantics="replaceSemantics"
                  :compact="compact"
                  :segments="[...(segments || []), k]"
                  :search="search"
                  :filter="filter"
                  :changes="changes"
                  :input-id="inputIdFor(k)"
                  @undo="emit('undo', $event)"
                />
              </div>
              <ConfigFieldActions
                v-if="compact && !containerKey(k) && !isProtectedKey(k)"
                inline
                :path="childPath(k)"
                :type="valueType(k)"
                :can-undo="!replaceSemantics && changedKey(k)"
                :can-restore="isOverriddenKey(k)"
                :custom="isCustomKey(k)"
                :baseline="baselineChild(k)"
                @command="fieldCommand(k, $event)"
              />
            </div>
            <div v-if="compact && hasOverlayKey(k) && !containerKey(k)" class="source-note">
              <span>{{ t('plugins.configUi.configured') }}</span
              ><span v-if="hasBaselineKey(k)" :title="configValueText(baselineChild(k))">{{
                t('plugins.configUi.baseValue', { value: configValueText(baselineChild(k)) })
              }}</span>
            </div>
          </div>
          <div v-if="!compact || containerKey(k)" class="ops">
            <ConfigFieldActions
              v-if="compact && !isProtectedKey(k)"
              :path="childPath(k)"
              :type="valueType(k)"
              :can-undo="!replaceSemantics && changedKey(k)"
              :can-restore="isOverriddenKey(k)"
              :custom="isCustomKey(k)"
              :baseline="baselineChild(k)"
              @command="fieldCommand(k, $event)"
            />
            <el-button
              v-else-if="!isProtectedKey(k) && isOverriddenKey(k)"
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
          <el-button size="small" :text="compact" @click="openAddKey">
            {{ t('plugins.addField') }}
          </el-button>
        </div>
      </div>

      <el-dialog
        v-model="addKeyDialog"
        :title="t('plugins.addField')"
        width="min(420px, 94vw)"
        append-to-body
      >
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
        <div
          v-for="(item, idx) in arrayItems"
          :key="idx"
          class="row"
          :class="rowClassForArrayIndex(idx)"
        >
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
              :compact="compact"
              :segments="[...(segments || []), String(idx)]"
              :search="search"
              :filter="filter"
              :changes="changes"
              :input-id="inputIdFor(String(idx))"
              @undo="emit('undo', $event)"
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
        <el-switch
          :id="inputId"
          :aria-label="path"
          v-model="boolVal"
          :disabled="isReadOnly"
          @change="emitUpdate(boolVal)"
        />
      </div>
    </template>

    <template v-else-if="kind === 'number'">
      <div class="input-wrap">
        <el-input
          v-if="compact"
          :id="inputId"
          :aria-label="path"
          :model-value="numberText"
          type="text"
          inputmode="decimal"
          :disabled="isReadOnly"
          @input="updateNumberText"
          @blur="settleNumberText"
        />
        <el-input-number
          v-else
          :id="inputId"
          :aria-label="path"
          v-model="numVal"
          :step="1"
          :value-on-clear="displayValue"
          :disabled="isReadOnly"
          @update:model-value="emitNumberUpdate"
        />
      </div>
    </template>

    <template v-else>
      <div class="input-wrap">
        <el-input
          :id="inputId"
          :aria-label="path"
          v-model="strVal"
          :type="strVal.includes('\n') ? 'textarea' : 'text'"
          :autosize="{ minRows: 2, maxRows: 8 }"
          :disabled="isReadOnly"
          @input="emitUpdate"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useConfigEditorI18n } from '@/composables/useConfigEditorI18n'
import { ElMessage } from 'element-plus'
import ConfigFieldActions from './ConfigFieldActions.vue'
import {
  configNodeMatches,
  setConfigKey,
  configValueText,
  hasConfigChangesAt,
  type ConfigChange,
  type ConfigFilter,
} from '@/utils/configEditor'
import { parseNumberText, settleNumberText as settleNumberReading } from '@/utils/numberInput'

interface Props {
  modelValue: any
  path?: string
  baselineValue?: any
  // 数组在后端是整体替换，数组项内部没有「未覆盖就继承基线」这回事：
  // 写回什么，生效的就是什么。这条上下文沿数组项往下传递，决定「重置」
  // 是把键摘掉退回继承，还是必须把基线值显式写回去。
  replaceSemantics?: boolean
  compact?: boolean
  segments?: string[]
  search?: string
  filter?: ConfigFilter
  changes?: ConfigChange[]
  inputId?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: any): void
  (e: 'undo', path: string[]): void
}>()
const { t } = useConfigEditorI18n()
function inputIdFor(k: string) {
  return 'config-field-' + encodeURIComponent(JSON.stringify([...(props.segments || []), k]))
}
function containerKey(k: string) {
  const v = overlayChild(k) !== undefined ? overlayChild(k) : baselineChild(k)
  return v !== null && typeof v === 'object'
}
function wideKey(k: string) {
  const value = overlayChild(k) !== undefined ? overlayChild(k) : baselineChild(k)
  return (
    containerKey(k) || (typeof value === 'string' && (value.includes('\n') || value.length > 100))
  )
}
function valueType(k: string) {
  const value = overlayChild(k) !== undefined ? overlayChild(k) : baselineChild(k)
  return Array.isArray(value) ? 'array' : typeof value
}
function changedKey(k: string) {
  return hasConfigChangesAt(props.changes || [], [...(props.segments || []), k])
}
function visibleKey(k: string) {
  return configNodeMatches(
    overlayChild(k),
    baselineChild(k),
    [...(props.segments || []), k],
    props.search || '',
    props.filter || 'all',
    props.changes || [],
    props.replaceSemantics
  )
}
async function fieldCommand(k: string, command: string) {
  if (command === 'undo') emit('undo', [...(props.segments || []), k])
  else if (command === 'reset') resetObjectKey(k)
  else if (command === 'delete') removeObjectKey(k)
  else if (command === 'copy') {
    try {
      await navigator.clipboard.writeText(childPath(k))
      ElMessage.success(t('plugins.configUi.pathCopied'))
    } catch {
      ElMessage.error(t('common.error'))
    }
  }
}

const FORBIDDEN_KEYS = new Set(['__proto__', 'prototype', 'constructor'])
// Guards keys created through the Add-field dialog: dotted and reserved spellings
// cannot be expressed as a path or a plain property.
function isValidNewKey(key: string) {
  if (!key) return false
  if (key.includes('.')) return false
  if (FORBIDDEN_KEYS.has(key)) return false
  if (!props.path && key === 'plugin') return false
  return true
}

// Keys that already exist in the configuration — including quoted TOML spellings
// such as "http.timeout" or "__proto__" — stay editable. They are written as own
// properties, so a reserved name cannot reach the prototype.
function canWriteKey(key: string) {
  return hasOverlayKey(key) || hasBaselineKey(key) || isValidNewKey(key)
}

// `modelValue` 只承载 profile overlay：某个键未被覆盖时它是 undefined。
// 渲染继承值要回落到 baseline，但写回时绝不能把 baseline 拷进 overlay，
// 否则用户只改一个字段就会把整段清单默认值固化进 profile。
function asPlainObject(v: unknown): Record<string, any> | null {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, any>)
    : null
}

function isEmptyPlainObject(v: unknown): boolean {
  const o = asPlainObject(v)
  return o !== null && Object.keys(o).length === 0
}

const overlayObject = computed<Record<string, any>>(() => asPlainObject(props.modelValue) ?? {})

const displayValue = computed<any>(() =>
  props.modelValue !== undefined ? props.modelValue : props.baselineValue
)

// Reads are own-property only: a literal "__proto__" key must not resolve to the
// prototype, and an absent key must not inherit anything.
function overlayChild(k: string) {
  const a = asPlainObject(props.modelValue)
  return a && Object.prototype.hasOwnProperty.call(a, k) ? a[k] : undefined
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
  // In the compact page, keep the base configuration's order stable while
  // editing; append profile-only fields instead of promoting every edited key.
  const keys = new Set<string>(
    props.compact ? [...Object.keys(b), ...Object.keys(a)] : [...Object.keys(a), ...Object.keys(b)]
  )

  // 在根节点编辑 profile 覆盖配置时，隐藏顶层的 plugin 段，避免在 diff 视图中被标记为“已删除”
  // plugin 段仍通过上方 JSON 预览完整展示，并且 profile 不能修改 plugin
  if (!props.path) {
    keys.delete('plugin')
    if (props.compact && keys.delete('plugin_runtime')) keys.add('plugin_runtime')
  }

  return props.compact ? Array.from(keys) : Array.from(keys).sort()
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
const numberText = ref('')
const boolVal = ref(false)

watch(
  displayValue,
  (v) => {
    if (kind.value === 'string') strVal.value = v == null ? '' : String(v)
    if (kind.value === 'number') {
      numVal.value = typeof v === 'number' ? v : undefined
      numberText.value = typeof v === 'number' ? String(v) : ''
    }
    if (kind.value === 'boolean') boolVal.value = typeof v === 'boolean' ? v : false
  },
  { immediate: true }
)

function updateNumberText(value: string) {
  // The raw edit is authoritative:
  // it may not parse yet ("-", "1.", "1e"), but keeping it lets the user finish
  // typing. Only finite values reach the model.
  numberText.value = value
  const parsed = parseNumberText(value)
  if (parsed !== undefined) emitNumberUpdate(parsed)
}
function settleNumberText(event: FocusEvent) {
  const raw = (event.target as HTMLInputElement | null)?.value ?? ''
  // An incomplete number cannot be represented in TOML. Restore the last finite
  // value instead of turning an empty edit into null or zero.
  numberText.value = settleNumberReading(raw, String(displayValue.value))
  const parsed = parseNumberText(numberText.value)
  if (parsed !== undefined) emitNumberUpdate(parsed)
}

function emitNumberUpdate(value: number | null | undefined) {
  // TOML has no null number. An unfinished numeric input stays local; clearing
  // and blurring restores the current value via value-on-clear.
  if (typeof value === 'number' && Number.isFinite(value) && !Object.is(value, displayValue.value))
    emitUpdate(value)
}

function emitUpdate(v: any) {
  emit('update:modelValue', v)
}

function baselineChild(k: string) {
  const b = props.baselineValue
  if (b && typeof b === 'object' && !Array.isArray(b) && Object.prototype.hasOwnProperty.call(b, k))
    return (b as any)[k]
  return undefined
}

function hasOverlayKey(k: string) {
  return Object.prototype.hasOwnProperty.call(overlayObject.value, k)
}

function hasBaselineKey(k: string) {
  const b =
    props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}
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
  const b =
    props.baselineValue && typeof props.baselineValue === 'object' ? props.baselineValue : {}

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
  if (props.compact) return {}
  const p = props.path || ''
  if (!p) return {}
  const depth = p.split('.').length - 1
  return { paddingLeft: `${Math.min(depth, 6) * 12}px` }
})

function updateObjectKey(k: string, v: any) {
  if (!canWriteKey(k)) return
  const next = { ...overlayObject.value }
  // 子层把最后一个覆盖项重置掉后会回传空对象。基线里该键是张表时，空表不是
  // 「什么都不覆盖」而是「清空这张表」—— 后端 deep_merge 把空 mapping 当替换
  // 处理（config_merge.py），存下去会把整段基线抹掉，而前端预览的合并不实现
  // 这条语义，界面上还显示着继承内容。所以把键本身摘掉让它退回继承；摘完自己
  // 也空了就继续向上冒泡。基线里没有的键是 profile 自己建的空表，属显式意图，
  // 保留。
  if (
    !props.replaceSemantics &&
    isEmptyPlainObject(v) &&
    asPlainObject(baselineChild(k)) !== null
  ) {
    delete next[k]
  } else {
    setConfigKey(next, k, v)
  }
  emitUpdate(next)
}

// 可继承上下文里「重置」= 把键摘掉退回继承；替换语义下没有回填，
// 摘掉等于把该字段从生效配置里删了（例如 servers[0].host），
// 所以必须把基线值显式写回。
function resetObjectKey(k: string) {
  if (!canWriteKey(k)) return
  if (!props.replaceSemantics) {
    removeObjectKey(k)
    return
  }
  const next = { ...overlayObject.value }
  setConfigKey(next, k, baselineChild(k))
  emitUpdate(next)
}

// 「删除」始终是把键移出 overlay。
function removeObjectKey(k: string) {
  if (!canWriteKey(k)) return
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

  if (!isValidNewKey(key)) {
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
  background: rgba(248, 81, 73, 0.1);
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

/* Scalar fields use a reading-order grid; real TOML tables keep their hierarchy. */
.cve.compact > .obj,
.cve.compact > .arr {
  border: 0;
  margin: 0;
  padding: 0;
}
.cve.compact > .obj {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 460px), 1fr));
  gap: 12px 28px;
  align-items: start;
}
.compact > .obj > .row {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 9px;
  padding: 0;
  min-width: 0;
  border: 0;
}
.compact > .obj > .row > .k,
.compact > .arr > .row > .k {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  max-width: none;
  padding: 0;
  font-size: 13px;
}
.compact .k label {
  overflow-wrap: anywhere;
  cursor: pointer;
  line-height: 1.5;
  font-weight: 500;
}
.compact > .obj > .row > .v,
.compact > .arr > .row > .v {
  min-width: 0;
}
.compact > .obj > .row > .ops,
.compact > .arr > .row > .ops {
  min-width: 0;
  padding: 0;
}
.field-value-line {
  position: relative;
  padding-right: 36px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.field-input {
  width: 100%;
  min-width: 0;
}
.field-value-line > :deep(.field-actions) {
  font-size: 12px;
}
.field-value-line > :deep(.field-actions:not(.has-direct-actions)) {
  position: absolute;
  right: 0;
  top: 0;
}
.field-value-line > :deep(.has-direct-actions) {
  padding-top: 2px;
}
.field-value-line > :deep(.has-direct-actions .more-actions) {
  position: absolute;
  right: 0;
  top: 0;
}
.compact .input-wrap :deep(.el-input),
.compact .input-wrap :deep(.el-textarea),
.compact .input-wrap :deep(.el-input-number) {
  width: 100%;
}
.compact .input-wrap :deep(.el-input__wrapper) {
  min-height: 32px;
  border-radius: 6px;
  background: var(--el-bg-color);
}
.compact .input-wrap :deep(.el-input__inner),
.compact .input-wrap :deep(.el-textarea__inner) {
  font-size: 14px;
  text-align: left;
}
.compact .input-wrap :deep(.el-input-number__increase) {
  border-radius: 0 7px 0 0;
}
.compact .input-wrap :deep(.el-input-number__decrease) {
  border-radius: 0 0 7px 0;
}
.compact.is-root > .obj {
  gap: 20px;
}
.compact.is-root > .obj > .section-row {
  grid-column: 1 / -1;
  grid-template-columns: minmax(0, 1fr) auto;
  padding: 16px;
  gap: 16px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  background: var(--el-bg-color);
}
.compact.is-root > .obj > .section-row > .k {
  font-size: 16px;
}
.compact.is-root > .obj > .section-row > .k label {
  font-weight: 600;
}
.compact.is-root > .obj > .section-row > .ops {
  grid-column: 2;
  grid-row: 1;
  align-items: center;
}
.compact.is-root > .obj > .section-row > .v {
  grid-column: 1 / -1;
  grid-row: 2;
}
.compact > .obj > .wide-row {
  grid-column: 1 / -1;
}
.compact > .obj > .table-row {
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
}
.compact > .obj > .table-row > .v {
  grid-column: 1 / -1;
  grid-row: 2;
  padding-left: 18px;
  border-left: 2px solid var(--el-border-color-lighter);
}
.compact > .obj > .table-row > .ops {
  grid-column: 2;
  grid-row: 1;
}
.compact > .obj > .boolean-row {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  background: var(--el-fill-color-extra-light);
  border-radius: 8px;
  min-height: 56px;
  align-self: end;
}
.compact > .obj > .boolean-row > .k {
  padding-right: 0 !important;
}
.compact > .obj > .boolean-row > .v {
  display: contents;
}
.boolean-row .field-value-line {
  padding-right: 0;
  justify-content: flex-end;
}
.boolean-row .field-input {
  width: auto;
}
.boolean-row .field-value-line > :deep(.field-actions) {
  position: static;
}
.boolean-row .field-value-line > :deep(.field-actions .more-actions) {
  position: static;
}
.boolean-row .source-note {
  grid-column: 1 / -1;
}
.compact .source-note {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin-top: 7px;
  color: var(--el-text-color-secondary);
  font-size: 11px;
}
.compact .source-note > span + span {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.compact .unsaved-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary);
  flex-shrink: 0;
}
.compact > .obj > .unsaved-row:not(.section-row):not(.table-row) > .k {
  color: var(--el-color-primary);
}
.compact .diff-added,
.compact .diff-modified,
.compact .diff-deleted {
  background: transparent;
}
.compact > .obj > .add,
.compact > .arr > .add {
  grid-column: 1 / -1;
  margin: -6px 0 0;
  padding: 0;
}
.compact .add > .el-button {
  color: var(--el-text-color-secondary);
  margin-left: -10px;
}
.compact > .arr > .row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
  padding: 10px 0;
  border-top: 1px solid var(--el-border-color-extra-light);
}
.compact > .obj > .row:hover > .v > .field-value-line :deep(.more-actions),
.compact > .obj > .row:focus-within > .v > .field-value-line :deep(.more-actions),
.compact > .obj > .section-row:hover > .ops :deep(.more-actions),
.compact > .obj > .section-row:focus-within > .ops :deep(.more-actions) {
  opacity: 1;
}
/* Each object chooses its column count from its own available width, including
   nested objects and array items. Long text and containers span the grid. */
.compact > .obj > .row:not(.section-row):not(.table-row):not(.wide-row):not(.boolean-row) {
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  align-items: start;
  gap: 12px;
}
.compact > .obj > .row:not(.section-row):not(.table-row):not(.wide-row):not(.boolean-row) > .k {
  padding-top: 7px;
}
.compact > .obj > .boolean-row {
  padding: 0;
  min-height: 32px;
  align-self: start;
  background: transparent;
}
.compact > .obj > .boolean-row > .v {
  display: block;
}
.compact > .obj > .table-row > .v {
  min-width: 0;
  padding-left: 12px;
}
.compact .field-value-line {
  min-width: 0;
}
@container config-fields (max-width: 520px) {
  .compact > .arr > .row {
    grid-template-columns: 26px minmax(0, 1fr);
    gap: 8px;
  }
  .compact > .arr > .row > .ops {
    grid-column: 2;
    display: flex;
    flex-wrap: wrap;
  }
  .compact > .arr > .row > .ops .el-button {
    max-width: 100%;
    margin-left: 0;
    height: auto;
    min-height: 28px;
    white-space: normal;
  }

  .compact > .obj > .row:not(.section-row):not(.table-row):not(.wide-row):not(.boolean-row) {
    grid-template-columns: minmax(0, 1fr);
    gap: 6px;
  }
  .compact.is-root > .obj > .section-row {
    padding: 12px;
  }
}
</style>

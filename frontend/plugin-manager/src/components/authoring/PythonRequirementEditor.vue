<template>
  <el-dialog
    v-model="visible"
    :title="t(declaration === undefined ? 'pythonPackages.add' : 'pythonPackages.edit')"
    width="min(560px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!saving"
    :show-close="!saving"
  >
    <form class="authoring-form" @submit.prevent="submit(true)">
      <el-alert
        v-if="editorError || error"
        :title="editorError ? t(`pythonPackages.${editorError}`) : error"
        type="error"
        :closable="false"
        show-icon
      />
      <template v-if="!fullDeclaration">
        <label for="python-package-name"
          >{{ t('pythonPackages.packageName')
          }}<el-input
            id="python-package-name"
            v-model="packageName"
            :placeholder="t('pythonPackages.nameExample')"
            :disabled="saving"
            @paste="pastePackageInput"
        /></label>
        <label for="python-package-version-policy"
          >{{ t('pythonPackages.versionChoice') }}
          <el-select
            id="python-package-version-policy"
            v-model="versionPolicy"
            :disabled="saving"
            @change="changeVersionPolicy"
          >
            <el-option value="auto" :label="t('pythonPackages.versionAuto')" />
            <el-option value="exact" :label="t('pythonPackages.versionExact')" />
            <el-option value="custom" :label="t('pythonPackages.versionCustom')" />
          </el-select>
        </label>
        <label v-if="versionPolicy === 'exact'" for="python-package-exact-version"
          >{{ t('pythonPackages.versionNumber') }}
          <el-input
            id="python-package-exact-version"
            v-model="exactVersion"
            :placeholder="t('pythonPackages.versionExample')"
            :disabled="saving"
          />
        </label>
      </template>
      <details
        :open="advancedOpen"
        @toggle="advancedOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary>{{ t('pythonPackages.advanced') }}</summary>
        <div class="python-package-advanced">
          <el-checkbox
            v-model="fullDeclaration"
            :disabled="saving"
            @change="changeDeclarationMode"
            >{{ t('pythonPackages.manualDeclaration') }}</el-checkbox
          >
          <template v-if="!fullDeclaration">
            <label v-if="versionPolicy === 'custom'" for="python-package-version"
              >{{ t('pythonPackages.versionConstraint') }}
              <el-input
                id="python-package-version"
                v-model="packageVersion"
                placeholder=">=0.27,<1"
                :disabled="saving"
              />
            </label>
            <label for="python-package-extras"
              >{{ t('pythonPackages.optionalFeatures')
              }}<el-input
                id="python-package-extras"
                v-model="packageExtras"
                placeholder="socks"
                :disabled="saving"
            /></label>
            <label for="python-package-marker"
              >{{ t('pythonPackages.marker')
              }}<el-input id="python-package-marker" v-model="packageMarker" :disabled="saving"
            /></label>
          </template>
          <label v-else for="python-package-requirement"
            >{{ t('pythonPackages.declaration')
            }}<el-input
              id="python-package-requirement"
              v-model="rawDeclaration"
              maxlength="4096"
              :disabled="saving"
          /></label>
        </div>
      </details>
      <p v-if="running" class="authoring-small authoring-muted">
        {{ t('pythonPackages.stopBeforeInstall') }}
      </p>
      <div class="python-packages-actions">
        <el-button :disabled="saving" @click="visible = false">{{ t('common.cancel') }}</el-button
        ><el-button :disabled="!canSubmit || busy" @click="submit(false)">{{
          t('pythonPackages.saveOnly')
        }}</el-button
        ><el-button
          native-type="submit"
          type="primary"
          :loading="saving"
          :disabled="!canSubmit || !canInstall"
          >{{
            t(
              declaration === undefined ? 'pythonPackages.addInstall' : 'pythonPackages.saveInstall'
            )
          }}</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { parsePythonPackageInput, pythonVersionPolicy } from '@/utils/pythonPackageInput'

const props = defineProps<{
  declaration?: string
  saving: boolean
  busy: boolean
  canInstall: boolean
  running?: boolean
  error?: string
}>()
const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ submit: [requirement: string, install: boolean] }>()
const { t } = useI18n()
const rawDeclaration = ref('')
const fullDeclaration = ref(false)
const packageName = ref('')
const packageVersion = ref('')
const packageExtras = ref('')
const packageMarker = ref('')
const versionPolicy = ref<'auto' | 'exact' | 'custom'>('auto')
const advancedOpen = ref(false)
const editorError = ref('')
const exactVersion = computed({
  get: () => packageVersion.value.replace(/^==/, ''),
  set: (value: string) => {
    packageVersion.value = value.trim() ? `==${value.trim()}` : ''
  },
})
const simpleDeclaration = computed(
  () =>
    packageName.value.trim() +
    (packageExtras.value.trim()
      ? '[' +
        packageExtras.value
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
          .join(',') +
        ']'
      : '') +
    packageVersion.value.trim() +
    (packageMarker.value.trim() ? '; ' + packageMarker.value.trim() : '')
)
const editorValue = computed(() =>
  fullDeclaration.value ? rawDeclaration.value : simpleDeclaration.value
)
const canSubmit = computed(
  () =>
    !!editorValue.value.trim() &&
    (fullDeclaration.value ||
      (!!packageName.value.trim() &&
        (versionPolicy.value !== 'exact' || !!exactVersion.value.trim())))
)

watch(
  visible,
  (open) => {
    if (!open) return
    const original = props.declaration ?? ''
    const parsed = parsePythonPackageInput(original)
    fullDeclaration.value = !!original && !parsed
    rawDeclaration.value = original
    packageName.value = parsed?.name ?? ''
    packageVersion.value = parsed?.specifier ?? ''
    packageExtras.value = parsed?.extras ?? ''
    packageMarker.value = parsed?.marker ?? ''
    versionPolicy.value = pythonVersionPolicy(packageVersion.value)
    advancedOpen.value =
      fullDeclaration.value ||
      versionPolicy.value === 'custom' ||
      !!packageExtras.value ||
      !!packageMarker.value
    editorError.value = ''
  },
  { immediate: true }
)
function changeVersionPolicy() {
  packageVersion.value = ''
  if (versionPolicy.value === 'custom') advancedOpen.value = true
}
function normalizeNameInput() {
  const parsed = parsePythonPackageInput(packageName.value)
  if (!parsed) return
  const hasOptions =
    parsed.name !== packageName.value.trim() ||
    !!parsed.specifier ||
    !!parsed.extras ||
    !!parsed.marker
  packageName.value = parsed.name
  if (hasOptions) {
    packageVersion.value = parsed.specifier
    packageExtras.value = parsed.extras
    packageMarker.value = parsed.marker
    versionPolicy.value = pythonVersionPolicy(parsed.specifier)
    if (versionPolicy.value === 'custom' || parsed.extras || parsed.marker)
      advancedOpen.value = true
  }
}
function pastePackageInput(event: ClipboardEvent) {
  const value = event.clipboardData?.getData('text') ?? ''
  const parsed = parsePythonPackageInput(value)
  if (!parsed || parsed.name === value.trim()) return
  event.preventDefault()
  packageName.value = value
  normalizeNameInput()
}
function changeDeclarationMode(value: boolean | string | number) {
  if (value) {
    rawDeclaration.value = simpleDeclaration.value
    return
  }
  if (!rawDeclaration.value.trim()) {
    packageName.value = ''
    packageVersion.value = ''
    packageExtras.value = ''
    packageMarker.value = ''
    versionPolicy.value = 'auto'
    return
  }
  const parsed = parsePythonPackageInput(rawDeclaration.value)
  if (!parsed) {
    fullDeclaration.value = true
    return
  }
  packageName.value = parsed.name
  packageExtras.value = parsed.extras
  packageVersion.value = parsed.specifier
  packageMarker.value = parsed.marker
  versionPolicy.value = pythonVersionPolicy(packageVersion.value)
}
function submit(install: boolean) {
  if (props.busy || (install && !props.canInstall)) return
  editorError.value = ''
  if (!fullDeclaration.value) {
    normalizeNameInput()
    if (!/^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/.test(packageName.value)) {
      editorError.value = 'invalidName'
      return
    }
    if (versionPolicy.value === 'exact' && !/^[vV]?\d[\w.!+-]*$/.test(exactVersion.value)) {
      editorError.value = 'invalidVersion'
      return
    }
  }
  const value = editorValue.value.trim()
  if (!canSubmit.value) return
  emit('submit', value, install)
}
</script>

<style scoped>
.python-package-advanced {
  display: grid;
  gap: 14px;
  padding-top: 14px;
}
.python-packages-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.python-packages-actions .el-button + .el-button {
  margin-left: 0;
}
summary {
  cursor: pointer;
  overflow-wrap: anywhere;
}
</style>

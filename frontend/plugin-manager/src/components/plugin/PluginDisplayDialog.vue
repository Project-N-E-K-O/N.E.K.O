<template>
  <el-dialog
    :model-value="visible"
    :title="
      t(`plugins.display.${confirmation?.mode === 'fullscreen' ? 'fullscreenTitle' : 'focusTitle'}`)
    "
    :append-to="appendTarget"
    append-to-body
    :close-on-click-modal="false"
    :close-on-press-escape="!applying"
    :show-close="!applying"
    width="min(440px, calc(100vw - 32px))"
    @update:model-value="onVisibilityChange"
  >
    <p>
      {{
        t(
          `plugins.display.${confirmation?.mode === 'fullscreen' ? 'fullscreenBody' : 'focusBody'}`,
          { plugin: confirmation?.pluginName }
        )
      }}
    </p>
    <template #footer>
      <el-button :disabled="applying" data-testid="display-cancel" @click="decline">{{
        t('common.cancel')
      }}</el-button>
      <el-button type="primary" :loading="applying" data-testid="display-confirm" @click="confirm">
        {{
          t(
            `plugins.display.${confirmation?.mode === 'fullscreen' ? 'enterFullscreen' : 'enterFocus'}`
          )
        }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { usePluginDisplayDialog } from '@/composables/usePluginDisplayDialog'
const { t } = useI18n()
const { visible, confirmation, applying, appendTarget, confirm, decline } = usePluginDisplayDialog()

function onVisibilityChange(value: boolean) {
  if (!value) decline()
}
</script>

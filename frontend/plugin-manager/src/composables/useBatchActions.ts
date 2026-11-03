import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'

export interface BatchOptions<T> {
  filter: (item: T) => boolean
  action: (item: T) => Promise<void>
  noItemsKey: string
  confirmKey: string
  confirmType?: 'info' | 'warning'
  successKey: string
  partialKey: string
}

/**
 * 对选中项逐个执行同一操作：筛选可操作项 → 确认 → 串行执行并计数 → 汇总提示 → 刷新。
 */
export function useBatchActions<T>(
  getSelected: () => T[],
  refreshAfterChange: () => Promise<void>,
) {
  const { t } = useI18n()
  const busy = ref(false)

  async function runBatch(options: BatchOptions<T>): Promise<void> {
    const items = getSelected().filter(options.filter)
    if (items.length === 0) {
      ElMessage.info(t(options.noItemsKey))
      return
    }
    try {
      await ElMessageBox.confirm(
        t(options.confirmKey, { count: items.length }),
        t('common.confirm'),
        { type: options.confirmType ?? 'info' },
      )
    } catch {
      return
    }

    busy.value = true
    let ok = 0
    let fail = 0
    for (const item of items) {
      try {
        await options.action(item)
        ok++
      } catch {
        fail++
      }
    }
    busy.value = false

    if (fail === 0) ElMessage.success(t(options.successKey, { count: ok }))
    else ElMessage.warning(t(options.partialKey, { success: ok, fail }))
    await refreshAfterChange()
  }

  return { busy, runBatch }
}

import type { ModelBindingErrorMessages } from './en-US'

const messages: ModelBindingErrorMessages = {
  conflict: 'Привязка изменена другой операцией. Обновите и проверьте её перед повторной попыткой.',
  unknown: 'Результат пока не подтверждён. Обновите страницу для подтверждения перед изменением.',
  failed: 'Не удалось завершить изменение. Проверьте текущую привязку перед повторной попыткой.',
  version: 'Обновите страницу перед изменением привязки.',
}

export default messages

import bindingErrors from '../model-binding-errors/ru'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: 'Привязки API моделей',
  manage: 'Слоты и использование',
  description: 'Выберите сохранённый слот модели для каждой задачи, объявленной плагином. Изменения сохраняются сразу.',
  ready: 'Все обязательные задачи моделей привязаны',
  notReady: 'Некоторые обязательные задачи моделей требуют привязки',
  readinessHint: 'Статус показывает только полноту обязательных привязок. Он не проверяет доступность провайдера и не управляет запуском плагина.',
  boundSummary: 'Привязано {bound} из {total}',
  collapse: 'Свернуть',
  expand: 'Развернуть',
  required: 'Обязательно',
  optional: 'Необязательно',
  unbound: 'Без привязки',
  incompatible: 'Недостаточно возможностей',
  noSlots: 'Слотов моделей пока нет. Добавьте слот в Plugin API, затем обновите этот раздел.',
  loadFailed: 'Не удалось загрузить привязки моделей. Повторите попытку.',
  saveFailed: 'Не удалось сохранить привязку. Отображается предыдущая привязка.',
  capabilities: { text: 'Текст', image_input: 'Ввод изображений', tool_calling: 'Вызов инструментов', streaming: 'Потоковый вывод' },
  status: { bound: 'Привязано', unbound: 'Слот не выбран', incompatible: 'Привязанный слот больше не соответствует требованиям этой задачи' },
}

export default messages

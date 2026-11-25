import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'Вызов моделей ИИ (может быть платным)',
    description: 'Может вызывать модели ИИ через шлюз моделей N.E.K.O, что может повлечь расходы.',
  },
  system_network_connect: {
    title: 'Подключение к интернету',
    description: 'Может подключаться к сетевым сервисам.',
  },
  system_network_listen: {
    title: 'Открытие сетевого порта',
    description: 'Может открыть сетевой порт на этом компьютере, чтобы к нему могли подключаться другие программы.',
  },
  system_fs_read: {
    title: 'Чтение файлов',
    description: 'Может читать файлы за пределами своей папки данных.',
  },
  system_fs_write: {
    title: 'Запись файлов',
    description: 'Может создавать или изменять файлы за пределами своей папки данных.',
  },
  system_process_spawn: {
    title: 'Запуск программ',
    description: 'Может запускать другие программы или команды на этом компьютере.',
  },
  system_env_read: {
    title: 'Чтение переменных окружения',
    description: 'Может читать переменные окружения этого компьютера, в которых могут быть ключи API.',
  },
}

export default labels

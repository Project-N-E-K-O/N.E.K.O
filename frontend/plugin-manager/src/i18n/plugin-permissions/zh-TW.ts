import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: '呼叫 AI 模型（可能產生費用）',
    description: '可能透過 N.E.K.O 的模型閘道呼叫 AI 模型，可能產生用量費用。',
  },
  system_network_connect: {
    title: '連網',
    description: '可能連線到網路服務。',
  },
  system_network_listen: {
    title: '開放本機網路連接埠',
    description: '可能在這台電腦上開放網路連接埠，讓其他程式連進來。',
  },
  system_fs_read: {
    title: '讀取檔案',
    description: '可能讀取自身資料目錄以外的檔案。',
  },
  system_fs_write: {
    title: '寫入檔案',
    description: '可能建立或修改自身資料目錄以外的檔案。',
  },
  system_process_spawn: {
    title: '啟動程式',
    description: '可能在這台電腦上啟動其他程式或指令。',
  },
  system_env_read: {
    title: '讀取環境變數',
    description: '可能讀取這台電腦的環境變數，其中可能包含 API 金鑰。',
  },
}

export default labels

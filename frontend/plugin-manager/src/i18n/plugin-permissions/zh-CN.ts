import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: '调用 AI 模型（可能产生费用）',
    description: '可能通过 N.E.K.O 的模型网关调用 AI 模型，可能产生用量费用。',
  },
  system_network_connect: {
    title: '联网',
    description: '可能连接网络服务。',
  },
  system_network_listen: {
    title: '开放本机网络端口',
    description: '可能在这台电脑上开放网络端口，让别的程序连进来。',
  },
  system_fs_read: {
    title: '读取文件',
    description: '可能读取自己数据目录以外的文件。',
  },
  system_fs_write: {
    title: '写入文件',
    description: '可能创建或修改自己数据目录以外的文件。',
  },
  system_process_spawn: {
    title: '启动程序',
    description: '可能在这台电脑上启动其他程序或命令。',
  },
  system_env_read: {
    title: '读取环境变量',
    description: '可能读取这台电脑的环境变量，其中可能包含 API 密钥。',
  },
}

export default labels

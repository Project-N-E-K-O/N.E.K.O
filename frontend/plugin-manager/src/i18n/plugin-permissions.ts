/**
 * Labels for plugin permission ids (plugin/sdk/shared/permissions/catalog.py).
 *
 * Keys are derived by `capabilityMessageKey`: every `.` and `:` becomes `_`.
 * Ids that had an earlier capability name reuse that name's label.
 */

type Label = { title: string; description: string }

export const PERMISSION_LEGACY_LABEL_KEYS = {
  plugin_sdk_main_agent_memory_read: 'memory_read',
  plugin_sdk_main_agent_conversations_read: 'conversations_read',
  plugin_sdk_main_agent_messages_push: 'messages_push',
} as const

const en = {
  plugin_sdk_models_invoke: {
    title: 'Use AI models (may incur costs)',
    description: "May call AI models through N.E.K.O's model gateway, which can incur usage costs.",
  },
  system_network_connect: {
    title: 'Connect to the internet',
    description: 'May connect to network services.',
  },
  system_network_listen: {
    title: 'Accept network connections',
    description: 'May open a port on this computer and accept connections from other devices.',
  },
  system_fs_read: {
    title: 'Read files',
    description: 'May read files outside its own data folder.',
  },
  system_fs_write: {
    title: 'Write files',
    description: 'May create or change files outside its own data folder.',
  },
  system_process_spawn: {
    title: 'Start programs',
    description: 'May start other programs or commands on this computer.',
  },
  system_env_read: {
    title: 'Read environment variables',
    description: "May read this computer's environment variables, which can contain API keys.",
  },
} satisfies Record<string, Label>

type PermissionLabels = Record<keyof typeof en, Label>

const zhCN: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: '调用 AI 模型（可能产生费用）',
    description: '可能通过 N.E.K.O 的模型网关调用 AI 模型，可能产生用量费用。',
  },
  system_network_connect: {
    title: '联网',
    description: '可能连接网络服务。',
  },
  system_network_listen: {
    title: '接受网络连接',
    description: '可能在这台电脑上打开端口，接受其他设备的连接。',
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

const zhTW: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: '呼叫 AI 模型（可能產生費用）',
    description: '可能透過 N.E.K.O 的模型閘道呼叫 AI 模型，可能產生用量費用。',
  },
  system_network_connect: {
    title: '連網',
    description: '可能連線到網路服務。',
  },
  system_network_listen: {
    title: '接受網路連線',
    description: '可能在這台電腦上開啟連接埠，接受其他裝置的連線。',
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

const ja: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'AI モデルを呼び出す（費用が発生する場合があります）',
    description: 'N.E.K.O のモデルゲートウェイ経由で AI モデルを呼び出すことがあり、利用料金が発生する場合があります。',
  },
  system_network_connect: {
    title: 'インターネットに接続',
    description: 'ネットワークサービスに接続することがあります。',
  },
  system_network_listen: {
    title: 'ネットワーク接続を受け付ける',
    description: 'このコンピューターでポートを開き、他のデバイスからの接続を受け付ける可能性があります。',
  },
  system_fs_read: {
    title: 'ファイルを読み取る',
    description: '自身のデータフォルダー以外のファイルを読み取ることがあります。',
  },
  system_fs_write: {
    title: 'ファイルを書き込む',
    description: '自身のデータフォルダー以外のファイルを作成・変更することがあります。',
  },
  system_process_spawn: {
    title: 'プログラムを起動',
    description: 'このコンピューターで他のプログラムやコマンドを起動することがあります。',
  },
  system_env_read: {
    title: '環境変数を読み取る',
    description: 'このコンピューターの環境変数を読み取ることがあります。環境変数には API キーが含まれる場合があります。',
  },
}

const ko: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'AI 모델 호출(비용이 발생할 수 있음)',
    description: 'N.E.K.O의 모델 게이트웨이를 통해 AI 모델을 호출할 수 있으며, 사용 요금이 발생할 수 있습니다.',
  },
  system_network_connect: {
    title: '인터넷 연결',
    description: '네트워크 서비스에 연결할 수 있습니다.',
  },
  system_network_listen: {
    title: '네트워크 연결 수락',
    description: '이 컴퓨터에서 포트를 열고 다른 기기의 연결을 받을 수 있습니다.',
  },
  system_fs_read: {
    title: '파일 읽기',
    description: '자체 데이터 폴더 밖의 파일을 읽을 수 있습니다.',
  },
  system_fs_write: {
    title: '파일 쓰기',
    description: '자체 데이터 폴더 밖의 파일을 만들거나 수정할 수 있습니다.',
  },
  system_process_spawn: {
    title: '프로그램 실행',
    description: '이 컴퓨터에서 다른 프로그램이나 명령을 실행할 수 있습니다.',
  },
  system_env_read: {
    title: '환경 변수 읽기',
    description: '이 컴퓨터의 환경 변수를 읽을 수 있으며, 여기에는 API 키가 포함될 수 있습니다.',
  },
}

const ru: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'Вызов моделей ИИ (может быть платным)',
    description: 'Может вызывать модели ИИ через шлюз моделей N.E.K.O, что может повлечь расходы.',
  },
  system_network_connect: {
    title: 'Подключение к интернету',
    description: 'Может подключаться к сетевым сервисам.',
  },
  system_network_listen: {
    title: 'Принимать сетевые подключения',
    description: 'Может открыть порт на этом компьютере и принимать подключения с других устройств.',
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

const es: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'Usar modelos de IA (puede tener costo)',
    description: 'Puede llamar a modelos de IA a través de la pasarela de modelos de N.E.K.O, lo que puede generar costos de uso.',
  },
  system_network_connect: {
    title: 'Conectarse a internet',
    description: 'Puede conectarse a servicios de red.',
  },
  system_network_listen: {
    title: 'Aceptar conexiones de red',
    description: 'Puede abrir un puerto en este equipo y aceptar conexiones de otros dispositivos.',
  },
  system_fs_read: {
    title: 'Leer archivos',
    description: 'Puede leer archivos fuera de su propia carpeta de datos.',
  },
  system_fs_write: {
    title: 'Escribir archivos',
    description: 'Puede crear o modificar archivos fuera de su propia carpeta de datos.',
  },
  system_process_spawn: {
    title: 'Iniciar programas',
    description: 'Puede iniciar otros programas o comandos en este equipo.',
  },
  system_env_read: {
    title: 'Leer variables de entorno',
    description: 'Puede leer las variables de entorno de este equipo, que pueden contener claves de API.',
  },
}

const pt: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'Usar modelos de IA (pode ter custo)',
    description: 'Pode chamar modelos de IA pelo gateway de modelos do N.E.K.O, o que pode gerar custos de uso.',
  },
  system_network_connect: {
    title: 'Conectar à internet',
    description: 'Pode se conectar a serviços de rede.',
  },
  system_network_listen: {
    title: 'Aceitar conexões de rede',
    description: 'Pode abrir uma porta neste computador e aceitar conexões de outros dispositivos.',
  },
  system_fs_read: {
    title: 'Ler arquivos',
    description: 'Pode ler arquivos fora da sua própria pasta de dados.',
  },
  system_fs_write: {
    title: 'Gravar arquivos',
    description: 'Pode criar ou alterar arquivos fora da sua própria pasta de dados.',
  },
  system_process_spawn: {
    title: 'Iniciar programas',
    description: 'Pode iniciar outros programas ou comandos neste computador.',
  },
  system_env_read: {
    title: 'Ler variáveis de ambiente',
    description: 'Pode ler as variáveis de ambiente deste computador, que podem conter chaves de API.',
  },
}

/** Add the permission-id labels next to a locale's earlier capability labels. */
export function withPermissionLabels<C extends Record<string, Label>>(capabilities: C, labels: PermissionLabels) {
  const aliased = Object.fromEntries(
    Object.entries(PERMISSION_LEGACY_LABEL_KEYS).map(([key, legacy]) => [key, capabilities[legacy]]),
  ) as Record<keyof typeof PERMISSION_LEGACY_LABEL_KEYS, Label>
  return { ...capabilities, ...aliased, ...labels }
}

export const permissionLabels = { en, zhCN, zhTW, ja, ko, ru, es, pt }

import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'Usar modelos de IA (pode ter custo)',
    description: 'Pode chamar modelos de IA pelo gateway de modelos do N.E.K.O, o que pode gerar custos de uso.',
  },
  system_network_connect: {
    title: 'Conectar à internet',
    description: 'Pode se conectar a serviços de rede.',
  },
  system_network_listen: {
    title: 'Abrir uma porta de rede',
    description: 'Pode abrir uma porta de rede neste computador para que outros programas se conectem a ele.',
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

export default labels

import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'Usar modelos de IA (puede tener costo)',
    description: 'Puede llamar a modelos de IA a través de la pasarela de modelos de N.E.K.O, lo que puede generar costos de uso.',
  },
  system_network_connect: {
    title: 'Conectarse a internet',
    description: 'Puede conectarse a servicios de red.',
  },
  system_network_listen: {
    title: 'Abrir un puerto de red',
    description: 'Puede abrir un puerto de red en este equipo para que otros programas se conecten a él.',
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

export default labels

import labels from '../plugin-permissions/es'
import { withPermissionLabels } from '../plugin-permissions/shared'
import type { PluginConsentMessages } from './en-US'

const messages: PluginConsentMessages = {
  title: 'Permisos',
  dialogTitle: 'Confirmar capacidades del plugin',
  introduction: 'Este plugin puede usar las siguientes capacidades mientras se ejecuta. Confírmalas para iniciarlo.',
  analysisPending: 'El análisis de capacidades aún no ha terminado. Haz clic en iniciar de nuevo para iniciarlo directamente: {names}',
  incomplete: 'El análisis de capacidades está incompleto. Aun así puedes confirmar e iniciar este plugin.',
  upgraded: 'Añadida tras la actualización',
  pending: 'Pendiente de confirmación',
  decision: {
    approved: 'Aprobado',
    denied: 'Rechazado',
  },
  banner: {
    confirm: '{n} capacidades pendientes de confirmación',
    upgradedConfirm: '{n} capacidades nuevas tras la actualización pendientes de confirmación',
    capabilitiesDesc:
      'El plugin puede leer tus conversaciones o memoria, o enviar contenido a N.E.K.O. Tras confirmarlo no se volverá a preguntar hasta que una actualización añada capacidades nuevas.',
    incompleteTitle: 'El análisis de capacidades no se completó del todo',
    incompleteDesc: 'Los resultados pueden estar incompletos; aun así puedes aprobar y seguir usándolo.',
    deniedDesc: 'El inicio automático omitirá este plugin; al iniciarlo manualmente se volverá a preguntar.',
    approvedAt: 'Confirmado el {date}',
    analyzingTitle: 'Analizando las capacidades del plugin…',
    analyzingDesc: 'Los resultados aparecerán aquí automáticamente al terminar.',
  },
  approve: 'Aprobar',
  reject: 'Rechazar',
  approveAndStart: 'Aprobar e iniciar',
  revoke: 'Revocar confirmación',
  revokeDecision: 'Retirar decisión',
  evidenceToggle: 'Ubicaciones del código ({n})',
  meta: {
    analyzed: 'Analizado el {date}',
    version: 'Versión {version}',
    files: '{n} archivos',
  },
  noticesTitle: 'Hay capacidades de plugins pendientes de confirmación',
  loading: 'Cargando información de capacidades…',
  loadFailed: 'No se pudo cargar la información de capacidades.',
  saveFailed: 'No se pudo guardar la decisión. Inténtalo de nuevo.',
  unknown:
    'Aún se desconoce su uso exacto de datos. Los SDK de plugins pueden leer conversaciones y memoria, y enviar contenido a N.E.K.O.',
  noneFound: 'No se encontraron problemas: el análisis del código no encontró uso de permisos ni código que no se pudiera confirmar estáticamente.',
  retry: 'Analizar de nuevo',
  findings: {
    usedTitle: 'Permisos que usa este plugin',
    usedBanner: 'Este plugin usa {n} permiso(s)',
    declared: 'Declarado por el autor',
    detected: 'Encontrado en el código',
    blindSpotsTitle: '{n} lugar(es) no se pudieron confirmar con el análisis estático',
    blindSpotsDesc: 'Estos lugares cargan código de forma dinámica, usan reflexión o ejecutan código generado, o no se analizaron. Lo que hacen no se puede saber sin ejecutar el plugin.',
    unlocated: 'Parte del plugin (sin un archivo concreto)',
    partialTag: 'Parcialmente sin confirmar',
    partialTagHint: 'Parte del código no se pudo analizar de forma estática. Consulta la página de detalles.',
  },
  errors: {
    sourceUnknown: 'No se pudo determinar el origen de instalación actual del plugin, así que el reemplazo se detuvo para proteger los permisos que concediste. El plugin no se modificó. Desinstala el plugin y vuelve a instalarlo para recuperarlo.',
    readFailed: 'No se pueden leer los registros de permisos de plugins en este momento. No se cambió nada. Inténtalo de nuevo en un momento.',
    corrupt: 'El archivo de registros de permisos de plugins (plugin_permissions.json) está dañado y no se puede leer, así que la operación se detuvo. Mueve ese archivo fuera de la carpeta de configuración de N.E.K.O y reinicia N.E.K.O; es posible que los plugins vuelvan a pedir sus permisos.',
    unsupportedVersion: 'Los registros de permisos de plugins se guardaron con una versión más reciente de N.E.K.O y esta versión no puede leerlos, así que la operación se detuvo. Actualiza N.E.K.O a la última versión e inténtalo de nuevo.',
    writeFailed: 'No se pudieron guardar los registros de permisos de plugins. No se cambió nada. Comprueba el espacio libre en disco y el permiso de escritura en la carpeta de configuración de N.E.K.O, e inténtalo de nuevo.',
  },
  warnings: {
    budget_exceeded: 'El análisis se detuvo al alcanzar su límite de trabajo.',
    source_too_large: 'Un archivo fuente superó el límite de tamaño del análisis.',
    source_unreadable: 'No se pudo analizar un archivo fuente.',
    analysis_failed: 'No se pudo completar el análisis en segundo plano.',
  },
  capabilities: {
    conversations_read: {
      title: 'Leer conversaciones',
      description: 'Puede leer mensajes recientes y registros de conversaciones, u observar nuevas conversaciones y respuestas completadas.',
    },
    memory_read: {
      title: 'Leer memoria',
      description: 'Puede buscar en la memoria a largo plazo o leer la memoria del personaje y la información de persona.',
    },
    messages_push: {
      title: 'Enviar contenido a N.E.K.O',
      description: 'Puede enviar mensajes, mostrar tarjetas u otro contenido, o adjuntar contenido a las respuestas.',
    },
  },
}

export default { ...messages, capabilities: withPermissionLabels(messages.capabilities, labels) }

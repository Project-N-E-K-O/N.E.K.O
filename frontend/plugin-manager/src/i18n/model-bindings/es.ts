import bindingErrors from '../model-binding-errors/es'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: 'Asignaciones de API de modelos',
  manage: 'Gestionar slots y uso',
  description: 'Elige un slot de modelo guardado para cada uso declarado por este plugin. Los cambios se guardan al instante.',
  ready: 'Todos los usos de modelo obligatorios están asignados',
  notReady: 'Algunos usos de modelo obligatorios necesitan una asignación',
  readinessHint: 'Este estado solo confirma las asignaciones obligatorias. No comprueba la disponibilidad del proveedor ni controla el inicio del plugin.',
  boundSummary: '{bound} de {total} asignados',
  collapse: 'Contraer',
  expand: 'Expandir',
  required: 'Obligatorio',
  optional: 'Opcional',
  unbound: 'Sin asignar',
  incompatible: 'Faltan capacidades',
  noSlots: 'Todavía no hay slots de modelo. Añade uno en Plugin API y actualiza esta sección.',
  loadFailed: 'No se pudieron cargar las asignaciones. Inténtalo de nuevo.',
  saveFailed: 'No se pudo guardar la asignación. Se sigue mostrando la anterior.',
  capabilities: { text: 'Texto', image_input: 'Entrada de imágenes', tool_calling: 'Llamadas a herramientas', streaming: 'Streaming' },
  status: { bound: 'Asignado', unbound: 'Ningún slot seleccionado', incompatible: 'El slot asignado ya no cumple los requisitos de este uso' },
}

export default messages

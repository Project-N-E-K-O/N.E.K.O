import type { ModelBindingErrorMessages } from './en-US'

const messages: ModelBindingErrorMessages = {
  conflict: 'Otra operación cambió la asignación. Actualiza y revísala antes de reintentar.',
  unknown: 'El resultado no está confirmado. Actualiza para confirmarlo antes de modificarlo.',
  failed: 'No se pudo completar el cambio. Revisa la asignación actual antes de reintentar.',
  version: 'Actualiza la página antes de cambiar la asignación.',
}

export default messages

/**
 * Shared error words of the model binding surfaces.
 * One module per language, all of one shape, so that a language pack carries
 * its own words only (see ../power).
 */

const messages = {
  conflict: 'This binding changed elsewhere. Refresh and review it before trying again.',
  unknown: 'The result is not confirmed. Refresh to confirm it before making another change.',
  failed: 'The change could not be completed. Review the current binding before trying again.',
  version: 'Refresh this page before changing the binding.',
}

export type ModelBindingErrorMessages = typeof messages
export default messages

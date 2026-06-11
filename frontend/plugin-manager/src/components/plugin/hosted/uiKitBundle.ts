import styles from './ui-kit/styles.css?raw'
import runtime from './ui-kit/runtime.js?raw'

export function buildUiKitBundle() {
  return {
    styles,
    runtime,
  }
}

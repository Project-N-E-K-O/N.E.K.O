import styles from './ui-kit/styles.css?raw'
import runtime from './ui-kit/runtime.js?raw'
import displayClient from '../../../../../../static/plugin-ui-sdk/display.js?raw'

export function buildUiKitBundle() {
  return {
    styles,
    // The static SDK stays a standalone classic script. Capture its factory
    // locally when embedding it into the opaque-origin hosted document.
    runtime: `const createPluginDisplayClient = (function () {
const module = { exports: {}, nekoPluginDisplayFactoryOnly: true };
${displayClient}
return module.exports;
})();
${runtime}`,
  }
}

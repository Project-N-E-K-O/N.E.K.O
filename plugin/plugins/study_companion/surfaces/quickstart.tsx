import { useEffect } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { ensureBrandCSS, text } from './study_surface_utils';

export default function Quickstart(props: PluginSurfaceProps) {
  useEffect(() => {
    ensureBrandCSS();
  }, []);

  return (
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.quickstart', 'Quickstart')}</h1>
          <span>{text(props, 'ui.quickstart.subtitle', 'Capture, ask, review, export')}</span>
        </div>
      </header>
      <section className="study-panel__state">
        <div>
          <span>{text(props, 'ui.button.ocr', 'OCR')}</span>
          <strong>1</strong>
        </div>
        <div>
          <span>{text(props, 'ui.button.generate_question', 'Generate Question')}</span>
          <strong>2</strong>
        </div>
        <div>
          <span>{text(props, 'ui.button.summarize_session', 'Summarize Session')}</span>
          <strong>3</strong>
        </div>
      </section>
    </div>
  );
}

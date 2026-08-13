(() => {
  'use strict';

  function formatDiagnostic(diagnostic, t) {
    const messages = {
      timeout: ['ui.error.llm_timeout', 'The model request timed out. Please retry shortly.'],
      rate_limited: ['ui.error.llm_rate_limited', 'The model service is receiving too many requests. Please retry shortly.'],
      authentication_failed: ['ui.error.llm_authentication_failed', 'The configured model credential is invalid. Check it in N.E.K.O model settings.'],
      model_not_supported: ['ui.error.llm_model_not_supported', 'The configured model is unavailable or does not support this request.'],
      provider_unavailable: ['ui.error.llm_provider_unavailable', 'The model service is temporarily unavailable. Please retry shortly.'],
      unsupported_provider: ['ui.error.llm_unsupported_provider', 'The configured model provider protocol is not supported by Study Companion.'],
      context_limit_exceeded: ['ui.error.llm_context_limit_exceeded', 'The content exceeds the configured model context limit. Shorten it and retry.'],
      vision_not_supported: ['ui.error.llm_vision_not_supported', 'The configured Vision model does not accept image input.'],
      agent_quota_exceeded: ['ui.error.llm_agent_quota_exceeded', 'The free Agent daily quota has been used up. Try again later or configure another Agent model.'],
      invalid_endpoint: ['ui.error.llm_invalid_endpoint', 'The configured model endpoint is invalid or unsupported.'],
      invalid_request: ['ui.error.llm_invalid_request', 'The model service rejected the request as invalid.'],
      invalid_image: ['ui.error.llm_invalid_image', 'The image could not be read. Please use a valid JPEG or PNG image.'],
    };
    const [key, fallback] = messages[String(diagnostic || '').trim()]
      || ['ui.error.llm_call_failed', 'The model service request failed. Please retry.'];
    return t(key, fallback);
  }

  function render(cards, runtime, t, tf) {
    cards.forEach((card) => {
      const role = String(card.dataset.modelRuntime || '');
      const item = runtime && typeof runtime[role] === 'object' ? runtime[role] : {};
      const model = card.querySelector('.model-runtime__model');
      const meta = card.querySelector('.model-runtime__meta');
      const status = card.querySelector('.model-runtime__status');
      const configured = item.configured === true;
      const supported = item.transport_supported !== false;
      const group = String(item.group || (role === 'vision' ? 'vision' : 'agent'));
      const protocol = String(item.provider_type || '').trim();
      if (model) model.textContent = String(item.model || t('ui.settings.model_runtime.not_configured', 'Not configured'));
      if (meta) meta.textContent = tf('ui.settings.model_runtime.meta', 'Group: {group} · Protocol: {protocol}', {
        group,
        protocol: protocol || t('ui.settings.model_runtime.protocol_unknown', 'Unknown'),
      });
      if (!status) return;
      let key = 'not_configured';
      if (configured && !supported) key = 'unsupported';
      else if (configured && item.credential_configured !== true) key = 'credential_missing';
      else if (configured) key = role === 'vision' ? 'configured_vision_unknown' : 'ready';
      status.textContent = t(`ui.settings.model_runtime.${key}`);
      status.dataset.ready = configured && supported && item.credential_configured === true ? 'true' : 'false';
    });
  }

  window.StudyModelRuntime = Object.freeze({ formatDiagnostic, render });
})();

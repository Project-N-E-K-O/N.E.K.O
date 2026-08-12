(() => {
  'use strict';

  function formatKnowledgeGuidanceEvidence(outcome, translate) {
    const status = String(outcome?.knowledge_guidance_status || '').trim().toLowerCase();
    const hasOutcome = typeof outcome?.knowledge_guidance_applied === 'boolean' || Boolean(status);
    if (!hasOutcome || status === 'not_applicable') return '';
    if (status === 'not_matched') {
      return translate(
        'ui.knowledge_guidance.not_matched',
        'No trustworthy related knowledge graph was matched; no nodes from other subjects were used.',
      );
    }
    if (status === 'low_confidence') {
      return translate(
        'ui.knowledge_guidance.low_confidence',
        'The knowledge graph match was uncertain, so it was not applied.',
      );
    }
    if (status === 'routing_unavailable') {
      return translate(
        'ui.knowledge_guidance.routing_unavailable',
        'Knowledge graph routing was not applicable, so the answer continued without graph guidance.',
      );
    }
    if (outcome?.knowledge_guidance_applied !== true && status !== 'applied') return '';

    const focusTopic = outcome?.knowledge_guidance_focus_topic;
    const focusLabel = String(focusTopic?.label || focusTopic?.name || '').trim();
    if (!focusLabel) {
      return translate(
        'ui.knowledge_guidance.not_matched',
        'No trustworthy related knowledge graph was matched; no nodes from other subjects were used.',
      );
    }
    const relatedLabels = Array.isArray(outcome?.knowledge_guidance_related_topics)
      ? outcome.knowledge_guidance_related_topics
        .map((topic) => String(topic?.label || topic?.name || '').trim())
        .filter(Boolean)
      : [];
    const localizedValue = (group, rawValue) => {
      const value = String(rawValue || '').trim().toLowerCase();
      if (!value) return '';
      return translate(`ui.knowledge_guidance.${group}.${value}`, value.replaceAll('_', ' '));
    };
    const subject = localizedValue('subject', outcome?.knowledge_guidance_subject);
    const contentType = localizedValue('content_type', outcome?.knowledge_guidance_content_type);
    const entity = String(outcome?.knowledge_guidance_entity || '').trim();
    const source = localizedValue('source', outcome?.knowledge_guidance_source);
    return [
      translate('ui.knowledge_guidance.applied', 'Knowledge graph applied'),
      subject ? `${translate('ui.knowledge_guidance.subject', 'Subject')}: ${subject}` : '',
      contentType ? `${translate('ui.knowledge_guidance.content_type', 'Content type')}: ${contentType}` : '',
      entity ? `${translate('ui.knowledge_guidance.entity', 'Entity')}: ${entity}` : '',
      `${translate('ui.knowledge_guidance.focus_topic', 'Focus topic')}: ${focusLabel}`,
      relatedLabels.length > 0
        ? `${translate('ui.knowledge_guidance.related_topics', 'Related topics')}: ${relatedLabels.join(', ')}`
        : '',
      source ? `${translate('ui.knowledge_guidance.source', 'Source')}: ${source}` : '',
    ].filter(Boolean).join('\n');
  }

  function formatSolutionNarrationNotice(outcome, translate) {
    const status = String(outcome?.solution_narration_status || '').trim().toLowerCase();
    const reason = String(outcome?.solution_narration_reason || '').trim().toLowerCase();
    const diagnostic = String(outcome?.diagnostic || '').trim().toLowerCase();
    const missingSections = Array.isArray(outcome?.solution_narration_missing_sections)
      ? outcome.solution_narration_missing_sections.map((section) => String(section).trim().toLowerCase())
      : [];
    const hasOutcome = typeof outcome?.solution_narration_scheduled === 'boolean'
      || Boolean(status)
      || Boolean(reason)
      || missingSections.length > 0
      || typeof outcome?.solution_repair_attempted === 'boolean';
    if (!hasOutcome || status === 'not_applicable') return '';
    if (outcome.solution_narration_scheduled === true || status === 'scheduled') {
      return translate('ui.status.solution_narration_scheduled', 'Narration has been scheduled.');
    }
    if (status === 'disabled') {
      return translate('ui.status.solution_narration_disabled', 'Solution narration is turned off.');
    }
    if (status === 'degraded') {
      return translate(
        'ui.error.solution_narration_degraded',
        'The explanation used a fallback response, so narration was not scheduled.',
      );
    }
    if (diagnostic === 'output_truncated' && reason === 'insufficient_time_budget') {
      return translate(
        'ui.error.solution_narration_truncated_repair_timeout',
        'The solution was truncated after reaching the output length limit. Automatic completion could not finish within the time limit, so narration was not scheduled. Please regenerate a concise solution.',
      );
    }
    if (
      status === 'repair_failed'
      || reason === 'invalid_repair_response'
      || (!status && outcome.solution_repair_attempted === true && outcome.solution_narration_scheduled === false)
    ) {
      return translate(
        'ui.error.solution_narration_repair_failed',
        'The explanation structure could not be repaired, so narration was not scheduled. Please analyze it again.',
      );
    }
    if (status === 'incomplete' || reason.startsWith('missing_')) {
      if (reason === 'missing_answer' || missingSections.includes('answer')) {
        return translate(
          'ui.error.solution_narration_missing_answer',
          'The explanation is incomplete: the Answer section is missing, so narration was not scheduled. Please analyze it again.',
        );
      }
      return translate(
        'ui.error.solution_narration_incomplete',
        'The explanation is incomplete, so narration was not scheduled. Please analyze it again.',
      );
    }
    if (status === 'runtime_unavailable' || reason === 'event_bus_unavailable') {
      return translate(
        'ui.error.solution_narration_runtime_unavailable',
        'Narration is temporarily unavailable. The explanation is still shown.',
      );
    }
    if (status === 'delivery_failed' || reason === 'event_delivery_failed') {
      return translate(
        'ui.error.solution_narration_delivery_failed',
        'The narration request could not be delivered. Please try again.',
      );
    }
    if (outcome.solution_narration_scheduled === false) {
      return translate(
        'ui.error.solution_narration_not_scheduled',
        'Narration was not scheduled for this explanation.',
      );
    }
    return '';
  }

  function formatGeneralNarrationNotice(outcome, translate) {
    const status = String(outcome?.general_narration_status || '').trim().toLowerCase();
    const reason = String(outcome?.general_narration_reason || '').trim().toLowerCase();
    const hasOutcome = typeof outcome?.general_narration_scheduled === 'boolean'
      || Boolean(status)
      || Boolean(reason)
      || Boolean(String(outcome?.general_narration_response_mode || '').trim());
    if (!hasOutcome || status === 'not_applicable') return '';

    if (status) {
      if (status === 'scheduled') {
        return translate('ui.status.general_narration_scheduled', 'General narration has been scheduled.');
      }
      if (status === 'disabled') {
        return translate('ui.status.general_narration_disabled', 'General narration is turned off.');
      }
      if (status === 'degraded') {
        return translate(
          'ui.error.general_narration_degraded',
          'The response used a fallback or had no narratable content, so general narration was not scheduled.',
        );
      }
      if (status === 'runtime_unavailable') {
        return translate(
          'ui.error.general_narration_runtime_unavailable',
          'General narration is temporarily unavailable. The response is still shown.',
        );
      }
      if (status === 'delivery_failed') {
        return translate(
          'ui.error.general_narration_delivery_failed',
          'The general narration request could not be delivered. Please try again.',
        );
      }
      return '';
    }

    if (reason === 'communication_disabled' || reason === 'general_narration_disabled') {
      return translate('ui.status.general_narration_disabled', 'General narration is turned off.');
    }
    if (reason === 'degraded_reply' || reason === 'empty_reply') {
      return translate(
        'ui.error.general_narration_degraded',
        'The response used a fallback or had no narratable content, so general narration was not scheduled.',
      );
    }
    if (reason === 'event_bus_unavailable') {
      return translate(
        'ui.error.general_narration_runtime_unavailable',
        'General narration is temporarily unavailable. The response is still shown.',
      );
    }
    if (reason === 'event_delivery_failed') {
      return translate(
        'ui.error.general_narration_delivery_failed',
        'The general narration request could not be delivered. Please try again.',
      );
    }
    if (outcome?.general_narration_scheduled === true) {
      return translate('ui.status.general_narration_scheduled', 'General narration has been scheduled.');
    }
    return '';
  }

  window.StudyOutcomeFormatters = Object.freeze({
    formatGeneralNarrationNotice,
    formatKnowledgeGuidanceEvidence,
    formatSolutionNarrationNotice,
  });
})();

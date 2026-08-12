function formatSolutionNarrationNotice(outcome) {
  const status = String(outcome?.solution_narration_status || '').trim().toLowerCase();
  const reason = String(outcome?.solution_narration_reason || '').trim().toLowerCase();
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
    return t('ui.status.solution_narration_scheduled', 'Narration has been scheduled.');
  }
  if (status === 'disabled') {
    return t('ui.status.solution_narration_disabled', 'Solution narration is turned off.');
  }
  if (status === 'degraded') {
    return t(
      'ui.error.solution_narration_degraded',
      'The explanation used a fallback response, so narration was not scheduled.',
    );
  }
  if (
    status === 'repair_failed'
    || reason === 'invalid_repair_response'
    || (!status && outcome.solution_repair_attempted === true && outcome.solution_narration_scheduled === false)
  ) {
    return t(
      'ui.error.solution_narration_repair_failed',
      'The explanation structure could not be repaired, so narration was not scheduled. Please analyze it again.',
    );
  }
  if (status === 'incomplete' || reason.startsWith('missing_')) {
    if (reason === 'missing_answer' || missingSections.includes('answer')) {
      return t(
        'ui.error.solution_narration_missing_answer',
        'The explanation is incomplete: the Answer section is missing, so narration was not scheduled. Please analyze it again.',
      );
    }
    return t(
      'ui.error.solution_narration_incomplete',
      'The explanation is incomplete, so narration was not scheduled. Please analyze it again.',
    );
  }
  if (status === 'runtime_unavailable' || reason === 'event_bus_unavailable') {
    return t(
      'ui.error.solution_narration_runtime_unavailable',
      'Narration is temporarily unavailable. The explanation is still shown.',
    );
  }
  if (status === 'delivery_failed' || reason === 'event_delivery_failed') {
    return t(
      'ui.error.solution_narration_delivery_failed',
      'The narration request could not be delivered. Please try again.',
    );
  }
  if (outcome.solution_narration_scheduled === false) {
    return t(
      'ui.error.solution_narration_not_scheduled',
      'Narration was not scheduled for this explanation.',
    );
  }
  return '';
}

(function () {
  let panelToken = 0;

  function t(ctx, key, fallback) {
    return ctx.t ? ctx.t(key, fallback) : fallback;
  }

  function tf(ctx, key, fallback, values) {
    return ctx.tf ? ctx.tf(key, fallback, values) : fallback.replace(/\{([^}]+)\}/g, (_, name) => values[name] ?? '');
  }

  function el(tag, className = '', text = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== '') node.textContent = text;
    return node;
  }

  function panel(ctx, surfaceId, subtitle = '') {
    const root = el('div', `study-panel surface-shell surface-shell--${surfaceId}`);
    root.dataset.surface = surfaceId;
    const header = el('header', 'study-panel__header');
    const titleWrap = el('div', 'study-panel__title');
    titleWrap.append(
      el('h1', '', ctx.label ? ctx.label(surfaceId) : surfaceId),
    );
    const status = el('div', 'study-panel__status-chip', subtitle || t(ctx, 'ui.status.ready', 'Ready'));
    header.append(titleWrap, status);
    root.appendChild(header);
    return root;
  }

  function state(items) {
    const section = el('section', 'study-panel__state');
    items.forEach(([label, value], index) => {
      const item = el('div', 'study-panel__metric');
      item.dataset.metricIndex = String(index + 1);
      item.append(el('span', 'study-panel__metric-label', label), el('strong', 'study-panel__metric-value', String(value ?? '-')));
      section.appendChild(item);
    });
    return section;
  }

  function actions(items, className = '') {
    const row = el('div', `study-panel__actions${className ? ` ${className}` : ''}`);
    items.forEach((item) => row.appendChild(item));
    return row;
  }

  function button(label, handler, primary = false) {
    const item = el('button', primary ? 'button button-primary' : 'button button-secondary', label);
    item.type = 'button';
    item.addEventListener('click', async () => {
      item.disabled = true;
      try {
        await handler();
      } finally {
        item.disabled = false;
      }
    });
    return item;
  }

  function input(value, attrs = {}) {
    const node = el('input');
    node.value = value;
    Object.entries(attrs).forEach(([key, val]) => node.setAttribute(key, String(val)));
    return node;
  }

  function select(value, options) {
    const node = el('select');
    options.forEach(([optionValue, label]) => {
      const option = el('option', '', label);
      option.value = optionValue;
      node.appendChild(option);
    });
    node.value = value;
    return node;
  }

  function deckTypeLabel(ctx, value) {
    const key = String(value || 'custom');
    const labels = {
      word: ['ui.memory.deck_type.word', 'Word'],
      passage: ['ui.memory.deck_type.passage', 'Passage'],
      formula: ['ui.memory.deck_type.formula', 'Formula'],
      custom: ['ui.memory.deck_type.custom', 'Custom'],
    };
    const pair = labels[key] || labels.custom;
    return t(ctx, pair[0], pair[1]);
  }

  function itemTypeLabel(ctx, value) {
    return deckTypeLabel(ctx, value);
  }

  function targetTypeLabel(ctx, value) {
    const key = String(value || '').trim().toLowerCase();
    if (key === 'subject') return t(ctx, 'ui.label.subject', 'Subject');
    if (key === 'deck') return t(ctx, 'ui.memory.deck', 'Deck');
    return key || '-';
  }

  function deckTypeOptions(ctx) {
    return ['word', 'passage', 'formula', 'custom'].map((value) => [value, deckTypeLabel(ctx, value)]);
  }

  function unitLabel(ctx, value) {
    const key = String(value || 'cards');
    const labels = {
      card: ['ui.daily_goal.deck_unit_cards', 'cards'],
      cards: ['ui.daily_goal.deck_unit_cards', 'cards'],
      minute: ['ui.daily_goal.deck_unit_minutes', 'minutes'],
      minutes: ['ui.daily_goal.deck_unit_minutes', 'minutes'],
      attempt: ['ui.daily_goal.deck_unit_attempts', 'attempts'],
      attempts: ['ui.daily_goal.deck_unit_attempts', 'attempts'],
    };
    const pair = labels[key] || [null, key];
    return pair[0] ? t(ctx, pair[0], pair[1]) : pair[1];
  }

  function unitOptions(ctx) {
    return ['cards', 'minutes', 'attempts'].map((value) => [value, unitLabel(ctx, value)]);
  }

  function pomodoroModeLabel(ctx, value) {
    const key = String(value || 'focus');
    const labels = {
      focus: ['ui.pomodoro.mode.focus', 'Focus'],
      break_short: ['ui.pomodoro.mode.break_short', 'Short break'],
      break_long: ['ui.pomodoro.mode.break_long', 'Long break'],
    };
    const pair = labels[key] || [null, key];
    return pair[0] ? t(ctx, pair[0], pair[1]) : pair[1];
  }

  function pomodoroStateLabel(ctx, value) {
    const key = String(value || 'idle');
    const labels = {
      idle: ['ui.status.pomodoro.idle', 'Idle'],
      focusing: ['ui.status.pomodoro.focusing', 'Focusing'],
      paused: ['ui.status.pomodoro.paused', 'Paused'],
      short_break: ['ui.status.pomodoro.short_break', 'Short break'],
      long_break: ['ui.status.pomodoro.long_break', 'Long break'],
      cancelled: ['ui.status.pomodoro.cancelled', 'Stopped'],
      completed: ['ui.status.pomodoro.completed', 'Completed'],
    };
    const pair = labels[key] || [null, key];
    return pair[0] ? t(ctx, pair[0], pair[1]) : pair[1];
  }

  function formatLabel(ctx, value) {
    const key = String(value || '');
    const labels = {
      csv: ['ui.format.csv', 'CSV'],
      json: ['ui.format.json', 'JSON'],
      markdown: ['ui.format.markdown', 'Markdown'],
      pdf: ['ui.format.pdf', 'PDF'],
      docx: ['ui.format.docx', 'DOCX'],
      xmind: ['ui.format.xmind', 'XMind'],
    };
    const pair = labels[key] || [null, key];
    return pair[0] ? t(ctx, pair[0], pair[1]) : pair[1];
  }

  function exportStyleLabel(ctx, value) {
    const key = String(value || 'neko');
    const labels = {
      neko: ['ui.export.style.neko', 'Neko'],
      academic: ['ui.export.style.academic', 'Academic'],
      compact: ['ui.export.style.compact', 'Compact'],
    };
    const pair = labels[key] || labels.neko;
    return t(ctx, pair[0], pair[1]);
  }

  function labeled(labelText, field) {
    const label = el('label');
    label.append(el('span', '', labelText), field);
    return label;
  }

  function pre(text) {
    return el('pre', 'study-panel__preview', text || '');
  }

  function errText(error) {
    return error instanceof Error ? error.message : String(error);
  }

  function safeList(payload, key) {
    return Array.isArray(payload?.[key]) ? payload[key] : [];
  }

  function valid(root, token, allowDetached = false) {
    return token === panelToken && (allowDetached || root.isConnected);
  }

  function replace(root, ctx, surfaceId, subtitle, children) {
    const next = panel(ctx, surfaceId, subtitle);
    root.className = next.className;
    root.dataset.surface = surfaceId;
    children.forEach((child) => next.appendChild(child));
    root.replaceChildren(...Array.from(next.childNodes));
  }

  function formatSeconds(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
  }

  function row(...parts) {
    const item = el('div', 'study-panel__row');
    parts.forEach((part, index) => {
      const child = typeof part === 'string' ? el('span', index === 0 ? 'study-panel__row-title' : 'study-panel__row-copy', part) : part;
      item.appendChild(child);
    });
    return item;
  }

  function renderDueReview(ctx, token) {
    const root = panel(ctx, 'due-review-panel', t(ctx, 'ui.status.loading', 'Loading...'));
    let reviews = [];
    let showAnswer = false;
    let busy = false;
    let status = '';
    let focusMinutes = 25;

    async function refresh() {
      const payload = await ctx.callPlugin('study_memory_due_reviews', { limit: 100 });
      reviews = safeList(payload, 'due_reviews');
      showAnswer = false;
      draw();
    }

    async function rate(rating) {
      const current = reviews[0];
      if (!current?.item_id || busy) return;
      busy = true;
      try {
        await ctx.callPlugin('study_memory_review_item', { item_id: current.item_id, rating });
        await refresh();
        status = t(ctx, 'ui.memory.review_saved', 'Review saved');
      } catch (error) {
        status = errText(error);
      } finally {
        busy = false;
        draw();
      }
    }

    async function startFocus(deckId) {
      busy = true;
      try {
        await ctx.callPlugin('study_pomodoro_start', { deck_id: deckId, focus_minutes: focusMinutes });
        status = t(ctx, 'ui.memory.focus_started', 'Focus started');
      } catch (error) {
        status = errText(error);
      } finally {
        busy = false;
        draw();
      }
    }

    function draw() {
      if (!valid(root, token)) return;
      const current = reviews[0];
      const cardText = current
        ? `${current.deck?.name || ''}\n\n${current.item?.prompt || current.front || current.item_id}\n\n${showAnswer ? current.item?.answer || current.back || '' : ''}`
        : t(ctx, 'ui.memory.empty_due', 'No due memory cards');
      const focusInput = input(String(focusMinutes), { type: 'number', min: 1, step: 1 });
      focusInput.addEventListener('change', () => { focusMinutes = Math.max(1, Math.floor(Number(focusInput.value) || 1)); draw(); });
      replace(root, ctx, 'due-review-panel', status || String(reviews.length), [
        pre(cardText),
        actions([
          button(t(ctx, 'ui.button.flip', 'Flip'), async () => { showAnswer = !showAnswer; draw(); }),
          ...['again', 'hard', 'good', 'easy'].map((rating) => button(t(ctx, `ui.button.rating.${rating}`, rating), () => rate(rating))),
          button(t(ctx, 'ui.button.refresh', 'Refresh'), refresh),
          labeled(t(ctx, 'ui.summary.memory_focus_minutes', 'Focus minutes'), focusInput),
        ], 'study-panel__actions--toolbar'),
        actions(reviews.map((review) => {
          const r = Number.isFinite(Number(review.retrievability)) ? `${Math.round(Number(review.retrievability) * 100)}%` : '-';
          const focus = review.deck?.id ? button(t(ctx, 'ui.focus.start_with_deck', 'Start Focus'), () => startFocus(review.deck.id)) : el('span');
          return row(`${review.deck?.name || ''} / ${itemTypeLabel(ctx, review.item?.item_type)} / ${r}`, review.item?.prompt || review.front || review.item_id || '-', focus);
        }), 'study-panel__actions--list'),
      ]);
    }

    refresh().catch((error) => { status = errText(error); draw(); });
    return root;
  }

  function renderPomodoro(ctx, token) {
    const root = panel(ctx, 'pomodoro-panel', t(ctx, 'ui.status.loading', 'Loading...'));
    let status = {};
    let errorText = '';
    let refreshTimer = 0;
    let refreshing = false;

    function activeTimerState() {
      return ['focusing', 'short_break', 'long_break'].includes(String(status.state || ''));
    }

    function stopTimer() {
      if (refreshTimer) {
        window.clearTimeout(refreshTimer);
        refreshTimer = 0;
      }
    }

    function scheduleRefresh() {
      stopTimer();
      if (!valid(root, token)) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = 0;
        refresh().catch((error) => {
          errorText = errText(error);
          draw();
          scheduleRefresh();
        });
      }, activeTimerState() ? 1000 : 5000);
    }

    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        status = await ctx.callPlugin('study_pomodoro_status');
        errorText = '';
        draw();
        scheduleRefresh();
      } finally {
        refreshing = false;
      }
    }

    async function act(entryId) {
      try {
        stopTimer();
        status = await ctx.callPlugin(entryId);
        errorText = '';
      } catch (error) {
        errorText = errText(error);
      }
      draw();
      scheduleRefresh();
    }

    function draw() {
      if (!valid(root, token, true)) return;
      const children = [];
      if (errorText) children.push(pre(errorText));
      children.push(
        state([
          [t(ctx, 'ui.label.remaining', 'Remaining'), formatSeconds(status.remaining_seconds)],
          [t(ctx, 'ui.label.sessions', 'Sessions'), status.session_count || 0],
          [t(ctx, 'ui.label.mode', 'Mode'), pomodoroModeLabel(ctx, status.mode)],
        ]),
      );
      const ring = el('div', 'pomodoro-ring', formatSeconds(status.remaining_seconds));
      ring.dataset.mode = String(status.mode || 'focus');
      children.push(ring, actions([
        button(t(ctx, 'ui.button.start', 'Start'), () => act('study_pomodoro_start')),
        button(t(ctx, 'ui.button.pause', 'Pause'), () => act('study_pomodoro_pause')),
        button(t(ctx, 'ui.button.resume', 'Resume'), () => act('study_pomodoro_resume')),
        button(t(ctx, 'ui.button.stop', 'Stop'), () => act('study_pomodoro_stop')),
        button(t(ctx, 'ui.button.skip_break', 'Skip break'), () => act('study_pomodoro_skip_break')),
      ], 'study-panel__actions--primary'));
      replace(root, ctx, 'pomodoro-panel', pomodoroStateLabel(ctx, status.state), children);
    }

    refresh().catch((error) => { errorText = errText(error); refreshing = false; draw(); scheduleRefresh(); });
    return root;
  }

  function renderHabit(ctx, token) {
    const root = panel(ctx, 'habit-dashboard', t(ctx, 'ui.status.loading', 'Loading...'));
    let payload = {};
    let errorText = '';

    async function refresh() {
      const [status, goals, checkin, summary, supervision] = await Promise.all([
        ctx.callPlugin('study_pomodoro_status'),
        ctx.callPlugin('study_goals'),
        ctx.callPlugin('study_checkin_status'),
        ctx.callPlugin('study_session_summary'),
        ctx.callPlugin('study_supervision_status'),
      ]);
      payload = { status, goals: safeList(goals, 'goals'), checkin, summary, supervision };
      errorText = '';
      draw();
    }

    async function act(entryId, args = {}) {
      try {
        await ctx.callPlugin(entryId, args);
        await refresh();
      } catch (error) {
        errorText = errText(error);
        draw();
      }
    }

    function draw() {
      if (!valid(root, token)) return;
      const goals = Array.isArray(payload.goals) ? payload.goals : [];
      const children = [];
      if (errorText) children.push(pre(errorText));
      children.push(
        state([
          [t(ctx, 'ui.label.streak', 'Streak'), payload.checkin?.streak_days || 0],
          [t(ctx, 'ui.label.focus_minutes', 'Focus'), payload.summary?.total_focus_minutes || 0],
          [t(ctx, 'ui.label.goals', 'Goals'), goals.length],
        ]),
        actions([
          button(t(ctx, 'ui.button.checkin', 'Check in'), () => act('study_checkin_manual'), true),
          button(payload.supervision?.enabled ? t(ctx, 'ui.button.quiet', 'Quiet') : t(ctx, 'ui.button.supervise', 'Supervise'), () => act('study_supervision_toggle', { enabled: !payload.supervision?.enabled })),
        ], 'study-panel__actions--primary'),
        actions(goals.map((goal) => row(`${goal.subject || targetTypeLabel(ctx, goal.target_type)}: ${goal.progress_amount || 0}/${goal.target_amount || 0} ${unitLabel(ctx, goal.unit)}`)), 'study-panel__actions--list'),
      );
      replace(root, ctx, 'habit-dashboard', pomodoroStateLabel(ctx, payload.status?.state), children);
    }

    refresh().catch((error) => { errorText = errText(error); draw(); });
    return root;
  }

  function renderDailyGoals(ctx, token) {
    const root = panel(ctx, 'daily-goal-editor', t(ctx, 'ui.status.loading', 'Loading...'));
    let goals = [];
    let subject = 'study';
    let targetAmount = 25;
    let errorText = '';

    async function refresh() {
      const payload = await ctx.callPlugin('study_goals');
      goals = safeList(payload, 'goals');
      draw();
    }

    async function createGoal() {
      try {
        await ctx.callPlugin('study_goal_create', { target_type: 'subject', subject, target_amount: targetAmount, unit: 'minute' });
        errorText = '';
        await refresh();
      } catch (error) {
        errorText = errText(error);
        draw();
      }
    }

    async function deleteGoal(goalId) {
      try {
        await ctx.callPlugin('study_goal_delete', { goal_id: goalId });
        errorText = '';
        await refresh();
      } catch (error) {
        errorText = errText(error);
        draw();
      }
    }

    function draw() {
      if (!valid(root, token)) return;
      const subjectInput = input(subject);
      subjectInput.addEventListener('input', () => { subject = subjectInput.value; });
      const targetInput = input(String(targetAmount), { type: 'number', min: 1 });
      targetInput.addEventListener('input', () => { targetAmount = Number(targetInput.value) || 1; });
      const children = [];
      if (errorText) children.push(pre(errorText));
      children.push(
        state([
          [t(ctx, 'ui.label.goals', 'Goals'), goals.length],
          [t(ctx, 'ui.label.subject', 'Subject'), subject],
          [t(ctx, 'ui.label.target', 'Target'), targetAmount],
        ]),
        actions([
          labeled(t(ctx, 'ui.label.subject', 'Subject'), subjectInput),
          labeled(t(ctx, 'ui.label.target', 'Target'), targetInput),
          button(t(ctx, 'ui.button.create_goal', 'Create'), createGoal, true),
        ], 'study-panel__actions--form'),
        actions(goals.map((goal) => button(`${goal.subject || targetTypeLabel(ctx, goal.target_type)}: ${goal.progress_amount || 0}/${goal.target_amount || 0} ${unitLabel(ctx, goal.unit)}`, () => deleteGoal(goal.id))), 'study-panel__actions--list'),
      );
      replace(root, ctx, 'daily-goal-editor', String(goals.length), children);
    }

    refresh().catch((error) => { errorText = errText(error); draw(); });
    return root;
  }

  function renderDeckList(ctx, token) {
    const root = panel(ctx, 'memory-deck-list', t(ctx, 'ui.status.loading', 'Loading...'));
    let decks = [];
    let name = '';
    let deckType = 'word';
    let goalAmount = 10;
    let goalUnit = 'cards';
    let status = '';

    async function refresh() {
      const payload = await ctx.callPlugin('study_memory_list_decks', { limit: 100 });
      decks = safeList(payload, 'decks');
      draw();
    }

    async function createDeck() {
      if (!name.trim()) {
        status = t(ctx, 'ui.memory.error_missing_deck_name', 'Deck name is required');
        draw();
        return;
      }
      try {
        await ctx.callPlugin('study_memory_create_deck', { name: name.trim(), deck_type: deckType });
        name = '';
        status = t(ctx, 'ui.status.reply_ready', 'Reply ready');
        await refresh();
      } catch (error) {
        status = errText(error);
        draw();
      }
    }

    async function deleteDeck(deckId) {
      try {
        await ctx.callPlugin('study_memory_delete_deck', { deck_id: deckId });
        await refresh();
      } catch (error) {
        status = errText(error);
        draw();
      }
    }

    async function saveGoal(deckId) {
      try {
        await ctx.callPlugin('study_memory_set_deck_goal', { deck_id: deckId, target_amount: goalAmount, unit: goalUnit });
        status = t(ctx, 'ui.memory.goal_saved', 'Goal saved');
        await refresh();
      } catch (error) {
        status = errText(error);
        draw();
      }
    }

    function draw() {
      if (!valid(root, token)) return;
      const nameInput = input(name);
      nameInput.addEventListener('input', () => { name = nameInput.value; });
      const typeSelect = select(deckType, deckTypeOptions(ctx));
      typeSelect.addEventListener('change', () => { deckType = typeSelect.value; });
      const goalInput = input(String(goalAmount), { type: 'number', min: 1 });
      goalInput.addEventListener('input', () => { goalAmount = Math.max(1, Number(goalInput.value) || 1); });
      const unitSelect = select(goalUnit, unitOptions(ctx));
      unitSelect.addEventListener('change', () => { goalUnit = unitSelect.value; });
      replace(root, ctx, 'memory-deck-list', status || String(decks.length), [
        state([
          [t(ctx, 'ui.memory.title', 'Memory Deck'), decks.length],
          [t(ctx, 'ui.label.name', 'Name'), name || '-'],
          [t(ctx, 'ui.memory.deck_type', 'Deck Type'), deckTypeLabel(ctx, deckType)],
        ]),
        actions([
          labeled(t(ctx, 'ui.label.name', 'Name'), nameInput),
          labeled(t(ctx, 'ui.memory.deck_type', 'Deck Type'), typeSelect),
          labeled(t(ctx, 'ui.daily_goal.set_for_deck', 'Deck goal'), goalInput),
          labeled(t(ctx, 'ui.memory.deck_goal_unit', 'Unit'), unitSelect),
          button(t(ctx, 'ui.button.create', 'Create'), createDeck, true),
        ], 'study-panel__actions--form'),
        actions(decks.map((deck) => row(
          `${deck.name} / ${deckTypeLabel(ctx, deck.deck_type)} / ${deck.item_count || 0}`,
          button(t(ctx, 'ui.daily_goal.set_for_deck', 'Set Goal'), () => saveGoal(deck.id)),
          button(t(ctx, 'ui.button.delete', 'Delete'), () => deleteDeck(deck.id)),
        )), 'study-panel__actions--list'),
      ]);
    }

    refresh().catch((error) => { status = errText(error); draw(); });
    return root;
  }

  function renderMemoryImporter(ctx, token) {
    const root = panel(ctx, 'memory-importer', t(ctx, 'ui.status.loading', 'Loading...'));
    let decks = [];
    let deckId = '';
    let fmt = 'csv';
    let content = 'word,meaning,example_sentence,tags\n';
    let result = '';

    async function refresh() {
      const payload = await ctx.callPlugin('study_memory_list_decks', { limit: 100 });
      decks = safeList(payload, 'decks');
      deckId = deckId || decks[0]?.id || '';
      draw();
    }

    async function importWords() {
      if (!deckId) {
        result = t(ctx, 'ui.memory.error_missing_deck', 'Choose a deck first');
        draw();
        return;
      }
      try {
        result = JSON.stringify(await ctx.callPlugin('study_memory_import_words', { deck_id: deckId, content, fmt }), null, 2);
      } catch (error) {
        result = errText(error);
      }
      draw();
    }

    function draw() {
      if (!valid(root, token)) return;
      const deckSelect = select(deckId, decks.map((deck) => [deck.id, `${deck.name} / ${deckTypeLabel(ctx, deck.deck_type)}`]));
      deckSelect.addEventListener('change', () => { deckId = deckSelect.value; });
      const fmtSelect = select(fmt, [['csv', formatLabel(ctx, 'csv')], ['json', formatLabel(ctx, 'json')]]);
      fmtSelect.addEventListener('change', () => { fmt = fmtSelect.value; });
      const textarea = el('textarea');
      textarea.value = content;
      textarea.addEventListener('input', () => { content = textarea.value; });
      replace(root, ctx, 'memory-importer', t(ctx, 'ui.memory.import_hint', 'CSV columns: word, meaning, example_sentence, tags'), [
        state([
          [t(ctx, 'ui.memory.deck', 'Deck'), deckId || '-'],
          [t(ctx, 'ui.label.format', 'Format'), formatLabel(ctx, fmt)],
        ]),
        actions([
          labeled(t(ctx, 'ui.memory.deck', 'Deck'), deckSelect),
          labeled(t(ctx, 'ui.label.format', 'Format'), fmtSelect),
        ], 'study-panel__actions--form'),
        textarea,
        actions([button(t(ctx, 'ui.button.import', 'Import'), importWords, true)], 'study-panel__actions--primary'),
        pre(result),
      ]);
    }

    refresh().catch((error) => { result = errText(error); draw(); });
    return root;
  }

  function downloadBase64(contentBase64, filename, contentType) {
    if (!contentBase64 || typeof atob !== 'function') return;
    const binary = atob(contentBase64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const url = URL.createObjectURL(new Blob([bytes], { type: contentType || 'application/octet-stream' }));
    const link = el('a');
    link.href = url;
    link.download = filename || 'study-notes';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function renderExporter(ctx, token) {
    const root = panel(ctx, 'note-exporter', t(ctx, 'ui.status.ready', 'Ready'));
    let fmt = 'markdown';
    let style = 'neko';
    let markdown = '';
    let status = '';

    async function exportNotes(previewOnly) {
      status = t(ctx, 'ui.status.exporting', 'Exporting...');
      draw();
      try {
        const payload = await ctx.callPlugin('study_export_notes', { fmt, style, preview_only: previewOnly });
        markdown = payload.markdown || '';
        if (!previewOnly) downloadBase64(payload.content_base64, payload.filename, payload.content_type);
        status = payload.filename || t(ctx, 'ui.status.export_ready', 'Export ready');
      } catch (error) {
        status = errText(error);
      }
      draw();
    }

    function draw() {
      if (!valid(root, token, true)) return;
      const fmtSelect = select(fmt, [['markdown', formatLabel(ctx, 'markdown')], ['pdf', formatLabel(ctx, 'pdf')], ['docx', formatLabel(ctx, 'docx')], ['xmind', formatLabel(ctx, 'xmind')]]);
      fmtSelect.addEventListener('change', () => { fmt = fmtSelect.value; draw(); });
      const styleSelect = select(style, [['neko', exportStyleLabel(ctx, 'neko')], ['academic', exportStyleLabel(ctx, 'academic')], ['compact', exportStyleLabel(ctx, 'compact')]]);
      styleSelect.addEventListener('change', () => { style = styleSelect.value; draw(); });
      const previewButton = button(t(ctx, 'ui.button.preview', 'Preview'), () => exportNotes(true));
      previewButton.dataset.surfaceAction = 'export-preview';
      const exportButton = button(t(ctx, 'ui.button.export', 'Export'), () => exportNotes(false), true);
      exportButton.dataset.surfaceAction = 'export-download';
      replace(root, ctx, 'note-exporter', status || t(ctx, 'ui.feature.export.body', 'Export notes or session artifacts'), [
        state([
          [t(ctx, 'ui.label.format', 'Format'), formatLabel(ctx, fmt)],
          [t(ctx, 'ui.label.style', 'Style'), exportStyleLabel(ctx, style)],
          [t(ctx, 'ui.label.reply', 'Reply'), status || t(ctx, 'ui.status.pending', 'Pending')],
        ]),
        actions([
          labeled(t(ctx, 'ui.label.format', 'Format'), fmtSelect),
          labeled(t(ctx, 'ui.label.style', 'Style'), styleSelect),
          previewButton,
          exportButton,
        ], 'study-panel__actions--form'),
        pre(markdown),
      ]);
    }

    draw();
    return root;
  }

  function renderSessionSummary(ctx, token) {
    const root = panel(ctx, 'session-summary', t(ctx, 'ui.status.loading', 'Loading...'));
    ctx.callPlugin('study_session_summary')
      .then((summary) => {
        if (!valid(root, token)) return;
        const completed = safeList(summary, 'completed_goals');
        const incomplete = safeList(summary, 'incomplete_goals');
        const memory = summary.memory_summary?.available ? summary.memory_summary : null;
        const memoryRate = Number(memory?.correct_rate);
        const children = [
          state([
            [t(ctx, 'ui.label.focus_minutes', 'Focus'), summary.total_focus_minutes || 0],
            [t(ctx, 'ui.label.completed', 'Completed'), completed.length],
            [t(ctx, 'ui.label.incomplete', 'Open'), incomplete.length],
          ]),
        ];
        if (memory) {
          children.push(state([
            [t(ctx, 'ui.summary.memory_block_title', 'Memory'), memory.deck_count || 0],
            [t(ctx, 'ui.summary.memory_reviewed', 'Reviewed'), memory.reviewed_items || 0],
            [t(ctx, 'ui.summary.memory_correct_rate', 'Correct'), Number.isFinite(memoryRate) ? `${Math.round(memoryRate * 100)}%` : '0%'],
          ]));
        }
        children.push(pre([...completed, ...incomplete].map((goal) => `${goal.subject || targetTypeLabel(ctx, goal.target_type)}: ${goal.progress_amount}/${goal.target_amount} ${unitLabel(ctx, goal.unit)}`).join('\n')));
        replace(root, ctx, 'session-summary', summary.date || '', children);
      })
      .catch((error) => replace(root, ctx, 'session-summary', '', [pre(errText(error))]));
    return root;
  }

  function render(surfaceId, ctx) {
    panelToken += 1;
    const token = panelToken;
    if (surfaceId === 'due-review-panel') return renderDueReview(ctx, token);
    if (surfaceId === 'pomodoro-panel') return renderPomodoro(ctx, token);
    if (surfaceId === 'habit-dashboard') return renderHabit(ctx, token);
    if (surfaceId === 'daily-goal-editor') return renderDailyGoals(ctx, token);
    if (surfaceId === 'memory-deck-list') return renderDeckList(ctx, token);
    if (surfaceId === 'memory-importer') return renderMemoryImporter(ctx, token);
    if (surfaceId === 'note-exporter') return renderExporter(ctx, token);
    if (surfaceId === 'session-summary') return renderSessionSummary(ctx, token);
    return null;
  }

  window.StudyCompanionSurfacePanels = {
    render,
    close() {
      panelToken += 1;
    },
  };
}());

(function () {
  let panelToken = 0;
  const selectedNoteIds = new Set();

  function t(ctx, key, fallback) {
    return ctx.t ? ctx.t(key, fallback) : fallback;
  }

  function tf(ctx, key, fallback, values) {
    if (ctx.tf) return ctx.tf(key, fallback, values);
    return fallback.replace(/\{([^}]+)\}/g, (_, name) => values[name] ?? '');
  }

  function el(tag, className = '', text = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== '') node.textContent = text;
    return node;
  }

  function errorText(error) {
    return error instanceof Error ? error.message : String(error || 'Unknown error');
  }

  function listFromCsv(value) {
    return String(value || '')
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function csvFromList(value) {
    return Array.isArray(value) ? value.join(', ') : '';
  }

  function formatDate(value) {
    const date = new Date(String(value || ''));
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  function render(surfaceId, ctx) {
    if (surfaceId !== 'notebook-panel') return null;

    panelToken += 1;
    const token = panelToken;
    let notebooks = [];
    let notes = [];
    let selectedNotebook = 'all';
    let selectedNote = null;
    let query = '';
    let searchTimer = 0;
    let notesRequest = 0;
    let noteDetailRequest = 0;
    let busyCount = 0;

    const root = el('div', 'study-panel surface-shell notebook-panel');
    root.dataset.surface = 'notebook-panel';

    const header = el('header', 'study-panel__header');
    const titleWrap = el('div', 'study-panel__title');
    titleWrap.appendChild(el('h1', '', ctx.label ? ctx.label(surfaceId) : t(ctx, 'ui.feature.notebook.title', 'Notebook')));
    const statusChip = el('div', 'study-panel__status-chip', t(ctx, 'ui.status.loading', 'Loading...'));
    header.append(titleWrap, statusChip);

    const toolbar = el('section', 'notebook-toolbar');
    const filterLabel = el('label', 'notebook-field');
    filterLabel.appendChild(el('span', '', t(ctx, 'ui.notebook.filter', 'Notebook')));
    const notebookSelect = el('select');
    filterLabel.appendChild(notebookSelect);

    const searchLabel = el('label', 'notebook-field notebook-field--search');
    searchLabel.appendChild(el('span', '', t(ctx, 'ui.button.search', 'Search')));
    const searchInput = el('input');
    searchInput.type = 'search';
    searchInput.placeholder = t(ctx, 'ui.notebook.search_placeholder', 'Search notes');
    searchLabel.appendChild(searchInput);

    const newNotebookLabel = el('label', 'notebook-field');
    newNotebookLabel.appendChild(el('span', '', t(ctx, 'ui.notebook.new_notebook', 'New notebook')));
    const newNotebookInput = el('input');
    newNotebookInput.maxLength = 120;
    newNotebookInput.placeholder = t(ctx, 'ui.notebook.notebook_name_placeholder', 'Notebook name');
    newNotebookLabel.appendChild(newNotebookInput);

    const toolbarActions = el('div', 'notebook-toolbar__actions');
    const createNotebookButton = commandButton(t(ctx, 'ui.notebook.create_notebook', 'Create notebook'), createNotebook);
    const renameNotebookButton = commandButton(t(ctx, 'ui.notebook.rename_notebook', 'Rename'), renameNotebook);
    const deleteNotebookButton = commandButton(t(ctx, 'ui.notebook.delete_notebook', 'Delete notebook'), deleteNotebook);
    const newNoteButton = commandButton(t(ctx, 'ui.notebook.new_note', 'New note'), createNote, true);
    const refreshButton = commandButton(t(ctx, 'ui.button.refresh', 'Refresh'), refresh);
    toolbarActions.append(createNotebookButton, renameNotebookButton, deleteNotebookButton, newNoteButton, refreshButton);
    toolbar.append(filterLabel, searchLabel, newNotebookLabel, toolbarActions);

    const selectionBar = el('section', 'notebook-selection');
    const selectionCount = el('strong');
    const selectionActions = el('div', 'notebook-selection__actions');
    const clearSelectionButton = commandButton(t(ctx, 'ui.notebook.clear_selection', 'Clear selection'), clearSelection);
    const exportSelectionButton = commandButton(t(ctx, 'ui.notebook.export_selected', 'Export selected'), openExport, true);
    selectionActions.append(clearSelectionButton, exportSelectionButton);
    selectionBar.append(selectionCount, selectionActions);

    const workspace = el('div', 'notebook-workspace');
    const list = el('section', 'notebook-list');
    list.setAttribute('aria-label', t(ctx, 'ui.notebook.note_list', 'Notes'));
    const editor = el('section', 'notebook-editor');
    editor.setAttribute('aria-label', t(ctx, 'ui.notebook.editor', 'Note editor'));
    workspace.append(list, editor);
    root.append(header, toolbar, selectionBar, workspace);

    function isValid() {
      return token === panelToken;
    }

    function setStatus(value) {
      if (isValid()) statusChip.textContent = String(value || t(ctx, 'ui.status.ready', 'Ready'));
    }

    function setBusy(active) {
      busyCount = Math.max(0, busyCount + (active ? 1 : -1));
      root.dataset.busy = busyCount > 0 ? 'true' : 'false';
    }

    function commandButton(label, handler, primary = false) {
      const button = el('button', primary ? 'button button-primary' : 'button button-secondary', label);
      button.type = 'button';
      button.addEventListener('click', async () => {
        if (button.disabled) return;
        button.disabled = true;
        setBusy(true);
        try {
          await handler();
        } catch (error) {
          setStatus(errorText(error));
        } finally {
          setBusy(false);
          if (isValid()) {
            button.disabled = false;
            updateNotebookActions();
            updateSelectionBar();
          }
        }
      });
      return button;
    }

    function currentNotebookId() {
      return selectedNotebook !== 'all' && selectedNotebook !== 'unfiled' ? selectedNotebook : '';
    }

    function noteListArgs() {
      if (selectedNotebook === 'unfiled') {
        return { notebook_filter: 'unfiled', search_query: query, limit: 200 };
      }
      if (selectedNotebook === 'all') {
        return { notebook_filter: 'all', search_query: query, limit: 200 };
      }
      return {
        notebook_id: selectedNotebook,
        notebook_filter: 'specific',
        search_query: query,
        limit: 200,
      };
    }

    function drawNotebookOptions() {
      const previous = selectedNotebook;
      notebookSelect.replaceChildren();
      const options = [
        ['all', t(ctx, 'ui.notebook.all_notes', 'All notes')],
        ['unfiled', t(ctx, 'ui.notebook.unfiled', 'Unfiled')],
        ...notebooks.map((notebook) => [
          String(notebook.id || ''),
          tf(ctx, 'ui.notebook.name_with_count', '{name} ({count})', {
            name: notebook.name || t(ctx, 'ui.notebook.untitled', 'Untitled'),
            count: Number(notebook.note_count || 0),
          }),
        ]),
      ];
      options.forEach(([value, label]) => {
        const option = el('option', '', label);
        option.value = value;
        notebookSelect.appendChild(option);
      });
      selectedNotebook = options.some(([value]) => value === previous) ? previous : 'all';
      notebookSelect.value = selectedNotebook;
      updateNotebookActions();
    }

    function updateNotebookActions() {
      const hasNotebook = Boolean(currentNotebookId());
      renameNotebookButton.disabled = !hasNotebook;
      deleteNotebookButton.disabled = !hasNotebook;
    }

    function updateSelectionBar() {
      selectionCount.textContent = tf(ctx, 'ui.notebook.selected_count', '{count} notes selected', {
        count: selectedNoteIds.size,
      });
      clearSelectionButton.disabled = selectedNoteIds.size === 0;
      exportSelectionButton.disabled = selectedNoteIds.size === 0;
    }

    function drawList() {
      list.replaceChildren();
      if (!notes.length) {
        list.appendChild(el('p', 'study-panel__empty', t(ctx, 'ui.notebook.no_notes', 'No notes found')));
        return;
      }
      notes.forEach((note) => {
        const row = el('article', 'notebook-note-row');
        row.dataset.selected = selectedNote?.id === note.id ? 'true' : 'false';
        const check = el('input', 'notebook-note-row__check');
        check.type = 'checkbox';
        check.checked = selectedNoteIds.has(note.id);
        check.setAttribute('aria-label', tf(ctx, 'ui.notebook.select_note', 'Select {title}', {
          title: note.title || t(ctx, 'ui.notebook.untitled', 'Untitled'),
        }));
        check.addEventListener('change', () => {
          if (check.checked) selectedNoteIds.add(note.id);
          else selectedNoteIds.delete(note.id);
          updateSelectionBar();
        });
        const openButton = el('button', 'notebook-note-row__open');
        openButton.type = 'button';
        openButton.append(
          el('strong', '', note.title || t(ctx, 'ui.notebook.untitled', 'Untitled')),
          el('span', '', note.snippet || t(ctx, 'ui.notebook.empty_note', 'Empty note')),
          el('time', '', formatDate(note.updated_at)),
        );
        openButton.addEventListener('click', () => selectNote(note.id));
        row.append(check, openButton);
        list.appendChild(row);
      });
    }

    function editorField(labelText, inputNode, wide = false) {
      const label = el('label', wide ? 'notebook-editor__field notebook-editor__field--wide' : 'notebook-editor__field');
      label.append(el('span', '', labelText), inputNode);
      return label;
    }

    function drawEditor() {
      editor.replaceChildren();
      if (!selectedNote) {
        editor.appendChild(el('p', 'study-panel__empty', t(ctx, 'ui.notebook.select_to_edit', 'Select a note to edit')));
        return;
      }

      const titleInput = el('input');
      titleInput.value = selectedNote.title || '';
      titleInput.maxLength = 160;
      const notebookInput = el('select');
      const unfiledOption = el('option', '', t(ctx, 'ui.notebook.unfiled', 'Unfiled'));
      unfiledOption.value = '';
      notebookInput.appendChild(unfiledOption);
      notebooks.forEach((notebook) => {
        const option = el('option', '', notebook.name || t(ctx, 'ui.notebook.untitled', 'Untitled'));
        option.value = notebook.id;
        notebookInput.appendChild(option);
      });
      notebookInput.value = selectedNote.notebook_id || '';
      const topicsInput = el('input');
      topicsInput.value = csvFromList(selectedNote.topic_ids);
      const tagsInput = el('input');
      tagsInput.value = csvFromList(selectedNote.tags);
      const contentInput = el('textarea', 'notebook-editor__content');
      contentInput.value = selectedNote.content || '';
      contentInput.spellcheck = true;

      const fields = el('div', 'notebook-editor__fields');
      fields.append(
        editorField(t(ctx, 'ui.label.title', 'Title'), titleInput, true),
        editorField(t(ctx, 'ui.notebook.filter', 'Notebook'), notebookInput),
        editorField(t(ctx, 'ui.notebook.topics', 'Topics'), topicsInput),
        editorField(t(ctx, 'ui.notebook.tags', 'Tags'), tagsInput),
        editorField(t(ctx, 'ui.notebook.content', 'Content'), contentInput, true),
      );

      const actions = el('div', 'study-panel__actions notebook-editor__actions');
      const saveButton = commandButton(t(ctx, 'ui.button.save', 'Save'), async () => {
        const noteId = selectedNote.id;
        const payload = await ctx.callPlugin('study_note_upsert', {
          note_id: noteId,
          notebook_id: notebookInput.value,
          title: titleInput.value,
          content: contentInput.value,
          topic_ids: listFromCsv(topicsInput.value),
          tags: listFromCsv(tagsInput.value),
        });
        if (!isValid() || selectedNote?.id !== noteId) return;
        selectedNote = payload.note || selectedNote;
        await refresh();
        setStatus(t(ctx, 'ui.notebook.saved', 'Saved'));
      }, true);
      const expandButton = commandButton(t(ctx, 'ui.notebook.ai_expand', 'AI expand'), async () => {
        const payload = await ctx.callPlugin('study_note_ai_expand', {
          note_id: selectedNote.id,
          content: contentInput.value,
          topic_context: topicsInput.value,
        });
        if (!isValid()) return;
        if (payload.content) contentInput.value = payload.content;
        setStatus(t(ctx, 'ui.status.reply_ready', 'Reply ready'));
      });
      const deleteButton = commandButton(t(ctx, 'ui.button.delete', 'Delete'), async () => {
        const confirmed = window.confirm(t(ctx, 'ui.notebook.delete_note_confirm', 'Delete this note?'));
        if (!confirmed) return;
        const noteId = selectedNote.id;
        await ctx.callPlugin('study_note_delete', { note_id: noteId });
        selectedNoteIds.delete(noteId);
        selectedNote = null;
        await refresh();
      });
      actions.append(deleteButton, expandButton, saveButton);
      editor.append(fields, actions);
    }

    async function loadNotebooks() {
      const payload = await ctx.callPlugin('study_notebook_list', { limit: 100 });
      if (!isValid()) return;
      notebooks = Array.isArray(payload.notebooks) ? payload.notebooks : [];
      drawNotebookOptions();
    }

    async function loadNotes() {
      noteDetailRequest += 1;
      const requestId = notesRequest += 1;
      const payload = await ctx.callPlugin('study_note_list', noteListArgs());
      if (!isValid() || requestId !== notesRequest) return;
      notes = Array.isArray(payload.notes) ? payload.notes : [];
      if (selectedNote) {
        selectedNote = notes.find((note) => note.id === selectedNote.id) || null;
      }
      drawList();
      drawEditor();
      updateSelectionBar();
      setStatus(tf(ctx, 'ui.notebook.note_count', '{count} notes', { count: notes.length }));
    }

    async function refresh() {
      await loadNotebooks();
      await loadNotes();
    }

    async function selectNote(noteId) {
      const requestId = noteDetailRequest += 1;
      const payload = await ctx.callPlugin('study_note_get', { note_id: noteId });
      if (!isValid() || requestId !== noteDetailRequest) return;
      selectedNote = payload.note || null;
      drawList();
      drawEditor();
    }

    async function createNotebook() {
      const name = newNotebookInput.value.trim();
      if (!name) {
        setStatus(t(ctx, 'ui.notebook.name_required', 'Notebook name is required'));
        return;
      }
      const payload = await ctx.callPlugin('study_notebook_create', { name });
      newNotebookInput.value = '';
      await loadNotebooks();
      if (payload.notebook?.id) {
        selectedNotebook = payload.notebook.id;
        notebookSelect.value = selectedNotebook;
        updateNotebookActions();
      }
      await loadNotes();
    }

    async function renameNotebook() {
      const notebookId = currentNotebookId();
      const notebook = notebooks.find((item) => item.id === notebookId);
      if (!notebook) return;
      const name = window.prompt(t(ctx, 'ui.notebook.rename_prompt', 'Notebook name'), notebook.name || '');
      if (name === null || !name.trim()) return;
      await ctx.callPlugin('study_notebook_update', { notebook_id: notebookId, name: name.trim() });
      await loadNotebooks();
      setStatus(t(ctx, 'ui.notebook.renamed', 'Notebook renamed'));
    }

    async function deleteNotebook() {
      const notebookId = currentNotebookId();
      if (!notebookId) return;
      if (!window.confirm(t(ctx, 'ui.notebook.delete_notebook_confirm', 'Delete this notebook? Its notes will become unfiled.'))) return;
      await ctx.callPlugin('study_notebook_delete', { notebook_id: notebookId });
      selectedNotebook = 'all';
      selectedNote = null;
      await refresh();
    }

    async function createNote() {
      const payload = await ctx.callPlugin('study_note_upsert', {
        notebook_id: currentNotebookId(),
        title: t(ctx, 'ui.notebook.new_note', 'New note'),
        content: '',
      });
      if (!isValid()) return;
      selectedNote = payload.note || null;
      await refresh();
      if (payload.note?.id) await selectNote(payload.note.id);
      setStatus(t(ctx, 'ui.notebook.saved', 'Saved'));
    }

    function clearSelection() {
      selectedNoteIds.clear();
      drawList();
      updateSelectionBar();
    }

    function openExport() {
      if (selectedNoteIds.size && typeof ctx.openSurface === 'function') {
        ctx.openSurface('note-exporter');
      }
    }

    notebookSelect.addEventListener('change', () => {
      selectedNotebook = notebookSelect.value;
      selectedNote = null;
      updateNotebookActions();
      loadNotes().catch((error) => setStatus(errorText(error)));
    });
    searchInput.addEventListener('input', () => {
      query = searchInput.value.trim();
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        loadNotes().catch((error) => setStatus(errorText(error)));
      }, 250);
    });
    newNotebookInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') createNotebookButton.click();
    });

    updateSelectionBar();
    refresh().catch((error) => setStatus(errorText(error)));
    return root;
  }

  window.StudyCompanionNotebook = {
    render,
    getSelectedNoteIds() {
      return Array.from(selectedNoteIds);
    },
    clearSelectedNoteIds() {
      selectedNoteIds.clear();
    },
    close() {
      panelToken += 1;
    },
  };
}());

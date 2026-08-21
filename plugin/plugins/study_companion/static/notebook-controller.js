(function () {
  const NOTEBOOK_PAGE_SIZE = 100;
  const NOTE_PAGE_SIZE = 200;
  let panelToken = 0;
  let activeBeforeClose = null;
  let exportSelectionIntent = false;
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
      .split(/[,，]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function csvFromList(value) {
    return Array.isArray(value) ? value.join(', ') : '';
  }

  function formatDate(value) {
    const raw = String(value || '').trim();
    const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
      ? `${raw.replace(' ', 'T')}Z`
      : raw;
    const date = new Date(normalized);
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
    let notesHasMore = false;
    let notesNextOffset = 0;
    let notesScope = '';
    let searchTimer = 0;
    let notebooksRequest = 0;
    let notesRequest = 0;
    let noteDetailRequest = 0;
    let noteNavigationRequest = 0;
    let busyCount = 0;
    let mutationBusyCount = 0;
    let noteDetailBusyCount = 0;
    let editorDirty = false;
    let currentEditor = null;

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
    const createNotebookButton = commandButton(t(ctx, 'ui.notebook.create_notebook', 'Create notebook'), createNotebook, false, true);
    const renameNotebookButton = commandButton(t(ctx, 'ui.notebook.rename_notebook', 'Rename'), renameNotebook, false, true);
    const deleteNotebookButton = commandButton(t(ctx, 'ui.notebook.delete_notebook', 'Delete notebook'), deleteNotebook, false, true);
    const newNoteButton = commandButton(t(ctx, 'ui.notebook.new_note', 'New note'), createNote, true, true);
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

    function preventDirtyUnload(event) {
      event.preventDefault();
      event.returnValue = true;
    }

    function setEditorDirty(dirty) {
      const nextDirty = Boolean(dirty);
      if (editorDirty === nextDirty) return;
      editorDirty = nextDirty;
      if (editorDirty) window.addEventListener('beforeunload', preventDirtyUnload);
      else window.removeEventListener('beforeunload', preventDirtyUnload);
    }

    function setEditorLocked(locked) {
      if (currentEditor) {
        currentEditor.inputs.forEach((input) => {
          input.disabled = locked;
        });
      }
      editor.querySelectorAll('.notebook-editor__actions button').forEach((button) => {
        button.disabled = locked;
      });
    }

    function updateListLocks() {
      const mutationsLocked = mutationBusyCount > 0;
      const noteOpeningLocked = mutationsLocked || noteDetailBusyCount > 0;
      notebookSelect.disabled = mutationsLocked;
      searchInput.disabled = mutationsLocked;
      list.querySelectorAll('.notebook-note-row__open').forEach((button) => {
        button.disabled = noteOpeningLocked;
      });
    }

    function setBusy(active, mutation = false) {
      busyCount = Math.max(0, busyCount + (active ? 1 : -1));
      if (mutation) mutationBusyCount = Math.max(0, mutationBusyCount + (active ? 1 : -1));
      root.dataset.busy = busyCount > 0 ? 'true' : 'false';
      setEditorLocked(busyCount > 0);
      updateListLocks();
      updateNotebookActions();
      updateSelectionBar();
      if (busyCount === 0) {
        root.querySelectorAll('button').forEach((button) => {
          button.disabled = false;
        });
        updateListLocks();
        updateNotebookActions();
        updateSelectionBar();
      }
    }

    function commandButton(label, handler, primary = false, mutation = false) {
      const button = el('button', primary ? 'button button-primary' : 'button button-secondary', label);
      button.type = 'button';
      button.disabled = busyCount > 0;
      button.addEventListener('click', async () => {
        if (button.disabled) return;
        button.disabled = true;
        setBusy(true, mutation);
        try {
          await handler();
        } catch (error) {
          setStatus(errorText(error));
        } finally {
          setBusy(false, mutation);
          if (isValid()) {
            button.disabled = busyCount > 0;
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

    function noteListArgs(offset = 0, limit = NOTE_PAGE_SIZE) {
      if (selectedNotebook === 'unfiled') {
        return { notebook_filter: 'unfiled', search_query: query, limit, offset };
      }
      if (selectedNotebook === 'all') {
        return { notebook_filter: 'all', search_query: query, limit, offset };
      }
      return {
        notebook_id: selectedNotebook,
        notebook_filter: 'specific',
        search_query: query,
        limit,
        offset,
      };
    }

    function noteScopeKey() {
      return `${selectedNotebook}\u0000${query}`;
    }

    function mergeNoteSummary(current, summary, preserveDraft = false) {
      const draftFields = preserveDraft
        ? {
          notebook_id: current.notebook_id,
          title: current.title,
          content: current.content,
          content_plain: current.content_plain,
          topic_ids: current.topic_ids,
          tags: current.tags,
        }
        : {};
      const merged = { ...current, ...summary, ...draftFields };
      if (!summary.content && current.content) merged.content = current.content;
      if (!summary.content_plain && current.content_plain) merged.content_plain = current.content_plain;
      return merged;
    }

    function selectedNoteSnapshot() {
      if (!selectedNote) return null;
      return {
        ...selectedNote,
        topic_ids: Array.isArray(selectedNote.topic_ids) ? [...selectedNote.topic_ids] : [],
        tags: Array.isArray(selectedNote.tags) ? [...selectedNote.tags] : [],
      };
    }

    function restoreSelectedDraft(snapshot, dirty) {
      if (snapshot) {
        selectedNote = snapshot;
        drawList();
        drawEditor();
      }
      setEditorDirty(dirty);
    }

    function captureEditorDraft() {
      if (!selectedNote || !currentEditor || currentEditor.noteId !== selectedNote.id) return;
      selectedNote = {
        ...selectedNote,
        notebook_id: currentEditor.notebookInput.value,
        title: currentEditor.titleInput.value,
        content: currentEditor.contentInput.value,
        content_plain: currentEditor.contentInput.value,
        topic_ids: listFromCsv(currentEditor.topicsInput.value),
        tags: listFromCsv(currentEditor.tagsInput.value),
      };
    }

    function confirmDiscardDraft() {
      captureEditorDraft();
      if (!editorDirty) return true;
      return window.confirm(t(ctx, 'ui.notebook.discard_draft_confirm', 'Discard unsaved changes?'));
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
      const mutationsLocked = mutationBusyCount > 0;
      createNotebookButton.disabled = mutationsLocked;
      renameNotebookButton.disabled = mutationsLocked || !hasNotebook;
      deleteNotebookButton.disabled = mutationsLocked || !hasNotebook;
      newNoteButton.disabled = mutationsLocked;
    }

    function updateSelectionBar() {
      selectionCount.textContent = tf(ctx, 'ui.notebook.selected_count', '{count} notes selected', {
        count: selectedNoteIds.size,
      });
      clearSelectionButton.disabled = selectedNoteIds.size === 0;
      exportSelectionButton.disabled = mutationBusyCount > 0 || selectedNoteIds.size === 0;
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
        openButton.disabled = mutationBusyCount > 0 || noteDetailBusyCount > 0;
        openButton.append(
          el('strong', '', note.title || t(ctx, 'ui.notebook.untitled', 'Untitled')),
          el('span', '', note.snippet || t(ctx, 'ui.notebook.empty_note', 'Empty note')),
          el('time', '', formatDate(note.updated_at)),
        );
        openButton.addEventListener('click', () => {
          selectNote(note.id).catch((error) => setStatus(errorText(error)));
        });
        row.append(check, openButton);
        list.appendChild(row);
      });
      if (notesHasMore) {
        const loadMore = commandButton(
          t(ctx, 'ui.notebook.load_more', 'Load more notes'),
          () => loadNotes(true),
        );
        loadMore.classList.add('notebook-list__load-more');
        list.appendChild(loadMore);
      }
    }

    function editorField(labelText, inputNode, wide = false) {
      const label = el('label', wide ? 'notebook-editor__field notebook-editor__field--wide' : 'notebook-editor__field');
      label.append(el('span', '', labelText), inputNode);
      return label;
    }

    function drawEditor() {
      currentEditor = null;
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
      const notebookOptionIds = new Set(['']);
      notebooks.forEach((notebook) => {
        const option = el('option', '', notebook.name || t(ctx, 'ui.notebook.untitled', 'Untitled'));
        option.value = notebook.id;
        notebookOptionIds.add(option.value);
        notebookInput.appendChild(option);
      });
      const selectedNotebookId = selectedNote.notebook_id || '';
      if (selectedNotebookId && !notebookOptionIds.has(selectedNotebookId)) {
        const currentNotebookOption = el('option', '', selectedNotebookId);
        currentNotebookOption.value = selectedNotebookId;
        notebookInput.appendChild(currentNotebookOption);
      }
      notebookInput.value = selectedNotebookId;
      const topicsInput = el('input');
      topicsInput.value = csvFromList(selectedNote.topic_ids);
      const tagsInput = el('input');
      tagsInput.value = csvFromList(selectedNote.tags);
      const contentInput = el('textarea', 'notebook-editor__content');
      contentInput.value = selectedNote.content || '';
      contentInput.spellcheck = true;
      currentEditor = {
        noteId: selectedNote.id,
        titleInput,
        notebookInput,
        topicsInput,
        tagsInput,
        contentInput,
        inputs: [titleInput, notebookInput, topicsInput, tagsInput, contentInput],
      };
      [titleInput, notebookInput, topicsInput, tagsInput, contentInput].forEach((input) => {
        input.addEventListener('input', () => {
          setEditorDirty(true);
          captureEditorDraft();
        });
        input.addEventListener('change', () => {
          setEditorDirty(true);
          captureEditorDraft();
        });
      });

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
        captureEditorDraft();
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
        setEditorDirty(false);
        await refresh();
        setStatus(t(ctx, 'ui.notebook.saved', 'Saved'));
      }, true, true);
      const expandButton = commandButton(t(ctx, 'ui.notebook.ai_expand', 'AI expand'), async () => {
        const noteId = selectedNote.id;
        const contentSnapshot = contentInput.value;
        const navigationRequestSnapshot = noteNavigationRequest;
        const payload = await ctx.callPlugin('study_note_ai_expand', {
          note_id: noteId,
          content: contentSnapshot,
          topic_context: topicsInput.value,
        });
        if (!isValid() || selectedNote?.id !== noteId || navigationRequestSnapshot !== noteNavigationRequest) return;
        if (payload.content) {
          const currentContentInput = contentInput.isConnected
            ? contentInput
            : editor.querySelector('.notebook-editor__content');
          if (!currentContentInput || currentContentInput.value !== contentSnapshot) return;
          if (currentContentInput) currentContentInput.value = payload.content;
          selectedNote = { ...selectedNote, content: payload.content, content_plain: payload.content };
          setEditorDirty(true);
        }
        setStatus(t(ctx, 'ui.status.reply_ready', 'Reply ready'));
      }, false, true);
      const deleteButton = commandButton(t(ctx, 'ui.button.delete', 'Delete'), async () => {
        const restoreDirtyDraft = editorDirty;
        if (restoreDirtyDraft && !confirmDiscardDraft()) return;
        const draftSnapshot = restoreDirtyDraft ? selectedNoteSnapshot() : null;
        const confirmed = window.confirm(t(ctx, 'ui.notebook.delete_note_confirm', 'Delete this note?'));
        if (!confirmed) return;
        setEditorDirty(false);
        const noteId = selectedNote.id;
        try {
          await ctx.callPlugin('study_note_delete', { note_id: noteId });
        } catch (error) {
          if (isValid()) restoreSelectedDraft(draftSnapshot, restoreDirtyDraft);
          throw error;
        }
        selectedNoteIds.delete(noteId);
        notesRequest += 1;
        notes = notes.filter((note) => note.id !== noteId);
        selectedNote = null;
        drawList();
        drawEditor();
        updateSelectionBar();
        await refresh();
      }, false, true);
      actions.append(deleteButton, expandButton, saveButton);
      editor.append(fields, actions);
      setEditorLocked(busyCount > 0);
    }

    async function loadNotebooks() {
      const requestId = notebooksRequest += 1;
      const loadedNotebooks = [];
      const loadedIds = new Set();
      let offset = 0;
      while (true) {
        let payload;
        try {
          payload = await ctx.callPlugin('study_notebook_list', {
            limit: NOTEBOOK_PAGE_SIZE,
            offset,
          });
        } catch (error) {
          if (!isValid() || requestId !== notebooksRequest) return false;
          throw error;
        }
        if (!isValid() || requestId !== notebooksRequest) return false;
        const pageNotebooks = Array.isArray(payload.notebooks) ? payload.notebooks : [];
        pageNotebooks.forEach((notebook) => {
          if (loadedIds.has(notebook.id)) return;
          loadedIds.add(notebook.id);
          loadedNotebooks.push(notebook);
        });
        if (payload.has_more !== true) break;
        const nextOffset = Number(payload.next_offset);
        if (!Number.isSafeInteger(nextOffset) || nextOffset <= offset) {
          throw new Error('Invalid notebook continuation offset');
        }
        offset = nextOffset;
      }
      notebooks = loadedNotebooks;
      drawNotebookOptions();
      return true;
    }

    async function loadNotes(append = false) {
      const scope = noteScopeKey();
      const resetPage = scope !== notesScope;
      const offset = append && !resetPage ? notesNextOffset : 0;
      const limit = append || resetPage ? NOTE_PAGE_SIZE : Math.max(NOTE_PAGE_SIZE, notes.length);
      if (!append) captureEditorDraft();
      const detailRequestAtStart = append ? noteDetailRequest : noteDetailRequest += 1;
      const requestId = notesRequest += 1;
      let payload;
      try {
        payload = await ctx.callPlugin('study_note_list', noteListArgs(offset, limit));
      } catch (error) {
        if (!isValid() || requestId !== notesRequest) return;
        throw error;
      }
      if (!isValid() || requestId !== notesRequest) return;
      const pageNotes = Array.isArray(payload.notes) ? payload.notes : [];
      if (append && !resetPage) {
        const loadedIds = new Set(notes.map((note) => note.id));
        notes = [...notes, ...pageNotes.filter((note) => !loadedIds.has(note.id))];
      } else {
        notes = pageNotes;
      }
      notesScope = scope;
      notesHasMore = payload.has_more === true;
      const nextOffset = Number(payload.next_offset);
      notesNextOffset = Number.isSafeInteger(nextOffset) && nextOffset > offset
        ? nextOffset
        : offset + pageNotes.length;
      if (notesHasMore && notesNextOffset <= offset) notesHasMore = false;
      if (!append && selectedNote && detailRequestAtStart === noteDetailRequest) {
        const summary = notes.find((note) => note.id === selectedNote.id);
        if (summary) {
          selectedNote = mergeNoteSummary(selectedNote, summary, editorDirty);
        } else if (!editorDirty) {
          selectedNote = null;
        }
      }
      drawList();
      if (detailRequestAtStart === noteDetailRequest) drawEditor();
      updateSelectionBar();
      setStatus(tf(ctx, 'ui.notebook.note_count', '{count} notes', { count: notes.length }));
    }

    async function refresh() {
      if (!await loadNotebooks()) return;
      await loadNotes();
    }

    async function selectNote(noteId) {
      const restoreDirtyDraft = editorDirty;
      if (!confirmDiscardDraft()) return;
      const draftSnapshot = restoreDirtyDraft ? selectedNoteSnapshot() : null;
      const navigationScope = noteScopeKey();
      setEditorDirty(false);
      const navigationId = noteNavigationRequest += 1;
      const requestId = noteDetailRequest += 1;
      noteDetailBusyCount += 1;
      updateListLocks();
      setBusy(true);
      try {
        const payload = await ctx.callPlugin('study_note_get', { note_id: noteId });
        if (!isValid() || navigationScope !== noteScopeKey()) return;
        if (requestId !== noteDetailRequest) {
          restoreSelectedDraft(draftSnapshot, restoreDirtyDraft);
          return;
        }
        if (editorDirty && !confirmDiscardDraft()) return;
        setEditorDirty(false);
        selectedNote = payload.note || null;
        drawList();
        drawEditor();
      } catch (error) {
        if (
          isValid()
          && requestId === noteDetailRequest
          && navigationScope === noteScopeKey()
          && navigationId === noteNavigationRequest
        ) {
          restoreSelectedDraft(draftSnapshot, restoreDirtyDraft);
          setStatus(errorText(error));
        }
      } finally {
        setBusy(false);
        noteDetailBusyCount = Math.max(0, noteDetailBusyCount - 1);
        updateListLocks();
      }
    }

    function showCreatedNotebook(notebook) {
      if (!notebook?.id) return;
      notebooks = [notebook, ...notebooks.filter((item) => item.id !== notebook.id)];
      selectedNotebook = notebook.id;
      selectedNote = null;
      notes = [];
      notesHasMore = false;
      notesNextOffset = 0;
      notesScope = '';
      drawNotebookOptions();
      drawList();
      drawEditor();
    }

    async function createNotebook() {
      const name = newNotebookInput.value.trim();
      if (!name) {
        setStatus(t(ctx, 'ui.notebook.name_required', 'Notebook name is required'));
        return;
      }
      const restoreDirtyDraft = editorDirty;
      if (!confirmDiscardDraft()) return;
      const draftSnapshot = restoreDirtyDraft ? selectedNoteSnapshot() : null;
      setEditorDirty(false);
      let payload;
      try {
        payload = await ctx.callPlugin('study_notebook_create', { name });
      } catch (error) {
        if (isValid()) restoreSelectedDraft(draftSnapshot, restoreDirtyDraft);
        throw error;
      }
      if (!isValid()) return;
      if (newNotebookInput.value.trim() === name) {
        newNotebookInput.value = '';
      }
      const createdNotebook = payload.notebook || null;
      showCreatedNotebook(createdNotebook);
      try {
        await refresh();
      } catch (error) {
        if (!isValid()) return;
        showCreatedNotebook(createdNotebook);
        setStatus(`${t(ctx, 'ui.notebook.saved', 'Saved')}: ${errorText(error)}`);
      }
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
      const restoreDirtyDraft = editorDirty;
      if (!confirmDiscardDraft()) return;
      const draftSnapshot = restoreDirtyDraft ? selectedNoteSnapshot() : null;
      if (!window.confirm(t(ctx, 'ui.notebook.delete_notebook_confirm', 'Delete this notebook? Its notes will become unfiled.'))) return;
      setEditorDirty(false);
      try {
        await ctx.callPlugin('study_notebook_delete', { notebook_id: notebookId });
      } catch (error) {
        if (isValid()) restoreSelectedDraft(draftSnapshot, restoreDirtyDraft);
        throw error;
      }
      notebooks = notebooks.filter((notebook) => notebook.id !== notebookId);
      selectedNotebook = 'all';
      selectedNote = null;
      notesRequest += 1;
      notes = [];
      notesScope = '';
      notesHasMore = false;
      notesNextOffset = 0;
      drawNotebookOptions();
      drawList();
      drawEditor();
      await refresh();
    }

    async function createNote() {
      const restoreDirtyDraft = editorDirty;
      if (!confirmDiscardDraft()) return;
      setEditorDirty(false);
      let payload;
      try {
        payload = await ctx.callPlugin('study_note_upsert', {
          notebook_id: currentNotebookId(),
          title: t(ctx, 'ui.notebook.new_note', 'New note'),
          content: '',
        });
      } catch (error) {
        setEditorDirty(restoreDirtyDraft);
        throw error;
      }
      if (!isValid()) return;
      selectedNote = payload.note || selectedNote;
      drawList();
      drawEditor();
      try {
        await refresh();
        if (!isValid()) return;
        if (payload.note?.id) await selectNote(payload.note.id);
        setStatus(t(ctx, 'ui.notebook.saved', 'Saved'));
      } catch (error) {
        if (!isValid()) return;
        selectedNote = payload.note || selectedNote;
        drawList();
        drawEditor();
        setStatus(`${t(ctx, 'ui.notebook.saved', 'Saved')}: ${errorText(error)}`);
      }
    }

    function clearSelection() {
      selectedNoteIds.clear();
      drawList();
      updateSelectionBar();
    }

    function openExport() {
      if (selectedNoteIds.size && typeof ctx.openSurface === 'function') {
        exportSelectionIntent = true;
        ctx.openSurface('note-exporter');
      }
    }

    notebookSelect.addEventListener('change', () => {
      if (!confirmDiscardDraft()) {
        notebookSelect.value = selectedNotebook;
        return;
      }
      setEditorDirty(false);
      selectedNotebook = notebookSelect.value;
      selectedNote = null;
      drawList();
      drawEditor();
      updateNotebookActions();
      loadNotes().catch((error) => setStatus(errorText(error)));
    });
    searchInput.addEventListener('input', () => {
      query = searchInput.value.trim();
      notesRequest += 1;
      notesHasMore = false;
      drawList();
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        loadNotes().catch((error) => setStatus(errorText(error)));
      }, 250);
    });
    newNotebookInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') createNotebookButton.click();
    });

    updateSelectionBar();
    activeBeforeClose = () => {
      if (!confirmDiscardDraft()) return false;
      setEditorDirty(false);
      return true;
    };
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
      exportSelectionIntent = false;
    },
    restoreExportSelectionIntent() {
      exportSelectionIntent = selectedNoteIds.size > 0;
    },
    consumeExportSelectionIntent() {
      const shouldUseSelection = exportSelectionIntent;
      exportSelectionIntent = false;
      return shouldUseSelection;
    },
    close() {
      if (typeof activeBeforeClose === 'function' && !activeBeforeClose()) return false;
      activeBeforeClose = null;
      panelToken += 1;
      return true;
    },
  };
}());

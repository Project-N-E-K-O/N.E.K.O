(function initializeQuickCardController(global) {
  'use strict';

  const ITEM_TYPE_BY_DECK_TYPE = Object.freeze({
    word: 'word',
    passage: 'paragraph',
    formula: 'custom',
    custom: 'custom',
  });

  function createController(options = {}) {
    const t = options.t || ((key, fallback) => fallback || key);
    const getDecks = options.getDecks || (() => []);
    const createDeck = options.createDeck || (async () => null);
    const reportError = options.reportError || (() => {});
    const deckSelect = document.getElementById('memoryDeckSelect');
    const itemTypeSelect = document.getElementById('memoryItemTypeSelect');
    const openButton = document.getElementById('memoryCreateDeckBtn');
    const dialog = document.getElementById('memoryDeckDialog');
    const dialogLabel = document.getElementById('memoryDeckDialogLabel');
    const dialogTitle = document.getElementById('memoryDeckDialogTitle');
    const dialogBody = document.getElementById('memoryDeckDialogBody');
    const nameInput = document.getElementById('memoryDeckNameInput');
    const deckTypeSelect = document.getElementById('memoryDeckTypeSelect');
    const createButton = document.getElementById('memoryDeckCreateBtn');
    const skipButton = document.getElementById('memoryDeckSkipBtn');
    let dialogMode = '';
    let dialogResolve = null;
    let dialogPromise = null;
    let busy = false;
    let lastSelectedDeckId = '';
    let itemTypeOverridden = false;
    let decorating = false;

    function deckTypeLabel(value) {
      const normalized = String(value || 'custom');
      const fallbacks = { word: 'Word', passage: 'Passage', formula: 'Formula', custom: 'Custom' };
      return t(`ui.memory.deck_type.${normalized}`, fallbacks[normalized] || fallbacks.custom);
    }

    function setBusy(value) {
      busy = Boolean(value);
      [openButton, createButton, skipButton, nameInput, deckTypeSelect].forEach((element) => {
        if (element) element.disabled = busy;
      });
    }

    function applyDeckDefault(deckType, force = false) {
      if (!itemTypeSelect || (itemTypeOverridden && !force)) return;
      itemTypeSelect.value = ITEM_TYPE_BY_DECK_TYPE[String(deckType || 'custom')] || 'custom';
      itemTypeOverridden = false;
    }

    function selectedDeck() {
      const selectedId = String(deckSelect?.value || '');
      return getDecks().find((deck) => String(deck?.id || '') === selectedId) || null;
    }

    function decorateDeckOptions() {
      if (!deckSelect || decorating) return;
      decorating = true;
      try {
        const decks = getDecks();
        Array.from(deckSelect.options).forEach((option) => {
          const deck = decks.find((candidate) => String(candidate?.id || '') === option.value);
          if (!deck) return;
          const name = deck.is_default
            ? t('ui.memory.default_deck_name', 'Default Deck')
            : String(deck.name || '');
          const label = `${name} / ${deckTypeLabel(deck.deck_type)}`;
          if (option.textContent !== label) option.textContent = label;
        });
        const nextSelectedId = String(deckSelect.value || '');
        if (nextSelectedId && nextSelectedId !== lastSelectedDeckId && !itemTypeOverridden) {
          applyDeckDefault(selectedDeck()?.deck_type);
        }
        lastSelectedDeckId = nextSelectedId;
      } finally {
        decorating = false;
      }
    }

    function finishDialog(result) {
      const resolve = dialogResolve;
      dialogResolve = null;
      dialogPromise = null;
      dialogMode = '';
      if (dialog?.open) dialog.close();
      if (resolve) resolve(result);
    }

    function openDialog(mode) {
      if (busy || !dialog || typeof dialog.showModal !== 'function') {
        return Promise.resolve(null);
      }
      if (dialogPromise) return dialogPromise;
      dialogMode = mode;
      const standalone = mode === 'standalone';
      if (dialogLabel) dialogLabel.textContent = t(
        standalone ? 'ui.button.create_deck' : 'ui.memory.first_deck_label',
        standalone ? 'Create deck' : 'First card',
      );
      if (dialogTitle) dialogTitle.textContent = t(
        standalone ? 'ui.memory.create_deck_title' : 'ui.memory.first_deck_title',
        standalone ? 'Create a deck' : 'Name your first deck',
      );
      if (dialogBody) dialogBody.textContent = t(
        standalone ? 'ui.memory.create_deck_body' : 'ui.memory.first_deck_body',
        standalone
          ? 'Choose a name and type for the new deck.'
          : 'Create a deck for this card, or skip to use the default deck.',
      );
      if (createButton) createButton.textContent = t(
        standalone ? 'ui.button.create' : 'ui.button.create_and_save',
        standalone ? 'Create' : 'Create and save',
      );
      if (nameInput) {
        nameInput.value = '';
        nameInput.setCustomValidity('');
      }
      if (deckTypeSelect) deckTypeSelect.value = 'custom';
      if (skipButton) skipButton.hidden = mode !== 'first-card';
      dialogPromise = new Promise((resolve) => {
        dialogResolve = resolve;
        dialog.showModal();
        nameInput?.focus();
      });
      return dialogPromise;
    }

    async function submitDialog(event) {
      if (!dialogMode) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const name = nameInput?.value.trim() || '';
      if (!name) {
        nameInput?.setCustomValidity(t('ui.memory.error_missing_deck_name', 'Deck name is required'));
        nameInput?.reportValidity();
        return;
      }
      nameInput?.setCustomValidity('');
      const choice = { name, deckType: deckTypeSelect?.value || 'custom' };
      if (dialogMode === 'first-card') {
        finishDialog(choice);
        return;
      }
      setBusy(true);
      try {
        const created = await createDeck(choice);
        if (created) applyDeckDefault(created.deck_type || choice.deckType);
        finishDialog(created ? choice : null);
      } catch (error) {
        reportError(error);
      } finally {
        setBusy(false);
      }
    }

    createButton?.addEventListener('click', submitDialog, true);
    skipButton?.addEventListener('click', (event) => {
      if (dialogMode !== 'first-card') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      finishDialog({ skip: true });
    }, true);
    dialog?.addEventListener('cancel', (event) => {
      if (!dialogMode) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      finishDialog(null);
    }, true);
    openButton?.addEventListener('click', () => {
      if (busy) return;
      openDialog('standalone');
    });
    itemTypeSelect?.addEventListener('change', () => {
      itemTypeOverridden = true;
    });
    deckSelect?.addEventListener('change', () => {
      lastSelectedDeckId = String(deckSelect.value || '');
      applyDeckDefault(selectedDeck()?.deck_type);
    });

    if (deckSelect && typeof MutationObserver === 'function') {
      new MutationObserver(() => decorateDeckOptions()).observe(deckSelect, { childList: true });
    }

    return {
      requestFirstDeck: () => openDialog('first-card'),
      decorateDeckOptions,
      applyDeckDefault,
      getItemType: () => itemTypeSelect?.value || 'custom',
    };
  }

  global.StudyQuickCardController = Object.freeze({ create: createController });
})(window);

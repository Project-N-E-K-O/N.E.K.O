import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, listAllMemoryDecks, text } from './memory_shared';
import { deckTypeLabel, ensureBrandCSS, memoryItemTypeLabel, postStudySurfaceMessage, STUDY_SURFACE_MESSAGE_TYPES } from './study_surface_utils';
import {
  deckGoalSavedMessage,
  getMemoryHabitStatus,
  habitBridgeAvailable,
  normalizePositiveInteger,
  setDeckGoal,
  type MemoryHabitStatus,
} from './memory_habit_bridge';

type MemoryDeck = {
  id: string;
  name: string;
  deck_type: string;
  item_count?: number;
  is_default?: boolean;
};

type MemoryItem = {
  id: string;
  prompt?: string;
  answer?: string;
  item_type?: string;
};

type MemoryItemPage = {
  items?: MemoryItem[];
  has_more?: boolean;
  next_offset?: number | null;
};

function formatText(
  props: PluginSurfaceProps,
  key: string,
  fallback: string,
  values: Record<string, string | number>,
): string {
  const translated = props.t?.(key, values);
  if (translated && translated !== key) return translated;
  return fallback.replace(/\{([^}]+)\}/g, (_, name: string) => String(values[name] ?? ''));
}

function deckDisplayName(props: PluginSurfaceProps, deck: MemoryDeck): string {
  return deck.is_default
    ? text(props, 'ui.memory.default_deck_name', 'Default Deck')
    : deck.name;
}

export default function MemoryDeckList(props: PluginSurfaceProps) {
  const [decks, setDecks] = useState<MemoryDeck[]>([]);
  const [name, setName] = useState('');
  const [deckType, setDeckType] = useState('word');
  const [goalAmount, setGoalAmount] = useState(10);
  const [goalUnit, setGoalUnit] = useState('cards');
  const [habitStatus, setHabitStatus] = useState<MemoryHabitStatus>({});
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [expandedDeckId, setExpandedDeckId] = useState('');
  const [itemsByDeck, setItemsByDeck] = useState<Record<string, MemoryItem[]>>({});
  const [hasMoreByDeck, setHasMoreByDeck] = useState<Record<string, boolean>>({});
  const [nextOffsetByDeck, setNextOffsetByDeck] = useState<Record<string, number>>({});

  async function refresh(signal?: AbortSignal) {
    const nextDecks = await listAllMemoryDecks<MemoryDeck>(props.api, signal);
    setDecks(nextDecks);
    postStudySurfaceMessage({
      type: STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated,
      payload: {
        card_count: nextDecks.reduce((total, deck) => total + (Number(deck.item_count) || 0), 0),
      },
    });
  }

  async function createDeck() {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setStatus(text(props, 'ui.memory.error_missing_deck_name', 'Deck name is required'));
      return;
    }
    setBusy(true);
    try {
      await callPlugin(props.api, 'study_memory_create_deck', { name: trimmedName, deck_type: deckType });
      setName('');
      await refresh();
      setStatus(text(props, 'ui.status.reply_ready', 'Reply ready'));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function deleteDeck(deckId: string) {
    setBusy(true);
    try {
      await callPlugin(props.api, 'study_memory_delete_deck', { deck_id: deckId });
      await refresh();
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function loadDeckItems(deckId: string, append = false) {
    setBusy(true);
    try {
      const offset = append ? (nextOffsetByDeck[deckId] || 0) : 0;
      const payload = await callPlugin<MemoryItemPage>(
        props.api,
        'study_memory_list_deck_items',
        { deck_id: deckId, limit: 500, offset },
      );
      const pageItems = Array.isArray(payload.items) ? payload.items : [];
      setItemsByDeck((current) => ({
        ...current,
        [deckId]: append ? [...(current[deckId] || []), ...pageItems] : pageItems,
      }));
      setHasMoreByDeck((current) => ({ ...current, [deckId]: payload.has_more === true }));
      setNextOffsetByDeck((current) => ({
        ...current,
        [deckId]: Number(payload.next_offset) || offset + pageItems.length,
      }));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function toggleDeckItems(deckId: string) {
    if (expandedDeckId === deckId) {
      setExpandedDeckId('');
      return;
    }
    setExpandedDeckId(deckId);
    await loadDeckItems(deckId);
  }

  async function saveDeckGoal(deckId: string) {
    setBusy(true);
    try {
      const amount = normalizePositiveInteger(goalAmount, 1);
      setGoalAmount(amount);
      const payload = await setDeckGoal(props.api, deckId, amount, goalUnit);
      setStatus(deckGoalSavedMessage(props, payload));
      await refresh();
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    ensureBrandCSS();
    const controller = new AbortController();
    getMemoryHabitStatus(props.api, controller.signal)
      .then(setHabitStatus)
      .catch(() => setHabitStatus({ available: false }));
    refresh(controller.signal).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, []);

  return (
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.memory_deck_list', 'Deck Management')}</h1>
          <span>
            {status || formatText(props, 'ui.memory.deck_count', '{count} decks', { count: decks.length })}
          </span>
        </div>
      </header>
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.label.name', 'Name')}</span>
          <input value={name} disabled={busy} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.memory.deck_type', 'Deck Type')}</span>
          <select value={deckType} disabled={busy} onChange={(event) => setDeckType(event.target.value)}>
            <option value="word">{deckTypeLabel(props, 'word')}</option>
            <option value="passage">{deckTypeLabel(props, 'passage')}</option>
            <option value="formula">{deckTypeLabel(props, 'formula')}</option>
            <option value="custom">{deckTypeLabel(props, 'custom')}</option>
          </select>
        </label>
        {habitBridgeAvailable(habitStatus) ? (
          <>
            <label>
              <span>{text(props, 'ui.daily_goal.set_for_deck', 'Deck goal')}</span>
              <input type="number" value={goalAmount} disabled={busy} min={1} step={1} onChange={(event) => setGoalAmount(normalizePositiveInteger(event.target.value, 1))} />
            </label>
            <label>
              <span>{text(props, 'ui.memory.deck_goal_unit', 'Unit')}</span>
              <select value={goalUnit} disabled={busy} onChange={(event) => setGoalUnit(event.target.value)}>
                <option value="cards">{text(props, 'ui.daily_goal.deck_unit_cards', 'cards')}</option>
                <option value="minutes">{text(props, 'ui.daily_goal.deck_unit_minutes', 'minutes')}</option>
                <option value="attempts">{text(props, 'ui.daily_goal.deck_unit_attempts', 'attempts')}</option>
              </select>
            </label>
          </>
        ) : null}
        <button type="button" disabled={busy} onClick={createDeck}>
          {text(props, 'ui.button.create', 'Create')}
        </button>
      </section>
      <div className="study-panel__actions">
        {decks.map((deck) => (
          <div key={deck.id} className="study-panel__deck">
            <div className="study-panel__row">
              <span>
                {formatText(props, 'ui.memory.deck_summary', '{name} / {type} / {count} cards', {
                  name: deckDisplayName(props, deck),
                  type: deckTypeLabel(props, deck.deck_type),
                  count: deck.item_count || 0,
                })}
              </span>
              <button type="button" disabled={busy} onClick={() => toggleDeckItems(deck.id)}>
                {expandedDeckId === deck.id
                  ? text(props, 'ui.button.hide_cards', 'Hide cards')
                  : text(props, 'ui.button.view_cards', 'View cards')}
              </button>
              {habitBridgeAvailable(habitStatus) ? (
                <button type="button" disabled={busy} onClick={() => saveDeckGoal(deck.id)}>
                  {text(props, 'ui.daily_goal.set_for_deck', 'Set Goal')}
                </button>
              ) : null}
              <button type="button" disabled={busy} onClick={() => deleteDeck(deck.id)}>
                {text(props, 'ui.button.delete', 'Delete')}
              </button>
            </div>
            {expandedDeckId === deck.id ? (
              <div className="study-panel__deck-items">
                {itemsByDeck[deck.id]?.length ? itemsByDeck[deck.id].map((item) => (
                  <div key={item.id} className="study-panel__row study-panel__deck-item">
                    <span>{item.prompt || '-'}</span>
                    <span>{item.answer || '-'}</span>
                    <span>{memoryItemTypeLabel(props, item.item_type)}</span>
                  </div>
                )) : (
                  <p className="study-panel__empty">{text(props, 'ui.memory.empty_deck', 'No cards in this deck')}</p>
                )}
                {hasMoreByDeck[deck.id] ? (
                  <button type="button" disabled={busy} onClick={() => loadDeckItems(deck.id, true)}>
                    {text(props, 'ui.button.load_more_cards', 'Load more cards')}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

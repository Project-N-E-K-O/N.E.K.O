import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import { deckTypeLabel, ensureBrandCSS, postStudySurfaceMessage, STUDY_SURFACE_MESSAGE_TYPES } from './study_surface_utils';
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
};

export default function MemoryDeckList(props: PluginSurfaceProps) {
  const [decks, setDecks] = useState<MemoryDeck[]>([]);
  const [name, setName] = useState('');
  const [deckType, setDeckType] = useState('word');
  const [goalAmount, setGoalAmount] = useState(10);
  const [goalUnit, setGoalUnit] = useState('cards');
  const [habitStatus, setHabitStatus] = useState<MemoryHabitStatus>({});
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh(signal?: AbortSignal) {
    const payload = await callPlugin<{ decks?: MemoryDeck[] }>(props.api, 'study_memory_list_decks', { limit: 100 }, signal);
    const nextDecks = Array.isArray(payload.decks) ? payload.decks : [];
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
          <span>{status || `${decks.length}`}</span>
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
          <div key={deck.id} className="study-panel__row">
            <span>{deck.name} / {deckTypeLabel(props, deck.deck_type)} / {deck.item_count || 0}</span>
            {habitBridgeAvailable(habitStatus) ? (
              <button type="button" disabled={busy} onClick={() => saveDeckGoal(deck.id)}>
                {text(props, 'ui.daily_goal.set_for_deck', 'Set Goal')}
              </button>
            ) : null}
            <button type="button" disabled={busy} onClick={() => deleteDeck(deck.id)}>
              {text(props, 'ui.button.delete', 'Delete')}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

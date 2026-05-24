import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, text } from './memory_shared';

export type MemoryHabitStatus = {
  available?: boolean;
  error?: string;
};

export type MemoryDeckGoalPayload = {
  goal?: {
    id?: string;
    target_amount?: number;
    progress_amount?: number;
    unit?: string;
  };
  created?: boolean;
};

export async function getMemoryHabitStatus(signal?: AbortSignal): Promise<MemoryHabitStatus> {
  return await callPlugin<MemoryHabitStatus>('study_memory_habit_status', {}, signal);
}

export async function setDeckGoal(
  deckId: string,
  targetAmount: number,
  unit: string,
): Promise<MemoryDeckGoalPayload> {
  return await callPlugin<MemoryDeckGoalPayload>('study_memory_set_deck_goal', {
    deck_id: deckId,
    target_amount: targetAmount,
    unit,
  });
}

export async function startDeckFocus(deckId: string, focusMinutes: number) {
  return await callPlugin('study_pomodoro_start', {
    deck_id: deckId,
    focus_minutes: focusMinutes,
  });
}

export function habitBridgeAvailable(status: MemoryHabitStatus): boolean {
  return Boolean(status.available);
}

export function deckGoalSavedMessage(
  props: PluginSurfaceProps,
  payload: MemoryDeckGoalPayload,
): string {
  const goal = payload.goal || {};
  return text(
    props,
    'ui.memory.goal_saved',
    'Goal saved',
  ) + ` ${goal.progress_amount || 0}/${goal.target_amount || 0} ${goal.unit || ''}`.trimEnd();
}

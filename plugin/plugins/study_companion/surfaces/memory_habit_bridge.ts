import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, text } from './memory_shared';
import { goalUnitLabel } from './study_surface_utils';

type HostedApi = PluginSurfaceProps['api'];

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

export type PomodoroStatus = {
  state?: string;
  current_focus_session?: {
    id?: string;
  };
};

export async function getMemoryHabitStatus(api: HostedApi, signal?: AbortSignal): Promise<MemoryHabitStatus> {
  return await callPlugin<MemoryHabitStatus>(api, 'study_memory_habit_status', {}, signal);
}

export async function getPomodoroStatus(api: HostedApi): Promise<PomodoroStatus> {
  return await callPlugin<PomodoroStatus>(api, 'study_pomodoro_status', {});
}

export async function setDeckGoal(
  api: HostedApi,
  deckId: string,
  targetAmount: number,
  unit: string,
): Promise<MemoryDeckGoalPayload> {
  return await callPlugin<MemoryDeckGoalPayload>(api, 'study_memory_set_deck_goal', {
    deck_id: deckId,
    target_amount: normalizePositiveInteger(targetAmount, 1),
    unit,
  });
}

export async function startDeckFocus(api: HostedApi, deckId: string, focusMinutes: number): Promise<PomodoroStatus> {
  return await callPlugin<PomodoroStatus>(api, 'study_pomodoro_start', {
    deck_id: deckId,
    focus_minutes: normalizePositiveInteger(focusMinutes, 1),
  });
}

export function habitBridgeAvailable(status: MemoryHabitStatus): boolean {
  return Boolean(status.available);
}

export function normalizePositiveInteger(value: unknown, fallback = 1): number {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : fallback;
}

export function focusSessionId(status: PomodoroStatus): string {
  return String((status.current_focus_session || {}).id || '');
}

export function startedNewFocusSession(before: PomodoroStatus, after: PomodoroStatus): boolean {
  const beforeId = focusSessionId(before);
  const afterId = focusSessionId(after);
  return String(after.state || '') === 'focusing' && Boolean(afterId) && afterId !== beforeId;
}

export function deckGoalUnitLabel(props: PluginSurfaceProps, unit: unknown): string {
  return goalUnitLabel(props, unit);
}

export function deckGoalSavedMessage(
  props: PluginSurfaceProps,
  payload: MemoryDeckGoalPayload,
): string {
  const goal = payload.goal || {};
  const progress = Number.isFinite(Number(goal.progress_amount)) ? Number(goal.progress_amount) : 0;
  const target = Number.isFinite(Number(goal.target_amount)) ? Number(goal.target_amount) : 0;
  return text(
    props,
    'ui.memory.goal_saved',
    'Goal saved',
  ) + ` ${progress}/${target} ${deckGoalUnitLabel(props, goal.unit)}`.trimEnd();
}

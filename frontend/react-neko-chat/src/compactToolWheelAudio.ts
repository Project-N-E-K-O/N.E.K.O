import { useEffect } from 'react';

export const COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS = [
  '/static/sounds/compact-tool-wheel/wheel-prompt.mp3',
] as const;

export const COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC = '';

export const COMPACT_TOOL_WHEEL_PRELOAD_SOUND_SRCS = [
  ...COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS,
] as const;

const COMPACT_TOOL_WHEEL_AUDIO_PRELOAD_RETRY_DELAYS_MS = [120, 300, 700, 1500] as const;

export const COMPACT_TOOL_WHEEL_REBOUND_VISUAL_SOFT_INTENSITY = 0.38;
export const COMPACT_TOOL_WHEEL_REBOUND_VISUAL_STRONG_INTENSITY = 0.85;

const COMPACT_TOOL_WHEEL_REBOUND_VISUAL_MIN_RATIO = 0.2;
const COMPACT_TOOL_WHEEL_REBOUND_VISUAL_MEDIUM_RATIO = 0.4;
const COMPACT_TOOL_WHEEL_REBOUND_VISUAL_STRONG_RATIO = 0.7;
const COMPACT_TOOL_WHEEL_REBOUND_VISUAL_MEDIUM_INTENSITY = 0.6;

type NekoGameAudioSystemInstance = {
  playSfx: (keyOrAudio: unknown, options?: Record<string, unknown>) => unknown;
  preloadSfx?: (keyOrAudio: unknown) => unknown;
};

type NekoGameAudioSystemConstructor = new (options?: Record<string, unknown>) => NekoGameAudioSystemInstance;

let compactToolWheelAudioSystem: NekoGameAudioSystemInstance | null | undefined;

export function getCompactToolWheelAudioSystem(): NekoGameAudioSystemInstance | null {
  if (compactToolWheelAudioSystem) {
    return compactToolWheelAudioSystem;
  }
  if (typeof window === 'undefined') {
    return null;
  }
  const GameAudioSystem = (window as Window & {
    NekoGameSystem?: {
      GameAudioSystem?: NekoGameAudioSystemConstructor;
    };
  }).NekoGameSystem?.GameAudioSystem;
  if (typeof GameAudioSystem !== 'function') {
    return null;
  }
  try {
    const audioSystem = new GameAudioSystem({
      config: {
        audioMix: {
          sfx: {
            baseVolume: 0.24,
            maxVolume: 1,
          },
        },
        sfx: {},
      },
    });
    if (typeof audioSystem.playSfx !== 'function') {
      return null;
    }
    audioSystem.preloadSfx?.(COMPACT_TOOL_WHEEL_PRELOAD_SOUND_SRCS);
    compactToolWheelAudioSystem = audioSystem;
  } catch {
    compactToolWheelAudioSystem = undefined;
    return null;
  }
  return compactToolWheelAudioSystem;
}

export function preloadCompactToolWheelSounds(): boolean {
  return getCompactToolWheelAudioSystem() !== null;
}

export function useCompactToolWheelAudioPreload() {
  useEffect(() => {
    let retryTimer: number | null = null;
    let retryIndex = 0;
    let cancelled = false;

    const tryPreload = () => {
      if (cancelled) return;
      if (preloadCompactToolWheelSounds()) return;
      if (retryIndex >= COMPACT_TOOL_WHEEL_AUDIO_PRELOAD_RETRY_DELAYS_MS.length) return;
      const delayMs = COMPACT_TOOL_WHEEL_AUDIO_PRELOAD_RETRY_DELAYS_MS[retryIndex];
      retryIndex += 1;
      retryTimer = window.setTimeout(tryPreload, delayMs);
    };

    tryPreload();
    return () => {
      cancelled = true;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
    };
  }, []);
}

export function resetCompactToolWheelDetentAudioForTests() {
  compactToolWheelAudioSystem = undefined;
}

export function playCompactToolWheelDetentSound(
  soundSrc: string | readonly string[] = COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS,
) {
  const soundSrcs = Array.isArray(soundSrc) ? soundSrc : [soundSrc];
  const availableSoundSrcs = soundSrcs.map(src => src.trim()).filter(Boolean);
  if (availableSoundSrcs.length === 0) return;
  const src = availableSoundSrcs[Math.floor(Math.random() * availableSoundSrcs.length)] ?? availableSoundSrcs[0];
  if (!src) return;
  const audioSystem = getCompactToolWheelAudioSystem();
  if (!audioSystem) return;
  try {
    void audioSystem.playSfx({ src, preload: 'auto' });
  } catch {
    // Optional UI SFX must never block wheel interaction.
  }
}

export function getCompactToolWheelReboundVisualIntensity(offsetRatio: number): number | null {
  const absOffsetRatio = Math.abs(offsetRatio);
  if (absOffsetRatio < COMPACT_TOOL_WHEEL_REBOUND_VISUAL_MIN_RATIO) return null;
  if (absOffsetRatio < COMPACT_TOOL_WHEEL_REBOUND_VISUAL_MEDIUM_RATIO) {
    return COMPACT_TOOL_WHEEL_REBOUND_VISUAL_SOFT_INTENSITY;
  }
  return absOffsetRatio >= COMPACT_TOOL_WHEEL_REBOUND_VISUAL_STRONG_RATIO
    ? COMPACT_TOOL_WHEEL_REBOUND_VISUAL_STRONG_INTENSITY
    : COMPACT_TOOL_WHEEL_REBOUND_VISUAL_MEDIUM_INTENSITY;
}

export function getCompactToolWheelReboundVolume(offsetRatio: number): number | null {
  return getCompactToolWheelReboundVisualIntensity(offsetRatio);
}

export function playCompactToolWheelReboundSound(
  soundSrc = COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC,
  intensity = COMPACT_TOOL_WHEEL_REBOUND_VISUAL_STRONG_INTENSITY,
) {
  const src = soundSrc.trim();
  if (!src) return;
  const audioSystem = getCompactToolWheelAudioSystem();
  if (!audioSystem) return;
  try {
    // The current callers pass visual rebound intensity; if rebound audio is restored, reuse it as legacy volume.
    void audioSystem.playSfx({ src, preload: 'auto' }, { volume: intensity });
  } catch {
    // Optional UI SFX must never block wheel interaction.
  }
}

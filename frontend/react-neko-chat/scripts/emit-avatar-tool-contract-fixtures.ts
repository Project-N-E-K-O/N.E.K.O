import {
  AVATAR_TOOL_DEFINITIONS,
  type AvatarToolContractDefinition,
} from '../src/avatar-tools/catalog';
import { projectDesktopAvatarToolContract } from '../src/avatar-tools/desktopContract';

const visualSource = AVATAR_TOOL_DEFINITIONS[0];

const syntheticRoundDefinition = {
  ...visualSource,
  sounds: [
    {
      id: 'round-confirm',
      src: '/static/sounds/avatar-tools/test-round/confirm.mp3',
      volume: 0.9,
    },
    {
      id: 'round-user-win',
      src: '/static/sounds/avatar-tools/test-round/user-win.mp3',
      volume: 0.9,
    },
    {
      id: 'round-other-result',
      src: '/static/sounds/avatar-tools/test-round/other-result.mp3',
      volume: 0.9,
    },
  ],
  effects: [{
    id: 'round-reveal',
    kind: 'round-reveal',
    interactionLock: 'effect-lifetime',
    anchors: {
      user: 'release-pointer',
      avatar: 'avatar-head',
    },
    timeline: [
      { phase: 'reveal', delayMs: 0 },
      { phase: 'result', delayMs: 1000 },
      { phase: 'idle', delayMs: 2500 },
    ],
    labels: {
      gestures: {
        alpha: { key: 'avatarTools.syntheticRound.gestures.alpha', fallback: 'Alpha' },
        beta: { key: 'avatarTools.syntheticRound.gestures.beta', fallback: 'Beta' },
        gamma: { key: 'avatarTools.syntheticRound.gestures.gamma', fallback: 'Gamma' },
      },
      results: {
        user_win: { key: 'avatarTools.syntheticRound.results.userWin', fallback: 'You win' },
        avatar_win: { key: 'avatarTools.syntheticRound.results.avatarWin', fallback: '{{name}} wins' },
        draw: { key: 'avatarTools.syntheticRound.results.draw', fallback: 'Draw' },
      },
      announcement: {
        key: 'avatarTools.syntheticRound.announcement',
        fallback: 'Ready, set, reveal!',
      },
    },
    layout: {
      userOffset: { x: 0, y: 0 },
      avatarOffset: { x: 12, y: -8 },
      resultOffset: { x: 0, y: 24 },
    },
  }],
  interaction: {
    kind: 'round-choice',
    actionId: 'play',
    intensity: 'normal',
    choices: {
      primary: 'alpha',
      secondary: 'beta',
      tertiary: 'gamma',
    },
    beats: {
      alpha: 'gamma',
      beta: 'alpha',
      gamma: 'beta',
    },
    facts: {
      userChoiceField: 'userGesture',
      avatarChoiceField: 'avatarGesture',
      resultField: 'roundResult',
    },
    cycle: {
      outsideMs: 240,
      inRangeMs: 1200,
      avatarPreviewMs: 200,
    },
    random: { strategy: 'uniform' },
    presentation: { confirmation: 'render-owner-required' },
    feedback: {
      effect: 'round-reveal',
      confirmSound: 'round-confirm',
      resultSounds: {
        user_win: 'round-user-win',
        avatar_win: 'round-other-result',
        draw: 'round-other-result',
      },
    },
  },
} as const satisfies AvatarToolContractDefinition;

console.log(JSON.stringify(projectDesktopAvatarToolContract(syntheticRoundDefinition)));

import type {
  AvatarToolEffectId,
  AvatarToolImageAction,
  AvatarToolImageId,
  AvatarToolInteractionId,
  AvatarToolSoundId,
  CustomGraphProfile,
} from './catalog';

export type CustomGraphScheduler = {
  now(): number;
  setTimeout(callback: () => void, delayMs: number): number;
  clearTimeout(timeoutId: number): void;
};

export type CustomGraphClickCompletion = {
  interactionId: AvatarToolInteractionId;
  capturedImageId: AvatarToolImageId;
  currentImageId: AvatarToolImageId;
};

export type CustomGraphRuntimeSnapshot = {
  currentImageId: AvatarToolImageId;
  waitingInteractionIds: ReadonlyArray<AvatarToolInteractionId>;
  activeClick: {
    interactionId: AvatarToolInteractionId;
    capturedImageId: AvatarToolImageId;
  } | null;
};

export type CustomGraphRuntime = {
  beginClick(): boolean;
  completeClick(): CustomGraphClickCompletion | null;
  cancelClick(): boolean;
  destroy(): void;
  getSnapshot(): CustomGraphRuntimeSnapshot;
};

type DelayTicket = {
  interactionId: AvatarToolInteractionId;
  dueAt: number;
  order: number;
  epoch: number;
  timeoutId: number;
};

type PendingDelay = Omit<DelayTicket, 'timeoutId'>;

export function getCustomGraphImageFrameIndex(
  profile: CustomGraphProfile,
  imageId: AvatarToolImageId,
): number {
  return profile.images.find(image => image.id === imageId)?.frameIndex ?? 0;
}

export function resolveCustomGraphLocalFeedback(
  profile: CustomGraphProfile,
  random: () => number,
): { sound?: AvatarToolSoundId; effect?: AvatarToolEffectId; specialTriggered: boolean } {
  const specialTriggered = !!profile.chance && random() < profile.chance.probability;
  if (specialTriggered && profile.chance) {
    return {
      ...(profile.chance.sound ?? profile.feedback?.sound
        ? { sound: profile.chance.sound ?? profile.feedback?.sound }
        : {}),
      effect: profile.chance.effect,
      specialTriggered: true,
    };
  }
  return {
    ...(profile.feedback ? { sound: profile.feedback.sound } : {}),
    specialTriggered: false,
  };
}

export function createCustomGraphRuntime(
  profile: CustomGraphProfile,
  options: {
    scheduler: CustomGraphScheduler;
    onImageChange(imageId: AvatarToolImageId, frameIndex: number): void;
  },
): CustomGraphRuntime {
  const interactionsById = new Map(profile.interactions.map(item => [item.id, item]));
  const successorsById = new Map<AvatarToolInteractionId, AvatarToolInteractionId[]>();
  profile.links.forEach((link) => {
    const successors = successorsById.get(link.from) ?? [];
    successors.push(link.to);
    successorsById.set(link.from, successors);
  });
  let currentImageId = profile.initialImageId;
  let waitingInteractionIds = [...profile.initialInteractionIds];
  let activeClick: {
    interactionId: AvatarToolInteractionId;
    capturedImageId: AvatarToolImageId;
    epoch: number;
  } | null = null;
  let epoch = 1;
  let destroyed = false;
  let nextDelayOrder = 0;
  const delays = new Map<AvatarToolInteractionId, DelayTicket>();
  let pendingDelays: PendingDelay[] = [];

  const setImage = (imageId: AvatarToolImageId) => {
    if (destroyed || currentImageId === imageId) return;
    currentImageId = imageId;
    options.onImageChange(imageId, getCustomGraphImageFrameIndex(profile, imageId));
  };

  const applyImageAction = (action: AvatarToolImageAction) => {
    if (action.kind === 'show') setImage(action.imageId);
  };

  const clearDelays = () => {
    delays.forEach(ticket => options.scheduler.clearTimeout(ticket.timeoutId));
    delays.clear();
    pendingDelays = [];
  };

  const collectDueDelays = () => {
    const now = options.scheduler.now();
    [...delays.values()].forEach((ticket) => {
      if (
        ticket.epoch !== epoch
        || ticket.dueAt > now
        || !waitingInteractionIds.includes(ticket.interactionId)
      ) return;
      options.scheduler.clearTimeout(ticket.timeoutId);
      delays.delete(ticket.interactionId);
      pendingDelays.push({
        interactionId: ticket.interactionId,
        dueAt: ticket.dueAt,
        order: ticket.order,
        epoch: ticket.epoch,
      });
    });
  };

  const scheduleWaitingPosition = () => {
    const scheduleEpoch = epoch;
    waitingInteractionIds.forEach((interactionId) => {
      const interaction = interactionsById.get(interactionId);
      if (!interaction || interaction.trigger.kind !== 'after') return;
      const dueAt = options.scheduler.now() + interaction.trigger.delayMs;
      const order = nextDelayOrder++;
      const timeoutId = options.scheduler.setTimeout(() => {
        const ticket = delays.get(interactionId);
        if (
          destroyed
          || !ticket
          || ticket.epoch !== epoch
          || ticket.epoch !== scheduleEpoch
          || !waitingInteractionIds.includes(interactionId)
        ) return;
        delays.delete(interactionId);
        pendingDelays.push({ interactionId, dueAt: ticket.dueAt, order: ticket.order, epoch: ticket.epoch });
        if (!activeClick) resolvePendingDelay();
      }, interaction.trigger.delayMs);
      delays.set(interactionId, { interactionId, dueAt, order, epoch: scheduleEpoch, timeoutId });
    });
  };

  const enterWaitingPosition = (interactionIds: ReadonlyArray<AvatarToolInteractionId>) => {
    clearDelays();
    epoch += 1;
    waitingInteractionIds = [...interactionIds];
    scheduleWaitingPosition();
  };

  const advanceFrom = (interactionId: AvatarToolInteractionId) => {
    enterWaitingPosition(successorsById.get(interactionId) ?? []);
  };

  function resolvePendingDelay() {
    if (destroyed || activeClick || pendingDelays.length === 0) return;
    pendingDelays.sort((left, right) => left.dueAt - right.dueAt || left.order - right.order);
    const winner = pendingDelays[0];
    if (winner.epoch !== epoch || !waitingInteractionIds.includes(winner.interactionId)) {
      pendingDelays.shift();
      resolvePendingDelay();
      return;
    }
    const interaction = interactionsById.get(winner.interactionId);
    if (!interaction || interaction.trigger.kind !== 'after' || !('complete' in interaction.actions)) {
      pendingDelays.shift();
      resolvePendingDelay();
      return;
    }
    applyImageAction(interaction.actions.complete);
    advanceFrom(interaction.id);
  }

  scheduleWaitingPosition();

  return {
    beginClick() {
      if (destroyed || activeClick) return false;
      collectDueDelays();
      resolvePendingDelay();
      const interaction = waitingInteractionIds
        .map(id => interactionsById.get(id))
        .find(candidate => candidate?.trigger.kind === 'mouse-click');
      if (!interaction || interaction.trigger.kind !== 'mouse-click' || !('press' in interaction.actions)) {
        return false;
      }
      activeClick = {
        interactionId: interaction.id,
        capturedImageId: currentImageId,
        epoch,
      };
      applyImageAction(interaction.actions.press);
      return true;
    },
    completeClick() {
      const ticket = activeClick;
      if (destroyed || !ticket || ticket.epoch !== epoch) return null;
      const interaction = interactionsById.get(ticket.interactionId);
      if (!interaction || interaction.trigger.kind !== 'mouse-click' || !('release' in interaction.actions)) {
        activeClick = null;
        return null;
      }
      activeClick = null;
      applyImageAction(interaction.actions.release);
      const completion = {
        interactionId: interaction.id,
        capturedImageId: ticket.capturedImageId,
        currentImageId,
      };
      advanceFrom(interaction.id);
      return completion;
    },
    cancelClick() {
      const ticket = activeClick;
      if (destroyed || !ticket) return false;
      activeClick = null;
      setImage(ticket.capturedImageId);
      collectDueDelays();
      resolvePendingDelay();
      return true;
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      activeClick = null;
      waitingInteractionIds = [];
      clearDelays();
    },
    getSnapshot() {
      return {
        currentImageId,
        waitingInteractionIds: [...waitingInteractionIds],
        activeClick: activeClick ? {
          interactionId: activeClick.interactionId,
          capturedImageId: activeClick.capturedImageId,
        } : null,
      };
    },
  };
}

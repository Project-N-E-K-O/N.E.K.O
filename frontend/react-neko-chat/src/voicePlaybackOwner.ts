// All replay surfaces share one owner, including requests still in flight.
let owner: (() => void) | undefined;
export function claimVoicePlayback(stop: () => void) { owner?.(); owner = stop; }
export function releaseVoicePlayback(stop: () => void) { if (owner === stop) owner = undefined; }

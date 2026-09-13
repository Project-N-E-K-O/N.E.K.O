// Pure media-clock scheduler. No wall-clock setTimeout reactions.
export class ReactionClock {
  constructor(events = []) { this.reset(events); }
  reset(events) { this.events = [...events].sort((a,b) => a.at-b.at); this.used = new Set(); this.last = null; }
  seek(time) {
    // Explicit rewind rearms future cues; skip-forward never dumps old cues.
    this.used = new Set(this.events.filter(e => e.at < time - 0.08));
    this.last = time - 0.08;
  }
  tick(time, running) {
    if (!running) return null;
    if (this.last !== null && (time < this.last - 0.2 || time - this.last > 1.5)) {
      this.seek(time); return null;
    }
    const previous = this.last ?? time - 0.08;
    this.last = time;
    for (const e of this.events) {
      if (this.used.has(e) || e.at > time) continue;
      this.used.add(e);
      if (e.at >= previous - 0.025 && time - e.at <= 0.3) return e;
    }
    return null;
  }
}

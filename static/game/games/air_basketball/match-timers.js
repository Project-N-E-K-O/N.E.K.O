// Pause gameplay deadlines without changing speech or decorative UI timers.
export class MatchTimers {
  constructor({ now = () => performance.now(), schedule = (callback, delay) => setTimeout(callback, delay), cancel = handle => clearTimeout(handle) } = {}) {
    Object.assign(this, { now, schedule, cancel });
    this.entries = new Set();
    this.paused = false;
  }
  set(callback, milliseconds) {
    const entry = { callback, remaining:milliseconds, handle:null, due:0 };
    this.entries.add(entry);
    if (!this.paused) this.arm(entry);
    return entry;
  }
  arm(entry) {
    entry.due = this.now() + entry.remaining;
    entry.handle = this.schedule(() => {
      if (!this.entries.delete(entry)) return;
      entry.callback();
    }, entry.remaining);
  }
  clear(entry) {
    if (!this.entries.delete(entry)) return;
    this.cancel(entry.handle);
  }
  pause() {
    if (this.paused) return;
    this.paused = true;
    for (const entry of this.entries) {
      this.cancel(entry.handle);
      entry.remaining = Math.max(0, entry.due - this.now());
    }
  }
  resume() {
    if (!this.paused) return;
    this.paused = false;
    for (const entry of this.entries) this.arm(entry);
  }
  clearAll() {
    for (const entry of [...this.entries]) this.clear(entry);
  }
}

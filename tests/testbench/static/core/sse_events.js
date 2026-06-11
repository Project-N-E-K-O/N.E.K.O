/**
 * sse_events.js - Frontend mirror of pipeline/sse_events.py::SseEvent.
 *
 * The backend enum is the single source of truth; this file copies the
 * string values so frontend dispatchers (`switch (ev.event)`) can refer
 * to named constants instead of free-floating literals.
 *
 * When renaming / adding an event, update BOTH sides:
 *   1. tests/testbench/pipeline/sse_events.py (add enum member)
 *   2. This file (add constant)
 * Future lint smoke (Rule 6) will cross-check the two lists.
 *
 * Usage is optional — keeping the literal `ev.event === 'user'` in
 * switches is still fine (and is what all current dispatchers do). The
 * value of this file is:
 *   - it documents the complete, authoritative list in one place
 *   - agents writing new dispatchers can import and get autocompletion
 *   - code review can grep `SSE_EVENT.*` to find consumers
 *
 * See P24_BLUEPRINT §14A.3.
 */

export const SSE_EVENT = Object.freeze({
  // chat/ core
  USER: 'user',
  WIRE_BUILT: 'wire_built',
  ASSISTANT_START: 'assistant_start',
  DELTA: 'delta',
  USAGE: 'usage',
  ASSISTANT: 'assistant',
  DONE: 'done',

  // auto/
  START: 'start',
  PAUSED: 'paused',
  RESUMED: 'resumed',
  STOPPED: 'stopped',
  SIMUSER_DONE: 'simuser_done',
  TURN_BEGIN: 'turn_begin',
  TURN_DONE: 'turn_done',

  // script/
  SCRIPT_TURN_DONE: 'script_turn_done',
  SCRIPT_TURN_WARNINGS: 'script_turn_warnings',
  SCRIPT_EXHAUSTED: 'script_exhausted',

  // meta/
  ERROR: 'error',
  WARNING: 'warning',
});

/** Set of all legal event strings - mirrors ALL_EVENTS in the Python enum. */
export const ALL_SSE_EVENTS = Object.freeze(new Set(Object.values(SSE_EVENT)));

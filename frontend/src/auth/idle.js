// src/auth/idle.js
//
// When the idle warning shows and when the session is checked, as plain
// functions so Node's test runner covers them (idle.test.js). endsAt is the
// session's end and now the time, both in ms on this browser's clock.

export const WARN_BEFORE_S = 120;

// The API reports whole seconds, rounded down, so the session can live up
// to a second past endsAt. It is checked only after that.
const ROUNDING_MS = 1000;

export function secondsLeft(endsAt, now) {
  return Math.max(0, Math.ceil((endsAt - now) / 1000));
}

// Whether the session must be over, so that the API should be asked.
export function isOver(endsAt, now) {
  return now >= endsAt + ROUNDING_MS;
}

// How long to wait before looking again: until the warning is due, then a
// second at a time.
export function nextLook(endsAt, now) {
  return Math.max(1000, endsAt - WARN_BEFORE_S * 1000 - now);
}

// "1:05"
export function formatLeft(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

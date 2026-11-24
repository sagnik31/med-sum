// src/audit.js
//
// The audit page's date filters as times, as a plain function so Node's
// test runner covers it (audit.test.js).

// The time a local day (YYYY-MM-DD) starts, or the one `days` after it.
function dayStart(day, days = 0) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d + days).toISOString();
}

// The days chosen, both inclusive, in the browser's time zone (the one the
// table shows times in), as the API takes times: from the start of the first
// to before the start of the day after the last. "" for a day not chosen.
export function localDays(from, to) {
  return { from: from ? dayStart(from) : "", to: to ? dayStart(to, 1) : "" };
}

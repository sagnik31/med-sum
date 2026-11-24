import { test } from "node:test";
import assert from "node:assert/strict";
import { WARN_BEFORE_S, formatLeft, isOver, nextLook, secondsLeft } from "./idle.js";

// Looks at the times nextLook gives, from 0, until the session is over.
function looks(endsAt) {
  const seen = [];
  let now = 0;
  while (!isOver(endsAt, now)) {
    now += nextLook(endsAt, now);
    seen.push({ now, left: secondsLeft(endsAt, now) });
  }
  return seen;
}

test("a full session warns two minutes before its end, then counts down to it", () => {
  const seen = looks(1800 * 1000);
  assert.deepEqual(seen[0], { now: 1680 * 1000, left: WARN_BEFORE_S });
  assert.deepEqual(seen.slice(1, 121).map((s) => s.left), Array.from({ length: 120 }, (_, i) => 119 - i));
});

test("the session is checked only once it must be over, the rounded-off second included", () => {
  const endsAt = 118 * 1000; // reported as 118; it really ends at up to 118.999 s
  assert.equal(isOver(endsAt, endsAt), false);
  assert.equal(isOver(endsAt, endsAt + 999), false);
  assert.equal(isOver(endsAt, endsAt + 1000), true);
  assert.equal(looks(endsAt).at(-1).now, 119 * 1000);
});

test("the time left is shown in minutes and seconds", () => {
  assert.equal(formatLeft(120), "2:00");
  assert.equal(formatLeft(65), "1:05");
});

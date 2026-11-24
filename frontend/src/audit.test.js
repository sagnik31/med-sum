import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import process from "node:process";
import { localDays } from "./audit.js";

const zone = process.env.TZ;
afterEach(() => {
  if (zone === undefined) delete process.env.TZ;
  else process.env.TZ = zone;
});

test("the days filtered are the days the table shows", () => {
  process.env.TZ = "Asia/Kolkata";
  const day = localDays("2026-11-24", "2026-11-24");
  assert.deepEqual(day, { from: "2026-11-23T18:30:00.000Z", to: "2026-11-24T18:30:00.000Z" });
  // Shown as 24 November, 00:45.
  assert.ok(day.from <= "2026-11-23T19:15:00.000Z" && "2026-11-23T19:15:00.000Z" < day.to);
});

test("a day the clocks change on is as long as it is", () => {
  process.env.TZ = "Europe/London";
  assert.deepEqual(localDays("2026-10-25", "2026-10-25"), {
    from: "2026-10-24T23:00:00.000Z",
    to: "2026-10-26T00:00:00.000Z",
  });
});

test("a day not chosen is no filter", () => {
  assert.deepEqual(localDays("", ""), { from: "", to: "" });
});

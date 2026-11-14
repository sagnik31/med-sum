import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration, runSummary, stepRow } from "./processing.js";

const run = {
  end_state: "needs_review",
  runs: 2,
  steps: [
    { seq: 1, node: "transcribe", page: 1, attempt: 1, status: "ok", model: "qwen3.5:4b", model_role: "primary",
      duration_ms: 420000, model_calls: 1, failures: [] },
    { seq: 2, node: "parse", page: null, attempt: 2, status: "failed", model: "gemma4:e2b", model_role: "fallback",
      duration_ms: 850, model_calls: 2,
      failures: [{ validator: "source_text", severity: "error", item_id: "obs-1", field: "value_text" }],
      error: "parse output did not validate" },
  ],
};

test("durations read plainly", () => {
  assert.equal(formatDuration(850), "850 ms");
  assert.equal(formatDuration(4200), "4.2 s");
  assert.equal(formatDuration(185000), "3 min 05 s");
  assert.equal(formatDuration(null), "");
});

test("a row names the step, its model and role, and what failed it", () => {
  const row = stepRow(run.steps[1]);
  assert.equal(row.name, "parse");
  assert.equal(row.attempt, "attempt 2");
  assert.equal(row.model, "gemma4:e2b (fallback)");
  assert.deepEqual(row.failures, ["source text (error) on obs-1, value_text"]);
  assert.equal(stepRow(run.steps[0]).name, "transcribe, page 1");
});

test("the summary counts retries, failures and fallback use", () => {
  assert.equal(
    runSummary(run),
    "ended needs review · 2 steps · 1 retried · 1 failed · the fallback model was used · 7 min 01 s · run 2 of this document"
  );
});

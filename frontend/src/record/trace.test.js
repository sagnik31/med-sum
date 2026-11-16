import { test } from "node:test";
import assert from "node:assert/strict";
import { codeLine, findingLine, notReplayableReason, pretty, promptOf, runLine, tokenLine } from "./trace.js";

const run = {
  started_at: "2026-11-16T09:00:00Z",
  ended_at: "2026-11-16T09:03:05Z",
  end_state: "needs_review",
  steps: 5,
  failed_steps: 1,
};

test("a run's line gives its end, steps, failures and length", () => {
  const line = runLine(run);
  assert.ok(line.endsWith(" · needs review · 5 steps · 1 failed · 3 min 05 s"), line);
  assert.ok(runLine({ ...run, end_state: null, ended_at: null, failed_steps: 0 }).endsWith("still running · 5 steps"));
});

test("the code a run came from says when it had uncommitted changes", () => {
  assert.equal(codeLine({ code_version: "abc123", code_dirty: false }), "abc123");
  assert.equal(codeLine({ code_version: "abc123", code_dirty: true }), "abc123 (uncommitted changes)");
  assert.equal(codeLine({ code_version: null }), "unknown");
});

test("stored JSON is shown indented, and nothing for none", () => {
  assert.equal(pretty({ page: 1 }), '{\n  "page": 1\n}');
  assert.equal(pretty(null), "");
  assert.equal(pretty([]), "");
});

test("a clinician's finding keeps its message", () => {
  assert.equal(
    findingLine({ validator: "source_text", severity: "error", item_id: "obs-1", field: "value_text",
                  message: "value 10.4 not on line 8" }),
    "source text (error) on obs-1, value_text: value 10.4 not on line 8"
  );
  assert.equal(findingLine({ validator: "coverage", severity: "info" }), "coverage (info)");
});

test("tokens read in and out, and nothing when no model ran", () => {
  assert.equal(tokenLine({ prompt_tokens: 1200, completion_tokens: 340 }), "1200 in / 340 out");
  assert.equal(tokenLine({ prompt_tokens: null, completion_tokens: null }), "");
});

test("a step's prompt comes from the trace's prompts by hash", () => {
  const prompts = { "sha256:b": { name: "extract_page", text: "Read the page." } };
  assert.deepEqual(promptOf({ prompt_hash: "sha256:b", prompt_name: "extract_page+repair_parse" }, prompts), {
    name: "extract_page+repair_parse",
    text: "Read the page.",
  });
  assert.equal(promptOf({ prompt_hash: "sha256:c" }, prompts), null);
  assert.equal(promptOf({ prompt_hash: null }, prompts), null);
});

test("a step without a replay command says why", () => {
  assert.equal(notReplayableReason({ node: "validate", status: "ok" }), "Only model steps can be replayed.");
  assert.equal(notReplayableReason({ node: "parse", status: "running" }), "The step is still running.");
  assert.match(notReplayableReason({ node: "repair", status: "ok" }), /used tools/);
});

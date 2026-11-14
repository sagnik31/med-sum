// src/record/processing.js
//
// The "Processing details" panel: how a document's latest pipeline run
// went, step by step. Only how each step went is shown (status, model and
// its role, duration, attempt, the checks that failed it), never what the
// step read or wrote. See docs/adr/0006-durable-traceable-runs.md.

// "850 ms", "4.2 s", "3 min 05 s"; "" when unknown.
export function formatDuration(ms) {
  if (ms === null || ms === undefined || ms < 0) return "";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

function failureText(f) {
  const on = f.item_id ? ` on ${f.item_id}` : "";
  const field = f.field ? `, ${f.field}` : "";
  return `${f.validator.replaceAll("_", " ")} (${f.severity})${on}${field}`;
}

// One row of the panel.
export function stepRow(step) {
  const fallback = step.model_role === "fallback" ? " (fallback)" : "";
  return {
    key: step.seq,
    name: step.page ? `${step.node}, page ${step.page}` : step.node,
    attempt: step.attempt > 1 ? `attempt ${step.attempt}` : "",
    status: step.status,
    model: step.model ? `${step.model}${fallback}` : "no model",
    duration: formatDuration(step.duration_ms),
    calls: step.model_calls,
    failures: (step.failures || []).map(failureText),
    error: step.error || null,
  };
}

// The run's line above the table: its end, steps, retries, fallback use.
export function runSummary(run) {
  const steps = run.steps || [];
  const retries = steps.filter((s) => s.attempt > 1).length;
  const failed = steps.filter((s) => s.status === "failed").length;
  const fallback = steps.some((s) => s.model_role === "fallback");
  const total = steps.reduce((sum, s) => sum + (s.duration_ms || 0), 0);
  const parts = [
    run.end_state ? `ended ${run.end_state.replaceAll("_", " ")}` : "still running",
    `${steps.length} steps`,
    retries ? `${retries} retried` : "no retries",
  ];
  if (failed) parts.push(`${failed} failed`);
  if (fallback) parts.push("the fallback model was used");
  parts.push(formatDuration(total));
  if (run.runs > 1) parts.push(`run ${run.runs} of this document`);
  return parts.join(" · ");
}

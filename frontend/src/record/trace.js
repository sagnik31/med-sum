// src/record/trace.js
//
// The trace screen: how the pipeline produced a document's record. A
// clinician sees a run in full (what each step read and wrote, its prompt,
// the checks' findings with their messages); an admin sees the
// organisation's runs with no content at all. Neither can run anything: a
// step that can be replayed shows the command an operator runs.
// See docs/adr/0006-durable-traceable-runs.md.
import { formatDuration } from "./processing.js";

// "16 Nov 2026, 09:00"; "" when unknown.
export function formatTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// One run as a line, for the run picker and the admin list.
export function runLine(run) {
  const parts = [
    formatTime(run.started_at),
    run.end_state ? run.end_state.replaceAll("_", " ") : "still running",
    `${run.steps} steps`,
  ];
  if (run.failed_steps) parts.push(`${run.failed_steps} failed`);
  if (run.started_at && run.ended_at) {
    parts.push(formatDuration(new Date(run.ended_at) - new Date(run.started_at)));
  }
  return parts.join(" · ");
}

// Where the code that made the run came from: "abc123", "abc123 (uncommitted
// changes)", or "unknown".
export function codeLine(run) {
  if (!run.code_version) return "unknown";
  return run.code_dirty ? `${run.code_version} (uncommitted changes)` : run.code_version;
}

// A step's stored JSON, readable; "" for none.
export function pretty(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value) && value.length === 0) return "";
  return JSON.stringify(value, null, 2);
}

// A finding, message included (clinicians only).
export function findingLine(f) {
  const on = f.item_id ? ` on ${f.item_id}` : "";
  const field = f.field ? `, ${f.field}` : "";
  const msg = f.message ? `: ${f.message}` : "";
  return `${f.validator.replaceAll("_", " ")} (${f.severity})${on}${field}${msg}`;
}

// The tokens a step used: "1200 in / 340 out"; "" when no model ran.
export function tokenLine(step) {
  if (step.prompt_tokens == null && step.completion_tokens == null) return "";
  return `${step.prompt_tokens ?? "?"} in / ${step.completion_tokens ?? "?"} out`;
}

// The prompt a step used, from the trace's prompts (by hash): its name and
// text, or null when the step used none or its text is not stored.
export function promptOf(step, prompts) {
  if (!step.prompt_hash) return null;
  const p = prompts?.[step.prompt_hash];
  return p ? { name: step.prompt_name || p.name, text: p.text } : null;
}

// Why a step has no replay command, for the hover text.
export function notReplayableReason(step) {
  if (!["transcribe", "classify", "parse", "repair"].includes(step.node)) {
    return "Only model steps can be replayed.";
  }
  if (step.status === "running") return "The step is still running.";
  return "The step's prompt or result is not stored, or it used tools.";
}

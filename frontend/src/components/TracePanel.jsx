// src/components/TracePanel.jsx
//
// "Full trace": every run of the document and, for the one chosen, what
// each step read and wrote, the prompt it used and what the checks found.
// It is patient data, so it loads only when opened, and each load is
// audited. See docs/adr/0006-durable-traceable-runs.md.
import React, { useState } from "react";
import { fetchTrace } from "../api/client.js";
import { stepRow } from "../record/processing.js";
import { codeLine, findingLine, pretty, promptOf, runLine, tokenLine } from "../record/trace.js";
import ReplayCommand from "./ReplayCommand.jsx";

function Block({ label, text }) {
  if (!text) return null;
  return (
    <div className="mt-2">
      <p className="font-semibold text-slate-600">{label}</p>
      <pre className="mt-1 max-h-72 overflow-auto bg-slate-50 border border-slate-200 rounded p-2 whitespace-pre-wrap break-words">
        {text}
      </pre>
    </div>
  );
}

function TraceStep({ step, prompts }) {
  const row = stepRow({ ...step, failures: [] });
  const prompt = promptOf(step, prompts);
  const findings = Array.isArray(step.findings) ? step.findings : [];
  const tokens = tokenLine(step);
  return (
    <details className="border-b border-slate-100 py-1">
      <summary className="cursor-pointer">
        <span className="text-slate-500 mr-2">{step.seq}</span>
        {row.name}
        {row.attempt && <span className="ml-1 text-amber-700">({row.attempt})</span>}
        <span className={`ml-2 ${step.status === "failed" ? "text-red-700" : "text-slate-600"}`}>{step.status}</span>
        <span className="ml-2 text-slate-500">
          {row.model} · {row.duration}
          {tokens && ` · ${tokens}`}
          {findings.length > 0 && ` · ${findings.length} findings`}
        </span>
      </summary>
      <div className="pl-6 pb-2">
        <p className="mt-1">
          Replay: <ReplayCommand step={step} />
        </p>
        {step.error && <p className="mt-1 text-red-700">{step.error}</p>}
        {findings.length > 0 && (
          <div className="mt-2">
            <p className="font-semibold text-slate-600">Findings</p>
            <ul className="list-disc pl-4">
              {findings.map((f, i) => (
                <li key={i}>{findingLine(f)}</li>
              ))}
            </ul>
          </div>
        )}
        <Block label="Input" text={pretty(step.input)} />
        <Block label="Output" text={pretty(step.output)} />
        {prompt ? (
          <Block label={`Prompt: ${prompt.name}`} text={prompt.text} />
        ) : (
          step.prompt_name && (
            <p className="mt-2 text-slate-500">
              Prompt {step.prompt_name}: its text is not stored.
            </p>
          )
        )}
      </div>
    </details>
  );
}

function TracePanel({ documentId }) {
  const [trace, setTrace] = useState(undefined); // undefined: not loaded
  const [error, setError] = useState(null);

  function load(runId) {
    setError(null);
    fetchTrace(documentId, runId)
      .then(setTrace)
      .catch((err) => setError(err.message || "Could not load the trace."));
  }

  function onToggle(e) {
    if (e.currentTarget.open && trace === undefined) load();
  }

  const run = trace?.run;
  return (
    <details className="mb-4 border border-slate-200 rounded-md" onToggle={onToggle}>
      <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-slate-700">Full trace</summary>
      <div className="px-3 pb-3 text-xs">
        <p className="text-slate-600 mb-2">
          What each step of the pipeline read and wrote. Opening it is recorded in the audit log.
        </p>
        {error && <p className="text-red-600">{error}</p>}
        {trace === undefined && !error && <p className="text-slate-600">Loading…</p>}
        {trace && !run && <p className="text-slate-600">This document has not been processed yet.</p>}
        {run && (
          <>
            <label className="flex items-center gap-2 mb-2">
              <span className="text-slate-600">Run</span>
              <select
                value={run.run_id}
                onChange={(e) => load(e.target.value)}
                className="border rounded px-1 py-0.5"
              >
                {trace.runs.map((r, i) => (
                  <option key={r.run_id} value={r.run_id}>
                    {i === 0 ? "Latest: " : ""}
                    {runLine(r)}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-slate-600 mb-2">
              <span className="font-mono">{run.graph_version}</span> · code {codeLine(run)}
              {run.models_used?.length > 0 && ` · ${run.models_used.join(", ")}`}
            </p>
            {run.error && <p className="mb-2 text-red-700">{run.error}</p>}
            {trace.steps.map((s) => (
              <TraceStep key={s.step_id} step={s} prompts={trace.prompts} />
            ))}
          </>
        )}
      </div>
    </details>
  );
}

export default TracePanel;

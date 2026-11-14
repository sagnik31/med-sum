// src/components/ProcessingPanel.jsx
//
// "Processing details": the document's latest run, step by step. Loaded
// when opened. See docs/adr/0006-durable-traceable-runs.md.
import React, { useState } from "react";
import { fetchProcessing } from "../api/client.js";
import { runSummary, stepRow } from "../record/processing.js";

function ProcessingPanel({ documentId }) {
  const [run, setRun] = useState(undefined); // undefined: not loaded; null: never ran
  const [error, setError] = useState(null);

  function load(e) {
    if (!e.currentTarget.open || run !== undefined) return;
    fetchProcessing(documentId)
      .then((data) => setRun(data.run))
      .catch((err) => setError(err.message || "Could not load the processing details."));
  }

  return (
    <details className="mb-4 border border-slate-200 rounded-md" onToggle={load}>
      <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-slate-700">Processing details</summary>
      <div className="px-3 pb-3 text-xs">
        {error && <p className="text-red-600">{error}</p>}
        {run === undefined && !error && <p className="text-slate-600">Loading…</p>}
        {run === null && <p className="text-slate-600">This document has not been processed yet.</p>}
        {run && (
          <>
            <p className="text-slate-600 mb-2">
              {runSummary(run)} · <span className="font-mono">{run.graph_version}</span>
            </p>
            <table className="w-full">
              <thead>
                <tr className="text-left text-slate-500 border-b">
                  <th className="py-1 pr-2">#</th>
                  <th className="py-1 pr-2">Step</th>
                  <th className="py-1 pr-2">Status</th>
                  <th className="py-1 pr-2">Model</th>
                  <th className="py-1 pr-2">Time</th>
                  <th className="py-1">Checks that failed</th>
                </tr>
              </thead>
              <tbody>
                {run.steps.map(stepRow).map((s) => (
                  <tr key={s.key} className="border-b border-slate-100 align-top">
                    <td className="py-1 pr-2">{s.key}</td>
                    <td className="py-1 pr-2">
                      {s.name}
                      {s.attempt && <span className="ml-1 text-amber-700">({s.attempt})</span>}
                    </td>
                    <td className={`py-1 pr-2 ${s.status === "failed" ? "text-red-700" : ""}`}>{s.status}</td>
                    <td className="py-1 pr-2">
                      {s.model}
                      {s.calls > 1 && <span className="text-slate-500"> · {s.calls} calls</span>}
                    </td>
                    <td className="py-1 pr-2 whitespace-nowrap">{s.duration}</td>
                    <td className="py-1">
                      {s.failures.length > 0 && (
                        <ul>
                          {s.failures.map((f, i) => (
                            <li key={i}>{f}</li>
                          ))}
                        </ul>
                      )}
                      {s.error && <p className="text-red-700">{s.error}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </details>
  );
}

export default ProcessingPanel;

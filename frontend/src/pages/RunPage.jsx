// src/pages/RunPage.jsx
//
// One pipeline run, as an admin sees it: each step's status, model, time,
// tokens and the checks that failed it (by name, never their messages),
// with the command that replays it. See docs/adr/0006-durable-traceable-runs.md.
import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchRun } from "../api/client.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import ReplayCommand from "../components/ReplayCommand.jsx";
import { stepRow } from "../record/processing.js";
import { codeLine, runLine, tokenLine } from "../record/trace.js";

function RunPage() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setError(null);
    fetchRun(id).then(setData).catch(setError);
  }, [id]);

  const run = data?.run;
  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-1">Pipeline run</h1>
      <ApiErrorNotice error={error} backTo="/admin/runs" backLabel="Back to runs" />
      {!data && !error && <p className="text-sm text-slate-600">Loading...</p>}
      {run && (
        <>
          <p className="text-sm text-slate-600 mb-1">{runLine(run)}</p>
          <p className="text-xs text-slate-500 mb-4">
            <span className="font-mono">{run.graph_version}</span> · code {codeLine(run)}
            {run.models_used?.length > 0 && ` · ${run.models_used.join(", ")}`}
            {run.has_error && <span className="ml-2 text-red-700">the run stopped on an error</span>}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-slate-500 border-b">
                  <th className="py-1 pr-2">#</th>
                  <th className="py-1 pr-2">Step</th>
                  <th className="py-1 pr-2">Status</th>
                  <th className="py-1 pr-2">Model</th>
                  <th className="py-1 pr-2">Time</th>
                  <th className="py-1 pr-2">Tokens</th>
                  <th className="py-1 pr-2">Checks that failed</th>
                  <th className="py-1">Replay</th>
                </tr>
              </thead>
              <tbody>
                {data.steps.map((st) => {
                  const s = stepRow(st);
                  return (
                    <tr key={st.step_id} className="border-b border-slate-100 align-top">
                      <td className="py-1 pr-2">{s.key}</td>
                      <td className="py-1 pr-2">
                        {s.name}
                        {s.attempt && <span className="ml-1 text-amber-700">({s.attempt})</span>}
                      </td>
                      <td className={`py-1 pr-2 ${s.status === "failed" ? "text-red-700" : ""}`}>
                        {s.status}
                        {st.has_error && " (error)"}
                      </td>
                      <td className="py-1 pr-2">
                        {s.model}
                        {s.calls > 1 && <span className="text-slate-500"> · {s.calls} calls</span>}
                      </td>
                      <td className="py-1 pr-2 whitespace-nowrap">{s.duration}</td>
                      <td className="py-1 pr-2 whitespace-nowrap">{tokenLine(st)}</td>
                      <td className="py-1 pr-2">
                        {s.failures.length > 0 && (
                          <ul>
                            {s.failures.map((f, i) => (
                              <li key={i}>{f}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td className="py-1">
                        <ReplayCommand step={st} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <Link to="/admin/runs" className="inline-block mt-4 text-blue-600 hover:underline text-sm font-medium">
        Back to runs
      </Link>
    </div>
  );
}

export default RunPage;

// src/pages/RunsPage.jsx
//
// Admins see the organisation's pipeline runs, newest first: how each went
// and, for one run, how each step went. No document, patient or content is
// shown: the API sends none. See docs/adr/0006-durable-traceable-runs.md.
import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { fetchRuns } from "../api/client.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import { codeLine, runLine } from "../record/trace.js";

function RunsPage() {
  const [runs, setRuns] = useState([]);
  const [nextBefore, setNextBefore] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async (before) => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchRuns(before);
      setRuns((prev) => (before ? [...prev, ...page.runs] : page.runs));
      setNextBefore(page.next_before);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-1">Pipeline runs</h1>
      <p className="text-sm text-slate-600 mb-4">
        How the processing of each document went in your organisation. Which document or patient a run was for is not
        shown, nor anything a step read or wrote.
      </p>

      <ApiErrorNotice error={error} />

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b">
              <th className="py-2 pr-3 font-medium">Run</th>
              <th className="py-2 pr-3 font-medium">Graph</th>
              <th className="py-2 pr-3 font-medium">Models</th>
              <th className="py-2 font-medium">Code</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} className="border-b align-top">
                <td className="py-1.5 pr-3">
                  <Link to={`/admin/runs/${r.run_id}`} className="text-blue-600 hover:underline">
                    {runLine(r)}
                  </Link>
                  {r.has_error && <span className="ml-2 text-red-700">error</span>}
                </td>
                <td className="py-1.5 pr-3 font-mono text-xs">{r.graph_version}</td>
                <td className="py-1.5 pr-3 text-xs">{(r.models_used || []).join(", ")}</td>
                <td className="py-1.5 font-mono text-xs">{codeLine(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {loading && <p className="text-sm text-slate-600 mt-3">Loading...</p>}
      {!loading && runs.length === 0 && !error && <p className="text-sm text-slate-500 mt-3">No runs yet.</p>}
      {!loading && nextBefore && (
        <button type="button" onClick={() => load(nextBefore)} className="mt-4 text-sm text-blue-600 hover:underline">
          Older runs
        </button>
      )}
    </div>
  );
}

export default RunsPage;

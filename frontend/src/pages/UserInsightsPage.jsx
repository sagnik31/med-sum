import React, { useEffect, useState } from "react";
import { fetchUserInsights, triggerUserInsights } from "../api/client";
import { Link } from "react-router-dom";
import HtmlFrame from "../components/HtmlFrame.jsx";

// How often to check while the summary is being generated.
const POLL_MS = 5000;

function UserInsightsPage() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    loadSummary();
  }, []);

  // Keep checking while a summary is being generated.
  useEffect(() => {
    if (summary?.status !== "processing") return;
    const timer = setTimeout(loadSummary, POLL_MS);
    return () => clearTimeout(timer);
  }, [summary]);

  async function loadSummary() {
    try {
      setSummary(await fetchUserInsights());
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to load the patient summary.");
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerate() {
    setStarting(true);
    setError("");
    try {
      await triggerUserInsights();
      await loadSummary();
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to start the patient summary.");
    } finally {
      setStarting(false);
    }
  }

  const status = summary?.status || "none";
  const processing = status === "processing";

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Patient Summary</h1>
        <Link
          to="/documents"
          className="text-sm text-slate-600 hover:text-slate-900 hover:underline"
        >
          &larr; Back to Documents
        </Link>
      </div>

      {loading ? (
        <p className="text-slate-600">Loading patient summary...</p>
      ) : (
        <>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm text-slate-500">
              {summary?.updated_at && status !== "none"
                ? `Last updated ${new Date(summary.updated_at).toLocaleString()}`
                : "Combines every processed document into one summary."}
            </p>
            <button
              onClick={handleGenerate}
              disabled={processing || starting}
              className="px-4 py-1.5 text-sm rounded-md bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-60"
            >
              {processing
                ? "Generating..."
                : status === "none"
                  ? "Generate summary"
                  : "Regenerate"}
            </button>
          </div>

          {error && (
            <div className="mb-4 bg-red-50 border border-red-100 rounded-md p-3 text-sm text-red-600">
              {error}
            </div>
          )}

          {processing && (
            <div className="mb-4 text-sm bg-yellow-50 border border-yellow-100 rounded-md px-3 py-2 text-yellow-800">
              Generating the summary from all processed documents. The page
              updates by itself; on a CPU this can take several minutes.
            </div>
          )}

          {status === "failed" && (
            <div className="mb-4 text-sm bg-red-50 border border-red-100 rounded-md px-3 py-2 text-red-700">
              Summary generation failed: {summary.error_message || "Unknown error."}
            </div>
          )}

          {summary?.insights_html ? (
            <HtmlFrame html={summary.insights_html} title="Patient summary" />
          ) : (
            !processing && (
              <div className="text-center py-12 bg-slate-50 rounded-lg border border-dashed border-slate-200">
                <p className="text-slate-500">No patient summary generated yet.</p>
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}

export default UserInsightsPage;

// src/pages/InsightPage.jsx
import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { getInsight, triggerInsightGeneration } from "../api/client.js";
import HtmlFrame from "../components/HtmlFrame.jsx";

// How often to check a document that is still being processed.
const POLL_MS = 5000;

function InsightPage() {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [insight, setInsight] = useState("");
  const [status, setStatus] = useState("checking");
  const [error, setError] = useState("");
  const [failure, setFailure] = useState("");
  // Bumped by Retry to restart the polling loop.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer;

    setLoading(true);
    setError("");
    setFailure("");
    setInsight("");
    setStatus("checking");

    async function check(isFirstCheck) {
      try {
        const data = await getInsight(id);
        if (cancelled) return;

        if (data?.status === "completed" && data.insights_html) {
          setInsight(data.insights_html);
          setStatus("ready");
        } else if (data?.status === "failed") {
          setFailure(data.error_message || "Unknown error.");
          setStatus("failed");
        } else {
          // Processing normally starts at upload. If no job exists (for
          // example, the AI service was down then), request one once.
          if (data?.status === "not_started" && isFirstCheck) {
            await triggerInsightGeneration(id);
          }
          if (cancelled) return;
          setStatus("generating");
          timer = setTimeout(() => check(false), POLL_MS);
        }
      } catch (err) {
        console.error(err);
        if (!cancelled) {
          setError(err.message || "Failed to fetch or generate insight.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    check(true);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id, attempt]);

  async function handleRetry() {
    setError("");
    try {
      await triggerInsightGeneration(id);
      setAttempt((n) => n + 1);
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to retry insight generation.");
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-2">Insights</h1>
      <p className="text-sm text-slate-600 mb-4">
        Document ID: <span className="font-mono">{id}</span>
      </p>

      {loading && (
        <p className="text-sm text-slate-600">Checking for insights…</p>
      )}

      {error && (
        <div className="mb-4 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {!loading && !error && status === "ready" && (
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-700 mb-2">
            Insight
          </h2>
          <div className="bg-slate-50 border border-slate-200 rounded-md">
            <HtmlFrame html={insight} title="Document insight" />
          </div>
        </div>
      )}

      {!loading && !error && status === "failed" && (
        <div className="mb-4 text-sm bg-red-50 border border-red-100 rounded-md px-3 py-2 text-red-700">
          <p>Insight generation failed: {failure}</p>
          <button
            onClick={handleRetry}
            className="mt-1 text-blue-600 hover:underline font-medium"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && status === "generating" && (
        <div className="mb-4 text-sm bg-yellow-50 border border-yellow-100 rounded-md px-3 py-2 text-yellow-800">
          This document is being processed. The page updates by itself when the
          insight is ready; on a CPU this can take a few minutes per page.
        </div>
      )}

      <div className="mt-4">
        <Link
          to="/documents"
          className="text-blue-600 hover:underline text-sm font-medium"
        >
          ← Back to Documents
        </Link>
      </div>
    </div>
  );
}

export default InsightPage;

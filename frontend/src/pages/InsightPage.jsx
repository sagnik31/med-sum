// src/pages/InsightPage.jsx
import React, { useEffect, useState, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getInsight,
  triggerInsightGeneration,
} from "../api/client.js";
import HtmlFrame from "../components/HtmlFrame.jsx";

function InsightPage() {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [insight, setInsight] = useState("");
  const [status, setStatus] = useState("checking");
  const [error, setError] = useState("");
  const [failure, setFailure] = useState("");

  // Ref to track if we've already triggered generation for this ID
  const generationTriggeredRef = useRef(null);

  useEffect(() => {
    async function loadInsight() {
      if (!id) return;

      setLoading(true);
      setError("");
      setFailure("");
      setInsight("");
      setStatus("checking");

      // Reset the trigger ref if the ID has changed
      if (generationTriggeredRef.current !== id) {
        generationTriggeredRef.current = null;
      }

      try {
        // 1) Ask Go service if insight exists
        const data = await getInsight(id);

        if (data?.insights_html) {
          // We have real insight text → show it
          setInsight(data.insights_html);
          setStatus("ready");
        } else if (data?.status === "failed") {
          // Last run failed → show why and let the user retry
          setFailure(data.error_message || "Unknown error.");
          setStatus("failed");
        } else {
          // No usable insight → trigger Python generation
          // Only trigger if we haven't already done so for this ID
          if (generationTriggeredRef.current !== id) {
            generationTriggeredRef.current = id;
            await triggerInsightGeneration(id);
          }
          setStatus("generating");
        }
      } catch (err) {
        console.error(err);
        setError(err.message || "Failed to fetch or generate insight.");
      } finally {
        setLoading(false);
      }
    }

    loadInsight();
  }, [id]);

  async function handleRetry() {
    setError("");
    try {
      await triggerInsightGeneration(id);
      setFailure("");
      setStatus("generating");
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to retry insight generation.");
    }
  }

  const isGenerating = status === "generating";

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

      {!loading && !error && insight && !isGenerating && (
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

      {!loading && !error && !insight && isGenerating && (
        <div className="mb-4 text-sm bg-yellow-50 border border-yellow-100 rounded-md px-3 py-2 text-yellow-800">
          Insights are being generated for this document. Please check again in
          some time.
        </div>
      )}

      <div className="mt-4 flex items-center justify-between">
        <Link
          to="/documents"
          className="text-blue-600 hover:underline text-sm font-medium"
        >
          ← Back to Documents
        </Link>
        <button
          onClick={() => window.location.reload()}
          className="text-xs text-slate-600 hover:underline"
        >
          Refresh
        </button>
      </div>
    </div>
  );
}

export default InsightPage;
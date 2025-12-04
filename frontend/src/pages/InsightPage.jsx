// src/pages/InsightPage.jsx
import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getInsight,
  triggerInsightGeneration,
} from "../api/client.js";

function InsightPage() {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [insight, setInsight] = useState("");
  const [status, setStatus] = useState("checking");
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadInsight() {
      if (!id) return;

      setLoading(true);
      setError("");
      setInsight("");
      setStatus("checking");

      try {
        // 1) Ask Go service if insight exists
        const result = await getInsight(id);

        let text = "";
        if (result.exists && result.data) {
          const data = result.data;

          // Try to extract text field from Go response
          // We prioritize insights_html as that's what the backend returns now
          text =
            data?.insights_html ||
            data?.insight ||
            data?.insight_text ||
            data?.summary ||
            (typeof data === "string" ? data : "");
        }

        if (result.exists && text) {
          // We have real insight text → show it
          setInsight(text);
          setStatus("ready");
        } else {
          // No usable insight → trigger Python generation
          await triggerInsightGeneration(id);
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
          <div
            className="text-sm whitespace-pre-wrap bg-slate-50 border border-slate-200 rounded-md px-3 py-2"
            dangerouslySetInnerHTML={{ __html: insight }}
          />
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
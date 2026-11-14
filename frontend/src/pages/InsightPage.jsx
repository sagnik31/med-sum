// src/pages/InsightPage.jsx
import React, { useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import {
  fetchDocument,
  fetchDocumentRecord,
  getInsight,
  reprocessDocument,
  triggerInsightGeneration,
} from "../api/client.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import HtmlFrame from "../components/HtmlFrame.jsx";
import RecordView from "../components/RecordView.jsx";
import { citationFromQuery } from "../record/recordView.js";

// How often to check a document that is still being processed.
const POLL_MS = 5000;

function InsightPage() {
  const { id } = useParams();
  const [loading, setLoading] = useState(true);
  const [insight, setInsight] = useState("");
  const [status, setStatus] = useState("checking");
  // An ApiError (with its status), or null.
  const [error, setError] = useState(null);
  const [failure, setFailure] = useState("");
  // The document: its name and its patient, for the way back.
  const [doc, setDoc] = useState(null);
  // Bumped by Retry to restart the polling loop.
  const [attempt, setAttempt] = useState(0);
  // The structured record, once the document is processed; its loading error.
  const [record, setRecord] = useState(null);
  const [recordError, setRecordError] = useState(null);
  // A link from the patient summary names the lines to show (?page=&lines=).
  const [searchParams] = useSearchParams();
  const linkedCitation = citationFromQuery(searchParams);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    fetchDocument(id)
      .then((d) => !cancelled && setDoc(d))
      .catch(() => {}); // the insight request below reports the error
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer;

    setLoading(true);
    setError(null);
    setFailure("");
    setInsight("");
    setStatus("checking");

    async function check(isFirstCheck) {
      try {
        const data = await getInsight(id);
        if (cancelled) return;

        if (
          (data?.status === "verified" || data?.status === "needs_review") &&
          data.insights_html
        ) {
          setInsight(data.insights_html);
          setStatus(data.status === "needs_review" ? "needs_review" : "ready");
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
          setError(err);
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

  // Load the record when the document is processed. A 404 means the
  // pipeline stored none (it can't happen for a finished document, but the
  // page then shows the insight alone).
  const processed = status === "ready" || status === "needs_review";
  useEffect(() => {
    if (!id || !processed) return undefined;
    let cancelled = false;
    setRecord(null);
    setRecordError(null);
    fetchDocumentRecord(id)
      .then((data) => !cancelled && setRecord(data.record))
      .catch((err) => !cancelled && err.status !== 404 && setRecordError(err));
    return () => {
      cancelled = true;
    };
  }, [id, processed, attempt]);

  async function handleReprocess() {
    setError(null);
    try {
      await reprocessDocument(id);
      setAttempt((n) => n + 1);
    } catch (err) {
      console.error(err);
      setError(err);
    }
  }

  async function handleRetry() {
    setError(null);
    try {
      await triggerInsightGeneration(id);
      setAttempt((n) => n + 1);
    } catch (err) {
      console.error(err);
      setError(err);
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-2">{doc?.original_name || "Document"}</h1>
      <p className="text-sm text-slate-600 mb-4">
        {doc?.uploaded_by_name && <>Uploaded by {doc.uploaded_by_name} · </>}
        Document ID: <span className="font-mono">{id}</span>
      </p>

      {loading && (
        <p className="text-sm text-slate-600">Checking for insights…</p>
      )}

      <ApiErrorNotice error={error} />

      {!loading && !error && status === "needs_review" && (
        <div className="mb-4 text-sm bg-amber-50 border border-amber-100 rounded-md px-3 py-2 text-amber-800">
          Some values in this document were flagged for review by a clinician.
          Check them against the source document before relying on them.
        </div>
      )}

      {!loading && !error && (status === "ready" || status === "needs_review") && (
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-700 mb-2">
            Insight
          </h2>
          <div className="bg-slate-50 border border-slate-200 rounded-md">
            <HtmlFrame html={insight} title="Document insight" />
          </div>
          <button
            onClick={handleReprocess}
            className="mt-2 text-sm text-blue-600 hover:underline font-medium"
          >
            Reprocess
          </button>
        </div>
      )}

      {!loading && !error && processed && (
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Values and where they are printed</h2>
          <ApiErrorNotice error={recordError} />
          {!record && !recordError && <p className="text-sm text-slate-600">Loading the record…</p>}
          {record && (
            // Keyed by the link, so following another source link to this
            // page selects its lines.
            <RecordView key={searchParams.toString()} record={record} initialCitation={linkedCitation} />
          )}
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
          to={doc ? `/patients/${doc.patient_id}` : "/patients"}
          className="text-blue-600 hover:underline text-sm font-medium"
        >
          {doc ? "← Back to the patient" : "← Back to patients"}
        </Link>
      </div>
    </div>
  );
}

export default InsightPage;

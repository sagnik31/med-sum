// src/pages/InsightPage.jsx
import React, { useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import {
  completeAudit,
  fetchDocument,
  fetchDocumentRecord,
  getInsight,
  reprocessDocument,
  reviewDocument,
  triggerInsightGeneration,
} from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { PERM, can } from "../auth/permissions.js";
import { flaggedCount, isPdfName, reprocessQuestion, reviewOutcome } from "../record/review.js";
import OriginalFile from "../components/OriginalFile.jsx";
import ProcessingPanel from "../components/ProcessingPanel.jsx";
import TracePanel from "../components/TracePanel.jsx";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import HtmlFrame from "../components/HtmlFrame.jsx";
import RecordView from "../components/RecordView.jsx";
import { citationFromQuery, reviewedRecordOf } from "../record/recordView.js";

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
  const [recordUpdatedAt, setRecordUpdatedAt] = useState(null);
  const [recordError, setRecordError] = useState(null);
  // Review mode shows the confirm and correct controls; a clinician with
  // review.write turns it on. Bumped by a saved review to reload.
  const { user } = useAuth();
  const canReview = can(user, PERM.reviewWrite);
  const canReprocess = can(user, PERM.documentReprocess);
  const [reviewing, setReviewing] = useState(searchParamsHasReview());
  const [reviewed, setReviewed] = useState(0);
  // True while a review is being saved: every review control waits, so a
  // second save never goes out with the record's old updated_at.
  const [saving, setSaving] = useState(false);
  // An audit sample (the queue links with &audit=1): a verified document a
  // clinician checks value by value, then marks finished.
  const [auditing, setAuditing] = useState(searchParamsHas("audit"));
  const [auditError, setAuditError] = useState(null);
  const [auditDone, setAuditDone] = useState(false);
  // The original upload beside the transcription; always on in review mode.
  const [showOriginal, setShowOriginal] = useState(false);
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
  // Another document: drop the last one's record.
  useEffect(() => {
    setRecord(null);
    setRecordUpdatedAt(null);
  }, [id]);
  // A reload after a review keeps the list on screen until the new record
  // arrives, so the scroll position, the selected source and any open
  // correction stay where they were.
  useEffect(() => {
    if (!id || !processed) return undefined;
    let cancelled = false;
    setRecordError(null);
    fetchDocumentRecord(id)
      .then((data) => {
        if (cancelled) return;
        // The reviewed view, never the stored record: after a correction
        // the stored one still holds the misread value and its flag.
        const view = reviewedRecordOf(data);
        if (!view) throw new Error("The record came back without its reviewed values.");
        setRecord(view);
        setRecordUpdatedAt(data.updated_at);
      })
      .catch((err) => !cancelled && err?.status !== 404 && setRecordError(err));
    return () => {
      cancelled = true;
    };
  }, [id, processed, attempt, reviewed]);

  // Save one review, then load the record and the page again. Only the
  // save itself can fail the review: a ReviewFindings error is left to the
  // controls to show, and once the save is done a failed refresh of the
  // page is not reported as a failed save.
  async function handleReview(review, { acceptFindings }) {
    setSaving(true);
    try {
      const result = await reviewDocument(id, review, { recordUpdatedAt, acceptFindings });
      const outcome = reviewOutcome(result, status);
      setStatus(outcome.status);
      if (outcome.recordUpdatedAt) setRecordUpdatedAt(outcome.recordUpdatedAt);
    } finally {
      setSaving(false);
    }
    setReviewed((n) => n + 1);
    getInsight(id)
      .then((data) => data?.insights_html && setInsight(data.insights_html))
      .catch((err) => console.error(err)); // the next load shows it
  }

  async function handleFinishAudit() {
    setAuditError(null);
    try {
      await completeAudit(id);
      setAuditing(false);
      setAuditDone(true);
    } catch (err) {
      setAuditError(err);
    }
  }

  async function handleReprocess() {
    if (!window.confirm(reprocessQuestion(record))) return;
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
          {record && <> {flaggedCount(record)} still await review.</>}
        </div>
      )}

      {!loading && !error && processed && canReview && record && (
        <div className="mb-4 flex items-center gap-3 text-sm">
          <button
            type="button"
            onClick={() => setReviewing((r) => !r)}
            aria-pressed={reviewing}
            className={`rounded px-3 py-1 border font-medium ${
              reviewing ? "bg-blue-600 text-white border-blue-600" : "bg-white text-blue-700 border-blue-300"
            }`}
          >
            {reviewing ? "Done reviewing" : "Review this document"}
          </button>
          {reviewing && (
            <span className="text-slate-600">
              Confirm each value that matches the original, or correct it. What was read is kept beside
              every correction.
            </span>
          )}
          {auditing && (
            <button
              type="button"
              onClick={handleFinishAudit}
              className="rounded px-3 py-1 border font-medium bg-white text-emerald-700 border-emerald-300"
            >
              Finish audit
            </button>
          )}
        </div>
      )}

      {auditing && (
        <p className="mb-4 text-sm bg-sky-50 border border-sky-100 rounded-md px-3 py-2 text-sky-800">
          This verified document was picked at random for an audit. Check every value against the original:
          confirm each one that matches and correct any that are wrong. The audit can be finished once every
          value is confirmed or corrected.
        </p>
      )}
      {auditDone && (
        <p className="mb-4 text-sm bg-emerald-50 border border-emerald-100 rounded-md px-3 py-2 text-emerald-800">
          Audit finished. The document has left the review queue.
        </p>
      )}
      <ApiErrorNotice error={auditError} />

      {!loading && !error && (status === "ready" || status === "needs_review") && (
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-700 mb-2">
            Insight
          </h2>
          <div className="bg-slate-50 border border-slate-200 rounded-md">
            <HtmlFrame html={insight} title="Document insight" />
          </div>
          {/* Not while reviewing: a new run discards the reviews. */}
          {canReprocess && !reviewing && (
            <button
              type="button"
              onClick={handleReprocess}
              className="mt-2 text-sm text-blue-600 hover:underline font-medium"
            >
              Reprocess
            </button>
          )}
        </div>
      )}

      {!loading && !error && processed && (
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-slate-700">Values and where they are printed</h2>
            {!reviewing && (
              <button
                type="button"
                onClick={() => setShowOriginal((v) => !v)}
                aria-pressed={showOriginal}
                className="text-sm text-blue-600 hover:underline"
              >
                {showOriginal ? "Hide the original" : "Show the original upload"}
              </button>
            )}
          </div>
          <ApiErrorNotice error={recordError} />
          {!record && !recordError && <p className="text-sm text-slate-600">Loading the record…</p>}
          {record && (
            // Keyed by the link, so following another source link to this
            // page selects its lines.
            <RecordView
              key={searchParams.toString()}
              record={record}
              initialCitation={linkedCitation}
              onReview={reviewing && canReview ? handleReview : undefined}
              saving={saving}
              sourceSlot={
                reviewing || showOriginal
                  ? (page) => <OriginalFile documentId={id} isPdf={isPdfName(doc?.original_name)} page={page} />
                  : undefined
              }
            />
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

      {!loading && !error && status !== "checking" && (
        <>
          <ProcessingPanel key={`${attempt}-${reviewed}`} documentId={id} />
          <TracePanel key={`trace-${attempt}-${reviewed}`} documentId={id} />
        </>
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

// The review queue links here with ?review=1, opening in review mode
// (and &audit=1 for an audit sample).
function searchParamsHasReview() {
  return searchParamsHas("review");
}

function searchParamsHas(name) {
  return new URLSearchParams(window.location.search).get(name) === "1";
}

export default InsightPage;

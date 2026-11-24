// src/pages/InsightPage.jsx
import React, { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router";
import {
  completeAudit,
  fetchDocument,
  fetchDocumentRecord,
  getInsight,
  isTransient,
  pollDelay,
  reprocessDocument,
  reviewDocument,
  signOffDocument,
  triggerInsightGeneration,
} from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { PERM, can } from "../auth/permissions.js";
import {
  auditProgress,
  flaggedCount,
  isPdfName,
  pageStatus,
  reprocessQuestion,
  statusAfterReview,
} from "../record/review.js";
import OriginalFile from "../components/OriginalFile.jsx";
import ProcessingPanel from "../components/ProcessingPanel.jsx";
import TracePanel from "../components/TracePanel.jsx";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import HtmlFrame from "../components/HtmlFrame.jsx";
import RecordView from "../components/RecordView.jsx";
import { citationFromQuery, reviewedRecordOf } from "../record/recordView.js";

// How often to check a document that is still being processed.
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
  // True while a review is being saved, and then while the values it
  // changed are read again: every review control waits, so a clinician
  // never reviews or signs off values older than the ones on record.
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // An audit sample: a verified document a clinician checks value by
  // value, then marks finished. Audit mode comes from the record's own
  // audit state (null when no audit is open), so it shows however the
  // document was opened, and each load says which values are left.
  const [audit, setAudit] = useState(null);
  const [auditError, setAuditError] = useState(null);
  const [auditDone, setAuditDone] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [signingOff, setSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState(null);
  const busy = saving || refreshing || finishing || signingOff;
  // Counts the requests for the page's HTML after a review, so that only
  // the latest one is shown.
  const htmlRequest = useRef(0);
  const auditing = audit !== null && !auditDone;
  const auditLeft = audit?.unreviewed?.length ?? 0;
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

    htmlRequest.current += 1;
    setLoading(true);
    setError(null);
    setFailure("");
    setInsight("");
    setStatus("checking");

    // Every check after the first is a background poll: it doesn't keep
    // the session alive, comes less often as it goes on, and carries on
    // through a transient error.
    let polls = 0;
    const again = () => {
      timer = setTimeout(() => check(false), pollDelay(polls));
      polls += 1;
    };

    async function check(isFirstCheck) {
      try {
        const data = await getInsight(id, { background: !isFirstCheck });
        if (cancelled) return;

        if (pageStatus(data?.status) && data.insights_html) {
          setInsight(data.insights_html);
          setStatus(pageStatus(data.status));
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
          again();
        }
      } catch (err) {
        console.error(err);
        if (cancelled) return;
        if (!isFirstCheck && isTransient(err)) again();
        else setError(err);
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
  const processed = status === "ready" || status === "needs_review" || status === "checks_passed";
  // Another document, or a new run of this one: drop the old record and
  // its audit.
  useEffect(() => {
    setRecord(null);
    setRecordUpdatedAt(null);
    setAudit(null);
    setAuditDone(false);
    setAuditError(null);
    setSignOffError(null);
    setRefreshing(false);
  }, [id, attempt]);
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
        setAudit(data.audit ?? null);
        setRefreshing(false);
      })
      .catch((err) => !cancelled && (err?.status !== 404 || reviewed > 0) && setRecordError(err));
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
      setStatus(statusAfterReview(result, status));
      setRefreshing(true); // until the record loads again
    } finally {
      setSaving(false);
    }
    setReviewed((n) => n + 1);
    htmlRequest.current += 1;
    const request = htmlRequest.current;
    getInsight(id)
      .then((data) => {
        if (data?.insights_html && request === htmlRequest.current) setInsight(data.insights_html);
      })
      .catch((err) => console.error(err)); // the next load shows it
  }

  async function handleFinishAudit() {
    setAuditError(null);
    setFinishing(true);
    try {
      await completeAudit(id);
      setAuditDone(true);
      setStatus("ready"); // finishing the audit signs the document off
    } catch (err) {
      setAuditError(err);
    } finally {
      setFinishing(false);
    }
  }

  // A document that passed every check is verified only by a clinician's
  // sign-off (docs/adr/0013-model-choice-after-the-first-live-evals.md),
  // which is of the record on screen: the button waits for it to load.
  async function handleSignOff() {
    setSignOffError(null);
    setSigningOff(true);
    try {
      await signOffDocument(id, { recordUpdatedAt });
      setStatus("ready");
    } catch (err) {
      setSignOffError(err);
    } finally {
      setSigningOff(false);
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

      {!loading && !error && status === "checks_passed" && (
        <div className="mb-4 text-sm bg-sky-50 border border-sky-100 rounded-md px-3 py-2 text-sky-800">
          Every value in this document passed its checks, but it is not verified until a clinician signs it
          off: the models have not yet passed the safety gate, and the first evaluations found wrong values
          that passed every check. Read it against the source document before relying on it.
          {canReview && !auditing && record && (
            <div className="mt-2">
              <button
                type="button"
                onClick={handleSignOff}
                disabled={busy}
                className="rounded px-3 py-1 border font-medium bg-white text-emerald-700 border-emerald-300 disabled:opacity-50"
              >
                Sign off as verified
              </button>
            </div>
          )}
        </div>
      )}
      <ApiErrorNotice error={signOffError} />

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
              disabled={auditLeft > 0 || busy}
              className="rounded px-3 py-1 border font-medium bg-white text-emerald-700 border-emerald-300 disabled:opacity-50"
            >
              Finish audit
            </button>
          )}
        </div>
      )}

      {auditing && (
        <p className="mb-4 text-sm bg-sky-50 border border-sky-100 rounded-md px-3 py-2 text-sky-800">
          This document passed every check and is an audit sample: a share of such runs is set aside, by a
          fixed rule on the run, to measure what the checks let through. Check every value against the original:
          confirm each one that matches and correct any that are wrong. The audit can be finished once every
          value is confirmed or corrected, which also signs it off.{" "}
          <strong>{auditProgress(audit)}</strong>
        </p>
      )}
      {auditDone && (
        <p className="mb-4 text-sm bg-emerald-50 border border-emerald-100 rounded-md px-3 py-2 text-emerald-800">
          Audit finished. The document is verified and has left the review queue.
        </p>
      )}
      <ApiErrorNotice error={auditError} />

      {!loading && !error && processed && (
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
              disabled={busy}
              className="mt-2 text-sm text-blue-600 hover:underline font-medium disabled:opacity-50"
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
          {recordError && refreshing && (
            <p className="mb-2 text-sm text-amber-800">
              The review was saved, but these values are from before it.{" "}
              <button
                type="button"
                onClick={() => setReviewed((n) => n + 1)}
                className="text-blue-600 hover:underline font-medium"
              >
                Load the values again
              </button>
            </p>
          )}
          {!record && !recordError && <p className="text-sm text-slate-600">Loading the record…</p>}
          {record && (
            // Keyed by the link, so following another source link to this
            // page selects its lines.
            <RecordView
              key={searchParams.toString()}
              record={record}
              initialCitation={linkedCitation}
              onReview={reviewing && canReview ? handleReview : undefined}
              saving={busy}
              audit={auditing ? audit : null}
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

// The review queue links here with ?review=1, opening in review mode.
function searchParamsHasReview() {
  return new URLSearchParams(window.location.search).get("review") === "1";
}

export default InsightPage;

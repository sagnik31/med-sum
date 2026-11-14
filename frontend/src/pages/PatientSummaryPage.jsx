// src/pages/PatientSummaryPage.jsx
import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { fetchPatient, fetchPatientSummary, triggerPatientSummary } from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { PERM, can } from "../auth/permissions.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import HtmlFrame from "../components/HtmlFrame.jsx";
import { citeLink } from "../record/recordView.js";

// How often to check while the summary is being generated.
const POLL_MS = 5000;

// One patient's cumulative summary, built from their processed documents.
function PatientSummaryPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [patient, setPatient] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await fetchPatientSummary(id));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchPatient(id).then(setPatient).catch(() => {}); // loadSummary reports errors
    loadSummary();
  }, [id, loadSummary]);

  // Keep checking while a summary is being generated.
  useEffect(() => {
    if (summary?.status !== "processing") return;
    const timer = setTimeout(loadSummary, POLL_MS);
    return () => clearTimeout(timer);
  }, [summary, loadSummary]);

  async function handleGenerate() {
    setStarting(true);
    setError(null);
    try {
      await triggerPatientSummary(id);
      await loadSummary();
    } catch (err) {
      setError(err);
    } finally {
      setStarting(false);
    }
  }

  const status = summary?.status || "none";
  const processing = status === "processing";

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">
          Patient summary{patient && <span className="text-slate-500 font-normal">: {patient.full_name}</span>}
        </h1>
        <Link
          to={`/patients/${id}`}
          className="text-sm text-slate-600 hover:text-slate-900 hover:underline"
        >
          &larr; Back to the patient
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
              hidden={!can(user, PERM.summaryGenerate)}
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

          <ApiErrorNotice error={error} backTo="/patients" />

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
            <HtmlFrame
              html={summary.insights_html}
              title="Patient summary"
              onCite={(cite) => {
                // A source reference opens its document at the cited lines.
                const path = citeLink(cite);
                if (path) navigate(path);
              }}
            />
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

export default PatientSummaryPage;

// src/pages/PatientPage.jsx
import React, { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import {
  deleteDocument,
  fetchPatient,
  fetchPatientDocuments,
  isTransient,
  pollDelay,
  updatePatient,
  uploadDocument,
} from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { PERM, can } from "../auth/permissions.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import PatientForm from "../components/PatientForm.jsx";
import { describePatient } from "../patients.js";

const STATUS_BADGES = {
  not_started: { label: "Not started", className: "bg-slate-100 text-slate-600" },
  processing: { label: "Processing", className: "bg-yellow-100 text-yellow-800" },
  checks_passed: { label: "Checks passed, awaiting sign-off", className: "bg-sky-100 text-sky-800" },
  verified: { label: "Verified", className: "bg-emerald-100 text-emerald-800" },
  needs_review: { label: "Needs review", className: "bg-amber-100 text-amber-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-700" },
};

// Processing starts at upload, so a new document may briefly have no job yet.
const isPending = (doc) =>
  doc.insight_status === "processing" || doc.insight_status === "not_started";

// One patient: details (editable), documents (upload, open, delete your
// own), and the way to the patient summary.
function PatientPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const [patient, setPatient] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null); // the patient couldn't be loaded
  const [error, setError] = useState(null); // an action failed
  const [editing, setEditing] = useState(false);
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);

  const loadDocuments = useCallback(async () => {
    try {
      const data = await fetchPatientDocuments(id);
      setDocuments(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchPatient(id)
      .then(async (p) => {
        if (cancelled) return;
        setPatient(p);
        await loadDocuments();
      })
      .catch((err) => !cancelled && setLoadError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [id, loadDocuments]);

  // Refresh quietly while any document is still being processed: a
  // background poll (it doesn't keep the session alive), less often as it
  // goes on, and kept going through a transient error.
  const [polls, setPolls] = useState(0);
  const pending = documents.some(isPending);
  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const data = await fetchPatientDocuments(id, { background: true });
        if (!cancelled) setDocuments(Array.isArray(data) ? data : []);
      } catch (err) {
        if (!isTransient(err)) {
          if (!cancelled) setError(err);
          return;
        }
      }
      if (!cancelled) setPolls((n) => n + 1);
    }, pollDelay(polls));
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id, pending, polls]);

  async function handleSave(details) {
    setPatient(await updatePatient(id, details));
    setEditing(false);
  }

  async function handleUpload(e) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      await uploadDocument(id, file);
      setFile(null);
      e.target.reset();
      setPolls(0); // a new document: poll often again
      await loadDocuments();
    } catch (err) {
      setError(err);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(doc) {
    if (!window.confirm(`Delete ${doc.original_name}? It will no longer appear for this patient.`)) {
      return;
    }
    setError(null);
    try {
      await deleteDocument(doc.id);
      setDocuments((docs) => docs.filter((d) => d.id !== doc.id));
    } catch (err) {
      setError(err);
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-600">Loading patient...</p>;
  }
  if (loadError) {
    return (
      <div className="bg-white rounded-xl shadow p-6">
        <ApiErrorNotice error={loadError} backTo="/patients" />
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <Link to="/patients" className="text-sm text-slate-600 hover:underline">
        &larr; Patients
      </Link>

      {editing ? (
        <div className="my-4 border rounded-md p-4">
          <PatientForm initial={patient} submitLabel="Save" onSubmit={handleSave} onCancel={() => setEditing(false)} />
        </div>
      ) : (
        <div className="flex items-start justify-between my-4">
          <div>
            <h1 className="text-xl font-semibold">{patient.full_name}</h1>
            <p className="text-sm text-slate-600">
              MRN <span className="font-mono">{patient.mrn}</span>
              {describePatient(patient) && <> · {describePatient(patient)}</>}
            </p>
          </div>
          {can(user, PERM.patientWrite) && (
            <button type="button" onClick={() => setEditing(true)} className="text-sm text-blue-600 hover:underline">
              Edit details
            </button>
          )}
        </div>
      )}

      <ApiErrorNotice error={error} />

      {can(user, PERM.documentUpload) && (
        <form onSubmit={handleUpload} className="mb-6 flex items-center gap-3">
          <input
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            aria-label="Document to upload"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            className="block text-sm text-slate-700 file:mr-4 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-slate-100 file:text-slate-700 hover:file:bg-slate-200"
          />
          <button
            type="submit"
            disabled={uploading || !file}
            className="px-4 py-1.5 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60"
          >
            {uploading ? "Uploading..." : "Upload"}
          </button>
        </form>
      )}

      <h2 className="text-sm font-semibold text-slate-700 mb-2">Documents</h2>
      {documents.length === 0 ? (
        <p className="text-sm text-slate-500">No documents for this patient yet.</p>
      ) : (
        <ul className="space-y-2">
          {documents.map((doc) => {
            const badge = STATUS_BADGES[doc.insight_status] || STATUS_BADGES.not_started;
            const mine = doc.uploaded_by === user.id;
            return (
              <li key={doc.id} className="flex items-center justify-between text-sm border rounded-md px-3 py-2">
                <div className="flex flex-col">
                  <span className="font-medium text-slate-800">{doc.original_name}</span>
                  <span className="text-xs text-slate-500">
                    {new Date(doc.uploaded_at).toLocaleString()}
                    {doc.uploaded_by_name && <> · uploaded by {mine ? "you" : doc.uploaded_by_name}</>}
                    {doc.duplicate_of && <> · same file as an earlier upload</>}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded ${badge.className}`}>{badge.label}</span>
                  <Link to={`/insights/${doc.id}`} className="text-blue-600 hover:underline text-xs font-medium">
                    Open
                  </Link>
                  {mine && can(user, PERM.documentDelete) && (
                    <button onClick={() => handleDelete(doc)} className="text-red-600 hover:underline text-xs font-medium">
                      Delete
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {can(user, PERM.summaryRead) && (
        <div className="mt-8 pt-6 border-t">
          <Link
            to={`/patients/${id}/summary`}
            className="block w-full text-center px-4 py-2 bg-indigo-600 text-white rounded-md font-medium hover:bg-indigo-700 transition-colors"
          >
            Patient summary
          </Link>
        </div>
      )}
    </div>
  );
}

export default PatientPage;

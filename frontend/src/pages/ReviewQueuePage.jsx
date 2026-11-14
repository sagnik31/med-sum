// src/pages/ReviewQueuePage.jsx
//
// The organisation's documents waiting for a clinician, longest waiting
// first, each with how many of its values await review. Any clinician of
// the organisation may review any of them. See docs/adr/0011-clinician-review.md.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchReviewQueue } from "../api/client.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import { queueLabel, reviewLink } from "../record/review.js";

const TYPE_LABELS = { lab_report: "Lab report", prescription: "Prescription", other: "Other" };

function ReviewQueuePage() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchReviewQueue()
      .then((data) => !cancelled && setEntries(data.documents))
      .catch((err) => !cancelled && setError(err));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-1">Review queue</h1>
      <p className="text-sm text-slate-600 mb-4">
        Documents with values the checks could not confirm, longest waiting first. Open one, compare each
        flagged value with the original upload, then confirm or correct it.
      </p>
      <ApiErrorNotice error={error} />
      {!entries && !error && <p className="text-sm text-slate-600">Loading…</p>}
      {entries && entries.length === 0 && <p className="text-sm text-slate-600">Nothing is waiting for review.</p>}
      {entries && entries.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b">
              <th className="py-1 pr-2">Waiting since</th>
              <th className="py-1 pr-2">Patient</th>
              <th className="py-1 pr-2">Document</th>
              <th className="py-1">To review</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.document_id} className="border-b border-slate-100 align-top">
                <td className="py-2 pr-2 whitespace-nowrap">{(e.queued_at || "").slice(0, 10)}</td>
                <td className="py-2 pr-2">
                  <Link to={`/patients/${e.patient_id}`} className="text-blue-600 hover:underline">
                    {e.patient_name}
                  </Link>
                  <div className="text-xs text-slate-500 font-mono">{e.patient_mrn}</div>
                </td>
                <td className="py-2 pr-2">
                  <Link to={reviewLink(e)} className="text-blue-600 hover:underline">
                    {e.original_name}
                  </Link>
                  <div className="text-xs text-slate-500">
                    {TYPE_LABELS[e.document_type] || "Not yet read"}
                    {e.document_date ? ` · ${e.document_date}` : " · undated"}
                  </div>
                </td>
                <td className={`py-2 ${e.reason === "audit" ? "text-purple-700" : "text-amber-800"}`}>
                  {queueLabel(e)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default ReviewQueuePage;

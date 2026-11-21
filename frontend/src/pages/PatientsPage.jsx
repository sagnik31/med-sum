// src/pages/PatientsPage.jsx
import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { createPatient, fetchPatients } from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { PERM, can } from "../auth/permissions.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";
import PatientForm from "../components/PatientForm.jsx";
import { describePatient } from "../patients.js";

// Waits this long after the last keystroke before searching.
const SEARCH_DELAY_MS = 250;

// The organisation's patients: search by name or MRN, open one, or add one.
function PatientsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [patients, setPatients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const data = await fetchPatients(query.trim());
        if (!cancelled) {
          setPatients(Array.isArray(data) ? data : []);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  async function handleCreate(details) {
    const patient = await createPatient(details);
    navigate(`/patients/${patient.id}`);
  }

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Patients</h1>
        {can(user, PERM.patientWrite) && !adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="px-4 py-1.5 text-sm rounded-md bg-slate-900 text-white"
          >
            New patient
          </button>
        )}
      </div>

      {adding && (
        <div className="mb-6 border rounded-md p-4">
          <h2 className="text-sm font-semibold text-slate-700 mb-3">New patient</h2>
          <PatientForm submitLabel="Create patient" onSubmit={handleCreate} onCancel={() => setAdding(false)} />
        </div>
      )}

      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search by name or MRN"
        aria-label="Search patients"
        maxLength={100}
        className="w-full border rounded-md px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-slate-300"
      />

      <ApiErrorNotice error={error} />

      {loading ? (
        <p className="text-sm text-slate-600">Loading patients...</p>
      ) : patients.length === 0 ? (
        <p className="text-sm text-slate-500">
          {query.trim() ? "No patient matches this search." : "No patients yet."}
        </p>
      ) : (
        <ul className="divide-y border rounded-md">
          {patients.map((p) => (
            <li key={p.id}>
              <Link to={`/patients/${p.id}`} className="flex items-center justify-between px-3 py-2 text-sm hover:bg-slate-50">
                <span className="font-medium text-slate-800">{p.full_name}</span>
                <span className="text-slate-500">
                  <span className="font-mono">{p.mrn}</span>
                  {describePatient(p) && <> · {describePatient(p)}</>}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default PatientsPage;

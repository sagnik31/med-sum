// src/components/PatientForm.jsx
import React, { useState } from "react";

const inputClass =
  "w-full border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300";

// A patient's details. The MRN (the hospital's patient ID) is required:
// the pipeline checks the ID printed on each document against it. Give a
// date of birth, or an age when that is all that is known. onSubmit gets
// every field (null for an empty one), with the updated_at of the details the
// form opened with when it edits a patient, and may throw an ApiError, whose
// message is shown.
function PatientForm({ initial = {}, submitLabel, onSubmit, onCancel }) {
  const [mrn, setMrn] = useState(initial.mrn || "");
  const [name, setName] = useState(initial.full_name || "");
  const [born, setBorn] = useState(initial.date_of_birth || "");
  const [age, setAge] = useState(initial.age_years != null ? String(initial.age_years) : "");
  const [sex, setSex] = useState(initial.sex || "");
  // Kept from when the form opened: the API refuses to save over a change
  // made since.
  const [openedAt] = useState(initial.updated_at);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (!mrn.trim() || !name.trim()) {
      setError("The MRN and the name are required.");
      return;
    }
    if (born && age) {
      setError("Give a date of birth or an age, not both.");
      return;
    }
    setSaving(true);
    try {
      await onSubmit({
        mrn: mrn.trim(),
        full_name: name.trim(),
        date_of_birth: born || null,
        age_years: age ? Number(age) : null,
        sex: sex || null,
        updated_at: openedAt,
      });
    } catch (err) {
      setError(err.message || "Could not save the patient.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
      <label className="block">
        <span className="block text-slate-700 mb-1">MRN</span>
        <input value={mrn} onChange={(e) => setMrn(e.target.value)} maxLength={64} required className={inputClass} />
      </label>
      <label className="block">
        <span className="block text-slate-700 mb-1">Full name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required className={inputClass} />
      </label>
      <label className="block">
        <span className="block text-slate-700 mb-1">Date of birth</span>
        <input type="date" value={born} onChange={(e) => setBorn(e.target.value)} className={inputClass} />
      </label>
      <label className="block">
        <span className="block text-slate-700 mb-1">Age in years (if no date of birth)</span>
        <input type="number" min={0} max={130} value={age} onChange={(e) => setAge(e.target.value)} className={inputClass} />
      </label>
      <label className="block">
        <span className="block text-slate-700 mb-1">Sex</span>
        <select value={sex} onChange={(e) => setSex(e.target.value)} className={inputClass}>
          <option value="">Not recorded</option>
          <option value="F">Female</option>
          <option value="M">Male</option>
          <option value="O">Other</option>
        </select>
      </label>
      <div className="sm:col-span-2 flex items-center gap-3">
        <button type="submit" disabled={saving} className="px-4 py-1.5 rounded-md bg-slate-900 text-white disabled:opacity-60">
          {saving ? "Saving..." : submitLabel}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="text-slate-600 hover:underline">
            Cancel
          </button>
        )}
        {error && <span className="text-red-600">{error}</span>}
      </div>
    </form>
  );
}

export default PatientForm;

// src/patients.js

const SEX_LABELS = { F: "Female", M: "Male", O: "Other" };

// The details a form changed: only the fields that differ from the patient
// it was opened on (null for a cleared one).
export function changedDetails(patient, details) {
  return Object.fromEntries(
    Object.entries(details).filter(([field, value]) => value !== (patient[field] ?? null))
  );
}

// "Female · born 1967-04-12", "Male · 46 years", or "" when neither is known.
export function describePatient(p) {
  const parts = [];
  if (p.sex) parts.push(SEX_LABELS[p.sex] || p.sex);
  if (p.date_of_birth) parts.push(`born ${p.date_of_birth}`);
  else if (p.age_years != null) parts.push(`${p.age_years} years`);
  return parts.join(" · ");
}

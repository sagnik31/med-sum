// src/patients.js

const SEX_LABELS = { F: "Female", M: "Male", O: "Other" };

// "Female · born 1967-04-12", "Male · 46 years", or "" when neither is known.
export function describePatient(p) {
  const parts = [];
  if (p.sex) parts.push(SEX_LABELS[p.sex] || p.sex);
  if (p.date_of_birth) parts.push(`born ${p.date_of_birth}`);
  else if (p.age_years != null) parts.push(`${p.age_years} years`);
  return parts.join(" · ");
}

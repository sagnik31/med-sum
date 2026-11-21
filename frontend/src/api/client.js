// src/api/client.js
//
// Every call goes through apiRequest. The session lives in an HttpOnly
// cookie that this code never sees; the browser sends it. In development,
// Vite serves the app and proxies /api to the Go server, so the cookie is
// first-party (see vite.config.js).

import {
  ApiError,
  ReviewFindings,
  apiRequest as requestAt,
  findingsOf,
  isTransient,
  onSessionExpiry,
  onUnauthorized,
  pollDelay,
} from "./request.js";

export { ApiError, ReviewFindings, isTransient, onSessionExpiry, onUnauthorized, pollDelay };

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const apiRequest = (path, options) => requestAt(API_BASE_URL, path, options);

// ---------- Auth API ----------

// The logged-in account, or null when there is no live session.
export async function fetchMe() {
  try {
    return await apiRequest("/auth/me", { signalUnauthorized: false });
  } catch (err) {
    if (err.status === 401) return null;
    throw err;
  }
}

// A failed login is a 401 too, but not a lost session: no event.
export async function login(email, password) {
  try {
    return await apiRequest("/auth/login", {
      method: "POST",
      json: { email, password },
      signalUnauthorized: false,
    });
  } catch (err) {
    // Show the server's message without the "Unauthorized:" prefix.
    if (err.status === 401) throw new ApiError(401, err.message.replace(/^Unauthorized: /, ""));
    throw err;
  }
}

export function logout() {
  return apiRequest("/auth/logout", { method: "POST", signalUnauthorized: false });
}

export function changePassword(currentPassword, newPassword) {
  return apiRequest("/auth/password", {
    method: "POST",
    json: { current_password: currentPassword, new_password: newPassword },
  });
}

// Whose account a setup or reset link is for.
export function fetchSetupInfo(token) {
  return apiRequest("/auth/setup/info", {
    method: "POST",
    json: { token },
    signalUnauthorized: false,
  });
}

export function setPasswordWithLink(token, password) {
  return apiRequest("/auth/setup", {
    method: "POST",
    json: { token, password },
    signalUnauthorized: false,
  });
}

// Your own sessions: [{id, current, created_at, last_seen_at, expires_at, ip, user_agent}]
export function fetchSessions() {
  return apiRequest("/auth/sessions");
}

// Ends every session of yours except this one.
export function endOtherSessions() {
  return apiRequest("/auth/sessions", { method: "DELETE" });
}

export function endSession(id) {
  return apiRequest(`/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ---------- Staff API (admins) ----------

export function fetchStaff() {
  return apiRequest("/admin/users");
}

// Returns { user, link: { purpose, url, expires_at } }.
export function createStaff({ email, fullName, registrationNo, roles }) {
  return apiRequest("/admin/users", {
    method: "POST",
    json: { email, full_name: fullName, registration_no: registrationNo, roles },
  });
}

// changes: any of { roles, status: "active" | "disabled", full_name, registration_no }
export function updateStaff(id, changes) {
  return apiRequest(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", json: changes });
}

// A new setup link (invited account) or reset link (active account).
export function issueStaffLink(id) {
  return apiRequest(`/admin/users/${encodeURIComponent(id)}/reset-link`, { method: "POST" });
}

// The organisation's audit log, newest first: { events, next_before }.
// filters: any of { actor, patient, from, to (YYYY-MM-DD), before }.
export function fetchAuditEvents(filters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      params.set(key, String(value).trim());
    }
  }
  const query = params.toString();
  return apiRequest(`/admin/audit${query ? `?${query}` : ""}`);
}

// The organisation's pipeline runs, newest first, with no content:
// { runs, next_before }. Pass next_before as before for older runs.
export function fetchRuns(before) {
  return apiRequest(`/admin/runs${before ? `?before=${encodeURIComponent(before)}` : ""}`);
}

// One of the organisation's runs and how each step went: { run, steps }.
export function fetchRun(id) {
  return apiRequest(`/admin/runs/${encodeURIComponent(id)}`);
}

// ---------- Patients API ----------
//
// Patients are records, not accounts. Every clinician of the organisation
// sees every patient of it; another organisation's patient answers 404.

const enc = encodeURIComponent;

// q matches part of the name or the start of the MRN; "" lists everyone.
export function fetchPatients(q = "") {
  return apiRequest(q ? `/patients?q=${enc(q)}` : "/patients");
}

// details: { mrn, full_name, date_of_birth?, age_years?, sex? }
export function createPatient(details) {
  return apiRequest("/patients", { method: "POST", json: details });
}

export function fetchPatient(id) {
  return apiRequest(`/patients/${enc(id)}`);
}

// changes: any of the details; null clears date_of_birth, age_years or sex.
export function updatePatient(id, changes) {
  return apiRequest(`/patients/${enc(id)}`, { method: "PATCH", json: changes });
}

// `{ background: true }` for a poll the page makes on its own.
export function fetchPatientDocuments(patientId, { background = false } = {}) {
  return apiRequest(`/patients/${enc(patientId)}/documents`, { background });
}

export function uploadDocument(patientId, file) {
  const formData = new FormData();
  formData.append("file", file);
  return apiRequest(`/patients/${enc(patientId)}/documents`, { method: "POST", body: formData });
}

// `{ background: true }` for a poll the page makes on its own.
export function fetchPatientSummary(patientId, { background = false } = {}) {
  return apiRequest(`/patients/${enc(patientId)}/summary`, { background });
}

// Start (re)building the patient summary in the background; poll
// fetchPatientSummary for the result.
export function triggerPatientSummary(patientId) {
  return apiRequest(`/patients/${enc(patientId)}/summary`, { method: "POST" });
}

// ---------- Documents API ----------

// One document: { id, patient_id, uploaded_by, uploaded_by_name, original_name, ... }
export function fetchDocument(id) {
  return apiRequest(`/documents/${enc(id)}`);
}

// Only the clinician who uploaded a document may delete it (403 otherwise).
// Deleting is soft: the document disappears from every page and summary.
export function deleteDocument(id) {
  return apiRequest(`/documents/${enc(id)}`, { method: "DELETE" });
}

// The document's structured record: { document_id, status, record,
// reviewed, citations, ... }. record holds every value as the model read
// it, what code derived from it, and the page transcriptions each value's
// citation points into; reviewed is the same record with the clinicians'
// corrections applied and their derived values computed again (by the AI
// service, never here). 404 until the pipeline has stored one; 503 when
// the reviewed view can't be computed. See docs/adr/0011-clinician-review.md.
export function fetchDocumentRecord(id) {
  return apiRequest(`/documents/${enc(id)}/record?view=reviewed`);
}

// `{ background: true }` for a poll the page makes on its own.
export function getInsight(documentId, { background = false } = {}) {
  return apiRequest(`/documents/${enc(documentId)}/insight`, { background });
}

// Ask the backend to start insight generation (it checks the organisation
// and forwards to the AI service). Fire-and-forget: poll getInsight for the result.
export function triggerInsightGeneration(documentId) {
  return apiRequest(`/documents/${enc(documentId)}/insight`, { method: "POST" });
}

// Process the document again from the start, as a new run (for when the
// pipeline has changed). Poll its insight afterwards.
export function reprocessDocument(documentId) {
  return apiRequest(`/documents/${enc(documentId)}/reprocess`, { method: "POST" });
}

// How the document's latest run went, step by step: { document_id, run }
// (run is null before any run).
export function fetchProcessing(documentId) {
  return apiRequest(`/documents/${encodeURIComponent(documentId)}/processing`);
}

// The document's full trace: { document_id, runs, run, steps, prompts }, for
// the latest run or the one runId names. Each call is audited as a read of
// patient data. See docs/adr/0006-durable-traceable-runs.md.
export function fetchTrace(documentId, runId) {
  const q = runId ? `?run=${enc(runId)}` : "";
  return apiRequest(`/documents/${enc(documentId)}/trace${q}`);
}

// ---------- Clinician review API ----------

// The organisation's documents waiting for review, longest waiting first:
// { documents: [{ document_id, patient_id, patient_name, patient_mrn,
// original_name, document_type, document_date, queued_at, flagged, reason }] }.
export function fetchReviewQueue() {
  return apiRequest("/review/queue");
}

// Finish the audit of a verified document sampled for one; it leaves the
// queue. 409 when it is not waiting for an audit.
export function completeAudit(documentId) {
  return apiRequest(`/documents/${enc(documentId)}/audit-complete`, { method: "POST" });
}

// Confirm an item's reading ({ action: "confirm", item_id }) or correct one
// field ({ action: "correct", item_id, field, value, note }). recordUpdatedAt
// is the record's updated_at as loaded; a record changed since answers 409.
// When a corrected value fails a check, nothing is saved and this throws a
// ReviewFindings with what the checks found; send it again with
// acceptFindings to keep the value. The answer is { document_id, status,
// record_updated_at, flagged, findings }. See docs/adr/0011-clinician-review.md.
export async function reviewDocument(documentId, review, { recordUpdatedAt, acceptFindings = false }) {
  try {
    return await apiRequest(`/documents/${enc(documentId)}/review`, {
      method: "POST",
      json: { ...review, record_updated_at: recordUpdatedAt, accept_findings: acceptFindings },
    });
  } catch (err) {
    const findings = err instanceof ApiError && err.status === 422 ? findingsOf(err.message) : null;
    if (findings) throw new ReviewFindings(findings);
    throw err;
  }
}

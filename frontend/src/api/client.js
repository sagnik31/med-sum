// src/api/client.js
//
// Every call goes through apiRequest. The session lives in an HttpOnly
// cookie that this code never sees; the browser sends it. In development,
// Vite serves the app and proxies /api to the Go server, so the cookie is
// first-party (see vite.config.js).

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// The API refuses state-changing requests without this header (CSRF
// protection).
const CSRF_HEADER = { "X-Requested-With": "med-sum" };

// Fired when the API answers 401, so the app can return to the login page.
const UNAUTHORIZED_EVENT = "medsum:unauthorized";

export function onUnauthorized(handler) {
  window.addEventListener(UNAUTHORIZED_EVENT, handler);
  return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
}

// Fired with the seconds the session has left (from the API's
// X-Session-Expires-In header) after every request that carried a session,
// so the app can warn before the idle timeout. See IdleWarning.jsx.
const SESSION_EXPIRY_EVENT = "medsum:session-expiry";

export function onSessionExpiry(handler) {
  const listener = (e) => handler(e.detail);
  window.addEventListener(SESSION_EXPIRY_EVENT, listener);
  return () => window.removeEventListener(SESSION_EXPIRY_EVENT, listener);
}

function reportSessionExpiry(response) {
  const value = response.headers.get("X-Session-Expires-In");
  if (value === null || !/^\d+$/.test(value)) return;
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRY_EVENT, { detail: Number(value) }));
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/**
 * Send a request to the API.
 * - json: a value to send as a JSON body
 * - body: a raw body (FormData for uploads)
 * - signalUnauthorized: fire UNAUTHORIZED_EVENT on a 401 (default true)
 */
async function apiRequest(
  path,
  { method = "GET", json, body, signalUnauthorized = true } = {}
) {
  const headers = new Headers();
  if (method !== "GET" && method !== "HEAD") {
    for (const [k, v] of Object.entries(CSRF_HEADER)) headers.set(k, v);
  }
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body,
    credentials: "include",
  });
  reportSessionExpiry(response);

  if (!response.ok) {
    const text = (await response.text().catch(() => "")).trim();
    if (response.status === 401) {
      if (signalUnauthorized) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      throw new ApiError(401, text ? `Unauthorized: ${text}` : "Unauthorized: please log in.");
    }
    throw new ApiError(response.status, text || `API error ${response.status}: ${response.statusText}`);
  }
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

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

export function fetchPatientDocuments(patientId) {
  return apiRequest(`/patients/${enc(patientId)}/documents`);
}

export function uploadDocument(patientId, file) {
  const formData = new FormData();
  formData.append("file", file);
  return apiRequest(`/patients/${enc(patientId)}/documents`, { method: "POST", body: formData });
}

export function fetchPatientSummary(patientId) {
  return apiRequest(`/patients/${enc(patientId)}/summary`);
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

export function getInsight(documentId) {
  return apiRequest(`/documents/${enc(documentId)}/insight`);
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

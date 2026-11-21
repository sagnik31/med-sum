// src/api/request.js
//
// The request code every API call uses, with nothing that needs Vite, so
// Node's test runner can import it (request.test.js). client.js binds it to
// the API's base URL.

// The API refuses state-changing requests without this header (CSRF
// protection).
export const CSRF_HEADER = { "X-Requested-With": "med-sum" };

// Sent on a poll the page makes on its own (a document or a summary being
// processed): the API checks the session but doesn't count the poll as
// activity, so an unattended screen still logs out after the idle timeout
// (docs/adr/0010-staff-access-and-security.md).
export const BACKGROUND_HEADER = { "X-Medsum-Background": "1" };

// The wait before the next background poll: 5 s, growing by half each time
// to at most a minute, so a long job isn't asked about every 5 s.
export function pollDelay(polls) {
  return Math.min(60000, Math.round(5000 * 1.5 ** Math.max(0, polls)));
}

// Whether a failed poll is worth trying again: the network, a timeout, a
// rate limit or a server error. A refusal (401, 403, 404) is not.
export function isTransient(err) {
  if (!(err instanceof ApiError)) return true;
  return err.status === 408 || err.status === 429 || err.status >= 500;
}

// Fired when the API answers 401, so the app can return to the login page.
export const UNAUTHORIZED_EVENT = "medsum:unauthorized";

export function onUnauthorized(handler) {
  window.addEventListener(UNAUTHORIZED_EVENT, handler);
  return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
}

// Fired with the seconds the session has left (from the API's
// X-Session-Expires-In header) after every request that carried a session,
// so the app can warn before the idle timeout. See IdleWarning.jsx.
export const SESSION_EXPIRY_EVENT = "medsum:session-expiry";

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
 * Send a request to the API at baseUrl.
 * - json: a value to send as a JSON body
 * - body: a raw body (FormData for uploads)
 * - signalUnauthorized: fire UNAUTHORIZED_EVENT on a 401 (default true)
 * - background: a poll the page makes on its own (BACKGROUND_HEADER)
 */
export async function apiRequest(
  baseUrl,
  path,
  { method = "GET", json, body, signalUnauthorized = true, background = false } = {}
) {
  const headers = new Headers();
  if (background) {
    for (const [k, v] of Object.entries(BACKGROUND_HEADER)) headers.set(k, v);
  }
  if (method !== "GET" && method !== "HEAD") {
    for (const [k, v] of Object.entries(CSRF_HEADER)) headers.set(k, v);
  }
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }

  const response = await fetch(`${baseUrl}${path}`, {
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

export class ReviewFindings extends Error {
  constructor(findings) {
    super("The corrected value fails a check.");
    this.findings = findings;
  }
}

// The findings of a 422 review answer ({"findings": [...]}), or null when the
// body is anything else.
export function findingsOf(text) {
  try {
    const body = JSON.parse(text);
    return Array.isArray(body?.findings) ? body.findings : null;
  } catch {
    return null;
  }
}

// Run with `npm test`. fetch and window are stubbed: no network.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  ApiError,
  SESSION_EXPIRY_EVENT,
  UNAUTHORIZED_EVENT,
  UnreadableAnswer,
  apiRequest,
  findingsOf,
  isTransient,
  pollDelay,
} from "./request.js";

let sent;
let answer;

beforeEach(() => {
  sent = [];
  answer = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
  globalThis.window = new EventTarget();
  globalThis.fetch = async (url, init) => {
    sent.push({ url, ...init });
    return answer();
  };
});

afterEach(() => {
  delete globalThis.window;
  delete globalThis.fetch;
});

function events(name) {
  const seen = [];
  window.addEventListener(name, (e) => seen.push(e.detail ?? true));
  return seen;
}

test("a GET carries the cookie and no CSRF header", async () => {
  assert.deepEqual(await apiRequest("/api", "/auth/me"), { ok: true });
  assert.equal(sent[0].url, "/api/auth/me");
  assert.equal(sent[0].credentials, "include");
  assert.equal(sent[0].headers.get("X-Requested-With"), null);
});

test("every other method carries the CSRF header, an upload's FormData too", async () => {
  for (const method of ["POST", "PATCH", "DELETE"]) {
    await apiRequest("/api", "/x", { method, json: { a: 1 } });
  }
  const form = new FormData();
  form.append("file", new Blob(["%PDF"]), "a.pdf");
  await apiRequest("/api", "/patients/p/documents", { method: "POST", body: form });

  for (const req of sent) {
    assert.equal(req.headers.get("X-Requested-With"), "med-sum", req.method);
  }
  assert.equal(sent[0].headers.get("Content-Type"), "application/json");
  assert.equal(sent[0].body, '{"a":1}');
  // The browser sets the multipart boundary itself.
  assert.equal(sent[3].headers.get("Content-Type"), null);
  assert.equal(sent[3].body, form);
});

test("a 401 fires the unauthorized event unless told not to", async () => {
  const seen = events(UNAUTHORIZED_EVENT);
  answer = () => new Response("session expired", { status: 401 });

  await assert.rejects(apiRequest("/api", "/x"), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(seen.length, 1);

  await assert.rejects(apiRequest("/api", "/auth/login", { signalUnauthorized: false }));
  assert.equal(seen.length, 1);
});

test("the session's time left is reported when the API sends it", async () => {
  const seen = events(SESSION_EXPIRY_EVENT);
  answer = () => new Response(null, { status: 204, headers: { "X-Session-Expires-In": "1740" } });
  assert.equal(await apiRequest("/api", "/x", { method: "DELETE" }), null);
  answer = () => new Response("{}", { headers: { "X-Session-Expires-In": "soon" } });
  await apiRequest("/api", "/x");
  assert.deepEqual(seen, [1740]);
});

test("an error answer keeps its status and the server's text", async () => {
  answer = () => new Response("no such patient\n", { status: 404 });
  await assert.rejects(apiRequest("/api", "/x"), (e) => e.status === 404 && e.message === "no such patient");
});

test("a success answer that can't be read is an error, never an empty result", async () => {
  for (const [body, status] of [['{"id":', 201], ["<!doctype html>", 200], ["", 200]]) {
    answer = () => new Response(body, { status });
    await assert.rejects(apiRequest("/api", "/x"), (e) => e instanceof UnreadableAnswer && isTransient(e), body);
  }
  answer = () => new Response(null, { status: 204 });
  assert.equal(await apiRequest("/api", "/x"), null);
});

test("findingsOf reads a 422 review answer's findings, and only those", () => {
  const findings = [{ validator: "source_text", message: "not on its line" }];
  assert.deepEqual(findingsOf(JSON.stringify({ findings })), findings);
  assert.equal(findingsOf(JSON.stringify({ findings: "x" })), null);
  assert.equal(findingsOf("record changed"), null);
  assert.equal(findingsOf(""), null);
});

test("a background poll says so, and nothing else does", async () => {
  await apiRequest("/api", "/documents/d/insight", { background: true });
  await apiRequest("/api", "/documents/d/insight");
  assert.equal(sent[0].headers.get("X-Medsum-Background"), "1");
  assert.equal(sent[1].headers.get("X-Medsum-Background"), null);
});

test("polls slow down to a minute at most", () => {
  assert.equal(pollDelay(0), 5000);
  assert.equal(pollDelay(1), 7500);
  assert.ok(pollDelay(3) > pollDelay(2));
  assert.equal(pollDelay(20), 60000);
});

test("a poll carries on through the network or a server error, not a refusal", () => {
  assert.ok(isTransient(new TypeError("Failed to fetch")));
  for (const status of [408, 429, 500, 502, 503]) assert.ok(isTransient(new ApiError(status, "x")), status);
  for (const status of [400, 401, 403, 404]) assert.ok(!isTransient(new ApiError(status, "x")), status);
});

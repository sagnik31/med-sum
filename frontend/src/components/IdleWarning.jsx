// src/components/IdleWarning.jsx
//
// A session ends after 30 minutes without a request (or 12 hours after
// login). Every API response says how many seconds are left; two minutes
// before the end this asks whether to stay signed in, so a clinician
// reading a long record isn't logged out mid-page without warning.
import React, { useEffect, useState } from "react";
import { onSessionExpiry } from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { WARN_BEFORE_S, formatLeft, isOver, nextLook, secondsLeft } from "../auth/idle.js";

function IdleWarning() {
  const { status, refresh, logout } = useAuth();
  const [endsAt, setEndsAt] = useState(null); // ms, on this browser's clock
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);

  // Each response moves the end; it is kept as a time on this clock.
  useEffect(() => onSessionExpiry((seconds) => setEndsAt(Date.now() + seconds * 1000)), []);

  useEffect(() => {
    if (status !== "in") setEndsAt(null);
  }, [status]);

  // Looks again when the warning is due, then every second: each look moves
  // `now`, which sets the next one.
  useEffect(() => {
    if (endsAt === null) return undefined;
    const timer = setTimeout(() => setNow(Date.now()), nextLook(endsAt, Date.now()));
    return () => clearTimeout(timer);
  }, [endsAt, now]);

  const left = endsAt === null ? null : secondsLeft(endsAt, now);

  // Once it must be over, ask the API, as a background check so that asking
  // can't keep a session alive that isn't over: the app goes to the login
  // page, which returns here after logging in. An answer that it is still
  // live (another tab was used) moves the end.
  const over = endsAt !== null && isOver(endsAt, now);
  useEffect(() => {
    if (status === "in" && over) refresh({ background: true });
  }, [status, over, refresh]);

  if (status !== "in" || left === null || left > WARN_BEFORE_S || left === 0) return null;

  async function stay() {
    setBusy(true);
    try {
      await refresh(); // any request counts as activity
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      role="alertdialog"
      aria-labelledby="idle-warning-title"
      className="fixed bottom-4 right-4 z-50 max-w-sm bg-white border border-amber-300 shadow-lg rounded-lg p-4 text-sm"
    >
      <p id="idle-warning-title" className="font-medium text-slate-900">
        You will be signed out in {formatLeft(left)}
      </p>
      <p className="text-slate-600 mt-1">Your session ends after 30 minutes without activity.</p>
      <div className="flex gap-3 mt-3">
        <button
          type="button"
          onClick={stay}
          disabled={busy}
          className="px-3 py-1.5 rounded-md bg-slate-900 text-white disabled:opacity-60"
        >
          Stay signed in
        </button>
        <button type="button" onClick={logout} className="px-3 py-1.5 rounded-md border">
          Sign out
        </button>
      </div>
    </div>
  );
}

export default IdleWarning;

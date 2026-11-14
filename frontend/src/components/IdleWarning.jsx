// src/components/IdleWarning.jsx
//
// A session ends after 30 minutes without a request (or 12 hours after
// login). Every API response says how many seconds are left; two minutes
// before the end this asks whether to stay signed in, so a clinician
// reading a long record isn't logged out mid-page without warning.
import React, { useEffect, useState } from "react";
import { onSessionExpiry } from "../api/client.js";
import { useAuth } from "../auth/context.js";

const WARN_BEFORE_S = 120;

function formatLeft(seconds) {
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

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

  // Tick once a second only while the warning could be due or showing.
  useEffect(() => {
    if (endsAt === null) return undefined;
    const untilWarning = endsAt - WARN_BEFORE_S * 1000 - Date.now();
    if (untilWarning > 0) {
      const timer = setTimeout(() => setNow(Date.now()), untilWarning);
      return () => clearTimeout(timer);
    }
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [endsAt]);

  const left = endsAt === null ? null : Math.max(0, Math.ceil((endsAt - now) / 1000));

  // At the end, ask the API: the session is gone, and the app goes to
  // the login page, which returns here after logging in.
  useEffect(() => {
    if (status === "in" && left === 0) refresh();
  }, [status, left, refresh]);

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

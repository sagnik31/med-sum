// src/components/SessionsSection.jsx
//
// The logged-in user's sessions, with a way to end the ones they don't
// recognise.
import React, { useEffect, useState } from "react";
import { endOtherSessions, endSession, fetchSessions } from "../api/client.js";

// A rough name for a browser from its user agent, for the list only.
function describeAgent(ua) {
  if (!ua) return "Unknown browser";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Chrome\//.test(ua)
      ? "Chrome"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Safari\//.test(ua)
          ? "Safari"
          : ua.slice(0, 40);
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Android/.test(ua)
      ? "Android"
      : /iPhone|iPad/.test(ua)
        ? "iOS"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser} on ${os}` : browser;
}

function SessionsSection() {
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setSessions(await fetchSessions());
      setError("");
    } catch (err) {
      setError(err.message || "Could not load your sessions.");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function run(action) {
    setBusy(true);
    try {
      await action();
      await load();
    } catch (err) {
      setError(err.message || "Could not end the session.");
    } finally {
      setBusy(false);
    }
  }

  const others = sessions.filter((s) => !s.current);

  return (
    <section>
      <h2 className="text-lg font-semibold mb-2">Where you are logged in</h2>
      {error && (
        <div role="alert" className="mb-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2">
          {error}
        </div>
      )}
      <ul className="text-sm divide-y border rounded-md">
        {sessions.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-3 px-3 py-2">
            <div>
              <div className="font-medium">
                {describeAgent(s.user_agent)}
                {s.current && <span className="ml-2 text-xs text-emerald-700">this session</span>}
              </div>
              <div className="text-xs text-slate-500">
                {s.ip || "unknown address"} · logged in {new Date(s.created_at).toLocaleString()} · last active{" "}
                {new Date(s.last_seen_at).toLocaleString()}
              </div>
            </div>
            {!s.current && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run(() => endSession(s.id))}
                className="text-xs text-red-600 hover:underline disabled:opacity-60"
              >
                Log out
              </button>
            )}
          </li>
        ))}
      </ul>
      {others.length > 0 && (
        <button
          type="button"
          disabled={busy}
          onClick={() => run(endOtherSessions)}
          className="mt-3 px-4 py-2 text-sm rounded-md border border-red-200 text-red-700 disabled:opacity-60"
        >
          Log out everywhere else
        </button>
      )}
    </section>
  );
}

export default SessionsSection;

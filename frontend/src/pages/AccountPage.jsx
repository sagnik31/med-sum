// src/pages/AccountPage.jsx
import React, { useState } from "react";
import { changePassword } from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { inputClass } from "./LoginPage.jsx";
import SessionsSection from "../components/SessionsSection.jsx";

function AccountPage() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [sessionsKey, setSessionsKey] = useState(0); // reloads the sessions list

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setMessage("");
    if (next !== confirm) {
      setError("The two new passwords are different.");
      return;
    }
    setSaving(true);
    try {
      await changePassword(current, next);
      setMessage("Password changed. Your other sessions have been logged out.");
      setSessionsKey((k) => k + 1);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err.message || "Could not change the password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6 max-w-2xl space-y-6">
      <section>
        <h1 className="text-xl font-semibold mb-2">Account</h1>
        <dl className="text-sm grid grid-cols-[auto,1fr] gap-x-4 gap-y-1">
          <dt className="text-slate-500">Name</dt>
          <dd>{user.full_name}</dd>
          <dt className="text-slate-500">Email</dt>
          <dd>{user.email}</dd>
          <dt className="text-slate-500">Organisation</dt>
          <dd>{user.org.name}</dd>
          <dt className="text-slate-500">Roles</dt>
          <dd>{user.roles.join(", ")}</dd>
        </dl>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-2">Change password</h2>
        {error && (
          <div role="alert" className="mb-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2">
            {error}
          </div>
        )}
        {message && (
          <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-md px-3 py-2">
            {message}
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-3">
          <input type="hidden" autoComplete="username" value={user.email} readOnly />
          <div>
            <label htmlFor="current-password" className="block text-sm font-medium text-slate-700 mb-1">
              Current password
            </label>
            <input
              id="current-password"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="new-password" className="block text-sm font-medium text-slate-700 mb-1">
              New password (at least 12 characters)
            </label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="confirm-password" className="block text-sm font-medium text-slate-700 mb-1">
              Confirm the new password
            </label>
            <input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className={inputClass}
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="px-4 py-2 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60"
          >
            {saving ? "Saving..." : "Change password"}
          </button>
        </form>
      </section>

      <SessionsSection key={sessionsKey} />
    </div>
  );
}

export default AccountPage;

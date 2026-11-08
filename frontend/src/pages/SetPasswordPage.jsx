// src/pages/SetPasswordPage.jsx
//
// Opened from a setup or reset link: /set-password#token=...
// The token is in the fragment, which browsers never send to a server.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchSetupInfo, setPasswordWithLink } from "../api/client.js";
import { inputClass } from "./LoginPage.jsx";

const MIN_LENGTH = 12;

function tokenFromHash() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return params.get("token") || "";
}

function SetPasswordPage() {
  const [token] = useState(tokenFromHash);
  const [info, setInfo] = useState(null); // { email, full_name, org }
  const [linkError, setLinkError] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    // Keep the token out of the address bar and history once it is read.
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    if (!token) {
      setLinkError("This page needs the link your administrator gave you.");
      return;
    }
    fetchSetupInfo(token)
      .then(setInfo)
      .catch((err) => setLinkError(err.message || "This link can't be used."));
  }, [token]);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if ([...password].length < MIN_LENGTH) {
      setError(`Use at least ${MIN_LENGTH} characters. A few words make a good password.`);
      return;
    }
    if (password !== confirm) {
      setError("The two passwords are different.");
      return;
    }
    setSaving(true);
    try {
      await setPasswordWithLink(token, password);
      setDone(true);
    } catch (err) {
      setError(err.message || "Could not set the password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6 max-w-md">
      <h1 className="text-xl font-semibold mb-2">Set your password</h1>

      {linkError && (
        <div role="alert" className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2">
          {linkError} Ask your administrator for a new link.
        </div>
      )}

      {done && (
        <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-md px-3 py-2">
          Your password is set.{" "}
          <Link to="/login" className="font-medium underline">
            Log in
          </Link>
        </div>
      )}

      {info && !done && (
        <>
          <p className="text-sm text-slate-600 mb-4">
            {info.full_name} ({info.email}), {info.org}. Use at least {MIN_LENGTH} characters;
            a few unrelated words work well.
          </p>
          {error && (
            <div role="alert" className="mb-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2">
              {error}
            </div>
          )}
          <form onSubmit={handleSubmit} className="space-y-3">
            {/* Lets password managers save the new password for this account. */}
            <input type="hidden" autoComplete="username" value={info.email} readOnly />
            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-slate-700 mb-1">
                New password
              </label>
              <input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
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
              className="w-full mt-2 px-4 py-2 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60"
            >
              {saving ? "Saving..." : "Set password"}
            </button>
          </form>
        </>
      )}
    </div>
  );
}

export default SetPasswordPage;

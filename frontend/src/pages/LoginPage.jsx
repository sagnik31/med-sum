// src/pages/LoginPage.jsx
import React, { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/context.js";

export const inputClass =
  "w-full border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300";

function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Where RequireAuth sent the user from, if anywhere.
  const from = location.state?.from?.pathname || "/";

  if (status === "in") {
    return <Navigate to={from} replace />;
  }

  async function handleLogin(e) {
    e.preventDefault();
    setError("");
    if (!email || !password) {
      setError("Enter your email and password.");
      return;
    }
    setLoading(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.message || "Login failed. Please try again.");
      setPassword("");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6 max-w-md">
      <h1 className="text-xl font-semibold mb-2">Log in</h1>
      <p className="text-sm text-slate-600 mb-4">
        For hospital staff. Your administrator creates your account and gives
        you a link to set your password.
      </p>

      {error && (
        <div
          role="alert"
          className="mb-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2"
        >
          {error}
        </div>
      )}

      <form onSubmit={handleLogin} className="space-y-3">
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-slate-700 mb-1">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        </div>

        <button
          type="submit"
          disabled={loading || status === "loading"}
          className="w-full mt-2 px-4 py-2 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60"
        >
          {loading ? "Logging in..." : "Log in"}
        </button>
      </form>

      <p className="mt-4 text-xs text-slate-500">
        Forgot your password? Ask your administrator for a reset link.
      </p>
    </div>
  );
}

export default LoginPage;

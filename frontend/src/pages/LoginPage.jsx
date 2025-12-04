// src/pages/LoginPage.jsx
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { API_BASE_URL } from "../api/client.js";

function LoginPage() {
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const navigate = useNavigate();

  async function handleLogin(e) {
    e.preventDefault();
    setError("");
    setSuccessMessage("");

    if (!phone || !otp) {
      setError("Please enter both phone number and OTP.");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          phone_number: phone,
          otp: otp,
        }),
      });

      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error(
          text || `Login failed with status ${response.status}`
        );
      }

      const data = await response.json().catch(() => ({}));
      const token = data.token || data.access_token;

      if (!token) {
        throw new Error("Login succeeded but no token returned from server.");
      }

      // Store token for later API requests
      localStorage.setItem("authToken", token);

      setSuccessMessage("Login successful! Redirecting to documents...");
      // Small delay so user sees the message
      setTimeout(() => {
        navigate("/documents");
      }, 800);
    } catch (err) {
      console.error(err);
      setError(err.message || "Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6 max-w-md">
      <h1 className="text-xl font-semibold mb-2">Login</h1>
      <p className="text-sm text-slate-600 mb-4">
        Enter your phone number and OTP to log in.
      </p>

      {error && (
        <div className="mb-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md px-3 py-2">
          {error}
        </div>
      )}
      {successMessage && (
        <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-md px-3 py-2">
          {successMessage}
        </div>
      )}

      <form onSubmit={handleLogin} className="space-y-3">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Phone number
          </label>
          <input
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="Enter phone number"
            className="w-full border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            OTP
          </label>
          <input
            type="text"
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            placeholder="Enter OTP"
            className="w-full border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full mt-2 px-4 py-2 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60"
        >
          {loading ? "Logging in..." : "Login"}
        </button>
      </form>

      <p className="mt-4 text-xs text-slate-400">
        API base URL: <code className="font-mono">{API_BASE_URL}</code>
      </p>
    </div>
  );
}

export default LoginPage;
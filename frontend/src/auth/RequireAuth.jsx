// src/auth/RequireAuth.jsx
import React from "react";
import { Link, Navigate, useLocation } from "react-router";
import { useAuth } from "./context.js";
import { can, homePath } from "./permissions.js";

// Shows its children only to a logged-in user (with the permission perm,
// when given); sends anyone else to the login page (/login?next=<here>),
// which comes back here afterwards. This is for navigation only: the API checks the session and
// the permission on every request.
function RequireAuth({ children, perm }) {
  const { status, user } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <p className="text-sm text-slate-500">Loading...</p>;
  }
  if (status === "out") {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  if (perm && !can(user, perm)) {
    return (
      <div className="bg-white rounded-xl shadow p-6">
        <h1 className="text-xl font-semibold mb-2">Not available</h1>
        <p className="text-sm text-slate-600 mb-4">Your account doesn&apos;t have access to this page.</p>
        <Link to={homePath(user)} className="text-blue-600 hover:underline text-sm font-medium">
          Go to your start page
        </Link>
      </div>
    );
  }
  // Keyed by the account: another one's login (in another tab) drops what
  // was on screen.
  return <React.Fragment key={user.id}>{children}</React.Fragment>;
}

export default RequireAuth;

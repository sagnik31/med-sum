// src/auth/RequireAuth.jsx
import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./context.js";

// Shows its children only to a logged-in user; sends anyone else to the
// login page, which comes back here afterwards. This is for navigation
// only: the API checks the session on every request.
function RequireAuth({ children }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <p className="text-sm text-slate-500">Loading...</p>;
  }
  if (status === "out") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return children;
}

export default RequireAuth;

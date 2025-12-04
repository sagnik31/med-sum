// src/pages/NotFoundPage.jsx
import React from "react";
import { Link } from "react-router-dom";

function NotFoundPage() {
  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-2">404 - Not Found</h1>
      <p className="text-sm text-slate-600 mb-4">
        The page you are looking for does not exist.
      </p>
      <Link
        to="/login"
        className="text-blue-600 hover:underline text-sm font-medium"
      >
        Go to Login
      </Link>
    </div>
  );
}

export default NotFoundPage;
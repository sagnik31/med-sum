// src/components/ApiErrorNotice.jsx
import React from "react";
import { Link } from "react-router";

// How a page shows an API error. A 403 is "not allowed" (the account lacks
// the permission, or e.g. only the uploader may delete a document); a 404
// is "not found", which is also what another organisation's patient or
// document answers. Anything else shows the API's message.
function ApiErrorNotice({ error, backTo, backLabel = "Back to patients" }) {
  if (!error) return null;
  if (error.status === 403 || error.status === 404) {
    const title = error.status === 403 ? "Not allowed" : "Not found";
    const text =
      error.status === 403
        ? error.message || "Your account isn't allowed to do this."
        : "There is nothing here, or your account can't see it.";
    return (
      <div className="mb-4 bg-slate-50 border border-slate-200 rounded-md p-4 text-sm">
        <p className="font-semibold text-slate-800">{title}</p>
        <p className="text-slate-600 mt-1">{text}</p>
        {backTo && (
          <Link to={backTo} className="inline-block mt-2 text-blue-600 hover:underline font-medium">
            {backLabel}
          </Link>
        )}
      </div>
    );
  }
  return (
    <div className="mb-4 bg-red-50 border border-red-100 rounded-md p-3 text-sm text-red-600">
      {error.message || "Something went wrong."}
    </div>
  );
}

export default ApiErrorNotice;

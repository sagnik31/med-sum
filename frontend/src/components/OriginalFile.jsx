// src/components/OriginalFile.jsx
//
// The original upload, to check values against: an image directly, a PDF
// in the browser's own viewer opened at the page being looked at
// (#page=N). The API serves it from GET /documents/{id}/file, the one
// route it lets the app frame. See docs/adr/0011-clinician-review.md.
import React from "react";
import { API_BASE_URL } from "../api/client.js";
import { originalFileUrl } from "../record/review.js";

function OriginalFile({ documentId, isPdf, page }) {
  const url = originalFileUrl(API_BASE_URL, documentId, isPdf ? page : null);
  return (
    <section className="mb-3">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold text-slate-700">
          Original upload{isPdf && page ? `, page ${page}` : ""}
        </h3>
        <a href={url} target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">
          Open in a new tab
        </a>
      </div>
      {isPdf ? (
        // Keyed by the page: a PDF viewer reads #page only when it loads.
        <iframe
          key={url}
          src={url}
          title="Original upload"
          className="w-full h-[32rem] border border-slate-200 rounded-md bg-white"
        />
      ) : (
        <img src={url} alt="The original upload" className="w-full border border-slate-200 rounded-md bg-white" />
      )}
    </section>
  );
}

export default OriginalFile;

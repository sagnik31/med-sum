// src/pages/DocumentsPage.jsx
import React, { useEffect, useState } from "react";
import { fetchDocuments, uploadDocument, deleteDocument } from "../api/client.js";
import { Link } from "react-router-dom";

function DocumentsPage() {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);

  // Fetch documents on page load
  useEffect(() => {
    loadDocuments();
  }, []);

  async function loadDocuments() {
    setLoading(true);
    setError("");
    try {
      const data = await fetchDocuments();
      // Expecting `data` to be an array; if backend wraps it, adjust here
      setDocuments(Array.isArray(data) ? data : data?.documents || []);
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to load documents.");
    } finally {
      setLoading(false);
    }
  }

  async function handleUpload(e) {
    e.preventDefault();
    if (!file) {
      setError("Please select a file first.");
      return;
    }

    setUploading(true);
    setError("");
    try {
      await uploadDocument(file);
      setFile(null);
      // Refresh list
      await loadDocuments();
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to upload document.");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id) {
    if (!window.confirm("Are you sure you want to delete this document?")) {
      return;
    }

    try {
      await deleteDocument(id);
      setDocuments((docs) => docs.filter((d) => d.id !== id));
    } catch (err) {
      console.error(err);
      setError(err.message || "Failed to delete document.");
    }
  }

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-4">Documents</h1>

      {/* Upload section */}
      <form onSubmit={handleUpload} className="mb-6 space-y-3">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Upload a new document
          </label>
          <input
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            className="block w-full text-sm text-slate-700 file:mr-4 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-slate-100 file:text-slate-700 hover:file:bg-slate-200"
          />
        </div>
        <button
          type="submit"
          disabled={uploading}
          className="px-4 py-1.5 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60"
        >
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </form>

      {/* Error message */}
      {error && (
        <div className="mb-4 text-sm bg-red-50 border border-red-100 rounded-md px-3 py-2">
          <p className="text-red-600">{error}</p>
          {error.toLowerCase().includes("unauthorized") && (
            <p className="mt-1">
              <Link
                to="/login"
                className="text-blue-600 hover:underline font-medium"
              >
                Go to Login
              </Link>
            </p>
          )}
        </div>
      )}

      {/* Documents list */}
      <div>
        <h2 className="text-sm font-semibold text-slate-700 mb-2">
          Your documents
        </h2>

        {loading ? (
          <p className="text-sm text-slate-600">Loading documents...</p>
        ) : documents.length === 0 ? (
          <p className="text-sm text-slate-500">
            No documents found. Try uploading one above.
          </p>
        ) : (
          <ul className="space-y-2">
            {documents.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center justify-between text-sm border rounded-md px-3 py-2"
              >
                <div className="flex flex-col">
                  <span className="font-medium text-slate-800">
                    {doc.original_filename || doc.filename || `Document ${doc.id}`}
                  </span>
                  <span className="text-xs text-slate-500">
                    ID: {doc.id}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <Link
                    to={`/insights/${doc.id}`}
                    className="text-blue-600 hover:underline text-xs font-medium"
                  >
                    Get insight
                  </Link>
                  <button
                    onClick={() => handleDelete(doc.id)}
                    className="text-red-600 hover:underline text-xs font-medium"
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-8 pt-6 border-t">
        <Link
          to="/user-insights"
          className="block w-full text-center px-4 py-2 bg-indigo-600 text-white rounded-md font-medium hover:bg-indigo-700 transition-colors"
        >
          Generate user insights
        </Link>
      </div>

      <p className="mt-4 text-xs text-slate-400">
        Note: API base URL:{" "}
        <code className="font-mono">
          {import.meta.env.VITE_API_BASE_URL || "http://localhost:8080"}
        </code>
      </p>
    </div>
  );
}

export default DocumentsPage;
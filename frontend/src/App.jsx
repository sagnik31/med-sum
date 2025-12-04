// src/App.jsx
import React from "react";
import { Routes, Route, Navigate, Link } from "react-router-dom";
import LoginPage from "./pages/LoginPage.jsx";
import DocumentsPage from "./pages/DocumentsPage.jsx";
import InsightPage from "./pages/InsightPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";

function App() {
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="bg-white border-b shadow-sm">
        <nav className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
          <Link to="/" className="font-semibold text-lg">
            med-sum
          </Link>
          <div className="flex gap-4 text-sm">
            <Link to="/login" className="hover:underline">
              Login
            </Link>
            <Link to="/documents" className="hover:underline">
              Documents
            </Link>
          </div>
        </nav>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/login" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/insights/:id" element={<InsightPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
// src/App.jsx
import React from "react";
import { Routes, Route, Navigate, Link, useNavigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage.jsx";
import SetPasswordPage from "./pages/SetPasswordPage.jsx";
import AccountPage from "./pages/AccountPage.jsx";
import DocumentsPage from "./pages/DocumentsPage.jsx";
import InsightPage from "./pages/InsightPage.jsx";
import UserInsightsPage from "./pages/UserInsightsPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import StaffPage from "./pages/StaffPage.jsx";
import RequireAuth from "./auth/RequireAuth.jsx";
import { useAuth } from "./auth/context.js";
import { PERM, can, homePath } from "./auth/permissions.js";

// "/" goes to the start page for the user's roles.
function Home() {
  const { user } = useAuth();
  return <Navigate to={homePath(user)} replace />;
}

function Nav() {
  const { status, user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <nav className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
      <Link to="/" className="font-semibold text-lg">
        med-sum
      </Link>
      {status === "in" ? (
        <div className="flex items-center gap-4 text-sm">
          {can(user, PERM.documentRead) && (
            <Link to="/documents" className="hover:underline">
              Documents
            </Link>
          )}
          {can(user, PERM.staffManage) && (
            <Link to="/admin/staff" className="hover:underline">
              Staff
            </Link>
          )}
          <Link to="/account" className="hover:underline" title={user.email}>
            {user.full_name}
          </Link>
          <button type="button" onClick={handleLogout} className="hover:underline">
            Log out
          </button>
        </div>
      ) : (
        <div className="flex gap-4 text-sm">
          <Link to="/login" className="hover:underline">
            Log in
          </Link>
        </div>
      )}
    </nav>
  );
}

function App() {
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="bg-white border-b shadow-sm">
        <Nav />
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/set-password" element={<SetPasswordPage />} />
          <Route path="/account" element={<RequireAuth><AccountPage /></RequireAuth>} />
          <Route path="/documents" element={<RequireAuth perm={PERM.documentRead}><DocumentsPage /></RequireAuth>} />
          <Route path="/user-insights" element={<RequireAuth perm={PERM.documentRead}><UserInsightsPage /></RequireAuth>} />
          <Route path="/insights/:id" element={<RequireAuth perm={PERM.documentRead}><InsightPage /></RequireAuth>} />
          <Route path="/admin/staff" element={<RequireAuth perm={PERM.staffManage}><StaffPage /></RequireAuth>} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;

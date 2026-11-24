// src/App.jsx
import React from "react";
import { Routes, Route, Navigate, Link, useNavigate } from "react-router";
import LoginPage from "./pages/LoginPage.jsx";
import SetPasswordPage from "./pages/SetPasswordPage.jsx";
import AccountPage from "./pages/AccountPage.jsx";
import PatientsPage from "./pages/PatientsPage.jsx";
import PatientPage from "./pages/PatientPage.jsx";
import PatientSummaryPage from "./pages/PatientSummaryPage.jsx";
import InsightPage from "./pages/InsightPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import StaffPage from "./pages/StaffPage.jsx";
import AuditPage from "./pages/AuditPage.jsx";
import RunsPage from "./pages/RunsPage.jsx";
import RunPage from "./pages/RunPage.jsx";
import ReviewQueuePage from "./pages/ReviewQueuePage.jsx";
import RequireAuth from "./auth/RequireAuth.jsx";
import IdleWarning from "./components/IdleWarning.jsx";
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
    if (await logout()) navigate("/login", { replace: true });
  }

  return (
    <nav className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
      <Link to="/" className="font-semibold text-lg">
        med-sum
      </Link>
      {status === "in" ? (
        <div className="flex items-center gap-4 text-sm">
          {can(user, PERM.patientRead) && (
            <Link to="/patients" className="hover:underline">
              Patients
            </Link>
          )}
          {can(user, PERM.reviewWrite) && (
            <Link to="/review" className="hover:underline">
              Review
            </Link>
          )}
          {can(user, PERM.staffManage) && (
            <Link to="/admin/staff" className="hover:underline">
              Staff
            </Link>
          )}
          {can(user, PERM.auditRead) && (
            <Link to="/admin/audit" className="hover:underline">
              Audit log
            </Link>
          )}
          {can(user, PERM.runsRead) && (
            <Link to="/admin/runs" className="hover:underline">
              Pipeline runs
            </Link>
          )}
          <Link to="/account" className="hover:underline" title={user.email}>
            {user.full_name}
          </Link>
          <button type="button" onClick={handleLogout} className="hover:underline">
            Log out
          </button>
        </div>
      ) : status === "out" ? (
        <div className="flex gap-4 text-sm">
          <Link to="/login" className="hover:underline">
            Log in
          </Link>
        </div>
      ) : null}
    </nav>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/set-password" element={<SetPasswordPage />} />
      <Route path="/account" element={<RequireAuth><AccountPage /></RequireAuth>} />
      <Route path="/patients" element={<RequireAuth perm={PERM.patientRead}><PatientsPage /></RequireAuth>} />
      <Route path="/patients/:id" element={<RequireAuth perm={PERM.patientRead}><PatientPage /></RequireAuth>} />
      <Route path="/patients/:id/summary" element={<RequireAuth perm={PERM.summaryRead}><PatientSummaryPage /></RequireAuth>} />
      <Route path="/insights/:id" element={<RequireAuth perm={PERM.documentRead}><InsightPage /></RequireAuth>} />
      <Route path="/review" element={<RequireAuth perm={PERM.reviewWrite}><ReviewQueuePage /></RequireAuth>} />
      <Route path="/admin/staff" element={<RequireAuth perm={PERM.staffManage}><StaffPage /></RequireAuth>} />
      <Route path="/admin/audit" element={<RequireAuth perm={PERM.auditRead}><AuditPage /></RequireAuth>} />
      <Route path="/admin/runs" element={<RequireAuth perm={PERM.runsRead}><RunsPage /></RequireAuth>} />
      <Route path="/admin/runs/:id" element={<RequireAuth perm={PERM.runsRead}><RunPage /></RequireAuth>} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

// Shown in place of every page from the moment a logout is sent until the
// API has ended the session.
function Leaving() {
  const { status, logout } = useAuth();
  if (status === "leaving") return <p className="text-sm text-slate-600">Logging out...</p>;
  return (
    <div role="alert" className="bg-white rounded-xl shadow p-6 max-w-md text-sm">
      <h1 className="text-xl font-semibold mb-2">You are not logged out</h1>
      <p className="text-red-700 mb-4">
        The server could not be told to end your session, so it may still be open: anyone who reloads this page could
        use it. Try again before leaving this computer.
      </p>
      <button type="button" onClick={logout} className="px-4 py-2 rounded-md bg-slate-900 text-white">
        Log out again
      </button>
    </div>
  );
}

function App() {
  const { status } = useAuth();
  const leaving = status === "leaving" || status === "leave_failed";
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="bg-white border-b shadow-sm">
        <Nav />
      </header>
      <IdleWarning />

      <main className="max-w-4xl mx-auto px-4 py-6">
        {leaving ? <Leaving /> : <AppRoutes />}
      </main>
    </div>
  );
}

export default App;

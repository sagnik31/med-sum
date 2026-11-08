// src/pages/StaffPage.jsx
//
// Admins invite staff, change their roles, disable or enable accounts and
// issue setup or reset links. There is no email yet, so a link is shown
// here for the admin to pass on.
import React, { useEffect, useState } from "react";
import { createStaff, fetchStaff, issueStaffLink, updateStaff } from "../api/client.js";
import { useAuth } from "../auth/context.js";
import { inputClass } from "./LoginPage.jsx";

const ROLES = [
  { id: "clinician", label: "Clinician", hint: "patients, documents and summaries" },
  { id: "admin", label: "Admin", hint: "staff accounts; no clinical data" },
];

const STATUS_BADGES = {
  invited: { label: "Invited", className: "bg-yellow-100 text-yellow-800" },
  active: { label: "Active", className: "bg-emerald-100 text-emerald-800" },
  disabled: { label: "Disabled", className: "bg-slate-200 text-slate-600" },
};

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "never";
}

function Alert({ kind = "error", children }) {
  const style =
    kind === "error"
      ? "text-red-600 bg-red-50 border-red-100"
      : "text-emerald-700 bg-emerald-50 border-emerald-100";
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`text-sm border rounded-md px-3 py-2 ${style}`}>
      {children}
    </div>
  );
}

// A setup or reset link to hand to the account's owner.
function LinkPanel({ link, email, onClose }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="border border-blue-200 bg-blue-50 rounded-lg p-4 space-y-2 text-sm">
      <p>
        {link.purpose === "setup" ? "Setup" : "Password reset"} link for <strong>{email}</strong>. It works once,
        until {formatTime(link.expires_at)}. Send it to them privately; issuing another link cancels this one.
      </p>
      <div className="flex gap-2">
        <input readOnly value={link.url} onFocus={(e) => e.target.select()} className={`${inputClass} font-mono text-xs`} aria-label="Link" />
        <button type="button" onClick={copy} className="px-3 py-1 rounded-md bg-slate-900 text-white">
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" onClick={onClose} className="px-3 py-1 rounded-md border bg-white">
          Done
        </button>
      </div>
    </div>
  );
}

function InviteForm({ onCreated }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [registrationNo, setRegistrationNo] = useState("");
  const [roles, setRoles] = useState(["clinician"]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function toggleRole(id) {
    setRoles((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (roles.length === 0) {
      setError("Choose at least one role.");
      return;
    }
    setSaving(true);
    try {
      const created = await createStaff({ email, fullName, registrationNo, roles });
      setEmail("");
      setFullName("");
      setRegistrationNo("");
      setRoles(["clinician"]);
      onCreated(created);
    } catch (err) {
      setError(err.message || "Could not create the account.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <h2 className="text-lg font-semibold">Invite a staff member</h2>
      {error && <Alert>{error}</Alert>}
      <div className="grid sm:grid-cols-2 gap-3">
        <div>
          <label htmlFor="invite-name" className="block text-sm font-medium text-slate-700 mb-1">
            Full name
          </label>
          <input id="invite-name" required value={fullName} onChange={(e) => setFullName(e.target.value)} className={inputClass} />
        </div>
        <div>
          <label htmlFor="invite-email" className="block text-sm font-medium text-slate-700 mb-1">
            Work email
          </label>
          <input id="invite-email" type="email" required autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
        </div>
        <div>
          <label htmlFor="invite-reg" className="block text-sm font-medium text-slate-700 mb-1">
            Medical registration number (optional)
          </label>
          <input id="invite-reg" value={registrationNo} onChange={(e) => setRegistrationNo(e.target.value)} className={inputClass} />
        </div>
        <fieldset>
          <legend className="block text-sm font-medium text-slate-700 mb-1">Roles</legend>
          {ROLES.map((r) => (
            <label key={r.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={roles.includes(r.id)} onChange={() => toggleRole(r.id)} />
              {r.label} <span className="text-slate-500">({r.hint})</span>
            </label>
          ))}
        </fieldset>
      </div>
      <button type="submit" disabled={saving} className="px-4 py-2 text-sm rounded-md bg-slate-900 text-white disabled:opacity-60">
        {saving ? "Creating..." : "Create account and setup link"}
      </button>
    </form>
  );
}

function StaffRow({ member, isSelf, onChange, onLink }) {
  const [busy, setBusy] = useState(false);
  const locked = member.locked_until && new Date(member.locked_until) > new Date();
  const badge = STATUS_BADGES[member.status] || STATUS_BADGES.active;

  async function run(action) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  function toggleRole(id) {
    const roles = member.roles.includes(id) ? member.roles.filter((r) => r !== id) : [...member.roles, id];
    if (roles.length === 0) return;
    run(() => onChange(member, { roles }));
  }

  return (
    <tr className="border-t align-top">
      <td className="py-2 pr-3">
        <div className="font-medium">
          {member.full_name} {isSelf && <span className="text-xs text-slate-500">(you)</span>}
        </div>
        <div className="text-xs text-slate-500">{member.email}</div>
        {member.registration_no && <div className="text-xs text-slate-500">Reg. {member.registration_no}</div>}
      </td>
      <td className="py-2 pr-3">
        {ROLES.map((r) => (
          <label key={r.id} className="flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              checked={member.roles.includes(r.id)}
              disabled={busy || (isSelf && r.id === "admin") || (member.roles.length === 1 && member.roles.includes(r.id))}
              onChange={() => toggleRole(r.id)}
            />
            {r.label}
          </label>
        ))}
      </td>
      <td className="py-2 pr-3 text-xs">
        <span className={`inline-block px-2 py-0.5 rounded-full ${badge.className}`}>{badge.label}</span>
        {locked && (
          <div className="mt-1 text-red-600" title={`until ${formatTime(member.locked_until)}`}>
            Locked (failed logins)
          </div>
        )}
      </td>
      <td className="py-2 pr-3 text-xs text-slate-600">{formatTime(member.last_login_at)}</td>
      <td className="py-2 text-xs space-y-1">
        {member.status !== "disabled" && (
          <button type="button" disabled={busy} onClick={() => run(() => onLink(member))} className="block text-blue-600 hover:underline disabled:opacity-60">
            {member.status === "invited" ? "New setup link" : "Reset password"}
          </button>
        )}
        {!isSelf &&
          (member.status === "disabled" ? (
            <button type="button" disabled={busy} onClick={() => run(() => onChange(member, { status: "active" }))} className="block text-blue-600 hover:underline disabled:opacity-60">
              Enable
            </button>
          ) : (
            <button type="button" disabled={busy} onClick={() => run(() => onChange(member, { status: "disabled" }))} className="block text-red-600 hover:underline disabled:opacity-60">
              Disable
            </button>
          ))}
      </td>
    </tr>
  );
}

function StaffPage() {
  const { user, refresh } = useAuth();
  const [staff, setStaff] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [shownLink, setShownLink] = useState(null); // { link, email }

  async function load() {
    try {
      setStaff(await fetchStaff());
      setError("");
    } catch (err) {
      setError(err.message || "Could not load the staff list.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleChange(member, changes) {
    if (changes.status === "disabled" && !window.confirm(`Disable ${member.email}? They are logged out at once.`)) {
      return;
    }
    if (changes.roles && member.id !== user.id && member.status === "active") {
      if (!window.confirm(`Change the roles of ${member.email}? They are logged out and log in again.`)) return;
    }
    try {
      await updateStaff(member.id, changes);
      setError("");
      if (member.id === user.id) await refresh(); // new permissions for the nav
      await load();
    } catch (err) {
      setError(err.message || "Could not change the account.");
    }
  }

  async function handleLink(member) {
    try {
      const link = await issueStaffLink(member.id);
      setShownLink({ link, email: member.email });
      setError("");
    } catch (err) {
      setError(err.message || "Could not issue a link.");
    }
  }

  function handleCreated({ user: created, link }) {
    setShownLink({ link, email: created.email });
    load();
  }

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl shadow p-6 space-y-4">
        <div>
          <h1 className="text-xl font-semibold">Staff</h1>
          <p className="text-sm text-slate-600">
            Accounts in {user.org.name}. Disabling an account or changing its roles logs it out everywhere.
          </p>
        </div>
        {error && <Alert>{error}</Alert>}
        {shownLink && <LinkPanel {...shownLink} onClose={() => setShownLink(null)} />}
        {loading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="pb-2 pr-3 font-medium">Name</th>
                  <th className="pb-2 pr-3 font-medium">Roles</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 pr-3 font-medium">Last login</th>
                  <th className="pb-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {staff.map((m) => (
                  <StaffRow key={m.id} member={m} isSelf={m.id === user.id} onChange={handleChange} onLink={handleLink} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="bg-white rounded-xl shadow p-6">
        <InviteForm onCreated={handleCreated} />
      </div>
    </div>
  );
}

export default StaffPage;

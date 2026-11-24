// src/pages/AuditPage.jsx
//
// Admins read the organisation's audit log: who did what to which record,
// newest first. Records are shown by id: the log holds no clinical
// content, and admins see none.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { fetchAuditEvents } from "../api/client.js";
import { localDays } from "../audit.js";
import ApiErrorNotice from "../components/ApiErrorNotice.jsx";

const inputClass =
  "border rounded-md px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300";

const OUTCOME_BADGES = {
  success: "bg-emerald-100 text-emerald-800",
  failure: "bg-red-100 text-red-700",
  denied: "bg-amber-100 text-amber-800",
};

const shortId = (id) => (id ? id.slice(0, 8) : "");

function describeDetails(details) {
  if (!details) return "";
  return Object.entries(details)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
    .join("; ");
}

function AuditPage() {
  const [filters, setFilters] = useState({ actor: "", patient: "", from: "", to: "" });
  const [applied, setApplied] = useState(filters);
  const [events, setEvents] = useState([]);
  const [nextBefore, setNextBefore] = useState(null); // the next page's cursor
  // Only the last request's answer is shown: one for older filters, or for
  // a page of them, says nothing about the ones applied now.
  const request = useRef(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(
    async (before) => {
      request.current += 1;
      const mine = request.current;
      setLoading(true);
      setError(null);
      if (!before) {
        setEvents([]);
        setNextBefore(null);
      }
      try {
        const page = await fetchAuditEvents({ ...applied, ...localDays(applied.from, applied.to), before });
        if (mine !== request.current) return;
        setEvents((prev) => (before ? [...prev, ...page.events] : page.events));
        setNextBefore(page.next_before ?? null);
      } catch (err) {
        if (mine === request.current) setError(err);
      } finally {
        if (mine === request.current) setLoading(false);
      }
    },
    [applied],
  );

  useEffect(() => {
    load();
  }, [load]);

  function handleFilter(e) {
    e.preventDefault();
    setApplied({ ...filters });
  }

  const field = (name) => ({
    value: filters[name],
    onChange: (e) => setFilters((f) => ({ ...f, [name]: e.target.value })),
  });

  return (
    <div className="bg-white rounded-xl shadow p-6">
      <h1 className="text-xl font-semibold mb-1">Audit log</h1>
      <p className="text-sm text-slate-600 mb-4">
        Every login, staff change, and read or change of patient data in your organisation. Entries cannot be edited or
        deleted. Dates and times are in your time zone.
      </p>

      <form onSubmit={handleFilter} className="flex flex-wrap items-end gap-3 mb-4 text-sm">
        <label className="flex flex-col">
          <span className="text-slate-700 mb-1">Staff account id</span>
          <input {...field("actor")} className={`${inputClass} font-mono`} size={36} />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-700 mb-1">Patient id</span>
          <input {...field("patient")} className={`${inputClass} font-mono`} size={36} />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-700 mb-1">From</span>
          <input type="date" {...field("from")} className={inputClass} />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-700 mb-1">Until (inclusive)</span>
          <input type="date" {...field("to")} className={inputClass} />
        </label>
        <button type="submit" className="px-4 py-1.5 rounded-md bg-slate-900 text-white">
          Filter
        </button>
      </form>

      <ApiErrorNotice error={error} />

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b">
              <th className="py-2 pr-3 font-medium">When</th>
              <th className="py-2 pr-3 font-medium">Who</th>
              <th className="py-2 pr-3 font-medium">Action</th>
              <th className="py-2 pr-3 font-medium">Record</th>
              <th className="py-2 pr-3 font-medium">Patient</th>
              <th className="py-2 pr-3 font-medium">Outcome</th>
              <th className="py-2 pr-3 font-medium">From</th>
              <th className="py-2 font-medium">Details</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => (
              <tr key={ev.id} className="border-b align-top">
                <td className="py-1.5 pr-3 whitespace-nowrap">{new Date(ev.at).toLocaleString()}</td>
                <td className="py-1.5 pr-3" title={ev.actor_id || ""}>
                  {ev.actor_name || (ev.actor_id ? shortId(ev.actor_id) : "unknown")}
                </td>
                <td className="py-1.5 pr-3 font-mono text-xs">{ev.action}</td>
                <td className="py-1.5 pr-3 font-mono text-xs" title={ev.target_id || ""}>
                  {ev.target_type && `${ev.target_type} ${shortId(ev.target_id)}`}
                </td>
                <td className="py-1.5 pr-3 font-mono text-xs" title={ev.patient_id || ""}>
                  {shortId(ev.patient_id)}
                </td>
                <td className="py-1.5 pr-3">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded ${OUTCOME_BADGES[ev.outcome] || ""}`}>
                    {ev.outcome}
                  </span>
                </td>
                <td className="py-1.5 pr-3 text-xs text-slate-600">{ev.ip}</td>
                <td className="py-1.5 text-xs text-slate-600">{describeDetails(ev.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {loading && <p className="text-sm text-slate-600 mt-3">Loading...</p>}
      {!loading && events.length === 0 && !error && <p className="text-sm text-slate-500 mt-3">No entries match.</p>}
      {!loading && nextBefore && (
        <button
          type="button"
          onClick={() => load(nextBefore)}
          className="mt-4 text-sm text-blue-600 hover:underline"
        >
          Older entries
        </button>
      )}
    </div>
  );
}

export default AuditPage;

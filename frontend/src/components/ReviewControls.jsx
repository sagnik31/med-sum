// src/components/ReviewControls.jsx
//
// A clinician's review of one item (or the patient's or the document's
// header values): confirm the reading, or correct one field. A corrected
// value that fails a check (its flag no longer agrees with its range, a
// date that isn't one) is shown with what failed and saved only when the
// clinician saves it again. Nothing is confirmed unless the value's source
// (its lines and its page of the original) is on screen: the first press
// of Confirm shows it, the next one confirms. Opening "Correct…" shows it
// too. See docs/adr/0011-clinician-review.md.
import React, { useState } from "react";
import { ReviewFindings } from "../api/client.js";
import { correctionsOf, currentValue, reviewNote } from "../record/review.js";

export function CorrectionList({ holder }) {
  const list = correctionsOf(holder);
  const note = reviewNote(holder);
  if (!note && list.length === 0) return null;
  return (
    <div className="mt-1 text-xs text-emerald-800">
      {note && <p className="font-medium">{note}</p>}
      {list.length > 0 && (
        <ul className="list-disc list-inside">
          {list.map((c, i) => (
            <li key={i}>
              {c.field}: read as {c.original === null ? "nothing" : `"${c.original}"`}, corrected to {`"${c.corrected}"`} by{" "}
              {c.clinician} on {c.date}
              {c.note && <> ({c.note})</>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// `locked` is true while any review of the document is being saved.
// `sourceShown` says whether this value's source is the one on screen, and
// `onShowSource` puts it there.
function ReviewControls({
  itemId,
  holder,
  fields,
  onReview,
  locked = false,
  confirmLabel = "Confirm",
  sourceShown = false,
  onShowSource = () => {},
  acknowledgements = [],
}) {
  const [open, setOpen] = useState(false);
  const [field, setField] = useState(fields[0]?.field ?? "");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [findings, setFindings] = useState(null);
  const [error, setError] = useState(null);
  // Set when Confirm was pressed without the source on screen.
  const [askedToCheck, setAskedToCheck] = useState(false);
  const [saving, setSaving] = useState(false);
  // The signals ticked so far; every one of `acknowledgements` must be
  // before the item can be confirmed or corrected (ADR 0011).
  const [ticked, setTicked] = useState([]);
  const unacknowledged = acknowledgements.some((a) => !ticked.includes(a.signal));
  const busy = saving || locked;
  const acknowledge = acknowledgements.map((a) => a.signal);

  const chosen = fields.find((f) => f.field === field);
  const shown = chosen ? currentValue(holder, chosen.field, chosen.value) : null;

  async function send(review, acceptFindings = false) {
    setSaving(true);
    setError(null);
    try {
      await onReview(review, { acceptFindings });
      setOpen(false);
      setFindings(null);
      setValue("");
      setNote("");
    } catch (err) {
      if (err instanceof ReviewFindings) setFindings(err.findings);
      else setError(err.message || "The review could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  function confirm() {
    if (!sourceShown) {
      onShowSource();
      setAskedToCheck(true);
      return;
    }
    setAskedToCheck(false);
    send({ action: "confirm", item_id: itemId, acknowledge });
  }

  function toggleCorrect() {
    if (!open) onShowSource();
    setOpen((o) => !o);
  }

  function correction() {
    return { action: "correct", item_id: itemId, field, value, note: note || null, acknowledge };
  }

  return (
    <div className="mt-2 text-xs">
      {acknowledgements.length > 0 && (
        <fieldset className="mb-2 space-y-1 rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
          <legend className="sr-only">Before reviewing</legend>
          {acknowledgements.map((a) => (
            <label key={a.signal} className="flex items-start gap-1">
              <input
                type="checkbox"
                checked={ticked.includes(a.signal)}
                onChange={(e) =>
                  setTicked((t) => (e.target.checked ? [...t, a.signal] : t.filter((s) => s !== a.signal)))
                }
              />
              <span>{a.text}</span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || unacknowledged}
          onClick={confirm}
          className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-emerald-800 hover:bg-emerald-100 disabled:opacity-50"
        >
          {confirmLabel}
        </button>
        {fields.length > 0 && (
          <button
            type="button"
            disabled={busy}
            onClick={toggleCorrect}
            aria-expanded={open}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 hover:bg-slate-50 disabled:opacity-50"
          >
            Correct…
          </button>
        )}
      </div>
      {askedToCheck && sourceShown && (
        <p className="mt-1 text-slate-700" role="status">
          Its lines and the original are shown beside the list. Check them, then confirm.
        </p>
      )}
      {open && (
        <form
          className="mt-2 space-y-1 rounded border border-slate-200 bg-slate-50 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(correction());
          }}
        >
          <label className="block">
            <span className="text-slate-600">Field</span>
            <select
              value={field}
              onChange={(e) => {
                setField(e.target.value);
                setFindings(null);
              }}
              className="ml-2 border rounded px-1"
            >
              {fields.map((f) => (
                <option key={f.field} value={f.field}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <p className="text-slate-600">
            Shown now: {shown === null || shown === undefined ? <em>nothing read</em> : `"${shown}"`}
          </p>
          <label className="block">
            <span className="text-slate-600">Right value, as on the original</span>
            <input
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setFindings(null);
              }}
              maxLength={500}
              required
              className="mt-0.5 w-full border rounded px-1 py-0.5"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Note (optional)</span>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={1000}
              className="mt-0.5 w-full border rounded px-1 py-0.5"
            />
          </label>
          {findings && (
            <div className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
              <p className="font-medium">This value fails a check. Check it against the original:</p>
              <ul className="list-disc list-inside">
                {findings.map((f, i) => (
                  <li key={i}>{f.message}</li>
                ))}
              </ul>
              <button
                type="button"
                disabled={busy || unacknowledged}
                onClick={() => send(correction(), true)}
                className="mt-1 rounded border border-amber-400 bg-white px-2 py-0.5 hover:bg-amber-100"
              >
                Save it anyway
              </button>
            </div>
          )}
          {!findings && (
            <button
              type="submit"
              disabled={busy || unacknowledged || !value.trim()}
              className="rounded bg-blue-600 px-2 py-0.5 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Save correction
            </button>
          )}
        </form>
      )}
      {error && <p className="mt-1 text-red-600">{error}</p>}
    </div>
  );
}

export default ReviewControls;

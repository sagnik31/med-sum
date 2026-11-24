// src/components/ReviewControls.jsx
//
// A clinician's review of one item (or the patient's or the document's
// header values): confirm the reading, or correct one field. A corrected
// value that fails a check (its flag no longer agrees with its range, a
// date that isn't one) is shown with what failed and saved only when the
// clinician saves it again: that exact correction, never one edited since. Nothing is confirmed unless the value's source
// (its lines and its page of the original) is on screen: the first press
// of Confirm shows it, the next one confirms. Opening "Correct…" shows it
// too, and a correction is sent only while it is on screen. See docs/adr/0011-clinician-review.md.
import React, { useEffect, useState } from "react";
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

// `locked` is true while any review of the document is being saved or the
// record is being read again after one. `citations` are the holder's;
// `isShown(citations)` says whether one of them is on screen, and
// `onShow(citations)` puts the first there. A field that has a citation of
// its own (a header value) is corrected beside that one.
function ReviewControls({
  itemId,
  holder,
  fields,
  onReview,
  locked = false,
  confirmLabel = "Confirm",
  citations = [],
  isShown = () => false,
  onShow = () => {},
  acknowledgements = [],
}) {
  const [open, setOpen] = useState(false);
  const [field, setField] = useState(fields[0]?.field ?? "");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  // What the checks found on a correction, with the review they are about:
  // { review, list }, or null.
  const [found, setFound] = useState(null);
  const [error, setError] = useState(null);
  // What was pressed without the source on screen: "confirm", "save" or null.
  const [askedToCheck, setAskedToCheck] = useState(null);
  const [saving, setSaving] = useState(false);
  // The signals ticked so far; every one of `acknowledgements` must be
  // before the item can be confirmed or corrected (ADR 0011).
  const [ticked, setTicked] = useState([]);
  const unacknowledged = acknowledgements.some((a) => !ticked.includes(a.signal));
  const busy = saving || locked;
  const acknowledge = acknowledgements.map((a) => a.signal);

  // The record was read again: what the checks found was about the old one.
  useEffect(() => setFound(null), [holder]);

  const chosen = fields.find((f) => f.field === field);
  const shown = chosen ? currentValue(holder, chosen.field, chosen.value) : null;
  const fieldCitations = chosen?.citation ? [chosen.citation] : citations;
  const asked = askedToCheck === "save" ? fieldCitations : citations;

  async function send(review, acceptFindings = false) {
    setSaving(true);
    setError(null);
    try {
      await onReview(review, { acceptFindings });
      setOpen(false);
      setFound(null);
      setValue("");
      setNote("");
    } catch (err) {
      if (err instanceof ReviewFindings) setFound({ review, list: err.findings });
      else setError(err.message || "The review could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  // Whether the source is on screen; puts it there when it isn't, and
  // whatever was pressed must be pressed again.
  function sourceOnScreen(action, cited) {
    const shown = isShown(cited);
    setAskedToCheck(shown ? null : action);
    if (!shown) onShow(cited);
    return shown;
  }

  function saveCorrection(review, acceptFindings) {
    if (sourceOnScreen("save", fieldCitations)) send(review, acceptFindings);
  }

  function confirm() {
    if (!sourceOnScreen("confirm", citations)) return;
    send({ action: "confirm", item_id: itemId, acknowledge });
  }

  function toggleCorrect() {
    if (!open && !isShown(fieldCitations)) onShow(fieldCitations);
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
      {askedToCheck && isShown(asked) && (
        <p className="mt-1 text-slate-700" role="status">
          Its lines and the original are shown beside the list. Check them, then {askedToCheck === "save" ? "save again" : "confirm"}.
        </p>
      )}
      {open && (
        <form
          className="mt-2 space-y-1 rounded border border-slate-200 bg-slate-50 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            saveCorrection(correction());
          }}
        >
          {/* Locked while a save is out: its answer is about what was sent. */}
          <fieldset disabled={busy} className="space-y-1">
          <label className="block">
            <span className="text-slate-600">Field</span>
            <select
              value={field}
              onChange={(e) => {
                setField(e.target.value);
                setFound(null);
                const next = fields.find((f) => f.field === e.target.value);
                if (next?.citation && !isShown([next.citation])) onShow([next.citation]);
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
                setFound(null);
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
              onChange={(e) => {
                setNote(e.target.value);
                setFound(null);
              }}
              maxLength={1000}
              className="mt-0.5 w-full border rounded px-1 py-0.5"
            />
          </label>
          {found && (
            <div className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
              <p className="font-medium">This value fails a check. Check it against the original:</p>
              <ul className="list-disc list-inside">
                {found.list.map((f, i) => (
                  <li key={i}>{f.message}</li>
                ))}
              </ul>
              <button
                type="button"
                disabled={busy || unacknowledged}
                onClick={() => saveCorrection(found.review, true)}
                className="mt-1 rounded border border-amber-400 bg-white px-2 py-0.5 hover:bg-amber-100"
              >
                Save it anyway
              </button>
            </div>
          )}
          {!found && (
            <button
              type="submit"
              disabled={busy || unacknowledged || !value.trim()}
              className="rounded bg-blue-600 px-2 py-0.5 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Save correction
            </button>
          )}
          </fieldset>
        </form>
      )}
      {error && <p className="mt-1 text-red-600">{error}</p>}
    </div>
  );
}

export default ReviewControls;

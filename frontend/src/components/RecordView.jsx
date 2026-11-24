// src/components/RecordView.jsx
//
// Every value of a document's record with the page and lines it was read
// from. Choosing a value's source shows those lines on the page beside it.
// `record` is the reviewed view: a corrected value is shown as corrected,
// with its derived values computed again, and what was read is listed
// beside it. Derived values were computed by code and are labelled. See
// docs/adr/0003-printed-and-derived-values.md and
// docs/adr/0011-clinician-review.md.
import React, { useState } from "react";
import {
  citationKey,
  documentDate,
  documentValues,
  formatCitation,
  patientValues,
  recordGroups,
  sourceLines,
  sourceShownFor,
  statusLabel,
} from "../record/recordView.js";
import {
  acknowledgementsNeeded,
  addableDates,
  auditMark,
  correctableFields,
  headerTarget,
} from "../record/review.js";
import ReviewControls, { CorrectionList } from "./ReviewControls.jsx";
import SourcePanel from "./SourcePanel.jsx";

function Status({ status, reasons }) {
  const label = statusLabel(status);
  if (!label) return null;
  const flagged = status === "low_confidence";
  return (
    <div className="mt-1 text-xs">
      <span
        className={`inline-block rounded px-1.5 py-0.5 ${
          flagged ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600"
        }`}
      >
        {label}
      </span>
      {reasons.length > 0 && (
        <ul className="mt-1 list-disc list-inside text-amber-800">
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// A value's place in an open audit: still to check, or checked during it.
function AuditMark({ audit, id }) {
  const mark = auditMark(audit, id);
  if (!mark) return null;
  const toCheck = mark === "to check";
  return (
    <span
      className={`mt-1 mr-1 inline-block rounded px-1.5 py-0.5 text-xs ${
        toCheck ? "bg-sky-100 text-sky-800" : "bg-emerald-100 text-emerald-800"
      }`}
    >
      Audit: {mark}
    </span>
  );
}

function SourceButton({ citation, selected, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(citation)}
      aria-pressed={selected}
      title="Show these lines on the page"
      className={`shrink-0 text-xs font-mono rounded px-1.5 py-0.5 border ${
        selected ? "bg-yellow-100 border-yellow-300" : "bg-white hover:bg-slate-50"
      }`}
    >
      {formatCitation(citation)}
    </button>
  );
}

function Fields({ fields, derived }) {
  if (fields.length === 0) return null;
  return (
    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 text-xs">
      {fields.map((f) => (
        <React.Fragment key={f.label}>
          <dt className="text-slate-500">
            {f.label}
            {derived && <span className="ml-1 italic">(derived)</span>}
          </dt>
          <dd className={derived ? "italic text-slate-600" : "text-slate-900"}>{f.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

// The header values of one holder (the patient or the document) as
// correctable fields.
function headerFields(values) {
  return values.map((v) => ({ field: headerTarget(v.key).field, label: v.label, value: v.text, citation: v.citation }));
}

// `onReview`, when given, shows the review controls: it is called with a
// review ({action, item_id, field?, value?, note?}) and {acceptFindings},
// and its promise settles when the review is saved (see ReviewControls).
// `saving` is true while a review is being saved; every control waits.
// `audit` is the record's open audit, or null: each value is marked as
// still to check or checked during it.
function RecordView({ record, initialCitation, onReview, saving = false, sourceSlot, audit = null }) {
  const [selected, setSelected] = useState(() => {
    if (!initialCitation) return null;
    return sourceLines(record.pages, initialCitation).found ? initialCitation : null;
  });
  const [page, setPage] = useState(selected?.page ?? 1);

  function select(c) {
    setSelected(c);
    setPage(c.page);
  }
  const isSelected = (c) => selected !== null && citationKey(c) === citationKey(selected);
  // The review controls show a value's source before a review of it.
  const sourceProps = (citations) => ({
    citations,
    isShown: (cited) => sourceShownFor(cited, selected, page),
    onShow: (cited) => {
      const first = cited.find(Boolean);
      if (first) select(first);
    },
  });

  const patient = patientValues(record);
  const docValues = documentValues(record);
  const header = [...patient, ...docValues];
  const raw = (key, id) => (record[key] || []).find((i) => i.id === id);
  const date = documentDate(record);
  const groups = recordGroups(record);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-4 min-w-0">
        {(header.length > 0 || date || onReview) && (
          <section>
            <h3 className="text-sm font-semibold text-slate-700 mb-1">As printed on the document</h3>
            <ul className="divide-y divide-slate-100 border border-slate-200 rounded-md">
              {header.map((v) => (
                <li
                  key={v.key}
                  className={`flex items-start justify-between gap-2 px-3 py-2 text-sm ${
                    isSelected(v.citation) ? "bg-yellow-50" : ""
                  }`}
                >
                  <div className="min-w-0">
                    <span className="text-slate-500">{v.label}: </span>
                    <span className="break-words">{v.text}</span>
                  </div>
                  <SourceButton citation={v.citation} selected={isSelected(v.citation)} onSelect={select} />
                </li>
              ))}
              {date && (
                <li className="px-3 py-2 text-sm italic text-slate-600">
                  {date.label} (derived): {date.value}
                </li>
              )}
              {[
                ["patient", record.patient, patient, "Confirm the patient details"],
                ["document", record.document, docValues, "Confirm the document details"],
              ].map(([id, holder, values, label]) =>
                holder && (holder.corrections?.length || holder.provenance?.reviewed_by || onReview) ? (
                  <li key={id} className="px-3 py-2">
                    {holder.provenance && <AuditMark audit={audit} id={id} />}
                    <Status status={holder.provenance?.status} reasons={holder.provenance?.low_confidence_reasons ?? []} />
                    <CorrectionList holder={holder} />
                    {onReview && holder.provenance && (
                      <ReviewControls
                        itemId={id}
                        holder={holder}
                        fields={
                          id === "document"
                            ? [...headerFields(values), ...addableDates(holder)]
                            : headerFields(values)
                        }
                        onReview={onReview}
                        locked={saving}
                        confirmLabel={label}
                        acknowledgements={acknowledgementsNeeded(holder)}
                        {...sourceProps(values.map((v) => v.citation))}
                      />
                    )}
                  </li>
                ) : null
              )}
            </ul>
          </section>
        )}

        {groups.map((g) => (
          <section key={g.key}>
            <h3 className="text-sm font-semibold text-slate-700 mb-1">{g.title}</h3>
            <ul className="divide-y divide-slate-100 border border-slate-200 rounded-md">
              {g.items.map((it) => (
                <li
                  key={it.id}
                  className={`px-3 py-2 text-sm ${isSelected(it.citation) ? "bg-yellow-50" : ""}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-medium break-words min-w-0">{it.title}</span>
                    <SourceButton citation={it.citation} selected={isSelected(it.citation)} onSelect={select} />
                  </div>
                  <Fields fields={it.printed} />
                  <Fields fields={it.derived} derived />
                  {it.status && <AuditMark audit={audit} id={it.id} />}
                  <Status status={it.status} reasons={it.reasons} />
                  <CorrectionList holder={raw(g.key, it.id)} />
                  {onReview && it.status && (
                    <ReviewControls
                      itemId={it.id}
                      holder={raw(g.key, it.id)}
                      fields={correctableFields(g.key, raw(g.key, it.id).printed)}
                      onReview={onReview}
                      locked={saving}
                      {...sourceProps([it.citation])}
                    />
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}

        {header.length === 0 && groups.length === 0 && (
          <p className="text-sm text-slate-600">
            No values were read from this document; its pages are shown as they were transcribed.
          </p>
        )}
      </div>

      <div className="md:sticky md:top-4 self-start min-w-0">
        {sourceSlot && sourceSlot(page)}
        <SourcePanel pages={record.pages} page={page} citation={selected} onPage={setPage} />
      </div>
    </div>
  );
}

export default RecordView;

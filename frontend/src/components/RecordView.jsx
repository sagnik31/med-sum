// src/components/RecordView.jsx
//
// Every value of a document's record with the page and lines it was read
// from. Choosing a value's source shows those lines on the page beside it.
// Printed values are as the model read them; derived values were computed
// by code and are labelled. See docs/adr/0003-printed-and-derived-values.md.
import React, { useState } from "react";
import {
  citationKey,
  documentDate,
  documentValues,
  formatCitation,
  patientValues,
  recordGroups,
  sourceLines,
  statusLabel,
} from "../record/recordView.js";
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

function RecordView({ record, initialCitation }) {
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

  const header = [...patientValues(record), ...documentValues(record)];
  const date = documentDate(record);
  const groups = recordGroups(record);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-4 min-w-0">
        {header.length > 0 && (
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
                  <Status status={it.status} reasons={it.reasons} />
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
        <SourcePanel pages={record.pages} page={page} citation={selected} onPage={setPage} />
      </div>
    </div>
  );
}

export default RecordView;

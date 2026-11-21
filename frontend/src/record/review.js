// src/record/review.js
//
// What the review screen needs from a record: the fields of each item a
// clinician can correct, and what has been reviewed so far. Plain
// functions, so `npm test` checks them.
//
// A correction never replaces what was read: the record keeps the model's
// reading in `printed` and each correction beside it, with who and when.
// See docs/adr/0011-clinician-review.md.

// The readings of each kind of item a clinician can correct, in the order
// shown. Labels from fixed lists (a remark's kind, a condition's kind) and
// citations are not readings, so they aren't here.
const CORRECTABLE = {
  observations: [
    ["test_name", "Test"],
    ["value_text", "Result"],
    ["unit", "Unit"],
    ["flag_printed", "Flag"],
    ["panel", "Panel"],
  ],
  medications: [
    ["name", "Name"],
    ["form", "Form"],
    ["strength", "Strength"],
    ["dose", "Dose"],
    ["frequency", "Frequency"],
    ["route", "Route"],
    ["duration", "Duration"],
    ["instructions", "Instructions"],
  ],
  conditions: [["text", "Text"]],
  remarks: [
    ["text", "Text"],
    ["test_name", "Test"],
    ["panel", "Panel"],
  ],
  vitals: [
    ["name", "Name"],
    ["value_text", "Value"],
    ["unit", "Unit"],
  ],
  allergies: [
    ["text", "Text"],
    ["substance", "Substance"],
    ["reaction", "Reaction"],
  ],
  procedures: [["name", "Name"], ["date", "Date"]],
  encounters: [["kind", "Kind"], ["date", "Date"], ["facility", "Facility"]],
  imaging_findings: [
    ["finding", "Finding"],
    ["modality", "Modality"],
    ["body_site", "Body site"],
    ["impression", "Impression"],
  ],
};

const PRINTED_TEXT_FIELDS = new Set(["date", "facility"]);

function textOf(value) {
  if (value === null || value === undefined) return null;
  return typeof value === "object" ? value.text ?? null : String(value);
}

// The fields of one clinical item a clinician can correct:
// [{ field, label, value }], value being what was read (null when the
// model read nothing for a plain text field). A printed-text field that
// was not read (a date nobody printed) can't be corrected and is left out.
export function correctableFields(groupKey, printed) {
  const out = [];
  for (const [field, label] of CORRECTABLE[groupKey] || []) {
    const value = printed[field];
    if ((value === null || value === undefined) && PRINTED_TEXT_FIELDS.has(field)) continue;
    out.push({ field, label, value: textOf(value) });
  }
  if (groupKey === "observations") {
    (printed.reference_ranges || []).forEach((r, i) =>
      out.push({ field: `reference_ranges[${i}]`, label: `Reference range ${i + 1}`, value: r })
    );
  }
  return out;
}

const DOCUMENT_DATES = [
  ["report_date", "Report date"],
  ["collection_date", "Collection date"],
  ["prescription_date", "Prescription date"],
];

// The dates a clinician may add to the document: [{ field, label, value }],
// value null. An undated document goes to review so that it can be dated
// there (docs/adr/0003-printed-and-derived-values.md), so when no date was
// read each date nobody printed is offered; a date added earlier stays
// correctable.
export function addableDates(document) {
  if (!document) return [];
  const printed = document.printed || {};
  const added = new Set((document.corrections || []).map((c) => c.field));
  const undated = !DOCUMENT_DATES.some(([field]) => printed[field]);
  return DOCUMENT_DATES.filter(([field]) => !printed[field] && (undated || added.has(field))).map(
    ([field, label]) => ({ field, label: `${label} (not read; add it)`, value: null })
  );
}

// Document-level signals a review must acknowledge by name: a plain
// confirm doesn't clear them (docs/adr/0011-clinician-review.md). Each is
// the statement the clinician ticks.
const ACKNOWLEDGED_BY_NAME = {
  "LC-13": "I have checked that this document belongs to this patient.",
  "LC-17": "I have checked the results table on the original: no result is missing from the list.",
};

// What a review of this holder must acknowledge, while it awaits review:
// [{ signal, text }], in the order of its reasons.
export function acknowledgementsNeeded(holder) {
  const p = holder?.provenance;
  if (!p || p.status !== "low_confidence") return [];
  const out = [];
  for (const reason of p.low_confidence_reasons || []) {
    const signal = /^(LC-\d{2})\b/.exec(reason)?.[1];
    if (signal && ACKNOWLEDGED_BY_NAME[signal] && !out.some((a) => a.signal === signal)) {
      out.push({ signal, text: ACKNOWLEDGED_BY_NAME[signal] });
    }
  }
  return out;
}

// A header value's key ("patient.name", "document.clinicians[0]") as the
// review API names it: { itemId, field }.
export function headerTarget(key) {
  const dot = key.indexOf(".");
  return { itemId: key.slice(0, dot), field: key.slice(dot + 1) };
}

// The corrections of an item (or the patient, or the document), oldest
// first: [{ field, original, corrected, clinician, date, note }].
export function correctionsOf(holder) {
  return (holder?.corrections || []).map((c) => ({
    field: c.field,
    original: c.original,
    corrected: c.corrected,
    clinician: c.clinician,
    date: (c.at || "").slice(0, 10),
    note: c.note ?? null,
  }));
}

// The value a field shows now: its latest correction, else what was read.
export function currentValue(holder, field, read) {
  const list = holder?.corrections || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i].field === field) return list[i].corrected;
  }
  return read;
}

// "Corrected by Dr X on 2025-03-05" or "Confirmed by Dr X on 2025-03-05",
// or null when nobody has reviewed it.
export function reviewNote(holder) {
  const p = holder?.provenance;
  if (!p || !p.reviewed_by) return null;
  const date = (p.reviewed_at || "").slice(0, 10);
  if (p.status === "clinician_corrected") {
    const last = (holder.corrections || []).at(-1);
    return `Corrected by ${last?.clinician ?? p.reviewed_by} on ${(last?.at || p.reviewed_at || "").slice(0, 10)}`;
  }
  if (p.status === "clinician_confirmed") return `Confirmed by ${p.reviewed_by} on ${date}`;
  return null;
}

// How many values of the record still await review.
export function flaggedCount(record) {
  const holders = [record.document, record.patient];
  for (const group of Object.keys(CORRECTABLE)) holders.push(...(record[group] || []));
  return holders.filter((h) => h?.provenance?.status === "low_confidence").length;
}

// What a saved review changes on the page, from the API's answer
// ({ status, record_updated_at, flagged }): the document's status as the
// page names it ("ready" once verified) and the record's new updated_at,
// which the next review must send. Taken from the answer itself, so a
// second review needs no reload of the record first.
export function reviewOutcome(result, status) {
  return {
    status: result?.status === "verified" ? "ready" : status,
    recordUpdatedAt: result?.record_updated_at ?? null,
  };
}

// How many values of the record a clinician has confirmed or corrected
// (the document's and the patient's details count once each).
export function reviewedCount(record) {
  if (!record) return 0;
  const holders = [record.document, record.patient];
  for (const group of Object.keys(CORRECTABLE)) holders.push(...(record[group] || []));
  return holders.filter((h) => h?.provenance?.reviewed_by || h?.corrections?.length).length;
}

// What to ask before reprocessing: a new run replaces the record, so its
// reviews start again (docs/adr/0011-clinician-review.md), and on a CPU it
// takes minutes a page.
export function reprocessQuestion(record) {
  const n = reviewedCount(record);
  const lost =
    n === 0
      ? ""
      : ` The ${n === 1 ? "review" : `${n} reviews`} made on it will be discarded and must be made again.`;
  return `Process this document again from the start? The new run replaces what was read.${lost} It can take several minutes a page.`;
}

// A queue entry's wait, in plain words: "3 values to review", "audit".
export function queueLabel(entry) {
  if (entry.reason === "audit") return "audit sample: check every value";
  const n = entry.flagged;
  return n === 1 ? "1 value to review" : `${n} values to review`;
}

// Where a queue entry opens: the document in review mode, marked as an
// audit for an audit sample.
export function reviewLink(entry) {
  const audit = entry.reason === "audit" ? "&audit=1" : "";
  return `/insights/${encodeURIComponent(entry.document_id)}?review=1${audit}`;
}

// The original upload's URL; a PDF opens at `page` when one is given.
export function originalFileUrl(base, documentId, page) {
  const url = `${base}/documents/${encodeURIComponent(documentId)}/file`;
  return Number.isInteger(page) && page >= 1 ? `${url}#page=${page}` : url;
}

// Whether a document's original is a PDF (else it is an image), from the
// stored name's extension, as the API decides its type.
export function isPdfName(name) {
  return /\.pdf$/i.test(name || "");
}

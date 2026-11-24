// src/record/recordView.js
//
// Turns a stored document record (GET /documents/{id}/record) into what the
// document page shows: each value with the page and lines it was read from.
// Plain functions, no React, so `npm test` can check them with Node's test
// runner.
//
// The page shows the record as clinicians reviewed it (reviewedRecordOf):
// printed values are what the model read off the page, or a clinician's
// correction of one, and derived values were computed by code from them
// and are always labelled as such. A corrected value's derived values are
// computed again by the AI service; this module never computes one.
// See docs/adr/0003-printed-and-derived-values.md. Every printed value has
// a citation into the page transcriptions (docs/adr/0002-two-model-steps.md),
// and the page shows those lines next to it so a clinician can check the
// reading against the source.

// The record a document page shows: the reviewed view from
// GET /documents/{id}/record?view=reviewed. Never the stored record, whose
// values and derived values are still those of the model's reading after a
// correction. Null when the response has no view.
export function reviewedRecordOf(response) {
  return response?.reviewed ?? null;
}

// The clinical groups, in the record's order.
export const GROUPS = [
  { key: "observations", title: "Test results" },
  { key: "medications", title: "Medicines" },
  { key: "conditions", title: "Conditions" },
  { key: "remarks", title: "Remarks" },
  { key: "vitals", title: "Vitals" },
  { key: "allergies", title: "Allergies" },
  { key: "procedures", title: "Procedures" },
  { key: "encounters", title: "Encounters" },
  { key: "imaging_findings", title: "Imaging findings" },
];

const STATUS_LABELS = {
  extracted: "not yet checked",
  validated: "checked",
  low_confidence: "awaiting review",
  clinician_confirmed: "confirmed by a clinician",
  clinician_corrected: "corrected by a clinician",
};

// A plain-words label for an item's review status; null without one.
export function statusLabel(status) {
  if (!status) return null;
  return STATUS_LABELS[status] || status.replaceAll("_", " ");
}

// A stable key for a citation, used to select it.
export function citationKey(c) {
  return c ? `${c.page}:${c.first_line}-${c.last_line}` : "";
}

// Whether one of a holder's citations (an item's, or the patient's or the
// document's header values) is the one selected and `page`, the page on
// screen, is its page, so its lines and its page of the original are on
// screen: turning to another page takes them off it. A value is confirmed only beside its
// source (docs/adr/0011-clinician-review.md). A holder with no citation
// (a patient with nothing printed) has no lines to show: true.
export function sourceShownFor(citations, selected, page) {
  const cited = (citations || []).filter(Boolean);
  if (cited.length === 0) return true;
  if (!selected || selected.page !== page) return false;
  const key = citationKey(selected);
  return cited.some((c) => citationKey(c) === key);
}

// "p. 1, line 6" or "p. 1, lines 6-7".
export function formatCitation(c) {
  if (c.first_line === c.last_line) return `p. ${c.page}, line ${c.first_line}`;
  return `p. ${c.page}, lines ${c.first_line}-${c.last_line}`;
}

// A citation from a link's query (?page=1&lines=6 or ?page=1&lines=6-7), as
// the patient summary's source links make; null when absent or malformed.
export function citationFromQuery(params) {
  const page = params.get("page");
  const lines = params.get("lines");
  if (!page || !lines || !/^\d+$/.test(page)) return null;
  const m = /^(\d+)(?:-(\d+))?$/.exec(lines);
  if (!m) return null;
  const first = Number(m[1]);
  const last = m[2] ? Number(m[2]) : first;
  if (Number(page) < 1 || first < 1 || last < first) return null;
  return { page: Number(page), first_line: first, last_line: last };
}

// The query string for a link to one citation on a document page.
export function citationQuery(c) {
  const lines = c.first_line === c.last_line ? `${c.first_line}` : `${c.first_line}-${c.last_line}`;
  return `?page=${c.page}&lines=${lines}`;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// The app path a source link in the patient summary leads to, from the
// <cite> element's data attributes (data-document-id, data-page,
// data-lines), or null when any is missing or malformed. The summary is
// HTML from the AI service, so nothing in it is used unchecked: the id must
// be a UUID and the lines numbers.
export function citeLink({ documentId, page, lines } = {}) {
  if (!documentId || !UUID.test(documentId)) return null;
  const c = citationFromQuery(new URLSearchParams({ page: page ?? "", lines: lines ?? "" }));
  return c ? `/insights/${documentId}${citationQuery(c)}` : null;
}

// One page's lines, numbered, with the cited ones marked. `matches` is false
// when the citation's quote isn't the text of those lines (the pipeline
// checks this, so it would mean a bug); `found` is false when the page or
// the lines don't exist.
export function sourceLines(pages, c) {
  const page = (pages || []).find((p) => p.page === c.page);
  if (!page) return { page: c.page, lines: [], found: false, matches: false };
  const lines = page.lines.map((text, i) => ({
    n: i + 1,
    text,
    cited: i + 1 >= c.first_line && i + 1 <= c.last_line,
  }));
  const found = c.last_line <= page.lines.length;
  const quoted = page.lines.slice(c.first_line - 1, c.last_line).join("\n");
  return { page: c.page, lines, found, matches: found && quoted === c.quote };
}

// ---------- Header values (patient and document) ----------

const PATIENT_FIELDS = [
  ["name", "Name"],
  ["age", "Age"],
  ["sex", "Sex"],
  ["date_of_birth", "Date of birth"],
  ["patient_id", "Patient ID"],
];

const DATE_FIELDS = [
  ["report_date", "Report date"],
  ["collection_date", "Collection date"],
  ["prescription_date", "Prescription date"],
];

const DATE_SOURCE_LABELS = {
  report_date: "the report date",
  collection_date: "the collection date",
  prescription_date: "the prescription date",
};

function headerValue(key, label, printed, provenance) {
  return {
    key,
    label,
    text: printed.text,
    citation: printed.citation,
    status: provenance?.status ?? null,
    reasons: provenance?.low_confidence_reasons ?? [],
  };
}

// The patient as printed on the document: [{key, label, text, citation, status, reasons}].
export function patientValues(record) {
  const pt = record.patient;
  if (!pt) return [];
  return PATIENT_FIELDS.filter(([k]) => pt.printed[k]).map(([k, label]) =>
    headerValue(`patient.${k}`, label, pt.printed[k], pt.provenance)
  );
}

// The document's header values, then its computed date.
export function documentValues(record) {
  const d = record.document;
  const out = [];
  if (d.printed.facility) {
    out.push(headerValue("document.facility", "Facility", d.printed.facility, d.provenance));
  }
  (d.printed.clinicians || []).forEach((c, i) =>
    out.push(headerValue(`document.clinicians[${i}]`, "Clinician", c, d.provenance))
  );
  for (const [k, label] of DATE_FIELDS) {
    if (d.printed[k]) out.push(headerValue(`document.${k}`, label, d.printed[k], d.provenance));
  }
  return out;
}

// The document date code chose, as a derived value, or null when undated.
export function documentDate(record) {
  const dd = record.document.derived;
  if (!dd || !dd.document_date) return null;
  return {
    label: "Document date",
    value: `${dd.document_date} (from ${DATE_SOURCE_LABELS[dd.date_source] || dd.date_source})`,
  };
}

// ---------- Clinical items ----------

function field(label, value) {
  return value === null || value === undefined || value === "" ? null : { label, value: String(value) };
}

function compact(list) {
  return list.filter(Boolean);
}

function withUnit(value, unit) {
  return unit ? `${value} ${unit}` : value;
}

function rangeText(r) {
  if (r.low !== null && r.low !== undefined && r.high !== null && r.high !== undefined) {
    return `${r.low} to ${r.high}`;
  }
  if (r.low !== null && r.low !== undefined) return `at least ${r.low}`;
  if (r.high !== null && r.high !== undefined) return `at most ${r.high}`;
  return null;
}

function scheduleText(s) {
  if (!s) return null;
  const parts = [];
  if (s.morning !== null && s.morning !== undefined) {
    parts.push(`morning ${s.morning}, noon ${s.noon ?? 0}, night ${s.night ?? 0}`);
  }
  if (s.times_per_day !== null && s.times_per_day !== undefined) {
    parts.push(`${s.times_per_day} a day`);
  }
  if (s.as_needed) parts.push("as needed");
  return parts.length ? parts.join("; ") : null;
}

// What each group shows: a title, the other printed values and the derived
// values. Printed values come from `printed` only, derived from `derived`.
const DESCRIBE = {
  observations: (p, d) => ({
    title: p.test_name,
    printed: compact([
      field("Panel", p.panel),
      field("Result", withUnit(p.value_text, p.unit)),
      field("Reference range", (p.reference_ranges || []).join("; ")),
      field("Flag", p.flag_printed),
    ]),
    derived: compact([
      field("Value", d.value_num),
      field("Range", (d.ranges || []).map(rangeText).filter(Boolean).join("; ")),
      field("Flag", d.flag_computed),
      d.converted && field("Converted", `${d.converted.value} ${d.converted.unit} (from ${d.converted.from_unit})`),
    ]),
  }),
  medications: (p, d) => ({
    title: compact([p.form, p.name, p.strength]).join(" "),
    printed: compact([
      field("Dose", p.dose),
      field("Frequency", p.frequency),
      field("Route", p.route),
      field("Duration", p.duration),
      field("Instructions", p.instructions),
    ]),
    derived: compact([
      field("Schedule", scheduleText(d.schedule)),
      field("Days", d.duration_days),
    ]),
  }),
  conditions: (p) => ({
    title: p.text,
    printed: compact([field("Kind", p.kind?.replaceAll("_", " "))]),
    derived: [],
  }),
  remarks: (p) => ({
    title: p.text,
    printed: compact([
      field("Kind", p.kind),
      field("On", p.scope === "test" ? p.test_name : p.scope === "panel" ? p.panel : "the document"),
    ]),
    derived: [],
  }),
  vitals: (p, d) => ({
    title: p.name,
    printed: compact([field("Value", withUnit(p.value_text, p.unit))]),
    derived: compact([
      field("Kind", d.kind?.replaceAll("_", " ")),
      field("Value", d.value_num),
      d.systolic !== null && d.systolic !== undefined && field("Systolic / diastolic", `${d.systolic} / ${d.diastolic}`),
    ]),
  }),
  allergies: (p, d) => ({
    title: p.text,
    printed: compact([field("Substance", p.substance), field("Reaction", p.reaction)]),
    derived: d.no_known_allergies ? [{ label: "No known allergies", value: "yes" }] : [],
  }),
  procedures: (p) => ({
    title: p.name,
    printed: compact([field("Date", p.date?.text)]),
    derived: [],
  }),
  encounters: (p) => ({
    title: p.kind,
    printed: compact([field("Date", p.date?.text), field("Facility", p.facility?.text)]),
    derived: [],
  }),
  imaging_findings: (p) => ({
    title: p.finding,
    printed: compact([
      field("Modality", p.modality),
      field("Body site", p.body_site),
      field("Impression", p.impression),
    ]),
    derived: [],
  }),
};

// Every non-empty group: [{key, title, items: [{id, title, printed,
// derived, citation, status, reasons}]}].
export function recordGroups(record) {
  return GROUPS.map(({ key, title }) => ({
    key,
    title,
    items: (record[key] || []).map((item) => {
      const shown = DESCRIBE[key](item.printed, item.derived || {});
      return {
        id: item.id,
        ...shown,
        citation: item.printed.citation,
        status: item.provenance?.status ?? null,
        reasons: item.provenance?.low_confidence_reasons ?? [],
      };
    }),
  })).filter((g) => g.items.length > 0);
}

// Every citation in the record, header values first, in order. The first
// one is selected when the page opens without a cited link.
export function allCitations(record) {
  return [
    ...patientValues(record).map((v) => v.citation),
    ...documentValues(record).map((v) => v.citation),
    ...recordGroups(record).flatMap((g) => g.items.map((i) => i.citation)),
  ];
}

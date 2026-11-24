// Run with `npm test` (Node's own test runner; no extra packages).
// The fixture is a synthetic record; scripts/tests/test_frontend_fixtures.py
// checks that it is a valid record, so these tests follow the real schema.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  allCitations,
  citationFromQuery,
  citationKey,
  citationQuery,
  citeLink,
  documentDate,
  documentValues,
  formatCitation,
  patientValues,
  recordGroups,
  reviewedRecordOf,
  sourceLines,
  sourceShownFor,
  statusLabel,
} from "./recordView.js";

const record = JSON.parse(readFileSync(new URL("./fixtures/record.json", import.meta.url), "utf8"));

function item(groups, key, id) {
  return groups.find((g) => g.key === key).items.find((i) => i.id === id);
}

test("every printed value carries a citation that points at its own lines", () => {
  const cites = allCitations(record);
  assert.equal(cites.length, 3 + 3 + 6); // patient, document, clinical items
  for (const c of cites) {
    const src = sourceLines(record.pages, c);
    assert.ok(src.found, formatCitation(c));
    assert.ok(src.matches, `${formatCitation(c)}: the quote isn't those lines`);
    assert.deepEqual(
      src.lines.filter((l) => l.cited).map((l) => l.n),
      Array.from({ length: c.last_line - c.first_line + 1 }, (_, i) => c.first_line + i)
    );
  }
});

test("only non-empty groups are shown, in the record's order", () => {
  assert.deepEqual(
    recordGroups(record).map((g) => g.key),
    ["observations", "medications", "conditions", "remarks", "vitals"]
  );
});

test("printed and derived values are kept apart", () => {
  const hb = item(recordGroups(record), "observations", "obs-1");
  assert.equal(hb.title, "Haemoglobin");
  assert.deepEqual(hb.printed, [
    { label: "Panel", value: "COMPLETE BLOOD COUNT" },
    { label: "Result", value: "10.2 g/dL" },
    { label: "Reference range", value: "12.0-15.5" },
    { label: "Flag", value: "L" },
  ]);
  assert.deepEqual(hb.derived, [
    { label: "Value", value: "10.2" },
    { label: "Range", value: "12 to 15.5" },
    { label: "Flag", value: "low" },
  ]);
  const plt = item(recordGroups(record), "observations", "obs-2");
  assert.ok(plt.derived.some((d) => d.label === "Converted" && d.value === "210 10^3/uL (from lakh/cumm)"));
  // Derived values never appear among the printed ones.
  assert.ok(!plt.printed.some((p) => p.value.includes("210")));
});

test("a flagged item shows its status and its reasons", () => {
  const plt = item(recordGroups(record), "observations", "obs-2");
  assert.equal(statusLabel(plt.status), "awaiting review");
  assert.deepEqual(plt.reasons, ["The unit was converted; check the printed unit."]);
  const med = item(recordGroups(record), "medications", "med-1");
  assert.equal(med.status, null); // no provenance: no label
  assert.equal(statusLabel(med.status), null);
});

test("medicines, remarks and vitals read as printed", () => {
  const groups = recordGroups(record);
  const med = item(groups, "medications", "med-1");
  assert.equal(med.title, "Tab Exampleine 50 mcg");
  assert.deepEqual(med.derived, [
    { label: "Schedule", value: "morning 1, noon 0, night 0; 1 a day" },
    { label: "Days", value: "30" },
  ]);
  assert.deepEqual(item(groups, "remarks", "rem-1").printed, [
    { label: "Kind", value: "comment" },
    { label: "On", value: "Haemoglobin" },
  ]);
  assert.deepEqual(item(groups, "vitals", "vit-1").derived, [
    { label: "Kind", value: "blood pressure" },
    { label: "Systolic / diastolic", value: "130 / 85" },
  ]);
});

test("header values: the patient and the document as printed, the date as derived", () => {
  assert.deepEqual(
    patientValues(record).map((v) => [v.label, v.text]),
    [["Name", "Test Patient A"], ["Age", "40 Y"], ["Sex", "F"]]
  );
  assert.deepEqual(
    documentValues(record).map((v) => [v.label, v.text]),
    [["Facility", "Example Diagnostics"], ["Clinician", "Ref. by: Dr. A. Example"], ["Report date", "05/09/2024"]]
  );
  assert.deepEqual(documentDate(record), {
    label: "Document date",
    value: "2024-09-05 (from the report date)",
  });
});

test("a record without a patient or a date shows none", () => {
  const bare = {
    ...record,
    patient: null,
    document: { ...record.document, derived: { date_source: "undated", document_date: null } },
  };
  assert.deepEqual(patientValues(bare), []);
  assert.equal(documentDate(bare), null);
});

test("a citation that doesn't fit the page is reported, not shown as found", () => {
  const off = { page: 1, first_line: 7, last_line: 99, quote: "x" };
  assert.equal(sourceLines(record.pages, off).found, false);
  assert.equal(sourceLines(record.pages, { ...off, page: 5 }).found, false);
  const wrongQuote = { page: 1, first_line: 6, last_line: 6, quote: "something else" };
  const src = sourceLines(record.pages, wrongQuote);
  assert.equal(src.found, true);
  assert.equal(src.matches, false);
});

test("citations round-trip through a link's query", () => {
  for (const c of [
    { page: 1, first_line: 6, last_line: 6 },
    { page: 2, first_line: 1, last_line: 3 },
  ]) {
    assert.deepEqual(citationFromQuery(new URLSearchParams(citationQuery(c))), c);
  }
  assert.equal(formatCitation({ page: 1, first_line: 6, last_line: 6 }), "p. 1, line 6");
  assert.equal(formatCitation({ page: 2, first_line: 1, last_line: 3 }), "p. 2, lines 1-3");
  assert.equal(citationKey({ page: 2, first_line: 1, last_line: 3 }), "2:1-3");
});

test("a malformed link selects nothing", () => {
  for (const q of ["", "?page=1", "?lines=4", "?page=x&lines=4", "?page=1&lines=5-4", "?page=0&lines=1", "?page=1&lines=0", "?page=1&lines=4-"]) {
    assert.equal(citationFromQuery(new URLSearchParams(q)), null, q);
  }
});

test("a summary source link leads to the document's lines, and only a well-formed one", () => {
  const id = "00000000-0000-4000-8000-000000000001";
  assert.equal(citeLink({ documentId: id, page: "1", lines: "6" }), `/insights/${id}?page=1&lines=6`);
  assert.equal(citeLink({ documentId: id, page: "2", lines: "1-3" }), `/insights/${id}?page=2&lines=1-3`);
  for (const bad of [
    {},
    { documentId: id, page: "1" },
    { documentId: "../admin/audit", page: "1", lines: "6" },
    { documentId: `${id}/x`, page: "1", lines: "6" },
    { documentId: id, page: "1", lines: "6&next=//evil" },
    { documentId: id, page: "javascript:1", lines: "6" },
  ]) {
    assert.equal(citeLink(bad), null, JSON.stringify(bad));
  }
});

test("the page shows the reviewed view, never the stored record", () => {
  const view = { ...record, observations: [] };
  assert.equal(reviewedRecordOf({ record, reviewed: view }), view);
  // Without a view there is nothing to show: the stored record would put
  // a misread value and its flag where the correction belongs.
  assert.equal(reviewedRecordOf({ record }), null);
  assert.equal(reviewedRecordOf(null), null);
});

test("a value's source is on screen only when one of its own citations is selected", () => {
  const groups = recordGroups(record);
  const [a, b] = groups[0].items.map((i) => i.citation);
  assert.notEqual(citationKey(a), citationKey(b));
  assert.equal(sourceShownFor([a], null, 1), false);
  assert.equal(sourceShownFor([a], b, b.page), false);
  assert.equal(sourceShownFor([a], { ...a }, a.page), true);
  // Still selected, but another page is on screen.
  assert.equal(sourceShownFor([a], { ...a }, a.page + 1), false);
  // The patient's details: any of its header values' lines will do.
  const header = patientValues(record).map((v) => v.citation);
  assert.equal(sourceShownFor(header, header.at(-1), header.at(-1).page), true);
  // Nothing printed, nothing to show.
  assert.equal(sourceShownFor([], null, 1), true);
  assert.equal(sourceShownFor([null], null, 1), true);
});

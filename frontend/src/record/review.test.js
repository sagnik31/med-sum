// Run with `npm test`. The fixture is the synthetic record recordView's
// tests use (checked against the real schema by the Python suite).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  correctableFields,
  correctionsOf,
  currentValue,
  flaggedCount,
  headerTarget,
  isPdfName,
  originalFileUrl,
  queueLabel,
  reviewNote,
} from "./review.js";

const record = JSON.parse(readFileSync(new URL("./fixtures/record.json", import.meta.url), "utf8"));

const corrected = {
  provenance: {
    status: "clinician_corrected",
    reviewed_by: "Dr. Meera Iyer",
    reviewed_at: "2025-03-05T10:00:00Z",
    low_confidence_reasons: ["LC-03: differs"],
  },
  corrections: [
    { field: "value_text", original: "10.4", corrected: "11.4", clinician: "Dr. Meera Iyer", at: "2025-03-05T10:00:00Z" },
    { field: "value_text", original: "10.4", corrected: "11.9", clinician: "Dr. Meera Iyer", at: "2025-03-06T09:00:00Z", note: "re-read" },
  ],
};

test("a result's readings and each of its ranges can be corrected", () => {
  const obs = record.observations[0];
  const fields = correctableFields("observations", obs.printed);
  const names = fields.map((f) => f.field);
  assert.deepEqual(names.slice(0, 5), ["test_name", "value_text", "unit", "flag_printed", "panel"]);
  assert.equal(names.filter((n) => n.startsWith("reference_ranges[")).length, obs.printed.reference_ranges.length);
  assert.equal(fields.find((f) => f.field === "value_text").value, obs.printed.value_text);
});

test("labels from a fixed list and citations are never offered", () => {
  for (const group of ["remarks", "conditions"]) {
    for (const item of record[group] || []) {
      const names = correctableFields(group, item.printed).map((f) => f.field);
      assert.ok(!names.includes("kind") && !names.includes("scope") && !names.includes("citation"));
    }
  }
});

test("a date nobody printed can't be corrected, a missing unit can", () => {
  const fields = correctableFields("encounters", { kind: "OPD visit", date: null, facility: null });
  assert.deepEqual(fields.map((f) => f.field), ["kind"]);
  const vital = correctableFields("vitals", { name: "BP", value_text: "120/80", unit: null });
  assert.deepEqual(vital.find((f) => f.field === "unit"), { field: "unit", label: "Unit", value: null });
});

test("header keys name the patient or the document and the field", () => {
  assert.deepEqual(headerTarget("patient.name"), { itemId: "patient", field: "name" });
  assert.deepEqual(headerTarget("document.clinicians[0]"), { itemId: "document", field: "clinicians[0]" });
});

test("corrections are listed oldest first and the latest one is shown", () => {
  const list = correctionsOf(corrected);
  assert.equal(list.length, 2);
  assert.deepEqual(list[1], {
    field: "value_text", original: "10.4", corrected: "11.9",
    clinician: "Dr. Meera Iyer", date: "2025-03-06", note: "re-read",
  });
  assert.equal(currentValue(corrected, "value_text", "10.4"), "11.9");
  assert.equal(currentValue(corrected, "unit", "g/dL"), "g/dL");
  assert.deepEqual(correctionsOf({}), []);
});

test("the review note says who and when", () => {
  assert.equal(reviewNote(corrected), "Corrected by Dr. Meera Iyer on 2025-03-06");
  assert.equal(
    reviewNote({ provenance: { status: "clinician_confirmed", reviewed_by: "Dr. A", reviewed_at: "2025-03-05T01:00:00Z" } }),
    "Confirmed by Dr. A on 2025-03-05"
  );
  assert.equal(reviewNote(record.observations[0]), null);
});

test("the flagged count is the values still awaiting review", () => {
  assert.equal(flaggedCount(record), 1);
  const reviewed = structuredClone(record);
  reviewed.observations[1].provenance.status = "clinician_confirmed";
  assert.equal(flaggedCount(reviewed), 0);
});

test("queue entries say what waits", () => {
  assert.equal(queueLabel({ reason: "needs_review", flagged: 1 }), "1 value to review");
  assert.equal(queueLabel({ reason: "needs_review", flagged: 3 }), "3 values to review");
  assert.match(queueLabel({ reason: "audit", flagged: 0 }), /audit/);
});

test("the original opens at the cited page", () => {
  assert.equal(originalFileUrl("/api", "abc", 3), "/api/documents/abc/file#page=3");
  assert.equal(originalFileUrl("/api", "abc", null), "/api/documents/abc/file");
  assert.equal(originalFileUrl("/api", "a/b", 0), "/api/documents/a%2Fb/file");
  assert.ok(isPdfName("Report.PDF") && !isPdfName("scan.png") && !isPdfName(null));
});

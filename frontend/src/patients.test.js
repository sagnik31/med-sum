import { test } from "node:test";
import assert from "node:assert/strict";
import { changedDetails } from "./patients.js";

const patient = { id: "p", mrn: "S-100", full_name: "Asha Rao", date_of_birth: "1967-04-12", age_years: null, sex: "F" };
const details = { mrn: "S-100", full_name: "Asha Rao", date_of_birth: "1967-04-12", age_years: null, sex: "F" };

test("an edit sends only the fields it changed, so another clinician's edit stays", () => {
  assert.deepEqual(changedDetails(patient, details), {});
  assert.deepEqual(changedDetails(patient, { ...details, full_name: "Asha R." }), { full_name: "Asha R." });
  assert.deepEqual(changedDetails(patient, { ...details, sex: null }), { sex: null });
  assert.deepEqual(changedDetails(patient, { ...details, date_of_birth: null, age_years: 59 }), {
    date_of_birth: null,
    age_years: 59,
  });
  // The API may leave an empty field out.
  assert.deepEqual(changedDetails({ mrn: "S-100", full_name: "Asha Rao", date_of_birth: "1967-04-12", sex: "F" }, details), {});
});

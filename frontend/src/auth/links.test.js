// Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { safeNext, tokenFromHash } from "./links.js";

test("safeNext keeps a path in this app", () => {
  assert.equal(safeNext("/patients/p1?tab=docs"), "/patients/p1?tab=docs");
  assert.equal(safeNext("/"), "/");
});

test("safeNext refuses anything that could leave the app", () => {
  for (const next of [
    null,
    "",
    "patients",
    "//evil.test/x",
    "/\\evil.test",
    "https://evil.test",
    "javascript:alert(1)",
  ]) {
    assert.equal(safeNext(next), "/", String(next));
  }
});

test("tokenFromHash reads the token from the fragment only", () => {
  assert.equal(tokenFromHash("#token=abc123"), "abc123");
  assert.equal(tokenFromHash("token=abc123"), "abc123");
  assert.equal(tokenFromHash("#other=1&token=a%2Bb"), "a+b");
  assert.equal(tokenFromHash(""), "");
  assert.equal(tokenFromHash("#"), "");
  assert.equal(tokenFromHash(undefined), "");
  assert.equal(tokenFromHash("#tok=abc"), "");
});

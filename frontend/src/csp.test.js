// The built app's content security policy (vite.config.js): checked here so
// a change to it is a decision, not an accident.
import { test } from "node:test";
import assert from "node:assert/strict";
import config, { APP_POLICY, SECURITY_HEADERS } from "../vite.config.js";

function directives(policy) {
  return Object.fromEntries(
    policy.split(";").map((d) => {
      const [name, ...values] = d.trim().split(/\s+/);
      return [name, values];
    })
  );
}

test("the built app runs only its own scripts", () => {
  const csp = directives(config.preview.headers["Content-Security-Policy"]);
  assert.deepEqual(csp["script-src"], ["'self'"]);
  assert.deepEqual(csp["object-src"], ["'none'"]);
  assert.deepEqual(csp["base-uri"], ["'none'"]);
  assert.deepEqual(csp["frame-ancestors"], ["'none'"]);
  for (const name of ["default-src", "img-src", "frame-src", "connect-src"]) {
    assert.deepEqual(csp[name], ["'self'"], name);
  }
  // srcdoc frames inherit the policy and carry their own <style>.
  assert.ok(csp["style-src"].includes("'unsafe-inline'"));
  assert.equal(config.preview.headers["Content-Security-Policy"], APP_POLICY);
});

test("the dev server keeps its headers but no script policy", () => {
  assert.equal(config.server.headers, SECURITY_HEADERS);
  assert.equal(SECURITY_HEADERS["Content-Security-Policy"], "frame-ancestors 'none'");
  assert.equal(config.preview.headers["X-Frame-Options"], "DENY");
});

test("the proxy names the browser's address to the API", () => {
  assert.equal(config.server.proxy["/api"].xfwd, true);
});

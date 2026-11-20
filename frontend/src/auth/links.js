// src/auth/links.js
//
// What the login and set-password pages read from their URL, as plain
// functions so Node's test runner covers them (links.test.js).

// next must be a path in this app: "/x", never "//host", "/\host" or
// "https://...", so the login page can't be used to send someone to another
// site.
export function safeNext(next) {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }
  return next;
}

// The token of a setup or reset link (/set-password#token=...), from the
// URL's fragment, which browsers never send to a server; "" when there is
// none.
export function tokenFromHash(hash) {
  const params = new URLSearchParams((hash || "").replace(/^#/, ""));
  return params.get("token") || "";
}

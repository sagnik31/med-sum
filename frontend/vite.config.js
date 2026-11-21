/* eslint-env node */
// Vite serves the app and proxies /api to the Go server, so the browser
// sees one origin and the HttpOnly session cookie is first-party. The
// proxy forwards the browser's Origin header, which the API checks
// against ALLOWED_ORIGINS (default http://localhost:5173).
const API_TARGET = process.env.MEDSUM_API_TARGET || "http://localhost:8080";

// The app's own pages may not be framed or sniffed, and links out send no
// referrer. (The API sets its own, stricter headers.) The dev server can
// carry no script policy, because it injects inline scripts for hot
// reload; see docs/limitations.md.
export const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer",
};

// The built app (vite preview, and the baseline for any deployment) gets a
// strict policy: scripts, frames, images and requests from its own origin
// only (the API is proxied under /api). Inline styles stay allowed: the
// document and summary pages are srcdoc frames, which inherit this policy,
// and carry their own <style> (HtmlFrame.jsx adds a stricter policy inside).
export const APP_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self'",
  "frame-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

export const PREVIEW_HEADERS = { ...SECURITY_HEADERS, "Content-Security-Policy": APP_POLICY };

// The API's one frameable route: GET /documents/{id}/file.
const FRAMEABLE = /^\/(api\/)?documents\/[0-9a-f-]+\/file(\?|#|$)/i;

export default {
  server: {
    port: 5173,
    strictPort: true,
    headers: SECURITY_HEADERS,
    proxy: {
      "/api": {
        target: API_TARGET,
        rewrite: (path) => path.replace(/^\/api/, ""),
        // Every proxied request reaches the API from this machine, so the
        // proxy appends the browser's address to X-Forwarded-For; the API
        // believes it only from TRUSTED_PROXIES (backend/.env.example sets
        // the loopback addresses), and keys its login limit and audit rows
        // on it.
        xfwd: true,
        // The dev server adds its own headers to every response, proxied
        // ones too. The API lets the app frame one route, a document's
        // original file (its CSP names who may frame it), so the dev
        // server's X-Frame-Options: DENY is dropped there and only there.
        configure: (proxy) => {
          proxy.on("proxyRes", (_proxyRes, req, res) => {
            if (FRAMEABLE.test(req.url || "")) res.removeHeader("X-Frame-Options");
          });
        },
      },
    },
  },
  preview: {
    headers: PREVIEW_HEADERS,
  },
};

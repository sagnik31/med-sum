/* eslint-env node */
// Vite serves the app and proxies /api to the Go server, so the browser
// sees one origin and the HttpOnly session cookie is first-party. The
// proxy forwards the browser's Origin header, which the API checks
// against ALLOWED_ORIGINS (default http://localhost:5173).
const API_TARGET = process.env.MEDSUM_API_TARGET || "http://localhost:8080";

// The app's own pages may not be framed or sniffed, and links out send no
// referrer. (The API sets its own, stricter headers.) A script policy is
// left to the deployment: Vite's dev server injects inline scripts.
const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer",
};

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
    headers: SECURITY_HEADERS,
  },
};

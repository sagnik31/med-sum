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

export default {
  server: {
    port: 5173,
    strictPort: true,
    headers: SECURITY_HEADERS,
    proxy: {
      "/api": {
        target: API_TARGET,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    headers: SECURITY_HEADERS,
  },
};

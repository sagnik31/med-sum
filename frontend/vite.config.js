/* eslint-env node */
// Vite serves the app and proxies /api to the Go server, so the browser
// sees one origin and the HttpOnly session cookie is first-party. The
// proxy forwards the browser's Origin header, which the API checks
// against ALLOWED_ORIGINS (default http://localhost:5173).
const API_TARGET = process.env.MEDSUM_API_TARGET || "http://localhost:8080";

export default {
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
};

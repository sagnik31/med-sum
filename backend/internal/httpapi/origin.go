package httpapi

import (
	"net/http"
	"strings"
)

// csrfHeader must be on every request that changes something. A form on
// another site can't set a header, and a script on another site can only
// set it after a CORS preflight, which only the allowed origins pass. With
// SameSite=Strict cookies this makes a CSRF token unnecessary.
const (
	csrfHeader = "X-Requested-With"
	csrfValue  = "med-sum"
)

func safeMethod(m string) bool {
	return m == http.MethodGet || m == http.MethodHead || m == http.MethodOptions
}

// withOrigins applies CORS for the allowed origins and the CSRF checks.
func (s *Server) withOrigins(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		allowed := origin != "" && s.allowedOrigins[origin]
		if origin != "" {
			w.Header().Add("Vary", "Origin")
		}
		if allowed {
			h := w.Header()
			h.Set("Access-Control-Allow-Origin", origin)
			h.Set("Access-Control-Allow-Credentials", "true")
			h.Set("Access-Control-Allow-Headers", "Content-Type, "+csrfHeader)
			h.Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
			h.Set("Access-Control-Max-Age", "600")
		}
		if r.Method == http.MethodOptions {
			// A preflight from an origin not on the list gets no CORS
			// headers, so the browser refuses the real request.
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if !safeMethod(r.Method) {
			if r.Header.Get(csrfHeader) != csrfValue {
				http.Error(w, "missing "+csrfHeader+" header", http.StatusForbidden)
				return
			}
			// Browsers always send Origin on cross-origin requests, so a
			// request without one comes from this site or from a client
			// that isn't a browser (the dataset loader, curl).
			if origin != "" && !allowed {
				http.Error(w, "origin not allowed", http.StatusForbidden)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// ParseOrigins splits a comma-separated ALLOWED_ORIGINS value.
func ParseOrigins(v string) []string {
	var out []string
	for _, o := range strings.Split(v, ",") {
		if o = strings.TrimRight(strings.TrimSpace(o), "/"); o != "" {
			out = append(out, o)
		}
	}
	return out
}

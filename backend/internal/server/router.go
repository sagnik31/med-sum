package server

import (
	"net/http"

	"backend/internal/config"

	"github.com/jackc/pgx/v5/pgxpool"
)

// NewRouter builds the main HTTP router.
// For now we just add a simple health check endpoint.
func NewRouter(pool *pgxpool.Pool, cfg *config.Config) http.Handler {
	mux := http.NewServeMux()

	// Health check endpoint
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})

	// TODO: later
	// - /auth/request-otp
	// - /auth/verify-otp
	// - /documents
	// - /documents/{id}
	// - /documents/{id}/insights

	return mux
}
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	appdb "backend/internal/db"
	"backend/internal/httpapi"
)

func main() {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		log.Fatal("DATABASE_URL is required")
	}

	jwtSecret := os.Getenv("JWT_SECRET")
	if jwtSecret == "" {
		log.Fatal("JWT_SECRET is required")
	}

	ctx := context.Background()

	store, err := appdb.New(ctx, dsn)
	if err != nil {
		log.Fatalf("failed to connect to database: %v", err)
	}
	defer store.Close()

	// Stored as an absolute path so the AI service finds uploads no matter
	// which directory either process was started from.
	uploadsDir, err := filepath.Abs(getEnv("UPLOADS_DIR", "uploads"))
	if err != nil {
		log.Fatalf("invalid UPLOADS_DIR: %v", err)
	}
	if err := os.MkdirAll(uploadsDir, 0o755); err != nil {
		log.Fatalf("failed to create uploads directory: %v", err)
	}

	api := httpapi.NewServer(store, jwtSecret, uploadsDir)

	addr := getEnv("HTTP_ADDRESS", ":8080")

	// Wrap the API handler with CORS middleware
	handler := withCORS(api)

	srv := &http.Server{
		Addr:         addr,
		Handler:      handler,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	log.Printf("server listening on %s", addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("server error: %v", err)
	}
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// CORS middleware: wrap your main handler with this.
func withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Allow your frontend origin during local dev
		w.Header().Set("Access-Control-Allow-Origin", "http://localhost:5173")
		w.Header().Set("Access-Control-Allow-Credentials", "true")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")

		// Handle preflight (OPTIONS) request
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		// For normal requests, go to the next handler
		next.ServeHTTP(w, r)
	})
}

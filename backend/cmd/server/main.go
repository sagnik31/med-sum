package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"backend/internal/auth"
	appdb "backend/internal/db"
	"backend/internal/httpapi"
)

func main() {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		log.Fatal("DATABASE_URL is required")
	}

	// Shared with the AI service, which rejects /internal/* calls without it.
	internalToken := os.Getenv("INTERNAL_API_TOKEN")
	if internalToken == "" {
		log.Fatal("INTERNAL_API_TOKEN is required")
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

	insightsURL := getEnv("INSIGHTS_SERVICE_URL", "http://localhost:9000")

	// Browser origins allowed to call the API with the session cookie. The
	// Vite dev server proxies /api to this server and forwards its origin.
	origins := httpapi.ParseOrigins(getEnv("ALLOWED_ORIGINS", "http://localhost:5173"))
	for _, o := range origins {
		if o == "*" {
			log.Fatal("ALLOWED_ORIGINS must list origins; * can't be used with cookies")
		}
	}

	// Secure cookies are only sent over HTTPS. Plain-HTTP local development
	// (and the dataset loader) needs COOKIE_SECURE=false.
	cookieSecure := getEnv("COOKIE_SECURE", "true") != "false"
	if !cookieSecure {
		log.Print("COOKIE_SECURE=false: session cookies are sent over plain HTTP (local development only)")
	}

	hasher, err := auth.NewHasher(auth.DefaultParams)
	if err != nil {
		log.Fatalf("password hasher: %v", err)
	}

	api := httpapi.NewServer(store, httpapi.Config{
		UploadsDir:     uploadsDir,
		InsightsURL:    insightsURL,
		InternalToken:  internalToken,
		AllowedOrigins: origins,
		CookieSecure:   cookieSecure,
		AppURL:         getEnv("MEDSUM_APP_URL", "http://localhost:5173"),
		Hasher:         hasher,
	})

	api.PurgeExpiredSessions(ctx)
	go func() {
		for range time.Tick(time.Hour) {
			api.PurgeExpiredSessions(ctx)
		}
	}()

	addr := getEnv("HTTP_ADDRESS", ":8080")

	// Read/WriteTimeout cover the whole request body, so they must allow a
	// 20 MB upload on a slow connection; slow clients are still cut off by
	// ReadHeaderTimeout.
	srv := &http.Server{
		Addr:              addr,
		Handler:           api,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       2 * time.Minute,
		WriteTimeout:      2 * time.Minute,
		IdleTimeout:       60 * time.Second,
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

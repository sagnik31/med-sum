package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"backend/internal/auth"
	appdb "backend/internal/db"
	"backend/internal/httpapi"
)

func main() {
	cfg, err := loadConfig(os.Getenv)
	if err != nil {
		log.Fatalf("configuration: %v", err)
	}

	ctx := context.Background()

	store, err := appdb.New(ctx, cfg.databaseURL)
	if err != nil {
		log.Fatalf("failed to connect to database: %v", err)
	}
	defer store.Close()

	if err := os.MkdirAll(cfg.uploadsDir, 0o755); err != nil {
		log.Fatalf("failed to create uploads directory: %v", err)
	}

	// Secure cookies are only sent over HTTPS. Plain-HTTP local development
	// (and the dataset loader) needs COOKIE_SECURE=false.
	if !cfg.cookieSecure {
		log.Print("COOKIE_SECURE=false: session cookies are sent over plain HTTP (local development only)")
	}

	hasher, err := auth.NewHasher(auth.DefaultParams)
	if err != nil {
		log.Fatalf("password hasher: %v", err)
	}

	api := httpapi.NewServer(store, httpapi.Config{
		UploadsDir:     cfg.uploadsDir,
		InsightsURL:    cfg.insightsURL,
		InternalToken:  cfg.internalToken,
		AllowedOrigins: cfg.allowedOrigins,
		CookieSecure:   cfg.cookieSecure,
		AppURL:         cfg.appURL,
		Hasher:         hasher,

		ReviewApplyTimeout: cfg.reviewApplyTimeout,
		Environment:        cfg.environment,
	})

	api.PurgeExpiredSessions(ctx)
	go func() {
		for range time.Tick(time.Hour) {
			api.PurgeExpiredSessions(ctx)
		}
	}()

	addr := cfg.httpAddress

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

package main

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
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
		TrustedProxies:     cfg.trustedProxies,
	})

	api.PurgeExpiredSessions(ctx)
	go func() {
		for range time.Tick(time.Hour) {
			api.PurgeExpiredSessions(ctx)
		}
	}()

	addr := cfg.httpAddress

	// On shutdown, requests in flight get the write timeout to finish.
	// Read/WriteTimeout cover the whole request body, so they must allow a
	// 20 MB upload on a slow connection; slow clients are still cut off by
	// ReadHeaderTimeout.
	srv := &http.Server{
		Addr:              addr,
		Handler:           api,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       2 * time.Minute,
		WriteTimeout:      writeTimeout(cfg.reviewApplyTimeout),
		IdleTimeout:       60 * time.Second,
	}

	// SIGINT or SIGTERM stops taking new requests and lets those in flight
	// finish, so an upload isn't cut off between writing its file and
	// inserting its row (which would leave a file no row names; medsumctl
	// orphan-uploads lists any).
	sigCtx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()
	l, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen on %s: %v", addr, err)
	}
	log.Printf("server listening on %s", addr)
	if err := serve(sigCtx, srv, l, srv.WriteTimeout); err != nil {
		log.Fatalf("server error: %v", err)
	}
	log.Print("server stopped")
}

// serve runs srv on l until ctx ends, then shuts it down gracefully: no
// new connections, and requests in flight get up to grace to finish.
func serve(ctx context.Context, srv *http.Server, l net.Listener, grace time.Duration) error {
	done := make(chan error, 1)
	go func() { done <- srv.Serve(l) }()
	select {
	case err := <-done:
		return err // Serve failed on its own
	case <-ctx.Done():
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), grace)
	defer cancel()
	err := srv.Shutdown(shutdownCtx)
	if serveErr := <-done; serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) && err == nil {
		err = serveErr
	}
	return err
}

// writeTimeout is how long a request may take to be answered: 2 minutes, or
// the review call's timeout and time to store the review after it, so a saved
// review is never reported as failed.
func writeTimeout(reviewApply time.Duration) time.Duration {
	return max(2*time.Minute, reviewApply+30*time.Second)
}

package main

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"backend/internal/httpapi"
)

// minTokenLength is the shortest INTERNAL_API_TOKEN accepted: 32
// characters, as from `openssl rand -hex 16` or longer.
const minTokenLength = 32

// config is what the server reads from its environment.
type config struct {
	databaseURL    string
	internalToken  string
	uploadsDir     string
	insightsURL    string
	allowedOrigins []string
	cookieSecure   bool
	appURL         string
	httpAddress    string
	// How long a review waits for the AI service (REVIEW_APPLY_TIMEOUT).
	reviewApplyTimeout time.Duration
}

// loadConfig reads the configuration and fails when a secret is missing
// or too weak, so the server never starts with a guessable token or no
// database. getenv is os.Getenv outside tests.
func loadConfig(getenv func(string) string) (config, error) {
	get := func(key, def string) string {
		if v := strings.TrimSpace(getenv(key)); v != "" {
			return v
		}
		return def
	}
	c := config{
		databaseURL:   get("DATABASE_URL", ""),
		internalToken: get("INTERNAL_API_TOKEN", ""),
		insightsURL:   get("INSIGHTS_SERVICE_URL", "http://localhost:9000"),
		cookieSecure:  get("COOKIE_SECURE", "true") != "false",
		appURL:        get("MEDSUM_APP_URL", "http://localhost:5173"),
		httpAddress:   get("HTTP_ADDRESS", ":8080"),
	}
	var errs []error
	if c.databaseURL == "" {
		errs = append(errs, errors.New("DATABASE_URL is required"))
	}
	// Shared with the AI service, which rejects /internal/* calls without it.
	switch {
	case c.internalToken == "":
		errs = append(errs, errors.New("INTERNAL_API_TOKEN is required"))
	case len(c.internalToken) < minTokenLength:
		errs = append(errs, fmt.Errorf("INTERNAL_API_TOKEN must be at least %d characters", minTokenLength))
	}

	// Stored as an absolute path so the AI service finds uploads no matter
	// which directory either process was started from.
	uploads, err := filepath.Abs(get("UPLOADS_DIR", "uploads"))
	if err != nil {
		errs = append(errs, fmt.Errorf("invalid UPLOADS_DIR: %w", err))
	}
	c.uploadsDir = uploads

	// Browser origins allowed to call the API with the session cookie. The
	// Vite dev server proxies /api to this server and forwards its origin.
	c.allowedOrigins = httpapi.ParseOrigins(get("ALLOWED_ORIGINS", "http://localhost:5173"))
	for _, o := range c.allowedOrigins {
		if o == "*" {
			errs = append(errs, errors.New("ALLOWED_ORIGINS must list origins; * can't be used with cookies"))
		}
	}
	c.reviewApplyTimeout = httpapi.DefaultReviewApplyTimeout
	if v := get("REVIEW_APPLY_TIMEOUT", ""); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil || d <= 0 || d > 10*time.Minute {
			errs = append(errs, errors.New("REVIEW_APPLY_TIMEOUT must be a duration such as 90s, at most 10m"))
		} else {
			c.reviewApplyTimeout = d
		}
	}
	return c, errors.Join(errs...)
}

package main

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"backend/internal/httpapi"
)

const goodToken = "0123456789abcdef0123456789abcdef"

func envOf(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestStartUpNeedsTheDatabaseAndAStrongToken(t *testing.T) {
	for name, c := range map[string]struct {
		env  map[string]string
		want string
	}{
		"nothing set":      {map[string]string{}, "DATABASE_URL is required"},
		"no token":         {map[string]string{"DATABASE_URL": "postgres://x"}, "INTERNAL_API_TOKEN is required"},
		"blank token":      {map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": "   "}, "INTERNAL_API_TOKEN is required"},
		"short token":      {map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": "dev-internal-token"}, "at least 32"},
		"no database":      {map[string]string{"INTERNAL_API_TOKEN": goodToken}, "DATABASE_URL is required"},
		"wildcard origins": {map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": goodToken, "ALLOWED_ORIGINS": "*"}, "ALLOWED_ORIGINS"},
	} {
		_, err := loadConfig(envOf(c.env))
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: err = %v, want it to mention %q", name, err, c.want)
		}
		if err != nil && strings.Contains(err.Error(), goodToken) {
			t.Errorf("%s: the error repeats the token", name)
		}
	}
}

func TestConfigDefaults(t *testing.T) {
	c, err := loadConfig(envOf(map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": goodToken}))
	if err != nil {
		t.Fatal(err)
	}
	if !c.cookieSecure || c.httpAddress != ":8080" || c.insightsURL != "http://localhost:9000" ||
		len(c.allowedOrigins) != 1 || !filepath.IsAbs(c.uploadsDir) {
		t.Errorf("config = %+v", c)
	}
	c, _ = loadConfig(envOf(map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": goodToken,
		"COOKIE_SECURE": "false"}))
	if c.cookieSecure {
		t.Error("COOKIE_SECURE=false is ignored")
	}
}

func TestTheReviewApplyTimeout(t *testing.T) {
	base := map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": goodToken}
	with := func(v string) (config, error) {
		env := map[string]string{"REVIEW_APPLY_TIMEOUT": v}
		for k, x := range base {
			env[k] = x
		}
		return loadConfig(envOf(env))
	}
	if c, err := with(""); err != nil || c.reviewApplyTimeout != httpapi.DefaultReviewApplyTimeout {
		t.Errorf("default = %v, %v", c.reviewApplyTimeout, err)
	}
	if c, err := with("90s"); err != nil || c.reviewApplyTimeout != 90*time.Second {
		t.Errorf("90s = %v, %v", c.reviewApplyTimeout, err)
	}
	for _, bad := range []string{"90", "-1s", "0s", "11m"} {
		if _, err := with(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}

func TestTheTrustedProxies(t *testing.T) {
	env := map[string]string{"DATABASE_URL": "postgres://x", "INTERNAL_API_TOKEN": goodToken}
	if c, err := loadConfig(envOf(env)); err != nil || len(c.trustedProxies) != 0 {
		t.Errorf("default = %v, %v; want none", c.trustedProxies, err)
	}
	env["TRUSTED_PROXIES"] = "127.0.0.1, ::1"
	c, err := loadConfig(envOf(env))
	if err != nil || len(c.trustedProxies) != 2 || c.trustedProxies[1].String() != "::1" {
		t.Errorf("two = %v, %v", c.trustedProxies, err)
	}
	for _, bad := range []string{"localhost", "10.0.0.0/8"} {
		env["TRUSTED_PROXIES"] = bad
		if _, err := loadConfig(envOf(env)); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}

package main

import (
	"path/filepath"
	"strings"
	"testing"
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

package config

import (
	"fmt"
	"os"
)

type Config struct {
	HTTPAddress string // e.g. ":8080"
	DatabaseURL string // Postgres connection string

	JWTSecret string // will be used later for auth
}

// Load reads configuration from environment variables.
func Load() (*Config, error) {
	cfg := &Config{
		HTTPAddress: getEnvOrDefault("HTTP_ADDRESS", ":8080"),
		DatabaseURL: os.Getenv("DATABASE_URL"),
		JWTSecret:   os.Getenv("JWT_SECRET"),
	}

	if cfg.DatabaseURL == "" {
		return nil, fmt.Errorf("DATABASE_URL is required")
	}

	if cfg.JWTSecret == "" {
		// For now we just log a warning; later we may enforce it
		fmt.Println("WARNING: JWT_SECRET is not set; auth will not be secure in production")
	}

	return cfg, nil
}

func getEnvOrDefault(key, def string) string {
	val := os.Getenv(key)
	if val == "" {
		return def
	}
	return val
}
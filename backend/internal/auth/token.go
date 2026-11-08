package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"time"
)

// TokenBytes is the size of session and setup tokens: 256 bits.
const TokenBytes = 32

// NewToken returns a random token for a client (URL-safe base64, no
// padding) and its SHA-256, which is all the database stores.
func NewToken() (token string, hash []byte, err error) {
	b := make([]byte, TokenBytes)
	if _, err := rand.Read(b); err != nil {
		return "", nil, err
	}
	token = base64.RawURLEncoding.EncodeToString(b)
	sum := sha256.Sum256(b)
	return token, sum[:], nil
}

// ErrBadToken means a token from a client isn't one NewToken could make.
var ErrBadToken = errors.New("malformed token")

// HashToken returns the SHA-256 of a token from a client, for lookup.
func HashToken(token string) ([]byte, error) {
	b, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil || len(b) != TokenBytes {
		return nil, ErrBadToken
	}
	sum := sha256.Sum256(b)
	return sum[:], nil
}

// How long setup and reset links stay usable.
const (
	SetupLinkTTL = 72 * time.Hour // a new account's first password
	ResetLinkTTL = 24 * time.Hour // a new password issued by an admin
)

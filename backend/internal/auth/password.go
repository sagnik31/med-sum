// Package auth holds the security primitives of login: password hashing,
// the password rules and random tokens. It knows nothing about HTTP or the
// database.
package auth

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"

	"golang.org/x/crypto/argon2"
)

// Params are argon2id parameters. Memory is in KiB.
type Params struct {
	Memory  uint32
	Time    uint32
	Threads uint8
	SaltLen uint32
	KeyLen  uint32
}

// DefaultParams are OWASP's minimum argon2id settings (19 MiB, 2 passes,
// 1 thread): about 30-60 ms per hash on a laptop CPU.
var DefaultParams = Params{Memory: 19 * 1024, Time: 2, Threads: 1, SaltLen: 16, KeyLen: 32}

// Hasher hashes and verifies passwords with fixed parameters.
type Hasher struct {
	params Params
	dummy  string // a hash to verify against when an account doesn't exist
}

// NewHasher returns a Hasher with the given parameters.
func NewHasher(p Params) (*Hasher, error) {
	h := &Hasher{params: p}
	dummy, err := h.Hash("not a password anyone has: " + randomString())
	if err != nil {
		return nil, err
	}
	h.dummy = dummy
	return h, nil
}

func randomString() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return base64.RawStdEncoding.EncodeToString(b)
}

var b64 = base64.RawStdEncoding

// Hash returns the password's argon2id hash in PHC string format:
// $argon2id$v=19$m=19456,t=2,p=1$<salt>$<key>
func (h *Hasher) Hash(password string) (string, error) {
	salt := make([]byte, h.params.SaltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	p := h.params
	key := argon2.IDKey([]byte(password), salt, p.Time, p.Memory, p.Threads, p.KeyLen)
	return fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
		argon2.Version, p.Memory, p.Time, p.Threads, b64.EncodeToString(salt), b64.EncodeToString(key)), nil
}

// ErrMalformedHash means a stored hash can't be parsed.
var ErrMalformedHash = errors.New("malformed password hash")

func decode(encoded string) (Params, []byte, []byte, error) {
	parts := strings.Split(encoded, "$")
	// "", "argon2id", "v=19", "m=..,t=..,p=..", salt, key
	if len(parts) != 6 || parts[0] != "" || parts[1] != "argon2id" {
		return Params{}, nil, nil, ErrMalformedHash
	}
	var version int
	if _, err := fmt.Sscanf(parts[2], "v=%d", &version); err != nil || version != argon2.Version {
		return Params{}, nil, nil, ErrMalformedHash
	}
	var p Params
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &p.Memory, &p.Time, &p.Threads); err != nil {
		return Params{}, nil, nil, ErrMalformedHash
	}
	salt, err := b64.DecodeString(parts[4])
	if err != nil || len(salt) == 0 {
		return Params{}, nil, nil, ErrMalformedHash
	}
	key, err := b64.DecodeString(parts[5])
	if err != nil || len(key) == 0 {
		return Params{}, nil, nil, ErrMalformedHash
	}
	if p.Memory == 0 || p.Time == 0 || p.Threads == 0 {
		return Params{}, nil, nil, ErrMalformedHash
	}
	p.SaltLen, p.KeyLen = uint32(len(salt)), uint32(len(key))
	return p, salt, key, nil
}

// Verify reports whether password matches the encoded hash, using the
// parameters stored in the hash (so old hashes keep working when the
// defaults are raised).
func (h *Hasher) Verify(password, encoded string) (bool, error) {
	p, salt, key, err := decode(encoded)
	if err != nil {
		return false, err
	}
	got := argon2.IDKey([]byte(password), salt, p.Time, p.Memory, p.Threads, p.KeyLen)
	return subtle.ConstantTimeCompare(got, key) == 1, nil
}

// VerifyDummy does the work of a verification against a hash nobody has,
// so a login for an unknown email takes as long as one for a known email.
func (h *Hasher) VerifyDummy(password string) {
	_, _ = h.Verify(password, h.dummy)
}

// NeedsRehash reports whether the encoded hash was made with parameters
// other than the Hasher's, so it should be replaced at the next login.
func (h *Hasher) NeedsRehash(encoded string) bool {
	p, _, _, err := decode(encoded)
	if err != nil {
		return true
	}
	return p != h.params
}

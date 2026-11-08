package auth

import (
	"errors"
	"strings"
	"testing"
)

// cheap keeps the tests fast; the format and logic are the same.
var cheap = Params{Memory: 64, Time: 1, Threads: 1, SaltLen: 16, KeyLen: 32}

func hasher(t *testing.T, p Params) *Hasher {
	t.Helper()
	h, err := NewHasher(p)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestHashThenVerify(t *testing.T) {
	h := hasher(t, cheap)
	encoded, err := h.Hash("correct horse battery")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(encoded, "$argon2id$v=19$m=64,t=1,p=1$") {
		t.Errorf("hash %q is not in PHC format with the given parameters", encoded)
	}
	ok, err := h.Verify("correct horse battery", encoded)
	if err != nil || !ok {
		t.Errorf("the right password was refused: ok=%v err=%v", ok, err)
	}
	ok, err = h.Verify("correct horse batterY", encoded)
	if err != nil || ok {
		t.Errorf("a wrong password was accepted: ok=%v err=%v", ok, err)
	}
}

func TestHashesAreSalted(t *testing.T) {
	h := hasher(t, cheap)
	a, _ := h.Hash("same password here")
	b, _ := h.Hash("same password here")
	if a == b {
		t.Error("two hashes of one password are equal; the salt is missing")
	}
}

func TestVerifyUsesTheParametersInTheHash(t *testing.T) {
	old := hasher(t, cheap)
	encoded, _ := old.Hash("an older password hash")
	stronger := hasher(t, Params{Memory: 128, Time: 2, Threads: 1, SaltLen: 16, KeyLen: 32})

	ok, err := stronger.Verify("an older password hash", encoded)
	if err != nil || !ok {
		t.Fatalf("a hash made with older parameters no longer verifies: ok=%v err=%v", ok, err)
	}
	if !stronger.NeedsRehash(encoded) {
		t.Error("a hash with older parameters should be rehashed")
	}
	if old.NeedsRehash(encoded) {
		t.Error("a hash with the current parameters should not be rehashed")
	}
}

func TestDefaultParamsAreOWASPMinimum(t *testing.T) {
	if DefaultParams.Memory < 19*1024 || DefaultParams.Time < 2 || DefaultParams.KeyLen < 32 {
		t.Errorf("DefaultParams = %+v, below OWASP's argon2id minimum", DefaultParams)
	}
}

func TestVerifyRejectsMalformedHashes(t *testing.T) {
	h := hasher(t, cheap)
	good, _ := h.Hash("a fine password")
	parts := strings.Split(good, "$")
	bad := map[string]string{
		"empty":           "",
		"bcrypt":          "$2a$12$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyzabcd",
		"argon2i":         strings.Replace(good, "argon2id", "argon2i", 1),
		"other version":   strings.Replace(good, "v=19", "v=16", 1),
		"no parameters":   strings.Join([]string{"", parts[1], parts[2], "", parts[4], parts[5]}, "$"),
		"zero memory":     strings.Replace(good, "m=64", "m=0", 1),
		"salt not base64": strings.Join([]string{"", parts[1], parts[2], parts[3], "!!!", parts[5]}, "$"),
		"no key":          strings.Join([]string{"", parts[1], parts[2], parts[3], parts[4], ""}, "$"),
		"extra field":     good + "$x",
	}
	for name, encoded := range bad {
		t.Run(name, func(t *testing.T) {
			ok, err := h.Verify("a fine password", encoded)
			if ok || !errors.Is(err, ErrMalformedHash) {
				t.Errorf("Verify = %v, %v; want false, ErrMalformedHash", ok, err)
			}
			if !h.NeedsRehash(encoded) {
				t.Error("a malformed hash should need a rehash")
			}
		})
	}
}

func TestVerifyDummyDoesNotPanic(t *testing.T) {
	hasher(t, cheap).VerifyDummy("anything")
}

func TestTokenRoundTrip(t *testing.T) {
	token, hash, err := NewToken()
	if err != nil {
		t.Fatal(err)
	}
	if len(hash) != 32 {
		t.Fatalf("hash is %d bytes, want 32", len(hash))
	}
	again, err := HashToken(token)
	if err != nil {
		t.Fatal(err)
	}
	if string(again) != string(hash) {
		t.Error("HashToken does not give the hash NewToken returned")
	}
	other, _, _ := NewToken()
	if other == token {
		t.Error("two tokens are equal")
	}
	if strings.ContainsAny(token, "+/=") {
		t.Errorf("token %q is not URL-safe", token)
	}
}

func TestHashTokenRejectsMalformedTokens(t *testing.T) {
	for _, token := range []string{"", "short", "not base64 at all!!", strings.Repeat("A", 44)} {
		if _, err := HashToken(token); !errors.Is(err, ErrBadToken) {
			t.Errorf("HashToken(%q) error = %v, want ErrBadToken", token, err)
		}
	}
}

func TestCheckPassword(t *testing.T) {
	tests := []struct {
		password string
		want     error
	}{
		{"short pass1", ErrPasswordTooShort},
		{"twelve chars", nil},
		{"ünïcödé pässwörd", nil},
		{"ಕನ್ನಡ ಪಾಸ್ವರ್ಡ್", nil}, // counted in characters, not bytes
		{strings.Repeat("x", 120) + "abcdefghi", ErrPasswordTooLong},
		{"Password1234", ErrPasswordCommon},
		{"QWERTY123456", ErrPasswordCommon},
		{"aaaaaaaaaaaaaa", ErrPasswordRepeating},
		{"abcdabcdabcd", ErrPasswordRepeating},
		{"my anjali.rao secret", ErrPasswordHasEmail},
		{"MY ANJALI.RAO SECRET", ErrPasswordHasEmail},
		{"a long passphrase for the ward", nil},
	}
	for _, tt := range tests {
		t.Run(tt.password, func(t *testing.T) {
			if got := CheckPassword(tt.password, "anjali.rao@hospital.example"); !errors.Is(got, tt.want) {
				t.Errorf("CheckPassword = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestCheckPasswordIgnoresShortEmailNames(t *testing.T) {
	// "dr" is in many passwords; only names of 4+ characters are checked.
	if err := CheckPassword("a drawn out sentence", "dr@hospital.example"); err != nil {
		t.Errorf("CheckPassword = %v, want nil", err)
	}
}

func TestNormalizeEmail(t *testing.T) {
	good := map[string]string{
		"anjali@hospital.example":        "anjali@hospital.example",
		"  Anjali.Rao@Hospital.Example ": "anjali.rao@hospital.example",
	}
	for in, want := range good {
		if got, err := NormalizeEmail(in); err != nil || got != want {
			t.Errorf("NormalizeEmail(%q) = %q, %v; want %q", in, got, err, want)
		}
	}
	for _, in := range []string{"", "anjali", "@hospital.example", "Anjali <anjali@hospital.example>",
		"a@b@c", strings.Repeat("a", 250) + "@x.in"} {
		if _, err := NormalizeEmail(in); !errors.Is(err, ErrBadEmail) {
			t.Errorf("NormalizeEmail(%q) error = %v, want ErrBadEmail", in, err)
		}
	}
}

func TestSetPasswordLinkPutsTheTokenInTheFragment(t *testing.T) {
	for _, app := range []string{"https://app.test", "https://app.test/"} {
		if got := SetPasswordLink(app, "abc"); got != "https://app.test/set-password#token=abc" {
			t.Errorf("SetPasswordLink(%q) = %q", app, got)
		}
	}
}

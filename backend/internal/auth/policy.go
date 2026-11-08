package auth

import (
	_ "embed"
	"errors"
	"net/mail"
	"strings"
	"unicode/utf8"
)

// Password length limits (NIST SP 800-63B style: long, no composition
// rules). The maximum keeps hashing cost bounded.
const (
	MinPasswordLen = 12
	MaxPasswordLen = 128
)

// Errors from CheckPassword. Their messages are shown to the user.
var (
	ErrPasswordTooShort  = errors.New("the password must be at least 12 characters")
	ErrPasswordTooLong   = errors.New("the password must be at most 128 characters")
	ErrPasswordCommon    = errors.New("this password is too common; choose another")
	ErrPasswordHasEmail  = errors.New("the password must not contain your email name")
	ErrPasswordRepeating = errors.New("the password must not be one short pattern repeated")
)

//go:embed common_passwords.txt
var commonPasswordsFile string

var commonPasswords = func() map[string]bool {
	m := map[string]bool{}
	for _, line := range strings.Split(commonPasswordsFile, "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "#") {
			m[strings.ToLower(line)] = true
		}
	}
	return m
}()

// CheckPassword applies the password rules for the account with the given
// email. It returns nil when the password is acceptable.
func CheckPassword(password, email string) error {
	n := utf8.RuneCountInString(password)
	if n < MinPasswordLen {
		return ErrPasswordTooShort
	}
	if n > MaxPasswordLen {
		return ErrPasswordTooLong
	}
	lower := strings.ToLower(password)
	if commonPasswords[lower] {
		return ErrPasswordCommon
	}
	if isRepeated(lower) {
		return ErrPasswordRepeating
	}
	if local, _, ok := strings.Cut(strings.ToLower(email), "@"); ok && utf8.RuneCountInString(local) >= 4 &&
		strings.Contains(lower, local) {
		return ErrPasswordHasEmail
	}
	return nil
}

// isRepeated reports whether s is a unit of at most 4 characters repeated,
// such as "aaaaaaaaaaaa" or "abcabcabcabc".
func isRepeated(s string) bool {
	r := []rune(s)
	for unit := 1; unit <= 4 && unit < len(r); unit++ {
		if len(r)%unit != 0 {
			continue
		}
		same := true
		for i := unit; i < len(r); i++ {
			if r[i] != r[i-unit] {
				same = false
				break
			}
		}
		if same {
			return true
		}
	}
	return false
}

// ErrBadEmail means an email address can't be used for an account.
var ErrBadEmail = errors.New("enter a valid email address")

// NormalizeEmail trims and lower-cases an email address and checks that it
// is a plain address (no display name). Accounts are keyed on the result.
func NormalizeEmail(email string) (string, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	if email == "" || len(email) > 254 {
		return "", ErrBadEmail
	}
	addr, err := mail.ParseAddress(email)
	if err != nil || addr.Address != email || addr.Name != "" {
		return "", ErrBadEmail
	}
	return email, nil
}

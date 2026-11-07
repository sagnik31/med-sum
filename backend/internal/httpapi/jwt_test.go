package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

func TestTokenCarriesTheUserID(t *testing.T) {
	s := &Server{jwtSecret: []byte(testJWTSecret)}

	token, err := s.generateToken("user-1")
	if err != nil {
		t.Fatal(err)
	}
	claims, err := s.parseToken(token)
	if err != nil {
		t.Fatal(err)
	}
	if claims.UserID != "user-1" {
		t.Errorf("UserID = %q, want user-1", claims.UserID)
	}
	if ttl := time.Until(claims.ExpiresAt.Time); ttl < 23*time.Hour || ttl > 24*time.Hour {
		t.Errorf("token expires in %v, want about 24h", ttl)
	}
}

func signed(t *testing.T, method jwt.SigningMethod, key any, claims Claims) string {
	t.Helper()
	token, err := jwt.NewWithClaims(method, claims).SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return token
}

func TestParseTokenRejectsForgedAndExpiredTokens(t *testing.T) {
	s := &Server{jwtSecret: []byte(testJWTSecret)}
	valid := jwt.RegisteredClaims{ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour))}

	tests := map[string]string{
		"other secret": signed(t, jwt.SigningMethodHS256, []byte("other-secret"),
			Claims{UserID: "user-1", RegisteredClaims: valid}),
		"expired": signed(t, jwt.SigningMethodHS256, []byte(testJWTSecret),
			Claims{UserID: "user-1", RegisteredClaims: jwt.RegisteredClaims{
				ExpiresAt: jwt.NewNumericDate(time.Now().Add(-time.Minute)),
			}}),
		"unsigned (alg none)": signed(t, jwt.SigningMethodNone, jwt.UnsafeAllowNoneSignatureType,
			Claims{UserID: "user-1", RegisteredClaims: valid}),
		"not a token": "not-a-token",
	}
	for name, token := range tests {
		t.Run(name, func(t *testing.T) {
			if _, err := s.parseToken(token); err == nil {
				t.Error("token accepted, want an error")
			}
		})
	}
}

func TestWithAuth(t *testing.T) {
	s := &Server{jwtSecret: []byte(testJWTSecret)}
	good, err := s.generateToken("user-1")
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name     string
		header   string
		wantCode int
	}{
		{"no header", "", http.StatusUnauthorized},
		{"no scheme", good, http.StatusUnauthorized},
		{"basic scheme", "Basic " + good, http.StatusUnauthorized},
		{"bad token", "Bearer nope", http.StatusUnauthorized},
		{"valid", "Bearer " + good, http.StatusOK},
		{"scheme in lower case", "bearer " + good, http.StatusOK},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var gotUser string
			h := s.withAuth(func(w http.ResponseWriter, r *http.Request) {
				gotUser, _ = userIDFromContext(r.Context())
			})
			req := httptest.NewRequest(http.MethodGet, "/documents", nil)
			if tt.header != "" {
				req.Header.Set("Authorization", tt.header)
			}
			rec := httptest.NewRecorder()

			h(rec, req)

			if rec.Code != tt.wantCode {
				t.Fatalf("status = %d, want %d", rec.Code, tt.wantCode)
			}
			if tt.wantCode == http.StatusOK && gotUser != "user-1" {
				t.Errorf("user in context = %q, want user-1", gotUser)
			}
			if tt.wantCode != http.StatusOK && gotUser != "" {
				t.Error("handler ran for an unauthorised request")
			}
		})
	}
}

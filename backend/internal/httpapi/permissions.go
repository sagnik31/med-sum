package httpapi

import (
	"net/http"

	"backend/internal/authz"

	"github.com/jackc/pgx/v5/pgtype"
)

// permit reports whether the logged-in account has the permission, and
// answers 403 when it doesn't. It runs inside withSession.
func permit(w http.ResponseWriter, r *http.Request, p authz.Permission) bool {
	principal, ok := principalFromContext(r.Context())
	if ok && authz.Can(principal.Roles, p) {
		return true
	}
	http.Error(w, "your account is not allowed to do this", http.StatusForbidden)
	return false
}

// byMethod routes a request to a handler by HTTP method, after checking
// that method's permission. Methods not listed answer 405.
type methodRoute struct {
	perm    authz.Permission
	handler http.HandlerFunc
}

func byMethod(routes map[string]methodRoute) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		route, ok := routes[r.Method]
		if !ok {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if !permit(w, r, route.perm) {
			return
		}
		route.handler(w, r)
	}
}

// validUUID reports whether an id from a path is a UUID, so a malformed
// one can be answered before the database fails to cast it.
func validUUID(id string) bool {
	var u pgtype.UUID
	return u.Scan(id) == nil
}

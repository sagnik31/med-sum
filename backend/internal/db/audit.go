package db

import (
	"context"
	"encoding/json"
	"fmt"
	"net/netip"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// AuditEvent is one row of the audit log: who did what to which record,
// from where, and how it ended. It never holds clinical content: targets
// are ids, and Details holds only counts, field names and reasons.
// See docs/adr/0010-staff-access-and-security.md.
type AuditEvent struct {
	ID         int64          `json:"id"`
	At         time.Time      `json:"at"`
	OrgID      *string        `json:"-"`
	ActorID    *string        `json:"actor_id"`
	ActorName  *string        `json:"actor_name"`
	Action     string         `json:"action"`
	TargetType *string        `json:"target_type"`
	TargetID   *string        `json:"target_id"`
	PatientID  *string        `json:"patient_id"`
	Outcome    string         `json:"outcome"`
	IP         string         `json:"ip"`
	UserAgent  string         `json:"user_agent"`
	Details    map[string]any `json:"details"`
}

// Audit outcomes.
const (
	OutcomeSuccess = "success"
	OutcomeFailure = "failure"
	OutcomeDenied  = "denied"
)

// ActionAccountLocked is the event RecordLoginFailure adds when a failure
// locks the account.
const ActionAccountLocked = "account_locked"

// about sets the event's target id and, when given, its patient, for a
// row whose id is known only once it is inserted. It returns the event,
// and is a no-op on nil.
func (ev *AuditEvent) about(targetID, patientID string) *AuditEvent {
	if ev == nil {
		return nil
	}
	ev.TargetID = &targetID
	if patientID != "" {
		ev.PatientID = &patientID
	}
	return ev
}

// dbtx is the pool or a transaction.
type dbtx interface {
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
}

// writeAudit inserts the event through q, so a mutation writes its audit
// row in its own transaction and the two commit or fail together. A nil
// event writes nothing: only maintenance paths (a password rehash, an
// expired session's clean-up) pass nil.
func writeAudit(ctx context.Context, q dbtx, ev *AuditEvent) error {
	if ev == nil {
		return nil
	}
	if ev.Action == "" || ev.Outcome == "" {
		return fmt.Errorf("audit event needs an action and an outcome")
	}
	var details []byte
	if len(ev.Details) > 0 {
		var err error
		if details, err = json.Marshal(ev.Details); err != nil {
			return fmt.Errorf("audit details: %w", err)
		}
	}
	at := ev.At
	if at.IsZero() {
		at = time.Now()
	}
	// The column is inet; an address that doesn't parse is left out
	// rather than failing the request it records.
	var ip *string
	if addr, err := netip.ParseAddr(ev.IP); err == nil {
		v := addr.String()
		ip = &v
	}
	_, err := q.Exec(ctx, `
INSERT INTO audit_events
    (at, org_id, actor_id, action, target_type, target_id, patient_id, outcome, ip, user_agent, details)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::inet, NULLIF($10, ''), $11)`,
		at, ev.OrgID, ev.ActorID, ev.Action, ev.TargetType, ev.TargetID, ev.PatientID,
		ev.Outcome, ip, ev.UserAgent, details)
	return err
}

// RecordAudit writes an event on its own, for reads and for actions the
// AI service carries out. Callers write it before answering and fail the
// request when it fails, so no read goes unrecorded.
func (s *Store) RecordAudit(ctx context.Context, ev AuditEvent) error {
	return writeAudit(ctx, s.pool, &ev)
}

// AuditFilter narrows ListAuditEvents. Empty fields don't filter.
type AuditFilter struct {
	ActorID   string
	PatientID string
	From, To  *time.Time
	BeforeID  int64 // for the next page: only events older than this id
	Limit     int
}

// MaxAuditPage is the most events ListAuditEvents returns at once.
const MaxAuditPage = 200

// ListAuditEvents returns the organisation's events, newest first, with
// each actor's name.
func (s *Store) ListAuditEvents(ctx context.Context, orgID string, f AuditFilter) ([]AuditEvent, error) {
	where := []string{"e.org_id = $1"}
	args := []any{orgID}
	add := func(cond string, v any) {
		args = append(args, v)
		where = append(where, fmt.Sprintf(cond, len(args)))
	}
	if f.ActorID != "" {
		add("e.actor_id = $%d", f.ActorID)
	}
	if f.PatientID != "" {
		add("e.patient_id = $%d", f.PatientID)
	}
	if f.From != nil {
		add("e.at >= $%d", *f.From)
	}
	if f.To != nil {
		add("e.at < $%d", *f.To)
	}
	if f.BeforeID > 0 {
		add("e.id < $%d", f.BeforeID)
	}
	limit := f.Limit
	if limit <= 0 || limit > MaxAuditPage {
		limit = MaxAuditPage
	}
	args = append(args, limit)
	rows, err := s.pool.Query(ctx, `
SELECT e.id, e.at, e.org_id::text, e.actor_id::text, u.full_name, e.action, e.target_type,
       e.target_id, e.patient_id::text, e.outcome, COALESCE(host(e.ip), ''),
       COALESCE(e.user_agent, ''), e.details
FROM audit_events e
LEFT JOIN staff_users u ON u.id = e.actor_id
WHERE `+strings.Join(where, " AND ")+`
ORDER BY e.id DESC
LIMIT $`+fmt.Sprint(len(args)), args...)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (AuditEvent, error) {
		var ev AuditEvent
		var details []byte
		if err := row.Scan(&ev.ID, &ev.At, &ev.OrgID, &ev.ActorID, &ev.ActorName, &ev.Action,
			&ev.TargetType, &ev.TargetID, &ev.PatientID, &ev.Outcome, &ev.IP, &ev.UserAgent,
			&details); err != nil {
			return ev, err
		}
		if len(details) > 0 {
			if err := json.Unmarshal(details, &ev.Details); err != nil {
				return ev, err
			}
		}
		return ev, nil
	})
}

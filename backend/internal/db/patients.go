package db

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// Patient maps to the `patients` table: a record, not an account. Every
// method that reads or writes one takes the caller's organisation and
// filters on it in SQL, so a patient of another organisation is simply
// not found. See docs/adr/0010-staff-access-and-security.md.
type Patient struct {
	ID          string    `json:"id"`
	OrgID       string    `json:"-"`
	MRN         string    `json:"mrn"`
	FullName    string    `json:"full_name"`
	DateOfBirth *string   `json:"date_of_birth"` // YYYY-MM-DD
	AgeYears    *int      `json:"age_years"`     // only when no date of birth is known
	Sex         *string   `json:"sex"`           // F, M or O
	CreatedBy   *string   `json:"created_by"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// PatientFields are the details a clinician enters for a patient.
type PatientFields struct {
	MRN         string
	FullName    string
	DateOfBirth *string // YYYY-MM-DD
	AgeYears    *int
	Sex         *string
}

// ErrPatientChanged means the patient's details changed since they were
// read: saving over them would undo that change.
var ErrPatientChanged = errors.New("the patient's details changed since you opened them; reload the page")

// ErrMRNTaken means the organisation already has a patient with that MRN,
// ignoring case and spaces.
var ErrMRNTaken = errors.New("a patient with this MRN already exists")

const patientColumns = `p.id, p.org_id, p.mrn, p.full_name, p.date_of_birth::text, p.age_years,
       p.sex, p.created_by, p.created_at, p.updated_at`

func scanPatient(row pgx.Row) (*Patient, error) {
	var p Patient
	err := row.Scan(&p.ID, &p.OrgID, &p.MRN, &p.FullName, &p.DateOfBirth, &p.AgeYears,
		&p.Sex, &p.CreatedBy, &p.CreatedAt, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &p, nil
}

// CreatePatient adds a patient to the organisation and writes ev (with
// the new patient as its target) in the same transaction.
func (s *Store) CreatePatient(ctx context.Context, orgID, createdBy string, f PatientFields, ev *AuditEvent) (*Patient, error) {
	const q = `
INSERT INTO patients AS p (org_id, mrn, full_name, date_of_birth, age_years, sex, created_by)
VALUES ($1, $2, $3, $4::date, $5, $6, $7)
RETURNING ` + patientColumns
	var p *Patient
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var err error
		p, err = scanPatient(tx.QueryRow(ctx, q, orgID, f.MRN, f.FullName, f.DateOfBirth, f.AgeYears, f.Sex, createdBy))
		if err != nil {
			return err
		}
		return writeAudit(ctx, tx, ev.about(p.ID, p.ID))
	})
	if isUniqueViolation(err) {
		return nil, ErrMRNTaken
	}
	if err != nil {
		return nil, err
	}
	return p, nil
}

// UpdatePatient replaces a patient's details, if they are still the ones
// read at readAt (their updated_at), and writes ev in the same transaction.
// It returns ErrPatientChanged when they are not, and nil when the
// organisation has no such patient, writing nothing.
func (s *Store) UpdatePatient(ctx context.Context, orgID, patientID string, readAt time.Time, f PatientFields, ev *AuditEvent) (*Patient, error) {
	const q = `
UPDATE patients AS p
SET mrn = $3, full_name = $4, date_of_birth = $5::date, age_years = $6, sex = $7, updated_at = now()
WHERE p.id = $1 AND p.org_id = $2 AND p.updated_at = $8
RETURNING ` + patientColumns
	var p *Patient
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var err error
		p, err = scanPatient(tx.QueryRow(ctx, q, patientID, orgID, f.MRN, f.FullName, f.DateOfBirth, f.AgeYears, f.Sex, readAt))
		if err != nil {
			return err
		}
		if p == nil {
			var exists bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM patients WHERE id = $1 AND org_id = $2)`,
				patientID, orgID).Scan(&exists); err != nil || !exists {
				return err
			}
			return ErrPatientChanged
		}
		return writeAudit(ctx, tx, ev)
	})
	if isUniqueViolation(err) {
		return nil, ErrMRNTaken
	}
	if err != nil {
		return nil, err
	}
	return p, nil
}

// GetPatient returns the organisation's patient, or nil.
func (s *Store) GetPatient(ctx context.Context, orgID, patientID string) (*Patient, error) {
	return scanPatient(s.pool.QueryRow(ctx,
		`SELECT `+patientColumns+` FROM patients p WHERE p.id = $1 AND p.org_id = $2`, patientID, orgID))
}

// maxPatientList caps a patient search; the list is for finding one.
const maxPatientList = 200

// likeEscape makes text match literally inside a LIKE pattern.
func likeEscape(text string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(text)
}

// ListPatients returns the organisation's patients by name. A query
// matches part of the name or the start of the MRN, ignoring case.
func (s *Store) ListPatients(ctx context.Context, orgID, query string) ([]Patient, error) {
	query = strings.TrimSpace(query)
	const q = `
SELECT ` + patientColumns + `
FROM patients p
WHERE p.org_id = $1
  AND ($2 = '' OR p.full_name ILIKE '%' || $3 || '%' OR p.mrn ILIKE $3 || '%')
ORDER BY lower(p.full_name), p.mrn
LIMIT $4`
	rows, err := s.pool.Query(ctx, q, orgID, query, likeEscape(query), maxPatientList)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Patient{}
	for rows.Next() {
		p, err := scanPatient(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *p)
	}
	return out, rows.Err()
}

// PatientSummary is a patient's cumulative summary and its job status.
type PatientSummary struct {
	HTML      *string
	Status    *string // "processing", "completed" or "failed"; nil if never requested
	Error     *string // set when Status is "failed"
	UpdatedAt *time.Time
}

// GetPatientSummary returns the patient's summary; all fields are nil
// when none was ever asked for. Callers load the patient first, which is
// where the organisation is checked.
func (s *Store) GetPatientSummary(ctx context.Context, patientID string) (*PatientSummary, error) {
	var p PatientSummary
	err := s.pool.QueryRow(ctx,
		`SELECT html, status, error, updated_at FROM patient_summaries WHERE patient_id = $1`, patientID,
	).Scan(&p.HTML, &p.Status, &p.Error, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return &PatientSummary{}, nil
	}
	if err != nil {
		return nil, err
	}
	return &p, nil
}

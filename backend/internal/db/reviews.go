package db

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// ----- Clinician review. See docs/adr/0011-clinician-review.md. -----

// ErrReviewConflict is returned by ApplyReview when the record changed
// since the caller read it (another review, or a reprocess), or the
// document is being processed: nothing is written.
var ErrReviewConflict = errors.New("the record changed or is being processed")

// MaxReviewQueue caps the review queue's length.
const MaxReviewQueue = 500

// flaggedCount is the SQL for how many values of a record r still await
// review: every provenance in it (the document, the patient and each item
// of every group) whose status is low_confidence. Lax mode unwraps each
// group's array, so one path reaches items and header holders alike.
const flaggedCount = `(SELECT count(*) FROM jsonb_path_query(r.record,
    'lax $.*.provenance.status ? (@ == "low_confidence")'))`

// ReviewQueueEntry is one document waiting for a clinician.
type ReviewQueueEntry struct {
	DocumentID   string     `json:"document_id"`
	PatientID    string     `json:"patient_id"`
	PatientName  string     `json:"patient_name"`
	PatientMRN   string     `json:"patient_mrn"`
	OriginalName string     `json:"original_name"`
	DocumentType *string    `json:"document_type"`
	DocumentDate *string    `json:"document_date"` // as derived; null when undated
	UploadedAt   time.Time  `json:"uploaded_at"`
	QueuedAt     time.Time  `json:"queued_at"` // when it began waiting
	Flagged      int        `json:"flagged"`   // values still awaiting review
	Reason       string     `json:"reason"`    // needs_review, or audit (a sampled verified document)
	UpdatedAt    *time.Time `json:"-"`
}

// ListReviewQueue lists the organisation's documents that wait for a
// review, longest waiting first: every live document whose processing
// ended in needs_review, and every verified one sampled for an audit
// whose audit is still open (only a sample of the document's current
// record: a reprocess leaves the old one behind).
func (s *Store) ListReviewQueue(ctx context.Context, orgID string) ([]ReviewQueueEntry, error) {
	rows, err := s.pool.Query(ctx, `
SELECT d.id, d.patient_id, p.full_name, p.mrn, d.original_name,
       r.record -> 'document' ->> 'type',
       r.record -> 'document' -> 'derived' ->> 'document_date',
       d.uploaded_at, w.queued_at, COALESCE(`+flaggedCount+`, 0), w.reason
FROM (
    SELECT i.document_id, i.updated_at AS queued_at, 'needs_review' AS reason
    FROM insights i WHERE i.status = 'needs_review'
    UNION ALL
    SELECT a.document_id, a.sampled_at, 'audit'
    FROM audit_samples a
    JOIN insights i ON i.document_id = a.document_id AND i.status = 'verified'
    JOIN document_records dr ON dr.document_id = a.document_id AND dr.run_id = a.run_id
    WHERE a.completed_at IS NULL
) w
JOIN documents d ON d.id = w.document_id
JOIN patients p ON p.id = d.patient_id
LEFT JOIN document_records r ON r.document_id = d.id
WHERE p.org_id = $1 AND d.deleted_at IS NULL
ORDER BY w.queued_at, d.id
LIMIT $2`, orgID, MaxReviewQueue)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []ReviewQueueEntry{}
	for rows.Next() {
		var e ReviewQueueEntry
		if err := rows.Scan(&e.DocumentID, &e.PatientID, &e.PatientName, &e.PatientMRN, &e.OriginalName,
			&e.DocumentType, &e.DocumentDate, &e.UploadedAt, &e.QueuedAt, &e.Flagged, &e.Reason); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// RecordReview is one review to store: the reviewed record and page the
// AI service built, and the trail row.
type RecordReview struct {
	Record     json.RawMessage
	HTML       string
	Verified   bool // nothing awaits review any more: the document becomes verified
	RunID      *string
	ItemID     string
	Field      *string
	Action     string // confirm or correct
	Original   *string
	Corrected  *string
	Note       *string
	Signals    []string
	Findings   []string
	ReviewerID string
	At         time.Time
}

// ApplyReview stores a review of a live document of the organisation in
// one transaction: the record (only if its updated_at is still
// recordUpdatedAt), the page and, when Verified, the verified status
// (only while the document's processing has ended: insights is
// processing from the moment a job is queued until its run ends),
// the record_reviews row (marked audit while the document is an open
// audit sample) and ev. It returns the record's new updated_at,
// or ErrReviewConflict, writing nothing.
func (s *Store) ApplyReview(ctx context.Context, orgID, docID string, recordUpdatedAt time.Time,
	rv RecordReview, ev *AuditEvent) (time.Time, error) {
	var updated time.Time
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
UPDATE insights i
SET html_insights = $3,
    status = CASE WHEN $4 THEN 'verified' ELSE i.status END,
    updated_at = CASE WHEN $4 AND i.status <> 'verified' THEN $5 ELSE i.updated_at END
FROM documents d JOIN patients p ON p.id = d.patient_id
WHERE i.document_id = $1 AND d.id = i.document_id AND p.org_id = $2
  AND d.deleted_at IS NULL AND i.status IN ('verified', 'needs_review')`,
			docID, orgID, rv.HTML, rv.Verified, rv.At)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return ErrReviewConflict
		}
		err = tx.QueryRow(ctx, `
UPDATE document_records SET record = $2, updated_at = $4
WHERE document_id = $1 AND updated_at = $3
RETURNING updated_at`, docID, []byte(rv.Record), recordUpdatedAt, rv.At).Scan(&updated)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrReviewConflict
		}
		if err != nil {
			return err
		}
		signals, findings := rv.Signals, rv.Findings
		if signals == nil {
			signals = []string{}
		}
		if findings == nil {
			findings = []string{}
		}
		if _, err := tx.Exec(ctx, `
INSERT INTO record_reviews (document_id, run_id, item_id, field, action, original, corrected,
                            note, signals, findings, audit, reviewer_id, reviewed_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
        `+openAuditOfTheRecord+`,
        $11, $12)`,
			docID, rv.RunID, rv.ItemID, rv.Field, rv.Action, rv.Original, rv.Corrected,
			rv.Note, signals, findings, rv.ReviewerID, rv.At); err != nil {
			return err
		}
		return writeAudit(ctx, tx, ev)
	})
	if err != nil {
		return time.Time{}, err
	}
	return updated, nil
}

// openAuditOfTheRecord is the SQL for whether document $1 has an open
// audit sample of the run its record is from. A sample is of one run (see
// docs/adr/0011-clinician-review.md): a reprocess leaves the old
// run's sample open but dead, and it must not tag reviews of the new
// record as audit reviews, which would take them out of signal precision
// and count their corrections as escapes.
const openAuditOfTheRecord = `EXISTS (SELECT 1 FROM audit_samples a
    JOIN document_records dr ON dr.document_id = a.document_id AND dr.run_id = a.run_id
    WHERE a.document_id = $1 AND a.completed_at IS NULL)`

// ErrNoOpenAudit is returned by CompleteAudit when the document has no
// audit sample left open for the run its record is from.
var ErrNoOpenAudit = errors.New("the document is not an open audit sample")

// AuditIncompleteError is CompleteAudit's answer while values of the
// record have had no review during the audit.
type AuditIncompleteError struct {
	Unreviewed int // values neither confirmed nor corrected yet
}

func (e *AuditIncompleteError) Error() string {
	return fmt.Sprintf("%d values are not yet confirmed or corrected", e.Unreviewed)
}

// unreviewedInAudit is the SQL for how many holders of the record r (the
// document, the patient and every item with a provenance) have no review
// made during the open audit sample a.
const unreviewedInAudit = `(SELECT count(*) FROM (` + holdersWithoutAuditReview + `) c)`

// unreviewedHolders is the SQL for the ids of those holders, in order.
const unreviewedHolders = `(SELECT array_agg(id ORDER BY id) FROM (` + holdersWithoutAuditReview + `) u)`

// holdersWithoutAuditReview is the SQL for the holders of the record r
// with no review made during the open audit sample a, one id per row.
const holdersWithoutAuditReview = `SELECT h.id FROM (
    SELECT 'document' AS id WHERE jsonb_typeof(r.record #> '{document,provenance}') = 'object'
    UNION ALL
    SELECT 'patient' WHERE jsonb_typeof(r.record #> '{patient,provenance}') = 'object'
    UNION ALL
    SELECT i #>> '{}' FROM jsonb_path_query(r.record,
        'lax $.*[*] ? (@.id.type() == "string" && @.provenance.type() == "object").id') i
  ) h
  WHERE NOT EXISTS (SELECT 1 FROM record_reviews rr
    WHERE rr.document_id = a.document_id AND rr.run_id = a.run_id
      AND rr.item_id = h.id AND rr.audit AND rr.reviewed_at >= a.sampled_at)`

// OpenAudit is a document's open audit sample of the run its record is
// from: when it was sampled, and the holders of the record (the document,
// the patient and every item with a provenance) not yet confirmed or
// corrected during it, by the rule CompleteAudit counts with.
type OpenAudit struct {
	SampledAt  time.Time
	Unreviewed []string
}

// GetOpenAudit returns the open audit of document docID, or (nil, nil)
// when it has none for the run its record is from. Callers check
// ownership first, as for GetDocumentRecord.
func (s *Store) GetOpenAudit(ctx context.Context, docID string) (*OpenAudit, error) {
	var a OpenAudit
	err := s.pool.QueryRow(ctx, `
SELECT a.sampled_at, COALESCE(`+unreviewedHolders+`, '{}')
FROM audit_samples a
JOIN document_records r ON r.document_id = a.document_id AND r.run_id = a.run_id
WHERE a.document_id = $1 AND a.completed_at IS NULL`, docID).Scan(&a.SampledAt, &a.Unreviewed)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &a, nil
}

// CompleteAudit finishes the open audit of a live document of the
// organisation, with ev, in one transaction. Every value must have been
// confirmed or corrected during the audit first, or it returns an
// *AuditIncompleteError and writes nothing. Reviews after it are no
// longer audit reviews, and the document leaves the queue. It returns
// ErrNoOpenAudit, writing nothing, when there is no open sample of the
// record's run (an older run's sample is dead, as in openAuditOfTheRecord).
func (s *Store) CompleteAudit(ctx context.Context, orgID, docID, userID string, at time.Time, ev *AuditEvent) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		// The sample is locked, so a review can't land between the count
		// and the update unseen.
		var unreviewed int
		var runID string
		err := tx.QueryRow(ctx, `
SELECT COALESCE(`+unreviewedInAudit+`, 0), a.run_id::text
FROM audit_samples a
JOIN documents d ON d.id = a.document_id
JOIN patients p ON p.id = d.patient_id
JOIN document_records r ON r.document_id = a.document_id AND r.run_id = a.run_id
WHERE a.document_id = $1 AND a.completed_at IS NULL
  AND p.org_id = $2 AND d.deleted_at IS NULL
FOR UPDATE OF a`, docID, orgID).Scan(&unreviewed, &runID)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNoOpenAudit
		}
		if err != nil {
			return err
		}
		if unreviewed > 0 {
			return &AuditIncompleteError{Unreviewed: unreviewed}
		}
		if _, err := tx.Exec(ctx, `
UPDATE audit_samples SET completed_at = $3, completed_by = $2
WHERE document_id = $1 AND run_id = $4 AND completed_at IS NULL`, docID, userID, at, runID); err != nil {
			return err
		}
		return writeAudit(ctx, tx, ev)
	})
}

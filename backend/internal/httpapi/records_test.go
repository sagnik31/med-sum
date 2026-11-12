package httpapi

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"backend/internal/db"
)

// testRecord is a small stored record in the AI service's format: patient
// and document header values, one validated result, one flagged result, a
// remark without provenance, and the page they cite.
const testRecord = `{
  "schema_version": 1,
  "document": {
    "document_id": "doc",
    "type": "lab_report",
    "page_count": 1,
    "has_text_layer": true,
    "printed": {
      "facility": {"citation": {"page": 1, "first_line": 1, "last_line": 1, "quote": "Asha Labs"}, "text": "Asha Labs"},
      "clinicians": [{"citation": {"page": 1, "first_line": 3, "last_line": 3, "quote": "Ref. By: Dr. A"}, "text": "Ref. By: Dr. A"}],
      "report_date": null,
      "collection_date": null,
      "prescription_date": null
    },
    "derived": {"date_source": "undated"},
    "provenance": {"run_id": "r", "node": "finish", "status": "low_confidence",
                   "low_confidence_reasons": ["LC-12: No date"]}
  },
  "pages": [{"page": 1, "lines": ["Asha Labs", "Patient Name: P", "Ref. By: Dr. A", "| Hb | 9.1 | g/dL |", "| TSH | 4 | mIU/L |", "Note: repeat"]}],
  "patient": {
    "printed": {
      "name": {"citation": {"page": 1, "first_line": 2, "last_line": 2, "quote": "Patient Name: P"}, "text": "P"},
      "age": null, "sex": null, "date_of_birth": null, "patient_id": null
    },
    "provenance": {"run_id": "r", "node": "parse", "status": "validated", "low_confidence_reasons": []}
  },
  "observations": [
    {"id": "obs-1", "printed": {"citation": {"page": 1, "first_line": 4, "last_line": 4, "quote": "| Hb | 9.1 | g/dL |"}, "test_name": "Hb", "value_text": "9.1"},
     "provenance": {"run_id": "r", "node": "parse", "status": "validated", "low_confidence_reasons": []}},
    {"id": "obs-2", "printed": {"citation": {"page": 1, "first_line": 5, "last_line": 5, "quote": "| TSH | 4 | mIU/L |"}, "test_name": "TSH", "value_text": "4"},
     "provenance": {"run_id": "r", "node": "parse", "status": "low_confidence",
                    "low_confidence_reasons": ["LC-10 [flag_printed]: The printed flag differs"]}}
  ],
  "remarks": [
    {"id": "rem-1", "printed": {"citation": {"page": 1, "first_line": 6, "last_line": 6, "quote": "Note: repeat"}, "text": "repeat"}}
  ]
}`

func addRecord(f *fakeStore, docID, record string) {
	runID := "11111111-1111-4111-8111-111111111111"
	f.records[docID] = &db.DocumentRecord{
		DocumentID: docID, SchemaVersion: 1, Record: json.RawMessage(record),
		RunID: &runID, UpdatedAt: time.Date(2026, 11, 12, 10, 0, 0, 0, time.UTC),
	}
}

func TestGetRecordReturnsTheRecordAndItsCitations(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(me, "a.pdf")
	addRecord(e.store, doc, testRecord)
	e.store.insights[doc] = &db.Insight{DocumentID: doc, Status: "needs_review"}

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record", auth, nil, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rec.Code, rec.Body)
	}
	resp := decodeJSON[recordResponse](t, rec)

	if resp.DocumentID != doc || resp.Status != "needs_review" || resp.SchemaVersion != 1 ||
		resp.RunID == nil || *resp.RunID != "11111111-1111-4111-8111-111111111111" {
		t.Errorf("response header = %+v", resp)
	}

	// The record comes back as stored, pages included.
	var stored, got map[string]any
	_ = json.Unmarshal([]byte(testRecord), &stored)
	if err := json.Unmarshal(resp.Record, &got); err != nil {
		t.Fatal(err)
	}
	sj, _ := json.Marshal(stored)
	gj, _ := json.Marshal(got)
	if string(sj) != string(gj) {
		t.Errorf("record changed on the way out:\n%s\n%s", gj, sj)
	}

	type key struct{ group, item, field string }
	byKey := map[key]citationResult{}
	for _, c := range resp.Citations {
		f := ""
		if c.Field != nil {
			f = *c.Field
		}
		byKey[key{c.Group, c.ItemID, f}] = c
	}
	if len(resp.Citations) != 6 || len(byKey) != 6 {
		t.Fatalf("citations = %+v, want 6 distinct", resp.Citations)
	}
	order := []key{
		{"patient", "patient", "name"},
		{"document", "document", "facility"},
		{"document", "document", "clinicians[0]"},
		{"observations", "obs-1", ""},
		{"observations", "obs-2", ""},
		{"remarks", "rem-1", ""},
	}
	for i, k := range order {
		c := resp.Citations[i]
		f := ""
		if c.Field != nil {
			f = *c.Field
		}
		if (key{c.Group, c.ItemID, f}) != k {
			t.Errorf("citation %d = %s/%s/%s, want %v", i, c.Group, c.ItemID, f, k)
		}
	}

	flagged := byKey[key{"observations", "obs-2", ""}]
	if flagged.Status == nil || *flagged.Status != "low_confidence" ||
		len(flagged.Reasons) != 1 || !strings.HasPrefix(flagged.Reasons[0], "LC-10") {
		t.Errorf("flagged item = %+v", flagged)
	}
	if flagged.Page != 1 || flagged.FirstLine != 5 || flagged.LastLine != 5 || flagged.Quote != "| TSH | 4 | mIU/L |" {
		t.Errorf("flagged citation = %+v", flagged)
	}
	if ok := byKey[key{"observations", "obs-1", ""}]; ok.Status == nil || *ok.Status != "validated" || len(ok.Reasons) != 0 {
		t.Errorf("validated item = %+v", ok)
	}
	if clin := byKey[key{"document", "document", "clinicians[0]"}]; clin.Status == nil || *clin.Status != "low_confidence" ||
		len(clin.Reasons) != 1 {
		t.Errorf("document header value = %+v, want the document's provenance", clin)
	}
	if rem := byKey[key{"remarks", "rem-1", ""}]; rem.Status != nil || rem.Reasons == nil || len(rem.Reasons) != 0 {
		t.Errorf("item without provenance = %+v, want null status and no reasons", rem)
	}
	if !strings.Contains(rec.Body.String(), `"reasons":[]`) {
		t.Error("an item without reasons should list [] rather than null")
	}
}

func TestGetRecordOfAnotherUsersDocumentIsNotFound(t *testing.T) {
	e := newTestEnv(t)
	_, auth := e.login("me@hospital.test")
	other, _ := e.login("other@hospital.test")
	theirs := e.store.addDocument(other, "b.pdf")
	addRecord(e.store, theirs, testRecord)

	rec := e.do(http.MethodGet, "/documents/"+theirs+"/record", auth, nil, "")
	if rec.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", rec.Code)
	}
	if strings.Contains(rec.Body.String(), "Asha Labs") {
		t.Error("another user's record leaked")
	}
}

func TestGetRecordBeforeOneExistsIsNotFound(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(me, "a.pdf")
	e.store.insights[doc] = &db.Insight{DocumentID: doc, Status: "processing"}

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record", auth, nil, "")
	if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), "no record yet") {
		t.Errorf("status = %d %q, want 404 no record yet", rec.Code, rec.Body)
	}
}

func TestGetRecordRouting(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(me, "a.pdf")

	if rec := e.do(http.MethodGet, "/documents/"+doc+"/record", "", nil, ""); rec.Code != http.StatusUnauthorized {
		t.Errorf("without a session = %d, want 401", rec.Code)
	}
	if rec := e.do(http.MethodPost, "/documents/"+doc+"/record", auth, nil, ""); rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST = %d, want 405", rec.Code)
	}
	if rec := e.do(http.MethodGet, "/documents/not-a-uuid/record", auth, nil, ""); rec.Code != http.StatusBadRequest {
		t.Errorf("malformed id = %d, want 400", rec.Code)
	}
	if rec := e.do(http.MethodGet, "/documents/"+doc+"/record/extra", auth, nil, ""); rec.Code != http.StatusNotFound {
		t.Errorf("extra segment = %d, want 404", rec.Code)
	}
}

func TestGetRecordIsForbiddenToAnAdmin(t *testing.T) {
	e := newTestEnv(t)
	adminID, auth := e.loginWith("admin@hospital.test", db.RoleAdmin)
	doc := e.store.addDocument(adminID, "a.pdf")
	addRecord(e.store, doc, testRecord)

	if rec := e.do(http.MethodGet, "/documents/"+doc+"/record", auth, nil, ""); rec.Code != http.StatusForbidden {
		t.Errorf("status = %d, want 403", rec.Code)
	}
}

func TestGetRecordErrorsAreInternalWithoutDetails(t *testing.T) {
	e := newTestEnv(t)
	me, auth := e.login("me@hospital.test")
	doc := e.store.addDocument(me, "a.pdf")
	addRecord(e.store, doc, `{"observations": [{"id": "obs-1", "printed": {}}]}`)

	rec := e.do(http.MethodGet, "/documents/"+doc+"/record", auth, nil, "")
	if rec.Code != http.StatusInternalServerError || strings.Contains(rec.Body.String(), "obs-1") {
		t.Errorf("unreadable record = %d %q, want 500 without details", rec.Code, rec.Body)
	}

	e.store.failWith = os.ErrPermission
	rec = e.do(http.MethodGet, "/documents/"+doc+"/record", auth, nil, "")
	if rec.Code != http.StatusInternalServerError || strings.Contains(rec.Body.String(), "permission") {
		t.Errorf("database error = %d %q, want 500 without details", rec.Code, rec.Body)
	}
}

// The citation list reads records in the AI service's real format: every
// answer key in the synthetic dataset (the same shape the pipeline stores)
// gives a citation for each item and header value, pointing at lines of
// its own pages.
func TestRecordCitationsReadTheDatasetKeys(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join("..", "..", "..", "scripts", "evals", "dataset", "keys", "*.json"))
	if err != nil || len(paths) == 0 {
		t.Fatalf("no dataset keys found (%v)", err)
	}
	for _, p := range paths {
		raw, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		citations, err := recordCitations(raw)
		if err != nil {
			t.Errorf("%s: %v", filepath.Base(p), err)
			continue
		}
		var rec struct {
			Pages []struct {
				Lines []string `json:"lines"`
			} `json:"pages"`
			Observations []json.RawMessage `json:"observations"`
			Medications  []json.RawMessage `json:"medications"`
		}
		if err := json.Unmarshal(raw, &rec); err != nil {
			t.Fatal(err)
		}
		items := 0
		for _, c := range citations {
			if c.Group == "observations" || c.Group == "medications" {
				items++
			}
			if c.Page < 1 || c.Page > len(rec.Pages) {
				t.Errorf("%s %s/%s: page %d out of range", filepath.Base(p), c.Group, c.ItemID, c.Page)
				continue
			}
			lines := rec.Pages[c.Page-1].Lines
			if c.FirstLine < 1 || c.LastLine > len(lines) || c.FirstLine > c.LastLine {
				t.Errorf("%s %s/%s: lines %d-%d out of range", filepath.Base(p), c.Group, c.ItemID, c.FirstLine, c.LastLine)
				continue
			}
			text := strings.Join(lines[c.FirstLine-1:c.LastLine], "\n")
			if !strings.Contains(text, c.Quote) {
				t.Errorf("%s %s/%s: quote %q is not on lines %d-%d", filepath.Base(p), c.Group, c.ItemID, c.Quote, c.FirstLine, c.LastLine)
			}
		}
		if items != len(rec.Observations)+len(rec.Medications) {
			t.Errorf("%s: %d result and medicine citations for %d items", filepath.Base(p), items, len(rec.Observations)+len(rec.Medications))
		}
	}
}

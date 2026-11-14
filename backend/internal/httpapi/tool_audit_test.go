package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// getAsTool sends a GET as the session, labelled with an MCP tool when tool is
// set.
func (e *testEnv) getAsTool(path, session, tool string) *httptest.ResponseRecorder {
	e.t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.AddCookie(&http.Cookie{Name: sessionCookie, Value: session})
	if tool != "" {
		req.Header.Set(toolHeader, tool)
	}
	rec := httptest.NewRecorder()
	e.server.ServeHTTP(rec, req)
	return rec
}

// Every read the MCP server makes writes one audit row, and the row names
// the tool it was made for. See docs/adr/0012-shared-tool-layer.md.
func TestEachReadTheMCPServerMakesIsOneAuditRowNamingItsTool(t *testing.T) {
	re := newReviewEnv(t)
	for _, c := range []struct{ tool, path string }{
		{"find_patients", "/patients?q=SH"},
		{"list_documents", "/patients/" + re.patient.ID + "/documents"},
		{"get_lines", "/documents/" + re.doc + "/record"},
	} {
		before := len(re.store.audit)
		if rec := re.getAsTool(c.path, re.auth, c.tool); rec.Code != http.StatusOK {
			t.Fatalf("%s: status = %d: %s", c.tool, rec.Code, rec.Body)
		}
		added := re.store.audit[before:]
		if len(added) != 1 {
			t.Fatalf("%s: %d audit rows, want 1", c.tool, len(added))
		}
		if got := added[0].Details["tool"]; got != c.tool {
			t.Errorf("%s: details = %v", c.tool, added[0].Details)
		}
	}
}

func TestAToolLabelKeepsTheEventsOtherDetails(t *testing.T) {
	re := newReviewEnv(t)
	before := len(re.store.audit)
	re.getAsTool("/patients?q=SH", re.auth, "find_patients")
	ev := re.store.audit[before]
	if ev.Details["tool"] != "find_patients" || ev.Details["results"] == nil {
		t.Errorf("details = %v", ev.Details)
	}
}

func TestALabelThatIsNotAToolNameIsLeftOut(t *testing.T) {
	re := newReviewEnv(t)
	for _, bad := range []string{"Get-Lines", "get lines", "x<script>", "a_very_long_tool_name_that_goes_past_forty_chars"} {
		before := len(re.store.audit)
		re.getAsTool("/documents/"+re.doc+"/record", re.auth, bad)
		if _, ok := re.store.audit[before].Details["tool"]; ok {
			t.Errorf("%q was kept: %v", bad, re.store.audit[before].Details)
		}
	}
}

// A labelled request for another organisation's document is refused like
// any other: the label changes nothing but the audit row.
func TestAToolCantReadAnotherOrganisationsDocument(t *testing.T) {
	re := newReviewEnv(t)
	theirs := re.store.addDocument(re.store.addPatient("org-2", "OT-1"), re.userID, "b.pdf")
	addRecord(re.store, theirs, testRecord)
	if rec := re.getAsTool("/documents/"+theirs+"/record", re.auth, "get_item"); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d", rec.Code)
	}
}

package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"
)

// The record's shape is owned by the AI service, which exports it as a
// JSON schema; recShape is Go's copy of the parts the citation list
// reads. A group or header value added on the Python side (an additive
// change needs no version bump, see docs/adr/0003-printed-and-derived-values.md)
// would otherwise be left out of /documents/{id}/record citations, which
// the review screen reads, while both sides' own tests still pass. This
// test ties the two together.

type jsonSchema map[string]any

func loadRecordSchema(t *testing.T) (props, defs map[string]jsonSchema) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "scripts", "schemas", "document_record.v1.json"))
	if err != nil {
		t.Fatalf("the exported record schema: %v", err)
	}
	var s struct {
		Properties map[string]jsonSchema `json:"properties"`
		Defs       map[string]jsonSchema `json:"$defs"`
	}
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatal(err)
	}
	return s.Properties, s.Defs
}

// defName is the definition a property refers to: directly, through a
// nullable anyOf, or, for an array, through its items.
func defName(p jsonSchema) string {
	if ref, ok := p["$ref"].(string); ok {
		return strings.TrimPrefix(ref, "#/$defs/")
	}
	if anyOf, ok := p["anyOf"].([]any); ok {
		for _, alt := range anyOf {
			if m, ok := alt.(map[string]any); ok {
				if name := defName(m); name != "" {
					return name
				}
			}
		}
	}
	if items, ok := p["items"].(map[string]any); ok {
		return defName(items)
	}
	return ""
}

func propertiesOf(t *testing.T, defs map[string]jsonSchema, name string) map[string]jsonSchema {
	t.Helper()
	def, ok := defs[name]
	if !ok {
		t.Fatalf("the schema has no definition %s", name)
	}
	out := map[string]jsonSchema{}
	props, _ := def["properties"].(map[string]any)
	for k, v := range props {
		m, _ := v.(map[string]any)
		out[k] = m
	}
	return out
}

// jsonFields maps a struct's JSON names to their Go types.
func jsonFields(typ reflect.Type) map[string]reflect.Type {
	for typ.Kind() == reflect.Pointer {
		typ = typ.Elem()
	}
	out := map[string]reflect.Type{}
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		name, _, _ := strings.Cut(f.Tag.Get("json"), ",")
		out[name] = f.Type
	}
	return out
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func TestTheCitationListWalksEveryGroupOfTheRecordSchema(t *testing.T) {
	props, defs := loadRecordSchema(t)
	shape := jsonFields(reflect.TypeOf(recShape{}))

	// An item group is a top-level array of items that carry a provenance.
	var groups []string
	for _, name := range sortedKeys(props) {
		p := props[name]
		if p["type"] != "array" {
			continue
		}
		item := defName(p)
		if _, ok := propertiesOf(t, defs, item)["provenance"]; ok {
			groups = append(groups, name)
		}
	}
	var walked []string
	for name, typ := range shape {
		if typ == reflect.TypeOf([]recItem{}) {
			walked = append(walked, name)
		}
	}
	sort.Strings(walked)
	if !slices.Equal(groups, walked) {
		t.Fatalf("item groups: the schema has %v, recShape walks %v", groups, walked)
	}

	// Each item is read as recItem: an id, printed.citation and a provenance.
	for _, g := range groups {
		item := defName(props[g])
		fields := propertiesOf(t, defs, item)
		if _, ok := fields["id"]; !ok {
			t.Errorf("%s (%s) has no id", g, item)
		}
		if got := defName(fields["provenance"]); got != "Provenance" {
			t.Errorf("%s provenance is %q", g, got)
		}
		printed := propertiesOf(t, defs, defName(fields["printed"]))
		if got := defName(printed["citation"]); got != "Citation" {
			t.Errorf("%s printed.citation is %q", g, got)
		}
	}
}

func TestTheCitationListReadsEveryPrintedHeaderValue(t *testing.T) {
	props, defs := loadRecordSchema(t)
	shape := jsonFields(reflect.TypeOf(recShape{}))

	for _, header := range []string{"document", "patient"} {
		def := propertiesOf(t, defs, defName(props[header]))
		if got := defName(def["provenance"]); got != "Provenance" {
			t.Errorf("%s provenance is %q", header, got)
		}
		// Every printed value of the header is a PrintedText (or a list
		// of them), each with its own citation.
		printed := propertiesOf(t, defs, defName(def["printed"]))
		var want []string
		for _, name := range sortedKeys(printed) {
			if defName(printed[name]) == "PrintedText" {
				want = append(want, name)
			}
		}
		goPrinted := jsonFields(jsonFields(shape[header])["printed"])
		got := sortedKeys(goPrinted)
		if !slices.Equal(want, got) {
			t.Errorf("%s printed values: the schema has %v, recShape reads %v", header, want, got)
		}
	}
	if got := defName(propertiesOf(t, defs, "PrintedText")["citation"]); got != "Citation" {
		t.Errorf("PrintedText citation is %q", got)
	}
}

func TestTheCitationAndProvenanceFieldsGoReadsAreInTheSchema(t *testing.T) {
	_, defs := loadRecordSchema(t)
	for def, typ := range map[string]reflect.Type{
		"Citation":   reflect.TypeOf(recCitation{}),
		"Provenance": reflect.TypeOf(recProvenance{}),
	} {
		inSchema := propertiesOf(t, defs, def)
		for _, name := range sortedKeys(jsonFields(typ)) {
			if _, ok := inSchema[name]; !ok {
				t.Errorf("Go reads %s.%s, which the schema doesn't have", def, name)
			}
		}
	}
}

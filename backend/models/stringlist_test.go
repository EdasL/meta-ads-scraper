package models

import (
	"encoding/json"
	"testing"
)

func TestStringListValueRoundTrip(t *testing.T) {
	v, err := StringList{"sleep", "ai"}.Value()
	if err != nil {
		t.Fatalf("Value: %v", err)
	}
	if v.(string) != `["sleep","ai"]` {
		t.Fatalf("Value = %q", v)
	}

	var got StringList
	if err := got.Scan(v); err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(got) != 2 || got[0] != "sleep" || got[1] != "ai" {
		t.Fatalf("Scan got %v", got)
	}
}

func TestStringListScanNilAndEmpty(t *testing.T) {
	var s StringList
	if err := s.Scan(nil); err != nil || s == nil || len(s) != 0 {
		t.Fatalf("Scan(nil) = %v, %v", s, err)
	}
	if err := s.Scan([]byte("")); err != nil || len(s) != 0 {
		t.Fatalf("Scan(empty) = %v, %v", s, err)
	}
	if err := s.Scan("[\"x\"]"); err != nil || len(s) != 1 || s[0] != "x" {
		t.Fatalf("Scan(string) = %v, %v", s, err)
	}
}

func TestStringListMarshalNilIsEmptyArray(t *testing.T) {
	b, err := json.Marshal(struct {
		Tags StringList `json:"tags"`
	}{})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if string(b) != `{"tags":[]}` {
		t.Fatalf("marshal nil StringList = %s", b)
	}
}

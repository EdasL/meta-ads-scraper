package helpers

import "testing"

func TestValidMediaKey(t *testing.T) {
	cases := []struct {
		key  string
		want bool
	}{
		{"creatives/2026-06-05_abc.jpg", true},
		{"funnels/shot.png", true},
		{"creatives/a/b/c.webp", true},
		{"", false},
		{"creatives/", false},               // needs a char after the prefix
		{"/creatives/x.jpg", false},         // leading slash
		{"other/x.jpg", false},              // disallowed prefix
		{"creatives/../secret", false},      // traversal
		{"funnels/..%2f", false},            // traversal substring
		{"CREATIVES/x.jpg", false},          // case-sensitive prefix
		{"creatives/space file.jpg", false}, // space not allowed
	}
	for _, c := range cases {
		if got := ValidMediaKey(c.key); got != c.want {
			t.Errorf("ValidMediaKey(%q) = %v, want %v", c.key, got, c.want)
		}
	}
}

package mcpserver

import (
	"crypto/tls"
	"net/http/httptest"
	"testing"
)

// TestPublicOrigin covers the scheme decision the integration suite cannot
// reach: httptest speaks plaintext HTTP with no proxy in front, so the
// X-Forwarded-Proto and TLS branches — the ones that decide whether a
// production download URL says https or http — are only exercised here.
func TestPublicOrigin(t *testing.T) {
	cases := []struct {
		name     string
		env      string
		fwd      string
		tls      bool
		host     string
		expected string
	}{
		{
			name:     "override wins and loses its trailing slash",
			env:      "https://api.example.test/",
			host:     "internal.svc.cluster.local",
			expected: "https://api.example.test",
		},
		{
			name:     "override without a trailing slash is untouched",
			env:      "https://api.example.test",
			host:     "internal.svc.cluster.local",
			expected: "https://api.example.test",
		},
		{
			name:     "forwarded proto beats the plaintext hop from the ingress",
			fwd:      "https",
			host:     "api.peon.works",
			expected: "https://api.peon.works",
		},
		{
			name:     "only the client-facing hop of a forwarded chain is used",
			fwd:      "https, http",
			host:     "api.peon.works",
			expected: "https://api.peon.works",
		},
		{
			name:     "direct TLS is https without any forwarded header",
			tls:      true,
			host:     "api.peon.works",
			expected: "https://api.peon.works",
		},
		{
			name:     "plain local request stays http",
			host:     "127.0.0.1:8080",
			expected: "http://127.0.0.1:8080",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv(publicAPIURLEnv, tc.env)

			req := httptest.NewRequest("POST", "/mcp", nil)
			req.Host = tc.host
			if tc.fwd != "" {
				req.Header.Set("X-Forwarded-Proto", tc.fwd)
			}
			if tc.tls {
				req.TLS = &tls.ConnectionState{}
			} else {
				req.TLS = nil
			}

			if got := publicOrigin(req); got != tc.expected {
				t.Errorf("publicOrigin() = %q, want %q", got, tc.expected)
			}
		})
	}
}

package helpers

import (
	"github.com/stretchr/testify/require"
	"testing"
)

func TestProductLinkIdentity(t *testing.T) {
	cases := []struct{ raw, kind, key string }{
		{"https://www.facebook.com/ads/library/?country=ALL&view_all_page_id=111657624853387", "meta", "111657624853387"},
		{"https://facebook.com/ads/library/?id=1234567", "ad", "1234567"},
		{"https://apps.apple.com/us/app/example/id123456?utm_source=x", "ios", "123456"},
		{"https://play.google.com/store/apps/details?id=com.example.app&hl=en", "android", "com.example.app"},
		{"www.example.com/product/?utm_source=fb&fbclid=x", "web", "https://example.com/product"},
	}
	for _, c := range cases {
		t.Run(c.raw, func(t *testing.T) {
			p, e := ParseProductLink(c.raw)
			require.NoError(t, e)
			require.Equal(t, c.kind, p.Kind)
			require.Equal(t, c.key, p.Key)
		})
	}
	for _, raw := range []string{"", "javascript:alert(1)", "https://user:pass@example.com", "http://127.0.0.1:8000", "https://facebook.com/ads/library/?q=brand", "https://apps.apple.com/app/no-id", "https://play.google.com/store/apps/details?id=broken"} {
		_, e := ParseProductLink(raw)
		require.Error(t, e)
	}
	// Lookalike domains must never acquire Meta page identity.
	p, e := ParseProductLink("https://notfacebook.com/ads/library/?view_all_page_id=1234567")
	require.NoError(t, e)
	require.Equal(t, "web", p.Kind)
}

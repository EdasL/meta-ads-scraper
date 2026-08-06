package helpers

import "testing"

func TestParseAdLibraryPageID_AcceptedForms(t *testing.T) {
	cases := []struct{ name, in, wantPage string }{
		{"canonical view_all_page_id",
			"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=123456789",
			"123456789"},
		{"page id first param",
			"https://www.facebook.com/ads/library/?view_all_page_id=987654321&country=US",
			"987654321"},
		{"no scheme",
			"www.facebook.com/ads/library/?view_all_page_id=555555",
			"555555"},
		{"m dot host",
			"https://m.facebook.com/ads/library/?view_all_page_id=555555",
			"555555"},
		{"web dot host",
			"https://web.facebook.com/ads/library/?view_all_page_id=555555",
			"555555"},
		{"bare host no www",
			"https://facebook.com/ads/library/?view_all_page_id=555555",
			"555555"},
		{"no trailing slash on path",
			"https://www.facebook.com/ads/library?view_all_page_id=555555",
			"555555"},
		{"bare numeric id", "123456789", "123456789"},
		{"bare numeric id with surrounding space", "  123456789  ", "123456789"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			page, ad, err := ParseAdLibraryPageID(c.in)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if ad != "" {
				t.Fatalf("expected no ad id, got %q", ad)
			}
			if page != c.wantPage {
				t.Fatalf("page id = %q, want %q", page, c.wantPage)
			}
		})
	}
}

func TestParseAdLibraryPageID_SingleAdLink(t *testing.T) {
	page, ad, err := ParseAdLibraryPageID(
		"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&id=778899123")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if page != "" {
		t.Fatalf("expected no page id, got %q", page)
	}
	if ad != "778899123" {
		t.Fatalf("ad id = %q, want 778899123", ad)
	}
}

// A link carrying both identifiers is the "see all ads from this advertiser"
// view of one ad — the page id is the stronger signal and wins.
func TestParseAdLibraryPageID_PageIDWinsOverAdID(t *testing.T) {
	page, ad, err := ParseAdLibraryPageID(
		"https://www.facebook.com/ads/library/?id=778899123&view_all_page_id=123456789")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if page != "123456789" {
		t.Fatalf("page id = %q, want 123456789", page)
	}
	if ad != "" {
		t.Fatalf("expected no ad id, got %q", ad)
	}
}

func TestParseAdLibraryPageID_Rejected(t *testing.T) {
	cases := []struct{ name, in, wantReason string }{
		{"empty", "", "not_ad_library"},
		{"whitespace", "   ", "not_ad_library"},
		{"wrong host", "https://example.com/ads/library/?view_all_page_id=123456", "not_ad_library"},
		{"lookalike host", "https://notfacebook.com/ads/library/?view_all_page_id=123456", "not_ad_library"},
		{"facebook but not ad library", "https://www.facebook.com/somepage", "not_ad_library"},
		{"garbage", "not a url at all", "not_ad_library"},
		{"keyword search url",
			"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&q=sleep",
			"no_identifier"},
		{"ad library no params", "https://www.facebook.com/ads/library/", "no_identifier"},
		{"non numeric page id",
			"https://www.facebook.com/ads/library/?view_all_page_id=abc", "no_identifier"},
		{"empty page id",
			"https://www.facebook.com/ads/library/?view_all_page_id=", "no_identifier"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			page, ad, err := ParseAdLibraryPageID(c.in)
			if err == nil {
				t.Fatalf("expected an error, got page=%q ad=%q", page, ad)
			}
			le, ok := err.(*AdLibraryLinkError)
			if !ok {
				t.Fatalf("expected *AdLibraryLinkError, got %T", err)
			}
			if le.Reason != c.wantReason {
				t.Fatalf("reason = %q, want %q", le.Reason, c.wantReason)
			}
			if le.Message == "" {
				t.Fatal("error message must be non-empty (it is rendered to the user)")
			}
		})
	}
}

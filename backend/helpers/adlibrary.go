package helpers

import (
	"net/url"
	"regexp"
	"strings"
)

// AdLibraryLinkError is a pasted-link rejection carrying a message the UI
// renders verbatim. Reason is a stable code for tests and client branching.
type AdLibraryLinkError struct {
	Reason  string
	Message string
}

func (e *AdLibraryLinkError) Error() string { return e.Message }

var (
	// Meta page and ad archive ids are long numerics. The floor rules out a
	// stray "1" being read as a page id; the ceiling keeps a pasted essay out.
	numericID = regexp.MustCompile(`^[0-9]{5,20}$`)
	// Anchored so a lookalike domain (notfacebook.com) cannot match.
	facebookHost = regexp.MustCompile(`^([a-z0-9-]+\.)*facebook\.com$`)
)

// ParseAdLibraryPageID extracts a Meta page id from a pasted Ad Library link.
//
// Accepted: an ads/library URL carrying view_all_page_id, and a bare numeric
// page id. An ads/library URL carrying only `id` identifies ONE ad rather than
// a page; it returns that ad id for the caller to resolve against public.ads,
// because Meta's AdLibraryAdDetailsV2Query response carries no page id (see
// src/agf/extract/ad_details.py — AdDetails is reach + active only). A link
// carrying both prefers the page id.
//
// Returns (pageID, "", nil), ("", adID, nil), or ("", "", *AdLibraryLinkError).
func ParseAdLibraryPageID(raw string) (string, string, error) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return "", "", notAdLibrary()
	}
	if numericID.MatchString(s) {
		return s, "", nil
	}
	withScheme := s
	if !strings.Contains(s, "://") {
		withScheme = "https://" + s
	}
	u, err := url.Parse(withScheme)
	if err != nil || u.Host == "" {
		return "", "", notAdLibrary()
	}
	if !facebookHost.MatchString(strings.ToLower(u.Hostname())) ||
		!strings.HasPrefix(strings.ToLower(u.Path), "/ads/library") {
		return "", "", notAdLibrary()
	}
	q := u.Query()
	if page := strings.TrimSpace(q.Get("view_all_page_id")); numericID.MatchString(page) {
		return page, "", nil
	}
	if ad := strings.TrimSpace(q.Get("id")); numericID.MatchString(ad) {
		return "", ad, nil
	}
	return "", "", &AdLibraryLinkError{
		Reason: "no_identifier",
		Message: "That link is not pointing at one advertiser. " +
			"Open the advertiser in Ad Library, then copy the URL from your browser.",
	}
}

func notAdLibrary() *AdLibraryLinkError {
	return &AdLibraryLinkError{
		Reason: "not_ad_library",
		Message: "Paste a Meta Ad Library link, for example " +
			"https://www.facebook.com/ads/library/?view_all_page_id=123456789",
	}
}

package helpers

import (
	"net"
	"net/url"
	"regexp"
	"strings"
)

type ProductInputError struct{ Message string }

func (e *ProductInputError) Error() string { return e.Message }
func InvalidProduct(message string) error  { return &ProductInputError{Message: message} }

type ProductLink struct{ URL, Kind, Key, Host string }

var appleProductID = regexp.MustCompile(`(?:^|/)id([0-9]+)$`)
var androidProductID = regexp.MustCompile(`^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$`)

// ParseProductLink normalizes identity without fetching the supplied URL.
func ParseProductLink(raw string) (ProductLink, error) {
	s := strings.TrimSpace(raw)
	if !strings.Contains(s, "://") {
		s = "https://" + s
	}
	u, err := url.Parse(s)
	if err != nil || len(s) > 2000 || u.User != nil || (u.Scheme != "https" && u.Scheme != "http") || !strings.Contains(u.Hostname(), ".") || net.ParseIP(u.Hostname()) != nil {
		return ProductLink{}, InvalidProduct("enter a valid product website, store, or Meta Ad Library link")
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if u.Port() != "" && u.Port() != "80" && u.Port() != "443" {
		return ProductLink{}, InvalidProduct("use a product link without a custom port")
	}
	host = strings.TrimPrefix(host, "www.")
	u.Host = host
	u.Scheme = "https"
	u.Fragment = ""
	if facebookHost.MatchString(host) && strings.HasPrefix(strings.ToLower(u.Path), "/ads/library") {
		page, ad, e := ParseAdLibraryPageID(u.String())
		if e != nil {
			return ProductLink{}, InvalidProduct(e.Error())
		}
		if page != "" {
			return ProductLink{URL: "https://www.facebook.com/ads/library/?view_all_page_id=" + page, Kind: "meta", Key: page}, nil
		}
		return ProductLink{URL: "https://www.facebook.com/ads/library/?id=" + ad, Kind: "ad", Key: ad}, nil
	}
	if host == "apps.apple.com" || host == "itunes.apple.com" {
		match := appleProductID.FindStringSubmatch(strings.TrimRight(u.Path, "/"))
		if match == nil {
			return ProductLink{}, InvalidProduct("use the App Store link for a specific app")
		}
		return ProductLink{URL: "https://apps.apple.com/app/id" + match[1], Kind: "ios", Key: match[1]}, nil
	}
	if host == "play.google.com" {
		id := u.Query().Get("id")
		if !androidProductID.MatchString(id) {
			return ProductLink{}, InvalidProduct("use the Google Play link for a specific app")
		}
		return ProductLink{URL: "https://play.google.com/store/apps/details?id=" + id, Kind: "android", Key: id}, nil
	}
	if host == "facebook.com" || strings.HasSuffix(host, ".facebook.com") {
		return ProductLink{}, InvalidProduct("use this brand’s Ad Library page link, including view_all_page_id")
	}
	q := u.Query()
	for k := range q {
		if strings.HasPrefix(strings.ToLower(k), "utm_") || k == "fbclid" || k == "gclid" {
			q.Del(k)
		}
	}
	u.RawQuery = q.Encode()
	u.Path = strings.TrimRight(u.Path, "/")
	u.RawPath = ""
	return ProductLink{URL: u.String(), Kind: "web", Key: u.String(), Host: host}, nil
}

from agf.parse import AdRecord
from agf.classify import classify_url, AppIntent


def _rec(link_url, cta="DOWNLOAD"):
    return AdRecord("1", "P", "N", True, 1, None, 1, cta, "x", link_url, "b")


def test_ios_app_store():
    out = classify_url("https://apps.apple.com/us/app/fitgem/id6470000001")
    assert out == AppIntent(kind="ios", store_id="6470000001")


def test_android_play_store():
    out = classify_url("https://play.google.com/store/apps/details?id=com.fit.gem")
    assert out == AppIntent(kind="android", store_id="com.fit.gem")


def test_facebook_shim_is_unwrapped():
    wrapped = "https://l.facebook.com/l.php?u=https%3A%2F%2Fapps.apple.com%2Fus%2Fapp%2Fx%2Fid42&h=AT"
    assert classify_url(wrapped) == AppIntent(kind="ios", store_id="42")


def test_plain_brand_domain_is_web():
    assert classify_url("https://getfitgem.com/quiz") == AppIntent(kind="web", store_id=None)


def test_none_when_no_url():
    assert classify_url(None) == AppIntent(kind="none", store_id=None)

from agf.fbjson import parse_fb_json


def test_strips_guard_prefix():
    assert parse_fb_json('for (;;);{"a": 1}') == {"a": 1}


def test_parses_plain_json():
    assert parse_fb_json('{"a": 2}') == {"a": 2}


def test_strips_with_leading_whitespace():
    assert parse_fb_json('  for (;;);{"a": 3}') == {"a": 3}

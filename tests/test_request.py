import json
from agf.request import build_variables, jazoest, build_form_body


def test_build_variables_defaults():
    v = build_variables(query="workout app", countries=["US"])
    assert v["queryString"] == "workout app"
    assert v["countries"] == ["US"]
    assert v["activeStatus"] == "ACTIVE"
    assert v["adType"] == "ALL"
    assert v["searchType"] == "KEYWORD_UNORDERED"
    assert v["first"] == 30
    assert "cursor" not in v  # absent on page 1


def test_build_variables_with_cursor():
    v = build_variables(query="x", countries=["US"], cursor="CUR123")
    assert v["cursor"] == "CUR123"


def test_jazoest_formula():
    # '2' + sum(ord) ; 'AB' -> 65+66 = 131 -> "2131"
    assert jazoest("AB") == "2131"


def test_build_form_body_contains_required_fields():
    body = build_form_body(lsd="AVxyz", variables={"a": 1}, doc_id="555")
    assert body["lsd"] == "AVxyz"
    assert body["doc_id"] == "555"
    assert body["fb_api_req_friendly_name"] == "AdLibrarySearchPaginationQuery"
    assert body["jazoest"] == jazoest("AVxyz")
    assert json.loads(body["variables"]) == {"a": 1}

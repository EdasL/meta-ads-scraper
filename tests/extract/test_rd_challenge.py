from pathlib import Path
from agf.extract.rd_challenge import extract_challenge_path, solve_rd_challenge
from tests.extract.conftest import FakeResponse, FakeSession

STUB = (Path(__file__).parent / "fixtures" / "rd_challenge_stub.html").read_text()


def test_extract_challenge_path():
    path = extract_challenge_path(STUB)
    assert path == "/__rd_verify_Q_6hBQNE9Z7FfIhrI6-9hWbT9aaHVKDP8?challenge=3"


def test_extract_challenge_path_absent_returns_none():
    assert extract_challenge_path("<html>nothing here</html>") is None


def test_solve_posts_then_reports_solved():
    session = FakeSession()

    def post(url, data=None, headers=None, **kw):
        session.calls.append(("POST", url))
        session.cookies["rd_challenge"] = "COOKIEVAL"
        return FakeResponse(status_code=200)
    session.post = post

    assert solve_rd_challenge(session, STUB) is True
    assert any(m == "POST" and "/__rd_verify_" in u for m, u in session.calls)
    assert session.cookies.get("rd_challenge") == "COOKIEVAL"


def test_solve_returns_false_when_no_challenge():
    session = FakeSession()
    assert solve_rd_challenge(session, "<html>real page, no challenge</html>") is False

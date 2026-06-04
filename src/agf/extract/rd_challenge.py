import re

_BASE = "https://www.facebook.com"
# The stub's only logic: fetch('/__rd_verify_<token>?challenge=N', {method:'POST'})
_CHALLENGE_RE = re.compile(r"""fetch\(\s*['"](/__rd_verify_[^'"]+)['"]""")


def extract_challenge_path(html: str) -> str | None:
    """Return the /__rd_verify_...?challenge=N path from a 403 stub, or None."""
    m = _CHALLENGE_RE.search(html or "")
    return m.group(1) if m else None


def solve_rd_challenge(session, html: str) -> bool:
    """If `html` is the rd_challenge stub, POST the verify path on `session`
    (minting the rd_challenge cookie into the session jar) and return True.
    Returns False if there is no challenge to solve.

    `session` is any object with `.post(url, data=...)` and a `.cookies` mapping
    (AdLibrarySession or curl_cffi Session). The caller re-GETs the page after.
    """
    path = extract_challenge_path(html)
    if not path:
        return False
    session.post(_BASE + path, data=b"")
    return True

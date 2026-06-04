from dataclasses import dataclass, field
import pytest


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    _json: dict | None = None

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("no json")
        return self._json


@dataclass
class FakeSession:
    """Duck-typed stand-in for AdLibrarySession / curl_cffi Session.

    Queue responses per (method, url-substring); records calls for assertions.
    """
    responses: dict = field(default_factory=dict)   # (method, substr) -> list[FakeResponse]
    cookies: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def _pop(self, method: str, url: str) -> FakeResponse:
        self.calls.append((method, url))
        for (m, substr), queue in self.responses.items():
            if m == method and substr in url and queue:
                return queue.pop(0)
        return FakeResponse(status_code=200, text="")

    def get(self, url, **kw):
        return self._pop("GET", url)

    def post(self, url, data=None, headers=None, **kw):
        return self._pop("POST", url)


@pytest.fixture
def fake_session():
    return FakeSession()

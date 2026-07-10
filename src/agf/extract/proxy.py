import os
import re
import time as _time
from itertools import cycle


class ProxyPool:
    """Round-robin pool of `user:pass@host:port` proxies with dead-proxy cooldown."""

    def __init__(self, proxies: list[str], max_failures: int = 3, cooldown: float = 300.0):
        self._proxies = list(proxies)
        self._cycle = cycle(self._proxies) if self._proxies else None
        self._max_failures = max_failures
        self._cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._dead_until: dict[str, float] = {}
        self._now = _time.time

    @staticmethod
    def list_from_env(var: str = "AGF_PROXIES") -> list[str]:
        raw = os.environ.get(var, "") or ""
        return [p.strip() for p in re.split(r"[\n,]+", raw) if p.strip()]

    @classmethod
    def from_env(cls, var: str = "AGF_PROXIES", **kw) -> "ProxyPool":
        return cls(cls.list_from_env(var), **kw)

    def __len__(self) -> int:
        return len(self._proxies)

    def _alive(self, proxy: str) -> bool:
        dead_until = self._dead_until.get(proxy)
        return dead_until is None or self._now() >= dead_until

    def acquire(self) -> str | None:
        if not self._cycle:
            return None
        for _ in range(len(self._proxies)):
            candidate = next(self._cycle)
            if self._alive(candidate):
                return candidate
        return None  # all dead

    def mark_failed(self, proxy: str | None) -> None:
        if proxy is None:
            return
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        if self._failures[proxy] >= self._max_failures:
            self._dead_until[proxy] = self._now() + self._cooldown
            self._failures[proxy] = 0

    def mark_ok(self, proxy: str | None) -> None:
        if proxy is not None:
            self._failures.pop(proxy, None)

    def as_curl_proxies(self, proxy: str | None) -> dict | None:
        """curl_cffi proxies dict, or None for a direct connection."""
        if not proxy:
            return None
        url = proxy if "://" in proxy else f"http://{proxy}"
        return {"http": url, "https": url}

"""What every data source shares: an injected HTTP function and two kinds of failure."""
from __future__ import annotations

from typing import Callable, Mapping

# (url, headers) -> (status, body, response headers); status 0 means a network failure.
# The caller supplies it, with its own pacing, retries and circuit breaker.
HttpGet = Callable[[str, Mapping[str, str]], tuple[int, bytes, Mapping[str, str]]]


class SourceError(RuntimeError):
    """The source could not answer (network, rate limit, server error)."""

    def __init__(self, source: str, url: str, status: int, detail: str = "") -> None:
        super().__init__(f"{source} HTTP {status} for {url}" + (f": {detail}" if detail else ""))
        self.source, self.url, self.status, self.detail = source, url, status, detail


class SourceNotFound(SourceError):
    """The source answered: there is no such document (HTTP 404)."""

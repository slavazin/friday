"""You.com web search as the nominator's SearchClient (SPEC § 7 drafting).

The key is read from the environment variable `run.yml` names (`search.api_key_env`); a missing key raises
at construction. A failed call or a body with no result list raises: an outage never reads as "no sources".
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from friday.config import SearchSection
from friday.ports.models import Citation

BASE_URL = "https://ydc-index.io"
SEARCH_PATH = "/v1/search"
PROVIDER = "youcom"


class SearchFailure(RuntimeError):
    """A search call that returned an error status or a body this adapter cannot read."""


def parse_results(body: Any, limit: int) -> list[Citation]:
    """Citations from a You.com search body (`results.web` or the older `hits`); raises SearchFailure when
    the body holds neither list."""
    if not isinstance(body, Mapping):
        raise SearchFailure("search body is not an object")
    results = body.get("results")
    if isinstance(results, Mapping) and isinstance(results.get("web"), list):
        rows = list(results["web"])
    elif isinstance(body.get("hits"), list):
        rows = list(body["hits"])
    else:
        raise SearchFailure(f"search body has no result list (keys: {sorted(body)})")
    out = []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("url"):
            raise SearchFailure("a search result has no url")
        snippets = row.get("snippets") or []
        snippet = str(snippets[0]) if isinstance(snippets, list) and snippets else str(row.get("description") or "")
        out.append(Citation(url=str(row["url"]), title=str(row.get("title") or ""), snippet=snippet))
    return out[:limit]


class YouSearchClient:
    """One GET per `search`, at most `search.results` citations."""

    def __init__(self, section: SearchSection, *, http: httpx.Client | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        if section.provider != PROVIDER:
            raise ValueError(f"search.provider is {section.provider!r}; this adapter serves {PROVIDER!r}")
        env = os.environ if environ is None else environ
        key = env.get(section.api_key_env)
        if not key:
            raise RuntimeError(f"environment variable {section.api_key_env} is not set; You.com search is offline")
        self._results = section.results
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=30.0)
        self._headers = {"X-API-Key": key}

    def search(self, query: str) -> Sequence[Citation]:
        """Citations for `query`; raises SearchFailure on an error status or an unreadable body."""
        try:
            resp = self._http.get(SEARCH_PATH, params={"query": query, "count": self._results},
                                  headers=self._headers)
        except httpx.HTTPError as err:
            raise SearchFailure(f"search transport failed: {err}") from err
        if resp.status_code != 200:
            raise SearchFailure(f"search returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as err:
            raise SearchFailure("search body is not JSON") from err
        return parse_results(body, self._results)

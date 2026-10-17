"""The You.com SearchClient, offline over httpx.MockTransport."""

from pathlib import Path

import httpx
import pytest

from friday.adapters.youcom import BASE_URL, SearchFailure, YouSearchClient, parse_results
from friday.config import load_spec

ROOT = Path(__file__).resolve().parents[2]
SEARCH = load_spec(ROOT / "run.yml").search
ENV = {SEARCH.api_key_env: "test-key"}


def client(handler, env=ENV):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return YouSearchClient(SEARCH, http=http, environ=env)


def web(n):
    return {"results": {"web": [{"url": f"https://e.x/{i}", "title": f"t{i}", "description": "d",
                                 "snippets": [f"s{i}"]} for i in range(n)]}}


def test_the_key_is_read_from_the_env_name_run_yml_gives_and_sent_as_a_header():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=web(2))

    got = client(handler).search("keyboard handler on unfocused element")
    assert [c.url for c in got] == ["https://e.x/0", "https://e.x/1"] and got[0].snippet == "s0"
    assert seen[0].headers["X-API-Key"] == "test-key"
    assert seen[0].url.params["query"] == "keyboard handler on unfocused element"
    assert seen[0].url.params["count"] == str(SEARCH.results)


def test_a_missing_key_raises_a_clear_error_at_construction():
    with pytest.raises(RuntimeError, match=SEARCH.api_key_env):
        client(lambda r: httpx.Response(200, json=web(1)), env={})


def test_results_are_capped_at_search_results():
    got = client(lambda r: httpx.Response(200, json=web(SEARCH.results + 3))).search("q")
    assert len(got) == SEARCH.results


@pytest.mark.parametrize("resp", [httpx.Response(401, json={"error": "bad key"}),
                                  httpx.Response(500, text="boom"),
                                  httpx.Response(200, text="<html>"),
                                  httpx.Response(200, json={"error": "quota"})])
def test_an_error_or_unreadable_body_raises_and_never_reads_as_no_sources(resp):
    with pytest.raises(SearchFailure):
        client(lambda r: resp).search("q")


def test_a_transport_error_raises_search_failure():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(SearchFailure, match="transport"):
        client(handler).search("q")


def test_the_older_hits_body_parses_and_a_true_empty_list_is_empty():
    assert parse_results({"hits": [{"url": "https://e.x", "title": "t", "description": "d"}]}, 5)[0].snippet == "d"
    assert parse_results({"results": {"web": []}}, 5) == []
    with pytest.raises(SearchFailure, match="no url"):
        parse_results({"hits": [{"title": "t"}]}, 5)

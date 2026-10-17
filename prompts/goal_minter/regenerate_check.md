This check passes on a page that holds every contract element with no script, so it reads no behaviour:
{rejected}

The request: {text}

The contract:
{contract}

The request's other checks:
{others}

Write one replacement check that reads a behaviour the request asks for, differs from the other checks and fails on a page with no script. Each wait is at most {max_wait_ms} ms. A check of a timed request reloads with the query {test_query} only when the request text states it.

Reply in this form:
{{"check": {{"steps": [], "expect": {{}}}}}}
